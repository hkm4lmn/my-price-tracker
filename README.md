# MY Price Tracker

[![tests](https://github.com/hkm4lmn/my-price-tracker/actions/workflows/tests.yml/badge.svg)](https://github.com/hkm4lmn/my-price-tracker/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram_Bot-26A5E4?logo=telegram&logoColor=white)

A Telegram bot that watches prices on Malaysian online shops. Send it a product link and it checks the price every six hours, then messages you when the price drops, reaches a target you set, or the item comes back in stock. Every tracked product also gets a public page with its price history.

Working part-time in retail, I see how often prices move, and how easy it is to miss a drop on something you've been waiting to buy. This project is the tool I wanted: something that keeps an eye on prices for me and only speaks up when it matters.

![Product page with the current price on a yellow shelf ticket, a step chart of price history, and a list of price changes](docs/images/product-page.png)

## Features

- **Track by link.** Paste any product link into the chat, even inside a sentence. Tracking parameters like `utm_source` are stripped, so the same product shared two ways is tracked once.
- **Three kinds of alert:** price drop, target price reached (`/target 2 59.90`), and back in stock.
- **Price history pages** with a step chart and a list of changes, readable on a phone.
- **Open to anyone on Telegram**, with limits so one user can't overload it: 20 products each, 10 new ones a day, and 20 commands a minute.
- **Copes with shops that fail.** Failed checks back off from 30 minutes to 24 hours. Watchers are told once when a product can't be read, and again if it's given up after 7 days.
- **A status page** showing how often each shop reader succeeds, so a shop changing its page layout shows up before users complain.

## How it works

```
Telegram ──webhook──▶ web (FastAPI) ──┐
Browser  ──HTTPS────▶   ├ bot commands ├──▶ PostgreSQL ◀──┐
                        └ product pages ┘                 │
                                                          │
                      worker ──────────────────────────────┘
                        ├ claims due checks (FOR UPDATE SKIP LOCKED)
                        ├ fetches via shop plug-ins ──▶ shop websites
                        ├ saves prices + writes alerts (one transaction)
                        └ sends queued alerts ──▶ Telegram Bot API
```

Two processes share one PostgreSQL database. The **web** process answers Telegram and serves the pages. The **worker** picks up products that are due, reads their price, saves any change, and sends alerts. Caddy sits in front and handles HTTPS.

## Design decisions

| Decision | Why |
|---|---|
| PostgreSQL is also the job queue: workers claim due products with `FOR UPDATE SKIP LOCKED` and push `next_check_at` forward as a **lease** | No Redis or Celery to run on a small server. Workers never check the same product twice. No lock is held while a slow shop responds, and a crashed worker's products become due again when the lease expires |
| **Transactional outbox** for alerts | The price change and the alert rows commit in one transaction, so an alert can't be lost or sent for a change that was rolled back. A sender delivers them afterwards and retries with backoff if Telegram is down |
| Fetch **once per product**, not once per watcher | 50 people tracking the same item cost one request per check, which is cheaper and gentler on shops |
| Price history stores **changes only** | Unchanged checks only update "last checked", which keeps the table small. The chart draws flat steps between changes |
| Money stored as **integer sen** | Floats can't represent amounts like 0.10 exactly |
| **SSRF protection** on every fetch | Anyone can send the bot a link, so links to `localhost`, private networks or cloud metadata (`169.254.169.254`) must never be fetched. Every address a host resolves to is checked, on every redirect, and the request goes to the exact IP that passed the check, so DNS can't change in between |
| **Plug-in design**, with a general schema.org reader as the default | Many shops publish standard product data for search engines, so one reader covers them. A shop that needs special handling gets its own plug-in |
| No Shopee support | Shopee only serves product data to its own app and blocks direct requests (HTTP 403, anti-bot error `90309999`). Supporting it would mean getting around that protection. Details are in [`docs/findings`](docs/findings/2026-10-shopee-lazada.md) |

### Proving the guarantees

The tests run against real PostgreSQL, because the locking is the point:

- `test_concurrent_claims_never_overlap`: three workers race over 200 due products, and every product is claimed exactly once. With the row lock removed, the test fails.
- `test_claim_does_not_wait_for_locked_rows`: a worker skips products another worker is claiming instead of queuing behind it. Without `SKIP LOCKED`, the test fails.
- `test_outbox_retries_then_delivers_exactly_once`: Telegram fails three times and then recovers. The alert arrives once, with the waits growing 2, 4, then 8 minutes.
- `test_two_senders_do_not_double_send`: two senders share 30 queued alerts, and each is delivered once.
- `test_fetch_refuses_redirect_to_private`: a shop link that redirects to an internal address is refused before the second request is made.

One honest limit: if the worker crashes in the instant after Telegram accepts a message but before the database records it as sent, that one message is sent again on restart. Delivery is at-least-once, which is the best any system can do when calling an outside service.

## Using the bot

| Command | What it does |
|---|---|
| *(a product link)* | Start tracking it |
| `/list` | What you're tracking, with current prices and page links |
| `/target 2 59.90` | Only alert when item 2 is RM 59.90 or less (`/target 2 off` to remove) |
| `/remove 2` | Stop tracking item 2 |
| `/check 2` | Check item 2 now (once an hour per product) |
| `/help` | Show the commands |

## Quick start

Requires Docker.

```bash
git clone https://github.com/hkm4lmn/my-price-tracker.git
cd my-price-tracker
docker compose up --build -d
docker compose exec web python -m scripts.seed_demo   # optional: three demo products
```

Open **http://localhost:8000**. Without a Telegram bot token, the worker still checks prices but writes alerts to its log (`docker compose logs -f worker`) instead of sending them. To use a real bot, copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN`.

### Tests

```bash
docker compose up -d db
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql+psycopg://tracker:tracker@localhost:5432/tracker_test pytest
```

There are 201 tests. Without `TEST_DATABASE_URL`, the database tests are skipped and only the pure-logic tests run. GitHub Actions runs everything against PostgreSQL 16 on every push.

## Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather). If unset, alerts are only logged |
| `TELEGRAM_WEBHOOK_SECRET` | Random string Telegram sends back with every update; other requests are rejected |
| `PUBLIC_BASE_URL` | Public HTTPS address, used for page links in messages |
| `BOT_USERNAME` | Bot username without `@`, linked from the home page |

## Deployment

Production runs on one small Linux server with Docker Compose: PostgreSQL, web, worker and Caddy, which handles HTTPS certificates automatically. Database backups run nightly and are kept for 7 days. A manually triggered GitHub Actions workflow deploys over SSH. Step-by-step setup is in [`deploy/README.md`](deploy/README.md).

## Project structure

```
app/
  fetching/        SSRF-safe HTTP client and shop plug-ins (schema.org reader)
  tracking/        claiming due checks, recording results, alert rules, scheduling
  notify/          Telegram client, message text, outbox sender
  bot/             command handling and per-user rate limit
  web/             FastAPI app: webhook, product pages, status page
  worker.py        the background loop
  models.py        database tables
migrations/        Alembic migrations
deploy/            production compose file, Caddyfile, backups, setup guide
docs/              design spec, build plan, Shopee/Lazada findings
tests/             pytest suite, with saved product pages as fixtures
```

## What I'd add next

- A Lazada plug-in, if its terms allow it and it doesn't block the server ([findings](docs/findings/2026-10-shopee-lazada.md))
- Replies in Malay as well as English
- Letting users choose a variant (colour, storage size) instead of tracking the cheapest in-stock one

## License

MIT
