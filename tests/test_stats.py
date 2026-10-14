from datetime import timedelta

from app.models import CheckResult
from app.web.stats import PluginHealth, plugin_health, product_counts
from tests.factories import NOW, make_product


def test_plugin_health_windows(session):
    def add(ago, ok, shop="schema_org"):
        session.add(CheckResult(shop=shop, ok=ok, checked_at=NOW - ago, error_kind=None if ok else "blocked"))

    add(timedelta(hours=1), True)
    add(timedelta(hours=1), False)
    add(timedelta(days=2), True)
    add(timedelta(days=9), False)  # outside both windows
    add(timedelta(hours=3), True, shop="lazada")
    session.commit()

    assert plugin_health(session, NOW) == [
        PluginHealth(shop="lazada", ok_24h=1, total_24h=1, ok_7d=1, total_7d=1),
        PluginHealth(shop="schema_org", ok_24h=1, total_24h=2, ok_7d=2, total_7d=3),
    ]


def test_status_counts_include_zeros(session):
    make_product(session)
    make_product(session, status="failing")
    assert product_counts(session) == {"active": 1, "failing": 1, "dead": 0}
