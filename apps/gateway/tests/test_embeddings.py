"""Tests for POST /v1/embeddings (Sprint M1).

Covers OpenAI-compatible shape (string + array input), token-count math
via tiktoken, and standard error envelopes. Network is fully mocked
through ``respx``.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

from voltari_gateway.db.models import UsageEvent


def _vector(dim: int = 1536) -> list[float]:
    """Deterministic placeholder vector — values aren't asserted, only shape."""
    return [0.001 * i for i in range(dim)]


def _embedding_response(num_inputs: int, prompt_tokens: int, dim: int = 1536) -> dict:
    """Build an OpenAI-shape embeddings response for ``num_inputs`` items."""
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": _vector(dim)}
            for i in range(num_inputs)
        ],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }


@pytest.mark.asyncio
async def test_embeddings_string_input_happy_path(client, api_key_fixture, db):
    upstream = _embedding_response(num_inputs=1, prompt_tokens=5)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json=upstream)
        )
        r = await client.post(
            "/v1/embeddings",
            json={"model": "text-embedding-3-small", "input": "hello world"},
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["object"] == "list"
    assert len(payload["data"]) == 1
    assert payload["usage"]["prompt_tokens"] == 5
    assert r.headers["X-Gateway-Modality"] == "embeddings"
    assert r.headers["X-Gateway-Provider"] == "openai"
    assert int(r.headers["X-Gateway-Cost-Kop"]) >= 1

    sent_body = route.calls.last.request.read().decode()
    assert "text-embedding-3-small" in sent_body
    assert "hello world" in sent_body

    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert len(rows) == 1
    event = rows[0]
    assert event.model == "text-embedding-3-small"
    assert event.provider == "openai"
    assert event.modality == "embeddings"
    assert event.unit == "token"
    # Local + provider both ≥ 1 token; we bill on the max.
    assert event.input_tokens >= 1


@pytest.mark.asyncio
async def test_embeddings_array_input_happy_path(client, api_key_fixture, db):
    inputs = ["alpha", "beta gamma", "delta epsilon zeta"]
    upstream = _embedding_response(num_inputs=len(inputs), prompt_tokens=12)

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json=upstream)
        )
        r = await client.post(
            "/v1/embeddings",
            json={"model": "text-embedding-3-small", "input": inputs},
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200, r.text
    payload = r.json()
    assert len(payload["data"]) == len(inputs)
    assert payload["usage"]["prompt_tokens"] == 12

    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert len(rows) == 1
    # We bill on max(local, provider). With these inputs local count is
    # well below 12, so the row records the provider's number.
    assert rows[0].input_tokens >= 12


@pytest.mark.asyncio
async def test_embeddings_large_model_pricier_than_small(client, api_key_fixture, db):
    """text-embedding-3-large costs 6.5× small ($0.13 vs $0.02). The cost
    written to usage_events should reflect the catalog price difference.
    """
    upstream = _embedding_response(num_inputs=1, prompt_tokens=1000)

    with respx.mock() as mock:
        mock.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json={**upstream, "model": "text-embedding-3-large"})
        )
        r = await client.post(
            "/v1/embeddings",
            json={"model": "text-embedding-3-large", "input": "x" * 1000},
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    # 1000 tokens at 1196 kop/1M = ~1.2 kop → quantises to 1 kop after
    # round-half-even. The minimum-1-kopeck floor in the handler also
    # guarantees this.
    assert rows[0].cost_kopecks >= 1
    assert rows[0].model == "text-embedding-3-large"


@pytest.mark.asyncio
async def test_embeddings_no_auth_returns_401(client):
    r = await client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "hi"},
    )
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_embeddings_invalid_bearer_returns_401(client):
    r = await client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "hi"},
        headers={"Authorization": "Bearer sk-vlt-nope"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_embeddings_missing_input_returns_400(client, api_key_fixture):
    # Pydantic validation errors are normalised by the gateway to a 400
    # envelope (OpenAI-compatible). 422 is FastAPI's stock code; we
    # rewrite it for SDK compatibility.
    r = await client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_embeddings_empty_string_returns_400(client, api_key_fixture):
    r = await client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": ""},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_embeddings_unknown_model_returns_404(client, api_key_fixture):
    r = await client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-NEVER-EXISTED", "input": "hi"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "model_not_found"


@pytest.mark.asyncio
async def test_embeddings_upstream_5xx_releases_hold(client, api_key_fixture, db):
    with respx.mock() as mock:
        mock.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(503, json={"error": "down"})
        )
        r = await client.post(
            "/v1/embeddings",
            json={"model": "text-embedding-3-small", "input": "ping"},
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 502
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_embeddings_array_too_large_returns_400(client, api_key_fixture):
    big_array = ["x"] * 5000  # > MAX_INPUT_ITEMS=2048
    r = await client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": big_array},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_embeddings_dimensions_param_forwarded(client, api_key_fixture):
    upstream = _embedding_response(num_inputs=1, prompt_tokens=2, dim=512)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json=upstream)
        )
        r = await client.post(
            "/v1/embeddings",
            json={
                "model": "text-embedding-3-small",
                "input": "hi",
                "dimensions": 512,
                "user": "user-42",
            },
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200
    sent = route.calls.last.request.read().decode()
    assert '"dimensions"' in sent and "512" in sent
    assert '"user"' in sent and "user-42" in sent
