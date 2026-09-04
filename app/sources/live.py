"""Bring real market data into the same tables the fixture writes.

This is the whole of Live Mode's storage story. Once an instrument's bars,
indices, quote and events are normalised into `daily_bars`, `index_bars`,
`quotes` and `corporate_events`, everything above -- the reader, the
classifier, the brief, the UI -- is the code that already existed. There is no
second classifier and no second pipeline, only a second writer.

Rows are tagged `live` so they cannot mix with the fixture. The session
calendar is keyed by source for the same reason: real sessions and the
fixture's synthetic ones fall on the same weekdays, and a shared calendar would
let a live fetch move the moment a demo brief is computed for.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import (
    LIVE_HISTORY_SESSIONS,
    LIVE_QUOTE_STALE_HOURS,
    SOURCE_LIVE,
    live_enabled,
)
from app.db.models import (
    CorporateEventRow,
    DailyBar,
    IndexBar,
    IndexMeta,
    QuoteRow,
    Symbol,
    TradingDay,
)
from app.domain.verdicts import Freshness, Quote
from app.sources import upstox
from app.sources.upstox import (
    NIFTY_50,
    Instrument,
    UpstoxClient,
    UpstoxError,
    UpstoxNotConfigured,
)

MARKET_CLOSE = time(15, 30)
MARKET_CODE = "NIFTY50"
MARKET_NAME = "Nifty 50"

# Calendar days fetched to obtain LIVE_HISTORY_SESSIONS trading sessions:
# weekends and holidays mean roughly a third of the span is not a session.
_CALENDAR_MULTIPLIER = 1.6

_client: UpstoxClient | None = None


class LiveNotConfigured(RuntimeError):
    """Live mode was requested without an access token."""


class LiveDataUnavailable(RuntimeError):
    """The provider answered, but not with enough to classify anything."""


def client() -> UpstoxClient:
    """One client per process, so the instrument master is fetched once."""
    global _client
    # An injected client wins, so tests exercise this module without a token
    # and without reaching the network.
    if _client is not None:
        return _client
    if not live_enabled():
        raise LiveNotConfigured("UPSTOX_ACCESS_TOKEN is not set")
    _client = UpstoxClient()
    return _client


def set_client(replacement: UpstoxClient | None) -> None:
    """Injection point for tests; no test should reach the network."""
    global _client
    _client = replacement


def index_code_for(instrument_key: str) -> str:
    """A short, stable code for an index, derived from its instrument key."""
    tail = instrument_key.split("|")[-1]
    return tail.upper().replace(" ", "_")[:32]


def search(query: str, limit: int = 25) -> list[dict]:
    return [instrument.as_dict() for instrument in client().search(query, limit)]


def freshen(quote: Quote, now: datetime) -> Quote:
    """Re-judge a stored live quote's freshness against the clock.

    Quotes are fetched on demand, not streamed, so one stored an hour ago may
    have gone stale since. Deciding this at read time means an aging quote
    stops supporting inference on its own, without a background job.
    """
    age = now - quote.event_time
    if age > timedelta(hours=LIVE_QUOTE_STALE_HOURS):
        return Quote(
            symbol=quote.symbol,
            price=quote.price,
            event_time=quote.event_time,
            freshness=Freshness.STALE,
            source=quote.source,
        )
    return quote


def add_instrument(db: Session, symbol: str) -> Symbol:
    """Resolve a real instrument and store everything needed to classify it.

    Market and sector history are fetched alongside the stock's own, and only
    the sessions all of them share are kept. A stock priced on a day its index
    was not is a comparison that cannot be made, and storing it would surface
    later as a crash inside the reader rather than as missing data here.
    """
    api = client()
    wanted = symbol.strip().upper()
    instrument = api.find(wanted)
    if instrument is None:
        raise LiveDataUnavailable(f"{wanted} is not a listed NSE equity")

    start, end = _history_window()
    market_bars = _index_history(db, api, NIFTY_50, MARKET_CODE, MARKET_NAME, True, start, end)

    sector_days: set[date] | None = None
    sector_code = None
    if instrument.sector_index:
        sector_code = index_code_for(instrument.sector_index)
        sector_bars = _index_history(
            db,
            api,
            instrument.sector_index,
            sector_code,
            upstox.SECTOR_INDICES.get(instrument.sector_index, sector_code),
            False,
            start,
            end,
        )
        sector_days = {bar.day for bar in sector_bars}
        if not sector_days:
            # Better no sector than a sector the comparison cannot use; the
            # classifier already discloses an unavailable sector.
            sector_code = None

    stock_bars = api.daily_candles(instrument.instrument_key, start, end)
    if not stock_bars:
        raise LiveDataUnavailable(f"no historical data returned for {wanted}")

    usable = {bar.day for bar in market_bars}
    if sector_days is not None and sector_code:
        usable &= sector_days
    aligned = [bar for bar in stock_bars if bar.day in usable]
    if not aligned:
        raise LiveDataUnavailable(f"{wanted} shares no sessions with its indices")

    row = _upsert_symbol(db, instrument, sector_code)
    _write_bars(db, wanted, aligned)
    _write_events(db, api, instrument)
    db.commit()

    refresh_quote(db, wanted, instrument=instrument)
    return row


def refresh_quote(db: Session, symbol: str, *, instrument: Instrument | None = None) -> Freshness:
    """Pull the latest quote. A provider failure downgrades, never fabricates."""
    row = db.get(Symbol, symbol)
    if row is None or not row.instrument_key:
        raise LiveDataUnavailable(f"{symbol} has no instrument key")

    price: float | None = None
    event_time: datetime | None = None
    prev_close: float | None = None
    freshness = Freshness.UNAVAILABLE

    try:
        quote = client().quote(row.instrument_key)
    except UpstoxError:
        quote = None

    if quote is not None and quote.event_time is not None:
        price, event_time, prev_close = quote.price, quote.event_time, quote.prev_close
        age = utcnow() - event_time
        freshness = (
            Freshness.STALE if age > timedelta(hours=LIVE_QUOTE_STALE_HOURS) else Freshness.LIVE
        )

    if price is None or event_time is None:
        # Fall back to the last stored close so the symbol still has a price to
        # show, labelled UNAVAILABLE so the classifier declines to infer from it.
        last = db.execute(
            select(TradingDay.close_at, DailyBar.close)
            .join(DailyBar, DailyBar.day == TradingDay.day)
            .where(DailyBar.symbol == symbol, TradingDay.source == SOURCE_LIVE)
            .order_by(TradingDay.day.desc())
            .limit(1)
        ).first()
        if last is None:
            raise LiveDataUnavailable(f"no price of any age is on record for {symbol}")
        event_time, price = last[0], last[1]
        freshness = Freshness.UNAVAILABLE

    existing = db.get(QuoteRow, symbol)
    if existing is None:
        db.add(
            QuoteRow(
                symbol=symbol,
                price=price,
                event_time=event_time,
                ingested_at=utcnow(),
                source="upstox",
                freshness=freshness.value,
                prev_close=prev_close,
            )
        )
    elif event_time >= existing.event_time:
        # Not strictly newer: a re-poll of the same session must still be able
        # to downgrade freshness as that session's price ages.
        existing.price = price
        existing.event_time = event_time
        existing.ingested_at = utcnow()
        existing.source = "upstox"
        existing.freshness = freshness.value
        if prev_close is not None:
            existing.prev_close = prev_close
    db.commit()
    return freshness


def refresh_watchlist(db: Session, symbols: list[str]) -> dict[str, str]:
    """Re-poll several symbols. One failure does not stop the others."""
    outcome: dict[str, str] = {}
    for symbol in symbols:
        try:
            outcome[symbol] = refresh_quote(db, symbol).value
        except (LiveDataUnavailable, LiveNotConfigured, UpstoxError) as exc:
            outcome[symbol] = f"unavailable: {exc}"
    return outcome


def _history_window() -> tuple[date, date]:
    end = utcnow().date()
    return end - timedelta(days=int(LIVE_HISTORY_SESSIONS * _CALENDAR_MULTIPLIER)), end


def _index_history(
    db: Session,
    api: UpstoxClient,
    instrument_key: str,
    code: str,
    name: str,
    is_market: bool,
    start: date,
    end: date,
) -> list[upstox.Bar]:
    bars = api.daily_candles(instrument_key, start, end)
    if not bars and is_market:
        raise LiveDataUnavailable("no history returned for the market index")

    meta = db.get(IndexMeta, code)
    if meta is None:
        db.add(
            IndexMeta(
                index_code=code,
                name=name,
                is_market=is_market,
                source=SOURCE_LIVE,
                instrument_key=instrument_key,
            )
        )
        db.flush()

    known = set(
        db.scalars(select(IndexBar.day).where(IndexBar.index_code == code))
    )
    for bar in bars:
        if bar.day not in known:
            db.add(IndexBar(index_code=code, day=bar.day, close=bar.close))

    if is_market:
        _write_calendar(db, [bar.day for bar in bars])
    db.flush()
    return bars


def _write_calendar(db: Session, days: list[date]) -> None:
    """The market index defines which days were sessions for the live source."""
    known = set(
        db.scalars(select(TradingDay.day).where(TradingDay.source == SOURCE_LIVE))
    )
    for day in days:
        if day not in known:
            db.add(
                TradingDay(
                    day=day,
                    source=SOURCE_LIVE,
                    close_at=datetime.combine(day, MARKET_CLOSE),
                )
            )
    db.flush()


def _upsert_symbol(db: Session, instrument: Instrument, sector_code: str | None) -> Symbol:
    existing = db.get(Symbol, instrument.symbol)
    if existing is not None:
        if existing.source != SOURCE_LIVE:
            raise LiveDataUnavailable(
                f"{instrument.symbol} already exists as fixture data and cannot be reused live"
            )
        existing.name = instrument.name
        existing.isin = instrument.isin
        existing.instrument_key = instrument.instrument_key
        existing.sector_index = sector_code
        db.flush()
        return existing

    row = Symbol(
        symbol=instrument.symbol,
        name=instrument.name,
        sector_index=sector_code,
        isin=instrument.isin,
        source=SOURCE_LIVE,
        instrument_key=instrument.instrument_key,
    )
    db.add(row)
    db.flush()
    return row


def _write_bars(db: Session, symbol: str, bars: list[upstox.Bar]) -> None:
    known = set(db.scalars(select(DailyBar.day).where(DailyBar.symbol == symbol)))
    for bar in bars:
        if bar.day not in known:
            db.add(
                DailyBar(
                    symbol=symbol,
                    day=bar.day,
                    close=bar.close,
                    volume=bar.volume,
                    source="upstox",
                )
            )
    db.flush()


def _write_events(db: Session, api: UpstoxClient, instrument: Instrument) -> None:
    try:
        events = api.corporate_actions(instrument.instrument_key, instrument.symbol)
    except UpstoxError:
        events = []

    known = {
        (row.occurred_on, row.kind)
        for row in db.scalars(
            select(CorporateEventRow).where(CorporateEventRow.symbol == instrument.symbol)
        )
    }
    for event in events:
        if (event.on_date, event.kind.value) in known:
            continue
        db.add(
            CorporateEventRow(
                symbol=instrument.symbol,
                occurred_on=event.on_date,
                kind=event.kind.value,
                value=event.value,
                detail=event.detail,
                source="upstox",
            )
        )
    db.flush()


__all__ = [
    "LiveDataUnavailable",
    "LiveNotConfigured",
    "MARKET_CODE",
    "UpstoxNotConfigured",
    "add_instrument",
    "client",
    "freshen",
    "index_code_for",
    "refresh_quote",
    "refresh_watchlist",
    "search",
    "set_client",
]
