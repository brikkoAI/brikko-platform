"""PII masking for /v1/embeddings (Sprint 13 / Privacy v2 — Phase 4).

**Crucial design constraint** verified by these tests:
``Account.pii_masking_enabled`` is **deliberately ignored** for the
embeddings endpoint. Masking embedding inputs changes the resulting
vector by design (replacing «Иванов» with ``<NAME_1>`` produces a
different vector). If we silently masked for ``PRO_PRIVACY`` accounts
their RAG pipelines would break — same texts would embed to different
vectors at index time vs. query time, breaking retrieval. Customers who
genuinely want masked-vector retrieval set ``pii_protect: true``
explicitly per call.

Coverage:

* Header opt-in masks ``input`` (single string).
* Body field opt-in masks ``input`` (list of strings — each item).
* Account flag does **NOT** mask (regression test for the carve-out).
* Default → no masking.
* No-PII input → no-op (no header/body flags = no mutation regardless).

Strategy: respx-mock OpenAI's ``/embeddings`` upstream and inspect the
request body sent to it. We never assert on the response (it's a vector
of floats — no unmask path).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from voltari_gateway.db.models import Account


def _vector(dim: int = 1536) -> list[float]:
    return [0.001 * i for i in range(dim)]


def _embedding_response(num_inputs: int, prompt_tokens: int = 10) -> dict:
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": _vector()} for i in range(num_inputs)
        ],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }


# ---------------------------------------------------------------------------
# Header opt-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_header_masks_string_input(client, api_key_fixture):
    """X-PII-Protect: true → email replaced by placeholder upstream."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json=_embedding_response(num_inputs=1))
        )
        r = await client.post(
            "/v1/embeddings",
            json={
                "model": "text-embedding-3-small",
                "input": "Свяжись с alice@example.io по поводу контракта.",
            },
            headers={
                **api_key_fixture.auth_header,
                "X-PII-Protect": "true",
            },
        )
        assert r.status_code == 200, r.text

        sent = json.loads(route.calls.last.request.content)
        assert "alice@example.io" not in sent["input"]
        assert "<EMAIL_1>" in sent["input"]


# ---------------------------------------------------------------------------
# Body flag opt-in (multi-input)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_body_flag_masks_each_array_item(client, api_key_fixture):
    """``pii_protect: true`` + array input → every string is masked."""
    inputs = [
        "Иванов Иван Иванович написал письмо.",
        "Контакт: bob@example.io телефон +79161234567.",
        "hello world plain text no sensitive data here",
    ]
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json=_embedding_response(num_inputs=len(inputs)))
        )
        r = await client.post(
            "/v1/embeddings",
            json={
                "model": "text-embedding-3-small",
                "input": inputs,
                "pii_protect": True,
            },
            headers=api_key_fixture.auth_header,
        )
        assert r.status_code == 200, r.text

        sent = json.loads(route.calls.last.request.content)
        sent_inputs = sent["input"]
        assert isinstance(sent_inputs, list)
        assert len(sent_inputs) == len(inputs)

        # Item 0: name masked.
        assert "Иванов Иван Иванович" not in sent_inputs[0]
        assert "<NAME_" in sent_inputs[0]

        # Item 1: email + phone masked.
        assert "bob@example.io" not in sent_inputs[1]
        assert "+79161234567" not in sent_inputs[1]
        assert "<EMAIL_" in sent_inputs[1]
        assert "<PHONE_" in sent_inputs[1]

        # Item 2: no PII → unchanged.
        assert sent_inputs[2] == inputs[2]


# ---------------------------------------------------------------------------
# Account flag IGNORED — regression test for retrieval-semantics carve-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_account_flag_does_NOT_mask_embeddings(  # noqa: N802
    client, api_key_fixture, app, db
):
    """Account.pii_masking_enabled=True is INTENTIONALLY ignored for
    embeddings. Masking would change the vector → break RAG retrieval.

    This is the most important test in this file: it proves the carve-out
    is wired correctly. If a future refactor accidentally routed embeddings
    through the chat-style three-way gate, this test would fail loudly.
    """
    account = await db.get(Account, api_key_fixture.account.id)
    account.pii_masking_enabled = True
    db.add(account)
    await db.commit()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json=_embedding_response(num_inputs=1))
        )
        r = await client.post(
            "/v1/embeddings",
            json={
                "model": "text-embedding-3-small",
                "input": "Иванов Иван Иванович — alice@example.io.",
            },
            headers=api_key_fixture.auth_header,  # NO X-PII-Protect / pii_protect
        )
        assert r.status_code == 200, r.text

        sent = json.loads(route.calls.last.request.content)
        # Account flag must NOT have triggered masking — original PII
        # forwarded verbatim.
        assert "Иванов Иван Иванович" in sent["input"]
        assert "alice@example.io" in sent["input"]
        assert "<NAME_" not in sent["input"]
        assert "<EMAIL_" not in sent["input"]


# ---------------------------------------------------------------------------
# Default — no masking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_disabled_by_default(client, api_key_fixture):
    """No header, no body flag, no account flag → input forwarded unchanged."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json=_embedding_response(num_inputs=1))
        )
        r = await client.post(
            "/v1/embeddings",
            json={
                "model": "text-embedding-3-small",
                "input": "Email is dev@x.io",
            },
            headers=api_key_fixture.auth_header,
        )
        assert r.status_code == 200, r.text

        sent = json.loads(route.calls.last.request.content)
        assert sent["input"] == "Email is dev@x.io"
        assert "<EMAIL_" not in sent["input"]


# ---------------------------------------------------------------------------
# No PII in input → no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_optin_no_pii_present_is_noop(client, api_key_fixture):
    """Header on, but no PII in input → input passes through unchanged."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json=_embedding_response(num_inputs=1))
        )
        r = await client.post(
            "/v1/embeddings",
            json={
                "model": "text-embedding-3-small",
                "input": "Hello, what is the weather?",
            },
            headers={
                **api_key_fixture.auth_header,
                "X-PII-Protect": "true",
            },
        )
        assert r.status_code == 200, r.text
        sent = json.loads(route.calls.last.request.content)
        assert sent["input"] == "Hello, what is the weather?"
