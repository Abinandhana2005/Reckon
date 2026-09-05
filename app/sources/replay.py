"""Rebuild classifier inputs from stored market data.

The classifier reads no clock and touches no database, so something has to turn
rows back into the arguments it expects. That is this module's whole job, and
it is why swapping replay for a live feed later changes nothing above it.
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import SOURCE_LIVE, SOURCE_REPLAY
from app.db.models import (
    CorporateEventRow,
    DailyBar,
    IndexBar,
    IndexMeta,
    QuoteRow,
    Symbol,
    TradingDay,
)
from app.domain.classifier import SymbolContext
from app.domain.verdicts import CorporateEvent, EventKind, Freshness, Quote


class UnknownSymbol(LookupError):
    """Asked for a symbol the market data does not contain."""


class NoMarketData(LookupError):
    """The database holds no sessions; nothing can be classified."""


LIVE_SESSION_CLOSE_UTC = time(10, 0)


def session_close_at(day: date, source: str = SOURCE_REPLAY) -> datetime:
    """Return a comparable naive-UTC close timestamp for a source session.

    Fixture timestamps are synthetic and stay at 15:30. Live NSE closes at
    15:30 IST, which is 10:00 UTC; using one convention prevents an in-progress
    live session from becoming the authoritative analysis endpoint.
    """
    close = LIVE_SESSION_CLOSE_UTC if source == SOURCE_LIVE else time(15, 30)
    return datetime.combine(day, close)


def latest_completed_session(
    db: Session, now: datetime, source: str = SOURCE_REPLAY
) -> datetime:
    """Find the latest source session whose close is not in the future."""
    rows = list(
        db.execute(
            select(TradingDay.day)
            .where(TradingDay.source == source)
            .order_by(TradingDay.day.desc())
        )
    )
    for (day,) in rows:
        close = session_close_at(day, source)
        if close <= now:
            return close
    raise NoMarketData(f"no completed trading days recorded for {source}")


def replay_now(db: Session, source: str = SOURCE_REPLAY) -> datetime:
    """The moment this source considers 'now': its last session's close.

    Held to the data rather than to the wall clock, so a brief is reproducible
    and does not silently go stale between demo runs. Scoped by source, because
    a live session arriving must not move the moment a demo brief is built for.
    """
    last = db.scalar(
        select(TradingDay)
        .where(TradingDay.source == source)
        .order_by(TradingDay.day.desc())
        .limit(1)
    )
    if last is None:
        raise NoMarketData(f"no trading days recorded for {source}")
    return session_close_at(last.day, source)


def trading_days(db: Session, source: str = SOURCE_REPLAY) -> list[date]:
    return list(
        db.scalars(
            select(TradingDay.day)
            .where(TradingDay.source == source)
            .order_by(TradingDay.day)
        )
    )


def sessions_between(
    db: Session, start: datetime | None, end: datetime, source: str = SOURCE_REPLAY
) -> int:
    """Trading sessions of this source that closed in (start, end]."""
    if start is None:
        return 0
    days = list(
        db.scalars(
            select(TradingDay.day)
            .where(TradingDay.source == source)
            .order_by(TradingDay.day)
        )
    )
    return sum(
        start < session_close_at(day, source) <= end
        for day in days
    )


def load_symbol(db: Session, symbol: str) -> Symbol:
    row = db.get(Symbol, symbol)
    if row is None:
        raise UnknownSymbol(symbol)
    return row


def load_context(
    db: Session, symbol: str, *, as_of: datetime | None = None
) -> SymbolContext:
    """Market data for one symbol, optionally as it stood at a past session.

    Truncating here rather than filtering later is what makes replaying a past
    moment honest: the classifier sees the history that existed then, so its
    baselines are the ones it would have used, not ones built from the future.
    """
    meta = load_symbol(db, symbol)
    statement = select(DailyBar).where(DailyBar.symbol == symbol)
    if as_of is not None:
        statement = statement.where(DailyBar.day <= as_of.date())
    bars = list(db.scalars(statement.order_by(DailyBar.day)))
    if not bars:
        raise NoMarketData(symbol)

    sessions = [bar.day for bar in bars]
    market_closes = _index_closes(db, _market_index(db, meta.source), sessions)
    sector_closes = (
        _index_closes(db, meta.sector_index, sessions) if meta.sector_index else None
    )

    return SymbolContext(
        symbol=symbol,
        sessions=sessions,
        closes=[bar.close for bar in bars],
        market_closes=market_closes,
        sector_index=meta.sector_index,
        sector_closes=sector_closes,
        volume_ratio=_volume_ratio(bars),
    )


def load_quote(
    db: Session,
    symbol: str,
    *,
    as_of: datetime | None = None,
    freshness: Freshness | None = None,
) -> Quote:
    """The quote for a symbol, optionally rewound to a past session's close.

    A rewound quote is read from the bar for that session, because the stored
    quote is always the latest one; leaving it in place would price a past
    moment with a future number.
    """
    if as_of is not None:
        point = price_on_or_before(db, symbol, as_of)
        if point is None:
            raise NoMarketData(f"{symbol} has no close at or before {as_of}")
        event_time, price = point
        return Quote(
            symbol=symbol,
            price=price,
            event_time=event_time,
            freshness=freshness or Freshness.CLOSED,
            source=load_symbol(db, symbol).source,
        )

    row = db.get(QuoteRow, symbol)
    if row is None:
        raise NoMarketData(symbol)
    return Quote(
        symbol=row.symbol,
        price=row.price,
        event_time=row.event_time,
        freshness=freshness or Freshness(row.freshness),
        source=row.source,
    )


def session_close(
    db: Session, symbol: str, *, sessions_ago: int, as_of: datetime | None = None
) -> tuple[datetime, float]:
    """Close of the session `sessions_ago` before `as_of`, from the symbol's own bars.

    Read off the symbol rather than the calendar so a recent listing anchors to
    a session it actually traded in.
    """
    statement = (
        select(TradingDay.day, DailyBar.close)
        .join(DailyBar, DailyBar.day == TradingDay.day)
        .where(DailyBar.symbol == symbol, TradingDay.source == load_symbol(db, symbol).source)
    )
    if as_of is not None:
        statement = statement.where(TradingDay.day <= as_of.date())
    rows = list(db.execute(statement.order_by(TradingDay.day)).all())
    source = load_symbol(db, symbol).source
    bars = [(session_close_at(day, source), close) for day, close in rows]
    if not bars:
        raise UnknownSymbol(f"{symbol} has no price history")

    index = max(len(bars) - 1 - max(sessions_ago, 0), 0)
    at, price = bars[index]
    return at, price


def load_events(db: Session, symbol: str) -> list[CorporateEvent]:
    rows = db.scalars(
        select(CorporateEventRow)
        .where(CorporateEventRow.symbol == symbol)
        .order_by(CorporateEventRow.occurred_on)
    )
    return [
        CorporateEvent(
            symbol=row.symbol,
            on_date=row.occurred_on,
            kind=EventKind(row.kind),
            value=row.value,
            detail=row.detail,
        )
        for row in rows
    ]


def index_return(db: Session, index_code: str, since: datetime, until: datetime) -> float | None:
    """Return of an index between the sessions bracketing two moments."""
    meta = db.get(IndexMeta, index_code)
    source = meta.source if meta else SOURCE_REPLAY
    rows = list(
        db.execute(
            select(TradingDay.day, IndexBar.close)
            .join(TradingDay, TradingDay.day == IndexBar.day)
            .where(IndexBar.index_code == index_code, TradingDay.source == source)
            .order_by(TradingDay.day)
        )
    )
    start = next(
        (close for day, close in reversed(rows) if session_close_at(day, source) <= since),
        None,
    )
    end = next(
        (close for day, close in reversed(rows) if session_close_at(day, source) <= until),
        None,
    )
    if start is None or end is None or start == 0:
        return None
    return end / start - 1.0


def market_index(db: Session, source: str = SOURCE_REPLAY) -> str:
    return _market_index(db, source)


def price_on_or_before(db: Session, symbol: str, moment: datetime) -> tuple[datetime, float] | None:
    """The last close for a symbol at or before a moment, with its session time."""
    rows = list(db.execute(
        select(TradingDay.day, DailyBar.close)
        .join(DailyBar, DailyBar.day == TradingDay.day)
        .where(
            DailyBar.symbol == symbol,
            TradingDay.source == load_symbol(db, symbol).source,
            TradingDay.day <= moment.date(),
        )
        .order_by(TradingDay.day.desc())
    ))
    source = load_symbol(db, symbol).source
    for day, price in rows:
        close = session_close_at(day, source)
        if close <= moment:
            return close, price
    return None


def _market_index(db: Session, source: str = SOURCE_REPLAY) -> str:
    code = db.scalar(
        select(IndexMeta.index_code).where(
            IndexMeta.is_market.is_(True), IndexMeta.source == source
        )
    )
    if code is None:
        raise NoMarketData(f"no index is marked as the market for {source}")
    return code


def index_names(db: Session) -> dict[str, str]:
    return dict(db.execute(select(IndexMeta.index_code, IndexMeta.name)).all())


def _index_closes(db: Session, index_code: str, sessions: list[date]) -> list[float]:
    closes = dict(
        db.execute(
            select(IndexBar.day, IndexBar.close).where(IndexBar.index_code == index_code)
        ).all()
    )
    missing = [day for day in sessions if day not in closes]
    if missing:
        raise NoMarketData(f"{index_code} has no close for {missing[0]}")
    return [closes[day] for day in sessions]


def _volume_ratio(bars: list[DailyBar]) -> float | None:
    """Latest volume against the median of the twenty sessions before it."""
    if len(bars) < 21:
        return None
    history = sorted(bar.volume for bar in bars[-21:-1])
    median = history[len(history) // 2]
    return bars[-1].volume / median if median else None
