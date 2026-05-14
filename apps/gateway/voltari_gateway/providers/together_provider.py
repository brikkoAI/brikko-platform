"""Together.ai provider adapter.

Together.ai exposes an OpenAI-compatible API at ``api.together.xyz/v1``
covering chat completions, embeddings, and image generation for a wide
catalog of OSS models (Llama, Qwen, Mixtral, FLUX, …). Like
``DeepSeekProvider`` we reuse the existing ``OpenAIProvider`` plumbing —
the SDK, the kwargs builder, the streaming SSE pass-through, and the
status-code → ``Provider*Error`` mapping all transfer one-to-one. The
only meaningful differences from OpenAI's surface:

* No prompt-cache discount on Together (the catalog stores
  ``cached_price_kop_per_1k == input_price_kop_per_1k``). Their usage
  payload doesn't include ``prompt_tokens_details.cached_tokens``, so we
  don't need DeepSeek's cache-translation step.
* No ``response_format=json_schema`` strict mode for most OSS models —
  ``supports_strict_json=False`` is the default in the catalog. The
  router never sends a strict-json request to Together unless the
  client pins the model.
* Audio (TTS / STT) — Together does NOT host audio models. We
  deliberately do NOT inherit ``audio_speech`` from
  ``OpenAIProvider``; the api/audio.py routes by spec.provider and
  TTS_CATALOG holds OpenAI-only entries, so no Together audio path
  exists. Calling ``audio_speech`` on a TogetherProvider would 404
  upstream — keeping the method off the class makes that a
  ``hasattr(...) is False`` instead of a runtime POST.
* Image generation — Together's ``/v1/images/generations`` endpoint
  IS OpenAI-shape; we inherit ``image_generate`` unchanged from the
  parent. Together uses different parameters (``steps``,
  ``guidance_scale``) but at default step counts the OpenAI body is
  accepted. Sizes are limited (1024x1024, 1024x768, 768x1024) — the
  IMAGE_CATALOG enforces the right ones at the API layer.

Streaming, tools, retries-via-failover are inherited verbatim. The
adapter overrides ``name`` and the SDK base_url; that's all.
"""

from __future__ import annotations

from voltari_gateway.providers.openai_provider import OpenAIProvider, _map_status_error

__all__ = ["TogetherProvider", "_map_status_error"]


class TogetherProvider(OpenAIProvider):
    """Adapter for Together.ai's OpenAI-compatible API."""

    name = "together"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.together.xyz/v1",
        timeout_seconds: float = 60.0,
        outbound_proxy: str | None = None,
    ) -> None:
        # Together is hosted in the US — same proxy story as OpenAI /
        # Anthropic / Google: we route through the WireGuard outbound
        # proxy from the РФ stack so api.together.xyz isn't called
        # directly from a РФ-IP address. Direct (no proxy) is fine for
        # dev/local; main.py wires settings.outbound_http_proxy.
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            outbound_proxy=outbound_proxy,
        )

    # NOTE: chat_completion, chat_completion_stream, image_generate, aclose
    # all inherit from OpenAIProvider unchanged. We deliberately do NOT
    # expose ``audio_speech`` — Together doesn't host TTS, so leaving the
    # method inherited would silently 404 on the upstream. The api/audio.py
    # call site does ``getattr(provider, "audio_speech", None)`` style
    # guards but our defence-in-depth: if a TogetherProvider ends up
    # routing audio (it shouldn't — TTS_CATALOG has no Together entries),
    # we want it to fail fast, not POST garbage to api.together.xyz.
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
        raise NotImplementedError(
            "Together.ai does not host audio (TTS) models. Pin an OpenAI tts-* model instead."
        )
