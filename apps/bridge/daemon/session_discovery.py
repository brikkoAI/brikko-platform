"""Discover existing Claude Code sessions on this PC.

Claude Code stores per-project sessions at::

    ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl

Each ``.jsonl`` file is one Claude conversation. The first lines often
contain a ``summary`` event we use as a one-line preview for the bot.

This module is **read-only** — never modifies session files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict


class SessionInfo(TypedDict):
    session_id: str         # filename stem (UUID)
    project_path: str       # parent dir name (encoded — used only as label)
    cwd: str | None         # real working directory extracted from jsonl
    last_modified: float    # mtime, for sorting "most recent first"
    summary: str | None     # first 'summary' string, or first user-message
                            # text as fallback (some sessions never get one)


def _claude_home() -> Path:
    """Where ~/.claude lives. Overridable for tests via monkeypatch."""
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or "~").expanduser() / ".claude"


def list_sessions() -> list[SessionInfo]:
    """Scan ~/.claude/projects/*/<uuid>.jsonl, return sorted by mtime DESC.

    Empty list if ~/.claude/projects/ doesn't exist (fresh installation).
    """
    projects_dir = _claude_home() / "projects"
    if not projects_dir.exists() or not projects_dir.is_dir():
        return []

    sessions: list[SessionInfo] = []
    for project in projects_dir.iterdir():
        if not project.is_dir():
            continue  # stray files in projects/ are ignored
        for session_file in project.glob("*.jsonl"):
            try:
                mtime = session_file.stat().st_mtime
            except OSError:
                continue
            cwd, summary = _extract_meta(session_file)
            sessions.append(
                SessionInfo(
                    session_id=session_file.stem,
                    project_path=project.name,
                    cwd=cwd,
                    last_modified=mtime,
                    summary=summary,
                )
            )

    sessions.sort(key=lambda s: s["last_modified"], reverse=True)
    return sessions


def _extract_meta(path: Path) -> tuple[str | None, str | None]:
    """Walk the jsonl once and pull out the two pieces we care about:

    1. ``cwd`` — the real working directory Claude used. Required so
       ``claude --resume <id>`` finds the session jsonl (it looks under
       ``~/.claude/projects/<encoded-cwd>/<id>.jsonl``).
    2. ``summary`` — a one-line preview. Modern sessions often don't ship a
       'summary' field; fall back to the first user-message text so the bot's
       /sessions list isn't all "(без описания)".

    Both are best-effort. Returns ``(None, None)`` on unreadable files.
    """
    cwd: str | None = None
    summary: str | None = None
    first_user_text: str | None = None

    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if cwd and (summary or first_user_text):
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if cwd is None:
                    c = obj.get("cwd")
                    if isinstance(c, str) and c:
                        cwd = c

                if summary is None:
                    s = obj.get("summary")
                    if isinstance(s, str) and s:
                        summary = s[:200]

                if first_user_text is None and obj.get("type") == "user":
                    first_user_text = _user_text(obj)
    except OSError:
        return None, None

    return cwd, (summary or (first_user_text[:200] if first_user_text else None))


def _user_text(obj: dict) -> str | None:
    """Pull plain text out of a user message, regardless of content shape.

    Claude stores ``message.content`` either as a string OR a list of content
    blocks (``{type: "text", text: "..."}`` etc.). Skip continuation blurbs
    starting with "This session is being continued" — those are auto-injected
    summaries from compact, not the user's actual prompt.
    """
    msg = obj.get("message") or {}
    content = msg.get("content")
    text: str | None = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    text = t
                    break
    if not text:
        return None
    if text.startswith("This session is being continued"):
        return None
    return text.strip()
