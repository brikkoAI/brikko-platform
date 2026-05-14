"""Moonshot AI (Kimi) provider adapter.

Moonshot exposes an OpenAI-compatible chat-completions endpoint at
``api.moonshot.ai/v1`` (international) — same SDK shape as OpenAI/DeepSeek/
Together, so we subclass ``OpenAIProvider`` and only override the base URL +
``name``. The ``api.moonshot.cn`` mirror also works but sits behind the
Chinese GFW for callers outside CN; we default to the international host.

Notes vs. the parent's surface:

* No prompt-cache discount column in usage payloads — Moonshot's chat
  responses don't return ``prompt_tokens_details.cached_tokens``. The
  inherited ``chat_completion`` reads ``cached_tokens=0`` by default,
  which is what we want.
* No vision in the chat catalog yet (Moonshot has a separate
  ``moonshot-v1-vision`` line — not catalogued here).
* Strict JSON schema (response_format=json_schema strict=true) is NOT
  uniformly supported across the Kimi family. We leave
  ``supports_strict_json=False`` on every catalog entry — the chat layer
  will 400 early if a client pins strict on a Moonshot model.
* Streaming, tools, response_format=json_object: supported for the K2 +
  v1-* family per Moonshot docs (verified 2026-05-10). Inherited
  unchanged from the parent.

Audio / images / TTS: Moonshot does NOT host any of these. We disable
``audio_speech`` to fail fast if someone wires a Moonshot model into a
TTS path by mistake.

Auth: simple Bearer header — same as OpenAI. No JWT signing, no per-call
challenge-response.
"""

from __future__ import annotations

from voltari_gateway.providers.openai_provider import OpenAIProvider, _map_status_error

__all__ = ["MoonshotProvider", "_map_status_error"]


class MoonshotProvider(OpenAIProvider):
    """Adapter for Moonshot AI's OpenAI-compatible Kimi API."""

    name = "moonshot"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.moonshot.ai/v1",
        timeout_seconds: float = 60.0,
        outbound_proxy: str | None = None,
    ) -> None:
        # Moonshot international endpoint is reachable from РФ as of 2026-05.
        # CEO note: when CEO pays via UnionPay, the account lives at
        # platform.moonshot.ai (international) — that's the host that the
        # ``moonshot.ai`` Bearer key actually authenticates against. The
        # ``moonshot.cn`` mirror requires a Chinese-resident account.
        # Routed through outbound_proxy (WireGuard tunnel) like the other
        # foreign providers, so api.moonshot.ai isn't called directly from
        # a РФ IP — same posture as OpenAI/Anthropic/Google/Together.
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            outbound_proxy=outbound_proxy,
        )

    # Moonshot does not host TTS — block the inherited audio_speech path
    # so a misrouted call fails fast instead of POSTing to a non-existent
    # /audio/speech upstream.
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
            "Moonshot AI does not host audio (TTS) models. Pin an OpenAI tts-* model instead."
        )
