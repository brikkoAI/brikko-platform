"""Inspect running ``claude`` processes on this PC.

Two consumers:

1. ``GET /processes`` endpoint (bot's ``/status`` shows CLI-claude activity)
2. The CLI-vs-bridge mutex: before ``claude_runner`` spawns a new
   ``claude --resume <id>``, it checks whether an interactive Claude is
   already running in the same project folder. Both writing into the
   same ``~/.claude/projects/<encoded>/<uuid>.jsonl`` corrupts it.

Classification by command line:

* ``daemon-spawned`` — has BOTH ``--print`` and ``--resume`` (matches
  ``daemon.claude_runner``'s argv exactly).
* ``cli`` — anything else, including the interactive REPL the user
  launched from a terminal / IDE.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import psutil

log = logging.getLogger(__name__)


# Both Windows (claude.exe) and Linux/macOS (claude) variants
_CLAUDE_NAMES = {"claude", "claude.exe"}


@dataclass(frozen=True)
class ClaudeProcInfo:
    pid: int
    ppid: int
    cwd: str | None
    started_at: float  # unix seconds
    kind: str          # 'cli' | 'daemon-spawned'
    cmdline: tuple[str, ...]


def _is_claude(p: psutil.Process) -> bool:
    try:
        name = (p.info.get("name") or "").lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return name in _CLAUDE_NAMES


def _classify(cmdline: list[str] | None) -> str:
    if not cmdline:
        return "cli"
    args = set(cmdline)
    if "--print" in args and "--resume" in args:
        return "daemon-spawned"
    return "cli"


def _norm(path: str | None) -> str:
    """Lower-case, forward-slash, no trailing slash. Comparable across cases."""
    if not path:
        return ""
    return path.replace("\\", "/").rstrip("/").lower()


def list_claude_processes() -> list[ClaudeProcInfo]:
    """Snapshot of all claude.exe processes the daemon can see."""
    out: list[ClaudeProcInfo] = []
    attrs = ["pid", "ppid", "name", "cmdline", "cwd", "create_time"]
    for p in psutil.process_iter(attrs=attrs):
        if not _is_claude(p):
            continue
        try:
            info = p.info
            out.append(
                ClaudeProcInfo(
                    pid=int(info.get("pid") or 0),
                    ppid=int(info.get("ppid") or 0),
                    cwd=info.get("cwd"),
                    started_at=float(info.get("create_time") or 0.0),
                    kind=_classify(info.get("cmdline")),
                    cmdline=tuple(info.get("cmdline") or ()),
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Process vanished mid-iteration, or we don't have rights to
            # inspect it. Skip; it's not actionable.
            continue
        except Exception as e:
            # Defensive — never let a single bad process kill the scan.
            log.debug("process_iter error pid=%s: %s", info.get("pid"), e)
            continue
    return out


def find_cli_processes_for_cwd(target_cwd: str) -> list[ClaudeProcInfo]:
    """CLI-claude processes whose cwd matches ``target_cwd``.

    Used by the mutex: if any are returned, ``claude_runner`` refuses
    to spawn so the user doesn't end up with two procs writing to the
    same session jsonl.

    Match is case-insensitive and ignores trailing separators. We don't
    walk parent directories — exact cwd match only — because Claude only
    reads sessions from its launch cwd.
    """
    target = _norm(target_cwd)
    if not target:
        return []
    return [
        p for p in list_claude_processes()
        if p.kind == "cli" and _norm(p.cwd) == target
    ]
