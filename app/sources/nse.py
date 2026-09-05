"""Regular NSE trading hours, stated once.

Used for wording only. Which session a brief is computed for comes from the
sessions actually observed in `trading_days`, never from this module: a holiday
this file does not know about would otherwise turn into a session that never
happened. What it does answer is "is the exchange in a session right now",
which is what lets the UI say the analysis ends at the last completed close
while a newer quote is shown beside it.
"""

from __future__ import annotations

from datetime import datetime, time

from app.clock import NSE_CLOSE_UTC

NSE_OPEN_UTC = time(3, 45)
"""09:15 IST, the start of the regular equity session, in naive UTC."""

OPEN = "OPEN"
CLOSED = "CLOSED"


def market_state(now: datetime) -> str:
    """OPEN during regular weekday trading hours, CLOSED otherwise.

    Deliberately does not consult a holiday list. Claiming CLOSED on a day the
    exchange was open would be the worse error, and nothing downstream treats
    OPEN as evidence that a session exists -- it only changes how the timing of
    a quote is described.
    """
    if now.weekday() >= 5:
        return CLOSED
    return OPEN if NSE_OPEN_UTC <= now.time() < NSE_CLOSE_UTC else CLOSED


__all__ = ["CLOSED", "NSE_OPEN_UTC", "OPEN", "market_state"]
