"""Rebuild classifier inputs from stored market data.

The classifier reads no clock and touches no database, so something has to turn
rows back into the arguments it expects. That is this module's whole job, and
it is why swapping replay for a live feed later changes nothing above it.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def replay_now(db: Session) -> datetime:
    """The moment the replay considers 'now': the last session's close.

    Held to the data rather than to the wall clock, so a brief is reproducible
    and does not silently go stale between demo runs.
    """
    last = db.scalar(select(TradingDay).order_by(TradingDay.day.desc()).limit(1))
    if last is None:
        raise NoMarketData("no trading days have been seeded")
    return last.close_at


def trading_days(db: Session) -> list[date]:
    return list(db.scalars(select(TradingDay.day).order_by(TradingDay.day)))


def sessions_between(db: Session, start: datetime | None, end: datetime) -> int:
    """Trading sessions that closed in (start, end]."""
    if start is None:
        return 0
    return len(
        list(
            db.scalars(
                select(TradingDay.day)
                .where(TradingDay.close_at > start, TradingDay.close_at <= end)
                .order_by(TradingDay.day)
            )
        )
    )


def load_symbol(db: Session, symbol: str) -> Symbol:
    row = db.get(Symbol, symbol)
    if row is None:
        raise UnknownSymbol(symbol)
    return row


def load_context(db: Session, symbol: str) -> SymbolContext:
    meta = load_symbol(db, symbol)
    bars = list(
        db.scalars(select(DailyBar).where(DailyBar.symbol == symbol).order_by(DailyBar.day))
    )
    if not bars:
        raise NoMarketData(symbol)

    sessions = [bar.day for bar in bars]
    market_closes = _index_closes(db, _market_index(db), sessions)
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


def load_quote(db: Session, symbol: str) -> Quote:
    row = db.get(QuoteRow, symbol)
    if row is None:
        raise NoMarketData(symbol)
    return Quote(
        symbol=row.symbol,
        price=row.price,
        event_time=row.event_time,
        freshness=Freshness(row.freshness),
        source=row.source,
    )


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
    start = db.scalar(
        select(IndexBar.close)
        .join(TradingDay, TradingDay.day == IndexBar.day)
        .where(IndexBar.index_code == index_code, TradingDay.close_at <= since)
        .order_by(IndexBar.day.desc())
        .limit(1)
    )
    end = db.scalar(
        select(IndexBar.close)
        .join(TradingDay, TradingDay.day == IndexBar.day)
        .where(IndexBar.index_code == index_code, TradingDay.close_at <= until)
        .order_by(IndexBar.day.desc())
        .limit(1)
    )
    if start is None or end is None or start == 0:
        return None
    return end / start - 1.0


def market_index(db: Session) -> str:
    return _market_index(db)


def price_on_or_before(db: Session, symbol: str, moment: datetime) -> tuple[datetime, float] | None:
    """The last close for a symbol at or before a moment, with its session time."""
    row = db.execute(
        select(TradingDay.close_at, DailyBar.close)
        .join(DailyBar, DailyBar.day == TradingDay.day)
        .where(DailyBar.symbol == symbol, TradingDay.close_at <= moment)
        .order_by(TradingDay.day.desc())
        .limit(1)
    ).first()
    return (row[0], row[1]) if row else None


def _market_index(db: Session) -> str:
    code = db.scalar(select(IndexMeta.index_code).where(IndexMeta.is_market.is_(True)))
    if code is None:
        raise NoMarketData("no index is marked as the market")
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
