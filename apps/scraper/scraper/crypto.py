"""Cookie blob encryption helpers.

Cookies are stored on disk as Fernet-encrypted blobs.  Both the gateway and
the scraper share ``ENCRYPTION_KEY`` (Fernet 32-byte URL-safe base64).  The
gateway encrypts at upload, the scraper decrypts just before injecting into
the Playwright browser context.

Why Fernet (and not age/PGP):
* Already a project dep (cryptography>=46) — no new image weight.
* Symmetric, key-rotation-friendly via ``MultiFernet`` if we ever need it.
* AEAD (HMAC-SHA256 + AES-128-CBC) — sufficient for "cookies at rest".
* Forward-compatible with rotating ``ENCRYPTION_KEY_ID`` (gateway-side
  versioning column in ``provider_cookies`` if we ever do rotation; today
  we don't).
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class CookieCipher:
    """Wraps a single Fernet key.

    Constructed once at startup from ``settings.encryption_key`` — empty
    key raises ``ValueError`` so the service refuses to boot in a state
    where it cannot decrypt anything it could store.
    """

    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("ENCRYPTION_KEY is empty — refusing to start cookie cipher")
        try:
            self._f = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
        except Exception as exc:  # ValueError when key is malformed
            raise ValueError(f"ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._f.encrypt(plaintext)

    def decrypt(self, token: bytes) -> bytes:
        """Decrypt a previously-encrypted blob.

        Raises:
            InvalidToken: blob was tampered with, encrypted with a different
                          key, or is not a valid Fernet token.
        """
        try:
            return self._f.decrypt(token)
        except InvalidToken:
            raise
