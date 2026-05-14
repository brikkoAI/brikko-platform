"""Scraper service configuration — env-loaded singleton."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScraperSettings(BaseSettings):
    """Top-level config for the scraper service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Bind ---
    host: str = Field(default="0.0.0.0", alias="SCRAPER_HOST")
    port: int = Field(default=9100, alias="SCRAPER_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Auth ---
    # Shared secret between gateway and scraper.  Empty value fails boot —
    # the service has no public-internet exposure but defence in depth.
    internal_token: SecretStr = Field(default=SecretStr(""), alias="SCRAPER_INTERNAL_TOKEN")

    # --- Crypto ---
    # Fernet key shared with the gateway.  Used to decrypt cookie blobs
    # before injecting into the Playwright browser context.  Empty value
    # disables decryption — if anyone uploads a cookie blob the scrape
    # endpoint returns 503 instead of leaking plaintext failure.
    encryption_key: SecretStr = Field(default=SecretStr(""), alias="ENCRYPTION_KEY")

    # --- Storage ---
    cookies_dir: Path = Field(
        default=Path("/encrypted-cookies"),
        alias="COOKIES_VOLUME_PATH",
    )

    # --- Playwright ---
    headless: bool = Field(default=True, alias="PLAYWRIGHT_HEADLESS")
    # 2026-05-12: OpenAI / Anthropic / Together блочат РФ-IP на уровне CF.
    # Scraper-контейнер на Reg.ru должен ходить через Aeza outbound proxy
    # (FI IP) — тот же что для chat/completions LLM-запросов.
    # Если переменная не задана — direct (для dev/локальной разработки).
    outbound_proxy: str = Field(default="", alias="OUTBOUND_HTTP_PROXY")
    nav_timeout_ms: int = Field(default=30_000, alias="PLAYWRIGHT_NAV_TIMEOUT_MS")
    viewport_width: int = Field(default=1280, alias="PLAYWRIGHT_VIEWPORT_WIDTH")
    viewport_height: int = Field(default=720, alias="PLAYWRIGHT_VIEWPORT_HEIGHT")
    user_agent: str = Field(
        # Match a recent stable chromium; some dashboards bot-detect headless UA.
        default=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        alias="PLAYWRIGHT_USER_AGENT",
    )

    @field_validator("internal_token", mode="after")
    @classmethod
    def _require_token(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if not raw or len(raw) < 16:
            raise ValueError(
                "SCRAPER_INTERNAL_TOKEN must be set and at least 16 chars. "
                "Generate via: python -c 'import secrets;print(secrets.token_urlsafe(32))'"
            )
        return v


@lru_cache(maxsize=1)
def get_settings() -> ScraperSettings:
    return ScraperSettings()
