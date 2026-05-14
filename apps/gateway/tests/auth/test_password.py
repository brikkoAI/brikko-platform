"""Argon2id password hashing — happy path + mismatch."""

from __future__ import annotations

import pytest

from voltari_gateway.auth.password import (
    MAX_PASSWORD_LEN,
    hash_password,
    verify_password,
)


def test_hash_and_verify_round_trip():
    plain = "correct horse battery staple"
    hashed = hash_password(plain)

    # argon2 hashes are PHC-encoded — start with $argon2id$
    assert hashed.startswith("$argon2id$")
    # Salt is random, two hashes of the same plaintext must differ.
    assert hash_password(plain) != hashed
    # ...but both verify.
    assert verify_password(plain, hashed) is True


def test_verify_rejects_wrong_password_and_garbage():
    hashed = hash_password("correct horse battery staple")

    # Wrong password
    assert verify_password("Tr0ub4dor&3", hashed) is False
    # Empty plaintext
    assert verify_password("", hashed) is False
    # Garbage hash format does not raise
    assert verify_password("anything", "not-a-real-hash") is False
    # None of the boundary inputs raise — all collapse to False.


def test_hash_rejects_too_short_or_too_long():
    with pytest.raises(ValueError):
        hash_password("short")  # < 8

    with pytest.raises(ValueError):
        hash_password("x" * (MAX_PASSWORD_LEN + 1))
