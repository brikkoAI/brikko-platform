# Brikko balance scraper

Standalone FastAPI service that runs Playwright headless to fetch upstream
balances from provider dashboards that **do not** expose a public balance API
(OpenAI, Anthropic, Together.ai). Phase 2 of the
[`balance_monitor`](../gateway/voltari_gateway/balance_monitor/) feature.

## Why a separate service

Playwright + chromium is ~500 MB on disk and pulls dozens of system libs. We
don't want any of that in the production gateway image (alpine, ~250 MB).
This service runs on the official Playwright base image and is only contacted
internally by the gateway over the `backend-net` Docker network.

## Endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET`  | `/healthz` | Liveness — always 200 when process is up. |
| `POST` | `/scrape/{provider}` | Scrape balance for `openai` / `anthropic` / `together`. |
| `POST` | `/cookies/{provider}` | Internal upload from gateway (encrypted blob). |

All scrape/cookies endpoints require `X-Internal-Token: <SCRAPER_INTERNAL_TOKEN>`.

## Storage

Cookies are stored on the `/encrypted-cookies/` volume as
`{provider}.enc` files. Encryption (Fernet) is done **on the gateway side
before upload** — this service receives an already-encrypted blob, stores it
verbatim, and decrypts it just before injecting into the Playwright context.

The Fernet key is `ENCRYPTION_KEY` (shared with the gateway via env). Same
key used for `request_payloads` PII encryption.

## Local dev

```bash
cd apps/scraper
docker build -t brikko-scraper .
docker run --rm \
  -e SCRAPER_INTERNAL_TOKEN=dev-token-32bytes-min-or-validation-fails \
  -e ENCRYPTION_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())') \
  -p 9100:9100 \
  -v "$(pwd)/data/cookies:/encrypted-cookies" \
  brikko-scraper
```

## Production

Wired into `infra/docker-compose.prod.yml` as service `brikko-scraper`. The
gateway adapter (`voltari_gateway/balance_monitor/adapters/scrape_remote.py`)
calls `http://brikko-scraper:9100/scrape/<provider>` from the `backend-net`
network when `SCRAPER_URL` is set in the gateway `.env`.

## DOM selectors

DOM selectors for each provider are educated guesses based on public dashboard
screenshots — they WILL drift. When a selector breaks the endpoint returns
`503 dom_changed`, the gateway records `fetch_status='error'` and CEO sees
the failure in the admin UI. Update selectors in `scraper/providers/*.py`
and redeploy.
