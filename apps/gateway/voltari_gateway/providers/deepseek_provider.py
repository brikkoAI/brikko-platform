"""DeepSeek provider adapter.

DeepSeek exposes an OpenAI-compatible API at ``api.deepseek.com``. We
reuse the same ``openai`` SDK as the OpenAI adapter — just point its
``base_url`` at DeepSeek and pick the right model id. This keeps the
adapter tiny and inherits SSE + tool-calling support for free.

Key differences from OpenAI's surface:

* Auto prompt cache: DeepSeek server returns ``prompt_cache_hit_tokens``
  and ``prompt_cache_miss_tokens`` in usage. We translate them into our
  ``cached_tokens`` field.
* ``upstream_id`` is ``deepseek-chat`` (not the public-facing
  ``deepseek-v3.2-chat`` we expose) — handled by ``ModelSpec``.
* Some OpenAI-only fields are not supported (``response_format=json_object``
  works; ``tools`` works; ``logprobs`` may 4xx). The router never sends
  unsupported fields because the strategy filters keep them off DeepSeek
  unless the client pinned the model.
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
    ProviderError,
)
from voltari_gateway.providers.openai_provider import _map_status_error
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


class DeepSeekProvider(Provider):
    """Adapter for DeepSeek's OpenAI-compatible chat API."""

    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        timeout_seconds: float = 60.0,
        outbound_proxy: str | None = None,
    ) -> None:
        # Двухсерверная архитектура (CEO 2026-04-30): см. OpenAIProvider.
        timeout = httpx.Timeout(timeout_seconds, read=None, connect=10.0)
        sdk_kwargs: dict[str, object] = {
            "api_key": api_key,
            "base_url": base_url,
            "max_retries": 0,
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
        if self._http_client is not None:
            await self._http_client.aclose()

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
            kwargs["max_tokens"] = req.max_tokens
        if req.stop is not None:
            kwargs["stop"] = req.stop
        # Forward whitelisted extras. DeepSeek supports these per their docs.
        for k in (
            "response_format",
            "tools",
            "tool_choice",
            "frequency_penalty",
            "presence_penalty",
            "logprobs",
            "top_logprobs",
            "stream_options",
        ):
            v = req.extra.get(k)
            if v is not None and k not in kwargs:
                kwargs[k] = v
        if stream:
            stream_opts = dict(kwargs.get("stream_options") or {})
            stream_opts.setdefault("include_usage", True)
            kwargs["stream_options"] = stream_opts
        return kwargs

    @staticmethod
    def _wrap_error(exc: Exception) -> ProviderError:
        # Same SDK as OpenAI — same error mapping.
        from voltari_gateway.providers.openai_provider import OpenAIProvider

        return OpenAIProvider._wrap_error(exc)

    # ---- non-stream ----------------------------------------------------------

    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        kwargs = self._build_kwargs(req, stream=False)
        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
            log.warning("deepseek_error", model=req.model.id, error=str(exc))
            raise self._wrap_error(exc) from exc
        except Exception as exc:  # pragma: no cover
            log.exception("deepseek_unexpected_error", model=req.model.id)
            raise ProviderError(str(exc)) from exc

        raw = completion.model_dump()
        usage_dict = raw.get("usage") or {}
        # DeepSeek-specific: prompt_cache_hit_tokens / prompt_cache_miss_tokens.
        cache_hit = int(usage_dict.get("prompt_cache_hit_tokens", 0) or 0)
        cache_miss = int(usage_dict.get("prompt_cache_miss_tokens", 0) or 0)
        # ``prompt_tokens`` from upstream already counts both hit and miss.
        prompt_tokens = int(usage_dict.get("prompt_tokens", cache_hit + cache_miss))
        completion_tokens = int(usage_dict.get("completion_tokens", 0))

        # Normalise into the OpenAI-compat shape we use everywhere.
        details = usage_dict.get("prompt_tokens_details") or {}
        details["cached_tokens"] = cache_hit
        usage_dict["prompt_tokens_details"] = details
        raw["usage"] = usage_dict

        usage = ChatCompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(usage_dict.get("total_tokens", prompt_tokens + completion_tokens)),
            cached_tokens=cache_hit,
        )
        # Echo the public-facing model id, not the upstream alias.
        raw["model"] = req.model.id
        return ChatCompletionResponse(
            raw=raw, usage=usage, model_id=req.model.id, provider=self.name
        )

    # ---- stream --------------------------------------------------------------

    async def chat_completion_stream(self, req: ChatCompletionRequest) -> AsyncIterator[bytes]:
        kwargs = self._build_kwargs(req, stream=True)
        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
            log.warning("deepseek_stream_error", model=req.model.id, error=str(exc))
            raise self._wrap_error(exc) from exc

        async def _gen() -> AsyncIterator[bytes]:
            try:
                async for chunk in stream:
                    payload = chunk.model_dump()
                    # Map DeepSeek's cache fields into OpenAI's prompt_tokens_details.
                    usage = payload.get("usage")
                    if usage:
                        cache_hit = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
                        details = usage.get("prompt_tokens_details") or {}
                        details["cached_tokens"] = cache_hit
                        usage["prompt_tokens_details"] = details
                    payload["model"] = req.model.id
                    line = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    yield line.encode("utf-8")
                yield b"data: [DONE]\n\n"
            except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
                log.warning("deepseek_stream_mid_error", model=req.model.id, error=str(exc))
                raise self._wrap_error(exc) from exc
            finally:
                close = getattr(stream, "close", None)
                if close is not None:
                    with contextlib.suppress(Exception):  # pragma: no cover
                        await close()

        return _gen()


# Re-export _map_status_error for symmetry; DeepSeek uses the OpenAI mapping.
__all__ = ["DeepSeekProvider", "_map_status_error"]
