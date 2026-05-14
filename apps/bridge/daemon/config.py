"""Daemon settings.

Read from env (`BRIDGE_DAEMON_*` prefix). The daemon listens **only on
127.0.0.1** — the SSH reverse tunnel forwards Aeza:8090 → here:9090.
There is intentionally no way to make this listen on a public IP — that
would expose `claude` execution to the world.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    listen_host: str = "127.0.0.1"
    listen_port: int = 9090
    claude_binary: str = "claude"
    bridge_version: str = "0.1.0"

    # Where Claude Code stores per-project session JSONL files.
    # Default for Windows: %USERPROFILE%\.claude\projects\
    sessions_dir: Path = Path.home() / ".claude" / "projects"

    # Reverse tunnel target (used by Day 2.3 supervisor, daemon doesn't need it).
    tunnel_remote_host: str = "185.125.101.83"
    tunnel_remote_port: int = 8090
    tunnel_ssh_user: str = "root"

    # ----------------------------------------------------------------
    # Phase 8 — per-tool approval flow (replaces --dangerously-skip-permissions)
    # ----------------------------------------------------------------

    # Redis connection. Daemon talks to Aeza Redis over an SSH local-forward
    # 127.0.0.1:6379 → Aeza:6379. Bot connects to the same Redis directly
    # on its loopback. See infra/two_server_setup.md for the tunnel.
    redis_url: str = "redis://127.0.0.1:6379/0"

    # How long the daemon waits for a Telegram button-tap before treating it
    # as a denial. CEO decision 2026-05-11 #1: 5 min = enough for phone-in-
    # pocket case, short enough to not pin the session forever.
    approval_timeout_seconds: int = 300

    # YOLO fallback mode. CEO decision 2026-05-11 #3: when Redis is
    # unreachable, the daemon falls back to allowing all tools and emits a
    # large red banner to Telegram. This is the OPPOSITE of the "fail-closed"
    # default the design doc proposed in §12.3 — CEO consciously opted for
    # availability over strictness for solo home use.
    #
    # Set this to false to flip back to fail-closed behaviour (deny all
    # gated tools when Redis is down). Useful if multi-user comes online.
    yolo_on_redis_down: bool = True

    # Hard-override switch for emergencies. When set, the daemon NEVER calls
    # the approval broker at all — every tool is allowed, and a one-shot
    # warning is logged on startup. This is the "delete-this-protection"
    # override CEO can flip at the OS env level to bypass Bridge entirely.
    #
    # Maps to the historical --dangerously-skip-permissions behaviour.
    yolo_mode: bool = False

    # When true, the legacy subprocess runner (claude --print
    # --dangerously-skip-permissions) is used instead of the SDK + callback
    # path. Kept for backwards compatibility with Day 1-7 tests and as a
    # last-ditch fallback if the SDK breaks on a CLI update. Not for prod use.
    legacy_subprocess_runner: bool = False

    # ----------------------------------------------------------------
    # Part A — CLI → Telegram mirror
    # ----------------------------------------------------------------

    # How often the jsonl tailer polls the session file for new bytes.
    # 200ms gives ~250ms perceived latency vs CLI stdout flush. Lower is
    # tighter but burns more CPU on idle sessions. Don't go below 50ms.
    follow_poll_interval_s: float = 0.2

    model_config = SettingsConfigDict(env_prefix="BRIDGE_DAEMON_")


def get_settings() -> Settings:
    return Settings()
