"""Deciding which alerts a price check should send to one watcher.

Pure functions: no database, no clock. record.py feeds them each watch's
state and saves what comes back.
"""

from dataclasses import dataclass, replace


@dataclass
class WatchState:
    target_price_sen: int | None
    baseline_price_sen: int | None
    last_alerted_price_sen: int | None
    target_armed: bool


@dataclass(frozen=True)
class AlertDraft:
    kind: str
    payload: dict


def evaluate_watch(
    state: WatchState,
    *,
    prev_price_sen: int | None,
    prev_in_stock: bool | None,
    price_sen: int,
    in_stock: bool,
) -> tuple[WatchState, list[AlertDraft]]:
    # First good check for this watch: record where the price started.
    if state.baseline_price_sen is None:
        return replace(state, baseline_price_sen=price_sen), [
            AlertDraft("tracking_started", {"new_price_sen": price_sen, "in_stock": in_stock})
        ]

    new = replace(state)
    drafts: list[AlertDraft] = []

    back_in_stock = prev_in_stock is False and in_stock
    if back_in_stock:
        drafts.append(AlertDraft("back_in_stock", {"new_price_sen": price_sen}))

    # A price you can't buy at isn't worth an alert.
    if not in_stock:
        return new, drafts

    if state.target_price_sen is not None:
        if price_sen > state.target_price_sen:
            new.target_armed = True
        elif state.target_armed:
            new.target_armed = False
            drafts.append(
                AlertDraft(
                    "target_reached",
                    {
                        "old_price_sen": prev_price_sen,
                        "new_price_sen": price_sen,
                        "target_price_sen": state.target_price_sen,
                    },
                )
            )
        return new, drafts

    reference = (
        state.last_alerted_price_sen
        if state.last_alerted_price_sen is not None
        else state.baseline_price_sen
    )
    if price_sen < reference:
        new.last_alerted_price_sen = price_sen
        # The back-in-stock message already shows the new price; one message is enough.
        if not back_in_stock:
            drafts.append(
                AlertDraft("price_drop", {"old_price_sen": reference, "new_price_sen": price_sen})
            )
    return new, drafts
