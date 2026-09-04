"""The single write path for quotes.

Quotes are the one market table that takes incremental updates, so it is the
one place a late message can undo a newer one. Ordering is enforced here rather
than trusted from the caller: a quote whose event time is not newer than the
stored one is discarded, which makes replaying the same message twice a no-op
and makes an out-of-order delivery harmless.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.db.models import QuoteRow
from app.domain.verdicts import Quote


def record_quote(db: Session, quote: Quote, *, prev_close: float | None = None) -> bool:
    """Store a quote if it is newer than the one held. Returns whether it landed."""
    existing = db.get(QuoteRow, quote.symbol)
    if existing is None:
        db.add(
            QuoteRow(
                symbol=quote.symbol,
                price=quote.price,
                event_time=quote.event_time,
                ingested_at=utcnow(),
                source=quote.source,
                freshness=quote.freshness.value,
                prev_close=prev_close,
            )
        )
        db.commit()
        return True

    result = db.execute(
        update(QuoteRow)
        .where(QuoteRow.symbol == quote.symbol, QuoteRow.event_time < quote.event_time)
        .values(
            price=quote.price,
            event_time=quote.event_time,
            ingested_at=utcnow(),
            source=quote.source,
            freshness=quote.freshness.value,
            **({"prev_close": prev_close} if prev_close is not None else {}),
        )
    )
    db.commit()
    # The UPDATE went round the ORM, so the instance in the identity map still
    # holds the old price. Expiring it means the next read sees what was stored
    # rather than what this session happened to load earlier.
    db.expire(existing)
    return result.rowcount > 0
