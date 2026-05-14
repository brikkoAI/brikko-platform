# Backlog — community wishlist

These are ideas captured during MVP development. Implementation effort
estimates are rough — community PRs welcome.

## Live CLI ↔ Telegram mirroring

When you're on the laptop typing in the CLI, mirror Claude's output to
Telegram so you can step away and keep watching. And vice versa.

### Part A — CLI → Telegram (realistic, ~1-2 days)

The daemon already reads jsonl files (`session_discovery.py`). Add a new
endpoint `GET /sessions/{id}/follow` (SSE): tail the
`~/.claude/projects/<encoded>/<id>.jsonl`, parse new lines via
`stream_parser`, push BridgeEvents to the bot. New `/follow on|off` slash
command. Read-only ⇒ no concurrency conflict with the existing CLI mutex.

Trade-offs:
- Latency 0.5-2 sec from `claude.exe` stdout buffering
- File-watcher: `watchdog` on Linux/macOS, polling 200 ms on Windows

### Part B — Telegram → CLI (hard, requires pivot)

Three sketched paths:

1. **PTY** — daemon holds `claude` as a PTY-subprocess in interactive
   mode, emulates input. The pivot we ruled out in Day 1 — approval flow
   doesn't propagate, input gets lost on Windows. Don't recommend.
2. **MCP server** — write an MCP server that the CLI Claude loads;
   bot pushes via WebSocket / pipe; MCP injects into conversation as
   user-input through MCP mechanics. Cleanest, but tied to MCP API
   stability.
3. **Jsonl push** — bot writes to jsonl as a user-row; CLI Claude (if
   running interactively) picks up on next prompt-cycle. **Doesn't work
   today** — CLI reads jsonl only on start.

Defer until Part A proves the use case AND MCP API stabilizes.

## UX polish

- **Inline `/yolo` and `/safe` buttons** under pre-flight warnings.
  Currently you have to retype the whole prompt with the prefix; one tap
  should accept the risk.
- **Multi-PC routing** (`/host laptop` / `/host desktop`). Pre-condition:
  multi-tenant hub.
- **`/diff <file>`** — show the last diff Claude made without leaving
  Telegram.
- **Voice input** — Telegram voice message → Whisper STT → prompt.

## Operational

- **Daily / weekly digest** posted to Telegram: "Claude ran 12 prompts
  this week, top tools Bash×8 / Edit×5, $4.20 spent."
- **Auto-rotate auth tokens** after each successful pairing (currently
  one token sits there for 24 h even after use).
- **Telegram inline-query** support — `@brikkoclaude_bot session 1`
  triggers from any chat.
- **Webhook mode** instead of long-poll for high-traffic deployments.

## Multi-tenant (the big piece)

If we want this to be a hosted product (one bot, many users with their
own daemons), here's the rough scope:

- Postgres replacing SQLite
- Tenant isolation: `chat_id` ↔ `tenant_id` ↔ `daemon_url`
- Hub admin API: list tenants, revoke, audit per-tenant
- Multi-region hub for latency
- Stripe / YooKassa billing
- One-click Windows installer (.msi) and macOS .pkg
- Auto-update via signed releases on GitHub
- Daemon as Windows Service / launchd / systemd unit (lifecycle
  independent of user session)

Estimated 6-8 weeks of solo work. Most of the existing code is
multi-tenant-ready architecturally; the deltas are the hub-side state
management and packaging.

---

If you start working on any of these, open a draft PR early — easier to
sync on direction.
