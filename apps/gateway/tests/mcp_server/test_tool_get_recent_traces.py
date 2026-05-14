"""Tests for the ``get_recent_traces`` MCP tool (S3).

Builds synthetic ``gateway_request_log`` rows and exercises:

* Limit default = 20, max = 50.
* Returned rows are in created_at DESC order.
* ``filter_model`` narrows.
* ``filter_status`` narrows ("success" | "error").
* ``store_prompts`` flag suppresses prompt previews when disabled.
* Prompt preview extracts the first 200 chars of the first user message.
* ``status`` normalises non-success values to "error".
* Cost RUB matches kopecks/100 to 2 decimal places.
* Invalid limit → 400.
* Invalid filter_status → 400.
* No principal → RuntimeError.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from voltari_gateway.db.models import GatewayRequestLog
from voltari_gateway.mcp_server.context import MCP_PRINCIPAL_CTX
from voltari_gateway.mcp_server.tools.get_recent_traces import handler
from voltari_gateway.utils.errors import GatewayError


async def _make_log(
    db,
    account_id,
    *,
    model: str = "gpt-5.4-mini",
    provider: str = "openai",
    status: str = "success",
    latency_ms: int = 120,
    cost_kop: int = 5,
    minutes_ago: int = 0,
    request_body: dict | None = None,
    error_message: str | None = None,
) -> GatewayRequestLog:
    now = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    row = GatewayRequestLog(
        id=uuid.uuid4(),
        account_id=account_id,
        api_key_id=None,
        request_id=f"req-{uuid.uuid4().hex[:12]}",
        provider=provider,
        model=model,
        started_at=now - timedelta(milliseconds=latency_ms),
        finished_at=now,
        latency_ms=latency_ms,
        ttft_ms=50,
        prompt_tokens=100,
        completion_tokens=200,
        cached_tokens=0,
        reasoning_tokens=0,
        cost_kop=cost_kop,
        status=status,
        http_code=200 if status == "success" else 500,
        error_code=None if status == "success" else "upstream_error",
        error_message=error_message,
        request_body=request_body,
        created_at=now,
    )
    db.add(row)
    await db.commit()
    return row


async def test_empty_when_no_traces(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["traces"] == []
    assert result["total"] == 0
    assert result["limit"] == 20


async def test_returns_recent_traces_descending(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    # 3 rows at different times. created_at DESC → newest first.
    await _make_log(db, mcp_token_fixture.account.id, minutes_ago=30)
    await _make_log(db, mcp_token_fixture.account.id, minutes_ago=10)
    await _make_log(db, mcp_token_fixture.account.id, minutes_ago=20)

    result = await handler({}, db)
    assert result["total"] == 3
    times = [t["created_at"] for t in result["traces"]]
    assert times == sorted(times, reverse=True)


async def test_respects_limit(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    for i in range(5):
        await _make_log(db, mcp_token_fixture.account.id, minutes_ago=i)
    result = await handler({"limit": 3}, db)
    assert result["total"] == 3
    assert result["limit"] == 3


async def test_filter_by_model(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    await _make_log(db, mcp_token_fixture.account.id, model="gpt-5.4-mini")
    await _make_log(db, mcp_token_fixture.account.id, model="claude-haiku-4.5")
    await _make_log(db, mcp_token_fixture.account.id, model="gpt-5.4-mini")
    result = await handler({"filter_model": "gpt-5.4-mini"}, db)
    assert result["total"] == 2
    assert all(t["model"] == "gpt-5.4-mini" for t in result["traces"])


async def test_filter_by_status_success(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    await _make_log(db, mcp_token_fixture.account.id, status="success")
    await _make_log(
        db,
        mcp_token_fixture.account.id,
        status="error",
        error_message="upstream timeout",
    )
    result = await handler({"filter_status": "success"}, db)
    assert result["total"] == 1
    assert result["traces"][0]["status"] == "success"


async def test_filter_by_status_error_includes_non_success(db, mcp_token_fixture, bind_principal):
    """``timeout`` / ``cancelled`` are also normalised to ``error``."""
    bind_principal(mcp_token_fixture)
    await _make_log(db, mcp_token_fixture.account.id, status="success")
    await _make_log(db, mcp_token_fixture.account.id, status="error")
    await _make_log(db, mcp_token_fixture.account.id, status="timeout")
    result = await handler({"filter_status": "error"}, db)
    assert result["total"] == 2
    for t in result["traces"]:
        assert t["status"] == "error"


async def test_prompt_preview_when_store_prompts_on(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    # mcp_token_fixture's account has store_prompts=True (conftest default).
    body = {"messages": [{"role": "user", "content": "Hello, world!" * 30}]}
    await _make_log(db, mcp_token_fixture.account.id, request_body=body)
    result = await handler({}, db)
    assert result["store_prompts_enabled"] is True
    assert result["traces"][0]["prompt_preview"] is not None
    assert "Hello, world!" in result["traces"][0]["prompt_preview"]
    # 200-char cap
    assert len(result["traces"][0]["prompt_preview"]) <= 200


async def test_prompt_preview_suppressed_when_store_prompts_off(
    db, mcp_token_fixture, bind_principal
):
    bind_principal(mcp_token_fixture)
    mcp_token_fixture.account.store_prompts = False
    await db.commit()
    body = {"messages": [{"role": "user", "content": "Sensitive content here"}]}
    await _make_log(db, mcp_token_fixture.account.id, request_body=body)
    result = await handler({}, db)
    assert result["store_prompts_enabled"] is False
    assert result["traces"][0]["prompt_preview"] is None


async def test_prompt_preview_handles_vision_content_list(db, mcp_token_fixture, bind_principal):
    """OpenAI/Anthropic vision content is a list of {type, text/image}.
    The extractor should pick the text chunks and skip image chunks
    without blowing up.
    """
    bind_principal(mcp_token_fixture)
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
    }
    await _make_log(db, mcp_token_fixture.account.id, request_body=body)
    result = await handler({}, db)
    assert "What's in this image?" in (result["traces"][0]["prompt_preview"] or "")


async def test_other_account_traces_not_returned(
    db, mcp_token_fixture, bind_principal, seed_mcp_token
):
    """A token must never see another account's traces — the auth
    boundary is account_id, not just user_id.
    """
    other = await seed_mcp_token()
    await _make_log(db, other.account.id, model="gpt-5.4-mini")
    bind_principal(mcp_token_fixture)
    result = await handler({}, db)
    assert result["total"] == 0


async def test_cost_rub_matches_kopecks(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    await _make_log(db, mcp_token_fixture.account.id, cost_kop=12345)
    result = await handler({}, db)
    trace = result["traces"][0]
    assert trace["cost_kopecks"] == 12345
    assert trace["cost_rub"] == 123.45


async def test_error_message_only_on_error_status(db, mcp_token_fixture, bind_principal):
    """Success traces don't carry an error_message; error traces do.
    Defensive against accidental leakage of error fields on success."""
    bind_principal(mcp_token_fixture)
    await _make_log(
        db,
        mcp_token_fixture.account.id,
        status="error",
        error_message="upstream timeout 504",
    )
    await _make_log(
        db,
        mcp_token_fixture.account.id,
        status="success",
        error_message=None,
    )
    result = await handler({}, db)
    # newest first; second one (success) should have None error_message
    success_traces = [t for t in result["traces"] if t["status"] == "success"]
    error_traces = [t for t in result["traces"] if t["status"] == "error"]
    assert all(t["error_message"] is None for t in success_traces)
    assert all(t["error_message"] == "upstream timeout 504" for t in error_traces)


async def test_invalid_limit_below_one(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler({"limit": 0}, db)
    assert exc.value.error_code == "invalid_limit"


async def test_invalid_limit_above_max(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler({"limit": 9999}, db)
    assert exc.value.error_code == "invalid_limit"


async def test_invalid_limit_wrong_type(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler({"limit": "twenty"}, db)
    assert exc.value.error_code == "invalid_limit"


async def test_invalid_filter_status(db, mcp_token_fixture, bind_principal):
    bind_principal(mcp_token_fixture)
    with pytest.raises(GatewayError) as exc:
        await handler({"filter_status": "panic"}, db)
    assert exc.value.error_code == "invalid_filter_status"


async def test_raises_without_principal(db, mcp_token_fixture):
    token = MCP_PRINCIPAL_CTX.set(None)
    try:
        with pytest.raises(RuntimeError, match="without an authenticated"):
            await handler({}, db)
    finally:
        MCP_PRINCIPAL_CTX.reset(token)
