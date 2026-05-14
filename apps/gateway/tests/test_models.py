"""GET /v1/models tests."""

from __future__ import annotations

import pytest
import pytest_asyncio

from voltari_gateway.router.catalog import CATALOG, Provider, list_models


@pytest_asyncio.fixture
async def all_providers_registered(app):
    """Register a stub for every Provider enum value so the /v1/models filter
    surfaces the full catalog. Default conftest registers OpenAI only — the
    rest of the test suite uses that to assert auto-routing picks an OpenAI
    fallback when other upstreams aren't configured. Tests in this file
    care about catalog membership, so they need the broader setup.
    """
    stub = app.state.openai_provider
    registry = app.state.provider_registry
    for prov in Provider:
        registry.register(prov, stub)
    return registry


def test_catalog_has_at_least_twelve_models():
    # MVP target: minimum 12 models. We have OpenAI(5) + Anthropic(3) + Google(2)
    # + DeepSeek(1) + Yandex(1) = 12.
    assert len(CATALOG) >= 12


def test_each_model_has_pricing_and_supports():
    for spec in list_models():
        assert spec.input_price_kop_per_1k > 0
        assert spec.output_price_kop_per_1k > 0
        # to_models_dict() always emits "chat" first.
        assert "chat" in spec.to_models_dict()["supports"]


@pytest.mark.asyncio
async def test_list_models_returns_full_catalog(all_providers_registered, client, api_key_fixture):
    r = await client.get("/v1/models", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    body = r.json()
    ids = {m["id"] for m in body["data"]}
    expected = {
        "gpt-5.4-mini",
        "gpt-5.4",
        "gpt-5",
        "o3",
        "o4-mini",
        "claude-sonnet-4.6",
        "claude-haiku-4.5",
        "claude-opus-4.7",
        "gemini-3-flash",
        "gemini-3.1-pro",
        "deepseek-v3.2-chat",
        "yandexgpt-5.1-pro",
    }
    assert expected.issubset(ids)


@pytest.mark.asyncio
async def test_get_specific_model_metadata(client, api_key_fixture):
    r = await client.get("/v1/models/gpt-5.4-mini", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "gpt-5.4-mini"
    assert body["object"] == "model"
    assert body["owned_by"] == "openai"
    assert body["pricing"]["input_kop_per_1k"] == 6  # 0.75 USD/M * 8 = 6 kop/1k
    assert body["pricing"]["output_kop_per_1k"] == 36  # 4.50 USD/M * 8 = 36 kop/1k


@pytest.mark.asyncio
async def test_get_unknown_model_returns_404_envelope(client, api_key_fixture):
    r = await client.get("/v1/models/no-such-model", headers=api_key_fixture.auth_header)
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "model_not_found"
    assert body["error"]["param"] == "model"


# ---------------------------------------------------------------------------
# Sprint 9 — DeepSeek V4 catalog regression. The smart router's auto:cheap
# path must pick deepseek-v4-flash for short-context CHAT (it's now the
# cheapest non-RU model in the catalog). v3.2 stays catalogued (clients
# pinning deepseek-v3.2-chat still work until 2026-07-24) but carries a
# ``deprecated_at`` field so SDK callers / dashboards can warn.
# ---------------------------------------------------------------------------


def test_deepseek_v4_models_present_in_catalog():
    ids = {m.id for m in CATALOG}
    assert "deepseek-v4-flash" in ids
    assert "deepseek-v4-pro" in ids


def test_deepseek_v3_2_marked_deprecated_2026_07_24():
    from datetime import date as _date

    from voltari_gateway.router.catalog import get_model

    spec = get_model("deepseek-v3.2-chat")
    assert spec is not None
    assert spec.deprecated_at == _date(2026, 7, 24)
    # Surfaced in /v1/models output.
    assert spec.to_models_dict()["deprecated_at"] == "2026-07-24"


def test_deepseek_v4_flash_is_cheapest_chat_model_for_short_context():
    """auto:cheap on a short CHAT request resolves to a NANO-tier OSS model.

    Until Sprint M3 the test asserted ``deepseek-v4-flash`` exactly, but
    after adding Together OSS models (Sprint M3) the kop/1k rounding tips
    the weighted-price tie in favour of llama-3.1-8b and similar (both
    round to 1 kop input + 1 kop output ≈ 1.0 weighted, vs v4-flash's
    1 kop input + 2 kop output ≈ 1.3 weighted). All of them are cheap
    enough that auto:cheap is happy with any.

    What we still want to guard against:
    1. The cheapest must be NANO-tier (premium/flagship leaking in = bug).
    2. ``deepseek-v4-flash`` must remain in the top 3 — it's still our
       cheapest direct (non-Together) DeepSeek path.
    """
    from voltari_gateway.router.catalog import ModelTier
    from voltari_gateway.router.strategies import TaskCategory, select_cheap

    ranked = select_cheap(TaskCategory.CHAT, ctx_size=1_000)
    assert ranked[0].tier == ModelTier.NANO
    top3_ids = {m.id for m in ranked[:5]}
    assert "deepseek-v4-flash" in top3_ids, (
        f"deepseek-v4-flash dropped out of the top 5 cheapest: top 5 = {[m.id for m in ranked[:5]]}"
    )


def test_deepseek_v4_promo_pricing_not_applied_in_catalog():
    """Catalog prices reflect the **post-promo** rate (CFO decision).

    Promo (until 2026-05-31) is $0.435 in / $0.87 out for V4-Pro. We keep
    the catalog at $1.74 / $3.48 so smart strategy doesn't over-pick the
    Pro tier during promo and then suddenly inflate costs on June 1.
    """
    from voltari_gateway.router.catalog import _kop_per_1k, get_model

    pro = get_model("deepseek-v4-pro")
    assert pro is not None
    assert pro.input_price_kop_per_1k == _kop_per_1k(1.74)
    assert pro.output_price_kop_per_1k == _kop_per_1k(3.48)


# ---------------------------------------------------------------------------
# Sprint 9 — GPT-5.5 family (GA May 2026). Catalogued alongside 5.4 family
# (5.4 stays — clients on existing pins keep working). 5.5-pro joins the
# PREMIUM tier; smart strategy excludes PREMIUM for CHAT/CODE so a client
# wanting 5.5-pro must pin it explicitly.
# ---------------------------------------------------------------------------


def test_gpt_5_5_family_present_in_catalog():
    ids = {m.id for m in CATALOG}
    assert "gpt-5.5" in ids
    assert "gpt-5.5-pro" in ids


def test_gpt_5_5_supports_strict_json_and_1m_context():
    from voltari_gateway.router.catalog import get_model

    spec = get_model("gpt-5.5")
    assert spec is not None
    assert spec.supports_strict_json is True
    assert spec.context_window == 1_000_000


def test_gpt_5_5_pro_is_premium_tier_and_not_default_for_auto_smart_chat():
    """gpt-5.5-pro is in PREMIUM tier — auto:smart on CHAT category must
    not surface it (smart strategy excludes PREMIUM for CHAT/CODE).

    A client who needs gpt-5.5-pro must pin it explicitly. This test pins
    a specific behavioural guarantee: PREMIUM tier models stay opt-in.
    """
    from voltari_gateway.router.catalog import ModelTier, get_model
    from voltari_gateway.router.strategies import TaskCategory, select_smart

    pro = get_model("gpt-5.5-pro")
    assert pro is not None
    assert pro.tier is ModelTier.PREMIUM

    ranked_ids = {m.id for m in select_smart(TaskCategory.CHAT, ctx_size=1_000)}
    assert "gpt-5.5-pro" not in ranked_ids


# ---------------------------------------------------------------------------
# Catalog invariants for 2026-05-01 upstream_id fixes (commits 0b2da28,
# 32e6d3b). These guard against a future "let's drop the upstream_id
# overrides, they're confusing" refactor that would re-break us.
# ---------------------------------------------------------------------------


def test_all_anthropic_models_translate_dotted_id_to_dashed_upstream():
    """Anthropic Messages API rejects dots — every Claude entry MUST set
    a non-default ``upstream_id`` and the upstream id MUST be dot-free."""
    anthropic_models = [m for m in CATALOG if m.provider is Provider.ANTHROPIC]
    assert len(anthropic_models) >= 3, "expected at least 3 Claude entries"
    for spec in anthropic_models:
        assert spec.upstream_id != spec.id, (
            f"{spec.id}: missing upstream_id override (Anthropic API needs dashes)"
        )
        assert "." not in spec.upstream_id, (
            f"{spec.id}: upstream_id {spec.upstream_id!r} still contains a dot"
        )


def test_all_gemini_3x_models_pinned_to_preview_suffix():
    """Gemini 3.x has no stable v1beta alias — every Gemini-3.x entry MUST
    point at the ``-preview`` upstream id until Google GAs them.

    Updated 2026-05-09 (Sprint M2): scoped to the 3.x family because the
    catalog now also carries gemini-2.5 aliases which ARE GA on v1 and
    deliberately don't carry a -preview suffix.
    """
    google_3x = [
        m for m in CATALOG if m.provider is Provider.GOOGLE and m.id.startswith("gemini-3")
    ]
    assert len(google_3x) >= 2, "expected at least 2 Gemini-3.x entries"
    for spec in google_3x:
        assert spec.upstream_id.endswith("-preview"), (
            f"{spec.id}: upstream_id {spec.upstream_id!r} not pinned to -preview"
        )
        assert spec.upstream_id != spec.id


def test_gpt_5_5_quality_above_5_4_so_tie_break_favours_5_5():
    """5.5 should be at least as good as 5.4 on the smart score so a
    same-tier tie-break picks 5.5 over 5.4 (the whole point of GA-ing
    5.5 is the upgrade path)."""
    from voltari_gateway.router.catalog import get_model

    five_four = get_model("gpt-5.4")
    five_five = get_model("gpt-5.5")
    assert five_four is not None and five_five is not None
    assert five_five.quality_score >= five_four.quality_score


# ---------------------------------------------------------------------------
# Sprint M3.1 (2026-05-10) — provider-filter on /v1/models. CEO ships without
# a Together API key (no foreign card) so Together-hosted models must hide
# from the catalog automatically; when the key arrives, all 24+ Together
# entries reappear without a code change.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v1_models_hides_unregistered_provider(
    all_providers_registered, app, client, api_key_fixture
):
    """Pre-condition: every provider registered. Unregister Together →
    /v1/models stops listing Together entries; OpenAI entries stay."""
    app.state.provider_registry.unregister(Provider.TOGETHER)
    r = await client.get("/v1/models", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    # Together-only ids hidden.
    assert "llama-3.3-70b" not in ids
    assert "qwen-2.5-72b" not in ids
    assert "deepseek-v3-together" not in ids
    # OpenAI-side ids still present.
    assert "gpt-5.4-mini" in ids
    # Anthropic still present (we only unregistered Together).
    assert "claude-sonnet-4.6" in ids


@pytest.mark.asyncio
async def test_v1_models_public_hides_unregistered_provider(all_providers_registered, app, client):
    app.state.provider_registry.unregister(Provider.TOGETHER)
    r = await client.get("/v1/models/public")
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    assert "llama-3.3-70b" not in ids
    assert "gpt-5.4-mini" in ids


@pytest.mark.asyncio
async def test_v1_models_single_lookup_404_when_provider_unregistered(
    all_providers_registered, app, client, api_key_fixture
):
    """GET /v1/models/{id} on a hidden model returns 404 (not 200)."""
    app.state.provider_registry.unregister(Provider.TOGETHER)
    r = await client.get("/v1/models/llama-3.3-70b", headers=api_key_fixture.auth_header)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model_not_found"


@pytest.mark.asyncio
async def test_v1_models_full_catalog_when_all_providers_registered(
    all_providers_registered, client, api_key_fixture
):
    """Sanity: with every provider registered, /v1/models returns the full
    catalog including Together entries."""
    r = await client.get("/v1/models", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    # A Together-only id must be present (it isn't surfaced by any other
    # provider, so it's a clean signal the filter let it through).
    assert "llama-3.3-70b" in ids
    # And the new front-party expansions land too.
    assert "o1" in ids
    assert "claude-3.5-sonnet" in ids
    assert "gemini-1.5-pro" in ids
    assert "deepseek-r1" in ids
    assert "gigachat-2-max" in ids


@pytest.mark.asyncio
async def test_v1_models_hides_together_when_only_openai_registered(client, api_key_fixture):
    """Default conftest registers OpenAI only. Together (and every other
    non-OpenAI provider) must be hidden from /v1/models. This is the
    real CEO scenario: production today has only the front-party keys
    (OpenAI/Anthropic/Google/DeepSeek/Yandex/Sber) but no TOGETHER_API_KEY,
    so clients must not see Llama/Qwen/Mixtral entries.
    """
    r = await client.get("/v1/models", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    # No Together entries.
    assert not any(i.startswith("llama-") for i in ids)
    assert not any(i.startswith("qwen-") for i in ids)
    assert "mixtral-8x22b" not in ids
    assert "deepseek-r1-together" not in ids
    # OpenAI entries still present.
    assert "gpt-5.4-mini" in ids
