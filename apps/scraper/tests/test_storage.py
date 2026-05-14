"""Tests for the encrypted cookie store."""

from __future__ import annotations

from pathlib import Path

import pytest

from scraper.storage import CookieStore


def test_store_round_trip(tmp_path: Path) -> None:
    store = CookieStore(tmp_path)
    store.store("openai", b"encrypted-blob")
    assert store.exists("openai") is True
    assert store.load("openai") == b"encrypted-blob"


def test_store_rejects_unknown_provider(tmp_path: Path) -> None:
    store = CookieStore(tmp_path)
    with pytest.raises(ValueError):
        store.path_for("hacker-attempt")
    with pytest.raises(ValueError):
        store.path_for("../etc/passwd")


@pytest.mark.parametrize("provider", ["openai", "anthropic", "together"])
def test_allowed_providers(tmp_path: Path, provider: str) -> None:
    store = CookieStore(tmp_path)
    store.store(provider, b"blob")
    assert store.exists(provider)


def test_load_missing_raises_file_not_found(tmp_path: Path) -> None:
    store = CookieStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load("openai")


def test_overwrite_replaces_blob(tmp_path: Path) -> None:
    store = CookieStore(tmp_path)
    store.store("openai", b"v1")
    store.store("openai", b"v2-longer")
    assert store.load("openai") == b"v2-longer"
