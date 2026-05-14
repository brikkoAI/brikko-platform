"""Provider abstraction and shared request/response types.

The gateway speaks OpenAI-compatible JSON to clients on the way in and to
upstream providers on the way out. The chat-completion request/response
schema is intentionally close to the wire format. Each concrete provider
adapter (OpenAI, Anthropic, Google, DeepSeek, Yandex, Sber) implements
``chat_completion`` and ``chat_completion_stream``.

Type sources of truth:

* ``ModelSpec`` lives in ``voltari_gateway.router.catalog`` (consolidated
  in PR 0). Everything in the gateway — providers, billing, router —
  depends on that one definition.
* ``Provider*Error`` classes live in ``voltari_gateway.router.failover``
  so the failover engine can pattern-match on them without circular
  imports. We re-export them here for backwards-compatible imports
  (existing call-sites do ``from voltari_gateway.providers.base import
  ProviderTimeoutError``).
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from voltari_gateway.router.catalog import ModelSpec
from voltari_gateway.router.failover import (
    ProviderAuthError,
    ProviderClientError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)

# ---------- provider request / response ----------------------------------------


@dataclass
class ChatCompletionRequest:
    """Internal representation of a /v1/chat/completions call."""

    model: ModelSpec
    messages: list[dict[str, Any]]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    stop: list[str] | str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatCompletionUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
        }


@dataclass
class ChatCompletionResponse:
    """Provider-normalised response. Already shaped like OpenAI JSON."""

    raw: dict[str, Any]  # the full upstream JSON, ready to return
    usage: ChatCompletionUsage
    model_id: str
    provider: str


# ---------- provider interface -------------------------------------------------


class Provider(abc.ABC):
    """Abstract provider adapter."""

    name: str

    @abc.abstractmethod
    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        """Non-streaming completion. Raises ``ProviderError`` on upstream failure."""

    @abc.abstractmethod
    async def chat_completion_stream(self, req: ChatCompletionRequest) -> AsyncIterator[bytes]:
        """Yield SSE lines verbatim. Each chunk is already ``data: {...}\\n\\n``.

        The implementor MUST emit the trailing ``data: [DONE]\\n\\n`` event,
        and SHOULD include a usage chunk before [DONE] when the upstream
        provider supports it (OpenAI: ``stream_options.include_usage``;
        Anthropic: synthesised from ``message_delta.usage``).
        """

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Release upstream connections / SDK clients."""


# ---------- re-exports (backwards-compat) -------------------------------------

__all__ = [
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ChatCompletionUsage",
    "ModelSpec",
    "Provider",
    "ProviderAuthError",
    "ProviderClientError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderServerError",
    "ProviderTimeoutError",
]
