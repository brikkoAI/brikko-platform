"""Catalogue of non-chat modalities (STT, TTS, Embeddings, Image).

Why a separate module from ``catalog.py``:

* The chat catalog is consumed by the smart router (``strategies.py``),
  which iterates over every entry and scores them on chat-shaped fields
  (input/output token prices, context_window, supports_streaming…).
  Dropping STT/TTS/embedding/image entries into the same tuple would
  either pollute ``auto:cheap`` candidate sets or force every strategy
  to filter by modality. Keeping the catalogs distinct preserves the
  router's invariant — *every model in CATALOG is a chat model* — and
  costs us one extra import in the modality endpoints.

* Each non-chat modality has its own billable unit:

  - STT — minutes of audio.
  - TTS — input characters (NOT tokens — OpenAI bills tts on chars).
  - Embeddings — input tokens.
  - Image — N images generated (price varies by quality + size).

  The chat ``ModelSpec`` is shaped around input/output token prices;
  bolting char/minute/image costs onto it would pollute that surface.
  Per-modality dataclasses keep the catalog honest.

Pricing — kopecks per billable unit, after applying the +15% Brikko
markup (BRIEF §7) but BEFORE the per-call rounding. We store the
*marketed* price (what a customer is charged) so the handlers don't
have to call ``compute_cost_kopecks`` for these — they multiply
``units × price_kop_per_unit`` and quantise.

Conversion examples (USD_RUB = 80, markup = 1.15):

* Whisper-1            $0.006/min   × 80 × 1.15 = ₽0.552/min   ≈ 55.2 kop/min
* embedding-3-small    $0.02/1M tok × 80 × 1.15 = ₽1.84/1M     = 184 kop/1M
* gpt-4o-mini-tts      $0.60/1M chr × 80 × 1.15 = ₽55.2/1M chr = 5520 kop/1M chr
* gpt-image-1 (std)    $0.04/image  × 80 × 1.15 = ₽3.68/image  = 368 kop/image

Round-half-even is applied at the *call* site (in ``api/audio.py``,
``api/embeddings.py``, ``api/images.py``), not at catalog-load — so
``Decimal`` precision survives until we hit the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from voltari_gateway.router.catalog import USD_RUB, Provider

# BRIEF §7 — gateway adds 15% over provider COGS. Re-declared as a
# module-local Decimal so we don't import from ``billing`` (would
# create a backwards dep: catalog → billing).
_MARKUP: Decimal = Decimal("1.15")
_KOPECK_QUANTUM: Decimal = Decimal("1")


class Modality(StrEnum):
    """Non-chat modalities exposed by Brikko."""

    STT = "stt"
    TTS = "tts"
    EMBEDDINGS = "embeddings"
    IMAGE = "image"


@dataclass(frozen=True, slots=True)
class STTModelSpec:
    """Speech-to-Text model — billed per minute of audio.

    ``upstream_id`` is what we send the provider. Public ``id`` matches
    OpenAI naming so SDK clients don't need a translation map.

    ``max_file_size_bytes`` mirrors the upstream limit (OpenAI: 25 MB).
    Enforced in the API handler before forwarding so a 100 MB upload
    doesn't burn our outbound bandwidth on a guaranteed reject.
    """

    id: str
    provider: Provider
    upstream_id: str
    # USD per minute of audio (provider list price). Source of truth — kop
    # price is computed from this, so changing the rate is a one-line edit.
    usd_per_minute: Decimal
    max_file_size_bytes: int = 25 * 1024 * 1024
    # ISO-639-1 codes the provider documents support for. We don't validate
    # against this list at request time (OpenAI quietly accepts unsupported
    # codes and falls back to autodetection); it's surfaced by /v1/models
    # for documentation only.
    languages: tuple[str, ...] = ()
    description: str = ""
    deprecated: bool = False

    @property
    def kop_per_minute(self) -> Decimal:
        """Customer-facing price: USD × USD_RUB × markup, RUB → kopecks."""
        return self.usd_per_minute * USD_RUB * _MARKUP * Decimal(100)

    def cost_kopecks(self, minutes: Decimal) -> int:
        """Round half-even to whole kopecks. ``minutes`` is Decimal so the
        caller controls how aggressively duration is rounded up."""
        raw = self.kop_per_minute * minutes
        return int(raw.quantize(_KOPECK_QUANTUM, rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True, slots=True)
class EmbeddingModelSpec:
    """Text embeddings model — billed per input token.

    ``dimensions`` is the *default* vector dimension. OpenAI's embedding-3
    series accepts a ``dimensions`` parameter to truncate; we forward it
    to the provider unchanged but bill on tokens (not on output size).
    """

    id: str
    provider: Provider
    upstream_id: str
    # USD per 1M input tokens (provider list price).
    usd_per_million_tokens: Decimal
    dimensions: int
    max_input_tokens: int = 8192
    description: str = ""
    deprecated: bool = False

    @property
    def kop_per_million_tokens(self) -> Decimal:
        """Customer-facing price for 1M input tokens, in kopecks."""
        return self.usd_per_million_tokens * USD_RUB * _MARKUP * Decimal(100)

    def cost_kopecks(self, tokens: int) -> int:
        """Round half-even to whole kopecks."""
        if tokens <= 0:
            return 0
        raw = self.kop_per_million_tokens * Decimal(tokens) / Decimal(1_000_000)
        return int(raw.quantize(_KOPECK_QUANTUM, rounding=ROUND_HALF_EVEN))


# ---------------------------------------------------------------------------
# Catalog — STT
# ---------------------------------------------------------------------------
# Whisper-1 is the cheapest OpenAI STT path ($0.006/min, 25 MB file cap).
# Sprint M1 — Groq Whisper-large-v3-turbo is 9× cheaper but blocked on
# legal/санкционная проверка (research-отчёт §C.3). When Groq lands, add
# a sibling STTModelSpec with provider=Provider.GROQ and the router falls
# back through the ``provider`` filter.

STT_CATALOG: tuple[STTModelSpec, ...] = (
    STTModelSpec(
        id="whisper-1",
        provider=Provider.OPENAI,
        upstream_id="whisper-1",
        usd_per_minute=Decimal("0.006"),
        # Multilingual model — supports 50+ languages per OpenAI docs;
        # we list the ones our customers ask about most so /v1/models
        # documentation isn't useless.
        languages=("en", "ru", "es", "fr", "de", "it", "pt", "zh", "ja", "ko"),
        description="OpenAI Whisper — multilingual STT, $0.006/min, 25 MB file cap.",
    ),
)


# ---------------------------------------------------------------------------
# Catalog — Embeddings
# ---------------------------------------------------------------------------
# Sprint M1: embedding-3-small (commodity default) + embedding-3-large
# (premium). Both share the same upstream auth/endpoint; the only thing
# that varies is price and default dimension count.

EMBEDDING_CATALOG: tuple[EmbeddingModelSpec, ...] = (
    EmbeddingModelSpec(
        id="text-embedding-3-small",
        provider=Provider.OPENAI,
        upstream_id="text-embedding-3-small",
        usd_per_million_tokens=Decimal("0.02"),
        dimensions=1536,
        description="OpenAI text-embedding-3-small — RAG/search default, 1536-dim.",
    ),
    EmbeddingModelSpec(
        id="text-embedding-3-large",
        provider=Provider.OPENAI,
        upstream_id="text-embedding-3-large",
        usd_per_million_tokens=Decimal("0.13"),
        dimensions=3072,
        description="OpenAI text-embedding-3-large — premium quality, 3072-dim.",
    ),
    # Sprint M3 (2026-05-10) — Together.ai embeddings.
    # NOTE: api/embeddings.py currently hardcodes the OpenAI base_url. Adding
    # provider=together to the catalog surfaces these models in /v1/models
    # but a request to e.g. ``model: "bge-large-en"`` will still hit
    # api.openai.com → 404. Routing the embeddings endpoint through the
    # provider registry is a separate task (TD-Together-1, see api/embeddings.py).
    EmbeddingModelSpec(
        id="bge-large-en",
        provider=Provider.TOGETHER,
        upstream_id="BAAI/bge-large-en-v1.5",
        usd_per_million_tokens=Decimal("0.016"),
        dimensions=1024,
        max_input_tokens=512,
        description="BAAI BGE Large EN v1.5 — top open-weight retrieval embedding.",
    ),
    EmbeddingModelSpec(
        id="bge-base-en",
        provider=Provider.TOGETHER,
        upstream_id="BAAI/bge-base-en-v1.5",
        usd_per_million_tokens=Decimal("0.008"),
        dimensions=768,
        max_input_tokens=512,
        description="BAAI BGE Base EN v1.5 — cheap small open-weight embedding.",
    ),
    EmbeddingModelSpec(
        id="m2-bert-32k",
        provider=Provider.TOGETHER,
        upstream_id="togethercomputer/m2-bert-80M-32k-retrieval",
        usd_per_million_tokens=Decimal("0.008"),
        dimensions=768,
        max_input_tokens=32_768,
        description="Together M2-BERT 80M — long-context (32k) retrieval embedding.",
    ),
)


# ---------------------------------------------------------------------------
# TTS — Text-to-Speech, billed per input character
# ---------------------------------------------------------------------------
# OpenAI TTS pricing is per-character (NOT per-token), e.g. gpt-4o-mini-tts
# at $0.60/1M chars. Output (audio bytes) is unmetered — the cost is fully
# carried by the input. Voices are a single shared catalog across all OpenAI
# TTS models; we validate the voice value at the API layer.

TTS_VALID_VOICES: tuple[str, ...] = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "onyx",
    "nova",
    "sage",
    "shimmer",
    "verse",
)
TTS_VALID_FORMATS: tuple[str, ...] = ("mp3", "opus", "aac", "flac", "wav", "pcm")


@dataclass(frozen=True, slots=True)
class TTSModelSpec:
    """Text-to-Speech model — billed per input character.

    ``usd_per_million_chars`` is the provider list price; we apply the
    +15% Brikko markup at the ``kop_per_million_chars`` accessor.

    ``max_input_chars`` mirrors OpenAI's documented hard cap (4096 chars
    per request as of 2026-05). Enforced in the API handler before
    forwarding so a 100K-char prompt doesn't burn outbound bandwidth on
    a guaranteed 400 reject.
    """

    id: str
    provider: Provider
    upstream_id: str
    # USD per 1M input characters (provider list price).
    usd_per_million_chars: Decimal
    max_input_chars: int = 4096
    description: str = ""
    deprecated: bool = False

    @property
    def kop_per_million_chars(self) -> Decimal:
        """Customer-facing price for 1M input chars, in kopecks."""
        return self.usd_per_million_chars * USD_RUB * _MARKUP * Decimal(100)

    def cost_kopecks(self, input_chars: int) -> int:
        """Round half-even to whole kopecks. Empty input → 0 (caller validates)."""
        if input_chars <= 0:
            return 0
        raw = self.kop_per_million_chars * Decimal(input_chars) / Decimal(1_000_000)
        return int(raw.quantize(_KOPECK_QUANTUM, rounding=ROUND_HALF_EVEN))


TTS_CATALOG: tuple[TTSModelSpec, ...] = (
    # gpt-4o-mini-tts — current OpenAI default (2026-05). Drop-in for
    # tts-1: same voices, same response_format, but expressive prompts
    # (the model honours "speak slowly", "with a French accent" etc).
    TTSModelSpec(
        id="gpt-4o-mini-tts",
        provider=Provider.OPENAI,
        upstream_id="gpt-4o-mini-tts",
        usd_per_million_chars=Decimal("0.60"),
        description="OpenAI gpt-4o-mini-tts — current TTS default, expressive prompting.",
    ),
    # tts-1 — legacy, still supported. Cheap on input metering by char
    # but more expensive USD/1M than the new gpt-4o-mini-tts. Some clients
    # pin to it for known voice artefacts; keep for backwards compat.
    TTSModelSpec(
        id="tts-1",
        provider=Provider.OPENAI,
        upstream_id="tts-1",
        usd_per_million_chars=Decimal("15.00"),
        description="OpenAI tts-1 — legacy, 15$/1M chars.",
    ),
    # tts-1-hd — high-definition voice, double the price of tts-1.
    TTSModelSpec(
        id="tts-1-hd",
        provider=Provider.OPENAI,
        upstream_id="tts-1-hd",
        usd_per_million_chars=Decimal("30.00"),
        description="OpenAI tts-1-hd — high-definition voice, 30$/1M chars.",
    ),
)


# ---------------------------------------------------------------------------
# Image — Image generation, billed per generated image
# ---------------------------------------------------------------------------
# Image pricing varies by (model, quality, size). We hold a small lookup
# table per spec keyed by (quality, size) → USD/image. The handler resolves
# the right cell at request time and multiplies by ``n``.
#
# Why flat per-image (not per-input-token): gpt-image-1 actually charges
# input tokens for the prompt PLUS output tokens for the generated image
# representation. That's a complicated billing surface; for MVP we charge
# a flat per-image fee that's high enough to cover both legs at typical
# prompt lengths (~50 tokens). When CEO wants finer-grained billing, we
# add a per-token leg here without changing the API contract.


@dataclass(frozen=True, slots=True)
class ImageModelSpec:
    """Image generation model — billed per generated image (count).

    ``price_table`` maps (quality, size) → USD per image. The handler
    resolves per-request and multiplies by ``n``.

    ``default_quality`` and ``default_size`` are used when the request
    omits those fields (matches the upstream's defaults).

    ``valid_qualities`` / ``valid_sizes`` are validated at the API layer
    so we surface a clean 400 instead of paying for a 400 from upstream.
    """

    id: str
    provider: Provider
    upstream_id: str
    # (quality, size) → USD per image. Quality "auto" / size "auto" must
    # be present if the model accepts them; otherwise the API layer
    # rejects the request.
    price_table: dict[tuple[str, str], Decimal]
    valid_qualities: tuple[str, ...]
    valid_sizes: tuple[str, ...]
    default_quality: str
    default_size: str
    max_n: int = 10
    description: str = ""
    deprecated: bool = False

    def usd_per_image(self, quality: str, size: str) -> Decimal:
        """Resolve USD/image for given quality+size, with sensible fallback.

        We try (quality, size) → (quality, "*") → ("*", size) → ("*", "*"),
        falling back to the most expensive cell if nothing matches (so we
        never under-bill on an unknown combination).
        """
        for key in [(quality, size), (quality, "*"), ("*", size), ("*", "*")]:
            if key in self.price_table:
                return self.price_table[key]
        # Unknown combo: bill at the catalog's maximum so we don't lose money
        # if OpenAI silently introduces a new size.
        return max(self.price_table.values())

    def cost_kopecks(self, *, n: int, quality: str, size: str) -> int:
        """Total cost for ``n`` images at the resolved (quality, size) cell."""
        if n <= 0:
            return 0
        usd = self.usd_per_image(quality, size)
        # USD → kopecks: USD × USD_RUB × markup × 100 (kopecks per ₽)
        per_image_kop = usd * USD_RUB * _MARKUP * Decimal(100)
        raw = per_image_kop * Decimal(n)
        return int(raw.quantize(_KOPECK_QUANTUM, rounding=ROUND_HALF_EVEN))


# Image catalog — OpenAI hosted models only for MVP.
# Prices verified against https://platform.openai.com/docs/pricing 2026-05-09.
# gpt-image-1: $0.011 (low) / $0.042 (medium) / $0.167 (high) for 1024x1024.
# We bin to standard/hd buckets below for simplicity; the true OpenAI table
# has ~12 cells (3 qualities × 4 sizes). When CEO wants the finer surface,
# fill price_table out — no API changes required.
IMAGE_CATALOG: tuple[ImageModelSpec, ...] = (
    ImageModelSpec(
        id="gpt-image-1",
        provider=Provider.OPENAI,
        upstream_id="gpt-image-1",
        # Coarse three-bucket table. ``auto`` falls back to ``medium`` cost.
        price_table={
            ("low", "*"): Decimal("0.011"),
            ("medium", "*"): Decimal("0.042"),
            ("high", "*"): Decimal("0.167"),
            ("auto", "*"): Decimal("0.042"),
        },
        valid_qualities=("low", "medium", "high", "auto"),
        valid_sizes=("1024x1024", "1024x1536", "1536x1024", "auto"),
        default_quality="auto",
        default_size="1024x1024",
        description="OpenAI gpt-image-1 — current image-gen default, multi-quality.",
    ),
    ImageModelSpec(
        id="dall-e-3",
        provider=Provider.OPENAI,
        upstream_id="dall-e-3",
        # DALL·E 3 has only "standard" / "hd" qualities and three sizes.
        price_table={
            ("standard", "1024x1024"): Decimal("0.040"),
            ("standard", "1024x1792"): Decimal("0.080"),
            ("standard", "1792x1024"): Decimal("0.080"),
            ("hd", "1024x1024"): Decimal("0.080"),
            ("hd", "1024x1792"): Decimal("0.120"),
            ("hd", "1792x1024"): Decimal("0.120"),
        },
        valid_qualities=("standard", "hd"),
        valid_sizes=("1024x1024", "1024x1792", "1792x1024"),
        default_quality="standard",
        default_size="1024x1024",
        max_n=1,  # DALL·E 3 hard-caps n=1 upstream.
        description="OpenAI DALL·E 3 — premium image gen, n=1 only.",
    ),
    ImageModelSpec(
        id="dall-e-2",
        provider=Provider.OPENAI,
        upstream_id="dall-e-2",
        price_table={
            ("standard", "256x256"): Decimal("0.016"),
            ("standard", "512x512"): Decimal("0.018"),
            ("standard", "1024x1024"): Decimal("0.020"),
        },
        valid_qualities=("standard",),
        valid_sizes=("256x256", "512x512", "1024x1024"),
        default_quality="standard",
        default_size="1024x1024",
        description="OpenAI DALL·E 2 — legacy budget image gen.",
    ),
    # Sprint M3 (2026-05-10) — Together.ai image generation.
    # Together image API is OpenAI-compatible at /v1/images/generations.
    # Pricing source: https://www.together.ai/pricing 2026-05-10. We use
    # flat per-image rates (their dev/schnell models are technically billed
    # by step but at default step counts the per-image total is stable —
    # if a customer overrides ``steps`` extra (TD-Together-2), we'll need
    # a step-aware billing path).
    ImageModelSpec(
        id="flux-pro",
        provider=Provider.TOGETHER,
        upstream_id="black-forest-labs/FLUX.1-pro",
        price_table={
            ("*", "*"): Decimal("0.05"),
        },
        valid_qualities=("standard", "auto"),
        valid_sizes=("1024x1024", "1024x768", "768x1024", "auto"),
        default_quality="standard",
        default_size="1024x1024",
        description="Black Forest Labs FLUX.1-pro — top-tier image-gen via Together.",
    ),
    ImageModelSpec(
        id="flux-schnell",
        provider=Provider.TOGETHER,
        upstream_id="black-forest-labs/FLUX.1-schnell",
        # Schnell at default 4 steps ≈ $0.003/image flat. Cheap, fast.
        price_table={
            ("*", "*"): Decimal("0.003"),
        },
        valid_qualities=("standard", "auto"),
        valid_sizes=("1024x1024", "1024x768", "768x1024", "auto"),
        default_quality="standard",
        default_size="1024x1024",
        description="Black Forest Labs FLUX.1-schnell — fast cheap image-gen.",
    ),
    ImageModelSpec(
        id="flux-dev",
        provider=Provider.TOGETHER,
        upstream_id="black-forest-labs/FLUX.1-dev",
        # Dev at default 28 steps ≈ $0.025/image flat.
        price_table={
            ("*", "*"): Decimal("0.025"),
        },
        valid_qualities=("standard", "auto"),
        valid_sizes=("1024x1024", "1024x768", "768x1024", "auto"),
        default_quality="standard",
        default_size="1024x1024",
        description="Black Forest Labs FLUX.1-dev — quality-focused image-gen.",
    ),
    ImageModelSpec(
        id="sdxl-base",
        provider=Provider.TOGETHER,
        upstream_id="stabilityai/stable-diffusion-xl-base-1.0",
        price_table={
            ("*", "*"): Decimal("0.01"),
        },
        valid_qualities=("standard", "auto"),
        valid_sizes=("1024x1024", "1024x768", "768x1024", "auto"),
        default_quality="standard",
        default_size="1024x1024",
        description="Stability AI SDXL Base 1.0 — workhorse OSS image-gen.",
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

_STT_BY_ID: dict[str, STTModelSpec] = {m.id: m for m in STT_CATALOG}
_EMBED_BY_ID: dict[str, EmbeddingModelSpec] = {m.id: m for m in EMBEDDING_CATALOG}
_TTS_BY_ID: dict[str, TTSModelSpec] = {m.id: m for m in TTS_CATALOG}
_IMAGE_BY_ID: dict[str, ImageModelSpec] = {m.id: m for m in IMAGE_CATALOG}


def get_stt_model(model_id: str) -> STTModelSpec | None:
    return _STT_BY_ID.get(model_id)


def get_embedding_model(model_id: str) -> EmbeddingModelSpec | None:
    return _EMBED_BY_ID.get(model_id)


def get_tts_model(model_id: str) -> TTSModelSpec | None:
    return _TTS_BY_ID.get(model_id)


def get_image_model(model_id: str) -> ImageModelSpec | None:
    return _IMAGE_BY_ID.get(model_id)


def list_stt_models() -> list[STTModelSpec]:
    return list(STT_CATALOG)


def list_embedding_models() -> list[EmbeddingModelSpec]:
    return list(EMBEDDING_CATALOG)


def list_tts_models() -> list[TTSModelSpec]:
    return list(TTS_CATALOG)


def list_image_models() -> list[ImageModelSpec]:
    return list(IMAGE_CATALOG)


__all__ = [
    "EMBEDDING_CATALOG",
    "IMAGE_CATALOG",
    "STT_CATALOG",
    "TTS_CATALOG",
    "TTS_VALID_FORMATS",
    "TTS_VALID_VOICES",
    "EmbeddingModelSpec",
    "ImageModelSpec",
    "Modality",
    "STTModelSpec",
    "TTSModelSpec",
    "get_embedding_model",
    "get_image_model",
    "get_stt_model",
    "get_tts_model",
    "list_embedding_models",
    "list_image_models",
    "list_stt_models",
    "list_tts_models",
]
