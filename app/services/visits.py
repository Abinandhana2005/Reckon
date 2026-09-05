"""When the user last opened Reckon, per data source.

"Last checked" is the sentence the whole brief hangs off, so what it means has
to be exact: the last time this person opened Reckon on this data source, not
the last time a request was served and not the last time they acknowledged
something.

Two rules follow from that, and both live here.

  The previous value is read before a brief is built and written only after one
  has been built successfully. A failed read must leave the previous visit
  intact, or the user loses the window they were away for.

  Re-reading the brief inside one sitting is not a new visit. Without an idle
  window, pressing refresh would reset "last checked" to a moment ago and the
  brief would describe an empty span.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import VISIT_IDLE_MINUTES
from app.db.models import User, UserSourceVisit


def previous(db: Session, user_id: str, source: str) -> datetime | None:
    """The visit a brief should describe, or None on a first visit."""
    row = db.get(UserSourceVisit, {"user_id": user_id, "source": source})
    return row.last_open_at if row else None


def record(db: Session, user: User, source: str, at: datetime) -> datetime | None:
    """Mark a visit, and report the value now stored.

    Moves forward only, and only across the idle window: a brief re-read inside
    one sitting leaves the stored visit where it was, so the header keeps
    describing the span the reader actually missed.
    """
    row = db.get(UserSourceVisit, {"user_id": user.id, "source": source})
    if row is None:
        db.add(UserSourceVisit(user_id=user.id, source=source, last_open_at=at))
        user.last_open_at = at
        return at

    if at > row.last_open_at + timedelta(minutes=VISIT_IDLE_MINUTES):
        row.last_open_at = at
    # The overall clock is a fact about the person, not about one source, so it
    # tracks every opening rather than only the ones that started a new window.
    if user.last_open_at is None or at > user.last_open_at:
        user.last_open_at = at
    return row.last_open_at


__all__ = ["previous", "record"]
