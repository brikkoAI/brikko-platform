"""Sprint 5 — bug-hunt + edge-case regression tests.

Все эти тесты являются результатом методичного прочёсывания billing /
auth / router / pii / api модулей перед публичным запуском. Каждый
кейс соответствует либо реальному найденному багу (BUG #N), либо
edge case'у, который МОЖЕТ привести к багу при изменении кода.

Группы:

* L.1  PII-маскинг — placeholder injection, multi-byte, заглушки
* L.2  Chat — list-content валидация, многоблочные payload'ы
* L.3  Billing — webhook negative amounts, refund overdraft floor
* L.4  Auth — пароли, токены, timing
* L.5  Encoding — Unicode/RTL/эмодзи в полях
* L.6  Boundary — max_tokens, stop tokens, очень длинные значения

Не дублируем тесты, которые уже есть в test_*_edge_cases.py /
test_pii_masker.py / test_chat_pii.py — здесь только новые сценарии,
которые те файлы не покрывали.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from voltari_gateway.api.chat import ChatCompletionRequestBody
from voltari_gateway.auth.email_verification import (
    generate_verification_token,
    hash_token,
    verify_password_reset_token,
    verify_verification_token,
)
from voltari_gateway.auth.password import (
    MAX_PASSWORD_LEN,
    MIN_PASSWORD_LEN,
    hash_password,
    needs_rehash,
    verify_password,
)
from voltari_gateway.billing.yookassa import YooKassaError, parse_webhook
from voltari_gateway.pii.masker import (
    PiiMapping,
    detect_pii,
    mask_messages,
    mask_text,
    unmask_text,
)


def _assert_openai_envelope(resp: Any, *, expected_status: int | tuple[int, ...]) -> dict[str, Any]:
    """Same shape as test_chat_edge_cases — pin OpenAI-style envelope."""
    assert resp.status_code != 500, (
        f"500 leaked through: {resp.text[:300]} — every user-facing error must be a 4xx."
    )
    if isinstance(expected_status, int):
        assert resp.status_code == expected_status, (resp.status_code, resp.text[:200])
    else:
        assert resp.status_code in expected_status, (resp.status_code, resp.text[:200])
    body = resp.json()
    assert "error" in body, body
    return body["error"]  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# L.1 PII masker — placeholder injection / multi-byte / edge cases
# ---------------------------------------------------------------------------


def test_pii_placeholder_injection_neutralised() -> None:
    """BUG #2: user-typed `<NAME_1>` must NOT round-trip as someone else's PII.

    Adversarial flow:
      1. User puts real name "Иванов Иван Иванович" in message → placeholder NAME_1.
      2. Same request, another message contains literal text "<NAME_1>".
      3. Without the fix, unmask substitutes user's real name into the model's
         response anywhere `<NAME_1>` appears — including text the user typed
         themselves. Cross-message PII leakage within the same request.

    Fix: ``_neutralise_user_placeholders`` defangs the leading `<` to `＜`
    (U+FF1C, fullwidth) before regex scanning. The model still sees user
    intent; unmask cannot misroute.
    """
    m = PiiMapping()
    real = "Я Иванов Иван Иванович"
    masked_real = mask_text(real, m)
    assert "<NAME_1>" in masked_real

    adversarial = "Привет <NAME_1>"
    masked_adv = mask_text(adversarial, m)
    # Adversarial placeholder must be defanged — no substitution on round-trip.
    restored_adv = unmask_text(masked_adv, m)
    assert "Иванов" not in restored_adv, f"PII leakage via placeholder injection: {restored_adv!r}"

    # Legitimate path still round-trips correctly.
    restored_real = unmask_text(masked_real, m)
    assert restored_real == real, "round-trip broken for legit text"


def test_pii_emoji_in_content_does_not_break_masker() -> None:
    """4-byte UTF-8 emoji shouldn't crash detection or shift offsets.

    Multi-byte chars matter because Python's str indexing is in code points,
    but the regex engine is too — ensure we don't accidentally introduce
    byte-offset arithmetic anywhere.
    """
    m = PiiMapping()
    text = "Email 🚀 user@test.com 🎉 contact"
    masked = mask_text(text, m)
    assert "<EMAIL_1>" in masked
    assert "🚀" in masked  # emoji preserved
    assert unmask_text(masked, m) == text


def test_pii_rtl_marker_in_name_does_not_break() -> None:
    """RTL override character (U+202E) inside text shouldn't crash."""
    m = PiiMapping()
    text = "Иванов‮Иван‭Иванович тестовый"
    masked = mask_text(text, m)
    # Whether the RTL marker is part of NAME match or splits it — doesn't
    # matter, just must not raise. The contract is "no exception".
    assert isinstance(masked, str)


def test_pii_empty_mapping_unmask_is_noop() -> None:
    """``unmask_text`` on empty mapping is a literal no-op."""
    m = PiiMapping()
    text = "Hello <NAME_1> world"  # placeholder-shaped but mapping empty
    assert unmask_text(text, m) == text


def test_pii_placeholder_with_huge_n_doesnt_crash() -> None:
    """Multi-digit placeholder (NAME_999) must round-trip."""
    m = PiiMapping()
    # Manually craft a mapping with a 3-digit placeholder.
    m.reverse["<NAME_999>"] = "Тест Тестов Тестович"
    m.forward["Тест Тестов Тестович"] = "<NAME_999>"
    out = unmask_text("Привет <NAME_999>", m)
    assert out == "Привет Тест Тестов Тестович"


def test_pii_repeat_pii_dedupes_to_same_placeholder() -> None:
    """One name appearing twice → one placeholder used twice (model entity preservation)."""
    m = PiiMapping()
    text = "Иванов Иван Иванович сказал, что Иванов Иван Иванович работает"
    masked = mask_text(text, m)
    # Both occurrences should be the same placeholder, not NAME_1 + NAME_2.
    assert masked.count("<NAME_1>") == 2
    assert "<NAME_2>" not in masked


def test_pii_messages_with_tool_calls_mask_arguments_only() -> None:
    """``role: assistant`` with ``tool_calls`` — args masked, name/id intact."""
    msgs = [
        {"role": "user", "content": "Call me at +7 999 111 22 33"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "send_sms",  # not maskable
                        "arguments": json.dumps({"to": "+7 999 111 22 33"}),
                    },
                }
            ],
        },
    ]
    out, mapping = mask_messages(msgs)
    # User message phone replaced.
    assert "<PHONE_1>" in out[0]["content"]
    # Tool-call arguments string also masked (same placeholder!).
    assert "<PHONE_1>" in out[1]["tool_calls"][0]["function"]["arguments"]
    # Tool name/id NOT masked.
    assert out[1]["tool_calls"][0]["function"]["name"] == "send_sms"
    assert out[1]["tool_calls"][0]["id"] == "call_1"


def test_pii_detect_overlapping_patterns_resolved_by_priority() -> None:
    """16-digit run shouldn't be split between CARD and INN.

    После Sprint 13 / Task 1.2 — карта проверяется Luhn'ом, поэтому
    fixture обязан быть Luhn-valid (4532015112830366 — test Visa).
    """
    text = "Карта 4532015112830366 не должна быть как ИНН"
    detections = detect_pii(text)
    types = [t for t, _ in detections]
    # CARD takes priority over INN (longer pattern first).
    assert "CARD" in types
    assert "INN" not in types


# ---------------------------------------------------------------------------
# L.2 Chat request — list-content / multimodal length validation
# ---------------------------------------------------------------------------


def test_chat_list_content_huge_block_rejected() -> None:
    """BUG #1: a single text-block >200k chars must be rejected.

    The original validator only checked ``isinstance(content, str)``. A
    legitimate vision payload uses ``content: [{"type": "text", ...}, ...]``
    — that path was completely unchecked. Sending a 5 MB block in the
    list form would happily reach the provider call.
    """
    big_block = [{"type": "text", "text": "a" * 500_000}]
    with pytest.raises(Exception):  # ValidationError
        ChatCompletionRequestBody(
            model="gpt-5.4-mini",
            messages=[{"role": "user", "content": big_block}],
        )


def test_chat_list_content_aggregate_rejected() -> None:
    """BUG #1: 50 blocks × 100k chars (5MB) must be rejected by aggregate cap."""
    blocks = [{"type": "text", "text": "a" * 100_000} for _ in range(50)]
    with pytest.raises(Exception):
        ChatCompletionRequestBody(
            model="gpt-5.4-mini",
            messages=[{"role": "user", "content": blocks}],
        )


def test_chat_list_content_legitimate_vision_accepted() -> None:
    """Legitimate vision payload (text + image_url) must still pass."""
    blocks = [
        {"type": "text", "text": "describe this image"},
        {"type": "image_url", "image_url": {"url": "https://x.example/cat.jpg"}},
    ]
    body = ChatCompletionRequestBody(
        model="gpt-5.4-mini",
        messages=[{"role": "user", "content": blocks}],
    )
    assert len(body.messages) == 1


def test_chat_list_content_total_under_limit_accepted() -> None:
    """Two blocks of 150k chars (300k total) — within 400k aggregate cap."""
    blocks = [{"type": "text", "text": "a" * 150_000} for _ in range(2)]
    body = ChatCompletionRequestBody(
        model="gpt-5.4-mini",
        messages=[{"role": "user", "content": blocks}],
    )
    assert body is not None


@pytest.mark.asyncio
async def test_chat_list_content_huge_returns_400(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    """End-to-end: list-content >limit → 400, NEVER 500."""
    big_block = [{"type": "text", "text": "z" * 500_000}]
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": big_block}],
        },
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


# ---------------------------------------------------------------------------
# L.3 Billing webhook — negative / nonsense amounts
# ---------------------------------------------------------------------------


def test_yookassa_webhook_negative_amount_rejected_at_parse() -> None:
    """BUG #3: a webhook with negative ``amount.value`` must fail to parse.

    Without the parse-level guard the negative amount would reach the
    event-dispatch code already authenticated and attributed (in logs +
    DLQ rows), which makes the audit-trail messy. Reject earlier.
    """
    body = json.dumps(
        {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "p1",
                "status": "succeeded",
                "amount": {"value": "-100.00", "currency": "RUB"},
            },
        }
    ).encode("utf-8")
    with pytest.raises(YooKassaError, match="negative_amount"):
        parse_webhook(body)


def test_yookassa_webhook_zero_amount_still_parses() -> None:
    """Zero is allowed to parse — the API layer routes it to DLQ.

    Existing contract from test_billing_webhook.py says zero amount must
    end up in the DLQ for ops review (not silently ignored). So the parser
    keeps the 0 path open; only negative is rejected.
    """
    body = json.dumps(
        {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "p2",
                "status": "succeeded",
                "amount": {"value": "0.00", "currency": "RUB"},
            },
        }
    ).encode("utf-8")
    result = parse_webhook(body)
    assert result.amount_kopecks == 0


def test_yookassa_webhook_huge_amount_doesnt_overflow() -> None:
    """Amount of 999_999_999.99 RUB → 99_999_999_999 kop. Must round cleanly."""
    body = json.dumps(
        {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "p3",
                "status": "succeeded",
                "amount": {"value": "999999999.99", "currency": "RUB"},
            },
        }
    ).encode("utf-8")
    result = parse_webhook(body)
    # Rounding tolerance for float — should be exact for this value.
    assert result.amount_kopecks == 99_999_999_999


def test_yookassa_webhook_malformed_amount_string_rejected() -> None:
    """Non-numeric ``value`` → YooKassaError(webhook_bad_amount)."""
    body = json.dumps(
        {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {"id": "p4", "amount": {"value": "abc", "currency": "RUB"}},
        }
    ).encode("utf-8")
    with pytest.raises(YooKassaError, match="bad_amount"):
        parse_webhook(body)


# ---------------------------------------------------------------------------
# L.4 Auth — password + email tokens
# ---------------------------------------------------------------------------


def test_password_min_length_strictly_enforced() -> None:
    """Hash function must reject 7-char password (boundary - 1)."""
    with pytest.raises(ValueError, match="at least"):
        hash_password("a" * (MIN_PASSWORD_LEN - 1))


def test_password_max_length_strictly_enforced() -> None:
    """Hash function must reject 129-char password (boundary + 1).

    Defends against billion-laughs DoS — argon2 is CPU-heavy; an attacker
    sending 100MB passwords would saturate workers fast.
    """
    with pytest.raises(ValueError, match="at most"):
        hash_password("a" * (MAX_PASSWORD_LEN + 1))


def test_password_at_max_length_accepted() -> None:
    """Exactly MAX_PASSWORD_LEN must be accepted (boundary)."""
    h = hash_password("a" * MAX_PASSWORD_LEN)
    assert h.startswith("$argon2")
    assert verify_password("a" * MAX_PASSWORD_LEN, h)


def test_password_at_min_length_accepted() -> None:
    """Exactly MIN_PASSWORD_LEN must be accepted."""
    h = hash_password("x" * MIN_PASSWORD_LEN)
    assert verify_password("x" * MIN_PASSWORD_LEN, h)


def test_password_verify_empty_inputs_returns_false() -> None:
    """Empty plain or hash must return False (never crash)."""
    assert verify_password("", "$argon2id$dummy") is False
    assert verify_password("password", "") is False
    assert verify_password("", "") is False


def test_password_verify_garbage_hash_returns_false() -> None:
    """Garbage hash must return False, not raise."""
    assert verify_password("password123", "not-a-hash") is False
    assert verify_password("password123", "$argon2id$invalid") is False


def test_needs_rehash_on_garbage_hash_returns_false() -> None:
    """``needs_rehash`` must not crash on malformed input."""
    assert needs_rehash("garbage") is False
    assert needs_rehash("") is False


def test_email_verification_token_round_trip() -> None:
    """Generate → verify on a fresh token must return the original (uid, email)."""
    uid = uuid.uuid4()
    email = "user@test.local"
    token = generate_verification_token(uid, email)
    parsed = verify_verification_token(token)
    assert parsed is not None
    assert parsed[0] == uid
    assert parsed[1] == email


def test_email_verification_token_with_modified_payload_rejected() -> None:
    """Tampering with the token (flipping a single base64 char) → None."""
    uid = uuid.uuid4()
    token = generate_verification_token(uid, "test@test.com")
    # Flip a char in the middle of the payload.
    tampered = token[:20] + ("Y" if token[20] != "Y" else "Z") + token[21:]
    assert verify_verification_token(tampered) is None


def test_email_verification_token_garbage_returns_none() -> None:
    """Random string → None (never raises)."""
    assert verify_verification_token("garbage-not-a-token") is None
    assert verify_verification_token("") is None


def test_email_verification_token_distinct_from_password_reset() -> None:
    """Reset and verify tokens use different SALTs → can't be cross-replayed."""
    uid = uuid.uuid4()
    verify_token = generate_verification_token(uid, "x@y.com")
    # Pass a verify-token to the reset-verifier — must reject.
    assert verify_password_reset_token(verify_token) is None


def test_email_token_hash_constant_time_properties() -> None:
    """Hash of same plaintext must be deterministic; different plaintext → different hash."""
    a = hash_token("token-1")
    b = hash_token("token-1")
    c = hash_token("token-2")
    assert a == b  # deterministic
    assert a != c  # different inputs → different outputs


# ---------------------------------------------------------------------------
# L.5 Encoding — Unicode / special characters in fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_with_emoji_content_accepted(client: AsyncClient, api_key_fixture: Any) -> None:
    """4-byte emoji must round-trip through the chat endpoint without crashing."""
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "Hello 🚀🎉🌟 world"}],
        },
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200, resp.text[:300]


@pytest.mark.asyncio
async def test_chat_with_rtl_marker_accepted(client: AsyncClient, api_key_fixture: Any) -> None:
    """RTL override character (U+202E) must not crash the parser."""
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "Hello‮ reversed text"}],
        },
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200, resp.text[:300]


@pytest.mark.asyncio
async def test_chat_with_null_byte_in_content_accepted_or_400(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    """``\\x00`` in JSON-string content — pydantic accepts; should NOT 500."""
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "abc\x00def"}],
        },
        headers=api_key_fixture.auth_header,
    )
    # NUL byte is unusual but valid JSON string content.  Either 200 (passed
    # through) or 400 (rejected by validator) — never 500.
    assert resp.status_code in (200, 400), resp.text[:300]


# ---------------------------------------------------------------------------
# L.6 Boundary values — max_tokens / messages / numeric edges
# ---------------------------------------------------------------------------


def test_chat_max_tokens_at_upper_limit_accepted() -> None:
    """``max_tokens=131072`` (catalogue ceiling) must be accepted by Pydantic."""
    body = ChatCompletionRequestBody(
        model="gpt-5.4-mini",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=131_072,
    )
    assert body.max_tokens == 131_072


def test_chat_max_tokens_above_limit_rejected() -> None:
    """``max_tokens=131073`` rejected (Field le=131_072)."""
    with pytest.raises(Exception):
        ChatCompletionRequestBody(
            model="gpt-5.4-mini",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=131_073,
        )


def test_chat_max_tokens_zero_rejected() -> None:
    """``max_tokens=0`` rejected (Field ge=1)."""
    with pytest.raises(Exception):
        ChatCompletionRequestBody(
            model="gpt-5.4-mini",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=0,
        )


def test_chat_max_tokens_negative_rejected() -> None:
    with pytest.raises(Exception):
        ChatCompletionRequestBody(
            model="gpt-5.4-mini",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=-1,
        )


def test_chat_temperature_boundary_two_accepted() -> None:
    """``temperature=2.0`` exactly the upper bound is accepted."""
    body = ChatCompletionRequestBody(
        model="gpt-5.4-mini",
        messages=[{"role": "user", "content": "hi"}],
        temperature=2.0,
    )
    assert body.temperature == 2.0


def test_chat_top_p_boundary_zero_and_one_accepted() -> None:
    for p in (0.0, 1.0):
        body = ChatCompletionRequestBody(
            model="gpt-5.4-mini",
            messages=[{"role": "user", "content": "hi"}],
            top_p=p,
        )
        assert body.top_p == p


# ---------------------------------------------------------------------------
# L.7 Auth — JWT manipulation
# ---------------------------------------------------------------------------


def test_jwt_access_with_wrong_audience_rejected() -> None:
    """JWT signed with our secret but ``aud`` set to a different service must be rejected."""
    import jwt as pyjwt

    from voltari_gateway.auth.session import verify_access_token
    from voltari_gateway.config import get_settings

    secret = get_settings().jwt_secret.get_secret_value()
    forged = pyjwt.encode(
        {
            "iss": "voltari-gateway",
            "aud": "voltari-OTHER-SERVICE",  # wrong audience
            "sub": str(uuid.uuid4()),
            "type": "access",
            "account_id": str(uuid.uuid4()),
            "iat": 1,
            "exp": 2_000_000_000,
        },
        secret,
        algorithm="HS256",
    )
    assert verify_access_token(forged) is None


def test_jwt_access_with_wrong_issuer_rejected() -> None:
    """JWT with foreign ``iss`` claim must be rejected."""
    import jwt as pyjwt

    from voltari_gateway.auth.session import verify_access_token
    from voltari_gateway.config import get_settings

    secret = get_settings().jwt_secret.get_secret_value()
    forged = pyjwt.encode(
        {
            "iss": "OTHER-GATEWAY",  # wrong issuer
            "aud": "voltari-management",
            "sub": str(uuid.uuid4()),
            "type": "access",
            "account_id": str(uuid.uuid4()),
            "iat": 1,
            "exp": 2_000_000_000,
        },
        secret,
        algorithm="HS256",
    )
    assert verify_access_token(forged) is None


def test_jwt_access_with_swapped_type_rejected() -> None:
    """A refresh-token JWT must NOT be accepted by ``verify_access_token``.

    Defends against an attacker stealing a refresh token (longer-lived,
    less-protected SameSite=Lax cookie) and trying to use it as an access
    token to bypass the per-request CSRF/auth.
    """
    import jwt as pyjwt

    from voltari_gateway.auth.session import verify_access_token
    from voltari_gateway.config import get_settings

    secret = get_settings().jwt_secret.get_secret_value()
    refresh_jwt_used_as_access = pyjwt.encode(
        {
            "iss": "voltari-gateway",
            "aud": "voltari-management",
            "sub": str(uuid.uuid4()),
            "type": "refresh",  # ← refresh, not access
            "jti": "x",
            "iat": 1,
            "exp": 2_000_000_000,
        },
        secret,
        algorithm="HS256",
    )
    assert verify_access_token(refresh_jwt_used_as_access) is None


def test_jwt_access_with_no_signature_rejected() -> None:
    """``alg=none`` attack: unsigned JWT must be rejected.

    Classic JWT vulnerability: some libraries accept ``{"alg": "none"}``
    which lets an attacker mint any payload. PyJWT closes this when you
    explicitly pass ``algorithms=[settings.jwt_algorithm]``.
    """
    import base64
    import json as _json

    from voltari_gateway.auth.session import verify_access_token

    header = base64.urlsafe_b64encode(_json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(
        b"="
    )
    payload = base64.urlsafe_b64encode(
        _json.dumps(
            {
                "iss": "voltari-gateway",
                "aud": "voltari-management",
                "sub": str(uuid.uuid4()),
                "type": "access",
                "iat": 1,
                "exp": 2_000_000_000,
            }
        ).encode()
    ).rstrip(b"=")
    forged = (header + b"." + payload + b".").decode()
    assert verify_access_token(forged) is None


def test_jwt_access_with_wrong_secret_rejected() -> None:
    """JWT signed with a different secret must be rejected."""
    import jwt as pyjwt

    from voltari_gateway.auth.session import verify_access_token

    forged = pyjwt.encode(
        {
            "iss": "voltari-gateway",
            "aud": "voltari-management",
            "sub": str(uuid.uuid4()),
            "type": "access",
            "iat": 1,
            "exp": 2_000_000_000,
        },
        "WRONG-SECRET-VALUE-32-CHARS-FOR-VALIDITY",
        algorithm="HS256",
    )
    assert verify_access_token(forged) is None


# ---------------------------------------------------------------------------
# L.8 Webhook signature spoofing
# ---------------------------------------------------------------------------


def test_yookassa_webhook_signature_with_no_header_rejected() -> None:
    """No signature header → fail."""
    from voltari_gateway.billing.yookassa import verify_webhook_signature

    assert verify_webhook_signature(b"body", None, "secret") is False


def test_yookassa_webhook_signature_with_empty_header_rejected() -> None:
    from voltari_gateway.billing.yookassa import verify_webhook_signature

    assert verify_webhook_signature(b"body", "", "secret") is False


def test_yookassa_webhook_signature_with_no_secret_rejected() -> None:
    """Empty secret → fail (defence against misconfiguration)."""
    from voltari_gateway.billing.yookassa import verify_webhook_signature

    assert verify_webhook_signature(b"body", "sha256=abc", "") is False


def test_yookassa_webhook_signature_with_unknown_algo_rejected() -> None:
    """Unknown ``algo=`` prefix (md5, etc.) → fail."""
    from voltari_gateway.billing.yookassa import verify_webhook_signature

    assert verify_webhook_signature(b"body", "md5=abc", "secret") is False
    assert verify_webhook_signature(b"body", "abc", "secret") is False  # no = at all


def test_yookassa_webhook_signature_correct_sha256_passes() -> None:
    """Correctly-computed sha256 must pass."""
    import hashlib
    import hmac as _hmac

    from voltari_gateway.billing.yookassa import verify_webhook_signature

    secret = "test-secret"
    body = b'{"hello":"world"}'
    expected = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, f"sha256={expected}", secret) is True


def test_yookassa_webhook_signature_off_by_one_byte_rejected() -> None:
    """Constant-time compare: flipping any byte → reject."""
    import hashlib
    import hmac as _hmac

    from voltari_gateway.billing.yookassa import verify_webhook_signature

    secret = "test-secret"
    body = b'{"hello":"world"}'
    correct = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # Flip last char.
    flipped = correct[:-1] + ("0" if correct[-1] != "0" else "1")
    assert verify_webhook_signature(body, f"sha256={flipped}", secret) is False
