"""Bot settings (BRIDGE_BOT_* env vars)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    bot_username: str = "brikko_agent_bot"

    # Daemon URL — fixed for single-tenant (CEO's PC via SSH reverse tunnel).
    # On Aeza this resolves to 127.0.0.1:8090, which gets forwarded to CEO PC :9090.
    daemon_url: str = "http://127.0.0.1:8090"

    db_path: str = "/data/bridge.db"

    # Telegram message limit
    telegram_msg_max: int = 4096

    # ----------------------------------------------------------------
    # Phase 8 — approval flow Redis link
    # ----------------------------------------------------------------

    # Bot connects to Aeza Redis directly (same machine, loopback).
    # Daemon connects to the same Redis via SSH local-forward.
    redis_url: str = "redis://127.0.0.1:6379/0"

    # TTL (seconds) on the bridge:approval-response:<id> list key. Set short
    # so a forgotten response can't accumulate forever. Must comfortably
    # exceed the daemon's approval_timeout_seconds + clock-skew margin.
    approval_response_ttl_seconds: int = 600

    # ----------------------------------------------------------------
    # Part A — CLI mirror coalescing
    # ----------------------------------------------------------------

    # How long the CLI-mirror renderer waits between text deltas before
    # flushing the buffer to Telegram. Higher = fewer messages but more
    # latency. 700ms = ~1 message per Claude "sentence" empirically.
    cli_mirror_coalesce_ms: int = 700

    model_config = SettingsConfigDict(env_prefix="BRIDGE_BOT_")


def get_settings() -> Settings:
    return Settings()
