# Brikko Bridge

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Tests](https://img.shields.io/badge/tests-297%20passing-brightgreen.svg)](#testing)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)

**Telegram → Claude Code, on your own machine.**

Run a Telegram bot that talks to the Claude Code CLI on your laptop. From
your phone — kick off a refactor, watch the stream, hit **🛑 Cancel** when
needed. Your OAuth, your machine, your jsonl history. No SaaS, no vendor
lock-in.

```
[ your phone (Telegram) ]
         │
         ▼
[ bot (any tiny VPS) ] ──── SSH reverse tunnel ────┐
                                                   ▼
                                       [ daemon on your PC ]
                                                   │
                                                   ▼
                                       [ claude --resume ]
                                                   │
                                                   ▼
                                  ~/.claude/projects/<...>/<id>.jsonl
```

---

## Why this exists

Anthropic shipped [Claude Code Channels][channels] as a built-in Telegram
plugin in March 2026 — a great solo-dev experience. But it doesn't help
when you need any of these:

- **Inline approval** for risky commands without leaving Telegram
- **Audit log** of every prompt / tool-call / cancel across the team
- **Pre-flight regex screen** that catches `rm -rf`, `force push`,
  `drop database` (incl. Russian / mixed-script variants)
- **Mutex** so an interactive CLI Claude on the same project can't
  race-corrupt the session jsonl when a Telegram message arrives
- **Self-hosted everything** — bot on your VPS, daemon on your PC, no
  third-party reading your prompts

If you want any of these, this is the project.

[channels]: https://code.claude.com/docs/en/channels

---

## Quickstart (5 minutes)

You need:

- A PC running Claude Code (Windows / macOS / Linux)
- Any Linux VPS for the bot (1 vCPU / 1 GB RAM is plenty)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- An SSH key from PC → VPS

### 1. On your PC

```bash
git clone https://github.com/brikkoAI/brikko-bridge
cd brikko-bridge
pip install -e ".[dev]"
```

Make sure `claude` is in PATH:

```bash
claude --version   # should print 2.x or higher
claude auth status # should be "Logged in as ..."
```

### 2. On your VPS (the "hub")

```bash
# Copy bot files over
scp -r bot shared requirements.bot.txt root@your-vps:/opt/brikko-bridge/

# On the VPS:
cd /opt/brikko-bridge
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.bot.txt

# Create env file
cat > bot.env <<EOF
BRIDGE_BOT_BOT_TOKEN=<token-from-BotFather>
BRIDGE_BOT_BOT_USERNAME=<your_bot_username>
BRIDGE_BOT_DAEMON_URL=http://127.0.0.1:8090
BRIDGE_BOT_DB_PATH=/opt/brikko-bridge/data/bridge.db
EOF
chmod 600 bot.env

# Install systemd unit (or run via screen/tmux for testing)
cp deploy/brikko-bridge-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now brikko-bridge-bot
```

### 3. Back on your PC

```bash
# Set bot username so the supervisor prints the right deep-link
$env:BRIDGE_BOT_USERNAME = "your_bot_username"  # PowerShell
# export BRIDGE_BOT_USERNAME=your_bot_username  # bash

# Start everything: daemon + SSH reverse tunnel + auto-restart
python -m daemon.supervisor
```

The supervisor will print:

```
==================================================================
  Brikko Bridge — open this link on your phone to pair Telegram:
  https://t.me/<your_bot>?start=<random-token>
==================================================================
```

### 4. On your phone

Open the link. The bot says ✅ paired. Send `/sessions` — you should see
your existing Claude Code projects. `/switch 1`, then any text → Claude
on your PC answers in chat.

That's it.

---

## Features

### 10 slash commands

| Command | What it does |
|---|---|
| `/sessions` | List recent Claude Code sessions on your PC (top 20) |
| `/switch <n>` | Pick the active session |
| `/status` | Daemon ok? Claude version? Active session? Other CLI Claudes running? |
| `/cancel` | Kill the in-flight `claude.exe` process |
| `/audit [n]` | Last n events for this chat (auth, prompts, cancels, blocks) |
| `/permissions [reset]` | Show or wipe the in-session always-allow / always-deny cache |
| `/yolo <prompt>` | Skip pre-flight + CLI mutex (you accept the risk) |
| `/safe <prompt>` | Stricter pre-flight — even "edit" / "write" are flagged |
| `/help` | List of commands |
| _any text_ | Send as a prompt to the active session |

### Per-tool approval (Phase 8)

Every state-changing tool call from Claude — `Bash` with side effects,
`Edit`, `Write`, `MultiEdit` — pauses on the daemon and shoots a Telegram
prompt with two buttons (`✅ Allow` / `❌ Deny`). For `Edit` / `Write` /
`MultiEdit` you also see `🔓 Always allow Edit` / `🔒 Always deny Edit`
which cache the choice for the rest of the **current** session.
`Bash` never gets "always allow" — each Bash is re-prompted.

Read-only tools (`Read`, `Glob`, `Grep`, `TodoWrite`) skip the round trip
entirely. CLI's own safe-Bash classifier auto-allows `echo`, `pwd`, `ls`,
`cat` even before we see them.

When CEO doesn't tap a button within 5 minutes (configurable) the daemon
auto-denies with `approval_timeout`. If Redis is unreachable the daemon
falls back to **YOLO mode** (allows the tool, but posts a big red banner
in Telegram so you know the safety net is off). Flip
`BRIDGE_DAEMON_YOLO_ON_REDIS_DOWN=false` to fail-closed instead.

Tap "Always allow" by mistake? Send `/permissions reset` — clears the
cache for the active session.

### Streaming with throttled edits

Claude's stream-json output renders into a single Telegram message that's
edited in place every ~600 ms. Tool calls show as `📖 Read /path/to/file`,
results as `✓ output...` or `❌ error`. Todo updates render as a checklist
with `✅ done`, `🔧 in-progress`, `○ pending`. Telegram's 4096-char limit
is handled by spawning a follow-up message.

### Inline 🛑 Cancel button

Every streaming reply ships an inline keyboard. One tap → daemon kills
the live `claude.exe` → stream emits a final flush with whatever was
already typed. Faster than `/cancel`.

### CLI-vs-bridge mutex

The daemon scans `claude.exe` processes via `psutil`. If you have an
interactive CLI Claude open in the same project folder when a Telegram
prompt arrives, the bot answers:

```
🔒 Сессия занята: в проекте C:\Users\...\Project уже работает
   CLI-Claude (PID 26220). Закрой её и попробуй снова — одновременная
   запись в jsonl может его испортить. Уверен — пришли с префиксом /yolo.
```

This prevents the race where two `claude` processes both append to the
same `<session-id>.jsonl` and corrupt the conversation.

### Pre-flight risk screen

`/yolo` and `/safe` are escape hatches around a regex-based pre-flight
that catches obviously destructive prompts. Default mode flags:

- `rm -rf`, `sudo rm`, `mkfs`, `chmod 777`, `:>` to absolute paths
- `git push --force`, `force push`, `форс пуш`
- `drop table`, `drop database`, `truncate`, `delete from`, `дропни`
- `.env` + leak verbs (post / push / send / upload)
- `удали всё`, plus mixed Russian/Latin variants

Strict mode (`/safe <prompt>`) additionally flags any write-intent verb
(edit, write, create file, deploy, push, commit, измени, перепиши,
запуши). Useful when you're walking somewhere and don't want to
accidentally tell Claude to push uncommitted work.

### Smart deep-link

Once paired, the supervisor stops printing pairing links — just shows
`Bridge ready. Paired with chat_id=N`. Run again later, no spam.

### Audit log

Every event lands in SQLite (`auth_success`, `auth_failed`,
`session_switched`, `prompt_sent`, `prompt_done`, `prompt_failed`,
`preflight_blocked`, `cancel_requested`). `/audit 50` from Telegram
shows them with friendly icons. Per-chat-id isolated — if you ever add
a second user, they only see their own events.

---

## Architecture

Three processes, two channels, one shared file system.

```
┌──────────────────────────────┐
│ Telegram Bot API (cloud)     │
└─────────────┬────────────────┘
              │  long-poll over HTTPS
              ▼
┌──────────────────────────────┐
│ bot — your VPS (Aeza, DO,    │
│ Hetzner, anywhere with port  │
│ 22 outbound)                 │
│  · aiogram 3.x               │
│  · whitelist by chat_id      │
│  · pre-flight regex          │
│  · stream renderer           │
│  · SQLite (authorized_chats, │
│    active_sessions, audit_log)│
└─────────────┬────────────────┘
              │  HTTP loopback :8090
              ▼
┌──────────────────────────────┐
│  SSH reverse tunnel          │
│  ssh -R 8090:127.0.0.1:9090  │
│  user@vps                    │
└─────────────┬────────────────┘
              │  outbound from PC, never inbound
              ▼
┌──────────────────────────────┐
│ daemon — your PC (Windows /  │
│ macOS / Linux)               │
│  · FastAPI on 127.0.0.1:9090 │
│  · session_discovery scans   │
│    ~/.claude/projects/       │
│  · runner_registry tracks    │
│    live claude.exe procs     │
│  · process_inspector (psutil)│
│    sees CLI Claudes for mutex│
│  · supervisor auto-restarts  │
│    daemon + tunnel           │
└─────────────┬────────────────┘
              │  spawn
              ▼
┌──────────────────────────────┐
│ claude --resume <id> --print │
│   --output-format=stream-json│
│   --dangerously-skip-permissions │
└──────────────────────────────┘
```

Why SSH reverse tunnel, not WireGuard or open ports? See
[`docs/DAY1_PROTOTYPE_RESULTS.md`](../../docs/superpowers/specs/DAY1_PROTOTYPE_RESULTS.md)
in the parent repo for the de-risking journey. TL;DR: WireGuard handshake
works but AmneziaVPN intercepts at WFP kernel level on Windows; SSH-R is
the lowest-friction primitive that survives any consumer firewall.

### Why `--dangerously-skip-permissions`?

`claude --print --resume` doesn't support interactive approval injection
(verified in `DAY1_PROTOTYPE_RESULTS.md`). So we pick **always-skip** +
**transparent streaming** + **fast cancel** + **bot-side pre-flight**.
The user sees every tool call as it happens. If something looks wrong,
🛑 in 1 second.

This is a deliberate trade-off, not a gap. If you want hard sandboxing,
use Anthropic Claude Code Channels (their managed solution). This
project is for people who want a self-hosted alternative with policy +
audit features they can extend.

---

## Configuration

### Bot (on the VPS)

| Env var | Required | Default | Notes |
|---|---|---|---|
| `BRIDGE_BOT_BOT_TOKEN` | yes | — | From [@BotFather](https://t.me/BotFather) |
| `BRIDGE_BOT_BOT_USERNAME` | no | `brikko_agent_bot` | Used for the `/start` deep-link |
| `BRIDGE_BOT_DAEMON_URL` | no | `http://127.0.0.1:8090` | Where the SSH tunnel lands |
| `BRIDGE_BOT_DB_PATH` | no | `/data/bridge.db` | SQLite database |
| `BRIDGE_BOT_TELEGRAM_MSG_MAX` | no | `4096` | Telegram split threshold |
| `BRIDGE_BOT_REDIS_URL` | no | `redis://127.0.0.1:6379/0` | Phase 8 approval channel — bot↔daemon |
| `BRIDGE_BOT_APPROVAL_RESPONSE_TTL_SECONDS` | no | `600` | TTL on response keys after BLPOP |

### Daemon (on the PC)

| Env var | Required | Default | Notes |
|---|---|---|---|
| `BRIDGE_DAEMON_LISTEN_HOST` | no | `127.0.0.1` | **Will not bind** anything else by design |
| `BRIDGE_DAEMON_LISTEN_PORT` | no | `9090` | |
| `BRIDGE_DAEMON_CLAUDE_BINARY` | no | `claude` | Full path to `claude`/`claude.exe` |
| `BRIDGE_DAEMON_TUNNEL_REMOTE_HOST` | no | (set me) | Your VPS IP |
| `BRIDGE_DAEMON_TUNNEL_REMOTE_PORT` | no | `8090` | |
| `BRIDGE_DAEMON_TUNNEL_SSH_USER` | no | `root` | |
| `BRIDGE_DAEMON_REDIS_URL` | no | `redis://127.0.0.1:6379/0` | Phase 8 — daemon talks to same Redis via SSH local-forward |
| `BRIDGE_DAEMON_APPROVAL_TIMEOUT_SECONDS` | no | `300` | How long daemon waits for a Telegram button tap before auto-denying |
| `BRIDGE_DAEMON_YOLO_ON_REDIS_DOWN` | no | `true` | When Redis is unreachable: `true` → allow tools (with red banner in TG); `false` → deny tools (strict mode) |
| `BRIDGE_DAEMON_YOLO_MODE` | no | `false` | Hard-override: when `true`, every tool auto-approved (== legacy `--dangerously-skip-permissions`). Use only as emergency switch |
| `BRIDGE_DAEMON_LEGACY_SUBPROCESS_RUNNER` | no | `false` | Use the pre-Phase-8 subprocess+stream-json runner. For dev only |

### SSH key

The supervisor expects `~/.ssh/brikko_aeza` (private key) by default.
Override with `--key-path` to `daemon.supervisor` if you keep keys
elsewhere. Tunnel-only restriction recommended in
`/root/.ssh/authorized_keys` on the VPS:

```
restrict,permitlisten="127.0.0.1:8090",command="echo tunnel-only;false" \
ssh-ed25519 AAAA... your-key-name
```

This way even if the key leaks, no shell on the VPS, no other forwards.

---

## Security model

| Threat | Mitigation |
|---|---|
| Random Telegram user finds your bot | Whitelist by `chat_id`; only `/start` and `/help` pass through middleware before pairing |
| Pairing link leaked | One-shot tokens with 24 h TTL; once consumed, useless. Re-pair on the same chat_id is allowed (you replaced your phone), but a different `chat_id` consuming the same token is rejected |
| MITM between bot and daemon | SSH reverse tunnel encrypts the channel end-to-end |
| External attacker probes daemon | Daemon refuses to bind anything but loopback (`RuntimeError` at startup if you try); the only way in is via the SSH tunnel that **you** initiated |
| Stolen SSH key | Tunnel-only authorized_keys (see above); recommend passphrase + `keychain` if your laptop is ever out of your sight |
| Prompt asking Claude to do bad things | Pre-flight regex on 18+ patterns (incl. Russian); `/yolo` is your conscious opt-out |
| Two Claudes writing to same jsonl | CLI-vs-bridge mutex via `psutil` cwd match |
| Compromised bot leaks audit log | SQLite stays on the VPS; if you don't want it, delete `/data/bridge.db` and pair again |

---

## Comparison

| Feature | Brikko Bridge | Claude Code Channels (official) | OpenSource alternatives |
|---|---|---|---|
| Self-hosted | ✓ both bot + daemon | ✗ Anthropic cloud | varies |
| OAuth lives on your machine | ✓ | ✗ Anthropic | varies |
| Inline approve / cancel | ✓ inline keyboard | tap-to-confirm in TG | varies |
| Audit log / `/audit` | ✓ per-chat | ✗ | rare |
| Pre-flight regex | ✓ 18 patterns | ✗ | rare |
| CLI-vs-bridge mutex | ✓ psutil-based | n/a | ✗ |
| Russian / mixed-script destructive verbs | ✓ | ✗ | ✗ |
| Cost (per month) | $5 VPS + your Claude subscription | $20 Pro + nothing else | varies |
| Multi-tenant ready | code says yes, MVP says no | ✓ | varies |
| License | MIT | proprietary | varies |

If your needs are met by Channels, use Channels — it's smoother. If you
want any of the right-hand column above, this project is for you.

---

## Testing

```bash
cd brikko-bridge
pip install -e ".[dev]"
pytest tests/
```

```
======================== 183 passed in 17.02s ========================
```

Tests cover:
- daemon: session discovery, stream parser (every event type), claude
  runner spawn + mutex, runner registry eviction, auth tokens, process
  inspector classification
- bot: db migrations, auth handler (ok / invalid / daemon-offline /
  re-pair), whitelist middleware, stream renderer (every event), daemon
  client (SSE parsing, errors, force flag), prompt handler (preflight
  block, daemon offline, empty response, frozen Message), cancel
  callback, all 9 commands, /audit per-chat isolation

CI lives in the parent monorepo at the time of writing — when this repo
becomes the canonical home, GitHub Actions config moves with it.

---

## Roadmap (community wishlist)

These are documented in
[`backlog.md`](./backlog.md) and PRs are welcome:

- Live mirroring CLI ↔ Telegram (read-only side: realistic 1-2 days,
  see backlog §"Part A")
- Inline `/yolo` + `/safe` buttons under pre-flight blocks
- Multi-PC routing (`/host laptop` / `/host desktop`)
- Voice input via Whisper (Telegram voice → STT → prompt)
- `/diff <file>` — last diff without leaving Telegram
- Daily/weekly digest
- Multi-tenant hub (single bot, many daemons) — biggest piece, ~6 weeks

---

## Related products

This project lives next to the rest of [Brikko](https://brikko.ru), a
Russian B2B AI gateway:

- **[Brikko Gateway](https://brikko.ru)** — OpenAI-compatible API to 19
  LLMs (GPT, Claude, Gemini, YandexGPT, GigaChat, DeepSeek) with
  ruble billing, server-side PII protection (Privacy Mode v2), and
  full closing documents (contract + invoice + EDM + receipt) for
  Russian companies. **Bridge users get 30 days of Pro free** —
  email `support@brikko.ru` from your Telegram-paired email.
- [`brikkoAI/presidio-ru-recognizers`](https://github.com/brikkoAI/presidio-ru-recognizers)
  — Russian recognizers for Microsoft Presidio (PyPI)
- [`brikkoAI/n8n-nodes-brikko`](https://github.com/brikkoAI/n8n-nodes-brikko)
  — n8n nodes for workflow automation with PII protection (npm)

---

## License

MIT — see [LICENSE](./LICENSE).

---

## Contact

- Issues / PRs: [github.com/brikkoAI/brikko-bridge](https://github.com/brikkoAI/brikko-bridge)
- Email: `support@brikko.ru`
- Telegram: [@brikko_news](https://t.me/brikko_news) (release notes)

If you build something cool with this, send it our way — happy to feature
on the Brikko site.
