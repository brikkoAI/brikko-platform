"""Bridge supervisor — runs daemon + SSH reverse tunnel together,
restarts on failure.

Replaces autossh + systemd (which we don't have on Windows). Good enough
for an MVP personal-use install. For production deployment we'd wrap this
as a Windows Service via NSSM.

Layout::

    [Aeza bot] --HTTP--> [Aeza :8090] <--SSH reverse tunnel--> [CEO PC :9090] --HTTP--> [daemon]

Run::

    python -m daemon.supervisor

Stop with Ctrl+C — supervisor terminates both children gracefully.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from daemon.config import get_settings


LOG_DIR = Path.home() / ".brikko-bridge"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "supervisor.log"

# Where the bridge code is. Defaults to the directory of this file's parent.
BRIDGE_ROOT = Path(__file__).resolve().parent.parent


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    sys.stderr.write(line + "\n")
    sys.stderr.flush()
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _spawn_daemon() -> subprocess.Popen:
    """Start the FastAPI daemon as a child process."""
    settings = get_settings()
    env = os.environ.copy()
    # Make sure the daemon doesn't accidentally bind something other than loopback,
    # even if env was tampered with.
    env["BRIDGE_DAEMON_LISTEN_HOST"] = "127.0.0.1"
    return subprocess.Popen(
        [sys.executable, "-m", "daemon.main"],
        cwd=str(BRIDGE_ROOT),
        env=env,
    )


def _spawn_tunnel(ssh_key_path: Path) -> subprocess.Popen:
    """Start the SSH reverse tunnel as a child process."""
    settings = get_settings()
    args = [
        "ssh",
        "-N",  # do not execute remote command
        "-R",
        f"{settings.tunnel_remote_port}:127.0.0.1:{settings.listen_port}",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-i",
        str(ssh_key_path),
        f"{settings.tunnel_ssh_user}@{settings.tunnel_remote_host}",
    ]
    return subprocess.Popen(args)


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    except OSError:
        pass


def _ensure_auth_token_and_print_deep_link() -> None:
    """On startup, decide whether to print a pairing deep-link.

    Three cases:
      1. **Already paired** (auth_tokens.json has a consumed_by_chat_id) —
         print a short reminder, no link. The bot's SQLite whitelist
         remembers the chat_id; a fresh token would just be noise.
      2. **Unconsumed token waiting** — print the existing deep-link.
         Avoids new-link-on-every-restart spam if the user is
         mid-onboarding.
      3. **Nothing yet** — generate a token and print the link.
    """
    from daemon import auth_tokens
    from daemon.config import get_settings

    settings = get_settings()
    bot_username = os.environ.get("BRIDGE_BOT_USERNAME", "brikko_agent_bot")

    paired = auth_tokens.previously_paired_chat_ids()
    if paired:
        ids = ", ".join(str(x) for x in paired)
        _log(
            "\n========================================================\n"
            f"  Brikko Bridge ready. Paired with chat_id(s): {ids}\n"
            "  (open Telegram and start typing — the bot is on)\n"
            "========================================================\n"
        )
        return

    token = auth_tokens.get_unconsumed_token() or auth_tokens.create_token()
    deep_link = f"https://t.me/{bot_username}?start={token}"
    banner = (
        "\n"
        "==================================================================\n"
        "  Brikko Bridge — open this link on your phone to pair Telegram:\n"
        f"  {deep_link}\n"
        "  (token is valid for 24 hours, then a new one is generated)\n"
        "==================================================================\n"
    )
    _log(banner)


def run(ssh_key_path: Path | None = None) -> int:
    """Main loop. Returns exit code (0 normal shutdown, !=0 fatal error)."""
    if ssh_key_path is None:
        ssh_key_path = Path.home() / ".ssh" / "brikko_aeza"
    if not ssh_key_path.exists():
        _log(f"FATAL: ssh key not found at {ssh_key_path}")
        return 2

    _log(f"supervisor start (key={ssh_key_path}, bridge_root={BRIDGE_ROOT})")
    _ensure_auth_token_and_print_deep_link()
    daemon: subprocess.Popen | None = None
    tunnel: subprocess.Popen | None = None

    # Brief backoff to avoid restart storm if a child crashes immediately
    daemon_backoff = 1.0
    tunnel_backoff = 1.0
    MAX_BACKOFF = 30.0

    def shutdown(signum, _frame):
        _log(f"received signal {signum}, shutting down")
        _terminate(daemon)
        _terminate(tunnel)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            if daemon is None or daemon.poll() is not None:
                if daemon is not None:
                    _log(f"daemon exited code={daemon.returncode}, sleeping {daemon_backoff:.1f}s")
                    time.sleep(daemon_backoff)
                    daemon_backoff = min(daemon_backoff * 2, MAX_BACKOFF)
                else:
                    daemon_backoff = 1.0
                _log("starting daemon")
                daemon = _spawn_daemon()

            if tunnel is None or tunnel.poll() is not None:
                if tunnel is not None:
                    _log(f"tunnel exited code={tunnel.returncode}, sleeping {tunnel_backoff:.1f}s")
                    time.sleep(tunnel_backoff)
                    tunnel_backoff = min(tunnel_backoff * 2, MAX_BACKOFF)
                else:
                    tunnel_backoff = 1.0
                _log("starting ssh reverse tunnel")
                tunnel = _spawn_tunnel(ssh_key_path)

            # Reset backoff on healthy run for >30s
            time.sleep(2)
            if daemon and daemon.poll() is None:
                daemon_backoff = max(1.0, daemon_backoff * 0.9)
            if tunnel and tunnel.poll() is None:
                tunnel_backoff = max(1.0, tunnel_backoff * 0.9)

    except KeyboardInterrupt:
        _log("KeyboardInterrupt, shutting down")
    finally:
        _terminate(daemon)
        _terminate(tunnel)
    return 0


if __name__ == "__main__":
    sys.exit(run())
