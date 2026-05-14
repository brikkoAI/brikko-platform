"""Pre-flight risk check for prompts.

We send all prompts to claude with ``--dangerously-skip-permissions``, so
there is no built-in approval gate. To compensate we scan the *prompt*
itself for obvious destructive intent and ask the user to confirm before
forwarding to the daemon.

Two modes:

  * ``normal`` (default) — only flags clearly destructive keywords
    (``rm -rf``, ``force push``, ``drop table``, …)
  * ``strict``  — the ``/safe`` modifier; additionally flags any *write*
    intent (edit, write, create file, push, commit)

A user can prefix a one-shot prompt with ``/yolo`` to skip the check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Patterns that are dangerous regardless of mode. Case-insensitive substrings.
HIGH_RISK_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bsudo\s+rm\b",
    r"\bgit\s+push\s+--force\b",
    r"\bgit\s+push\s+-f\b",
    r"\bforce\s+push\b",
    r"\bdrop\s+table\b",
    r"\bdrop\s+database\b",
    r"\btruncate\s+table\b",
    r"\bdelete\s+from\b",
    r"\bmkfs\b",
    r"\b:>\s*/",          # redirect-truncate to absolute path
    r"\bchmod\s+777\b",
    r"\b/etc/passwd\b",
    r"\b\.env\b.*\b(post|push|send|upload)\b",  # leaking secrets
    # Russian keywords CEO might use
    r"удали\s+всё",
    r"форс[ -]?пуш",
    r"дропни",
]

# Additional "write intent" patterns flagged only in strict mode.
WRITE_INTENT_PATTERNS = [
    r"\bedit\b",
    r"\bwrite\b",
    r"\bcreate\s+file\b",
    r"\bgit\s+commit\b",
    r"\bgit\s+push\b",
    r"\bdeploy\b",
    r"\bmerge\b",
    r"измени",
    r"перепиши",
    r"закоммить",
    r"запуши",
]


@dataclass(frozen=True)
class PreflightVerdict:
    """Result of scanning a prompt."""
    risky: bool
    reasons: tuple[str, ...]  # human-readable matched patterns

    @property
    def reason_text(self) -> str:
        if not self.reasons:
            return ""
        return ", ".join(self.reasons)


def check(prompt: str, *, strict: bool = False) -> PreflightVerdict:
    """Scan ``prompt`` for risky patterns.

    Args:
      prompt: User text (already stripped of /yolo or /safe prefix).
      strict: If True, also flag write-intent verbs.

    Returns:
      PreflightVerdict with ``risky=True`` if any pattern matched.
    """
    if not prompt:
        return PreflightVerdict(risky=False, reasons=())

    text = prompt.lower()
    reasons: list[str] = []

    for pat in HIGH_RISK_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            reasons.append(m.group(0))

    if strict:
        for pat in WRITE_INTENT_PATTERNS:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                reasons.append(m.group(0))

    # De-duplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for r in reasons:
        if r not in seen:
            deduped.append(r)
            seen.add(r)

    return PreflightVerdict(risky=bool(deduped), reasons=tuple(deduped))
