"""OAuth scope enum + parsing helpers.

Read scopes only for pre-M0. Write scopes (`*.write`, `tools.write`) land
in the M2 plan along with Brikko Studio tool-call support.

Scope strings come from the wire as either space- or plus-separated
(some clients URL-encode the space as ``+``). We accept both.
"""

from __future__ import annotations

import enum


class OAuthScope(enum.StrEnum):
    CHAT_READ = "chat.read"
    MESSAGES_READ = "messages.read"
    EMBEDDINGS_READ = "embeddings.read"
    AUDIO_READ = "audio.read"
    MODELS_READ = "models.read"


_BY_VALUE: dict[str, OAuthScope] = {s.value: s for s in OAuthScope}


def parse_scope_string(raw: str) -> list[OAuthScope]:
    """Parse a wire scope string. Unknown tokens are silently dropped.

    The strict policy lives in ``api/oauth.py``: the /authorize endpoint
    rejects unknown scopes with ``invalid_scope`` BEFORE storage so we
    never end up with an unresolved scope on a live token.
    """
    out: list[OAuthScope] = []
    seen: set[OAuthScope] = set()
    for token in raw.replace("+", " ").split():
        scope = _BY_VALUE.get(token)
        if scope is None or scope in seen:
            continue
        seen.add(scope)
        out.append(scope)
    return out


def serialize_scopes(scopes: list[OAuthScope]) -> str:
    """List → space-separated wire form (RFC 6749 §3.3 default)."""
    return " ".join(s.value for s in scopes)


def parse_strict(raw: str) -> list[OAuthScope]:
    """Like ``parse_scope_string`` but raises ``ValueError`` on unknown tokens.

    Used by /authorize to reject malformed scope requests with
    ``invalid_scope`` per RFC 6749 §4.1.2.1.
    """
    out: list[OAuthScope] = []
    seen: set[OAuthScope] = set()
    for token in raw.replace("+", " ").split():
        scope = _BY_VALUE.get(token)
        if scope is None:
            raise ValueError(f"unknown scope: {token}")
        if scope in seen:
            continue
        seen.add(scope)
        out.append(scope)
    return out
