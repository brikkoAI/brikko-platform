"""YandexGPT provider adapter.

# TODO(ceo, reseller-ok):
# Yandex Foundation Models has a "reseller" mode that requires a signed
# agreement with Yandex Cloud (CEO 29.04 — pending). Until that's signed
# this adapter is for staging/internal evaluation only. Do NOT route
# production traffic to YandexGPT through this gateway without CEO sign-off.
# When the agreement lands, remove this comment and update README/marketing.

REST API: ``POST https://llm.api.cloud.yandex.net/foundationModels/v1/completion``
auth via ``Authorization: Api-Key <YANDEX_API_KEY>`` (preferred for service
accounts, no rotation needed) OR ``Bearer <iam_token>`` (rotates every ~12h).

We support both. ``YANDEX_API_KEY`` is the simple path — no token cache needed.
``YANDEX_IAM_TOKEN_URL`` (with a service-account JWT) is the rotating path —
we cache the IAM token in-process with an asyncio.Lock and refresh ~5 min
before expiry. (V2 will move the cache to Redis for multi-pod deployments.)

Streaming: V2.1 (NDJSON parsing). ``supports_streaming=False`` in catalog
for both yandexgpt-* models. The Provider interface still requires
``chat_completion_stream`` so we provide a synthetic non-streaming stream
that emits a single chunk + DONE.

Token counts: returned in ``result.usage`` (``inputTextTokens`` and
``completionTokens``). No prompt cache field — we report ``cached_tokens=0``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
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

YANDEX_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_IAM_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"

# Refresh the IAM token this many seconds before it actually expires so a
# slow request doesn't get caught with a stale credential mid-flight.
IAM_REFRESH_SAFETY_S: int = 300


class _IamTokenCache:
    """In-memory IAM token cache with an asyncio lock.

    Per architectural decision 5 — single-pod MVP uses an in-process cache.
    Redis-backed shared cache is V2.

    The cache is opt-in: instances are constructed only when the adapter is
    configured with a JWT-based service account (``iam_jwt`` set). When the
    adapter uses a static API key, this cache is unused.
    """

    def __init__(self, jwt_factory: Callable[[], str]) -> None:
        self._jwt_factory = jwt_factory
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(self, http: httpx.AsyncClient) -> str:
        now = time.time()
        if self._token is not None and self._expires_at - IAM_REFRESH_SAFETY_S > now:
            return self._token
        async with self._lock:
            # Double-check inside the lock — another request may have refreshed.
            now = time.time()
            if self._token is not None and self._expires_at - IAM_REFRESH_SAFETY_S > now:
                return self._token
            jwt = self._jwt_factory()
            try:
                resp = await http.post(
                    YANDEX_IAM_URL,
                    json={"jwt": jwt},
                    timeout=10.0,
                )
            except httpx.TimeoutException as exc:
                raise ProviderTimeoutError(f"yandex_iam_timeout: {exc}") from exc
            if resp.status_code >= 500:
                raise ProviderServerError(
                    f"yandex_iam_status_{resp.status_code}",
                    status_code=resp.status_code,
                )
            if resp.status_code in (401, 403):
                raise ProviderAuthError(
                    f"yandex_iam_auth_{resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                )
            if resp.status_code >= 400:
                raise ProviderClientError(
                    f"yandex_iam_status_{resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                )
            data = resp.json()
            self._token = str(data["iamToken"])
            # ``expiresAt`` is RFC3339; we approximate with +12h if missing.
            expires_at = data.get("expiresAt")
            if expires_at:
                # Best-effort parse; on failure assume 12h validity.
                try:
                    from datetime import datetime

                    self._expires_at = datetime.fromisoformat(
                        expires_at.replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    self._expires_at = now + 12 * 3600
            else:
                self._expires_at = now + 12 * 3600
            return self._token


def _split_system(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI-shaped messages → Yandex Foundation Models format.

    Yandex native REST: roles ``system`` / ``user`` / ``assistant`` с полем
    ``text``. Phase 5 #2 (2026-05-09) — добавлена поддержка function calling:

    * **assistant.tool_calls** (modern OpenAI) → assistant message с полем
      ``toolCallList: {toolCalls: [{name, arguments-object}]}``.
      Yandex принимает arguments как JSON object (не stringified).
    * **role: tool** → assistant message с полем
      ``toolResultList: {toolResults: [{name, content}]}``. Это формат из
      Yandex SDK examples (06_Operations/2026-05-09-night-research/
      13-ru-tools-api.md §2.3). Имя функции lookup'им по ``tool_call_id``.

    See research § 2.4 for the schema reference.
    """
    # Map tool_call_id → function_name из предыдущих assistant.tool_calls.
    tc_id_to_name: dict[str, str] = {}
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id") and isinstance(tc.get("function"), dict):
                    fname = tc["function"].get("name")
                    if fname:
                        tc_id_to_name[str(tc["id"])] = str(fname)

    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        if role == "developer":
            role = "system"
        # Modern OpenAI tool message → Yandex assistant + toolResultList.
        if role == "tool":
            content = m.get("content")
            text_val = content if isinstance(content, str) else ""
            tc_id = m.get("tool_call_id")
            name = tc_id_to_name.get(str(tc_id), "") if tc_id else (m.get("name") or "")
            tool_result_msg: dict[str, Any] = {
                "role": "assistant",
                "text": "",
                "toolResultList": {"toolResults": [{"name": name, "content": text_val}]},
            }
            out.append(tool_result_msg)
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
        # Assistant с tool_calls: добавляем toolCallList в message.
        if role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "text": text}
            tool_calls = m.get("tool_calls") or []
            if tool_calls:
                yandex_calls = []
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    args_raw = fn.get("arguments", "{}")
                    if isinstance(args_raw, str):
                        try:
                            args_obj = json.loads(args_raw) if args_raw else {}
                        except json.JSONDecodeError:
                            args_obj = {}
                    else:
                        args_obj = args_raw or {}
                    yandex_calls.append({"name": fn.get("name", ""), "arguments": args_obj})
                if yandex_calls:
                    entry["toolCallList"] = {"toolCalls": yandex_calls}
            # Skip пустые assistant без content и без tool_calls.
            if not text and "toolCallList" not in entry:
                continue
            out.append(entry)
            continue
        if not text:
            continue
        out.append({"role": role, "text": text})
    return out


def _convert_tools_to_yandex(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """OpenAI ``tools=[{type:function, function:{name, description, parameters}}]``
    → Yandex ``[{function: {name, description, parameters, strict?}}]``.

    Yandex поддерживает strict JSON schema (``strict=True`` в SDK), мы
    пробрасываем флаг 1:1 если клиент его прислал.
    """
    if not tools:
        return None
    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if t.get("type") == "function" else t
        if not isinstance(fn, dict):
            continue
        yandex_fn: dict[str, Any] = {
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {}),
        }
        if fn.get("strict") is True:
            yandex_fn["strict"] = True
        out.append({"function": yandex_fn})
    return out or None


def _model_uri(folder_id: str, upstream_id: str) -> str:
    """Build the ``modelUri`` Yandex expects.

    Format: ``gpt://<folder_id>/<model>/<version>``. We accept either a bare
    model name (``yandexgpt-5.1-pro`` → ``yandexgpt-5.1/latest``) or a
    fully-qualified URI passed through ``upstream_id``. For MVP we map our
    public ids to the canonical Yandex aliases:
    """
    if upstream_id.startswith("gpt://"):
        return upstream_id
    alias_map = {
        "yandexgpt-5.1-pro": "yandexgpt/rc",  # 5.1 family RC channel
        "yandexgpt-5-lite": "yandexgpt-lite/rc",
    }
    alias = alias_map.get(upstream_id, f"{upstream_id}/latest")
    return f"gpt://{folder_id}/{alias}"


class YandexProvider(Provider):
    """REST adapter for YandexGPT Foundation Models."""

    name = "yandex"

    def __init__(
        self,
        *,
        folder_id: str,
        api_key: str | None = None,
        iam_jwt_factory: Callable[[], str] | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not folder_id:
            raise ValueError("YandexProvider requires folder_id")
        if not api_key and iam_jwt_factory is None:
            raise ValueError("YandexProvider requires either api_key or iam_jwt_factory")
        self._folder_id = folder_id
        self._api_key = api_key
        # Sprint 5 perf: pool sizing — same reasoning as Sber adapter,
        # keepalive count matches max_connections so YandexGPT requests
        # don't churn TCP/TLS handshakes under sustained load.
        # Двухсерверная архитектура (CEO 2026-04-30): YandexGPT доступен из РФ
        # напрямую и НЕ должен идти через зарубежный outbound_proxy. ``trust_env=False``
        # отключает авто-подхват ``HTTPS_PROXY``/``HTTP_PROXY`` env-переменных,
        # которые могут быть установлены провайдером Google для своих нужд.
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
        self._iam_cache = _IamTokenCache(iam_jwt_factory) if iam_jwt_factory is not None else None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _auth_header(self) -> dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Api-Key {self._api_key}"}
        assert self._iam_cache is not None
        token = await self._iam_cache.get(self._http)
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _wrap_status(status: int, body: str) -> ProviderError:
        msg = f"yandex_status_{status}: {body[:300]}"
        if status in (401, 403):
            return ProviderAuthError(msg, status_code=status)
        if status == 429:
            return ProviderRateLimitError(msg, status_code=status)
        if 500 <= status < 600:
            return ProviderServerError(msg, status_code=status)
        if 400 <= status < 500:
            return ProviderClientError(msg, status_code=status)
        return ProviderError(msg, status_code=status)

    def _build_payload(self, req: ChatCompletionRequest) -> dict[str, Any]:
        completion_options: dict[str, Any] = {
            "stream": False,  # MVP: no streaming.
            "temperature": 0.6 if req.temperature is None else req.temperature,
        }
        if req.max_tokens is not None:
            completion_options["maxTokens"] = req.max_tokens
        # No top_p in Yandex's API as of 2026-04 — silently drop.
        payload: dict[str, Any] = {
            "modelUri": _model_uri(self._folder_id, req.model.upstream_id),
            "completionOptions": completion_options,
            "messages": _split_system(req.messages),
        }
        # Phase 5 #2 — function calling pass-through (2026-05-09).
        # См. 06_Operations/2026-05-09-night-research/13-ru-tools-api.md §2.5.
        # Yandex native API принимает tools на корне request body.
        yandex_tools = _convert_tools_to_yandex(req.extra.get("tools"))
        if yandex_tools:
            payload["tools"] = yandex_tools
        # tool_choice — полный паритет с OpenAI: "auto" / "none" / "required" /
        # {"type":"function", "function":{"name":"..."}}. Передаём 1:1.
        tool_choice = req.extra.get("tool_choice")
        if tool_choice is not None:
            payload["toolChoice"] = tool_choice
        return payload

    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        payload = self._build_payload(req)
        headers = {"Content-Type": "application/json", **(await self._auth_header())}
        headers["x-folder-id"] = self._folder_id
        try:
            resp = await self._http.post(YANDEX_COMPLETION_URL, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"yandex_timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTimeoutError(f"yandex_network: {exc}") from exc

        if resp.status_code >= 400:
            raise self._wrap_status(resp.status_code, resp.text)

        data = resp.json()
        result = data.get("result") or {}
        alternatives = result.get("alternatives") or []
        first = alternatives[0] if alternatives else {}
        message = first.get("message") or {}
        text_raw = message.get("text", "")
        text = str(text_raw) if text_raw is not None else ""
        finish = {
            "ALTERNATIVE_STATUS_FINAL": "stop",
            "ALTERNATIVE_STATUS_TRUNCATED_FINAL": "length",
        }.get(str(first.get("status", "")), "stop")

        # Phase 5 #2 — Yandex `toolCallList.toolCalls` (native) → OpenAI
        # `tool_calls` (modern). Yandex может возвращать несколько вызовов
        # параллельно (Go-SDK schema — массив). arguments как object →
        # stringify для OpenAI-совместимости.
        out_message: dict[str, Any] = {"role": "assistant", "content": text or None}
        tcl = message.get("toolCallList") or {}
        yandex_calls = tcl.get("toolCalls") if isinstance(tcl, dict) else None
        if yandex_calls and isinstance(yandex_calls, list):
            tool_calls_out = []
            for tc in yandex_calls:
                if not isinstance(tc, dict) or not tc.get("name"):
                    continue
                args = tc.get("arguments", {})
                args_str = (
                    json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
                )
                tool_calls_out.append(
                    {
                        "id": f"call_{uuid.uuid4().hex[:24]}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": args_str,
                        },
                    }
                )
            if tool_calls_out:
                out_message["tool_calls"] = tool_calls_out
                out_message["content"] = None
                finish = "tool_calls"

        usage_dict = result.get("usage") or {}
        prompt_tokens = int(usage_dict.get("inputTextTokens", 0) or 0)
        completion_tokens = int(usage_dict.get("completionTokens", 0) or 0)
        total_tokens = int(usage_dict.get("totalTokens", prompt_tokens + completion_tokens) or 0)

        envelope: dict[str, Any] = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model.id,
            "choices": [
                {
                    "index": 0,
                    "message": out_message,
                    "finish_reason": finish,
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

    async def chat_completion_stream(self, req: ChatCompletionRequest) -> AsyncIterator[bytes]:
        """Synthetic stream — call non-stream then chunk into SSE.

        MVP only. V2.1 will switch to NDJSON streaming via Yandex's
        ``stream: true`` flag. Catalog has ``supports_streaming=False``
        for all yandexgpt-* models so the router never picks them on a
        ``stream=true`` request through ``auto:*``; this method exists
        to satisfy the abstract interface and to keep client code that
        explicitly pinned a yandex model from crashing — instead they
        get a single-chunk faux stream.
        """
        response = await self.chat_completion(req)

        chat_id = response.raw["id"]
        created = response.raw["created"]
        model_id = req.model.id
        text = response.raw["choices"][0]["message"].get("content") or ""
        finish = response.raw["choices"][0]["finish_reason"]

        async def _gen() -> AsyncIterator[bytes]:
            yield _wrap_openai_chunk(
                chat_id,
                created,
                model_id,
                delta={"role": "assistant"},
                finish_reason=None,
            )
            if text:
                yield _wrap_openai_chunk(
                    chat_id,
                    created,
                    model_id,
                    delta={"content": text},
                    finish_reason=None,
                )
            yield _wrap_openai_chunk(
                chat_id,
                created,
                model_id,
                delta={},
                finish_reason=finish,
                usage=response.raw.get("usage"),
            )
            yield b"data: [DONE]\n\n"

        return _gen()


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
