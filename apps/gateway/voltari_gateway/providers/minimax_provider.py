"""MiniMax (Hailuo) provider adapter.

MiniMax exposes an OpenAI-compatible chat-completions endpoint at
``api.minimaxi.chat/v1`` (international host — note the trailing ``i`` in
``minimaxi``, MiniMax's actual public domain). We subclass
``OpenAIProvider`` and only swap the base URL + ``name``.

Notes vs. the parent's surface:

* No prompt-cache discount column on the M-1 / M-Text-01 / abab6.5 family
  — usage payloads don't carry ``prompt_tokens_details.cached_tokens``,
  so the inherited ``chat_completion`` defaults ``cached_tokens=0``.
* Streaming + tools supported on MiniMax-M1, MiniMax-Text-01, abab6.5*.
* response_format=json_object supported but strict json schema is not —
  ``supports_strict_json=False`` on every catalog entry.
* No audio / image / TTS endpoint on this base URL. There is a separate
  ``api.minimaxi.chat/v1/t2a_v2`` for TTS but its surface is NOT
  OpenAI-shaped — out of scope for this adapter.

Auth: Bearer header with the API key from the MiniMax developer console.
A ``GroupId`` query parameter is sometimes required for non-chat
endpoints (TTS, video) but chat completions accept Bearer alone.
"""

from __future__ import annotations

from voltari_gateway.providers.openai_provider import OpenAIProvider, _map_status_error

__all__ = ["MiniMaxProvider", "_map_status_error"]


class MiniMaxProvider(OpenAIProvider):
    """Adapter for MiniMax's OpenAI-compatible Hailuo chat API."""

    name = "minimax"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.minimaxi.chat/v1",
        timeout_seconds: float = 60.0,
        outbound_proxy: str | None = None,
    ) -> None:
        # api.minimaxi.chat is reachable from РФ but we keep the proxy
        # toggle for symmetry — same code path as every other foreign
        # provider — and to avoid a РФ-IP showing up in MiniMax abuse
        # logs. Direct mode (no proxy) works fine for dev.
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            outbound_proxy=outbound_proxy,
        )

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
        # MiniMax has TTS at /v1/t2a_v2 but it's NOT OpenAI-shaped —
        # different field names, different streaming protocol. If TTS
        # ever gets surfaced through Brikko it'll need its own adapter
        # path, not this fallthrough.
        raise NotImplementedError(
            "MiniMax TTS endpoint exists but is not OpenAI-compatible. "
            "Pin an OpenAI tts-* model instead."
        )
