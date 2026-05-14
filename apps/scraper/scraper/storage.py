"""Encrypted cookie storage on shared volume.

File layout: ``${COOKIES_VOLUME_PATH}/{provider}.enc`` — one file per provider.

Why files (not Postgres):
* Volume already present (mounted into scraper container at /encrypted-cookies).
* Filesystem ACLs (chmod 600) provide an extra defence layer if image leaks.
* No round-trip to the gateway DB from inside the scraper — keeps the scraper
  side stateless and easy to redeploy independently.
* Atomic writes via os.replace() — no half-written files mid-rotation.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path

# Only these provider keys are accepted — defence in depth (path traversal
# prevention).  Mirror of gateway's ``KNOWN_PROVIDERS`` for the providers
# that actually use scrape adapter.
_ALLOWED_PROVIDERS = frozenset({"openai", "anthropic", "together"})
_PROVIDER_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _validate_provider(provider: str) -> None:
    if not _PROVIDER_KEY_RE.match(provider):
        raise ValueError(f"invalid provider key: {provider!r}")
    if provider not in _ALLOWED_PROVIDERS:
        raise ValueError(
            f"provider {provider!r} not supported by scraper. Allowed: {sorted(_ALLOWED_PROVIDERS)}"
        )


class CookieStore:
    """Filesystem-backed cookie blob store.

    Thread-safety: ``store()`` uses os.replace() which is atomic on POSIX
    and modern Windows NTFS.  Concurrent reads see either the old blob or
    the new one — never a partial write.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = Path(base_dir)
        # Don't create the dir here — let docker-compose volume mount own
        # the directory.  We only enforce that it exists.
        if not self._base.exists():
            try:
                self._base.mkdir(parents=True, exist_ok=True)
            except PermissionError as exc:
                raise RuntimeError(f"cookies volume {self._base} is not writable: {exc}") from exc

    def path_for(self, provider: str) -> Path:
        _validate_provider(provider)
        return self._base / f"{provider}.enc"

    def exists(self, provider: str) -> bool:
        return self.path_for(provider).is_file()

    def store(self, provider: str, ciphertext: bytes) -> int:
        """Write an encrypted blob atomically. Returns its size in bytes."""
        target = self.path_for(provider)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file then atomic rename.
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{provider}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(ciphertext)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, target)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
        # chmod 600 — owner-only.  Skip on Windows where chmod is mostly a no-op.
        with contextlib.suppress(OSError):
            os.chmod(target, 0o600)
        return len(ciphertext)

    def load(self, provider: str) -> bytes:
        """Return the raw encrypted blob.

        Raises:
            FileNotFoundError: no cookies uploaded for this provider yet.
        """
        path = self.path_for(provider)
        return path.read_bytes()
