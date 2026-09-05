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

from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import NSE_CLOSE_UTC, utcnow
import app.config as config
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
from app.sources import yahoo
from app.sources.provider import (
    NIFTY_50,
    SECTOR_INDICES,
    Bar,
    Instrument,
    ProviderError,
)
from app.sources.replay import session_close_at

MARKET_CODE = "NIFTY50"
MARKET_NAME = "Nifty 50"

# How far back a refresh re-asks for bars. Widened when the stored calendar
# is older than this, so a gap is never left in the middle of a price series.
REFRESH_LOOKBACK_DAYS = 30

# Below this, the intersection with a patchy sector index has cost more history
# than the comparison is worth, and no sector is the better answer.
MIN_SECTOR_SESSIONS = 120

# Calendar days fetched to obtain LIVE_HISTORY_SESSIONS trading sessions:
# weekends and holidays mean roughly a third of the span is not a session.
_CALENDAR_MULTIPLIER = 1.6

_client: object | None = None


class LiveNotConfigured(RuntimeError):
    """Live mode was requested without a usable provider configuration."""


class LiveDataUnavailable(RuntimeError):
    """The provider answered, but not with enough to classify anything."""


def client() -> object:
    """One provider client per process, so metadata is fetched once."""
    global _client
    # An injected client wins, so tests exercise this module without
    # reaching the network.
    if _client is not None:
        return _client
    if not live_enabled():
        raise LiveNotConfigured("live provider is not configured")
    _client = yahoo.YahooClient()
    return _client


def set_client(replacement: object | None) -> None:
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

    Retried once on a primary-key conflict: market data is shared across
    users, so two people adding the same new symbol -- or the very first live
    symbol anyone adds, which also creates the shared market index row -- can
    race to insert the same row. The loser's transaction is rolled back and
    replayed; by then the winner's row is committed and `_upsert_symbol` takes
    the update branch instead of colliding again.
    """
    try:
        return _add_instrument_once(db, symbol)
    except IntegrityError:
        db.rollback()
        return _add_instrument_once(db, symbol)


def _add_instrument_once(db: Session, symbol: str) -> Symbol:
    """Resolve a real instrument and store everything needed to classify it.

    The market index defines which days count as sessions, and a stock's bars
    are kept only for days the market also has: a stock priced on a day its
    market was not is a comparison that cannot be made.

    A sector index is optional rather than a second constraint. Some of the NSE
    sector series Yahoo publishes stop updating for weeks at a time, and
    intersecting against one of those silently truncated the stock's own history
    to the sector's last good day, which then read as a stock that had stopped
    trading. What matters is the end of the series, not the middle: a sector
    missing an interior session only costs that session from the baseline, while
    a sector missing the latest session would move the moment the brief is
    computed for. `_resolve_sector` draws exactly that line.
    """
    api = client()
    wanted = symbol.strip().upper()
    instrument = api.find(wanted)
    if instrument is None:
        raise LiveDataUnavailable(f"{wanted} is not a listed NSE equity")

    start, end = _history_window()
    market_bars = completed_sessions(api.daily_candles(NIFTY_50, start, end))
    if not market_bars:
        raise LiveDataUnavailable("no history returned for the market index")
    _store_index(db, MARKET_CODE, MARKET_NAME, True, NIFTY_50, market_bars)

    stock_bars = completed_sessions(api.daily_candles(instrument.instrument_key, start, end))
    if not stock_bars:
        raise LiveDataUnavailable(f"no historical data returned for {wanted}")

    market_days = {bar.day for bar in market_bars}
    aligned = [bar for bar in stock_bars if bar.day in market_days]
    if not aligned:
        raise LiveDataUnavailable(f"{wanted} shares no sessions with the market index")

    sector_code, aligned = _resolve_sector(db, api, instrument, aligned)

    row = _upsert_symbol(db, instrument, sector_code)
    _write_bars(db, wanted, aligned)
    _write_events(db, api, instrument)
    db.commit()

    refresh_quote(db, wanted, instrument=instrument)
    return row


def _resolve_sector(
    db: Session, api: object, instrument: Instrument, aligned: list[Bar]
) -> tuple[str | None, list[Bar]]:
    """Decide whether the sector can be used, and on which sessions.

    Kept when it reaches the stock's latest session and still leaves the
    classifier enough history after the intersection; dropped otherwise. Nothing
    is written until that decision is made, so an index that has stopped
    publishing never leaves half-populated rows behind it.

    A kept sector narrows the stored bars to the sessions all three series
    share, because a session the sector did not price is a comparison that
    cannot be made. A dropped sector changes nothing about the stock's history
    and is disclosed as an unavailable comparison instead.
    """
    if not instrument.sector_index:
        return None, aligned

    sector_days = {bar.day for bar in _fetch_bars(api, instrument.sector_index)}
    shared = [bar for bar in aligned if bar.day in sector_days]
    reaches_latest = bool(shared) and shared[-1].day == aligned[-1].day
    if not reaches_latest or len(shared) < MIN_SECTOR_SESSIONS:
        return None, aligned

    code = index_code_for(instrument.sector_index)
    _store_index(
        db,
        code,
        SECTOR_INDICES.get(instrument.sector_index, code),
        False,
        instrument.sector_index,
        [bar for bar in _fetch_bars(api, instrument.sector_index) if bar.day in sector_days],
    )
    return code, shared


def _fetch_bars(
    api: object, instrument_key: str, since: date | None = None
) -> list[Bar]:
    """Bars for an instrument, with a provider failure reported as no bars.

    Used where an absent series is a supported outcome -- an unusable sector, a
    top-up that could not reach the provider -- and never for the market index
    or the stock itself, where an empty answer has to be raised.
    """
    start, end = _history_window()
    if since is not None:
        start = min(start, since)
    try:
        return completed_sessions(api.daily_candles(instrument_key, start, end))
    except ProviderError:
        return []


def completed_sessions(bars: list[Bar]) -> list[Bar]:
    """Drop any bar for a session that has not closed yet.

    Asked for history while the market is trading, Yahoo returns a bar for
    today built from the prices so far. Storing it would freeze a half-formed
    close into the price series -- the writers skip days they already hold, so
    it would never be corrected -- and would hand the classifier an
    in-progress session as though it were a settled one.
    """
    now = utcnow()
    return [bar for bar in bars if datetime.combine(bar.day, NSE_CLOSE_UTC) <= now]


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
    except ProviderError:
        quote = None

    if quote is not None and quote.event_time is not None:
        price, event_time, prev_close = quote.price, quote.event_time, quote.prev_close
        age = utcnow() - event_time
        if age > timedelta(hours=LIVE_QUOTE_STALE_HOURS):
            freshness = Freshness.STALE
        elif quote.settled_close:
            # A settled close is trusted and is not a live observation. Calling
            # it LIVE would let the UI describe a Friday close as the current
            # market on a Sunday afternoon.
            freshness = Freshness.CLOSED
        else:
            freshness = Freshness.LIVE

    if price is None or event_time is None:
        # Fall back to the last stored close so the symbol still has a price to
        # show, labelled UNAVAILABLE so the classifier declines to infer from it.
        last = db.execute(
            select(TradingDay.day, DailyBar.close)
            .join(DailyBar, DailyBar.day == TradingDay.day)
            .where(DailyBar.symbol == symbol, TradingDay.source == SOURCE_LIVE)
            .order_by(TradingDay.day.desc())
            .limit(1)
        ).first()
        if last is None:
            raise LiveDataUnavailable(f"no price of any age is on record for {symbol}")
        event_time, price = session_close_at(last[0], SOURCE_LIVE), last[1]
        freshness = Freshness.UNAVAILABLE

    _store_quote(db, symbol, price, event_time, prev_close, freshness)
    return freshness


def _store_quote(
    db: Session,
    symbol: str,
    price: float,
    event_time: datetime,
    prev_close: float | None,
    freshness: Freshness,
) -> None:
    """Write a quote without ever letting a late one rewind a newer one.

    The ordering rule is enforced in the WHERE clause rather than by reading the
    row and deciding in Python: two refreshes for the same symbol can interleave
    between the read and the write, and the loser of that race would otherwise
    put the older price back.

    The guard is `<=`, not `<`. A re-poll of the same session is not new
    information about the price, but it is new information about the price's
    age, and that has to be allowed to land.
    """
    existing = db.get(QuoteRow, symbol)
    if existing is None:
        db.add(
            QuoteRow(
                symbol=symbol,
                price=price,
                event_time=event_time,
                ingested_at=utcnow(),
                source=provider_source(),
                freshness=freshness.value,
                prev_close=prev_close,
            )
        )
        db.commit()
        return

    values = {
        "price": price,
        "event_time": event_time,
        "ingested_at": utcnow(),
        "source": provider_source(),
        "freshness": freshness.value,
    }
    if prev_close is not None:
        values["prev_close"] = prev_close
    landed = db.execute(
        update(QuoteRow)
        .where(QuoteRow.symbol == symbol, QuoteRow.event_time <= event_time)
        .values(**values)
    ).rowcount

    if not landed and freshness in {Freshness.STALE, Freshness.UNAVAILABLE}:
        # A newer price is already stored, so keep it -- but stop claiming it is
        # trusted after a failed refresh or a stale provider response.
        db.execute(
            update(QuoteRow)
            .where(QuoteRow.symbol == symbol, QuoteRow.event_time > event_time)
            .values(ingested_at=utcnow(), freshness=freshness.value)
        )
    db.commit()
    # The UPDATE went round the ORM, so the instance in the identity map still
    # holds the old row. Expiring it means the next read sees what was stored.
    db.expire(existing)


def refresh_watchlist(db: Session, symbols: list[str]) -> dict[str, str]:
    """Re-poll the watchlist. One failing symbol does not stop the others.

    Quotes alone are not enough. Sessions close while nobody is looking, and a
    watchlist whose bars stopped on the day each symbol was added would leave
    the brief analysing a session those symbols hold no price for. The market
    index and each symbol's bars are topped up first, and only then are quotes
    re-asked for.
    """
    outcome: dict[str, str] = {}
    try:
        _topup_market(db)
    except (LiveNotConfigured, ProviderError):
        # A failed top-up leaves the stored history exactly as it was, which is
        # still classifiable. The quotes below are attempted regardless.
        pass

    for symbol in symbols:
        try:
            _topup_symbol(db, symbol)
            outcome[symbol] = refresh_quote(db, symbol).value
        except (LiveDataUnavailable, LiveNotConfigured, ProviderError) as exc:
            outcome[symbol] = f"unavailable: {exc}"
    return outcome


def _topup_since(db: Session, last_known: date | None) -> date:
    """Where a top-up fetch should start: at the last stored session, or earlier.

    Asking only for the last month would open a hole in the middle of the price
    series if the app had not run for longer than that, and a hole makes every
    rolling window that spans it wrong.
    """
    recent = utcnow().date() - timedelta(days=REFRESH_LOOKBACK_DAYS)
    return min(last_known, recent) if last_known else recent


def _topup_market(db: Session) -> None:
    last = db.scalar(
        select(TradingDay.day)
        .where(TradingDay.source == SOURCE_LIVE)
        .order_by(TradingDay.day.desc())
        .limit(1)
    )
    bars = _fetch_bars(client(), NIFTY_50, since=_topup_since(db, last))
    if bars:
        _store_index(db, MARKET_CODE, MARKET_NAME, True, NIFTY_50, bars)
        db.commit()


def _topup_symbol(db: Session, symbol: str) -> None:
    """Extend one symbol's bars to the sessions the market index now has."""
    row = db.get(Symbol, symbol)
    if row is None or row.source != SOURCE_LIVE or not row.instrument_key:
        return
    last = db.scalar(
        select(DailyBar.day)
        .where(DailyBar.symbol == symbol)
        .order_by(DailyBar.day.desc())
        .limit(1)
    )
    bars = _fetch_bars(client(), row.instrument_key, since=_topup_since(db, last))
    if not bars:
        return
    market_days = set(
        db.scalars(select(TradingDay.day).where(TradingDay.source == SOURCE_LIVE))
    )
    _write_bars(db, symbol, [bar for bar in bars if bar.day in market_days])
    db.commit()


def _history_window() -> tuple[date, date]:
    end = utcnow().date()
    return end - timedelta(days=int(LIVE_HISTORY_SESSIONS * _CALENDAR_MULTIPLIER)), end


def _store_index(
    db: Session,
    code: str,
    name: str,
    is_market: bool,
    instrument_key: str,
    bars: list[Bar],
) -> None:
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

    # `known` grows as rows are added, not only from what was already stored: a
    # provider that returns the same day twice in one response would otherwise
    # queue two inserts for one primary key and fail the whole write.
    known = set(db.scalars(select(IndexBar.day).where(IndexBar.index_code == code)))
    for bar in bars:
        if bar.day not in known:
            known.add(bar.day)
            db.add(IndexBar(index_code=code, day=bar.day, close=bar.close))

    if is_market:
        _write_calendar(db, [bar.day for bar in bars])
    db.flush()


def _write_calendar(db: Session, days: list[date]) -> None:
    """The market index defines which days were sessions for the live source."""
    known = set(
        db.scalars(select(TradingDay.day).where(TradingDay.source == SOURCE_LIVE))
    )
    for day in days:
        if day not in known:
            known.add(day)
            db.add(
                TradingDay(
                    day=day,
                    source=SOURCE_LIVE,
                    close_at=datetime.combine(day, NSE_CLOSE_UTC),
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


def _write_bars(db: Session, symbol: str, bars: list[Bar]) -> None:
    known = set(db.scalars(select(DailyBar.day).where(DailyBar.symbol == symbol)))
    for bar in bars:
        if bar.day not in known:
            known.add(bar.day)
            db.add(
                DailyBar(
                    symbol=symbol,
                    day=bar.day,
                    close=bar.close,
                    volume=bar.volume,
                    source=provider_source(),
                )
            )
    db.flush()


def _write_events(db: Session, api: object, instrument: Instrument) -> None:
    try:
        events = api.corporate_actions(instrument.instrument_key, instrument.symbol)
    except ProviderError:
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
                source=provider_source(),
            )
        )
    db.flush()


__all__ = [
    "LiveDataUnavailable",
    "completed_sessions",
    "LiveNotConfigured",
    "MARKET_CODE",
    "add_instrument",
    "client",
    "freshen",
    "index_code_for",
    "refresh_quote",
    "refresh_watchlist",
    "search",
    "set_client",
    "provider_source",
]


def provider_source() -> str:
    """Tag stored live quotes with the provider that supplied them."""
    return config.LIVE_PROVIDER if config.LIVE_PROVIDER == "yahoo" else "live"
