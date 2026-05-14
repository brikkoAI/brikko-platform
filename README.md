# Brikko Platform

**Open-source AI Privacy infrastructure for the Russian market.**

Backend, web UI, and infrastructure that powers [brikko.ru](https://brikko.ru) — а unified PII masking layer for working with LLMs (ChatGPT, Claude, Gemini, GigaChat, YandexGPT) while staying compliant with Russian Federal Law 152-ФЗ.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![brikko.ru](https://img.shields.io/badge/site-brikko.ru-1a1a1a.svg)](https://brikko.ru)

---

## Что внутри

```
apps/
  gateway/    — FastAPI backend (Python 3.12). Provider adapters, smart router,
                MCP server, PII masking pipeline. 1786 lines of PII detection
                logic with Natasha NER + checksum validation for Russian IDs.
  web/        — Next.js 14 frontend. Landing, dashboard, admin panel.
                Cream Studio v6 design system.
  bridge/     — Telegram ↔ Claude Code bridge for remote development.
  scraper/    — Playwright-based provider balance scraping (optional service).

infra/
  docker-compose.prod.yml — production stack (gateway + web + postgres + redis + caddy + monitoring)
  Caddyfile.prod          — TLS termination + reverse proxy
  monitoring/             — Prometheus + Grafana dashboards
  RUNBOOK.md              — operational runbook

.github/workflows/        — CI/CD pipelines (lint, test, build, deploy)
```

## Связанные проекты Brikko ecosystem

| Repo | Описание |
|---|---|
| [brikko-platform](https://github.com/brikkoAI/brikko-platform) (this) | Backend + web + infra |
| [brikko-studio](https://github.com/brikkoAI/brikko-studio) | Desktop AI agent (Docker) |
| [brikko-shield](https://github.com/brikkoAI/brikko-shield) | Chrome extension |
| [brikko-cli](https://github.com/brikkoAI/brikko-cli) | npm CLI |
| [brikko-pii-skill](https://github.com/brikkoAI/brikko-pii-skill) | Skill for Claude Code / Cursor / OpenClaw |
| [n8n-nodes-brikko](https://github.com/brikkoAI/n8n-nodes-brikko) | n8n community nodes |
| [presidio-ru-recognizers](https://github.com/brikkoAI/presidio-ru-recognizers) | Russian recognizers for Microsoft Presidio |

## Why open source

PII detection algorithms in this codebase use **public regulatory specifications**:

- ИНН checksums — order of Russian Federal Tax Service from 2004
- СНИЛС mod-101 — Russian Pension Fund methodology
- ОГРН / ОГРНИП — mod 11 / mod 13 algorithms
- Russian bank accounts — Central Bank check-key algorithm
- Natasha NER — independent open-source Russian NLP library (Apache 2.0)

Closed-source commercial product around public regulatory algorithms feels wrong. So the code is MIT.

Monetization happens on the hosted layer (api.brikko.ru) — convenient API, ruble payments through ЮKassa, unified key across the ecosystem. Self-hosting this stack works too — just point everything at your own deployment.

## Quick start (self-host)

```bash
# Clone
git clone https://github.com/brikkoAI/brikko-platform.git
cd brikko-platform

# Copy env template
cp infra/.env.production.template infra/.env
# Edit .env — set DB password, Redis password, provider API keys, etc.

# Bring up the stack
docker compose -f infra/docker-compose.prod.yml up -d --build

# Verify
curl http://localhost:8000/v1/health
```

For production deployment with TLS + monitoring, see `infra/RUNBOOK.md`.

## Architecture

```
                       ┌──────────────────────────────┐
                       │   api.brikko.ru (gateway)    │
   Browser ─────────►  │  FastAPI + Natasha NER       │
   Studio  ─────────►  │  POST /v1/anonymize          │
   CLI     ─────────►  │  POST /v1/restore (free)     │
   Skill   ─────────►  │  POST /v1/chat/completions   │  ──────► OpenAI / Anthropic /
   n8n     ─────────►  │  Smart Router v2             │          Gemini / DeepSeek /
   Shield  ─────────►  │  + PII pipeline              │          YandexGPT / GigaChat
                       │  + Audit log + Redis cache   │
                       └──────────────────────────────┘
                                       │
                                       ▼
                       ┌──────────────────────────────┐
                       │   brikko.ru (web)            │
                       │  Next.js dashboard           │
                       │  Signup, API keys, billing   │
                       │  Cream Studio v6 design      │
                       └──────────────────────────────┘
```

## Compliance — 152-ФЗ

- All masking happens on Russian-hosted infrastructure (Reg.ru Moscow + Aeza FI for outbound)
- ПД never leaves Russian jurisdiction before placeholder substitution
- Reversible unmask through Redis-backed mapping store, 60min TTL
- Audit log retention 90 days, gzip after 7d, **zero PII in log itself**
- Reusable for 152-ФЗ ст. 6 ч. 3 «поручение на обработку ПД» for B2B

## Contributing

Issues with edge cases (regional patronymics, unusual ИНН patterns, regional bank account formats) — very welcome. Russia is full of regional quirks that no test suite catches up front.

PRs for additional Russian entity types (driver's license, KPP, BIK as separate type) — open.

Pre-commit hooks set up in `.pre-commit-config.yaml`. Run `pre-commit install` after clone.

## License

MIT — see [LICENSE](LICENSE).
