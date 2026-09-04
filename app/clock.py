"""Wall-clock time, in one place.

Naive UTC rather than aware datetimes: the market data carries naive session
timestamps, and mixing the two raises on comparison. One helper keeps that
choice deliberate instead of scattered across call sites.

The domain layer never calls this. It takes time as an argument, which is what
keeps it testable.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
