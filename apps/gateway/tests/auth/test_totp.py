"""Unit tests for ``voltari_gateway.auth.totp``."""

from __future__ import annotations

import pyotp
import pytest

from voltari_gateway.auth.totp import (
    RECOVERY_CODE_COUNT,
    build_provisioning_uri,
    decrypt_secret,
    encrypt_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    is_code_replay,
    mark_code_used,
    normalize_recovery_code,
    verify_recovery_code,
    verify_totp,
)


def test_generate_totp_secret_is_base32_32_chars() -> None:
    s = generate_totp_secret()
    assert isinstance(s, str)
    assert len(s) == 32
    # base32 alphabet
    for ch in s:
        assert ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def test_encrypt_decrypt_roundtrip() -> None:
    secret = generate_totp_secret()
    blob = encrypt_secret(secret)
    assert isinstance(blob, bytes)
    assert blob != secret.encode("utf-8")
    assert decrypt_secret(blob) == secret


def test_encrypt_produces_different_blobs_for_same_secret() -> None:
    """Fernet uses a random IV — same plaintext → different ciphertext."""
    secret = generate_totp_secret()
    blob_a = encrypt_secret(secret)
    blob_b = encrypt_secret(secret)
    assert blob_a != blob_b
    assert decrypt_secret(blob_a) == decrypt_secret(blob_b) == secret


def test_provisioning_uri_format() -> None:
    secret = "JBSWY3DPEHPK3PXP"
    uri = build_provisioning_uri(secret, account_email="alice@example.com", issuer="Brikko")
    assert uri.startswith("otpauth://totp/Brikko:")
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    assert "issuer=Brikko" in uri


def test_verify_totp_accepts_current_code() -> None:
    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert verify_totp(secret, code) is True


def test_verify_totp_rejects_wrong_code() -> None:
    secret = generate_totp_secret()
    assert verify_totp(secret, "000000") is False


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 345", "12345 "])
def test_verify_totp_rejects_malformed_input(bad: str) -> None:
    secret = generate_totp_secret()
    assert verify_totp(secret, bad) is False


@pytest.mark.asyncio
async def test_redis_replay_protection(redis_client) -> None:
    user_id = "u-1"
    code = "123456"
    assert await is_code_replay(redis_client, user_id, code) is False
    await mark_code_used(redis_client, user_id, code)
    assert await is_code_replay(redis_client, user_id, code) is True
    # Different user not affected.
    assert await is_code_replay(redis_client, "u-2", code) is False


@pytest.mark.asyncio
async def test_replay_protection_no_redis_returns_false() -> None:
    """When Redis is unavailable we conservatively allow — fails open
    on observability, but the totp code itself is still required."""
    assert await is_code_replay(None, "u", "123456") is False
    await mark_code_used(None, "u", "123456")  # must not raise


def test_generate_recovery_codes_default_count() -> None:
    codes = generate_recovery_codes()
    assert len(codes) == RECOVERY_CODE_COUNT
    # Format XXXX-XXXX-XXXX
    for c in codes:
        parts = c.split("-")
        assert len(parts) == 3
        assert all(len(p) == 4 for p in parts)
    # All distinct
    assert len(set(codes)) == len(codes)


def test_normalize_recovery_code() -> None:
    assert normalize_recovery_code("ABCD-EFGH-1234") == "abcdefgh1234"
    assert normalize_recovery_code(" ab cd  ") == "abcd"
    assert normalize_recovery_code("ABCDEFGH1234") == "abcdefgh1234"


def test_hash_recovery_code_is_deterministic() -> None:
    h1 = hash_recovery_code("abcd-efgh-1234")
    h2 = hash_recovery_code("ABCD-EFGH-1234")  # case-insensitive
    h3 = hash_recovery_code("abcdefgh1234")  # separators ignored
    assert h1 == h2 == h3


def test_verify_recovery_code_match_and_miss() -> None:
    codes = generate_recovery_codes()
    hashes = [hash_recovery_code(c) for c in codes]
    # Any code matches its hash
    assert verify_recovery_code(codes[0], hashes) == hashes[0]
    assert verify_recovery_code(codes[5], hashes) == hashes[5]
    # Wrong code returns None
    assert verify_recovery_code("0000-0000-0000", hashes) is None
