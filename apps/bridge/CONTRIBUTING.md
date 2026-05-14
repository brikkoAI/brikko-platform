# Contributing to Brikko Bridge

Thanks for the interest. A few short notes — nothing fancy.

## Before opening a big PR

For anything beyond a small fix (typo, doc clarification, narrow bug),
**open an issue first** to sketch the approach. Saves you and us time
if the direction conflicts with something on the roadmap that isn't
publicly visible yet.

Items in [`backlog.md`](./backlog.md) are pre-approved for someone to
work on — open a draft PR early and tag it with the backlog section.

## Local setup

```bash
git clone https://github.com/brikkoAI/brikko-bridge
cd brikko-bridge
python3.12 -m venv .venv
source .venv/bin/activate         # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest tests/
```

You should see `183 passed` (number grows with each PR).

## Code style

- Black / ruff with default config (run before commit if you want, CI
  doesn't enforce yet)
- Type hints required for public functions; internal helpers can skip
  if the type is obvious
- One thought per PR. Easier to review, easier to revert.

## Commit messages

Follow conventional-commit-ish:

```
<type>(<scope>): <short summary>

<body explaining the why, not the what>
```

`type` ∈ `feat / fix / docs / test / refactor / chore / perf / ci`.
`scope` ∈ `bridge / bot / daemon / docs / deploy / tests` (use what
matches; not strict).

The body should answer "why does this change exist" — anyone can read
the diff to know what changed; only you know why.

## Testing rules

- New feature → new test. We're at 183 tests; PRs that drop coverage
  noticeably will get pushback.
- If you find a bug, write the failing test first, then fix it. Saves
  someone reproducing it later.
- Don't disable tests. If a test is genuinely wrong, fix the test in the
  same PR; if a feature regresses, fix the feature.

## Security

If you find a security issue **do not open a public issue**. Email
`support@brikko.ru` with details. We'll respond within 48 hours.

In particular, please flag:

- Anything that lets a non-paired Telegram user reach the daemon
- Anything that leaks the daemon's loopback-only constraint
- Anything that bypasses pre-flight without explicit `/yolo` (the user
  must consciously opt in)
- Anything that lets the daemon spawn `claude` without the CLI-vs-bridge
  mutex check (when `force=False`)

## License

By contributing, you agree your contribution is licensed under MIT (the
project license). No CLA, no fancy paperwork.
