"""OpenAI provider adapter.

Uses the official ``openai`` SDK (>=1.54). The SDK is fully async and handles
auth, retries on connection errors, and SSE parsing. We forward whatever the
caller sent under ``extra`` so OpenAI-only fields (``response_format``,
``tools``, ``tool_choice``, ``logprobs``, ``seed``…) keep working without us
having to enumerate them.

Streaming: we emit the upstream chunks verbatim as ``data: ...`` SSE lines,
plus the standard terminating ``data: [DONE]``. When ``stream_options
.include_usage`` is set, OpenAI sends a final chunk with ``usage`` populated;
we surface it through the SSE so the caller's billing path can extract it.

Error mapping (per architectural decision in failover.py):

* ``RateLimitError`` / 429                 → ``ProviderRateLimitError``
* ``APITimeoutError`` / connection         → ``ProviderTimeoutError``
* ``APIStatusError`` 401/403               → ``ProviderAuthError``
* ``APIStatusError`` 5xx                   → ``ProviderServerError``
* ``APIStatusError`` 4xx (other)           → ``ProviderClientError``
* anything else                            → ``ProviderError``
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

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


def _map_status_error(exc: APIStatusError) -> ProviderError:
    """Translate an ``APIStatusError`` into the right Provider*Error subclass."""
    status = exc.status_code
    msg = f"openai_status_{status}: {getattr(exc, 'message', str(exc))}"
    if status in (401, 403):
        return ProviderAuthError(msg, status_code=status)
    if status == 429:
        retry_after: float | None = None
        # SDK exposes response on the exception; pull Retry-After if present.
        resp = getattr(exc, "response", None)
        if resp is not None:
            ra = resp.headers.get("retry-after") if hasattr(resp, "headers") else None
            try:
                retry_after = float(ra) if ra is not None else None
            except (TypeError, ValueError):
                retry_after = None
        return ProviderRateLimitError(msg, retry_after_s=retry_after, status_code=status)
    if 500 <= status < 600:
        return ProviderServerError(msg, status_code=status)
    if 400 <= status < 500:
        return ProviderClientError(msg, status_code=status)
    return ProviderError(msg, status_code=status)


class OpenAIProvider(Provider):
    """Adapter wrapping the OpenAI Python SDK."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        outbound_proxy: str | None = None,
    ) -> None:
        # Двухсерверная архитектура (CEO 2026-04-30): когда задан outbound_proxy,
        # все запросы к OpenAI идут через зарубежный HTTP-proxy
        # (WireGuard tunnel из РФ). Используем httpx >= 0.26 параметр ``proxy``
        # (singular). Передаём кастомный httpx-клиент в SDK через ``http_client``.
        timeout = httpx.Timeout(timeout_seconds, read=None, connect=10.0)
        self._owns_http_client = outbound_proxy is not None
        sdk_kwargs: dict[str, object] = {
            "api_key": api_key,
            "base_url": base_url,
            "max_retries": 0,  # we handle retries / failover ourselves
        }
        if outbound_proxy:
            self._http_client: httpx.AsyncClient | None = httpx.AsyncClient(
                proxy=outbound_proxy,
                timeout=timeout,
            )
            sdk_kwargs["http_client"] = self._http_client
        else:
            self._http_client = None
            sdk_kwargs["timeout"] = timeout
        self._client = AsyncOpenAI(**sdk_kwargs)  # type: ignore[arg-type]

    async def aclose(self) -> None:
        await self._client.close()
        # SDK ``.close()`` doesn't close a caller-supplied http_client (we're
        # the owner) — close it explicitly to avoid leaking the connection pool.
        if self._http_client is not None:
            await self._http_client.aclose()

    # ---- helpers -------------------------------------------------------------

    @staticmethod
    def _build_kwargs(req: ChatCompletionRequest, *, stream: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": req.model.upstream_id,
            "messages": req.messages,
            "stream": stream,
        }
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.top_p is not None:
            kwargs["top_p"] = req.top_p
        if req.max_tokens is not None:
            # Reasoning models (gpt-5.x, o3, o4-mini) deprecated max_tokens
            # 2025-Q4; sending it returns 400 unsupported_parameter. Translate
            # to max_completion_tokens for those; keep max_tokens for legacy
            # gpt-4 family that still accepts both.
            if req.model.requires_max_completion_tokens:
                kwargs["max_completion_tokens"] = req.max_tokens
            else:
                kwargs["max_tokens"] = req.max_tokens
        if req.stop is not None:
            kwargs["stop"] = req.stop
        # forward the rest verbatim — tools, response_format, seed, etc.
        # Underscore-prefixed keys are gateway-internal hints (e.g.
        # ``_brikko_anthropic_cache_extended``) and must NOT be forwarded
        # upstream — OpenAI 4xx-s on unknown fields.
        for k, v in req.extra.items():
            if k in kwargs or k.startswith("_"):
                continue
            kwargs[k] = v
        if stream:
            # Always include usage on the final stream chunk; harmless if user
            # already passed `stream_options`.
            stream_opts = dict(kwargs.get("stream_options") or {})
            stream_opts.setdefault("include_usage", True)
            kwargs["stream_options"] = stream_opts
        return kwargs

    @staticmethod
    def _wrap_error(exc: Exception) -> ProviderError:
        if isinstance(exc, RateLimitError):
            # SDK RateLimitError is a subclass of APIStatusError with status=429.
            return _map_status_error(exc)
        if isinstance(exc, APITimeoutError):
            return ProviderTimeoutError(str(exc))
        if isinstance(exc, APIConnectionError):
            # Network blip — treat as timeout-ish so failover retries.
            return ProviderTimeoutError(str(exc))
        if isinstance(exc, APIStatusError):
            return _map_status_error(exc)
        return ProviderError(str(exc))

    # ---- non-stream ----------------------------------------------------------

    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        kwargs = self._build_kwargs(req, stream=False)
        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
            log.warning("openai_error", model=req.model.id, error=str(exc))
            raise self._wrap_error(exc) from exc
        except Exception as exc:  # pragma: no cover — defensive
            log.exception("openai_unexpected_error", model=req.model.id)
            raise ProviderError(str(exc)) from exc

        raw = completion.model_dump()
        usage_dict = raw.get("usage") or {}
        usage = ChatCompletionUsage(
            prompt_tokens=int(usage_dict.get("prompt_tokens", 0)),
            completion_tokens=int(usage_dict.get("completion_tokens", 0)),
            total_tokens=int(usage_dict.get("total_tokens", 0)),
            cached_tokens=int(
                (usage_dict.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            ),
        )
        return ChatCompletionResponse(
            raw=raw, usage=usage, model_id=req.model.id, provider=self.name
        )

    # ---- audio: TTS ---------------------------------------------------------

    async def audio_speech(
        self,
        *,
        model: str,
        input_text: str,
        voice: str,
        response_format: str = "mp3",
        speed: float = 1.0,
        instructions: str | None = None,
    ) -> tuple[bytes, str]:
        """Forward to ``POST /v1/audio/speech`` and return ``(audio_bytes, content_type)``.

        We bypass the SDK and use the underlying httpx client directly:
        the SDK's ``audio.speech.create`` returns a streamed response that
        we'd have to materialise anyway — going through httpx keeps the
        outbound proxy / timeout / connection-pool config the SDK has
        without paying for the SDK's response-class abstraction.

        Errors are mapped through ``_wrap_error`` so the API layer's
        retry / rate-limit handling stays uniform with chat.
        """
        # AsyncOpenAI exposes its underlying httpx client through ``_client``.
        # Using it directly preserves auth / proxy / timeout configuration.
        http_client = self._client._client
        base_url = str(self._client.base_url).rstrip("/")
        url = f"{base_url}/audio/speech"
        api_key = self._client.api_key

        body: dict[str, Any] = {
            "model": model,
            "input": input_text,
            "voice": voice,
            "response_format": response_format,
            "speed": speed,
        }
        if instructions is not None:
            # ``instructions`` is gpt-4o-mini-tts only — older tts-1*
            # models 400 on unknown fields. Caller must pre-filter.
            body["instructions"] = instructions

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = await http_client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(str(exc)) from exc

        if resp.status_code >= 400:
            # Wrap as APIStatusError so _map_status_error gives the same
            # taxonomy chat does. The SDK exception expects (message, response,
            # body) — we synthesize a minimal body to keep the constructor happy.
            try:
                err = APIStatusError(
                    message=f"openai_status_{resp.status_code}",
                    response=resp,
                    body=None,
                )
            except Exception:
                # SDK constructor changed shape: fall back to a flat ProviderError.
                raise ProviderError(
                    f"openai_status_{resp.status_code}: {resp.text[:200]}",
                    status_code=resp.status_code,
                ) from None
            raise _map_status_error(err)

        return resp.content, resp.headers.get("content-type", "audio/mpeg")

    # ---- images -------------------------------------------------------------

    async def image_generate(
        self,
        *,
        model: str,
        prompt: str,
        n: int = 1,
        size: str | None = None,
        quality: str | None = None,
        response_format: str | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        """Forward to ``POST /v1/images/generations`` and return parsed JSON.

        The OpenAI image API returns ``{"created": int, "data": [...], "usage": {...}}``
        — we forward verbatim. ``response_format`` for gpt-image-1 is
        ``b64_json`` only (no signed URLs); for dall-e-* it accepts both
        ``url`` and ``b64_json``. The API layer validates which combo is
        legal so we don't pay for upstream 400s.
        """
        http_client = self._client._client
        base_url = str(self._client.base_url).rstrip("/")
        url = f"{base_url}/images/generations"
        api_key = self._client.api_key

        body: dict[str, Any] = {"model": model, "prompt": prompt, "n": n}
        if size is not None:
            body["size"] = size
        if quality is not None:
            body["quality"] = quality
        if response_format is not None:
            body["response_format"] = response_format
        if user is not None:
            body["user"] = user

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Image generation на gpt-image-1 / dall-e-3 hd часто занимает
        # 30-120 секунд (особенно gpt-image-1 high quality).  Default 60s
        # из __init__ слишком жёсткий — клиент получал 502 хотя OpenAI
        # отдавал 200 (бaг 2026-05-10).  Override timeout per-request 180s.
        image_timeout = httpx.Timeout(180.0, read=180.0, connect=10.0)
        try:
            resp = await http_client.post(url, headers=headers, json=body, timeout=image_timeout)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(str(exc)) from exc

        if resp.status_code >= 400:
            try:
                err = APIStatusError(
                    message=f"openai_status_{resp.status_code}",
                    response=resp,
                    body=None,
                )
            except Exception:
                raise ProviderError(
                    f"openai_status_{resp.status_code}: {resp.text[:200]}",
                    status_code=resp.status_code,
                ) from None
            raise _map_status_error(err)

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"image response not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ProviderError("image response did not parse to a dict")
        return data

    # ---- stream --------------------------------------------------------------

    async def chat_completion_stream(self, req: ChatCompletionRequest) -> AsyncIterator[bytes]:
        kwargs = self._build_kwargs(req, stream=True)
        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
            log.warning("openai_stream_error", model=req.model.id, error=str(exc))
            raise self._wrap_error(exc) from exc

        async def _gen() -> AsyncIterator[bytes]:
            try:
                async for chunk in stream:
                    payload = chunk.model_dump()
                    line = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    yield line.encode("utf-8")
                yield b"data: [DONE]\n\n"
            except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
                log.warning("openai_stream_mid_error", model=req.model.id, error=str(exc))
                raise self._wrap_error(exc) from exc
            finally:
                close = getattr(stream, "close", None)
                if close is not None:
                    # contextlib.suppress: cleanup-time errors не должны затмевать
                    # реальный API error (если был). pragma: no cover — путь не
                    # реалистично воспроизвести в unit-test'ах.
                    with contextlib.suppress(Exception):  # pragma: no cover
                        await close()

        return _gen()
