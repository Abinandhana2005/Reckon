"""Watchlist membership and the anchor each membership starts from."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import DEFAULT_ANCHOR_SESSIONS_AGO
from app.db.models import DailyBar, Symbol, TradingDay, UserSymbolAnchor, WatchlistItem
from app.sources.replay import UnknownSymbol, load_symbol


def list_symbols(db: Session, query: str | None = None, limit: int = 50) -> list[Symbol]:
    statement = select(Symbol).order_by(Symbol.symbol)
    if query:
        pattern = f"%{query.strip().upper()}%"
        statement = statement.where(
            func.upper(Symbol.symbol).like(pattern) | func.upper(Symbol.name).like(pattern)
        )
    return list(db.scalars(statement.limit(limit)))


def watched(db: Session, user_id: str) -> list[WatchlistItem]:
    return list(
        db.scalars(
            select(WatchlistItem)
            .where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.sort_order, WatchlistItem.symbol)
        )
    )


def watched_symbols(db: Session, user_id: str) -> list[str]:
    return [item.symbol for item in watched(db, user_id)]


def add(
    db: Session,
    user_id: str,
    symbol: str,
    *,
    note: str | None = None,
    anchor_sessions_ago: int = DEFAULT_ANCHOR_SESSIONS_AGO,
) -> WatchlistItem:
    """Add a symbol. Adding one already watched updates the note and nothing else.

    Re-adding must not reset the anchor: that would silently mark everything the
    user had not yet acknowledged as seen.
    """
    load_symbol(db, symbol)

    existing = db.get(WatchlistItem, {"user_id": user_id, "symbol": symbol})
    if existing is not None:
        if note is not None:
            existing.note = note
            db.commit()
        return existing

    item = WatchlistItem(user_id=user_id, symbol=symbol, note=note, added_at=utcnow())
    db.add(item)
    ensure_anchor(db, user_id, symbol, sessions_ago=anchor_sessions_ago)
    db.commit()
    return item


def remove(db: Session, user_id: str, symbol: str) -> bool:
    """Remove a symbol. Removing one not watched is not an error.

    The anchor is kept: re-adding later should resume from where the user was,
    not from a reference point that hides everything they missed.
    """
    item = db.get(WatchlistItem, {"user_id": user_id, "symbol": symbol})
    if item is None:
        return False
    db.delete(item)
    db.commit()
    return True


def ensure_anchor(
    db: Session,
    user_id: str,
    symbol: str,
    *,
    sessions_ago: int = DEFAULT_ANCHOR_SESSIONS_AGO,
) -> UserSymbolAnchor:
    existing = db.get(UserSymbolAnchor, {"user_id": user_id, "symbol": symbol})
    if existing is not None:
        return existing

    at, price = _session_close(db, symbol, sessions_ago)
    anchor = UserSymbolAnchor(
        user_id=user_id,
        symbol=symbol,
        anchor_at=at,
        anchor_price=price,
        anchor_adj=1.0,
    )
    db.add(anchor)
    db.flush()
    return anchor


def _session_close(db: Session, symbol: str, sessions_ago: int) -> tuple[datetime, float]:
    """Close of the session `sessions_ago` back, from the symbol's own bars.

    Read off the symbol rather than the calendar so a recent listing anchors to
    a session it actually traded in.
    """
    bars = list(
        db.execute(
            select(TradingDay.close_at, DailyBar.close)
            .join(DailyBar, DailyBar.day == TradingDay.day)
            .where(DailyBar.symbol == symbol)
            .order_by(TradingDay.day)
        ).all()
    )
    if not bars:
        raise UnknownSymbol(f"{symbol} has no price history")

    index = max(len(bars) - 1 - max(sessions_ago, 0), 0)
    at, price = bars[index]
    return at, price
