"""Tests for prompt-cache wiring (Sprint 4 Поток M, Task 4).

Coverage layered top-down:

* User-supplied ``cache_control`` in a content block survives the gateway
  and reaches the provider verbatim (passthrough — no rewrite).
* ``compute_cost_kopecks`` bills cached tokens at the cached input rate
  (cheaper) — the savings are real.
* Anthropic provider's auto-cache-control on long system prompts still
  works (regression — we changed nothing there but this is the cheapest
  guard against future drift).

Out of scope here:

* Yandex / Sber Redis-side prompt cache — deferred to TD-044 (added to
  registry). Those providers don't expose native cache_control fields;
  building a Redis-keyed system-prompt cache requires per-provider
  hashing + careful invalidation (~10h work; Sprint 5 / V2 candidate).
* Streaming + mid-stream cache hits — providers don't currently emit
  cached_tokens in delta chunks; settled on the final usage event already.
"""

from __future__ import annotations

import pytest

from voltari_gateway.billing import compute_cost_kopecks
from voltari_gateway.providers.base import ChatCompletionUsage
from voltari_gateway.router.catalog import get_model

# ---------- billing arithmetic for cached tokens --------------------------


def test_cached_tokens_billed_cheaper_than_input() -> None:
    """Same total prompt_tokens, different split between cached vs new —
    the cached-heavy split MUST cost less (the whole point of caching)."""
    model = get_model("claude-sonnet-4.6")
    # 10k input tokens, all "fresh": full input rate.
    no_cache = compute_cost_kopecks(
        model,
        ChatCompletionUsage(
            prompt_tokens=10_000,
            completion_tokens=500,
            total_tokens=10_500,
            cached_tokens=0,
        ),
    )
    # Same shape but 8k of those input tokens were served from cache.
    with_cache = compute_cost_kopecks(
        model,
        ChatCompletionUsage(
            prompt_tokens=10_000,
            completion_tokens=500,
            total_tokens=10_500,
            cached_tokens=8_000,
        ),
    )
    # Anthropic charges ~0.1× for cache reads → with_cache must be
    # noticeably cheaper. Threshold is loose to survive catalog tweaks.
    assert with_cache < no_cache
    # Sanity: at least 30% savings when 80% is cached (industry expectation).
    assert with_cache <= int(no_cache * 0.7)


def test_cached_tokens_zero_is_no_op() -> None:
    """cached_tokens=0 → no discount applied (regression guard)."""
    model = get_model("gpt-5.4-mini")
    a = compute_cost_kopecks(
        model,
        ChatCompletionUsage(prompt_tokens=1000, completion_tokens=100, total_tokens=1100),
    )
    b = compute_cost_kopecks(
        model,
        ChatCompletionUsage(
            prompt_tokens=1000,
            completion_tokens=100,
            total_tokens=1100,
            cached_tokens=0,
        ),
    )
    assert a == b


def test_cached_tokens_cannot_exceed_prompt_tokens() -> None:
    """Defensive: caller passing cached > prompt should not produce
    negative non-cached billing."""
    model = get_model("gpt-5.4-mini")
    # Trying cached=2000 when prompt=1000 — non_cached clamps to 0, no
    # negative arithmetic.
    cost = compute_cost_kopecks(
        model,
        ChatCompletionUsage(
            prompt_tokens=1000,
            completion_tokens=100,
            total_tokens=1100,
            cached_tokens=2000,
        ),
    )
    assert cost >= 0


# ---------- gateway preserves cache_control in content blocks --------------


@pytest.mark.asyncio
async def test_cache_control_passthrough_to_provider(client, app, api_key_fixture) -> None:
    """User-supplied ``cache_control`` in a content block must reach the
    provider untouched — gateway is a transparent pipe for caching hints."""
    stub = app.state.openai_provider

    payload = {
        "model": "gpt-5.4-mini",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Long system prompt content here.",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ],
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200, resp.text
    sent = stub.last_request.messages
    # Walk down to the content block.
    blocks = sent[0]["content"]
    assert isinstance(blocks, list)
    assert blocks[0].get("cache_control") == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_no_cache_control_is_default(client, app, api_key_fixture) -> None:
    """Without explicit cache_control, no field is invented at the gateway.

    Provider-specific auto-caching (Anthropic system-prompt threshold) lives
    inside the provider adapter, not the gateway, so a request to OpenAI
    with no cache_control should reach the stub provider with no
    cache_control field present.
    """
    stub = app.state.openai_provider
    payload = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "Just a plain question."}],
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200, resp.text
    sent_msg = stub.last_request.messages[0]
    # String content has no cache_control surface.
    assert sent_msg["content"] == "Just a plain question."


# ---------- Anthropic auto-cache regression -------------------------------


def test_anthropic_split_system_attaches_cache_control_on_long_prompt() -> None:
    """Direct unit-test of `_split_system` — long enough prompt converts
    to block-list with cache_control."""
    from voltari_gateway.providers.anthropic_provider import (
        CACHE_THRESHOLD_CHARS,
        _split_system,
    )

    long_text = "X" * (CACHE_THRESHOLD_CHARS + 100)
    messages = [
        {"role": "system", "content": long_text},
        {"role": "user", "content": "Hi"},
    ]
    system_value, rest = _split_system(messages)
    # Long → list with cache_control.
    assert isinstance(system_value, list)
    assert system_value[0]["cache_control"] == {"type": "ephemeral"}
    # User message preserved.
    assert rest == [{"role": "user", "content": "Hi"}]


def test_anthropic_split_system_skips_cache_control_on_short_prompt() -> None:
    """Short system → plain string, no cache_control overhead."""
    from voltari_gateway.providers.anthropic_provider import _split_system

    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Hi"},
    ]
    system_value, rest = _split_system(messages)
    assert isinstance(system_value, str)
    assert system_value == "Be concise."


# ---------- Sprint 9 Task 4 — extended TTL for Anthropic --------------------


def test_anthropic_split_system_extends_ttl_when_flag_set() -> None:
    """X-Brikko-Cache: anthropic-extended → ttl=1h on auto-emitted cache_control."""
    from voltari_gateway.providers.anthropic_provider import (
        CACHE_THRESHOLD_CHARS,
        _split_system,
    )

    long_text = "Y" * (CACHE_THRESHOLD_CHARS + 50)
    messages = [{"role": "system", "content": long_text}]
    system_value, _ = _split_system(messages, extended_cache=True)
    assert isinstance(system_value, list)
    assert system_value[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_anthropic_apply_extended_ttl_upgrades_user_block() -> None:
    """Caller-supplied cache_control on a content block gets ttl=1h when
    the extended flag is set."""
    from voltari_gateway.providers.anthropic_provider import (
        _apply_extended_ttl_to_messages,
    )

    msgs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Long shared prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]
    out = _apply_extended_ttl_to_messages(msgs)
    assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # Source untouched.
    assert msgs[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_apply_extended_ttl_leaves_uncached_blocks_alone() -> None:
    """We never *add* cache_control — only extend TTL on blocks the caller
    already opted into. A block without cache_control is untouched."""
    from voltari_gateway.providers.anthropic_provider import (
        _apply_extended_ttl_to_messages,
    )

    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "regular text"}]},
    ]
    out = _apply_extended_ttl_to_messages(msgs)
    assert out[0]["content"][0] == {"type": "text", "text": "regular text"}


def test_openai_provider_drops_underscore_prefixed_extras() -> None:
    """Gateway-internal flags (``_brikko_*``) must not leak to OpenAI upstream."""
    from voltari_gateway.providers.base import ChatCompletionRequest
    from voltari_gateway.providers.openai_provider import OpenAIProvider
    from voltari_gateway.router.catalog import get_model

    gpt = get_model("gpt-5.5")
    assert gpt is not None
    req = ChatCompletionRequest(
        model=gpt,
        messages=[{"role": "user", "content": "Hi"}],
        extra={
            "_brikko_anthropic_cache_extended": True,
            "seed": 42,
        },
    )
    kwargs = OpenAIProvider._build_kwargs(req, stream=False)
    assert "_brikko_anthropic_cache_extended" not in kwargs
    # Public extras still pass through.
    assert kwargs["seed"] == 42


@pytest.mark.asyncio
async def test_extended_cache_header_threads_to_anthropic_extra(
    client, app, api_key_fixture
) -> None:
    """Header X-Brikko-Cache: anthropic-extended sets the extra flag on the
    provider request. Verifies end-to-end plumbing for non-stream path."""
    # Wire a stub anthropic provider that records the last request.
    from tests.conftest import StubProvider
    from voltari_gateway.router.catalog import Provider as ProviderEnum

    stub = StubProvider()
    stub.name = "anthropic"  # type: ignore[misc]
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, stub)

    payload = {
        "model": "claude-sonnet-4.6",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Big shared system text",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ],
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={**api_key_fixture.auth_header, "X-Brikko-Cache": "anthropic-extended"},
    )
    assert resp.status_code == 200, resp.text
    # The provider request the stub saw should carry the gateway flag.
    assert stub.last_request.extra.get("_brikko_anthropic_cache_extended") is True
