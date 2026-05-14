"""Zhipu AI (GLM) provider adapter.

Zhipu's BigModel platform exposes an OpenAI-compatible chat-completions
endpoint at ``open.bigmodel.cn/api/paas/v4``. Like the other Chinese
frontier vendors we subclass ``OpenAIProvider`` and override base URL +
``name``.

Auth — important nuance:

Zhipu's API key has the form ``<id>.<secret>``. They support two auth
modes:

1. **Direct Bearer** (newer, 2024+): send ``Authorization: Bearer
   <id>.<secret>`` verbatim. Works for the v4 endpoint and is what we
   use here. Simpler — no per-request signing — and matches every
   other OpenAI-compatible adapter in this codebase.

2. **JWT-signed** (legacy): split the key on ``.``, sign a JWT body
   ``{"api_key": id, "exp": now+3600, "timestamp": now}`` with HS256
   keyed by ``secret``. Some older corporate accounts still require
   this. If Direct Bearer 401-s on a freshly-issued key, the next
   step is to swap auth to JWT — that's a config-time decision, not
   a runtime fallback (we don't want to double the request latency
   on every call to retry).

   To enable JWT mode in the future:
     pip install pyjwt
     def _make_jwt(api_key: str) -> str:
         kid, secret = api_key.split(".", 1)
         payload = {"api_key": kid, "timestamp": int(time.time()*1000),
                    "exp": int(time.time()*1000) + 3600*1000}
         return jwt.encode(payload, secret, algorithm="HS256",
                           headers={"alg":"HS256","sign_type":"SIGN"})
   …and inject via a custom httpx Auth. Tracked as @review.

Notes vs. the parent's surface:

* GLM-4.5 / GLM-4.5-Air / GLM-4-Long / GLM-4V-Plus all expose the chat
  completions surface. Streaming + tools supported.
* response_format=json_object supported. Strict json schema NOT
  uniformly supported — ``supports_strict_json=False`` on every catalog
  entry; the chat layer will 400 early if a client pins strict.
* Zhipu's error JSON shape differs slightly (``{"error": {"code":
  "1234", "message": "..."}}`` with a string code instead of an int)
  but APIStatusError.status_code mapping in ``_map_status_error`` is
  HTTP-status-driven, not body-driven, so the inherited mapping works
  unchanged.
* GLM-4V-Plus is a vision model — clients pass image URLs / base64
  inside the OpenAI ``content: [{"type":"image_url",...}]`` array;
  the upstream accepts it 1:1.

Audio / TTS / images: Zhipu has separate endpoints for these (CogView
for images, ChatGLM TTS) but they are NOT OpenAI-shaped on this base
URL — disabled here.
"""

from __future__ import annotations

from voltari_gateway.providers.openai_provider import OpenAIProvider, _map_status_error

__all__ = ["ZhipuProvider", "_map_status_error"]


class ZhipuProvider(OpenAIProvider):
    """Adapter for Zhipu AI's OpenAI-compatible GLM chat API."""

    name = "zhipu"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds: float = 60.0,
        outbound_proxy: str | None = None,
    ) -> None:
        # Zhipu's open.bigmodel.cn is reachable from РФ as of 2026-05.
        # Routed through outbound_proxy for the same posture-reasons as
        # every other foreign provider — keeps РФ-IPs out of upstream
        # access logs. Direct mode works for dev.
        #
        # NOTE: api_key here is the full ``<id>.<secret>`` string from
        # the Zhipu console. We forward it verbatim as Bearer; see
        # module docstring for the JWT alternative if Direct Bearer
        # ever 401-s.
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
        # Zhipu has ChatGLM TTS but not on this base_url and not in
        # OpenAI shape. Block the inherited path.
        raise NotImplementedError(
            "Zhipu AI TTS lives on a separate non-OpenAI-shaped endpoint. "
            "Pin an OpenAI tts-* model instead."
        )
