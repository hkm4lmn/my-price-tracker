import random
from datetime import timedelta

from sqlalchemy import func, select

from app.fetching.errors import Blocked
from app.fetching.snapshot import Snapshot
from app.models import CheckResult, Product
from app.tracking.pacing import HostPacer
from app.worker import run_once
from tests.factories import NOW, make_product, make_user, make_watch
from tests.fakes import FakeClock, FakeTelegram

BASE = "https://tracker.example.my"


def deps(snapshot_fn, clock=None, telegram=None):
    return dict(
        clock=clock or FakeClock(NOW),
        rng=random.Random(1),
        snapshot_fn=snapshot_fn,
        pacer=HostPacer(0, sleep=lambda s: None),
        telegram=telegram or FakeTelegram(),
        base_url=BASE,
    )


def two_watched_products(session):
    user = make_user(session, chat_id=55)
    good = make_product(session, url="https://good.my/a")
    bad = make_product(session, url="https://bad.my/b")
    make_watch(session, user, good)
    make_watch(session, user, bad)
    return good, bad


def test_run_once_success_and_failure_paths(session, session_factory):
    good, bad = two_watched_products(session)

    def snapshot_fn(url):
        if url == good.url:
            return Snapshot(title="Fan", price_sen=12900, in_stock=True, currency="MYR", image_url=None)
        raise Blocked("HTTP 403")

    tg = FakeTelegram()
    assert run_once(session_factory, **deps(snapshot_fn, telegram=tg)) == 2

    session.expire_all()
    good, bad = session.get(Product, good.id), session.get(Product, bad.id)
    assert good.current_price_sen == 12900
    assert bad.consecutive_failures == 1 and bad.last_error == "blocked: HTTP 403"
    # The tracking_started alert went out in the same run.
    assert len(tg.sent) == 1 and tg.sent[0][0] == 55 and "Fan" in tg.sent[0][1]


def test_run_once_nothing_due(session, session_factory):
    assert run_once(session_factory, **deps(lambda url: None)) == 0


def test_run_once_survives_unexpected_exception(session, session_factory):
    good, bad = two_watched_products(session)

    def snapshot_fn(url):
        if url == bad.url:
            raise KeyError("surprise")
        return Snapshot(title="Fan", price_sen=100, in_stock=True, currency="MYR", image_url=None)

    assert run_once(session_factory, **deps(snapshot_fn)) == 2
    session.expire_all()
    bad = session.get(Product, bad.id)
    assert bad.last_error.startswith("internal")
    assert session.get(Product, good.id).current_price_sen == 100


def test_run_once_paces_by_host(session, session_factory):
    user = make_user(session)
    for path in ("a", "b"):
        make_watch(session, user, make_product(session, url=f"https://same.my/{path}"))
    hosts = []

    class RecordingPacer:
        def wait(self, host):
            hosts.append(host)

    d = deps(lambda url: Snapshot(title="x", price_sen=1, in_stock=True, currency="MYR", image_url=None))
    d["pacer"] = RecordingPacer()
    run_once(session_factory, **d)
    assert hosts == ["same.my", "same.my"]


def test_run_once_prunes_old_check_results(session, session_factory):
    session.add_all(
        [
            CheckResult(shop="schema_org", ok=True, checked_at=NOW - timedelta(days=9)),
            CheckResult(shop="schema_org", ok=True, checked_at=NOW - timedelta(days=7)),
        ]
    )
    session.commit()
    run_once(session_factory, **deps(lambda url: None))
    remaining = session.scalar(select(func.count()).select_from(CheckResult))
    assert remaining == 1
