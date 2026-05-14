"""Unit tests for daemon.session_discovery.

Sessions live at ``~/.claude/projects/<encoded-path>/<session-uuid>.jsonl``.
Each line is a JSON event from a previous Claude Code conversation.
"""
import json

import pytest

from daemon.session_discovery import list_sessions


@pytest.fixture
def fake_claude_dir(tmp_path, monkeypatch):
    """Build a fake ~/.claude/projects/ tree for tests."""
    home = tmp_path / "home"
    projects = home / ".claude" / "projects"
    projects.mkdir(parents=True)

    # Project A: 1 session, has both a summary and a cwd
    proj_a = projects / "C--Users-x-repos-brikko"
    proj_a.mkdir()
    (proj_a / "11111111-aaaa-bbbb-cccc-000000000001.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "summary", "summary": "Fixing auth bug"}),
                json.dumps({"type": "system", "subtype": "init", "cwd": "C:/Users/x/repos/brikko"}),
                json.dumps({"type": "user", "message": {"content": "hi"}}),
            ]
        ),
        encoding="utf-8",
    )

    # Project B: 2 sessions, no summary line — fallback to first user text
    proj_b = projects / "C--Users-x-repos-test"
    proj_b.mkdir()
    (proj_b / "22222222-aaaa-bbbb-cccc-000000000002.jsonl").write_text(
        "\n".join([
            json.dumps({"cwd": "C:/Users/x/repos/test"}),
            json.dumps({"type": "user", "message": {"content": "yo can you fix the bug"}}),
        ]),
        encoding="utf-8",
    )
    (proj_b / "22222222-aaaa-bbbb-cccc-000000000003.jsonl").write_text(
        "\n".join([
            json.dumps({"cwd": "C:/Users/x/repos/test"}),
            json.dumps({
                "type": "user",
                "message": {"content": [{"type": "text", "text": "another prompt here"}]},
            }),
        ]),
        encoding="utf-8",
    )

    # Random non-jsonl file in a project dir — must be ignored
    (proj_b / "ignore-me.txt").write_text("garbage", encoding="utf-8")

    # Empty session file — must be ignored (no useful info)
    proj_c = projects / "C--Users-x-repos-empty"
    proj_c.mkdir()
    (proj_c / "33333333-aaaa-bbbb-cccc-000000000099.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr("daemon.session_discovery._claude_home", lambda: home / ".claude")
    return home


def test_list_sessions_returns_all_jsonl_files(fake_claude_dir):
    sessions = list_sessions()
    ids = {s["session_id"] for s in sessions}
    assert ids == {
        "11111111-aaaa-bbbb-cccc-000000000001",
        "22222222-aaaa-bbbb-cccc-000000000002",
        "22222222-aaaa-bbbb-cccc-000000000003",
        "33333333-aaaa-bbbb-cccc-000000000099",
    }


def test_list_sessions_includes_project_path(fake_claude_dir):
    sessions = list_sessions()
    by_id = {s["session_id"]: s for s in sessions}
    assert by_id["11111111-aaaa-bbbb-cccc-000000000001"]["project_path"] == "C--Users-x-repos-brikko"
    assert by_id["22222222-aaaa-bbbb-cccc-000000000002"]["project_path"] == "C--Users-x-repos-test"


def test_list_sessions_extracts_summary_when_present(fake_claude_dir):
    sessions = list_sessions()
    by_id = {s["session_id"]: s for s in sessions}
    assert by_id["11111111-aaaa-bbbb-cccc-000000000001"]["summary"] == "Fixing auth bug"


def test_list_sessions_falls_back_to_first_user_text_when_no_summary(fake_claude_dir):
    """Most modern sessions don't ship a 'summary' field; fall back to the
    first user message so the /sessions list isn't all '(без описания)'."""
    sessions = list_sessions()
    by_id = {s["session_id"]: s for s in sessions}
    # String-content user message
    assert by_id["22222222-aaaa-bbbb-cccc-000000000002"]["summary"] == "yo can you fix the bug"
    # List-of-blocks content (Anthropic shape)
    assert by_id["22222222-aaaa-bbbb-cccc-000000000003"]["summary"] == "another prompt here"


def test_list_sessions_extracts_cwd_when_present(fake_claude_dir):
    """cwd from jsonl is what /switch persists so claude --resume can find
    the session. Without it claude says 'No conversation found'."""
    sessions = list_sessions()
    by_id = {s["session_id"]: s for s in sessions}
    assert by_id["11111111-aaaa-bbbb-cccc-000000000001"]["cwd"] == "C:/Users/x/repos/brikko"
    assert by_id["22222222-aaaa-bbbb-cccc-000000000002"]["cwd"] == "C:/Users/x/repos/test"


def test_list_sessions_cwd_none_when_jsonl_lacks_it(fake_claude_dir):
    """Empty / corrupt sessions: cwd is None, bot will fall back gracefully."""
    sessions = list_sessions()
    by_id = {s["session_id"]: s for s in sessions}
    assert by_id["33333333-aaaa-bbbb-cccc-000000000099"]["cwd"] is None


def test_list_sessions_skips_continuation_blurb_in_summary_fallback(tmp_path, monkeypatch):
    """When a session was resumed after compact, the first user message is an
    auto-injected 'This session is being continued...' blurb. That's noise,
    not a real user prompt — fallback should skip it."""
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "C--proj"
    proj.mkdir(parents=True)
    (proj / "abc.jsonl").write_text(
        "\n".join([
            json.dumps({"cwd": "C:/proj"}),
            json.dumps({
                "type": "user",
                "message": {"content": "This session is being continued from a previous..."},
            }),
            json.dumps({
                "type": "user",
                "message": {"content": "the real first prompt"},
            }),
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr("daemon.session_discovery._claude_home", lambda: home / ".claude")
    sessions = list_sessions()
    assert sessions[0]["summary"] == "the real first prompt"


def test_list_sessions_sorted_by_last_modified_desc(fake_claude_dir, tmp_path):
    """Most recently used session comes first — useful for UX (default to current)."""
    import os
    import time

    sessions = list_sessions()
    # All four sessions should appear
    assert len(sessions) == 4
    # Sorted descending by last_modified — first one should have highest mtime
    times = [s["last_modified"] for s in sessions]
    assert times == sorted(times, reverse=True), "expected DESC order"


def test_list_sessions_returns_empty_when_no_projects_dir(tmp_path, monkeypatch):
    """Daemon on a fresh PC where claude was never run — should return [] not crash."""
    monkeypatch.setattr(
        "daemon.session_discovery._claude_home", lambda: tmp_path / "nonexistent" / ".claude"
    )
    assert list_sessions() == []


def test_list_sessions_skips_non_directory_in_projects(fake_claude_dir):
    """If a stray file ends up at ~/.claude/projects/some_file, don't choke on it."""
    home = fake_claude_dir
    (home / ".claude" / "projects" / "stray-file.txt").write_text("oops", encoding="utf-8")
    sessions = list_sessions()
    # Must still have all 4 sessions, no crash
    assert len(sessions) == 4


def test_list_sessions_handles_corrupt_jsonl_gracefully(fake_claude_dir):
    """A jsonl file with broken JSON in first line should still be listed (summary=None)."""
    home = fake_claude_dir
    proj = home / ".claude" / "projects" / "C--Users-x-broken"
    proj.mkdir()
    (proj / "44444444-aaaa-bbbb-cccc-000000000044.jsonl").write_text(
        "{this is not json\n", encoding="utf-8"
    )

    sessions = list_sessions()
    by_id = {s["session_id"]: s for s in sessions}
    assert "44444444-aaaa-bbbb-cccc-000000000044" in by_id
    assert by_id["44444444-aaaa-bbbb-cccc-000000000044"]["summary"] is None
