"""Tests for bot.services.preflight."""
import pytest

from bot.services.preflight import check


def test_safe_prompt_not_risky():
    v = check("Расскажи как работает event loop в Python")
    assert v.risky is False
    assert v.reasons == ()


@pytest.mark.parametrize(
    "prompt",
    [
        "rm -rf /",
        "please rm -rf the temp folder",
        "sudo rm -rf node_modules",
        "git push --force origin main",
        "git push -f",
        "force push to origin",
        "DROP TABLE users",
        "drop database production",
        "DELETE FROM users WHERE id=1",
        "TRUNCATE TABLE big",
        "mkfs.ext4 /dev/sda",
        "chmod 777 /etc",
        "удали всё в папке",
        "сделай форс пуш",
        "дропни таблицу users",
    ],
)
def test_high_risk_patterns_flagged(prompt):
    v = check(prompt)
    assert v.risky, f"expected risky for: {prompt!r}"
    assert v.reasons


def test_strict_mode_flags_write_verbs():
    v = check("Edit src/main.py to fix the bug", strict=True)
    assert v.risky
    assert any("edit" in r for r in v.reasons)


def test_strict_mode_flags_russian_write_verbs():
    v = check("Перепиши main.py", strict=True)
    assert v.risky


def test_normal_mode_does_not_flag_write_verbs():
    v = check("edit the file", strict=False)
    assert not v.risky


def test_empty_prompt_not_risky():
    v = check("")
    assert not v.risky


def test_dedup_repeated_matches():
    v = check("rm -rf foo && rm -rf bar")
    # Same pattern matched twice — should appear only once
    assert v.risky
    assert v.reasons.count("rm -rf") <= 1


def test_reason_text_joins_reasons():
    v = check("rm -rf / && drop table users")
    assert v.risky
    assert "rm -rf" in v.reason_text
    assert "drop table" in v.reason_text
    assert ", " in v.reason_text
