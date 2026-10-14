# MY Price Tracker: Design

**Date:** 2026-10-15
**Status:** Draft for review

## 1. Goal

A public Telegram bot that tracks product prices on Malaysian online shops and alerts users when a price drops, reaches their target, or comes back in stock. Each tracked product has a public web page with its price-history chart.

It is a portfolio project, so it must be **real and always on**: hosted, checking prices on a schedule, collecting real price history, and usable by anyone on Telegram.

### Success criteria

- Anyone can message the bot, add a product link, and receive alerts without any setup.
- The service runs unattended on a small server for weeks, with real price history building up.
- A blocked or changed shop degrades gracefully: users are told once, the service keeps working for other shops, and the status page shows it.
- Tests prove the key guarantees: each due product is checked exactly once by concurrent workers, alerts are never lost or duplicated, and unsafe URLs are refused.

### Decisions made during design

| Question | Decision |
|---|---|
| How it runs | Hosted and always on |
| Which shops | Plug-in design: one generic reader for schema.org product data, plus Shopee and Lazada plug-ins only if a feasibility test shows they work |
| Who can use it | Anyone on Telegram, with per-user limits |
| Interface | Telegram bot for all actions, plus read-only web pages with price-history charts |
| Hosting | Free via GitHub Student Developer Pack credit; portable to any Linux host with Docker Compose |
| Architecture | One web app plus one worker, sharing PostgreSQL, which also serves as the job queue |

### Out of scope (this version)

Web login, non-MYR prices, email alerts, browser automation to get around blocking, and a Malay-language bot (a good follow-up).

## 2. Architecture

```
Telegram ──webhook──▶ web (FastAPI) ──┐
Browser  ──HTTPS────▶   ├ bot commands ├──▶ PostgreSQL ◀──┐
                        └ product pages ┘                 │
                                                          │
                      worker ──────────────────────────────┘
                        ├ claims due checks (FOR UPDATE SKIP LOCKED)
                        ├ fetches via shop plug-ins ──▶ shop websites
                        ├ saves prices + writes notifications (one transaction)
                        └ sends queued notifications ──▶ Telegram Bot API
```

Containers (Docker Compose): `web`, `worker`, `postgres`, `caddy` (reverse proxy with automatic HTTPS).

Stack: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, httpx, Jinja2 templates, Chart.js. The Telegram Bot API is called directly with httpx; no bot framework.

## 3. Data model

All money is stored as integer sen (RM 72.90 → `7290`) and converted at the edges.

### users

| Column | Notes |
|---|---|
| `id` | primary key |
| `telegram_chat_id` | unique |
| `created_at` | |
| `is_blocked` | set when Telegram reports the user blocked the bot; no further messages are sent |

### products

One row per normalised product URL, shared by every user who watches it.

| Column | Notes |
|---|---|
| `id` | primary key |
| `url` | normalised (lower-case host, tracking query parameters such as `utm_*` removed, fragment removed); unique |
| `shop` | plug-in name, e.g. `schema_org`, `shopee` |
| `public_id` | random 8-character slug for `/p/{public_id}`; unique; not guessable |
| `title`, `image_url` | from the latest successful check |
| `current_price_sen`, `in_stock` | from the latest successful check; null until the first check |
| `last_checked_at`, `last_success_at` | |
| `next_check_at` | indexed; drives scheduling |
| `status` | `active`, `failing` or `dead` |
| `consecutive_failures` | reset to 0 on success |
| `last_error` | error type and short message |
| `created_at` | |

Fetching once per product, not per watcher, keeps load on shops and the server low.

### watches

| Column | Notes |
|---|---|
| `id` | primary key |
| `user_id`, `product_id` | unique together |
| `target_price_sen` | optional |
| `baseline_price_sen` | price when the watch was created (or at first successful check) |
| `last_alerted_price_sen` | price in the most recent drop alert |
| `target_armed` | true when the next target crossing should alert |
| `created_at` | |

Limit: 20 watches per user.

### price_points

| Column | Notes |
|---|---|
| `id` | primary key |
| `product_id` | indexed together with `observed_at` |
| `price_sen`, `in_stock` | |
| `observed_at` | |

A row is written only when price or stock changes. The chart draws a step line between changes and extends the last value to `last_success_at`.

### notifications (transactional outbox)

| Column | Notes |
|---|---|
| `id` | primary key |
| `user_id` | |
| `kind` | `price_drop`, `target_reached`, `back_in_stock`, `failing`, `dead` |
| `payload` | JSON with the values needed to render the message |
| `created_at`, `sent_at` | `sent_at` null until delivered |
| `attempts`, `next_attempt_at`, `last_error` | for retries |

## 4. Price check flow

The worker loop repeats every few seconds.

1. **Claim.** In a short transaction, select up to 10 products with `status != 'dead' AND next_check_at <= now()`, ordered by `next_check_at`, using `FOR UPDATE SKIP LOCKED`. Set their `next_check_at` to `now() + 10 minutes` (a lease) and commit. No lock is held during network I/O; if the worker crashes, the lease expires and the product is claimed again.
2. **Fetch.** Pick the plug-in by URL host (falling back to `schema_org`). Respect a per-host minimum gap of a few seconds between requests. The plug-in returns `Snapshot(title, price_sen, in_stock, currency, image_url)` or raises one of `Blocked`, `NotFound`, `ParseError`, `FetchTimeout`. Snapshots with a currency other than MYR are treated as `ParseError`.
3. **Save.** In one transaction: insert a `price_points` row if price or stock changed; update the product; evaluate alert rules for each watch and insert `notifications` rows; set the next check time.
4. **Send.** A sender step in the same worker process selects unsent notifications with `next_attempt_at <= now()` using `FOR UPDATE SKIP LOCKED`, sends each to Telegram, and sets `sent_at`. On failure it increments `attempts` and backs off. If Telegram says the user blocked the bot, it marks the user `is_blocked` and drops their pending notifications.
5. **Schedule.** After a success, `next_check_at = now() + 6 hours ± 20% random jitter`.

Users can request an immediate check with `/check`, at most once per hour per product.

### Alert rules

Evaluated per watch after each successful check:

- **Price drop (no target set):** alert when the new price is lower than `last_alerted_price_sen` (or `baseline_price_sen` if no alert has been sent). Set `last_alerted_price_sen` to the new price.
- **Target reached (target set):** alert once when price ≤ target and `target_armed` is true, then set `target_armed = false`. Re-arm when the price rises above the target.
- **Back in stock:** alert when `in_stock` goes from false to true.

A watch with a target does not receive plain drop alerts. Users only want the target alert.

## 5. Failure handling

- **Backoff.** On failure, increment `consecutive_failures` and set `next_check_at` to 30 minutes, 2 hours, 6 hours, then 24 hours (maximum) ahead.
- **Failing.** At 3 consecutive failures, set `status = 'failing'` and send one `failing` notification to each watcher.
- **Dead.** When `last_success_at` (or `created_at`, if never successful) is more than 7 days ago and the product is still failing, set `status = 'dead'`, notify watchers, and stop scheduling it. Users can remove and re-add it.
- **Recovery.** Any success resets `consecutive_failures` and sets `status = 'active'`.
- **Freshness.** Product pages show "last checked successfully …" so stale data is never shown as current.
- **Status page.** `/status` shows per-plug-in success rate over the last 24 hours and 7 days, plus product counts by status.

### Shopee and Lazada feasibility test

Before the main build, a throwaway test checks whether product prices can be read from a cloud server without logging in and within the sites' terms. If yes, build their plug-ins. If not, document the finding in the README. That test's code is not kept.

## 6. Security and abuse limits

- **URL safety (SSRF).** Only `http` and `https` on ports 80 and 443. Resolve the hostname and refuse private, loopback, link-local, multicast, reserved and cloud metadata addresses (IPv4 and IPv6). Re-check on every redirect (maximum 5 redirects). Connect to the address that was checked, so DNS can't change between check and connect. Response bodies are capped at 2 MB, and requests time out after 10 seconds.
- **User limits.** 20 watches per user; 10 new products per user per day; 20 bot commands per user per minute.
- **Webhook.** The Telegram webhook is registered with a secret token. Requests without the matching `X-Telegram-Bot-Api-Secret-Token` header are rejected.
- **Privacy.** Public pages show only product data, never watchers or counts tied to users.
- **Secrets.** Bot token, webhook secret and database password come from environment variables only, with a committed `.env.example`.

## 7. Bot commands

| Command | Behaviour |
|---|---|
| `/start` | Register and explain usage |
| *(a product link)* | Add a watch; reply with title, current price and page link once the first check succeeds |
| `/list` | List watches with current price and page link |
| `/target <n> <price>` | Set a target price for watch number *n* in `/list`; `/target <n> off` clears it |
| `/remove <n>` | Remove watch *n* |
| `/check <n>` | Request an immediate check (rate-limited) |
| `/help` | Usage |

New products are checked straight away: the first check is scheduled immediately, and the reply is sent as a notification when it completes.

## 8. Web pages

- `/p/{public_id}`: title, image, current price, shop link, last successful check, and a Chart.js step chart of price history.
- `/status`: plug-in health and product counts.
- `/`: short description and link to the bot.

Server-rendered with Jinja2. Works on phone width.

## 9. Testing

pytest, run in GitHub Actions against a real PostgreSQL 16 service container.

- **Plug-ins:** parse saved HTML fixtures of real product pages; no network in tests.
- **Alert rules:** one test per rule and transition, using an injected clock.
- **Concurrency:** 3 workers race over 200 due products; each product is checked exactly once.
- **Outbox:** a fake Telegram client fails, then recovers; every notification is delivered exactly once.
- **URL safety:** private, loopback, metadata and IPv6 local addresses are refused, including via redirect.
- **Bot commands:** handled against a fake Telegram client.
- **Failure handling:** backoff steps, failing/dead transitions and recovery.

## 10. Deployment

- One small DigitalOcean server (Student Pack credit) running Docker Compose.
- Domain from the Student Pack (Namecheap), with DuckDNS as a free fallback. Caddy handles HTTPS certificates.
- GitHub Actions: tests on every push; a manually triggered deploy workflow that connects over SSH, pulls, runs Alembic migrations and restarts the containers.
- Nightly `pg_dump` backup kept on the server for 7 days.

## 11. Build order

1. Shopee and Lazada feasibility test (throwaway)
2. Data model, migrations and the `schema_org` plug-in
3. Worker loop and alert rules
4. Notification sending (outbox)
5. Bot commands and webhook
6. Product pages and status page
7. Deployment
8. README with screenshots and a design-decisions section
