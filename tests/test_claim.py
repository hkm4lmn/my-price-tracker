import threading
from datetime import timedelta

from app.models import Product, Watch
from app.tracking.claim import claim_due
from tests.factories import NOW, make_product, make_user, make_watch


def watched_product(session, user=None, **overrides):
    user = user or make_user(session)
    product = make_product(session, **overrides)
    make_watch(session, user, product)
    return product


def test_claim_returns_due_and_leases(session):
    p = watched_product(session)
    assert claim_due(session, NOW) == [p.id]
    session.expire_all()
    assert session.get(Product, p.id).next_check_at == NOW + timedelta(minutes=10)
    # Leased: not claimable again until the lease runs out.
    assert claim_due(session, NOW) == []
    assert claim_due(session, NOW + timedelta(minutes=10)) == [p.id]


def test_claim_skips_dead_and_future(session):
    user = make_user(session)
    watched_product(session, user, status="dead")
    watched_product(session, user, next_check_at=NOW + timedelta(seconds=1))
    due = watched_product(session, user, status="failing")
    assert claim_due(session, NOW) == [due.id]


def test_claim_skips_products_without_watches(session):
    make_product(session)
    removed = watched_product(session)
    session.query(Watch).filter_by(product_id=removed.id).delete()
    session.commit()
    assert claim_due(session, NOW) == []


def test_claim_respects_limit_and_order(session):
    user = make_user(session)
    products = [
        watched_product(session, user, next_check_at=NOW - timedelta(minutes=m)) for m in range(5)
    ]
    oldest_first = [p.id for p in reversed(products)]
    assert claim_due(session, NOW, limit=3) == oldest_first[:3]
    assert claim_due(session, NOW, limit=3) == oldest_first[3:]


def test_concurrent_claims_never_overlap(session, session_factory):
    user = make_user(session)
    for _ in range(200):
        watched_product(session, user)

    barrier = threading.Barrier(3)
    claimed: list[list[int]] = [[], [], []]
    errors = []

    def worker(i):
        try:
            with session_factory() as s:
                barrier.wait()
                while ids := claim_due(s, NOW, limit=10):
                    claimed[i].extend(ids)
        except Exception as e:  # pragma: no cover - surfaced below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        # A deadlock fails the test instead of hanging the suite.
        t.join(timeout=30)
        assert not t.is_alive(), "claim_due deadlocked"

    assert errors == []
    everything = [pid for ids in claimed for pid in ids]
    assert len(everything) == 200
    assert len(set(everything)) == 200
    # The work really was shared, not done by one thread.
    assert sum(1 for ids in claimed if ids) >= 2


def test_claim_does_not_wait_for_locked_rows(session, session_factory):
    user = make_user(session)
    first = watched_product(session, user, next_check_at=NOW - timedelta(minutes=1))
    second = watched_product(session, user)

    with session_factory() as other:
        # Another worker is in the middle of claiming `first`.
        other.get(Product, first.id, with_for_update=True)

        result = []
        t = threading.Thread(target=lambda: result.append(claim_due(session, NOW)))
        t.start()
        t.join(timeout=5)
        locked_out = t.is_alive()
        other.rollback()
        t.join()

    assert not locked_out, "claim_due waited for a row another worker had locked"
    assert result == [[second.id]]
