"""Corporate action adjustment.

A user who anchored at 1000 and returns to find 500 has not lost half their
money if a 1:2 split went ex in between. Comparing a raw anchor price to a raw
current price across a corporate action produces a number that is simply wrong,
so the anchor is restated onto today's terms before any return is computed.

Every adjustment is expressed as a multiplicative factor applied to the anchor:

    effective_anchor = anchor_price * product(factors)

If any event in the window cannot be adjusted with the data we hold, we refuse
to compute a return at all rather than show an unadjusted one.
"""

from __future__ import annotations

from datetime import date

from app.domain.verdicts import CorporateEvent, EventKind


class UnadjustableAction(Exception):
    """Raised when an action's adjustment factor cannot be determined."""

    def __init__(self, event: CorporateEvent, detail: str) -> None:
        super().__init__(f"{event.kind} on {event.on_date} for {event.symbol}: {detail}")
        self.event = event


def split_factor(ratio: float) -> float:
    """A 1:k split multiplies share count by k, so the anchor price scales by 1/k."""
    if ratio <= 0:
        raise ValueError("split ratio must be positive")
    return 1.0 / ratio


def bonus_factor(ratio: float) -> float:
    """An a:b bonus issues `a` new shares for every `b` held."""
    if ratio <= 0:
        raise ValueError("bonus ratio must be positive")
    return 1.0 / (1.0 + ratio)


def dividend_factor(dividend: float, close_before_ex: float) -> float:
    """The standard ex-dividend adjustment.

    The price drops mechanically by roughly the dividend on the ex-date. The
    factor restates the anchor so that a pure ex-dividend drop reads as no
    change, and the dividend itself is surfaced to the user as an event.
    """
    if close_before_ex <= 0:
        raise ValueError("reference close must be positive")
    if dividend < 0:
        raise ValueError("dividend must not be negative")
    if dividend >= close_before_ex:
        raise ValueError("dividend exceeds the reference close")
    return (close_before_ex - dividend) / close_before_ex


def adjustment_factor(
    events: list[CorporateEvent],
    closes_by_date: dict[date, float],
) -> float:
    """Combine every adjusting event in a window into one factor.

    `closes_by_date` supplies the close on the session before each ex-date,
    which the dividend adjustment needs. A missing close is a hard failure: we
    would rather report CANT_SAY than a wrong percentage.
    """
    factor = 1.0
    for event in sorted(events, key=lambda e: e.on_date):
        if not event.adjusts_price:
            continue
        if event.value is None:
            raise UnadjustableAction(event, "no value recorded")

        if event.kind is EventKind.SPLIT:
            factor *= split_factor(event.value)
        elif event.kind is EventKind.BONUS:
            factor *= bonus_factor(event.value)
        elif event.kind is EventKind.EX_DIVIDEND:
            reference = _close_before(event.on_date, closes_by_date)
            if reference is None:
                raise UnadjustableAction(event, "no close before the ex-date")
            factor *= dividend_factor(event.value, reference)

    return factor


def _close_before(ex_date: date, closes_by_date: dict[date, float]) -> float | None:
    earlier = [d for d in closes_by_date if d < ex_date]
    if not earlier:
        return None
    return closes_by_date[max(earlier)]
