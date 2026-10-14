"""The background worker: checks due prices and sends alerts.

Run with `python -m app.worker`. Several copies can run at once; claim_due
makes sure they never check the same product at the same time.
"""

import functools
import logging
import random
import signal
import time
from collections.abc import Callable
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock, SystemClock
from app.config import settings
from app.db import make_engine, make_session_factory
from app.fetching.errors import FetchError
from app.fetching.plugins import fetch_snapshot
from app.fetching.snapshot import Snapshot
from app.models import CheckResult, Product
from app.notify.outbox import send_pending
from app.notify.telegram import LogOnlyTelegram, TelegramClient
from app.tracking.claim import claim_due
from app.tracking.pacing import HostPacer
from app.tracking.record import record_failure, record_success

log = logging.getLogger("worker")

CHECK_RESULTS_KEPT = timedelta(days=8)
IDLE_SLEEP_SECONDS = 5


def run_once(
    session_factory: sessionmaker[Session],
    *,
    clock: Clock,
    rng: random.Random,
    snapshot_fn: Callable[[str], Snapshot],
    pacer: HostPacer,
    telegram: TelegramClient,
    base_url: str,
) -> int:
    """Check every product that is due (one batch), send alerts, tidy up."""
    with session_factory() as session:
        ids = claim_due(session, clock.now())

    for product_id in ids:
        _check_one(session_factory, product_id, clock=clock, rng=rng, snapshot_fn=snapshot_fn, pacer=pacer)

    send_pending(session_factory, telegram, clock.now(), base_url=base_url)

    with session_factory() as session:
        session.execute(delete(CheckResult).where(CheckResult.checked_at < clock.now() - CHECK_RESULTS_KEPT))
        session.commit()
    return len(ids)


def _check_one(session_factory, product_id, *, clock, rng, snapshot_fn, pacer) -> None:
    started = time.monotonic()
    with session_factory() as session:
        product = session.get(Product, product_id)
        url, shop = product.url, product.shop
        pacer.wait(urlsplit(url).hostname or "")
        try:
            snapshot = snapshot_fn(url)
        except FetchError as e:
            outcome = e.kind
            record_failure(session, product_id, e, clock.now())
        except Exception as e:
            # A bug in one plug-in must not stop every other product's checks.
            log.exception("unexpected error checking product %s", product_id)
            outcome = "internal"
            record_failure(session, product_id, FetchError(f"{type(e).__name__}: {e}", kind="internal"), clock.now())
        else:
            outcome = "ok"
            record_success(session, product_id, snapshot, clock.now(), rng)
    log.info("checked product=%s shop=%s result=%s in %.1fs", product_id, shop, outcome, time.monotonic() - started)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    session_factory = make_session_factory(make_engine(settings.database_url))
    if settings.telegram_bot_token:
        telegram = TelegramClient(settings.telegram_bot_token, httpx.Client())
    else:
        log.warning("TELEGRAM_BOT_TOKEN is not set: alerts will be logged, not sent")
        telegram = LogOnlyTelegram()
    deps = dict(
        clock=SystemClock(),
        rng=random.Random(),
        snapshot_fn=functools.partial(fetch_snapshot, user_agent=settings.user_agent),
        pacer=HostPacer(),
        telegram=telegram,
        base_url=settings.public_base_url,
    )

    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        log.info("received signal %s, finishing current batch", signum)
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log.info("worker started")
    while not stopping:
        try:
            checked = run_once(session_factory, **deps)
        except Exception:
            # e.g. the database restarting; wait and try again rather than exit.
            log.exception("worker loop error")
            checked = 0
        if not checked and not stopping:
            time.sleep(IDLE_SLEEP_SECONDS)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
