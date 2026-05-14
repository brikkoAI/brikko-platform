"""One-time auth tokens. Stored in-memory + disk (so survives daemon restart
without forcing CEO to re-pair).

Flow:
  1. Daemon creates a token at startup (if no unconsumed token exists).
  2. Supervisor logs the deep-link `https://t.me/<bot>?start=<token>`.
  3. CEO taps the link on Android, opens the bot.
  4. Bot's /start handler extracts token, calls daemon
     `GET /auth/tokens/<token>?consume=1` over the SSH reverse tunnel.
  5. Daemon: if token valid + unconsumed → mark consumed, return 200.
     Bot then whitelists CEO's chat_id.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

TOKEN_TTL_SECONDS = 24 * 3600  # 24h — token can wait in deep-link until next morning


def _tokens_file() -> Path:
    base = Path.home() / ".brikko-bridge"
    base.mkdir(parents=True, exist_ok=True)
    return base / "auth_tokens.json"


def _load() -> list[dict]:
    f = _tokens_file()
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save(tokens: list[dict]) -> None:
    _tokens_file().write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def _prune_expired(tokens: list[dict]) -> list[dict]:
    now = int(time.time())
    return [t for t in tokens if t.get("expires_at", 0) > now]


def create_token() -> str:
    """Create a new unconsumed token. Returns the token string."""
    tokens = _prune_expired(_load())
    token = secrets.token_urlsafe(32)
    tokens.append(
        {
            "token": token,
            "created_at": int(time.time()),
            "expires_at": int(time.time()) + TOKEN_TTL_SECONDS,
            "consumed_at": None,
            "consumed_by_chat_id": None,
        }
    )
    _save(tokens)
    return token


def get_unconsumed_token() -> str | None:
    """If there's already an unconsumed, unexpired token, return it.
    Otherwise return None.

    This avoids spamming new tokens on every supervisor restart.
    """
    tokens = _prune_expired(_load())
    for t in tokens:
        if t.get("consumed_at") is None:
            return t["token"]
    return None


def consume_token(token: str, chat_id: int) -> bool:
    """Mark token as consumed by chat_id. Returns True if successful,
    False if token not found / already consumed / expired.
    """
    tokens = _prune_expired(_load())
    now = int(time.time())
    for t in tokens:
        if t["token"] == token:
            if t.get("consumed_at") is not None:
                return False
            t["consumed_at"] = now
            t["consumed_by_chat_id"] = chat_id
            _save(tokens)
            return True
    return False


def is_token_consumed_by(token: str, chat_id: int) -> bool:
    """Check if a token has been consumed by a specific chat_id.

    Used for re-auth (CEO re-opens bot on a new device — token still valid).
    """
    tokens = _load()
    for t in tokens:
        if t["token"] == token and t.get("consumed_by_chat_id") == chat_id:
            return True
    return False


def previously_paired_chat_ids() -> list[int]:
    """Return chat_ids that successfully consumed a token in the past.

    Used by the supervisor to skip the deep-link banner on subsequent
    runs — once paired, the chat_id sticks in the bot's whitelist DB,
    so a new token would only be needed if pairing got revoked.
    """
    out: list[int] = []
    for t in _load():
        cid = t.get("consumed_by_chat_id")
        if isinstance(cid, int) and cid not in out:
            out.append(cid)
    return out
