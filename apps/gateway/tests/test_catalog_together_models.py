"""Together.ai catalog regression tests (Sprint M3, 2026-05-10).

Together adds 24 chat models, 3 embeddings, and 4 image-gen models — total
31 entries, comfortably above the "30+" target in the brief. These tests
guard against:

* model id collisions (we use short ``llama-3.3-70b`` style aliases that
  must not collide with existing OpenAI/Anthropic entries),
* missing prices (every entry must have non-zero kop prices — a 0 here
  means a free LLM, which we don't have),
* regressions on the public-id → upstream-id mapping (Together upstream
  ids are vendor-prefixed paths like ``meta-llama/Llama-3.3-70B-…``).
"""

from __future__ import annotations

from voltari_gateway.router.catalog import CATALOG, Provider, get_model
from voltari_gateway.router.modality_catalog import (
    EMBEDDING_CATALOG,
    IMAGE_CATALOG,
    get_embedding_model,
    get_image_model,
)

# ---------------------------------------------------------------------------
# Expected Together chat models. Order doesn't matter; this is the source of
# truth the test asserts against.
# ---------------------------------------------------------------------------

EXPECTED_TOGETHER_CHAT_IDS: set[str] = {
    "llama-3.3-70b",
    "llama-3.1-405b",
    "llama-3.1-70b",
    "llama-3.1-8b",
    "qwen-2.5-72b",
    "qwen-coder-32b",
    "mixtral-8x22b",
    "mistral-7b",
    "deepseek-v3-together",
    "deepseek-r1-together",
    "dbrx-instruct",
    "gemma-2-27b",
    "gemma-2-9b",
    "nemotron-70b",
    "llama-3.2-90b-vision",
    "llama-3.2-11b-vision",
    "qwen-2-vl-72b",
    "llama-3-70b",
    "llama-3-8b",
    "mixtral-8x7b",
    "wizardlm-2-8x22b",
    "qwen-2.5-7b",
    "deepseek-llm-67b",
    "qwen-72b",
}

EXPECTED_TOGETHER_EMBEDDING_IDS: set[str] = {
    "bge-large-en",
    "bge-base-en",
    "m2-bert-32k",
}

EXPECTED_TOGETHER_IMAGE_IDS: set[str] = {
    "flux-pro",
    "flux-schnell",
    "flux-dev",
    "sdxl-base",
}


def test_together_chat_models_present_and_count() -> None:
    """At least 24 Together chat models in CATALOG, all expected ids present."""
    together_specs = [m for m in CATALOG if m.provider is Provider.TOGETHER]
    together_ids = {m.id for m in together_specs}

    missing = EXPECTED_TOGETHER_CHAT_IDS - together_ids
    assert not missing, f"missing Together chat models: {missing}"
    assert len(together_specs) >= len(EXPECTED_TOGETHER_CHAT_IDS), (
        f"expected at least {len(EXPECTED_TOGETHER_CHAT_IDS)} Together chat "
        f"models, got {len(together_specs)}"
    )


def test_together_chat_models_have_pricing() -> None:
    """No free models — every Together entry must have non-zero in/out price."""
    for spec in CATALOG:
        if spec.provider is not Provider.TOGETHER:
            continue
        assert spec.input_price_kop_per_1k > 0, f"{spec.id}: zero input price"
        assert spec.output_price_kop_per_1k > 0, f"{spec.id}: zero output price"
        assert spec.cached_price_kop_per_1k >= 0, f"{spec.id}: negative cached price"
        # Together has no cache discount — assert symmetry.
        assert spec.cached_price_kop_per_1k == spec.input_price_kop_per_1k, (
            f"{spec.id}: Together has no cache discount, expected "
            f"cached==input but got {spec.cached_price_kop_per_1k} vs "
            f"{spec.input_price_kop_per_1k}"
        )


def test_together_chat_models_have_upstream_id_with_vendor_prefix() -> None:
    """Together upstream ids are vendor-prefixed paths.

    Defensive: catches a typo where someone copy-pastes a public id into
    the upstream_id slot — a request to ``llama-3.3-70b`` would 404 at
    Together (they want ``meta-llama/Llama-3.3-70B-Instruct-Turbo``).
    """
    for spec in CATALOG:
        if spec.provider is not Provider.TOGETHER:
            continue
        # Every Together upstream id we set has a "/" separator (vendor/model).
        assert "/" in spec.upstream_id, (
            f"{spec.id}: upstream_id {spec.upstream_id!r} missing vendor "
            f"prefix — Together expects ``vendor/model-name``."
        )
        # Public id must NOT equal upstream id (we deliberately picked
        # short aliases for SDK convenience).
        assert spec.id != spec.upstream_id, (
            f"{spec.id}: public id == upstream id; pick a shorter alias."
        )


def test_together_chat_models_are_not_ru_legal() -> None:
    """Together is US-hosted — no Together model can be ru_legal=True."""
    for spec in CATALOG:
        if spec.provider is Provider.TOGETHER:
            assert spec.ru_legal is False, f"{spec.id}: ru_legal=True but Together is US-hosted"


def test_together_vision_models_have_supports_vision() -> None:
    """Sanity: vision-tagged Together models declare supports_vision=True."""
    vision_ids = {"llama-3.2-90b-vision", "llama-3.2-11b-vision", "qwen-2-vl-72b"}
    for vid in vision_ids:
        spec = get_model(vid)
        assert spec is not None, f"{vid} missing from catalog"
        assert spec.supports_vision is True, f"{vid}: supports_vision should be True"


def test_together_embedding_models_present() -> None:
    embed_ids = {m.id for m in EMBEDDING_CATALOG if m.provider is Provider.TOGETHER}
    missing = EXPECTED_TOGETHER_EMBEDDING_IDS - embed_ids
    assert not missing, f"missing Together embedding models: {missing}"
    for eid in EXPECTED_TOGETHER_EMBEDDING_IDS:
        spec = get_embedding_model(eid)
        assert spec is not None
        assert spec.usd_per_million_tokens > 0
        assert spec.dimensions > 0


def test_together_image_models_present() -> None:
    img_ids = {m.id for m in IMAGE_CATALOG if m.provider is Provider.TOGETHER}
    missing = EXPECTED_TOGETHER_IMAGE_IDS - img_ids
    assert not missing, f"missing Together image models: {missing}"
    for iid in EXPECTED_TOGETHER_IMAGE_IDS:
        spec = get_image_model(iid)
        assert spec is not None
        # Flat per-image pricing — at least one entry, all > 0.
        assert spec.price_table
        for usd in spec.price_table.values():
            assert usd > 0, f"{iid}: zero price in table"


def test_together_total_models_meets_brief_target() -> None:
    """Brief target: at minimum 30 Together models across modalities."""
    chat = sum(1 for m in CATALOG if m.provider is Provider.TOGETHER)
    embed = sum(1 for m in EMBEDDING_CATALOG if m.provider is Provider.TOGETHER)
    image = sum(1 for m in IMAGE_CATALOG if m.provider is Provider.TOGETHER)
    total = chat + embed + image
    assert total >= 30, (
        f"Together catalog has only {total} entries (chat={chat}, "
        f"embed={embed}, image={image}); brief asks for 30+."
    )


def test_together_provider_in_display_names() -> None:
    """Public catalog endpoint must label Together properly."""
    from voltari_gateway.router.catalog import PROVIDER_DISPLAY_NAMES

    assert PROVIDER_DISPLAY_NAMES.get("together") == "Together AI"


def test_no_together_id_collides_with_other_provider() -> None:
    """Short aliases (llama-3.1-8b etc.) must be unique across the catalog."""
    seen: dict[str, str] = {}
    for spec in CATALOG:
        prev = seen.get(spec.id)
        assert prev is None, (
            f"id collision: {spec.id} appears under provider {prev} and {spec.provider}"
        )
        seen[spec.id] = str(spec.provider)


def test_together_settings_alias_present() -> None:
    """Settings exposes TOGETHER_API_KEY / BASE_URL / TIMEOUT_SECONDS aliases."""
    from voltari_gateway.config import Settings

    fields = Settings.model_fields
    assert "together_api_key" in fields
    assert "together_base_url" in fields
    assert "together_timeout_seconds" in fields
    # Defaults are graceful — no key, no boot crash.
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.together_api_key.get_secret_value() == ""
    assert s.together_base_url == "https://api.together.xyz/v1"
    assert s.together_timeout_seconds == 60.0
