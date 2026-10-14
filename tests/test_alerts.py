import copy

from app.tracking.alerts import AlertDraft, WatchState, evaluate_watch


def state(**kw):
    base = dict(target_price_sen=None, baseline_price_sen=10000, last_alerted_price_sen=None, target_armed=True)
    base.update(kw)
    return WatchState(**base)


def run(s, price, in_stock=True, prev_price=10000, prev_in_stock=True):
    return evaluate_watch(
        s, prev_price_sen=prev_price, prev_in_stock=prev_in_stock, price_sen=price, in_stock=in_stock
    )


def kinds(drafts):
    return [d.kind for d in drafts]


def test_first_check_sets_baseline_and_tracking_started():
    new, drafts = run(state(baseline_price_sen=None), 8990, prev_price=None, prev_in_stock=None)
    assert new.baseline_price_sen == 8990
    assert drafts == [AlertDraft("tracking_started", {"new_price_sen": 8990, "in_stock": True})]


def test_drop_alerts_and_updates_last_alerted():
    new, drafts = run(state(), 9000)
    assert drafts == [AlertDraft("price_drop", {"old_price_sen": 10000, "new_price_sen": 9000})]
    assert new.last_alerted_price_sen == 9000


def test_no_alert_on_rise_or_same():
    assert run(state(), 10000)[1] == []
    assert run(state(), 12000)[1] == []


def test_second_drop_compares_to_last_alerted_not_baseline():
    s = state(last_alerted_price_sen=8000)
    assert run(s, 9000, prev_price=8500)[1] == []
    new, drafts = run(s, 7500, prev_price=8500)
    assert drafts == [AlertDraft("price_drop", {"old_price_sen": 8000, "new_price_sen": 7500})]
    assert new.last_alerted_price_sen == 7500


def test_target_fires_once_then_rearms_after_rise():
    s = state(target_price_sen=5000)
    s, drafts = run(s, 4900)
    assert drafts == [
        AlertDraft("target_reached", {"old_price_sen": 10000, "new_price_sen": 4900, "target_price_sen": 5000})
    ]
    assert s.target_armed is False
    s, drafts = run(s, 4500, prev_price=4900)
    assert drafts == []
    s, drafts = run(s, 5100, prev_price=4500)
    assert drafts == [] and s.target_armed is True
    s, drafts = run(s, 5000, prev_price=5100)
    assert kinds(drafts) == ["target_reached"]


def test_target_watch_gets_no_plain_drop():
    _, drafts = run(state(target_price_sen=5000), 9000)
    assert drafts == []


def test_back_in_stock():
    _, drafts = run(state(), 10000, prev_in_stock=False)
    assert drafts == [AlertDraft("back_in_stock", {"new_price_sen": 10000})]


def test_back_in_stock_with_lower_price_sends_one_message():
    new, drafts = run(state(), 9000, prev_in_stock=False)
    assert kinds(drafts) == ["back_in_stock"]
    assert new.last_alerted_price_sen == 9000


def test_no_price_alert_while_out_of_stock():
    new, drafts = run(state(), 5000, in_stock=False)
    assert drafts == []
    assert new.last_alerted_price_sen is None
    _, drafts = run(state(target_price_sen=6000), 5000, in_stock=False)
    assert drafts == []


def test_evaluate_does_not_mutate_input():
    s = state(target_price_sen=5000)
    before = copy.deepcopy(s)
    run(s, 4000)
    assert s == before
