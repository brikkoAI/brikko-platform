"""API-key generation and verification.

Format: ``sk-brk-<22-base62-chars>`` — 16 random bytes (128 bits) base64-url
encoded. ``prefix`` is the first 14 characters of the plaintext, kept in DB
for cheap candidate lookup before the expensive argon2 verify.

Why ``sk-`` prefix: matches OpenAI SDK convention so the same client libs
work without configuration tweaks. ``brk`` distinguishes Brikko-issued keys
from OpenAI ones at a glance (logs, screenshots, accidental leaks).

Legacy compat: old keys issued during the Voltari era (``sk-vlt-``) remain
valid — DB lookups go by prefix, so the row is found regardless of which
literal it uses. Only NEW keys get the new ``sk-brk-`` literal.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# OWASP-recommended params, balanced for our hot-path budget (~5–10 ms / verify).
_HASHER = PasswordHasher(time_cost=2, memory_cost=64 * 1024, parallelism=2)

KEY_PREFIX_LITERAL = "sk-brk-"
# Legacy literal — keys issued during the Voltari rebrand era. Still valid;
# only new keys ship with the current literal.
LEGACY_KEY_PREFIX_LITERALS = ("sk-vlt-",)
KEY_PREFIX_LEN = 14  # "sk-brk-aB12cd"


@dataclass(frozen=True)
class GeneratedKey:
    """The set of values returned when creating a new API key.

    The plaintext is shown to the user **once** at creation time and then
    discarded server-side. Only ``prefix`` and ``key_hash`` end up in the DB.
    """

    plaintext: str
    prefix: str
    key_hash: str


def generate_api_key() -> GeneratedKey:
    """Generate a fresh API key. Plaintext must never be persisted."""
    raw = secrets.token_bytes(16)
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    plaintext = f"{KEY_PREFIX_LITERAL}{body}"
    prefix = plaintext[:KEY_PREFIX_LEN]
    key_hash = _HASHER.hash(plaintext)
    return GeneratedKey(plaintext=plaintext, prefix=prefix, key_hash=key_hash)


def verify_api_key(plaintext: str, key_hash: str) -> bool:
    """Constant-time argon2 verify. False on mismatch, never raises."""
    try:
        return _HASHER.verify(key_hash, plaintext)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def extract_prefix(plaintext: str) -> str | None:
    """Return the 14-char DB lookup prefix, or None for malformed input.

    Accepts both the current literal (``sk-brk-``) and any legacy literals
    (currently ``sk-vlt-`` from the Voltari rebrand era). DB rows store the
    full 14-char prefix as it was at issuance, so lookup just works.
    """
    if not plaintext:
        return None
    if not (
        plaintext.startswith(KEY_PREFIX_LITERAL)
        or any(plaintext.startswith(legacy) for legacy in LEGACY_KEY_PREFIX_LITERALS)
    ):
        return None
    if len(plaintext) < KEY_PREFIX_LEN:
        return None
    return plaintext[:KEY_PREFIX_LEN]


# ---------------------------------------------------------------------------
# Brikko-MCP tokens (Sprint MCP S1)
# ---------------------------------------------------------------------------
#
# Same generation/verify logic as regular API keys — but with a distinct
# plaintext literal so MCP credentials are visually unmistakable in logs,
# screenshots, and screen-shared dashboards. Format: ``mcp-brk-<22 b62>``.
# Reuses the same ``_HASHER`` instance so we pay one argon2 init, not two.

MCP_TOKEN_PREFIX_LITERAL = "mcp-brk-"
MCP_TOKEN_PREFIX_LEN = 14  # "mcp-brk-aB12cd"


@dataclass(frozen=True)
class GeneratedMcpToken:
    """Mirror of ``GeneratedKey`` for the MCP surface.

    Plaintext is shown once at creation and discarded — only ``prefix`` and
    ``token_hash`` end up in the DB.
    """

    plaintext: str
    prefix: str
    token_hash: str


def generate_mcp_token() -> GeneratedMcpToken:
    """Generate a fresh MCP token. Plaintext must never be persisted."""
    raw = secrets.token_bytes(16)
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    plaintext = f"{MCP_TOKEN_PREFIX_LITERAL}{body}"
    prefix = plaintext[:MCP_TOKEN_PREFIX_LEN]
    token_hash = _HASHER.hash(plaintext)
    return GeneratedMcpToken(plaintext=plaintext, prefix=prefix, token_hash=token_hash)


def verify_mcp_token(plaintext: str, token_hash: str) -> bool:
    """Constant-time argon2 verify. False on mismatch, never raises."""
    try:
        return _HASHER.verify(token_hash, plaintext)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def extract_mcp_prefix(plaintext: str) -> str | None:
    """Return the 14-char DB lookup prefix, or None for malformed input."""
    if not plaintext:
        return None
    if not plaintext.startswith(MCP_TOKEN_PREFIX_LITERAL):
        return None
    if len(plaintext) < MCP_TOKEN_PREFIX_LEN:
        return None
    return plaintext[:MCP_TOKEN_PREFIX_LEN]
