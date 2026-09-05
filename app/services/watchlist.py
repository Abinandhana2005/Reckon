"""Watchlist membership and the anchor each membership starts from."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import DEFAULT_ANCHOR_SESSIONS_AGO, SOURCE_LIVE, SOURCE_REPLAY
from app.db.models import IndexMeta, Symbol, UserSymbolAnchor, WatchlistItem
from app.sources import live, replay
from app.sources.replay import load_symbol


def list_symbols(
    db: Session,
    query: str | None = None,
    limit: int = 50,
    source: str = SOURCE_REPLAY,
) -> list[Symbol]:
    """The catalogue a user can add from, restricted to their own source.

    Without the filter a demo session could add a live instrument it has no
    data for, and a live session could add a fictional issuer.
    """
    statement = select(Symbol).where(Symbol.source == source).order_by(Symbol.symbol)
    if query:
        pattern = f"%{query.strip().upper()}%"
        statement = statement.where(
            func.upper(Symbol.symbol).like(pattern) | func.upper(Symbol.name).like(pattern)
        )
    return list(db.scalars(statement.limit(limit)))


def watched(
    db: Session, user_id: str, *, source: str | None = None
) -> list[WatchlistItem]:
    statement = (
        select(WatchlistItem)
        .join(Symbol, Symbol.symbol == WatchlistItem.symbol)
        .where(WatchlistItem.user_id == user_id)
        .order_by(WatchlistItem.sort_order, WatchlistItem.symbol)
    )
    if source is not None:
        statement = statement.where(Symbol.source == source)
    return list(db.scalars(statement))


def watched_symbols(
    db: Session, user_id: str, *, source: str | None = None
) -> list[str]:
    return [item.symbol for item in watched(db, user_id, source=source)]


def names_for(db: Session, symbols: list[str]) -> dict[str, str]:
    """Company names for a set of symbols, in one query."""
    if not symbols:
        return {}
    return dict(
        db.execute(
            select(Symbol.symbol, Symbol.name).where(Symbol.symbol.in_(symbols))
        ).all()
    )


def sectors_for(db: Session, symbols: list[str]) -> dict[str, str | None]:
    """The sector index name each symbol is compared against, where one is held.

    Read through `indices` rather than returning the raw code, so the watchlist
    shows the same name the brief uses when it groups by sector.
    """
    if not symbols:
        return {}
    names = dict(db.execute(select(IndexMeta.index_code, IndexMeta.name)).all())
    rows = db.execute(
        select(Symbol.symbol, Symbol.sector_index).where(Symbol.symbol.in_(symbols))
    ).all()
    return {
        symbol: names.get(code, code) if code else None for symbol, code in rows
    }


def add_for_user(
    db: Session,
    user,
    symbol: str,
    *,
    note: str | None = None,
    anchor_sessions_ago: int = DEFAULT_ANCHOR_SESSIONS_AGO,
) -> WatchlistItem:
    """Add a symbol, fetching it first when the user is in live mode.

    A live instrument is unknown to the database until someone asks for it, so
    its history, indices, quote and events are pulled and stored before it can
    be watched. Fixture symbols are already there and this is a no-op for them.

    Market data is shared between users; watchlists are not. So a symbol someone
    else already watches is not re-downloaded -- but it is brought up to date,
    because sessions may have closed since it was last fetched and the reader
    adding it now would otherwise open a brief built on somebody else's stale
    copy.
    """
    code = symbol.strip().upper()
    existing_symbol = db.get(Symbol, code)
    if user.data_source == SOURCE_LIVE:
        if existing_symbol is not None and existing_symbol.source != SOURCE_LIVE:
            raise live.LiveDataUnavailable(
                f"{code} is available only in sample data; choose a live NSE symbol"
            )
        if existing_symbol is None:
            live.add_instrument(db, code)
        else:
            live.refresh_watchlist(db, [code])
    elif existing_symbol is not None and existing_symbol.source != SOURCE_REPLAY:
        raise replay.UnknownSymbol(
            f"{code} is available only in live data; switch to Live mode to use it"
        )
    return add(db, user.id, code, note=note, anchor_sessions_ago=anchor_sessions_ago)


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


def remove(db: Session, user_id: str, symbol: str, *, source: str | None = None) -> bool:
    """Remove a symbol. Removing one not watched is not an error.

    The anchor is kept: re-adding later should resume from where the user was,
    not from a reference point that hides everything they missed.
    """
    item = db.get(WatchlistItem, {"user_id": user_id, "symbol": symbol})
    if item is None:
        return False
    if source is not None:
        row = db.get(Symbol, symbol)
        if row is None or row.source != source:
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

    at, price = replay.session_close(db, symbol, sessions_ago=sessions_ago)
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
