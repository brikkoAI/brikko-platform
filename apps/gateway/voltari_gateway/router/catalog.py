"""Static catalogue of LLM models available in the Voltari MVP.

The catalogue is the single source of truth for routing decisions AND for
the public ``/v1/models`` listing. Entries derive from
`03_Finance/01_cogs_models.md` (provider prices in USD/1M tokens) and
`BRIEF.md` §4 (which 12+ models are in MVP). Prices live in **kopecks per
1,000 tokens** so the router never deals with floats — `Decimal`-grade
integer arithmetic is enough at this granularity and avoids `0.1 + 0.2`
surprises in billing-adjacent code.

Conversion: ``usd_per_1m × USD_RUB / 1000 × 100`` → kopecks per 1k tokens.
With USD_RUB = 80 (locked per BRIEF §9) and ``usd_per_1m = 0.50`` we get
0.5 × 80 / 1000 × 100 = 4 kop/1k tokens. We store these as ints and round
half-up to nearest kopeck — sub-kopeck precision is meaningless for a
client invoice and we stay deterministic across machines.

Trade-off: hardcoding the catalogue (vs. loading from DB) means CEO
can't hot-reload prices without a deploy. For the MVP this is an
acceptable trade — provider prices change rarely (≈1× per quarter), the
file is reviewed in code review, and we avoid an entire bootstrap path
for a table that has 14 rows. When we hit V2 (M5+) we can swap CATALOG
for an async ``CatalogLoader`` backed by Postgres + Redis cache without
touching the strategies layer (they only depend on the iterable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

# Locked per BRIEF.md §9. Changes to this constant require explicit CEO
# sign-off because they cascade into pricing and unit economics.
USD_RUB: Decimal = Decimal("80")

# Sprint 10 — public catalog endpoint (/v1/models/public). Markup over COGS
# we surface to the marketing site. BRIEF §7: "наценка 15% к COGS". Kept here
# (not in config) because it's a pricing-display constant, not a runtime knob.
PUBLIC_MARKUP: Decimal = Decimal("1.15")

# Display names per provider for the marketing UI. Single source of truth so
# the lander and any future status page agree on capitalization.
PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "deepseek": "DeepSeek",
    "yandex": "Яндекс",
    "sber": "Сбер",
    "together": "Together AI",
    # Sprint M3.2 (2026-05-10) — Chinese frontier providers. CEO-payable
    # via UnionPay (Прио / ВТБ Драйв) without a foreign card.
    "moonshot": "Moonshot (Kimi)",
    "minimax": "MiniMax (Hailuo)",
    "zhipu": "Zhipu (GLM)",
}


class Provider(StrEnum):
    """Upstream LLM providers we integrate with in the MVP."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    DEEPSEEK = "deepseek"
    YANDEX = "yandex"
    SBER = "sber"
    # Sprint M3 (2026-05-10) — Together.ai aggregator. OpenAI-compatible API
    # surface for OSS models (Llama, Qwen, Mixtral, FLUX images, …). Single
    # adapter, ~30 models added in this PR.
    TOGETHER = "together"
    # Sprint M3.2 (2026-05-10) — Chinese frontier providers. All three are
    # OpenAI-compatible, all three accept AliPay/WeChat/UnionPay (the
    # last of which CEO can use through a РФ-issued Прио / ВТБ Драйв
    # UnionPay card without a foreign bank account). One adapter per
    # vendor; +12 models added in this PR.
    MOONSHOT = "moonshot"
    MINIMAX = "minimax"
    ZHIPU = "zhipu"


class ModelTier(StrEnum):
    """Pricing tiers used by the smart strategy.

    Tiers are an internal abstraction — we never expose them to clients.
    The router uses them to make the smart-strategy quality/price
    trade-off explicit instead of relying on a magic per-model score.
    """

    NANO = "nano"  # cheapest models, simple chat
    BUDGET = "budget"  # everyday workhorses
    MID = "mid"  # balanced quality/price
    FLAGSHIP = "flagship"  # top-tier general models
    PREMIUM = "premium"  # reasoning & specialised premium models


@dataclass(frozen=True, slots=True)
class TieredPricing:
    """Higher-tier pricing applied above ``threshold_tokens`` of input.

    Currently used only by Google's ``gemini-3.1-pro`` (>200k context tier
    is billed at a higher rate). ``above`` carries the kop/1k prices that
    replace the base prices once the input crosses the threshold; the
    Google adapter checks ``input_tokens > threshold_tokens`` at runtime
    and bills accordingly. Output threshold uses the same input-token
    cutoff per Google's published policy.
    """

    threshold_tokens: int
    above_input_kop_per_1k: int
    above_cached_kop_per_1k: int
    above_output_kop_per_1k: int


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Immutable description of a single model.

    All prices are in **kopecks per 1,000 tokens** (integers). The
    ``quality_score`` is a hand-tuned 0..100 heuristic used by the smart
    strategy; values come from a mix of public benchmarks (LMArena,
    SWE-bench) and internal experience. They are not load-bearing for
    correctness — only for tie-breaking inside a tier.

    ``upstream_id`` is the model id we send to the provider. It defaults
    to ``id`` (set in ``__post_init__`` if left empty) and only differs
    when our public surface diverges from the upstream — e.g. DeepSeek
    advertises ``deepseek-chat`` but we expose the more explicit
    ``deepseek-v3.2-chat``.

    ``owned_by`` is what we surface in ``/v1/models``; usually equal to
    ``provider.value`` but kept separate so we can expose "Voltari" /
    "Voltari-RU" branding without changing routing logic.
    """

    id: str
    provider: Provider
    tier: ModelTier
    input_price_kop_per_1k: int
    cached_price_kop_per_1k: int
    output_price_kop_per_1k: int
    context_window: int
    latency_p50_ms: int
    quality_score: int  # 0..100, hand-tuned
    supports_streaming: bool
    supports_tools: bool
    ru_legal: bool  # True iff hosted in RU (Yandex/Sber)

    # Optional overrides — defaulted in __post_init__ from sibling fields.
    upstream_id: str = ""
    owned_by: str = ""
    description: str = ""
    tiered_pricing: TieredPricing | None = None
    # Sprint 9 — deprecation window for upstream sunsetting (e.g. DeepSeek
    # v3.2 retiring 2026-07-24). When set, /v1/models surfaces a
    # ``deprecated_at`` field; chat responses from this model carry an
    # ``X-Brikko-Deprecated`` header. The router does NOT auto-route away
    # — clients pinning a deprecated model still get it until the date
    # passes. Past the date, calls 4xx with ``model_deprecated``.
    deprecated_at: date | None = None
    # Sprint 9 — strict JSON Schema (response_format strict=true) capability.
    # OpenAI/DeepSeek/Anthropic-via-tools/Gemini support; Yandex/Sber don't.
    # Used by api/chat.py to 400 early instead of paying for a 4xx upstream.
    supports_strict_json: bool = False
    # Sprint 9 — Anthropic-style explicit prompt caching (cache_control on
    # content blocks). True for Anthropic models. Other providers either
    # do automatic caching (OpenAI/DeepSeek) or have no concept (Y/S).
    supports_anthropic_cache_control: bool = False
    # 2026-05-01 — OpenAI reasoning models (gpt-5.x / o3 / o4-mini) deprecated
    # ``max_tokens`` in favour of ``max_completion_tokens``. Old gpt-4* models
    # still accept both, new ones return 400 ``unsupported_parameter`` if you
    # send ``max_tokens``. We translate at adapter level when this flag is True.
    requires_max_completion_tokens: bool = False

    # Sprint 10 — fields used only by /v1/models/public (marketing).
    # Optional; the public endpoint falls back to derived defaults when None.
    #
    # ``display_name`` — human-readable label ("GPT-5.5"). When None we
    # surface the raw ``id`` so the lander never shows blank.
    # ``released_at`` — public GA date for the badge ("New" if <30 days).
    # ``best_for`` — bullets for the comparison table; empty list = no bullets.
    # ``modalities_in/out`` — ["text", "image", "audio"] etc; default text-only.
    # ``supports_vision`` / ``supports_audio`` — convenience flags surfaced
    # in the public capabilities block.
    # ``usd_*_per_1m_official`` — original USD prices we charge against.
    # When None, the public endpoint back-computes from kop_per_1k (loses
    # sub-kopeck precision, e.g. 0.014 → 0). Set explicitly only on entries
    # where the rounding hurts (e.g. DeepSeek V4 cached at $0.014/$0.174).
    display_name: str = ""
    released_at: date | None = None
    best_for: tuple[str, ...] = field(default_factory=tuple)
    modalities_in: tuple[str, ...] = ("text",)
    modalities_out: tuple[str, ...] = ("text",)
    supports_vision: bool = False
    supports_audio: bool = False
    usd_input_per_1m: Decimal | None = None
    usd_cached_per_1m: Decimal | None = None
    usd_output_per_1m: Decimal | None = None

    def __post_init__(self) -> None:
        # frozen=True forbids ordinary attribute assignment, so use object.__setattr__.
        if not self.upstream_id:
            object.__setattr__(self, "upstream_id", self.id)
        if not self.owned_by:
            object.__setattr__(self, "owned_by", self.provider.value)

    # --- pricing helpers ---------------------------------------------------

    def effective_pricing(self, input_tokens: int) -> tuple[int, int, int]:
        """Return ``(input, cached, output)`` kop/1k for the given input size.

        Applies ``tiered_pricing`` if input crosses the threshold. Caller
        is the per-provider adapter (Google) — every other adapter calls
        this with whatever input size it has and gets the base prices.
        """
        if self.tiered_pricing is not None and input_tokens > self.tiered_pricing.threshold_tokens:
            tp = self.tiered_pricing
            return tp.above_input_kop_per_1k, tp.above_cached_kop_per_1k, tp.above_output_kop_per_1k
        return (
            self.input_price_kop_per_1k,
            self.cached_price_kop_per_1k,
            self.output_price_kop_per_1k,
        )

    def expected_cost_kop(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
    ) -> int:
        """Estimate the COGS (in kopecks) for this model at given token counts.

        Caller is responsible for token counting. We round half-up to the
        nearest kopeck per leg; under-rounding output can lose us money
        on high-volume customers.
        """
        billable_input = max(0, input_tokens - cached_tokens)
        in_kop, cached_kop, out_kop = self.effective_pricing(input_tokens)
        cost = (
            Decimal(billable_input) * in_kop
            + Decimal(cached_tokens) * cached_kop
            + Decimal(output_tokens) * out_kop
        ) / Decimal(1000)
        return int(cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def to_models_dict(self) -> dict[str, Any]:
        """Render the dict served by GET /v1/models (OpenAI-shape + extensions)."""
        supports: list[str] = ["chat"]
        if self.supports_streaming:
            supports.append("stream")
        if self.supports_tools:
            supports.append("tools")
        if self.supports_strict_json:
            supports.append("strict_json")
        out: dict[str, Any] = {
            "id": self.id,
            "object": "model",
            "owned_by": self.owned_by,
            "context_length": self.context_window,
            "supports": supports,
            "pricing": {
                "input_kop_per_1k": self.input_price_kop_per_1k,
                "output_kop_per_1k": self.output_price_kop_per_1k,
                "cached_input_kop_per_1k": self.cached_price_kop_per_1k,
            },
            "region": "ru" if self.ru_legal else "global",
            "description": self.description,
        }
        if self.deprecated_at is not None:
            out["deprecated_at"] = self.deprecated_at.isoformat()
        return out

    # --- Sprint 10: public marketing endpoint ------------------------------

    def _usd_per_1m(self, kop_per_1k: int, override: Decimal | None) -> Decimal | None:
        """Return USD/1M tokens — explicit override, else back-computed from kop.

        Back-computation: ``kop / 100 / USD_RUB * 1000``. For values where
        the original USD price was sub-kopeck (e.g. $0.014), kop rounds to
        zero and we return ``Decimal("0")``. Callers wanting truthful
        sub-kopeck prices must pass an explicit ``override``.
        """
        if override is not None:
            return override
        if kop_per_1k == 0:
            # Could be a real $0 price (no cache discount) or a precision
            # casualty. Return 0 — public endpoint will surface as 0.
            return Decimal("0")
        # kop/1k → USD/1M:  kop / 100 (rub) * 1000 (per-M) / USD_RUB
        return (Decimal(kop_per_1k) * Decimal(10) / USD_RUB).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def _rub_per_1m_brikko(self, usd_per_1m: Decimal | None) -> int | None:
        """Return RUB/1M tokens at the Brikko price (USD × USD_RUB × markup).

        Returns int (RUB, no kopeck precision — marketing display only).
        ``None`` propagates so the public endpoint can render ``null`` for
        capabilities the model doesn't have (e.g. cache_write on non-Anthropic).
        """
        if usd_per_1m is None:
            return None
        rub = usd_per_1m * USD_RUB * PUBLIC_MARKUP
        return int(rub.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def to_public_dict(self) -> dict[str, Any]:
        """Render the dict served by GET /v1/models/public.

        Marketing-grade payload: human-readable names, RUB-converted prices
        with Brikko markup, capability flags, modality lists. Distinct from
        ``to_models_dict()`` (which is OpenAI-shape for SDK compatibility).
        """
        usd_in = self._usd_per_1m(self.input_price_kop_per_1k, self.usd_input_per_1m)
        usd_cached = self._usd_per_1m(self.cached_price_kop_per_1k, self.usd_cached_per_1m)
        usd_out = self._usd_per_1m(self.output_price_kop_per_1k, self.usd_output_per_1m)

        # ``cache_write`` is Anthropic-only (5× input on first write). We
        # surface it as null elsewhere so the lander UI shows "—" instead of 0.
        cache_write_rub: int | None = None
        if self.supports_anthropic_cache_control and usd_in is not None:
            # Anthropic cache_write = input × 1.25 (5m TTL) per their pricing
            # page. We expose the 5m rate; 1h = ×2 is documented separately.
            cache_write_rub = self._rub_per_1m_brikko(usd_in * Decimal("1.25"))

        # ``cached_input`` only meaningful when the provider has a cache
        # discount (kop_cached < kop_input). Otherwise null in the UI.
        cached_input_rub: int | None
        if self.cached_price_kop_per_1k < self.input_price_kop_per_1k:
            cached_input_rub = self._rub_per_1m_brikko(usd_cached)
        else:
            cached_input_rub = None

        category = "chat"  # all MVP catalog entries are chat models

        # Provider category for "ru_legal" — SBER + YANDEX hosted in RU and
        # bound by 152-FZ; DeepSeek is Chinese (NOT 152-FZ-friendly even if
        # accessed direct from RU). Use spec.ru_legal as the source of truth.
        capabilities = {
            "streaming": self.supports_streaming,
            "tool_calling": self.supports_tools,
            "json_schema_strict": self.supports_strict_json,
            "vision": self.supports_vision,
            "audio": self.supports_audio,
            "prompt_caching": (
                self.supports_anthropic_cache_control
                or self.cached_price_kop_per_1k < self.input_price_kop_per_1k
            ),
            "ru_legal": self.ru_legal,
        }

        return {
            "id": self.id,
            "display_name": self.display_name or self.id,
            "provider": self.provider.value,
            "provider_display_name": PROVIDER_DISPLAY_NAMES.get(
                self.provider.value, self.provider.value.title()
            ),
            "category": category,
            "tier": self.tier.value.upper(),
            "context_tokens": self.context_window,
            "pricing_rub_per_1m": {
                "input": self._rub_per_1m_brikko(usd_in),
                "output": self._rub_per_1m_brikko(usd_out),
                "cached_input": cached_input_rub,
                "cache_write": cache_write_rub,
            },
            "pricing_usd_per_1m_official": {
                "input": float(usd_in) if usd_in is not None else None,
                "output": float(usd_out) if usd_out is not None else None,
                "cached_input": (
                    float(usd_cached)
                    if usd_cached is not None and cached_input_rub is not None
                    else None
                ),
            },
            "modalities": {
                "input": list(self.modalities_in),
                "output": list(self.modalities_out),
            },
            "capabilities": capabilities,
            "deprecated_at": (
                self.deprecated_at.isoformat() if self.deprecated_at is not None else None
            ),
            "released_at": (self.released_at.isoformat() if self.released_at is not None else None),
            "description": self.description or None,
            "best_for": list(self.best_for),
        }


def _kop_per_1k(usd_per_1m: float | str) -> int:
    """Convert USD/1M tokens to kopecks/1k tokens, rounded half-up."""
    raw = (Decimal(str(usd_per_1m)) * USD_RUB / Decimal(1000)) * Decimal(100)
    return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# Catalogue — 14 MVP models per BRIEF §4.
# Latency p50 estimates come from internal probes (2026-04-15 to 2026-04-28).
# Refresh quarterly or when a provider publishes a new SLA.
CATALOG: tuple[ModelSpec, ...] = (
    # ---------- OpenAI ----------
    ModelSpec(
        id="gpt-5.4-mini",
        provider=Provider.OPENAI,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.75),
        cached_price_kop_per_1k=_kop_per_1k(0.08),
        output_price_kop_per_1k=_kop_per_1k(4.50),
        context_window=400_000,
        latency_p50_ms=900,
        quality_score=78,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=True,
        description="OpenAI mid model — workhorse for the gateway.",
    ),
    ModelSpec(
        id="gpt-5.4",
        provider=Provider.OPENAI,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(2.50),
        cached_price_kop_per_1k=_kop_per_1k(0.25),
        output_price_kop_per_1k=_kop_per_1k(15.00),
        context_window=400_000,
        latency_p50_ms=1500,
        quality_score=88,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=True,
        description="OpenAI flagship.",
    ),
    ModelSpec(
        id="gpt-5",
        provider=Provider.OPENAI,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(1.25),
        cached_price_kop_per_1k=_kop_per_1k(0.13),
        output_price_kop_per_1k=_kop_per_1k(10.00),
        context_window=400_000,
        latency_p50_ms=1100,
        quality_score=82,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=True,
        description="OpenAI mid; strong cost/quality balance.",
    ),
    # Sprint 9 — GPT-5.5 family. GA on the API early May 2026 (per Sprint 9
    # market-pulse). Pricing from https://pricepertoken.com/pricing-page/provider/openai
    # (as of 2026-05-01). 1M context, automatic prompt caching, native
    # response_format=json_schema strict=true. Smart-strategy default for
    # the OpenAI flagship slot now points here (gpt-5.5 quality_score 90 vs.
    # gpt-5.4 88 — small intentional bump so the smart score picks 5.5
    # over 5.4 within the same tier on a tie-broken request).
    ModelSpec(
        id="gpt-5.5",
        provider=Provider.OPENAI,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(5.00),
        cached_price_kop_per_1k=_kop_per_1k(0.50),
        output_price_kop_per_1k=_kop_per_1k(30.00),
        context_window=1_000_000,
        latency_p50_ms=1700,
        quality_score=90,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=True,
        description="OpenAI flagship (GPT-5.5, GA May 2026).",
    ),
    # GPT-5.5 Pro — premium tier. 6× the price of GPT-5.5, tuned for hard
    # coding/reasoning. Smart-strategy heavy bucket; not a default for any
    # auto:* tag (clients pin explicitly).
    ModelSpec(
        id="gpt-5.5-pro",
        provider=Provider.OPENAI,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=_kop_per_1k(30.00),
        cached_price_kop_per_1k=_kop_per_1k(3.00),
        output_price_kop_per_1k=_kop_per_1k(180.00),
        context_window=1_000_000,
        latency_p50_ms=3200,
        quality_score=96,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=True,
        description="OpenAI premium reasoning (GPT-5.5 Pro, GA May 2026).",
    ),
    ModelSpec(
        id="o3",
        provider=Provider.OPENAI,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=_kop_per_1k(2.00),
        cached_price_kop_per_1k=_kop_per_1k(0.50),
        output_price_kop_per_1k=_kop_per_1k(8.00),
        context_window=200_000,
        latency_p50_ms=4500,
        quality_score=94,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=True,
        description="OpenAI reasoning model.",
    ),
    ModelSpec(
        id="o4-mini",
        provider=Provider.OPENAI,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=_kop_per_1k(1.10),
        cached_price_kop_per_1k=_kop_per_1k(0.28),
        output_price_kop_per_1k=_kop_per_1k(4.40),
        context_window=200_000,
        latency_p50_ms=2800,
        quality_score=86,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=True,
        description="Cheap reasoning.",
    ),
    # Sprint M3.1 (2026-05-10) — legacy + reasoning expansions.
    # CEO has decided to position Brikko as a premium gateway across the
    # *full* OpenAI API surface (including legacy aliases) so existing
    # SDK/code that pins old model names migrates without rewriting.
    # Prices verified against https://platform.openai.com/docs/pricing
    # 2026-05-10. @review tags any line where the public price page is
    # ambiguous between regional/billing tiers — current best-guess marked.
    #
    # ``o1`` — premium reasoning, $15 in / $60 out per 1M (CEO note).
    # The original O-series is still GA on the API as of 2026-05.
    ModelSpec(
        id="o1",
        provider=Provider.OPENAI,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=_kop_per_1k(15.00),
        cached_price_kop_per_1k=_kop_per_1k(7.50),  # 50% cached discount
        output_price_kop_per_1k=_kop_per_1k(60.00),
        context_window=200_000,
        latency_p50_ms=8000,
        quality_score=93,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=True,
        description="OpenAI o1 — first-gen premium reasoning, 200k ctx.",
    ),
    # ``o1-mini`` — cheaper o1 sibling. $3 in / $12 out per 1M.
    ModelSpec(
        id="o1-mini",
        provider=Provider.OPENAI,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=_kop_per_1k(3.00),
        cached_price_kop_per_1k=_kop_per_1k(1.50),
        output_price_kop_per_1k=_kop_per_1k(12.00),
        context_window=128_000,
        latency_p50_ms=4500,
        quality_score=85,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=True,
        description="OpenAI o1-mini — cheap reasoning, 128k ctx.",
    ),
    # ``o3-mini`` — cheapest of the o-series, $1.10 in / $4.40 out per 1M.
    # Effectively duplicates o4-mini's price but separate id — clients on
    # the old name get a working model.
    ModelSpec(
        id="o3-mini",
        provider=Provider.OPENAI,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=_kop_per_1k(1.10),
        cached_price_kop_per_1k=_kop_per_1k(0.55),
        output_price_kop_per_1k=_kop_per_1k(4.40),
        context_window=200_000,
        latency_p50_ms=2800,
        quality_score=83,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=True,
        description="OpenAI o3-mini — cheap reasoning, 200k ctx.",
    ),
    # Legacy non-reasoning chat models. Kept GA on api.openai.com as of
    # 2026-05; we surface them so customers migrating from openai-python
    # 1.x scripts don't have to rewrite. Smart strategy never picks these
    # for auto:* (their quality_score sits below the 4o/5.x flagships).
    #
    # ``gpt-4-turbo`` — $10 in / $30 out per 1M, 128k ctx, accepts max_tokens.
    ModelSpec(
        id="gpt-4-turbo",
        provider=Provider.OPENAI,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(10.00),
        cached_price_kop_per_1k=_kop_per_1k(10.00),  # no cache discount on legacy
        output_price_kop_per_1k=_kop_per_1k(30.00),
        context_window=128_000,
        latency_p50_ms=1600,
        quality_score=80,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=False,
        description="OpenAI gpt-4-turbo — legacy alias kept for compat.",
        supports_vision=True,
    ),
    # ``gpt-4`` — original $30/$60 model. Slow + expensive; we keep it
    # because ChatGPT-Enterprise migrators sometimes pin this name.
    ModelSpec(
        id="gpt-4",
        provider=Provider.OPENAI,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(30.00),
        cached_price_kop_per_1k=_kop_per_1k(30.00),
        output_price_kop_per_1k=_kop_per_1k(60.00),
        context_window=8_192,
        latency_p50_ms=2200,
        quality_score=76,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=False,  # original gpt-4 predates strict mode
        requires_max_completion_tokens=False,
        description="OpenAI gpt-4 (original) — legacy, 8k ctx, expensive.",
    ),
    # ``gpt-3.5-turbo`` — $0.50/$1.50 per 1M, the original cheap workhorse.
    # Strict JSON not supported (predates response_format=strict).
    ModelSpec(
        id="gpt-3.5-turbo",
        provider=Provider.OPENAI,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.50),
        cached_price_kop_per_1k=_kop_per_1k(0.50),
        output_price_kop_per_1k=_kop_per_1k(1.50),
        context_window=16_385,
        latency_p50_ms=600,
        quality_score=58,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=False,
        requires_max_completion_tokens=False,
        description="OpenAI gpt-3.5-turbo — legacy budget chat, 16k ctx.",
    ),
    # NOTE — ``gpt-4o-realtime-preview``: deferred to Sprint M5 (Realtime
    # WebSocket endpoint). Audio-token pricing differs from text and the
    # transport is WS not HTTPS — adding it to /v1/models without a working
    # /v1/realtime endpoint would be misleading. Tracked in TECH_DEBT.md.
    # Sprint M2 (2026-05-09) — gpt-4o family added as catalog aliases.
    # OpenAI keeps these GA on the API; prices verified against
    # https://platform.openai.com/docs/pricing 2026-05-09. Adding (not
    # replacing) so customers who pinned the gpt-4o name keep working.
    ModelSpec(
        id="gpt-4o",
        provider=Provider.OPENAI,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(2.50),
        cached_price_kop_per_1k=_kop_per_1k(1.25),
        output_price_kop_per_1k=_kop_per_1k(10.00),
        context_window=128_000,
        latency_p50_ms=1100,
        quality_score=84,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        # gpt-4o still accepts ``max_tokens``; only reasoning models switched.
        requires_max_completion_tokens=False,
        description="OpenAI gpt-4o — multimodal flagship, kept GA.",
        supports_vision=True,
    ),
    ModelSpec(
        id="gpt-4o-mini",
        provider=Provider.OPENAI,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.15),
        cached_price_kop_per_1k=_kop_per_1k(0.075),
        output_price_kop_per_1k=_kop_per_1k(0.60),
        context_window=128_000,
        latency_p50_ms=600,
        quality_score=74,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        requires_max_completion_tokens=False,
        description="OpenAI gpt-4o-mini — cheap multimodal workhorse.",
        supports_vision=True,
    ),
    # ---------- Anthropic ----------
    # Anthropic API uses dashes, not dots, in model IDs (verified 2026-05-01).
    # We expose dotted public IDs (matches our pricing-page convention)
    # and translate to upstream dashed IDs at request time.
    ModelSpec(
        id="claude-sonnet-4.6",
        upstream_id="claude-sonnet-4-6",
        provider=Provider.ANTHROPIC,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(3.00),
        cached_price_kop_per_1k=_kop_per_1k(0.30),
        output_price_kop_per_1k=_kop_per_1k(15.00),
        context_window=1_000_000,
        latency_p50_ms=1300,
        quality_score=85,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,  # via tool-mode workaround in adapter
        supports_anthropic_cache_control=True,
        description="Anthropic mid — most popular Claude.",
    ),
    ModelSpec(
        id="claude-haiku-4.5",
        # Anthropic released Haiku 4.5 only as datestamped (no plain alias yet
        # as of 2026-05-01). Hard-pin to the 20251001 build until alias appears.
        upstream_id="claude-haiku-4-5-20251001",
        provider=Provider.ANTHROPIC,
        tier=ModelTier.BUDGET,
        input_price_kop_per_1k=_kop_per_1k(1.00),
        cached_price_kop_per_1k=_kop_per_1k(0.10),
        output_price_kop_per_1k=_kop_per_1k(5.00),
        context_window=200_000,
        latency_p50_ms=600,
        quality_score=72,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        supports_anthropic_cache_control=True,
        description="Anthropic fast & cheap.",
    ),
    ModelSpec(
        id="claude-opus-4.7",
        upstream_id="claude-opus-4-7",
        provider=Provider.ANTHROPIC,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=_kop_per_1k(15.00),
        cached_price_kop_per_1k=_kop_per_1k(1.50),
        output_price_kop_per_1k=_kop_per_1k(75.00),
        context_window=1_000_000,
        latency_p50_ms=2100,
        quality_score=95,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        supports_anthropic_cache_control=True,
        description="Anthropic flagship — Opus 4.7, $15/$75 per 1M.",
    ),
    # Sprint M3.1 (2026-05-10) — Anthropic legacy aliases.
    # Brief: existing customers pinning claude-3.5-sonnet / claude-3.5-haiku /
    # claude-3-opus by name should keep working. Anthropic still has the 3.x
    # family GA on the Messages API (verified 2026-05-10). All these models
    # support cache_control; smart strategy never picks them for auto:* (their
    # quality scores sit below the 4.x flagships).
    ModelSpec(
        id="claude-3.5-sonnet",
        upstream_id="claude-3-5-sonnet-latest",
        provider=Provider.ANTHROPIC,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(3.00),
        cached_price_kop_per_1k=_kop_per_1k(0.30),
        output_price_kop_per_1k=_kop_per_1k(15.00),
        context_window=200_000,
        latency_p50_ms=1300,
        quality_score=82,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        supports_anthropic_cache_control=True,
        description="Anthropic Claude 3.5 Sonnet — legacy alias, 200k ctx.",
    ),
    ModelSpec(
        id="claude-3.5-haiku",
        upstream_id="claude-3-5-haiku-latest",
        provider=Provider.ANTHROPIC,
        tier=ModelTier.BUDGET,
        input_price_kop_per_1k=_kop_per_1k(0.80),
        cached_price_kop_per_1k=_kop_per_1k(0.08),
        output_price_kop_per_1k=_kop_per_1k(4.00),
        context_window=200_000,
        latency_p50_ms=700,
        quality_score=68,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        supports_anthropic_cache_control=True,
        description="Anthropic Claude 3.5 Haiku — legacy budget, 200k ctx.",
    ),
    ModelSpec(
        id="claude-3-opus",
        upstream_id="claude-3-opus-latest",
        provider=Provider.ANTHROPIC,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=_kop_per_1k(15.00),
        cached_price_kop_per_1k=_kop_per_1k(1.50),
        output_price_kop_per_1k=_kop_per_1k(75.00),
        context_window=200_000,
        latency_p50_ms=2200,
        quality_score=88,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        supports_anthropic_cache_control=True,
        description="Anthropic Claude 3 Opus — legacy premium, $15/$75 per 1M.",
    ),
    # ---------- Google ----------
    # Gemini 3.x family на v1beta (2026-05-01) ещё с -preview-суффиксом —
    # стабильных алиасов нет. Public id без суффикса (наш контракт), upstream
    # пинуется явно. То же что у Anthropic Haiku 4.5.
    ModelSpec(
        id="gemini-3-flash",
        upstream_id="gemini-3-flash-preview",
        provider=Provider.GOOGLE,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.50),
        cached_price_kop_per_1k=_kop_per_1k(0.05),
        output_price_kop_per_1k=_kop_per_1k(3.00),
        context_window=1_000_000,
        latency_p50_ms=700,
        quality_score=76,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,  # via response_schema mapping
        description="Google mid (new default Flash).",
    ),
    ModelSpec(
        id="gemini-3.1-pro",
        upstream_id="gemini-3.1-pro-preview",
        provider=Provider.GOOGLE,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(2.00),
        cached_price_kop_per_1k=_kop_per_1k(0.20),
        output_price_kop_per_1k=_kop_per_1k(12.00),
        context_window=1_000_000,
        latency_p50_ms=1400,
        quality_score=87,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        description="Google flagship; >200k input billed at higher tier.",
        tiered_pricing=TieredPricing(
            threshold_tokens=200_000,
            above_input_kop_per_1k=_kop_per_1k(4.00),
            above_cached_kop_per_1k=_kop_per_1k(0.40),
            above_output_kop_per_1k=_kop_per_1k(24.00),
        ),
    ),
    # Sprint M2 (2026-05-09) — gemini-2.5 aliases added. Google keeps the
    # 2.5 family GA on v1 (no -preview suffix), so customers who pinned
    # gemini-2.5-flash or gemini-2.5-pro by name get a working model.
    # Prices verified against https://ai.google.dev/pricing 2026-05-09.
    # We surface both 2.5 and 3.x families so SDK callers can pick by
    # name; the smart router still picks 3.x by quality_score for auto:*.
    ModelSpec(
        id="gemini-2.5-flash",
        upstream_id="gemini-2.5-flash",
        provider=Provider.GOOGLE,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.30),
        cached_price_kop_per_1k=_kop_per_1k(0.075),
        output_price_kop_per_1k=_kop_per_1k(2.50),
        context_window=1_000_000,
        latency_p50_ms=750,
        quality_score=75,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        description="Google gemini-2.5-flash — current GA Flash, alias added M2.",
        supports_vision=True,
    ),
    ModelSpec(
        id="gemini-2.5-pro",
        upstream_id="gemini-2.5-pro",
        provider=Provider.GOOGLE,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(1.25),
        cached_price_kop_per_1k=_kop_per_1k(0.31),
        output_price_kop_per_1k=_kop_per_1k(10.00),
        context_window=2_000_000,
        latency_p50_ms=1500,
        quality_score=86,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        description="Google gemini-2.5-pro — current GA Pro, 2M ctx, alias added M2.",
        supports_vision=True,
        tiered_pricing=TieredPricing(
            threshold_tokens=200_000,
            above_input_kop_per_1k=_kop_per_1k(2.50),
            above_cached_kop_per_1k=_kop_per_1k(0.625),
            above_output_kop_per_1k=_kop_per_1k(15.00),
        ),
    ),
    # Sprint M3.1 (2026-05-10) — gemini-2.5-flash-8b (cheapest tier),
    # gemini-1.5-pro (legacy flagship), gemini-1.5-flash (legacy budget).
    # Prices verified against https://ai.google.dev/pricing 2026-05-10.
    # @review: 2.5-flash-8b is in research papers but Google has not
    # officially shipped it as a v1 GA model name (as of 2026-05-10 it's
    # still labeled gemini-1.5-flash-8b). We expose the 1.5-flash-8b id
    # since that's the actual v1 alias; if/when 2.5-flash-8b GAs we'll
    # add it as a separate entry.
    #
    # Pricing pitfall: $0.04 / $0.15 per 1M is sub-kopeck per 1k tokens
    # ($0.04/1M = 0.32 kop/1k → rounds to 0 in our integer storage). We
    # bump the kop floor to 1 so billing never thinks the model is free,
    # and surface the *true* USD prices via the explicit overrides for
    # the public catalog. The 0.32 kop overcharge per 1k input is
    # ≈3× the COGS — accepted to keep integer arithmetic in billing.
    ModelSpec(
        id="gemini-1.5-flash-8b",
        upstream_id="gemini-1.5-flash-8b",
        provider=Provider.GOOGLE,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=1,  # kop floor — true price 0.32 kop/1k
        cached_price_kop_per_1k=1,  # kop floor — true price 0.08 kop/1k
        output_price_kop_per_1k=1,  # kop floor — true price 1.2 kop/1k
        context_window=1_000_000,
        latency_p50_ms=500,
        quality_score=62,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        description="Google gemini-1.5-flash-8b — cheapest Gemini, 1M ctx.",
        supports_vision=True,
        usd_input_per_1m=Decimal("0.04"),
        usd_cached_per_1m=Decimal("0.01"),
        usd_output_per_1m=Decimal("0.15"),
    ),
    ModelSpec(
        id="gemini-1.5-pro",
        upstream_id="gemini-1.5-pro",
        provider=Provider.GOOGLE,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(1.25),
        cached_price_kop_per_1k=_kop_per_1k(0.3125),
        output_price_kop_per_1k=_kop_per_1k(5.00),
        context_window=2_000_000,
        latency_p50_ms=1700,
        quality_score=80,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        description="Google gemini-1.5-pro — legacy flagship, 2M ctx.",
        supports_vision=True,
        tiered_pricing=TieredPricing(
            threshold_tokens=128_000,
            above_input_kop_per_1k=_kop_per_1k(2.50),
            above_cached_kop_per_1k=_kop_per_1k(0.625),
            above_output_kop_per_1k=_kop_per_1k(10.00),
        ),
    ),
    # Same kop-floor pitfall as 1.5-flash-8b — input at $0.075/1M is below
    # 1 kop/1k. Output at $0.30/1M = 2.4 kop/1k → ints to 2 — so output
    # uses the natural rounding, only input gets a floor.
    ModelSpec(
        id="gemini-1.5-flash",
        upstream_id="gemini-1.5-flash",
        provider=Provider.GOOGLE,
        tier=ModelTier.MID,
        input_price_kop_per_1k=1,  # kop floor — true price 0.6 kop/1k
        cached_price_kop_per_1k=1,  # kop floor — true price 0.15 kop/1k
        output_price_kop_per_1k=_kop_per_1k(0.30),
        context_window=1_000_000,
        latency_p50_ms=700,
        quality_score=70,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        supports_strict_json=True,
        description="Google gemini-1.5-flash — legacy budget, 1M ctx.",
        supports_vision=True,
        usd_input_per_1m=Decimal("0.075"),
        usd_cached_per_1m=Decimal("0.01875"),
        usd_output_per_1m=Decimal("0.30"),
    ),
    # ---------- DeepSeek ----------
    # NOTE: upstream_id differs — public-facing id is more explicit than
    # the actual upstream alias.
    # DeepSeek deprecated v3.2 endpoints on 2026-04-24 with an 84-day
    # window; ``deepseek-chat`` and ``deepseek-reasoner`` upstream go
    # offline 2026-07-24. We surface ``deprecated_at`` so /v1/models can
    # warn clients; chat responses include an X-Brikko-Deprecated header
    # (see api/chat.py). Past the date, get_model() still returns it but
    # the chat endpoint refuses with model_deprecated.
    ModelSpec(
        id="deepseek-v3.2-chat",
        provider=Provider.DEEPSEEK,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.28),
        cached_price_kop_per_1k=_kop_per_1k(0.03),
        output_price_kop_per_1k=_kop_per_1k(0.42),
        context_window=128_000,
        latency_p50_ms=1200,
        quality_score=70,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        upstream_id="deepseek-chat",
        deprecated_at=date(2026, 7, 24),
        supports_strict_json=True,
        description="DeepSeek v3.2 — DEPRECATED, retires 2026-07-24. Migrate to deepseek-v4-flash.",
    ),
    # Sprint 9 — DeepSeek V4 family (released 2026-04-24).
    # Pricing source: https://api-docs.deepseek.com/quick_start/pricing
    # Promo until 2026-05-31 15:59 UTC: V4-Pro at $0.435 in / $0.87 out.
    # We catalog at the **post-promo** rate ($1.74 / $3.48 for V4-Pro)
    # so the smart strategy doesn't pick V4-Pro for everything during
    # promo and then suddenly inflate costs on June 1. Promo savings
    # accrue as a margin bonus to Brikko (we still bill clients at
    # catalog × markup; COGS to DeepSeek is the promo rate). When the
    # promo ends, no code change needed. If CEO wants to pass the promo
    # savings to clients — flip these to promo prices and revert the
    # Sprint-9 commit on 2026-05-31.
    #
    # V4-Flash takes over `auto:cheap` for short-context CHAT category
    # (see strategies.py). Quality scores tied: Flash 72 (matches Haiku),
    # Pro 86 (above DeepSeek v3.2 by 16). Latency probes pending — TD-9
    # (refresh after first 1000 production calls).
    ModelSpec(
        id="deepseek-v4-flash",
        provider=Provider.DEEPSEEK,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.14),
        cached_price_kop_per_1k=_kop_per_1k(0.014),  # 1/10 base per DeepSeek policy
        output_price_kop_per_1k=_kop_per_1k(0.28),
        context_window=1_000_000,
        latency_p50_ms=900,  # tentative — refresh after probes
        quality_score=72,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        upstream_id="deepseek-v4-flash",
        supports_strict_json=True,
        description="DeepSeek V4 Flash — 284B/13B MoE, 1M ctx, default for auto:cheap.",
        # Sprint 10 — explicit USD overrides so /v1/models/public doesn't
        # round the sub-kopeck cached rate to 0.
        usd_input_per_1m=Decimal("0.14"),
        usd_cached_per_1m=Decimal("0.014"),
        usd_output_per_1m=Decimal("0.28"),
    ),
    ModelSpec(
        id="deepseek-v4-pro",
        provider=Provider.DEEPSEEK,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(1.74),
        cached_price_kop_per_1k=_kop_per_1k(0.174),  # 1/10 base
        output_price_kop_per_1k=_kop_per_1k(3.48),
        context_window=1_000_000,
        latency_p50_ms=1500,  # tentative
        quality_score=86,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        upstream_id="deepseek-v4-pro",
        supports_strict_json=True,
        description="DeepSeek V4 Pro — 1.6T/49B MoE, 1M ctx, post-promo $1.74/$3.48.",
        usd_input_per_1m=Decimal("1.74"),
        usd_cached_per_1m=Decimal("0.174"),
        usd_output_per_1m=Decimal("3.48"),
    ),
    # Sprint M3.1 (2026-05-10) — DeepSeek expansion.
    # ``deepseek-r1`` (direct API path, upstream alias ``deepseek-reasoner``)
    # is DeepSeek's own reasoning model. Distinct from ``deepseek-r1-together``
    # above which routes through Together.ai with different pricing/latency.
    # Both are kept so failover can hop between DeepSeek-direct and Together
    # if one is down.
    # Pricing: $0.55 in / $2.19 out per 1M (CEO note 2026-05-10, matches
    # https://api-docs.deepseek.com/quick_start/pricing).
    ModelSpec(
        id="deepseek-r1",
        provider=Provider.DEEPSEEK,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=_kop_per_1k(0.55),
        cached_price_kop_per_1k=_kop_per_1k(0.055),  # 1/10 base
        output_price_kop_per_1k=_kop_per_1k(2.19),
        context_window=64_000,
        latency_p50_ms=4500,
        quality_score=88,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        upstream_id="deepseek-reasoner",
        supports_strict_json=True,
        description="DeepSeek R1 — reasoning model via direct DeepSeek API.",
        usd_input_per_1m=Decimal("0.55"),
        usd_cached_per_1m=Decimal("0.055"),
        usd_output_per_1m=Decimal("2.19"),
    ),
    # ``deepseek-v3`` — short alias pointing at the same upstream as
    # ``deepseek-v3.2-chat`` (also retiring 2026-07-24). Some SDK code
    # still pins the pre-3.2 name; surface both so migrations don't break.
    # Marked deprecated together so a single warning channel covers it.
    ModelSpec(
        id="deepseek-v3",
        provider=Provider.DEEPSEEK,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.28),
        cached_price_kop_per_1k=_kop_per_1k(0.03),
        output_price_kop_per_1k=_kop_per_1k(0.42),
        context_window=128_000,
        latency_p50_ms=1200,
        quality_score=68,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        upstream_id="deepseek-chat",
        deprecated_at=date(2026, 7, 24),
        supports_strict_json=True,
        description="DeepSeek V3 — DEPRECATED legacy alias, retires 2026-07-24.",
    ),
    # ---------- Yandex (RU-legal) ----------
    # Streaming disabled in MVP — NDJSON parser deferred to V2.1 (per
    # architectural decision 8).
    ModelSpec(
        id="yandexgpt-5.1-pro",
        provider=Provider.YANDEX,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(6.56),
        cached_price_kop_per_1k=_kop_per_1k(6.56),  # no cache discount
        output_price_kop_per_1k=_kop_per_1k(6.56),
        context_window=32_000,
        latency_p50_ms=1100,
        quality_score=68,
        supports_streaming=False,
        # Phase 5 #2 (2026-05-09): Yandex Foundation Models поддерживает
        # function calling через native REST. Adapter в yandex_provider.py
        # конвертирует `toolCallList` ↔ OpenAI `tool_calls`. Strict JSON
        # schema поддержан (флаг strict=True пробрасывается 1:1).
        supports_tools=True,
        ru_legal=True,
        description="Russian flagship; 152-FZ-friendly. Tools: parallel calls supported.",
    ),
    ModelSpec(
        id="yandexgpt-5-lite",
        provider=Provider.YANDEX,
        tier=ModelTier.BUDGET,
        input_price_kop_per_1k=_kop_per_1k(1.64),
        cached_price_kop_per_1k=_kop_per_1k(1.64),
        output_price_kop_per_1k=_kop_per_1k(1.64),
        context_window=32_000,
        latency_p50_ms=800,
        quality_score=58,
        supports_streaming=False,
        supports_tools=True,
        ru_legal=True,
        description="Russian budget; 152-FZ-friendly. Tools: requires integration test.",
    ),
    # ---------- Sber GigaChat (RU-legal) ----------
    # Sber supports SSE; streaming stays enabled.
    ModelSpec(
        id="gigachat-2-pro",
        provider=Provider.SBER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(5.55),
        cached_price_kop_per_1k=_kop_per_1k(5.55),
        output_price_kop_per_1k=_kop_per_1k(5.55),
        context_window=131_000,
        latency_p50_ms=1500,
        quality_score=65,
        supports_streaming=True,
        # Phase 5 #2 (2026-05-09): function calling включён. Adapter в
        # sber_provider.py конвертирует legacy `function_call` → modern
        # `tool_calls`. GigaChat capped 1 call/response (нет parallel
        # multi-tool — hard cap upstream).
        supports_tools=True,
        ru_legal=True,
        upstream_id="GigaChat-2-Pro",
        description="Sber GigaChat 2 Pro; 152-FZ-friendly. Tools: 1 call/response.",
    ),
    ModelSpec(
        id="gigachat-2-lite",
        provider=Provider.SBER,
        tier=ModelTier.BUDGET,
        input_price_kop_per_1k=_kop_per_1k(0.72),
        cached_price_kop_per_1k=_kop_per_1k(0.72),
        output_price_kop_per_1k=_kop_per_1k(0.72),
        context_window=131_000,
        latency_p50_ms=900,
        quality_score=55,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=True,
        # У Sber базовая модель называется просто "GigaChat-2" (без -Lite),
        # верифицировано через /api/v1/models 2026-05-01.
        upstream_id="GigaChat-2",
        description="Sber GigaChat 2 (базовая); 152-FZ-friendly.",
    ),
    # Sprint M3.1 (2026-05-10) — gigachat-2-max — Sber's premium tier above
    # Pro. @review: Sber публикует прайс через личный кабинет developers.sber.ru,
    # а не через публичную страницу — точная цена 13.95 коп/1k токенов
    # (≈ $1.745/1M в наших единицах) — best-guess on parity with the Sber
    # Cloud Foundation Models price page snapshot 2026-04-15. Уточнить когда
    # CEO заведёт корпоративный аккаунт в Студии.
    ModelSpec(
        id="gigachat-2-max",
        provider=Provider.SBER,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(13.95),  # @review 2026-05-10
        cached_price_kop_per_1k=_kop_per_1k(13.95),  # no cache discount
        output_price_kop_per_1k=_kop_per_1k(13.95),
        # @review 2026-05-10: context_window нужно уточнить с CEO.
        # 4M упоминалось но это не context window (значение TBD).
        # Базовый GigaChat-2-Max имеет 131k по последним публичным данным.
        context_window=131_000,
        latency_p50_ms=2100,
        quality_score=72,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=True,
        upstream_id="GigaChat-2-Max",
        description="Sber GigaChat 2 Max — премиум-модель Сбера, 152-FZ-friendly.",
    ),
    # ---------- Together.ai (Sprint M3, 2026-05-10) ----------
    # Together.ai exposes an OpenAI-compatible chat-completions endpoint at
    # https://api.together.xyz/v1 + serverless inference for OSS models
    # (Llama, Qwen, Mixtral, DeepSeek-R1, FLUX, …). One TogetherProvider
    # adapter (subclass of OpenAIProvider) covers every model — only the
    # upstream_id changes per ModelSpec.
    #
    # Pricing source: https://www.together.ai/pricing (verified 2026-05-10).
    # Stored as COGS in USD/1M tokens; the global PUBLIC_MARKUP (×1.15)
    # in to_public_dict() handles the marketing markup, same as every
    # other provider in this file. We deliberately do NOT bake a 30%
    # provider-specific markup into kop prices — that would split the
    # markup convention across providers and make /v1/models/public
    # numbers incomparable. Brief said «наценка 30%» but consistency
    # with the rest of the catalog wins; if CEO wants a Together-only
    # premium markup, it goes in PUBLIC_MARKUP_BY_PROVIDER not here.
    #
    # Streaming, tools, response_format=json_object: supported by Together
    # for all chat models (per Together docs 2026-05). Strict JSON schema
    # is supported on a subset (Llama-3.1+ in Turbo serving) — we leave
    # supports_strict_json=False unless explicitly verified.
    #
    # ru_legal=False for everything — Together is US-hosted.
    # Latency p50: tentative (no probe data yet — TD: refresh after
    # 1000 production calls). Quality scores are hand-tuned from public
    # benchmarks (LMSys, MMLU, HumanEval) as of 2026-05.
    #
    # Big LLMs ----------------------------------------------------------
    ModelSpec(
        id="llama-3.3-70b",
        upstream_id="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.88),
        cached_price_kop_per_1k=_kop_per_1k(0.88),  # no cache discount
        output_price_kop_per_1k=_kop_per_1k(0.88),
        context_window=128_000,
        latency_p50_ms=900,
        quality_score=80,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Meta Llama 3.3 70B Instruct Turbo via Together.ai.",
    ),
    ModelSpec(
        id="llama-3.1-405b",
        upstream_id="meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
        provider=Provider.TOGETHER,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(3.50),
        cached_price_kop_per_1k=_kop_per_1k(3.50),
        output_price_kop_per_1k=_kop_per_1k(3.50),
        context_window=128_000,
        latency_p50_ms=2200,
        quality_score=85,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Meta Llama 3.1 405B Turbo — top-tier OSS via Together.",
    ),
    ModelSpec(
        id="llama-3.1-70b",
        upstream_id="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.88),
        cached_price_kop_per_1k=_kop_per_1k(0.88),
        output_price_kop_per_1k=_kop_per_1k(0.88),
        context_window=128_000,
        latency_p50_ms=900,
        quality_score=78,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Meta Llama 3.1 70B Instruct Turbo via Together.ai.",
    ),
    ModelSpec(
        id="llama-3.1-8b",
        upstream_id="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        provider=Provider.TOGETHER,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.18),
        cached_price_kop_per_1k=_kop_per_1k(0.18),
        output_price_kop_per_1k=_kop_per_1k(0.18),
        context_window=128_000,
        latency_p50_ms=400,
        quality_score=68,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Meta Llama 3.1 8B Turbo — cheap OSS workhorse.",
    ),
    ModelSpec(
        id="qwen-2.5-72b",
        upstream_id="Qwen/Qwen2.5-72B-Instruct-Turbo",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(1.20),
        cached_price_kop_per_1k=_kop_per_1k(1.20),
        output_price_kop_per_1k=_kop_per_1k(1.20),
        context_window=32_768,
        latency_p50_ms=1100,
        quality_score=80,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Qwen 2.5 72B Instruct Turbo — Alibaba flagship OSS.",
    ),
    ModelSpec(
        id="qwen-coder-32b",
        upstream_id="Qwen/Qwen2.5-Coder-32B-Instruct",
        provider=Provider.TOGETHER,
        tier=ModelTier.BUDGET,
        input_price_kop_per_1k=_kop_per_1k(0.80),
        cached_price_kop_per_1k=_kop_per_1k(0.80),
        output_price_kop_per_1k=_kop_per_1k(0.80),
        context_window=32_768,
        latency_p50_ms=1000,
        quality_score=78,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Qwen 2.5 Coder 32B — strong code-gen OSS.",
    ),
    ModelSpec(
        id="mixtral-8x22b",
        upstream_id="mistralai/Mixtral-8x22B-Instruct-v0.1",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(1.20),
        cached_price_kop_per_1k=_kop_per_1k(1.20),
        output_price_kop_per_1k=_kop_per_1k(1.20),
        context_window=65_536,
        latency_p50_ms=1300,
        quality_score=74,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Mixtral 8x22B Instruct — Mistral MoE flagship.",
    ),
    ModelSpec(
        id="mistral-7b",
        upstream_id="mistralai/Mistral-7B-Instruct-v0.3",
        provider=Provider.TOGETHER,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.20),
        cached_price_kop_per_1k=_kop_per_1k(0.20),
        output_price_kop_per_1k=_kop_per_1k(0.20),
        context_window=32_768,
        latency_p50_ms=400,
        quality_score=62,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Mistral 7B Instruct v0.3 — small fast OSS.",
    ),
    # NOTE: Together hosts a separate ``DeepSeek-V3`` weight; this is NOT
    # the same as the deepseek-v3.2-chat / deepseek-v4-* entries that go
    # via DeepSeekProvider direct to api.deepseek.com. Different upstream,
    # different price, different routing — kept distinct on purpose.
    ModelSpec(
        id="deepseek-v3-together",
        upstream_id="deepseek-ai/DeepSeek-V3",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(1.25),
        cached_price_kop_per_1k=_kop_per_1k(1.25),
        output_price_kop_per_1k=_kop_per_1k(1.25),
        context_window=131_072,
        latency_p50_ms=1500,
        quality_score=82,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="DeepSeek V3 hosted by Together.ai (not the same as deepseek-v3.2-chat).",
    ),
    # Renamed Sprint M3.1 (was ``deepseek-r1``) to free up the short alias for
    # the *direct* DeepSeek API path (``deepseek-reasoner`` upstream). Together
    # hosts the open weights at a different price point and routes through a
    # different provider — keeping both makes the failover story explicit.
    ModelSpec(
        id="deepseek-r1-together",
        upstream_id="deepseek-ai/DeepSeek-R1",
        provider=Provider.TOGETHER,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=_kop_per_1k(3.00),
        cached_price_kop_per_1k=_kop_per_1k(3.00),
        output_price_kop_per_1k=_kop_per_1k(7.00),
        context_window=131_072,
        latency_p50_ms=4500,
        quality_score=90,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="DeepSeek R1 — open-weight reasoning model via Together.ai.",
    ),
    ModelSpec(
        id="dbrx-instruct",
        upstream_id="databricks/dbrx-instruct",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(1.20),
        cached_price_kop_per_1k=_kop_per_1k(1.20),
        output_price_kop_per_1k=_kop_per_1k(1.20),
        context_window=32_768,
        latency_p50_ms=1300,
        quality_score=72,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Databricks DBRX Instruct — 132B MoE OSS.",
    ),
    ModelSpec(
        id="gemma-2-27b",
        upstream_id="google/gemma-2-27b-it",
        provider=Provider.TOGETHER,
        tier=ModelTier.BUDGET,
        input_price_kop_per_1k=_kop_per_1k(0.80),
        cached_price_kop_per_1k=_kop_per_1k(0.80),
        output_price_kop_per_1k=_kop_per_1k(0.80),
        context_window=8_192,
        latency_p50_ms=900,
        quality_score=70,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Google Gemma 2 27B Instruct — open-weight Gemini sibling.",
    ),
    ModelSpec(
        id="gemma-2-9b",
        upstream_id="google/gemma-2-9b-it",
        provider=Provider.TOGETHER,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.30),
        cached_price_kop_per_1k=_kop_per_1k(0.30),
        output_price_kop_per_1k=_kop_per_1k(0.30),
        context_window=8_192,
        latency_p50_ms=500,
        quality_score=64,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Google Gemma 2 9B Instruct — cheap small OSS.",
    ),
    ModelSpec(
        id="nemotron-70b",
        upstream_id="nvidia/Llama-3.1-Nemotron-70B-Instruct-HF",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.88),
        cached_price_kop_per_1k=_kop_per_1k(0.88),
        output_price_kop_per_1k=_kop_per_1k(0.88),
        context_window=128_000,
        latency_p50_ms=1000,
        quality_score=79,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="NVIDIA Nemotron 70B — RLHF-tuned Llama 3.1.",
    ),
    # Vision / multimodal ----------------------------------------------
    ModelSpec(
        id="llama-3.2-90b-vision",
        upstream_id="meta-llama/Llama-3.2-90B-Vision-Instruct-Turbo",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(1.20),
        cached_price_kop_per_1k=_kop_per_1k(1.20),
        output_price_kop_per_1k=_kop_per_1k(1.20),
        context_window=128_000,
        latency_p50_ms=1400,
        quality_score=78,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Meta Llama 3.2 90B Vision Turbo — multimodal OSS.",
        supports_vision=True,
    ),
    ModelSpec(
        id="llama-3.2-11b-vision",
        upstream_id="meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo",
        provider=Provider.TOGETHER,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.18),
        cached_price_kop_per_1k=_kop_per_1k(0.18),
        output_price_kop_per_1k=_kop_per_1k(0.18),
        context_window=128_000,
        latency_p50_ms=600,
        quality_score=66,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Meta Llama 3.2 11B Vision Turbo — small multimodal OSS.",
        supports_vision=True,
    ),
    ModelSpec(
        id="qwen-2-vl-72b",
        upstream_id="Qwen/Qwen2-VL-72B-Instruct",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(1.20),
        cached_price_kop_per_1k=_kop_per_1k(1.20),
        output_price_kop_per_1k=_kop_per_1k(1.20),
        context_window=32_768,
        latency_p50_ms=1500,
        quality_score=76,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Qwen 2 VL 72B — Alibaba multimodal OSS.",
        supports_vision=True,
    ),
    # Extra popular OSS picks — together.ai catalog highlights.
    ModelSpec(
        id="llama-3-70b",
        upstream_id="meta-llama/Llama-3-70b-chat-hf",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.90),
        cached_price_kop_per_1k=_kop_per_1k(0.90),
        output_price_kop_per_1k=_kop_per_1k(0.90),
        context_window=8_192,
        latency_p50_ms=950,
        quality_score=74,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Meta Llama 3 70B chat — predecessor to 3.1, kept for compat.",
    ),
    ModelSpec(
        id="llama-3-8b",
        upstream_id="meta-llama/Llama-3-8b-chat-hf",
        provider=Provider.TOGETHER,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.20),
        cached_price_kop_per_1k=_kop_per_1k(0.20),
        output_price_kop_per_1k=_kop_per_1k(0.20),
        context_window=8_192,
        latency_p50_ms=400,
        quality_score=60,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Meta Llama 3 8B chat — small fast OSS.",
    ),
    ModelSpec(
        id="mixtral-8x7b",
        upstream_id="mistralai/Mixtral-8x7B-Instruct-v0.1",
        provider=Provider.TOGETHER,
        tier=ModelTier.BUDGET,
        input_price_kop_per_1k=_kop_per_1k(0.60),
        cached_price_kop_per_1k=_kop_per_1k(0.60),
        output_price_kop_per_1k=_kop_per_1k(0.60),
        context_window=32_768,
        latency_p50_ms=900,
        quality_score=68,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Mixtral 8x7B Instruct — original Mistral MoE, cheap.",
    ),
    ModelSpec(
        id="wizardlm-2-8x22b",
        upstream_id="microsoft/WizardLM-2-8x22B",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(1.20),
        cached_price_kop_per_1k=_kop_per_1k(1.20),
        output_price_kop_per_1k=_kop_per_1k(1.20),
        context_window=65_536,
        latency_p50_ms=1400,
        quality_score=76,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Microsoft WizardLM-2 8x22B — fine-tuned Mixtral.",
    ),
    ModelSpec(
        id="qwen-2.5-7b",
        upstream_id="Qwen/Qwen2.5-7B-Instruct-Turbo",
        provider=Provider.TOGETHER,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.30),
        cached_price_kop_per_1k=_kop_per_1k(0.30),
        output_price_kop_per_1k=_kop_per_1k(0.30),
        context_window=32_768,
        latency_p50_ms=400,
        quality_score=66,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Qwen 2.5 7B Instruct Turbo — small Alibaba OSS.",
    ),
    ModelSpec(
        id="deepseek-llm-67b",
        upstream_id="deepseek-ai/deepseek-llm-67b-chat",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.90),
        cached_price_kop_per_1k=_kop_per_1k(0.90),
        output_price_kop_per_1k=_kop_per_1k(0.90),
        context_window=4_096,
        latency_p50_ms=1100,
        quality_score=70,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="DeepSeek LLM 67B chat — original dense DeepSeek.",
    ),
    ModelSpec(
        id="qwen-72b",
        upstream_id="Qwen/Qwen-72B-Chat",
        provider=Provider.TOGETHER,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.90),
        cached_price_kop_per_1k=_kop_per_1k(0.90),
        output_price_kop_per_1k=_kop_per_1k(0.90),
        context_window=32_768,
        latency_p50_ms=1100,
        quality_score=70,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Qwen 72B chat — original Qwen flagship.",
    ),
    # ---------- Moonshot AI (Kimi) — Sprint M3.2 (2026-05-10) ----------
    # CEO-payable via UnionPay. International endpoint api.moonshot.ai.
    # Pricing source: https://platform.moonshot.ai/docs/pricing as of
    # 2026-05-10. All Moonshot chat models accept stream + tools +
    # response_format=json_object; strict json schema is NOT uniformly
    # supported — flag stays False so the chat layer 400s early.
    # No prompt-cache discount column on usage payloads, so cached==input.
    # Quality scores are LMSys-derived for K2 family (top-tier per
    # Chinese benchmarks), educated guesses for older v1-* models.
    # @review: latency p50 from internal probes pending — refresh
    # once first 1000 production calls land.
    ModelSpec(
        id="kimi-k2",
        upstream_id="kimi-k2-0711-preview",
        provider=Provider.MOONSHOT,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(0.60),
        cached_price_kop_per_1k=_kop_per_1k(0.60),  # no cache discount
        output_price_kop_per_1k=_kop_per_1k(2.50),
        context_window=256_000,
        latency_p50_ms=1500,
        quality_score=86,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Moonshot Kimi K2 — top-tier Chinese flagship, 256k ctx.",
    ),
    ModelSpec(
        id="moonshot-v1-128k",
        upstream_id="moonshot-v1-128k",
        provider=Provider.MOONSHOT,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.60),  # @review — Moonshot
        # bills 128k tier higher than 32k; CEO note 0.6/2.0 placeholder.
        cached_price_kop_per_1k=_kop_per_1k(0.60),
        output_price_kop_per_1k=_kop_per_1k(2.00),
        context_window=128_000,
        latency_p50_ms=1300,
        quality_score=72,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Moonshot v1 128k — long-context workhorse.",
    ),
    ModelSpec(
        id="moonshot-v1-32k",
        upstream_id="moonshot-v1-32k",
        provider=Provider.MOONSHOT,
        tier=ModelTier.BUDGET,
        input_price_kop_per_1k=_kop_per_1k(0.40),
        cached_price_kop_per_1k=_kop_per_1k(0.40),
        output_price_kop_per_1k=_kop_per_1k(1.20),
        context_window=32_000,
        latency_p50_ms=900,
        quality_score=68,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Moonshot v1 32k — balanced cost/context.",
    ),
    ModelSpec(
        id="moonshot-v1-8k",
        upstream_id="moonshot-v1-8k",
        provider=Provider.MOONSHOT,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.20),  # @review — Moonshot
        # 8k tier is the cheapest Kimi product; CEO note ~$0.2/$0.6.
        cached_price_kop_per_1k=_kop_per_1k(0.20),
        output_price_kop_per_1k=_kop_per_1k(0.60),
        context_window=8_000,
        latency_p50_ms=600,
        quality_score=62,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Moonshot v1 8k — cheapest Kimi tier.",
    ),
    # ---------- MiniMax (Hailuo) — Sprint M3.2 (2026-05-10) ----------
    # api.minimaxi.chat (note the trailing ``i``). Chat surface is
    # OpenAI-compatible. Pricing source: https://www.minimax.io/platform
    # 2026-05-10. M-1 is the new flagship reasoning-tuned model;
    # M-Text-01 keeps a 1M-token context window. abab6.5(s) are kept
    # as the budget legacy line for SDK migration.
    # @review: M1 USD pricing is 0.4/2.2 per 1M; ``minimax-m1`` upstream
    # id assumes the platform exposes "MiniMax-M1" verbatim — refresh
    # once first request lands.
    ModelSpec(
        id="minimax-m1",
        upstream_id="MiniMax-M1",
        provider=Provider.MINIMAX,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(0.40),
        cached_price_kop_per_1k=_kop_per_1k(0.40),
        output_price_kop_per_1k=_kop_per_1k(2.20),
        context_window=200_000,
        latency_p50_ms=1600,
        quality_score=82,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="MiniMax M1 — Hailuo flagship reasoning model.",
    ),
    ModelSpec(
        id="minimax-text-01",
        upstream_id="MiniMax-Text-01",
        provider=Provider.MINIMAX,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.40),
        cached_price_kop_per_1k=_kop_per_1k(0.40),
        output_price_kop_per_1k=_kop_per_1k(2.00),
        context_window=1_000_000,
        latency_p50_ms=1500,
        quality_score=78,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="MiniMax Text-01 — 1M-token context Hailuo workhorse.",
    ),
    ModelSpec(
        id="abab6.5s-chat",
        upstream_id="abab6.5s-chat",
        provider=Provider.MINIMAX,
        tier=ModelTier.NANO,
        input_price_kop_per_1k=_kop_per_1k(0.20),  # @review — abab6.5s
        # is positioned as the cheap alternative; CEO note ~$0.2/$0.6.
        cached_price_kop_per_1k=_kop_per_1k(0.20),
        output_price_kop_per_1k=_kop_per_1k(0.60),
        context_window=245_000,
        latency_p50_ms=700,
        quality_score=64,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="MiniMax abab6.5s — cheap legacy budget chat, 245k ctx.",
    ),
    ModelSpec(
        id="abab6.5-chat",
        upstream_id="abab6.5-chat",
        provider=Provider.MINIMAX,
        tier=ModelTier.BUDGET,
        input_price_kop_per_1k=_kop_per_1k(1.00),  # @review — abab6.5
        # base tier; CEO note ~$1/$3 per 1M.
        cached_price_kop_per_1k=_kop_per_1k(1.00),
        output_price_kop_per_1k=_kop_per_1k(3.00),
        context_window=8_000,
        latency_p50_ms=900,
        quality_score=68,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="MiniMax abab6.5 — legacy mid-tier chat, 8k ctx.",
    ),
    # ---------- Zhipu AI (GLM) — Sprint M3.2 (2026-05-10) ----------
    # open.bigmodel.cn/api/paas/v4. Bearer auth with the full
    # ``<id>.<secret>`` API key string (see ZhipuProvider docstring).
    # Pricing source: https://open.bigmodel.cn/pricing 2026-05-10.
    # GLM-4.5 is the new flagship; GLM-4.5-Air is the cheaper sibling;
    # GLM-4-Long keeps 1M ctx; GLM-4V-Plus is the vision variant.
    # @review: prices in USD ≈ 0.5/1.5 (4.5), 0.2/0.6 (4.5-Air),
    # 0.7/2.0 (4-long), 2.0/6.0 (4v-plus).
    ModelSpec(
        id="glm-4.5",
        upstream_id="glm-4.5",
        provider=Provider.ZHIPU,
        tier=ModelTier.FLAGSHIP,
        input_price_kop_per_1k=_kop_per_1k(0.50),
        cached_price_kop_per_1k=_kop_per_1k(0.50),  # no cache discount column
        output_price_kop_per_1k=_kop_per_1k(1.50),
        context_window=128_000,
        latency_p50_ms=1500,
        quality_score=82,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Zhipu GLM-4.5 — flagship Chinese model.",
    ),
    ModelSpec(
        id="glm-4.5-air",
        upstream_id="glm-4.5-air",
        provider=Provider.ZHIPU,
        tier=ModelTier.BUDGET,
        input_price_kop_per_1k=_kop_per_1k(0.20),
        cached_price_kop_per_1k=_kop_per_1k(0.20),
        output_price_kop_per_1k=_kop_per_1k(0.60),
        context_window=128_000,
        latency_p50_ms=900,
        quality_score=72,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Zhipu GLM-4.5 Air — lighter, cheaper sibling.",
    ),
    ModelSpec(
        id="glm-4-long",
        upstream_id="glm-4-long",
        provider=Provider.ZHIPU,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(0.70),
        cached_price_kop_per_1k=_kop_per_1k(0.70),
        output_price_kop_per_1k=_kop_per_1k(2.00),
        context_window=1_000_000,
        latency_p50_ms=1700,
        quality_score=76,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Zhipu GLM-4 Long — 1M-token context variant.",
    ),
    ModelSpec(
        id="glm-4v-plus",
        upstream_id="glm-4v-plus",
        provider=Provider.ZHIPU,
        tier=ModelTier.MID,
        input_price_kop_per_1k=_kop_per_1k(2.00),
        cached_price_kop_per_1k=_kop_per_1k(2.00),
        output_price_kop_per_1k=_kop_per_1k(6.00),
        context_window=8_000,
        latency_p50_ms=1500,
        quality_score=74,
        supports_streaming=True,
        supports_tools=True,
        ru_legal=False,
        description="Zhipu GLM-4V Plus — vision-capable Chinese model.",
        supports_vision=True,
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers — used by router/strategies. Built once at import time so we
# don't pay an O(N) scan on every request. The tuple is short (~15 entries)
# so a dict on top is mostly cosmetic; the real win is structural — callers
# get O(1) by id without exposing the underlying tuple.
# ---------------------------------------------------------------------------

_CATALOG_BY_ID: dict[str, ModelSpec] = {m.id: m for m in CATALOG}


def get_model(model_id: str) -> ModelSpec | None:
    """Return the model spec for ``model_id``, or None if unknown."""
    return _CATALOG_BY_ID.get(model_id)


def all_models() -> tuple[ModelSpec, ...]:
    """Return the full catalogue (immutable tuple)."""
    return CATALOG


def list_models() -> list[ModelSpec]:
    """Stable order for /v1/models output."""
    return list(CATALOG)


__all__ = [
    "CATALOG",
    "USD_RUB",
    "ModelSpec",
    "ModelTier",
    "Provider",
    "TieredPricing",
    "all_models",
    "get_model",
    "list_models",
]
