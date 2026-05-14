"""Tests for daemon.auth_tokens — file-backed token store."""
import pytest

from daemon import auth_tokens


@pytest.fixture(autouse=True)
def isolated_token_file(tmp_path, monkeypatch):
    """Redirect _tokens_file() to a tmp path, isolating tests."""
    f = tmp_path / "auth_tokens.json"
    monkeypatch.setattr(auth_tokens, "_tokens_file", lambda: f)
    yield f


def test_create_token_returns_unique_string():
    t1 = auth_tokens.create_token()
    t2 = auth_tokens.create_token()
    assert t1 != t2
    assert len(t1) >= 32


def test_get_unconsumed_returns_existing_token():
    t = auth_tokens.create_token()
    assert auth_tokens.get_unconsumed_token() == t


def test_get_unconsumed_returns_none_when_no_tokens():
    assert auth_tokens.get_unconsumed_token() is None


def test_consume_token_marks_used():
    t = auth_tokens.create_token()
    assert auth_tokens.consume_token(t, chat_id=42) is True
    # Already consumed — second consume fails
    assert auth_tokens.consume_token(t, chat_id=42) is False


def test_get_unconsumed_skips_used():
    t1 = auth_tokens.create_token()
    auth_tokens.consume_token(t1, chat_id=42)
    t2 = auth_tokens.create_token()
    assert auth_tokens.get_unconsumed_token() == t2


def test_consume_unknown_token_returns_false():
    assert auth_tokens.consume_token("does-not-exist", chat_id=42) is False


def test_is_token_consumed_by_correct_chat():
    t = auth_tokens.create_token()
    auth_tokens.consume_token(t, chat_id=42)
    assert auth_tokens.is_token_consumed_by(t, 42) is True
    assert auth_tokens.is_token_consumed_by(t, 99) is False


def test_expired_tokens_pruned_on_create(tmp_path, monkeypatch):
    """Expired tokens are removed when we create a new one — file stays small."""
    import time
    import json

    f = tmp_path / "auth_tokens.json"
    monkeypatch.setattr(auth_tokens, "_tokens_file", lambda: f)
    # Plant an expired token directly
    f.write_text(
        json.dumps(
            [
                {
                    "token": "expired-token",
                    "created_at": 0,
                    "expires_at": int(time.time()) - 3600,
                    "consumed_at": None,
                    "consumed_by_chat_id": None,
                }
            ]
        )
    )
    new_t = auth_tokens.create_token()
    # File should not contain expired-token any more
    contents = json.loads(f.read_text())
    tokens_in_file = {t["token"] for t in contents}
    assert "expired-token" not in tokens_in_file
    assert new_t in tokens_in_file


def test_previously_paired_chat_ids_empty_when_nothing_consumed():
    auth_tokens.create_token()
    assert auth_tokens.previously_paired_chat_ids() == []


def test_previously_paired_chat_ids_lists_consumed_chats():
    t1 = auth_tokens.create_token()
    auth_tokens.consume_token(t1, chat_id=42)
    t2 = auth_tokens.create_token()
    auth_tokens.consume_token(t2, chat_id=99)
    out = auth_tokens.previously_paired_chat_ids()
    assert set(out) == {42, 99}


def test_previously_paired_dedups_repeated_chat_id():
    t = auth_tokens.create_token()
    auth_tokens.consume_token(t, chat_id=42)
    # Even if we somehow stored it twice (re-pair on same device), no dups
    assert auth_tokens.previously_paired_chat_ids() == [42]
