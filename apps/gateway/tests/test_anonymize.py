"""Integration tests for /v1/anonymize and /v1/restore.

Cover happy-path round-trip, auth, expired-mapping 404, idempotency,
and PII detection across the main RU categories.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_anonymize_happy_path_masks_inn_and_phone(client, api_key_fixture):
    text = "Клиент Иванов Иван Иванович, ИНН 7707083893, телефон +7 999 123 45 67"

    r = await client.post(
        "/v1/anonymize",
        json={"text": text},
        headers=api_key_fixture.auth_header,
    )

    assert r.status_code == 200, r.text
    payload = r.json()

    # Original strings must NOT appear in the masked output.
    assert "Иванов" not in payload["masked_text"]
    assert "7707083893" not in payload["masked_text"]
    assert "999" not in payload["masked_text"]

    # At least 2 distinct PII categories detected (NAME / INN / PHONE).
    assert payload["count"] >= 2
    assert payload["mapping_id"]
    assert len(payload["mapping_id"]) >= 8
    assert payload["expires_at_unix"] > 0

    # Audit summary is structurally well-formed.
    types = {entry["type"] for entry in payload["audit"]}
    assert types  # at least one
    for entry in payload["audit"]:
        assert entry["count"] >= 1
        assert len(entry["placeholders"]) == entry["count"]


@pytest.mark.asyncio
async def test_anonymize_then_restore_round_trip(client, api_key_fixture):
    original = "ИНН 7707083893"

    r_mask = await client.post(
        "/v1/anonymize",
        json={"text": original},
        headers=api_key_fixture.auth_header,
    )
    assert r_mask.status_code == 200, r_mask.text
    masked = r_mask.json()

    # Compose a fake LLM-style response that quotes the placeholder.
    placeholders = [p for entry in masked["audit"] for p in entry["placeholders"]]
    assert placeholders, "expected at least one INN placeholder"
    fake_llm_text = f"Validated: {placeholders[0]} found in tax registry."

    r_restore = await client.post(
        "/v1/restore",
        json={"text": fake_llm_text, "mapping_id": masked["mapping_id"]},
        headers=api_key_fixture.auth_header,
    )
    assert r_restore.status_code == 200, r_restore.text
    assert "7707083893" in r_restore.json()["restored_text"]


@pytest.mark.asyncio
async def test_anonymize_no_pii_returns_empty_mapping_id(client, api_key_fixture):
    r = await client.post(
        "/v1/anonymize",
        json={"text": "Hello world, no personal data here at all."},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    # No PII → no mapping persisted → empty mapping_id signals "nothing to restore".
    assert payload["count"] == 0
    assert payload["mapping_id"] == ""
    assert payload["audit"] == []


@pytest.mark.asyncio
async def test_restore_unknown_mapping_id_returns_404(client, api_key_fixture):
    r = await client.post(
        "/v1/restore",
        json={"text": "<INN_1> placeholder", "mapping_id": "nonexistent-mapping-id-xx"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 404
    # Gateway transforms HTTPException → custom error envelope (or stays
    # FastAPI default {"detail": "..."}). Check both shapes.
    body = r.json()
    msg = body.get("detail") or body.get("error", {}).get("message", "")
    assert "not found" in msg.lower() or "expired" in msg.lower()


@pytest.mark.asyncio
async def test_anonymize_no_auth_returns_401(client):
    r = await client.post("/v1/anonymize", json={"text": "ИНН 7707083893"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_anonymize_invalid_bearer_returns_401(client):
    r = await client.post(
        "/v1/anonymize",
        json={"text": "ИНН 7707083893"},
        headers={"Authorization": "Bearer sk-brk-fake-key"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_anonymize_empty_text_returns_422(client, api_key_fixture):
    """Pydantic min_length=1 rejects empty text with 422 (validation error)."""
    r = await client.post(
        "/v1/anonymize",
        json={"text": ""},
        headers=api_key_fixture.auth_header,
    )
    # Gateway maps Pydantic validation → 400 (custom envelope). Accept either.
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_anonymize_ttl_clamped_to_valid_range(client, api_key_fixture):
    # Below min (60s) → 422.
    r = await client.post(
        "/v1/anonymize",
        json={"text": "ИНН 7707083893", "ttl_seconds": 10},
        headers=api_key_fixture.auth_header,
    )
    # Gateway maps Pydantic validation → 400 (custom envelope). Accept either.
    assert r.status_code in (400, 422)

    # Above max (86400s) → 422.
    r = await client.post(
        "/v1/anonymize",
        json={"text": "ИНН 7707083893", "ttl_seconds": 100_000},
        headers=api_key_fixture.auth_header,
    )
    # Gateway maps Pydantic validation → 400 (custom envelope). Accept either.
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_restore_idempotent_within_ttl(client, api_key_fixture):
    """Calling /restore twice with the same mapping_id returns the same result —
    we don't delete the mapping on first restore (different from /v1/chat
    which auto-deletes after unmask)."""
    r_mask = await client.post(
        "/v1/anonymize",
        json={"text": "ИНН 7707083893"},
        headers=api_key_fixture.auth_header,
    )
    assert r_mask.status_code == 200
    masked = r_mask.json()
    placeholder = masked["audit"][0]["placeholders"][0]

    payload = {"text": placeholder, "mapping_id": masked["mapping_id"]}
    r1 = await client.post("/v1/restore", json=payload, headers=api_key_fixture.auth_header)
    r2 = await client.post("/v1/restore", json=payload, headers=api_key_fixture.auth_header)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()
