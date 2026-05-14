"""Chinese frontier providers catalog regression tests.

Sprint M3.2 (2026-05-10) — Moonshot (Kimi) / MiniMax (Hailuo) / Zhipu (GLM).
All three are OpenAI-compatible, all three CEO-payable via UnionPay
(Прио / ВТБ Драйв) without a foreign card. We add 12 models across the
three providers (4+4+4); these tests guard against:

* model id collisions (short aliases like ``kimi-k2``, ``glm-4.5`` must
  not collide with existing OpenAI/Anthropic/Together entries),
* missing prices (every entry must have non-zero kop prices),
* wrong provider enum value,
* missing or wrong settings env-aliases,
* missing display-name entries (the public catalog UI relies on them).
"""

from __future__ import annotations

from voltari_gateway.router.catalog import (
    CATALOG,
    PROVIDER_DISPLAY_NAMES,
    Provider,
    get_model,
)

EXPECTED_MOONSHOT_IDS: set[str] = {
    "kimi-k2",
    "moonshot-v1-128k",
    "moonshot-v1-32k",
    "moonshot-v1-8k",
}

EXPECTED_MINIMAX_IDS: set[str] = {
    "minimax-m1",
    "minimax-text-01",
    "abab6.5s-chat",
    "abab6.5-chat",
}

EXPECTED_ZHIPU_IDS: set[str] = {
    "glm-4.5",
    "glm-4.5-air",
    "glm-4-long",
    "glm-4v-plus",
}

EXPECTED_TOTAL_CHINESE = (
    len(EXPECTED_MOONSHOT_IDS) + len(EXPECTED_MINIMAX_IDS) + len(EXPECTED_ZHIPU_IDS)
)


# ---------------------------------------------------------------------------
# Provider enum + display names
# ---------------------------------------------------------------------------


def test_provider_enum_has_chinese_values() -> None:
    assert Provider.MOONSHOT.value == "moonshot"
    assert Provider.MINIMAX.value == "minimax"
    assert Provider.ZHIPU.value == "zhipu"


def test_provider_display_names_present() -> None:
    """Public catalog UI relies on PROVIDER_DISPLAY_NAMES — missing
    entry would render the raw enum value to clients."""
    assert PROVIDER_DISPLAY_NAMES.get("moonshot") == "Moonshot (Kimi)"
    assert PROVIDER_DISPLAY_NAMES.get("minimax") == "MiniMax (Hailuo)"
    assert PROVIDER_DISPLAY_NAMES.get("zhipu") == "Zhipu (GLM)"


# ---------------------------------------------------------------------------
# Catalog membership + counts
# ---------------------------------------------------------------------------


def test_moonshot_models_present() -> None:
    ids = {m.id for m in CATALOG if m.provider is Provider.MOONSHOT}
    missing = EXPECTED_MOONSHOT_IDS - ids
    assert not missing, f"missing Moonshot models: {missing}"
    assert len(ids) >= len(EXPECTED_MOONSHOT_IDS)


def test_minimax_models_present() -> None:
    ids = {m.id for m in CATALOG if m.provider is Provider.MINIMAX}
    missing = EXPECTED_MINIMAX_IDS - ids
    assert not missing, f"missing MiniMax models: {missing}"
    assert len(ids) >= len(EXPECTED_MINIMAX_IDS)


def test_zhipu_models_present() -> None:
    ids = {m.id for m in CATALOG if m.provider is Provider.ZHIPU}
    missing = EXPECTED_ZHIPU_IDS - ids
    assert not missing, f"missing Zhipu models: {missing}"
    assert len(ids) >= len(EXPECTED_ZHIPU_IDS)


def test_total_chinese_models_meets_brief_target() -> None:
    """Brief target: at minimum 12 Chinese frontier chat models."""
    chinese_providers = {Provider.MOONSHOT, Provider.MINIMAX, Provider.ZHIPU}
    total = sum(1 for m in CATALOG if m.provider in chinese_providers)
    assert total >= 12, f"Chinese frontier catalog has only {total} entries; brief asks for 12+."
    assert total >= EXPECTED_TOTAL_CHINESE


# ---------------------------------------------------------------------------
# Pricing sanity
# ---------------------------------------------------------------------------


def test_chinese_models_have_pricing() -> None:
    """No free models — every entry must have non-zero in/out price."""
    chinese_providers = {Provider.MOONSHOT, Provider.MINIMAX, Provider.ZHIPU}
    for spec in CATALOG:
        if spec.provider not in chinese_providers:
            continue
        assert spec.input_price_kop_per_1k > 0, f"{spec.id}: zero input price"
        assert spec.output_price_kop_per_1k > 0, f"{spec.id}: zero output price"
        assert spec.cached_price_kop_per_1k >= 0, f"{spec.id}: negative cached price"
        # None of these three providers expose a cache discount column —
        # cached should equal input. Defensive: catches a future
        # copy-paste from a DeepSeek/Anthropic spec.
        assert spec.cached_price_kop_per_1k == spec.input_price_kop_per_1k, (
            f"{spec.id}: Chinese providers have no cache discount; "
            f"expected cached==input but got {spec.cached_price_kop_per_1k} "
            f"vs {spec.input_price_kop_per_1k}"
        )


def test_chinese_models_are_not_ru_legal() -> None:
    """All three providers are CN-hosted — no model can claim 152-FZ legality."""
    chinese_providers = {Provider.MOONSHOT, Provider.MINIMAX, Provider.ZHIPU}
    for spec in CATALOG:
        if spec.provider in chinese_providers:
            assert spec.ru_legal is False, (
                f"{spec.id}: ru_legal=True but {spec.provider.value} is CN-hosted"
            )


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def test_zhipu_vision_model_has_supports_vision() -> None:
    """GLM-4V Plus is the only catalog vision entry from these three."""
    spec = get_model("glm-4v-plus")
    assert spec is not None
    assert spec.supports_vision is True


def test_chinese_models_no_strict_json_yet() -> None:
    """Strict JSON schema isn't uniformly supported by these three —
    catalog flag must stay False until verified per-model. Otherwise
    api/chat.py would happily forward strict requests upstream and we'd
    eat 4xx-s on the round-trip."""
    chinese_providers = {Provider.MOONSHOT, Provider.MINIMAX, Provider.ZHIPU}
    for spec in CATALOG:
        if spec.provider in chinese_providers:
            assert spec.supports_strict_json is False, (
                f"{spec.id}: supports_strict_json=True is unverified for "
                f"{spec.provider.value} — leave False until tested."
            )


# ---------------------------------------------------------------------------
# Id-collision and upstream-id sanity
# ---------------------------------------------------------------------------


def test_no_chinese_id_collides_with_other_provider() -> None:
    """Short aliases (kimi-k2, glm-4.5, …) must be unique catalog-wide."""
    seen: dict[str, str] = {}
    for spec in CATALOG:
        prev = seen.get(spec.id)
        assert prev is None, (
            f"id collision: {spec.id} appears under provider {prev} and {spec.provider.value}"
        )
        seen[spec.id] = spec.provider.value


def test_kimi_k2_alias_maps_to_dated_upstream() -> None:
    """Public ``kimi-k2`` must resolve to the dated upstream snapshot —
    Moonshot ships K2 only as ``kimi-k2-0711-preview`` as of 2026-05.
    A plain ``kimi-k2`` upstream id would 404."""
    spec = get_model("kimi-k2")
    assert spec is not None
    assert spec.upstream_id == "kimi-k2-0711-preview"


def test_minimax_aliases_use_capital_upstream_ids() -> None:
    """MiniMax upstream model ids are capitalised (``MiniMax-M1``,
    ``MiniMax-Text-01``). Public ids are lowercase aliases."""
    m1 = get_model("minimax-m1")
    txt = get_model("minimax-text-01")
    assert m1 is not None and m1.upstream_id == "MiniMax-M1"
    assert txt is not None and txt.upstream_id == "MiniMax-Text-01"


# ---------------------------------------------------------------------------
# Settings env-aliases
# ---------------------------------------------------------------------------


def test_chinese_settings_aliases_present() -> None:
    """Settings exposes MOONSHOT_/MINIMAX_/ZHIPU_ env aliases."""
    from voltari_gateway.config import Settings

    fields = Settings.model_fields
    for prefix in ("moonshot", "minimax", "zhipu"):
        assert f"{prefix}_api_key" in fields, f"missing {prefix}_api_key"
        assert f"{prefix}_base_url" in fields, f"missing {prefix}_base_url"
        assert f"{prefix}_timeout_seconds" in fields, f"missing {prefix}_timeout_seconds"

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.moonshot_api_key.get_secret_value() == ""
    assert s.moonshot_base_url == "https://api.moonshot.ai/v1"
    assert s.moonshot_timeout_seconds == 60.0
    assert s.minimax_api_key.get_secret_value() == ""
    assert s.minimax_base_url == "https://api.minimaxi.chat/v1"
    assert s.minimax_timeout_seconds == 60.0
    assert s.zhipu_api_key.get_secret_value() == ""
    assert s.zhipu_base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert s.zhipu_timeout_seconds == 60.0
