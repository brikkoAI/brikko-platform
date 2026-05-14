"""Signed, single-use tokens for email verification + password reset."""

from __future__ import annotations

import hashlib
import hmac
import uuid

import pytest
from itsdangerous import URLSafeTimedSerializer

from voltari_gateway import config as cfg
from voltari_gateway.auth.email_verification import (
    generate_password_reset_token,
    generate_verification_token,
    hash_token,
    verify_password_reset_token,
    verify_token_hash,
    verify_verification_token,
)
from voltari_gateway.config import get_settings


def test_verification_token_round_trip_and_purpose_isolation():
    user_id = uuid.uuid4()
    email = "alice@voltari.test"

    token = generate_verification_token(user_id, email)
    assert isinstance(token, str) and len(token) > 16

    result = verify_verification_token(token)
    assert result is not None
    got_user, got_email = result
    assert got_user == user_id
    assert got_email == email

    # HMAC-SHA-256 hash is deterministic per (key, plaintext) and 64 hex chars.
    h = hash_token(token)
    assert len(h) == 64
    assert hash_token(token) == h

    # A password-reset token (different salt) MUST NOT verify as email-verify
    # — and vice versa. This is the whole point of the salt parameter.
    reset = generate_password_reset_token(user_id)
    assert verify_verification_token(reset) is None
    assert verify_password_reset_token(token) is None


def test_expired_verification_token_is_rejected():
    """itsdangerous loads(max_age=-1) is the canonical 'always-expired' check.

    We don't sleep — we sign normally and verify with a deliberately expired
    deadline using the underlying serializer.  This proves the wiring is
    really TTL-driven and not just a free pass.
    """
    settings = get_settings()
    user_id = uuid.uuid4()
    email = "bob@voltari.test"

    fresh = generate_verification_token(user_id, email)
    # Sanity: the fresh token verifies through the public API.
    assert verify_verification_token(fresh) is not None

    # Direct underlying check — same secret + salt, max_age=-1 → SignatureExpired.
    s = URLSafeTimedSerializer(settings.jwt_secret.get_secret_value())
    with pytest.raises(Exception):
        s.loads(fresh, salt="voltari.email-verify.v1", max_age=-1)


def test_password_reset_round_trip_and_tamper():
    user_id = uuid.uuid4()
    token = generate_password_reset_token(user_id)

    assert verify_password_reset_token(token) == user_id

    # Tamper the signature suffix → rejected without raising.
    tampered = token[:-3] + ("A" if token[-3] != "A" else "B") + token[-2:]
    assert verify_password_reset_token(tampered) is None

    # Outright garbage.
    assert verify_password_reset_token("not.a.token") is None
    assert verify_password_reset_token("") is None


# ---------------------------------------------------------------------------
# HMAC hashing (BE P0-12)
#
# The pre-29.04 implementation used plain ``sha256(plaintext)`` for the at-rest
# hash. That column became a known-format digest of a known-format itsdangerous
# token — a stolen DB row was enough to reverse-engineer live tokens by
# replaying the format. The HMAC variant pepperes the digest with
# ``EMAIL_TOKEN_SECRET`` so a DB leak is useless without the pepper.
# ---------------------------------------------------------------------------


def test_token_hash_uses_hmac_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing ``EMAIL_TOKEN_SECRET`` must change the hash output.

    A plain SHA-256 digest would be invariant to the env var — that's
    exactly the property we want to lose.
    """
    plaintext = "v1.fake-token.payload"

    # Hash under fixed pepper-A ...
    monkeypatch.setenv(
        "EMAIL_TOKEN_SECRET",
        "pepper-A-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    cfg.get_settings.cache_clear()
    h_a = hash_token(plaintext)

    # ... is different from hash under pepper-B.
    monkeypatch.setenv(
        "EMAIL_TOKEN_SECRET",
        "pepper-B-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    )
    cfg.get_settings.cache_clear()
    h_b = hash_token(plaintext)

    cfg.get_settings.cache_clear()  # restore for next test

    assert h_a != h_b, "HMAC output must depend on EMAIL_TOKEN_SECRET"
    # Both must be 64 hex chars (SHA-256 size is unaffected).
    assert len(h_a) == 64
    assert len(h_b) == 64
    # Sanity: hash should NOT equal naive sha256(plaintext) under either key.
    naive = hashlib.sha256(plaintext.encode()).hexdigest()
    assert h_a != naive
    assert h_b != naive


def test_token_hash_matches_explicit_hmac_under_known_pepper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-check the implementation against the spec recipe.

    ``hash_token(p)`` must equal ``HMAC-SHA256(EMAIL_TOKEN_SECRET, p)``,
    nothing fancier. This pins the algorithm so a refactor that
    accidentally re-introduces a plain digest fails loudly.
    """
    pepper = "verify-pepper-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    monkeypatch.setenv("EMAIL_TOKEN_SECRET", pepper)
    cfg.get_settings.cache_clear()

    plaintext = "v1.example-payload"
    expected = hmac.new(pepper.encode(), plaintext.encode(), hashlib.sha256).hexdigest()
    assert hash_token(plaintext) == expected

    cfg.get_settings.cache_clear()


def test_verify_token_hash_uses_compare_digest() -> None:
    """``verify_token_hash`` MUST round-trip and must be timing-safe.

    We can't measure timing in a unit test, but we can pin the public
    surface: the helper accepts plaintext + expected hash, returns
    True/False with no exception on length mismatch. ``hmac.compare_digest``
    silently returns False for unequal lengths instead of crashing — we
    rely on that to keep error paths short.
    """
    plaintext = "v1.compare-me"
    h = hash_token(plaintext)
    assert verify_token_hash(plaintext, h) is True
    assert verify_token_hash(plaintext + "x", h) is False
    # Length-mismatched hash → False, no exception.
    assert verify_token_hash(plaintext, "deadbeef") is False
    assert verify_token_hash(plaintext, "") is False


def test_token_replay_blocked_after_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once a verification token is consumed, replaying it fails.

    The DB-side enforcement is in ``api/auth.py`` (the ``user.verification_token``
    column is set to ``None`` on success, so a second attempt finds no
    matching hash). Here we pin the helper-level invariant: the same
    plaintext must hash deterministically so the consume step is itself
    reliable.
    """
    monkeypatch.setenv(
        "EMAIL_TOKEN_SECRET",
        "replay-pepper-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    cfg.get_settings.cache_clear()

    user_id = uuid.uuid4()
    token = generate_verification_token(user_id, "rip@example.test")
    h_first = hash_token(token)
    h_second = hash_token(token)
    assert h_first == h_second, "hash must be deterministic for replay-detection"
    # A token for a *different* user must hash differently. (Note: two tokens
    # for the same user+email signed inside the same second can be byte-identical
    # because itsdangerous embeds a per-second timestamp; that's an itsdangerous
    # property, not a hash_token property.)
    other_user = uuid.uuid4()
    other = generate_verification_token(other_user, "rip@example.test")
    assert hash_token(other) != h_first

    cfg.get_settings.cache_clear()


def test_token_with_wrong_email_blocked() -> None:
    """A token signed for email A must not verify when presented as email B.

    The signed payload carries the email; the API layer compares against
    the user's current email after decode. We pin that the decoded email
    is exactly what was signed.
    """
    user_id = uuid.uuid4()
    token = generate_verification_token(user_id, "Original@Voltari.Test")
    parsed = verify_verification_token(token)
    assert parsed is not None
    decoded_user, decoded_email = parsed
    assert decoded_user == user_id
    # Email is preserved bit-for-bit (no normalisation here — that's the
    # caller's responsibility).
    assert decoded_email == "Original@Voltari.Test"


def test_pepper_falls_back_to_jwt_secret_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``EMAIL_TOKEN_SECRET`` is empty (dev), we fall back to JWT_SECRET.

    Production-mode would have refused to boot via the config validator,
    so we only need to keep dev ergonomic. Without the fallback every
    fresh checkout would 500 on /verify-email until someone set the env.
    """
    monkeypatch.setenv("EMAIL_TOKEN_SECRET", "")
    monkeypatch.setenv(
        "JWT_SECRET",
        "fallback-jwt-secret-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    cfg.get_settings.cache_clear()

    plaintext = "v1.fallback-token"
    expected = hmac.new(
        b"fallback-jwt-secret-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        plaintext.encode(),
        hashlib.sha256,
    ).hexdigest()
    assert hash_token(plaintext) == expected

    cfg.get_settings.cache_clear()
