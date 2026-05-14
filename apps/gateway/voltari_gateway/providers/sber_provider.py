"""Sber GigaChat provider adapter.

# TODO(ceo, reseller-ok):
# Sber GigaChat business access requires a signed agreement with Sber
# Cloud (CEO 29.04 — pending). Until that's signed, this adapter is for
# staging/internal evaluation only. Do NOT route production traffic to
# GigaChat through this gateway without CEO sign-off.

REST API: ``POST https://gigachat.devices.sberbank.ru/api/v1/chat/completions``
auth via Bearer access_token from OAuth 2.0:

    POST https://ngw.devices.sberbank.ru:9443/api/v2/oauth
    Headers: Authorization: Basic <SBER_AUTH_KEY>, RqUID: <uuid>
    Body: scope=GIGACHAT_API_CORP   (or GIGACHAT_API_PERS / GIGACHAT_API_B2B)

Tokens last 30 minutes. We cache in-process with ``asyncio.Lock`` and
refresh ~2 minutes before expiry. (V2: Redis-shared.)

Streaming: SSE supported. The body is OpenAI-shaped but the response
schema is slightly different (``data_type``, ``role`` location). We
translate to the OpenAI SSE format the gateway already uses.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    Provider,
    ProviderAuthError,
    ProviderClientError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

SBER_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
SBER_CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

OAUTH_REFRESH_SAFETY_S: int = 120


class _SberOAuthCache:
    """OAuth 2.0 access-token cache with asyncio.Lock for single-pod MVP."""

    def __init__(self, auth_key: str, scope: str) -> None:
        if not auth_key:
            raise ValueError("Sber auth_key (Basic) required")
        self._auth_key = auth_key
        self._scope = scope
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(self, http: httpx.AsyncClient) -> str:
        now = time.time()
        if self._token is not None and self._expires_at - OAUTH_REFRESH_SAFETY_S > now:
            return self._token
        async with self._lock:
            now = time.time()
            if self._token is not None and self._expires_at - OAUTH_REFRESH_SAFETY_S > now:
                return self._token
            headers = {
                "Authorization": f"Basic {self._auth_key}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }
            try:
                # Sber's OAuth endpoint uses a self-signed cert chain for
                # MinTsifry; production uses Russian Trusted CA. Verification
                # is left to the underlying httpx defaults — operators must
                # install the Russian Trust CA bundle on the host.
                resp = await http.post(
                    SBER_OAUTH_URL,
                    data={"scope": self._scope},
                    headers=headers,
                    timeout=10.0,
                )
            except httpx.TimeoutException as exc:
                raise ProviderTimeoutError(f"sber_oauth_timeout: {exc}") from exc
            except httpx.HTTPError as exc:
                raise ProviderTimeoutError(f"sber_oauth_network: {exc}") from exc

            if resp.status_code >= 500:
                raise ProviderServerError(
                    f"sber_oauth_status_{resp.status_code}",
                    status_code=resp.status_code,
                )
            if resp.status_code in (401, 403):
                raise ProviderAuthError(
                    f"sber_oauth_auth_{resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                )
            if resp.status_code >= 400:
                raise ProviderClientError(
                    f"sber_oauth_status_{resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                )
            data = resp.json()
            self._token = str(data["access_token"])
            # Sber returns ``expires_at`` as epoch milliseconds.
            expires_at_ms = int(data.get("expires_at", 0) or 0)
            if expires_at_ms > 0:
                self._expires_at = expires_at_ms / 1000.0
            else:
                self._expires_at = now + 30 * 60
            return self._token


def _normalise_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI messages → Sber GigaChat ``{role, content, ...}`` shape.

    GigaChat поддерживает ``system``, ``user``, ``assistant``, ``function``
    (legacy OpenAI-формат для function calling — НЕ ``tool``). ``developer``
    мапим в ``system``.

    Function-calling маппинг (Phase 5 #2 — 2026-05-09):
    * Если в assistant-message есть ``tool_calls`` (modern OpenAI) — берём
      ПЕРВЫЙ (GigaChat capped 1 tool call per response) и кладём в
      ``function_call: {name, arguments}`` (legacy формат, который понимает
      GigaChat upstream).
    * Если message приходит с ``role=tool``, конвертируем в ``role=function``
      и переносим ``tool_call_id``-привязанное имя в поле ``name``. Имя ищем
      по соседним assistant.tool_calls; если не нашли — оставляем пустым,
      GigaChat принимает но логичнее иметь.
    """
    # Build tool_call_id → function_name map for downstream tool messages.
    tc_id_to_name: dict[str, str] = {}
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id") and tc.get("function"):
                    fname = tc["function"].get("name") if isinstance(tc["function"], dict) else None
                    if fname:
                        tc_id_to_name[str(tc["id"])] = str(fname)

    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        if role == "developer":
            role = "system"
        # Modern tool message → legacy function message
        if role == "tool":
            content = m.get("content")
            text = content if isinstance(content, str) else ""
            tc_id = m.get("tool_call_id")
            name = tc_id_to_name.get(str(tc_id), "") if tc_id else (m.get("name") or "")
            entry: dict[str, Any] = {"role": "function", "content": text}
            if name:
                entry["name"] = name
            out.append(entry)
            continue
        if role not in ("system", "user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                str(p.get("text", ""))
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        else:
            text = ""
        # Assistant с tool_calls → берём первый и кладём в function_call.
        if role == "assistant":
            tool_calls = m.get("tool_calls") or []
            entry = {"role": "assistant", "content": text or None}
            if tool_calls:
                first = tool_calls[0]
                if isinstance(first, dict) and isinstance(first.get("function"), dict):
                    fn = first["function"]
                    args_raw = fn.get("arguments", "{}")
                    # OpenAI-style: arguments — stringified JSON. GigaChat
                    # legacy: arguments — JSON object. Парсим и кладём object.
                    if isinstance(args_raw, str):
                        try:
                            args_obj = json.loads(args_raw) if args_raw else {}
                        except json.JSONDecodeError:
                            args_obj = {}
                    else:
                        args_obj = args_raw or {}
                    entry["function_call"] = {
                        "name": fn.get("name", ""),
                        "arguments": args_obj,
                    }
            # Skip пустые assistant без content и без tool_call.
            if entry["content"] is None and "function_call" not in entry:
                continue
            out.append(entry)
            continue
        if not text:
            continue
        out.append({"role": role, "content": text})
    return out


def _wrap_openai_chunk(
    chat_id: str,
    created: int,
    model_id: str,
    *,
    delta: dict[str, Any],
    finish_reason: str | None,
    usage: dict[str, Any] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_id,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = usage
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


class SberProvider(Provider):
    """REST adapter for Sber GigaChat."""

    name = "sber"

    def __init__(
        self,
        *,
        auth_key: str,
        scope: str = "GIGACHAT_API_CORP",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._oauth = _SberOAuthCache(auth_key, scope)
        # Sprint 5 perf: explicit pool limits. httpx default
        # (max_connections=100, max_keepalive_connections=20) is fine for
        # a couple-RPS test bench but at 100 RPS with mixed stream/non-
        # stream traffic the keepalive limit becomes the bottleneck —
        # connections churn and HTTP/2 multiplexing benefits drop. Bump
        # keepalive to match max_connections so the pool stays warm.
        # Двухсерверная архитектура (CEO 2026-04-30): GigaChat доступен из РФ
        # напрямую и НЕ должен идти через зарубежный outbound_proxy.
        # ``trust_env=False`` блокирует авто-подхват ``HTTPS_PROXY`` env-vars,
        # которые могут быть выставлены другим провайдером (Google) при инициализации.
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, read=None, connect=10.0),
            http2=True,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=100,
                keepalive_expiry=30.0,
            ),
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._oauth.get(self._http)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _wrap_status(status: int, body: str) -> ProviderError:
        msg = f"sber_status_{status}: {body[:300]}"
        if status in (401, 403):
            return ProviderAuthError(msg, status_code=status)
        if status == 429:
            return ProviderRateLimitError(msg, status_code=status)
        if 500 <= status < 600:
            return ProviderServerError(msg, status_code=status)
        if 400 <= status < 500:
            return ProviderClientError(msg, status_code=status)
        return ProviderError(msg, status_code=status)

    def _build_payload(self, req: ChatCompletionRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": req.model.upstream_id,
            "messages": _normalise_messages(req.messages),
            "stream": stream,
        }
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.top_p is not None:
            payload["top_p"] = req.top_p
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        # Function calling pass-through (Phase 5 #2 — 2026-05-09).
        # GigaChat принимает modern OpenAI-формат `tools`/`tool_choice` на
        # input (см. `13-ru-tools-api.md` §1.2), а возвращает legacy
        # `function_call` (парсится в _parse_tool_call ниже).
        tools = req.extra.get("tools")
        if tools:
            payload["tools"] = tools
        tool_choice = req.extra.get("tool_choice")
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        return payload

    # ---- non-stream ----------------------------------------------------------

    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        payload = self._build_payload(req, stream=False)
        headers = await self._auth_headers()
        try:
            resp = await self._http.post(SBER_CHAT_URL, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"sber_timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTimeoutError(f"sber_network: {exc}") from exc

        if resp.status_code >= 400:
            raise self._wrap_status(resp.status_code, resp.text)

        data = resp.json()
        choices = data.get("choices") or []
        first = choices[0] if choices else {}
        message = first.get("message") or {}
        text_raw = message.get("content")
        text = str(text_raw) if text_raw is not None else ""
        finish_reason = first.get("finish_reason") or "stop"

        # Phase 5 #2 — function_call (legacy) → tool_calls (modern).
        # GigaChat возвращает {"function_call": {"name": ..., "arguments": {...}}}
        # с finish_reason="function_call". OpenAI-clients ожидают tool_calls
        # массив. Конвертируем — это safe, поскольку GigaChat capped 1 call.
        out_message: dict[str, Any] = {"role": "assistant", "content": text or None}
        fc = message.get("function_call")
        if isinstance(fc, dict) and fc.get("name"):
            args = fc.get("arguments", {})
            # OpenAI-shape arguments — string. GigaChat присылает object.
            args_str = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
            out_message["tool_calls"] = [
                {
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": fc["name"],
                        "arguments": args_str,
                    },
                }
            ]
            out_message["content"] = None  # OpenAI: content=null when tool_calls
            finish_reason = "tool_calls"

        usage_dict = data.get("usage") or {}
        prompt_tokens = int(usage_dict.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage_dict.get("completion_tokens", 0) or 0)
        total_tokens = int(usage_dict.get("total_tokens", prompt_tokens + completion_tokens))

        envelope: dict[str, Any] = {
            "id": data.get("id") or f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(data.get("created") or time.time()),
            "model": req.model.id,
            "choices": [
                {
                    "index": 0,
                    "message": out_message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }
        usage = ChatCompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=0,
        )
        return ChatCompletionResponse(
            raw=envelope, usage=usage, model_id=req.model.id, provider=self.name
        )

    # ---- stream --------------------------------------------------------------

    async def chat_completion_stream(self, req: ChatCompletionRequest) -> AsyncIterator[bytes]:
        payload = self._build_payload(req, stream=True)
        headers = await self._auth_headers()
        try:
            req_obj = self._http.build_request("POST", SBER_CHAT_URL, json=payload, headers=headers)
            stream_resp = await self._http.send(req_obj, stream=True)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"sber_stream_timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTimeoutError(f"sber_stream_network: {exc}") from exc

        if stream_resp.status_code >= 400:
            body = await stream_resp.aread()
            await stream_resp.aclose()
            raise self._wrap_status(stream_resp.status_code, body.decode("utf-8", errors="ignore"))

        chat_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        model_id = req.model.id

        async def _gen() -> AsyncIterator[bytes]:
            # Mixed-type aggregator: token counts (int) + finish reason (str)
            # + buffered function_call (для конвертации в tool_calls SSE).
            agg: dict[str, Any] = {
                "prompt": 0,
                "completion": 0,
                "finish": "stop",
                "fc_name": None,
                "fc_args": "",
            }
            try:
                yield _wrap_openai_chunk(
                    chat_id,
                    created,
                    model_id,
                    delta={"role": "assistant"},
                    finish_reason=None,
                )
                async for raw_line in stream_resp.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload_text = line[len("data:") :].strip()
                    if payload_text == "[DONE]":
                        # Sber emits its own [DONE] before usage in some setups;
                        # we'll emit a normalised one at the end.
                        break
                    try:
                        evt = json.loads(payload_text)
                    except json.JSONDecodeError:
                        continue
                    choices = evt.get("choices") or []
                    first = choices[0] if choices else {}
                    delta = first.get("delta") or {}
                    finish = first.get("finish_reason")
                    content = delta.get("content")
                    if content:
                        yield _wrap_openai_chunk(
                            chat_id,
                            created,
                            model_id,
                            delta={"content": content},
                            finish_reason=None,
                        )
                    # Phase 5 #2 — function_call streaming. GigaChat шлёт
                    # `function_call` обычно одним финальным куском (а не
                    # инкрементами как OpenAI). Буферизуем и эмитим в конце
                    # как один tool_calls-фрагмент.
                    fc = delta.get("function_call")
                    if isinstance(fc, dict):
                        if fc.get("name"):
                            agg["fc_name"] = fc["name"]
                        args = fc.get("arguments")
                        if args is not None:
                            if isinstance(args, str):
                                agg["fc_args"] += args
                            else:
                                agg["fc_args"] = json.dumps(args, ensure_ascii=False)
                    if finish:
                        agg["finish"] = finish
                    usage = evt.get("usage")
                    if usage:
                        agg["prompt"] = int(usage.get("prompt_tokens", agg["prompt"]) or 0)
                        agg["completion"] = int(
                            usage.get("completion_tokens", agg["completion"]) or 0
                        )

                # Если был function_call — эмитим один tool_calls-чанк перед
                # final usage, конвертируя legacy-формат в modern.
                if agg["fc_name"]:
                    args_final = agg["fc_args"] or "{}"
                    yield _wrap_openai_chunk(
                        chat_id,
                        created,
                        model_id,
                        delta={
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call_{uuid.uuid4().hex[:24]}",
                                    "type": "function",
                                    "function": {
                                        "name": agg["fc_name"],
                                        "arguments": args_final,
                                    },
                                }
                            ]
                        },
                        finish_reason=None,
                    )
                    agg["finish"] = "tool_calls"
                # Final usage chunk + DONE.
                yield _wrap_openai_chunk(
                    chat_id,
                    created,
                    model_id,
                    delta={},
                    finish_reason=agg["finish"],
                    usage={
                        "prompt_tokens": agg["prompt"],
                        "completion_tokens": agg["completion"],
                        "total_tokens": agg["prompt"] + agg["completion"],
                    },
                )
                yield b"data: [DONE]\n\n"
            finally:
                await stream_resp.aclose()

        return _gen()
