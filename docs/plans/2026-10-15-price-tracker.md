# MY Price Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a public Telegram bot that tracks Malaysian online shop prices, alerts users on drops, target prices and restocks, and serves a price-history page per product.

**Architecture:** A FastAPI `web` process (Telegram webhook and HTML pages) and a `worker` process share PostgreSQL. The worker claims due products with `FOR UPDATE SKIP LOCKED` and a lease, fetches through shop plug-ins behind an SSRF-safe HTTP client, saves price changes and alert rows in one transaction (transactional outbox), and a sender delivers the alerts to Telegram.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, psycopg 3, httpx, selectolax, Jinja2, Chart.js (CDN), pytest, Docker Compose, Caddy, GitHub Actions.

**Spec:** `docs/specs/2026-10-15-price-tracker-design.md`. Read it before starting any task.

## Global Constraints

- Python 3.12 in Docker and CI (`requires-python = ">=3.12"`); code must also run on the developer's local 3.14.
- Money is integer sen everywhere inside the app; convert only at the edges (`parse_ringgit`, `format_sen`).
- All datetimes are timezone-aware UTC. Get "now" from the injected `Clock`, never from `datetime.now()` inside logic.
- Tests needing a database run against real PostgreSQL 16 via `TEST_DATABASE_URL` (no SQLite: `SKIP LOCKED` is the point). Pure-logic tests need no database. Tests never touch the network.
- Values fixed by the spec: check interval 6 h ± 20% jitter; lease 10 min; claim batch 10; backoff 30 min → 2 h → 6 h → 24 h max; `failing` at 3 consecutive failures; `dead` after 7 days without success; 20 watches per user; 10 new watches per user per 24 h; 20 bot commands per user per minute; `/check` at most once per hour per product; fetch timeout 10 s, body cap 2 MB, max 5 redirects, ports 80/443 only; `public_id` 8 random characters.
- Only MYR prices are accepted.
- Secrets come from environment variables only; commit `.env.example`, never `.env`.
- Bot messages use Telegram `parse_mode=HTML`; every user- or shop-supplied string is HTML-escaped.
- Follow the style of `hkm4lmn/retail-stock-tracker`: `requirements.txt` + `requirements-dev.txt`, `app/` package, `app/config.py` with pydantic-settings, plain module docstrings, short comments that explain *why*.
- Commits are authored as `Hakim Al-Amin <hkm4lmn@users.noreply.github.com>` (already set in the repo's local git config) and end with the `Co-Authored-By` line.

## Review Focus

1. **Products with no watchers.** Once every user removes a product, it must stop being checked. The spec doesn't say this. → Task 6 test `test_claim_skips_products_without_watches`.
2. **Price text formats.** `"RM1,299.00"`, `"1299"`, `"1,299.5"`, and JSON-LD `AggregateOffer` with `lowPrice` must all parse to the right sen; a price range uses the lowest. → Task 2 `test_parse_ringgit_formats`, Task 4 `test_aggregate_offer_uses_low_price`.
3. **Out of stock with no price shown.** Must record `in_stock = False` and keep the last known price, not fail the check. → Task 4 `test_out_of_stock_without_price`, Task 6 `test_record_success_keeps_price_when_snapshot_price_missing`.
4. **Product titles with `<`, `&` or emoji.** These must not break Telegram HTML or the web page. → Task 7 `test_render_escapes_title`.
5. **Links pasted inside a sentence, or the same product pasted in different URL forms.** "check this https://shop.my/x?utm_source=fb thanks" must add one product, deduplicated with `https://SHOP.my/x/`. → Task 2 `test_extract_url_from_sentence`, `test_normalize_url_variants_match`.

---

## File Structure

```
requirements.txt, requirements-dev.txt, pyproject.toml   # deps; pytest config only
.env.example, .gitignore, LICENSE
alembic.ini, migrations/env.py, migrations/versions/0001_initial.py
app/
  config.py            Settings
  db.py                Base, make_engine, make_session_factory
  clock.py             Clock, SystemClock
  models.py            User, Product, Watch, PricePoint, Notification, CheckResult
  money.py             parse_ringgit, format_sen
  urls.py              normalize_url, extract_url, InvalidUrl
  fetching/
    errors.py          FetchError and subclasses
    safe_http.py       fetch, FetchedPage, is_public_ip
    snapshot.py        Snapshot
    plugins/__init__.py   Plugin protocol, plugin_for, PLUGINS
    plugins/schema_org.py SchemaOrgPlugin
  tracking/
    schedule.py        next_check_after_success, backoff_delay
    alerts.py          WatchState, AlertDraft, evaluate_watch
    claim.py           claim_due
    record.py          record_success, record_failure
    pacing.py          HostPacer
  notify/
    telegram.py        TelegramClient, TelegramBlocked, TelegramError
    messages.py        render
    outbox.py          enqueue, send_pending
  worker.py            run_once, main
  bot/
    limits.py          CommandLimiter
    commands.py        handle_update, Reply
  web/
    main.py            create_app
    stats.py           plugin_health, product_counts
    templates/         base.html, home.html, product.html, status.html
scripts/set_webhook.py
deploy/docker-compose.prod.yml, deploy/Caddyfile, deploy/backup.sh
Dockerfile, docker-compose.yml
.github/workflows/tests.yml, .github/workflows/deploy.yml
tests/  (one test file per app module, plus conftest.py, fakes.py, fixtures/)
```

Three small additions to the spec, made here because the spec needs them to work:
- `products.last_manual_check_at` enforces the `/check` cooldown.
- A `check_results` table (`id, shop, ok, error_kind, checked_at`, pruned after 8 days) gives `/status` its success rates; the spec's data model has no record of individual checks.
- Notification kind `tracking_started` delivers "the reply sent when the first check completes" (spec §7).

---

### Task 1: Shopee and Lazada feasibility test (throwaway)

**Files:**
- Create: `docs/findings/2026-10-shopee-lazada.md`
- Throwaway scripts live in the session scratchpad, never in the repo.

- [ ] **Step 1: Read terms and robots.txt.** Fetch `https://shopee.com.my/robots.txt`, `https://www.lazada.com.my/robots.txt`, and each site's Terms of Service. Record whether automated access to product pages is prohibited, quoting the clause.
- [ ] **Step 2: Try one public product page per site** with plain httpx (normal browser user agent, no login, no cookies from a real account, no CAPTCHA solving, no request to private APIs that need signing). Record HTTP status, whether price data is present in the HTML or JSON-LD, and whether a challenge page appears. Run from the local machine; note that a cloud server IP is likely to do worse.
- [ ] **Step 3: Write the findings** with a verdict per site: `build plug-in` only if the terms allow it **and** the price is readable without login or anti-bot workarounds; otherwise `do not build`, with the reason in one paragraph for the README.
- [ ] **Step 4: Report to the user and wait for agreement** before adding any Shopee or Lazada plug-in. If a plug-in is approved, it is an extra task after Task 4, following the same `Plugin` interface and fixture-based tests.
- [ ] **Step 5: Commit** `docs/findings/2026-10-shopee-lazada.md` with message `docs: record Shopee and Lazada feasibility findings`.

### Task 2: Project skeleton, money and URLs

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `.gitignore`, `.env.example`, `LICENSE` (MIT, Hakim Al-Amin, 2026), `app/__init__.py`, `app/config.py`, `app/clock.py`, `app/money.py`, `app/urls.py`, `tests/__init__.py`, `tests/test_money.py`, `tests/test_urls.py`, `.github/workflows/tests.yml`

**Interfaces:**
- Produces:
  - `app.config.Settings` fields (env names are upper-case): `database_url: str`, `telegram_bot_token: str = ""`, `telegram_webhook_secret: str = ""`, `public_base_url: str = "http://localhost:8000"`, `bot_username: str = ""`, `user_agent: str = "MYPriceTracker/1.0 (+https://github.com/hkm4lmn/my-price-tracker)"`; `settings = Settings()`.
  - `app.clock.Clock` (Protocol with `now() -> datetime`), `SystemClock`.
  - `app.money.parse_ringgit(text: str) -> int` (raises `ValueError`), `app.money.format_sen(sen: int) -> str`.
  - `app.urls.InvalidUrl(ValueError)`, `normalize_url(raw: str) -> str`, `extract_url(text: str) -> str | None`.

- [ ] **Step 1: Write dependency files.** `requirements.txt`: `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `sqlalchemy>=2.0`, `alembic>=1.13`, `psycopg[binary]>=3.2`, `pydantic-settings>=2.3`, `httpx>=0.27`, `selectolax>=0.3`, `jinja2>=3.1`. `requirements-dev.txt`: `-r requirements.txt`, `pytest>=8`. `pyproject.toml` holds only `[tool.pytest.ini_options] testpaths = ["tests"]`.
- [ ] **Step 2: Write failing tests.**
  - `test_parse_ringgit_formats`: `"RM1,299.00"→129900`, `"RM 72.90"→7290`, `"1299"→129900`, `"1,299.5"→129950`, `"72.9"→7290`, `" rm12 "→1200`, the number `72.9` passed as `"72.9"` from JSON → `7290`.
  - `test_parse_ringgit_rejects`: `""`, `"free"`, `"-5"`, `"1.234"` (three decimals) raise `ValueError`.
  - `test_format_sen`: `129900→"RM 1,299.00"`, `5→"RM 0.05"`.
  - `test_normalize_url_variants_match`: `https://SHOP.my/x/`, `https://shop.my/x?utm_source=fb&utm_medium=x`, `https://shop.my/x#reviews` all → `https://shop.my/x`; non-tracking query params are kept and sorted (`?b=2&a=1` → `?a=1&b=2`); `fbclid`, `gclid`, `utm_*` are removed.
  - `test_normalize_url_rejects`: `ftp://x.my/a`, `javascript:alert(1)`, `https://x.my:8443/a`, `https://user:pw@x.my/a`, `not a url` raise `InvalidUrl`.
  - `test_extract_url_from_sentence`: `"check this https://shop.my/x?utm_source=fb thanks"` → `"https://shop.my/x?utm_source=fb"`; trailing `)`, `.`, `,` are stripped; `"no link here"` → `None`; with two links, the first is returned.
- [ ] **Step 3: Run** `pytest -v` and confirm these tests fail on import.
- [ ] **Step 4: Implement** `money.py` with `Decimal` (never float) and `urls.py` with `urllib.parse`. Normalisation: lower-case scheme and host, drop default port, drop fragment, strip one trailing `/` from a non-root path, remove tracking params, sort remaining params. Reject userinfo and any port other than 80/443.
- [ ] **Step 5: Run** `pytest -v` and confirm everything passes.
- [ ] **Step 6: Add CI.** `.github/workflows/tests.yml` follows the retail tracker's workflow (Python 3.12, pip cache, `postgres:16` service with user/password/db `tracker`/`tracker`/`tracker_test`, healthcheck) without the SQLite matrix, and sets `TEST_DATABASE_URL=postgresql+psycopg://tracker:tracker@localhost:5432/tracker_test`.
- [ ] **Step 7: Commit** with message `feat: project skeleton, money and URL helpers`.

### Task 3: Data model, migrations and test database

**Files:**
- Create: `app/db.py`, `app/models.py`, `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_initial.py`, `docker-compose.yml` (dev: `db` service only for now, port 5432, user/password `tracker`, databases `tracker` and `tracker_test` via an init script), `tests/conftest.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: `Settings.database_url`.
- Produces:
  - `app.db.Base`, `make_engine(url: str) -> Engine`, `make_session_factory(engine) -> sessionmaker[Session]` (`expire_on_commit=False`).
  - `app.models`: `User`, `Product`, `Watch`, `PricePoint`, `Notification`, `CheckResult`, with columns exactly as in spec §3 plus `Product.last_manual_check_at` and the `check_results` table above. String constants: `ProductStatus.ACTIVE/FAILING/DEAD = "active"/"failing"/"dead"`; `NotificationKind` values `price_drop, target_reached, back_in_stock, failing, dead, tracking_started`.
  - `app.models.new_public_id() -> str`: 8 characters from `secrets.choice` over lower-case letters and digits.
  - Fixtures in `tests/conftest.py`: `engine` (skips the test with a clear message if `TEST_DATABASE_URL` is unset; runs `alembic upgrade head` once per session and truncates all tables before each test), `session_factory`, `session`.
- Database constraints: `products.url` unique, `products.public_id` unique, `watches (user_id, product_id)` unique, `users.telegram_chat_id` unique, `CHECK (current_price_sen >= 0)`, `CHECK (price_sen >= 0)`, `CHECK (target_price_sen > 0)`. Indexes: `products (next_check_at)`, `price_points (product_id, observed_at)`, `notifications (next_attempt_at) WHERE sent_at IS NULL`, `check_results (checked_at)`. Foreign keys from `watches`, `notifications` and `price_points` cascade on delete.

- [ ] **Step 1: Write failing tests** in `tests/test_models.py`: `test_migration_creates_tables` (all six tables exist), `test_product_url_unique`, `test_watch_unique_per_user_and_product`, `test_negative_price_rejected` (each expects `IntegrityError`), `test_public_id_shape` (8 chars, `[a-z0-9]`).
- [ ] **Step 2: Run** `docker compose up -d db`, then `TEST_DATABASE_URL=postgresql+psycopg://tracker:tracker@localhost:5432/tracker_test pytest tests/test_models.py -v`. Expected: FAIL (modules missing).
- [ ] **Step 3: Implement** `db.py` and `models.py`, then write `0001_initial.py` by hand to match the models (autogenerate may be used as a starting point, then reviewed). `migrations/env.py` reads `DATABASE_URL` from `Settings`, or from an `-x url=...` override that conftest uses.
- [ ] **Step 4: Run** the same command. Expected: PASS. Also run `alembic downgrade base && alembic upgrade head` against `tracker_test` with no errors.
- [ ] **Step 5: Commit** with message `feat: data model and initial migration`.

### Task 4: Safe fetching and the schema.org plug-in

**Files:**
- Create: `app/fetching/__init__.py`, `errors.py`, `safe_http.py`, `snapshot.py`, `plugins/__init__.py`, `plugins/schema_org.py`, `tests/test_safe_http.py`, `tests/test_schema_org.py`, `tests/fixtures/` (saved HTML)

**Interfaces:**
- Consumes: `normalize_url`, `InvalidUrl`, `parse_ringgit`.
- Produces:
  - `app.fetching.errors`: `FetchError(Exception)` with class attribute `kind: str`; subclasses `Blocked ("blocked")`, `NotFound ("not_found")`, `ParseError ("parse_error")`, `FetchTimeout ("timeout")`, `UnsafeUrl ("unsafe_url")`.
  - `app.fetching.safe_http`: `@dataclass(frozen=True) FetchedPage(url: str, status: int, text: str)`; `is_public_ip(ip: str) -> bool`; `fetch(url: str, *, user_agent: str, resolver: Callable[[str], list[str]] = default_resolver, transport: httpx.BaseTransport | None = None, timeout: float = 10.0, max_bytes: int = 2_000_000, max_redirects: int = 5) -> FetchedPage`.
  - `app.fetching.snapshot`: `@dataclass(frozen=True) Snapshot(title: str, price_sen: int | None, in_stock: bool, currency: str, image_url: str | None)`. `price_sen` may be `None` only when `in_stock` is `False`.
  - `app.fetching.plugins`: `Plugin` Protocol with `name: str`, `matches(host: str) -> bool`, `parse(page: FetchedPage) -> Snapshot`; `PLUGINS: list[Plugin]` with `SchemaOrgPlugin` last as the catch-all; `plugin_for(url: str) -> Plugin`; `fetch_snapshot(url: str, *, user_agent: str, fetcher=fetch) -> Snapshot`.

- [ ] **Step 1: Write failing tests for `safe_http`.** Use an `httpx.MockTransport` and a fake resolver, never real DNS or network.
  - `test_is_public_ip`: `False` for `10.0.0.1`, `172.16.5.4`, `192.168.1.1`, `127.0.0.1`, `169.254.169.254`, `0.0.0.0`, `100.64.0.1`, `224.0.0.1`, `::1`, `fe80::1`, `fc00::1`, `::ffff:127.0.0.1`; `True` for `8.8.8.8` and `2606:4700:4700::1111`.
  - `test_fetch_refuses_private_resolution`: a host resolving to `127.0.0.1` raises `UnsafeUrl`, and the transport is never called.
  - `test_fetch_refuses_if_any_address_private`: the resolver returns `["8.8.8.8", "10.0.0.1"]` → `UnsafeUrl`.
  - `test_fetch_refuses_redirect_to_private`: the first response is a 302 to `http://internal.test/`, which resolves privately → `UnsafeUrl`.
  - `test_fetch_too_many_redirects`: 6 redirects → `Blocked`.
  - `test_fetch_connects_to_checked_ip`: the request sent to the transport targets the resolved IP, carries a `Host` header with the original host, and for https sets the `sni_hostname` extension to it.
  - `test_fetch_status_mapping`: 403 and 429 → `Blocked`; 404 and 410 → `NotFound`; 500 → `FetchError` with kind `"http_error"`; a transport timeout → `FetchTimeout`.
  - `test_fetch_truncates_large_body`: a 3 MB body raises `ParseError("response too large")`.
- [ ] **Step 2: Write failing tests for the plug-in** with fixtures saved from real Malaysian shop pages (at least two different shops that publish JSON-LD; strip any personal data from the saved files and keep each under 200 KB) plus small hand-written fixtures.
  - `test_parses_real_fixture_<shop>` per saved page: exact title, price and stock values.
  - `test_jsonld_in_graph_and_list`: a Product inside `@graph`, and inside a top-level list.
  - `test_aggregate_offer_uses_low_price`: `lowPrice "59.90"`, `highPrice "89.90"` → `5990`.
  - `test_availability_mapping`: `https://schema.org/OutOfStock`, `http://schema.org/SoldOut` and `OutOfStock` → `False`; `InStock` and a missing value → `True`.
  - `test_out_of_stock_without_price`: no price, `OutOfStock` → `Snapshot(price_sen=None, in_stock=False)`.
  - `test_missing_price_in_stock_is_parse_error`.
  - `test_non_myr_is_parse_error`: `priceCurrency "SGD"` → `ParseError`; a missing currency is treated as MYR.
  - `test_opengraph_fallback`: no JSON-LD, but `og:title` with `product:price:amount` and `product:price:currency` meta tags → parsed.
  - `test_no_product_data_is_parse_error`.
- [ ] **Step 3: Run** `pytest tests/test_safe_http.py tests/test_schema_org.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement `fetch`.** Loop over at most `max_redirects + 1` hops. Each hop: `normalize_url` (`InvalidUrl` → `UnsafeUrl`), resolve the host, require every address to pass `is_public_ip`, then send the request to the first address with the original `Host` header and `extensions={"sni_hostname": host}`. Stream the body and stop once `max_bytes` is exceeded. Use `follow_redirects=False` and handle `Location` manually. `is_public_ip` uses `ipaddress` (`is_global`, plus explicit unwrapping of IPv4-mapped IPv6).
- [ ] **Step 5: Implement `SchemaOrgPlugin.parse`** with selectolax: collect `script[type="application/ld+json"]`, ignore blocks with invalid JSON, find the first node whose `@type` is or contains `Product`, and read `name`, `image` (first if a list), and `offers` (dict, list, or `AggregateOffer`; for a list, use the lowest-priced offer). Parse prices with `parse_ringgit(str(value))`. Fall back to OpenGraph meta tags.
- [ ] **Step 6: Run** the same tests. Expected: PASS.
- [ ] **Step 7: Commit** with message `feat: SSRF-safe fetching and schema.org product plug-in`.

### Task 5: Scheduling and alert rules (pure logic)

**Files:**
- Create: `app/tracking/__init__.py`, `app/tracking/schedule.py`, `app/tracking/alerts.py`, `tests/test_schedule.py`, `tests/test_alerts.py`

**Interfaces:**
- Produces:
  - `next_check_after_success(now: datetime, rng: random.Random) -> datetime`: `now + 6 h × uniform(0.8, 1.2)`.
  - `backoff_delay(consecutive_failures: int) -> timedelta`: `1 → 30 min`, `2 → 2 h`, `3 → 6 h`, `≥4 → 24 h`.
  - `@dataclass WatchState(target_price_sen: int | None, baseline_price_sen: int | None, last_alerted_price_sen: int | None, target_armed: bool)`.
  - `@dataclass(frozen=True) AlertDraft(kind: str, payload: dict)`.
  - `evaluate_watch(state: WatchState, *, prev_price_sen: int | None, prev_in_stock: bool | None, price_sen: int, in_stock: bool) -> tuple[WatchState, list[AlertDraft]]`. This is pure and never mutates its input. `price_sen` is the effective price after record keeping (the last known price when the snapshot had none).

Rules (spec §4): if `baseline_price_sen` is `None`, this is the watch's first check, so set the baseline to `price_sen` and emit only `tracking_started`. No target → `price_drop` when `price_sen < (last_alerted_price_sen or baseline_price_sen)`, then set `last_alerted_price_sen`. Target set → `target_reached` once when `price_sen <= target` and armed, then disarm; re-arm when `price_sen > target`; never `price_drop`. `back_in_stock` when `prev_in_stock is False and in_stock`. Drop and target alerts only fire while `in_stock` is true. Payload keys: `old_price_sen`, `new_price_sen`, `target_price_sen` (where relevant).

- [ ] **Step 1: Write failing tests:** `test_backoff_steps` (exact values above); `test_next_check_jitter_bounds` (1,000 seeded draws within [4 h 48 m, 7 h 12 m]); `test_first_check_sets_baseline_and_tracking_started`; `test_drop_alerts_and_updates_last_alerted`; `test_no_alert_on_rise_or_same`; `test_second_drop_compares_to_last_alerted_not_baseline`; `test_target_fires_once_then_rearms_after_rise`; `test_target_watch_gets_no_plain_drop`; `test_back_in_stock`; `test_no_price_alert_while_out_of_stock`; `test_evaluate_does_not_mutate_input`.
- [ ] **Step 2: Run** `pytest tests/test_schedule.py tests/test_alerts.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** with message `feat: check scheduling and alert rules`.

### Task 6: Claiming and recording checks

**Files:**
- Create: `app/tracking/claim.py`, `app/tracking/record.py`, `app/notify/__init__.py`, `app/notify/outbox.py` (only `enqueue` in this task), `tests/factories.py`, `tests/test_claim.py`, `tests/test_record.py`

**Interfaces:**
- Consumes: models, `Snapshot`, `FetchError`, `evaluate_watch`, `WatchState`, `next_check_after_success`, `backoff_delay`.
- Produces:
  - `claim_due(session: Session, now: datetime, *, limit: int = 10, lease: timedelta = timedelta(minutes=10)) -> list[int]`: commits before returning.
  - `record_success(session: Session, product_id: int, snapshot: Snapshot, now: datetime, rng: random.Random) -> None` and `record_failure(session: Session, product_id: int, error: FetchError, now: datetime) -> None`: each runs as one transaction and commits.
  - `enqueue(session: Session, *, user_id: int, kind: str, payload: dict, now: datetime) -> None`: adds a row with `next_attempt_at = now`, without committing.
  - `tests/factories.py`: `make_user(session, chat_id=None) -> User`, `make_product(session, **overrides) -> Product`, `make_watch(session, user, product, **overrides) -> Watch`.

Claim query: `status != 'dead' AND next_check_at <= now AND EXISTS (watch for product)`, ordered by `next_check_at`, `LIMIT`, `FOR UPDATE SKIP LOCKED`, then set `next_check_at = now + lease`.

`record_success` locks the product row (`FOR UPDATE`) and the watches. Effective price is `snapshot.price_sen`, or `product.current_price_sen` when that is `None`. If both are `None`, treat it as a failure (`ParseError`). Insert a `PricePoint` only if the effective price or stock differs from the product's current values (or the product has never succeeded). Update the product (title, image, price, stock, `last_checked_at`, `last_success_at`, `status=active`, `consecutive_failures=0`, `last_error=None`, `next_check_at`). Run `evaluate_watch` per watch and `enqueue` each draft, adding `product_title`, `public_id` and `url` to the payload. Insert a `CheckResult(ok=True)`.

`record_failure` increments failures, sets `last_checked_at`, `last_error=f"{kind}: {message[:200]}"` and `next_check_at = now + backoff_delay(n)`. At exactly 3 failures it sets `failing` and enqueues `failing` per watcher. If the product is still failing and `now - (last_success_at or created_at) > 7 days`, it sets `dead` and enqueues `dead` per watcher (once). Insert a `CheckResult(ok=False, error_kind=kind)`.

- [ ] **Step 1: Write failing tests.**
  - `test_claim_returns_due_and_leases`: due products are returned and their `next_check_at` becomes `now + 10 min`; a second claim at the same `now` returns nothing.
  - `test_claim_skips_dead_and_future`.
  - `test_claim_skips_products_without_watches`.
  - `test_claim_respects_limit_and_order`.
  - `test_concurrent_claims_never_overlap`: 200 due products and 3 threads, each with its own session, call `claim_due(limit=10)` in a loop until empty. The union has 200 ids, there are no duplicates, and the threads start together on a `threading.Barrier`.
  - `test_record_success_first_check_writes_point_and_tracking_started`.
  - `test_record_success_unchanged_price_writes_no_point`.
  - `test_record_success_drop_enqueues_price_drop_in_same_transaction`: patch `enqueue` to raise, then assert that neither the price point nor the price update was saved.
  - `test_record_success_keeps_price_when_snapshot_price_missing`.
  - `test_record_failure_backoff_and_failing_once`: failures 1, 2 and 3 → delays 30 min, 2 h, 6 h; `failing` is enqueued exactly once, on the third.
  - `test_record_failure_marks_dead_after_7_days`.
  - `test_success_after_failing_resets_to_active`.
- [ ] **Step 2: Run** `pytest tests/test_claim.py tests/test_record.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `claim.py`, `record.py` and `enqueue`.
- [ ] **Step 4: Run** the same command. Expected: PASS. Then temporarily delete `skip_locked=True` and confirm `test_concurrent_claims_never_overlap` fails or deadlocks within its timeout (the test joins its threads with a 30 s timeout so a deadlock fails instead of hanging). Restore it.
- [ ] **Step 5: Commit** with message `feat: claim due checks and record results with outbox`.

### Task 7: Telegram client, messages and the outbox sender

**Files:**
- Create: `app/notify/telegram.py`, `app/notify/messages.py`; modify `app/notify/outbox.py` (add `send_pending`); create `tests/fakes.py`, `tests/test_messages.py`, `tests/test_outbox.py`, `tests/test_telegram.py`

**Interfaces:**
- Consumes: `Notification`, `User`, `format_sen`, `Settings.public_base_url`.
- Produces:
  - `TelegramClient(token: str, http: httpx.Client)`: `send_message(chat_id: int, html: str) -> None` (POST `sendMessage` with `parse_mode=HTML`, `disable_web_page_preview=True`); `set_webhook(url: str, secret: str) -> None`. Raises `TelegramBlocked` when the API returns 403, or 400 with "chat not found"; raises `TelegramError` otherwise.
  - `render(kind: str, payload: dict, *, base_url: str) -> str`: HTML-escaped, one message per kind, each with a link to `{base_url}/p/{public_id}`.
  - `send_pending(session_factory, client: TelegramClient, now: datetime, *, limit: int = 50) -> int`: returns the number sent.
  - `tests/fakes.py`: `FakeTelegram` records sent messages, and can be scripted to raise `TelegramError` for the next *n* calls or `TelegramBlocked` for a given chat id. `FakeClock(now)` with `advance(timedelta)`.

Message copy, which the implementer may polish but must keep these facts in:
- `tracking_started`: "✅ Now tracking <b>{title}</b>. Current price: {price}. History: {link}"
- `price_drop`: "📉 <b>{title}</b>: {old} → {new}. {link}"
- `target_reached`: "🎯 <b>{title}</b> is {new}, at or below your target of {target}. {link}"
- `back_in_stock`: "📦 <b>{title}</b> is back in stock at {new}. {link}"
- `failing`: "⚠️ I can't read the price for <b>{title}</b> right now. I'll keep trying."
- `dead`: "❌ I've stopped tracking <b>{title}</b> after 7 days of failed checks. Send the link again to retry."

Sender: each notification is claimed with `FOR UPDATE SKIP LOCKED` in its own transaction and sent; on success `sent_at = now`. On `TelegramError`, increment `attempts` and set `next_attempt_at = now + min(2 ** attempts minutes, 2 h)`; after 10 attempts, set `next_attempt_at = NULL` (abandoned, kept for inspection). On `TelegramBlocked`, set `user.is_blocked = True` and delete that user's unsent notifications. Never send to blocked users.

- [ ] **Step 1: Write failing tests.**
  - `test_render_escapes_title`: title `Cable <USB-C> & "Fast" 🔌` appears as `Cable &lt;USB-C&gt; &amp; "Fast" 🔌`, and the message includes the page link.
  - `test_render_each_kind`.
  - `test_telegram_client_request_shape`: via `httpx.MockTransport`, checks the URL path `/bot{token}/sendMessage` and the JSON body.
  - `test_telegram_client_maps_403_to_blocked`.
  - `test_send_pending_marks_sent`.
  - `test_outbox_retries_then_delivers_exactly_once`: `FakeTelegram` fails 3 times; advance the clock between calls; finally exactly one message has been delivered and `sent_at` is set.
  - `test_blocked_user_flagged_and_queue_dropped`.
  - `test_abandons_after_10_attempts`.
  - `test_two_senders_do_not_double_send`: 30 notifications and 2 threads; the fake records each id exactly once.
- [ ] **Step 2: Run** `pytest tests/test_messages.py tests/test_telegram.py tests/test_outbox.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the three modules.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** with message `feat: Telegram client and transactional outbox sender`.

### Task 8: Worker loop and per-shop pacing

**Files:**
- Create: `app/tracking/pacing.py`, `app/worker.py`, `tests/test_pacing.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes: `claim_due`, `record_success`, `record_failure`, `fetch_snapshot`, `send_pending`, `TelegramClient`.
- Produces:
  - `HostPacer(min_gap_seconds: float = 3.0, *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep)`, with `wait(host: str) -> None`.
  - `run_once(session_factory, *, clock: Clock, rng: random.Random, snapshot_fn: Callable[[str], Snapshot], pacer: HostPacer, telegram: TelegramClient) -> int`: claims, checks each product (catching `FetchError` → `record_failure`; any other exception is logged and recorded as `FetchError` kind `"internal"`, so one bad page never stops the loop), calls `send_pending`, deletes `check_results` older than 8 days, and returns the number of products checked.
  - `main() -> None`: builds the real dependencies from `Settings`, loops `run_once`, sleeps 5 s when nothing was due, handles SIGTERM cleanly, and logs one line per check (product id, shop, ok/error kind, duration).

- [ ] **Step 1: Write failing tests:** `test_pacer_waits_between_same_host` (fake clock and sleep: second call to the same host sleeps 3 s; a different host doesn't sleep); `test_run_once_success_and_failure_paths` (two due products, the snapshot function returns one `Snapshot` and raises `Blocked` for the other, both are recorded, and the alert is delivered via `FakeTelegram`); `test_run_once_survives_unexpected_exception`; `test_run_once_prunes_old_check_results`.
- [ ] **Step 2: Run** `pytest tests/test_pacing.py tests/test_worker.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules. `main()` is not unit-tested; it is exercised in Task 11.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** with message `feat: worker loop with per-shop pacing`.

### Task 9: Bot commands and webhook

**Files:**
- Create: `app/bot/__init__.py`, `app/bot/limits.py`, `app/bot/commands.py`, `app/web/__init__.py`, `app/web/main.py` (webhook and `/healthz` only in this task), `tests/test_limits.py`, `tests/test_commands.py`, `tests/test_webhook.py`

**Interfaces:**
- Consumes: models, `extract_url`, `normalize_url`, `plugin_for`, `new_public_id`, `format_sen`, `TelegramClient`.
- Produces:
  - `CommandLimiter(per_minute: int = 20, clock: Callable[[], float] = time.monotonic)` with `allow(chat_id: int) -> bool` (in-memory sliding window; documented as valid because `web` runs as a single process).
  - `@dataclass(frozen=True) Reply(chat_id: int, html: str)`.
  - `handle_update(session: Session, update: dict, *, now: datetime, base_url: str) -> list[Reply]`.
  - `create_app(*, session_factory, telegram: TelegramClient, settings: Settings, limiter: CommandLimiter | None = None) -> FastAPI` with `POST /telegram/webhook` (rejects a missing or wrong `X-Telegram-Bot-Api-Secret-Token` with 403; otherwise always returns 200 so Telegram doesn't retry, sending replies via `telegram.send_message`) and `GET /healthz` (checks a `SELECT 1`).

Behaviour (spec §7): every message upserts the `User` and clears `is_blocked`. Messages without text, and edited messages, are ignored. When a message contains a link: `normalize_url` (invalid → friendly error); refuse if the user has 20 watches ("limit of 20") or created 10 watches in the last 24 h; find or create the `Product` (`shop = plugin_for(url).name`, `public_id = new_public_id()`, `next_check_at = now`, and revive a `dead` product to `active` with failures reset). If already watching, say so. If the product already has a price, create the watch with `baseline_price_sen` set and reply with the current price and page link; otherwise create it with no baseline and reply "Checking the price now…", and `tracking_started` follows from the worker. `/list` numbers watches by `created_at`. `/target <n> <price>` parses with `parse_ringgit`, sets `target_armed = True`; `/target <n> off` clears it. `/remove <n>`. `/check <n>` sets `next_check_at = now` unless `last_manual_check_at` is within 60 min (the reply says when it's allowed). `/start` and `/help` show usage. Unknown commands → help. Invalid numbers → a short error naming `/list`.

- [ ] **Step 1: Write failing tests** for `handle_update` with a DB session and plain dict updates shaped like Telegram's: `test_start_registers_user`; `test_link_in_sentence_creates_product_and_watch`; `test_same_product_different_url_forms_dedupes`; `test_existing_priced_product_replies_with_price_and_sets_baseline`; `test_watch_limit_20`; `test_daily_new_watch_limit_10`; `test_already_watching`; `test_list_numbering_and_prices`; `test_target_set_and_off`; `test_target_bad_price`; `test_remove`; `test_check_cooldown_60_minutes`; `test_dead_product_revived_on_readd`; `test_unknown_command_shows_help`; `test_non_text_update_ignored`. For the limiter: `test_limiter_blocks_21st_in_a_minute_then_recovers`. For the webhook via `TestClient`: `test_webhook_rejects_bad_secret`, `test_webhook_sends_replies`, `test_webhook_rate_limited_reply_sent_once` (the 21st command gets one "slow down" reply; later ones in the same minute get none), `test_healthz`.
- [ ] **Step 2: Run** `pytest tests/test_limits.py tests/test_commands.py tests/test_webhook.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the modules.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** with message `feat: Telegram bot commands and webhook`.

### Task 10: Product pages and status page

**Files:**
- Create: `app/web/stats.py`, `app/web/templates/base.html`, `home.html`, `product.html`, `status.html`, `app/web/static/style.css`; modify `app/web/main.py`; create `tests/test_pages.py`, `tests/test_stats.py`

**Interfaces:**
- Consumes: models, `format_sen`, `Settings.bot_username`.
- Produces:
  - `plugin_health(session: Session, now: datetime) -> list[PluginHealth]`, where `@dataclass PluginHealth(shop: str, ok_24h: int, total_24h: int, ok_7d: int, total_7d: int)`.
  - `product_counts(session: Session) -> dict[str, int]`, keyed by status.
  - Routes: `GET /`, `GET /p/{public_id}` (404 page for an unknown id), `GET /status`.

The product page shows title, image, current price (`format_sen`), stock, a link to the shop (`rel="nofollow noopener"`), "last checked successfully {relative time}" with a visible warning when the product is `failing` or `dead`, and a Chart.js step chart (`stepped: true`). The chart data comes from all `price_points`, plus a final point at `last_success_at` carrying the last price, embedded as JSON through Jinja's `tojson` filter. It never shows watcher counts or anything user-related. The pages are server-rendered with autoescaping on, include a viewport meta tag, work at 360 px width, and adapt to light and dark mode. Chart.js is loaded from cdnjs with an SRI hash.

- [ ] **Step 1: Write failing tests:** `test_product_page_shows_price_and_chart_data` (parses the embedded JSON: points in time order, last point at `last_success_at`); `test_product_page_escapes_title`; `test_product_page_unknown_id_404`; `test_product_page_failing_warning`; `test_product_page_has_no_user_data` (seed a watch with a recognisable chat id and assert it does not appear); `test_status_page_counts`; `test_plugin_health_windows` (results at 1 h, 2 d and 9 d ago are counted correctly in the 24 h and 7 d windows); `test_home_links_bot`.
- [ ] **Step 2: Run** `pytest tests/test_pages.py tests/test_stats.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the stats functions, templates and routes.
- [ ] **Step 4: Run** the same command. Expected: PASS. Run `uvicorn` locally with seeded data (Step 5's script) and check a product page at phone width in a browser.
- [ ] **Step 5: Add `scripts/seed_demo.py`**, which inserts 3 demo products with 30 days of price history, for local screenshots only. It refuses to run unless `DATABASE_URL` contains `localhost` or `--force` is passed.
- [ ] **Step 6: Commit** with message `feat: product history and status pages`.

### Task 11: Containers and deployment

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `deploy/docker-compose.prod.yml`, `deploy/Caddyfile`, `deploy/backup.sh`, `deploy/README.md` (server setup steps), `scripts/set_webhook.py`, `.github/workflows/deploy.yml`; modify `docker-compose.yml` (add `web`, `worker`, `migrate` services for local use)

**Interfaces:**
- Consumes: `app.web.main.create_app`, `app.worker.main`, `TelegramClient.set_webhook`, Alembic.
- Produces: one image used by both `web` (`uvicorn app.web.main:app`; `app` is built by a module-level factory from `Settings`) and `worker` (`python -m app.worker`). A one-shot `migrate` service runs `alembic upgrade head`; `web` and `worker` depend on it completing successfully.

Production compose: `postgres:16` with a named volume and no published port; `web` published only to Caddy; Caddy on 80/443 with `{$DOMAIN}` and `reverse_proxy web:8000`; `restart: unless-stopped` everywhere; log rotation (`max-size: 10m`, `max-file: 3`). `backup.sh` runs `pg_dump` via `docker compose exec` to `/var/backups/price-tracker/YYYY-MM-DD.sql.gz` and deletes files older than 7 days. `deploy/README.md` documents the cron entry (`0 3 * * *`) and every server setup step: create the droplet, install Docker, configure the firewall (22, 80 and 443 only), point the domain, create `.env`, first start, run `scripts/set_webhook.py`. `deploy.yml`: `workflow_dispatch` only; uses repository secrets `DEPLOY_HOST`, `DEPLOY_USER` and `DEPLOY_SSH_KEY`; over SSH it runs `git pull`, `docker compose -f deploy/docker-compose.prod.yml up -d --build`, then prints `docker compose ps`.

- [ ] **Step 1: Build and run locally:** `docker compose up -d --build`. Expected: `migrate` exits 0, and `web` and `worker` are running.
- [ ] **Step 2: Verify:** `curl -fsS localhost:8000/healthz` returns 200; the `worker` logs show it polling; after `python scripts/seed_demo.py`, `localhost:8000/p/<id>` renders.
- [ ] **Step 3: Verify the prod compose file** with `docker compose -f deploy/docker-compose.prod.yml --env-file .env.example config`. Expected: valid config, with no published Postgres port.
- [ ] **Step 4: Commit** with message `chore: Docker images and deployment setup`.
- [ ] **Step 5: Live deployment (needs the user).** Stop and ask the user to: activate the GitHub Student Pack, create the DigitalOcean droplet and domain, create the bot with @BotFather, and add the deploy secrets. Then follow `deploy/README.md` together, run the deploy workflow, set the webhook, and confirm end to end from a real Telegram account: add a real product, receive `tracking_started`, open its page over HTTPS.

### Task 12: README

**Files:**
- Create: `README.md`, `docs/images/` (screenshots); modify the profile repo `hkm4lmn/hkm4lmn` `README.md` (side-projects table: remove "(in progress)" from MY Price Tracker, link the repo, update the description and stack; remove the URL Shortener and ML Model Comparison rows, which the user approved replacing)

- [ ] **Step 1: Write `README.md`** in the retail tracker's style: badges (tests workflow, Python, FastAPI, PostgreSQL), one-paragraph pitch, links to the live bot and an example product page, a "Why" paragraph in the user's voice (to be confirmed with the user), features, architecture diagram (from the spec), a **Design decisions** table (integer sen; Postgres as job queue with `SKIP LOCKED` and leases; transactional outbox; fetch once per product; change-only price points; SSRF protections; plug-in design and the Shopee/Lazada finding), a "Proving the guarantees" section (the concurrency and outbox tests, with the command to run them), quick start, configuration table, deployment summary linking `deploy/README.md`, limitations, and licence.
- [ ] **Step 2: Add screenshots:** a Telegram conversation (add, alert), a product page on a phone, and the status page.
- [ ] **Step 3: Verify** that every link and command in the README works by following the quick start from a fresh clone.
- [ ] **Step 4: Commit** with message `docs: README with design decisions`, then push.
- [ ] **Step 5: Update the profile README** in `hkm4lmn/hkm4lmn` as above. Show the user the diff and push only after they approve it.
