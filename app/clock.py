"""Wall-clock time, in one place.

Naive UTC rather than aware datetimes: the market data carries naive session
timestamps, and mixing the two raises on comparison. One helper keeps that
choice deliberate instead of scattered across call sites.

The domain layer never calls this. It takes time as an argument, which is what
keeps it testable.
"""

from __future__ import annotations

from datetime import UTC, datetime, time


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


NSE_CLOSE_UTC = time(10, 0)
"""The NSE closing bell, 15:30 IST, expressed in this module's naive-UTC frame.

Stated once. A daily bar carries a date and no time, so every place that turns
one into a comparable moment -- the live calendar, a quote synthesised from a
daily bar, the session a brief is computed for -- has to agree on which moment
that date means. Two of them once used 15:30 naive, which is 21:00 IST, and
made a settled close look five and a half hours newer than the session it came
from.
"""
