"""Yahoo Finance adapter for Reckon's optional live data source.

This module is deliberately the only place that knows about yfinance.  It
normalises Yahoo's pandas frames and quote dictionaries into the small provider
types already consumed by ``app.sources.live``.  The rest of Live Mode keeps
using the existing tables, reader and classifier.

Yahoo does not expose a dependable NSE instrument master.  Search therefore
uses Yahoo's lightweight search endpoint, which resolves both tickers and
company names, and keeps only the ``.NS`` listings it returns.  It never
downloads the whole exchange universe.

A company-name query sometimes comes back with the Bombay listing and no NSE
one -- "Infosys" returns ``INFY.BO`` but not ``INFY.NS``.  Those are resolved by
looking the same trading symbol up on NSE and keeping it only if Yahoo confirms
it, so the result is still an instrument Yahoo actually lists rather than a
ticker this module guessed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.clock import NSE_CLOSE_UTC, utcnow
from app.domain.verdicts import CorporateEvent, EventKind
from app.sources.provider import (
    Bar,
    Instrument,
    LiveQuote,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
    sector_for,
)

YAHOO_SUFFIX = ".NS"
NIFTY_50 = "^NSEI"

# These are Yahoo symbols for NSE indices that are published consistently
# enough to be useful.  A missing response remains an unavailable sector; it is
# never replaced with a return inferred from the user's watchlist.
SECTOR_TICKERS: dict[str, str] = {
    "NSE_INDEX|Nifty Bank": "^NSEBANK",
    "NSE_INDEX|Nifty IT": "^CNXIT",
    "NSE_INDEX|Nifty Pharma": "^CNXPHARMA",
    "NSE_INDEX|Nifty FMCG": "^CNXFMCG",
    "NSE_INDEX|Nifty Auto": "^CNXAUTO",
    "NSE_INDEX|Nifty Metal": "^CNXMETAL",
    "NSE_INDEX|Nifty Energy": "^CNXENERGY",
}

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,22}$")


class YahooError(ProviderError):
    """Yahoo could not answer without implying a market verdict."""


class YahooRateLimited(YahooError, ProviderRateLimited):
    """Yahoo or an upstream Yahoo endpoint asked the caller to slow down."""


class YahooUnavailable(YahooError, ProviderUnavailable):
    """Transport, provider or response-shape failure."""


def yahoo_ticker(symbol_or_index: str) -> str:
    """Convert a bare NSE symbol or stored index name to a Yahoo ticker."""
    value = symbol_or_index.strip().upper()
    if value in {"NIFTY50", "NSE_INDEX|NIFTY 50", "NSE_INDEX|NIFTY50"}:
        return NIFTY_50
    sector_ticker = next(
        (ticker for index_name, ticker in SECTOR_TICKERS.items() if value == index_name.upper()),
        None,
    )
    if sector_ticker is not None:
        return sector_ticker
    if value.startswith("^") or value.endswith(YAHOO_SUFFIX):
        return value
    return f"{value}{YAHOO_SUFFIX}"


def symbol_from_ticker(ticker: str) -> str:
    value = ticker.strip().upper()
    return value.removesuffix(YAHOO_SUFFIX)


BSE_SUFFIX = ".BO"

MAX_CROSS_LISTING_LOOKUPS = 4
"""How many Bombay listings a fallback search will try to resolve onto NSE.

One extra request each, and only when the direct search found no NSE listing at
all, so an ordinary ticker search still costs exactly one call.
"""


def search_rows(payload: Any) -> list[Mapping[str, Any]]:
    """The quote rows out of whatever shape yfinance handed back."""
    if isinstance(payload, Mapping):
        rows = payload.get("quotes") or payload.get("data") or []
    else:
        rows = getattr(payload, "quotes", payload)
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, (list, tuple)) else []


def bse_symbols(payload: Any) -> list[str]:
    """Trading symbols of the Bombay equity listings in a search response.

    NSE and BSE share a trading symbol for most listed companies, but "most" is
    not "all", so these are candidates to look up rather than tickers to use.
    """
    found: list[str] = []
    for row in search_rows(payload):
        ticker = str(row.get("symbol") or "").strip().upper()
        if not ticker.endswith(BSE_SUFFIX):
            continue
        quote_type = str(row.get("quoteType") or row.get("typeDisp") or "").upper()
        if quote_type and "EQUITY" not in quote_type:
            continue
        symbol = ticker.removesuffix(BSE_SUFFIX)
        if symbol and symbol not in found:
            found.append(symbol)
    return found


def normalize_search_results(payload: Any, query: str = "", limit: int = 25) -> list[Instrument]:
    """Normalise yfinance Search quotes to NSE equity instruments."""
    rows = search_rows(payload)
    needle = (query or "").strip().upper().removesuffix(YAHOO_SUFFIX)
    found: list[Instrument] = []
    seen: set[str] = set()
    # Yahoo returns rows in relevance order; that order is worth keeping, so
    # position is recorded and only an exact ticker match is allowed to jump.
    rank: dict[str, int] = {}
    for row in rows:
        ticker = str(row.get("symbol") or "").strip().upper()
        if not ticker.endswith(YAHOO_SUFFIX):
            continue
        quote_type = str(row.get("quoteType") or row.get("typeDisp") or "").upper()
        exchange = str(row.get("exchange") or "").upper()
        if quote_type and "EQUITY" not in quote_type and quote_type not in {"STOCK", "COMMON STOCK"}:
            continue
        if exchange and exchange not in {"NSI", "NSE", "NSEI"} and not ticker.endswith(YAHOO_SUFFIX):
            continue
        symbol = symbol_from_ticker(ticker)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        rank[symbol] = len(rank)
        found.append(
            Instrument(
                symbol=symbol,
                name=str(row.get("longname") or row.get("shortname") or symbol).strip(),
                instrument_key=ticker,
                isin=None,
                sector_index=sector_for(symbol),
            )
        )
        if len(found) >= limit:
            break

    # Yahoo occasionally ranks a foreign listing above the NSE one for a bare
    # ticker, so an exact match on the typed symbol is hoisted. Everything else
    # keeps the provider's relevance order, which is what makes a company-name
    # search useful rather than alphabetical.
    found.sort(key=lambda item: (item.symbol != needle, rank[item.symbol]))
    return found[:limit]


def normalize_history(frame: Any) -> list[Bar]:
    """Turn a yfinance daily DataFrame into oldest-first normalized bars."""
    if frame is None or not hasattr(frame, "iterrows"):
        return []
    bars: dict[date, Bar] = {}
    for index, row in frame.iterrows():
        day = _as_date(index)
        close = _as_float(_field(row, "Close"))
        if day is None or close is None or close <= 0:
            continue
        volume = _as_float(_field(row, "Volume")) or 0
        bars[day] = Bar(day=day, close=close, volume=max(0, int(volume)))
    return [bars[day] for day in sorted(bars)]


def normalize_quote_from_history(
    frame: Any, symbol: str, now: datetime | None = None
) -> LiveQuote | None:
    """Turn the most recent daily bar into a quote, saying which kind it is.

    Two different things arrive in the same shape. Once a session has closed,
    its bar is a settled close and is stamped at the closing bell -- in the
    app's naive-UTC frame, because stamping 15:30 naive would date it five and a
    half hours into its own future.

    While a session is still trading, Yahoo returns a bar for today built from
    the prices so far. That is a live observation, not a close: stamping it at
    the bell would put the timestamp in the future and label a mid-session price
    as settled, which is the one thing the freshness model must not say.
    """
    bars = normalize_history(frame)
    if not bars:
        return None
    moment = now or utcnow()
    last = bars[-1]
    previous = bars[-2].close if len(bars) > 1 else None
    close_at = datetime.combine(last.day, NSE_CLOSE_UTC)
    if close_at <= moment:
        return LiveQuote(
            price=last.close,
            event_time=close_at,
            prev_close=previous,
            settled_close=True,
        )
    return LiveQuote(price=last.close, event_time=moment, prev_close=previous)


def normalize_actions(frame: Any, symbol: str) -> list[CorporateEvent]:
    """Normalise Yahoo's dividend and split columns when supplied."""
    if frame is None or not hasattr(frame, "iterrows"):
        return []
    events: list[CorporateEvent] = []
    for index, row in frame.iterrows():
        day = _as_date(index)
        if day is None:
            continue
        dividend = _as_float(_field(row, "Dividends")) or 0.0
        split = _as_float(_field(row, "Stock Splits")) or 0.0
        if dividend > 0:
            events.append(
                CorporateEvent(
                    symbol=symbol,
                    on_date=day,
                    kind=EventKind.EX_DIVIDEND,
                    value=dividend,
                    detail=f"Dividend {dividend:g}",
                )
            )
        if split > 0 and split != 1.0:
            events.append(
                CorporateEvent(
                    symbol=symbol,
                    on_date=day,
                    kind=EventKind.SPLIT,
                    value=split,
                    detail=f"Split {split:g}:1",
                )
            )
    return sorted(events, key=lambda event: (event.on_date, event.kind.value))


class YahooClient:
    """Small synchronous yfinance client with injectable boundaries for tests."""

    def __init__(
        self,
        *,
        ticker_factory: Callable[[str], Any] | None = None,
        search_factory: Callable[..., Any] | None = None,
    ) -> None:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise YahooUnavailable("yfinance is not installed") from exc
        session = _yahoo_session() if ticker_factory is None or search_factory is None else None
        self._ticker_factory = ticker_factory or _ticker_factory(yf, session)
        self._search_factory = search_factory or _search_factory(yf, session)
        self._history_cache: dict[tuple[str, date, date], tuple[datetime, Any]] = {}

    def _ticker(self, symbol_or_index: str) -> Any:
        try:
            return self._ticker_factory(yahoo_ticker(symbol_or_index))
        except Exception as exc:  # yfinance exposes no stable transport exception type.
            _raise_provider_error(exc)

    def search(self, query: str, limit: int = 25) -> list[Instrument]:
        """Resolve a ticker or a company name to NSE listings.

        The raw term is sent first. Appending ``.NS`` up front looks harmless
        and is not: Yahoo matches ``TCS.NS`` but returns nothing at all for
        ``TATA CONSULTANCY.NS``, which silently reduced search to
        exact-ticker-only. The suffixed form is kept as a second attempt for
        the case where a bare ticker is ambiguous across exchanges.
        """
        needle = (query or "").strip().upper().removesuffix(YAHOO_SUFFIX)
        if not needle:
            return []

        payload = self._raw_search(needle, limit)
        instruments = normalize_search_results(payload, needle, limit)
        if instruments:
            return instruments

        if _SYMBOL_RE.fullmatch(needle):
            suffixed = self._raw_search(f"{needle}{YAHOO_SUFFIX}", limit)
            instruments = normalize_search_results(suffixed, needle, limit)
            if instruments:
                return instruments

        return self._resolve_cross_listings(payload, needle, limit)

    def _resolve_cross_listings(self, payload: Any, needle: str, limit: int) -> list[Instrument]:
        """Turn Bombay listings into their NSE counterparts, where Yahoo has one."""
        found: list[Instrument] = []
        seen: set[str] = set()
        for symbol in bse_symbols(payload)[:MAX_CROSS_LISTING_LOOKUPS]:
            confirmed = normalize_search_results(
                self._raw_search(f"{symbol}{YAHOO_SUFFIX}", limit), symbol, limit
            )
            match = next((item for item in confirmed if item.symbol == symbol), None)
            if match is not None and match.symbol not in seen:
                seen.add(match.symbol)
                found.append(match)
            if len(found) >= limit:
                break
        return found

    def _raw_search(self, term: str, limit: int) -> Any:
        try:
            return self._search_factory(term, max_results=limit)
        except Exception as exc:  # yfinance exposes no stable transport exception type.
            _raise_provider_error(exc)

    def find(self, symbol: str) -> Instrument | None:
        wanted = symbol.strip().upper().removesuffix(YAHOO_SUFFIX)
        if not _SYMBOL_RE.fullmatch(wanted):
            return None
        matches = self.search(wanted, limit=10)
        exact = next((item for item in matches if item.symbol == wanted), None)
        if exact is not None:
            return exact
        return Instrument(
            symbol=wanted,
            name=wanted,
            instrument_key=f"{wanted}{YAHOO_SUFFIX}",
            isin=None,
            sector_index=sector_for(wanted),
        )

    def daily_candles(self, instrument_key: str, start: date, end: date) -> list[Bar]:
        ticker = instrument_key
        frame = self._history(ticker, start=start, end=end)
        return normalize_history(frame)

    def quote(self, instrument_key: str) -> LiveQuote | None:
        """The latest price Yahoo will give for this instrument.

        Taken from recent daily bars rather than from ``fast_info``. fast_info
        is one extra request per symbol per refresh and carries no trade
        timestamp for NSE tickers -- none of its fields is a time -- so a quote
        built from it could never establish its own age, and the code fell
        through to this call anyway. One request now answers price, timestamp
        and whether the session has closed.
        """
        ticker = self._ticker(instrument_key)
        try:
            frame = ticker.history(period="5d", interval="1d", auto_adjust=False, actions=False)
        except Exception as exc:  # yfinance exposes no stable transport exception type.
            _raise_provider_error(exc)
        return normalize_quote_from_history(frame, symbol_from_ticker(instrument_key))

    def corporate_actions(self, instrument_key: str, symbol: str) -> list[CorporateEvent]:
        """Splits and dividends, reusing the frame the history fetch already holds.

        The history call asks for ``actions=True``, so the dividend and split
        columns are already there. Re-requesting them would double this
        symbol's share of Yahoo's rate limit for no new information.
        """
        frame = self._cached_frame(yahoo_ticker(instrument_key))
        if frame is None:
            ticker = self._ticker(instrument_key)
            try:
                frame = ticker.history(period="1y", interval="1d", auto_adjust=False, actions=True)
            except Exception as exc:  # yfinance exposes no stable transport exception type.
                _raise_provider_error(exc)
        return normalize_actions(frame, symbol)

    def _cached_frame(self, ticker_name: str) -> Any | None:
        """The widest live cached frame for a ticker, whatever window fetched it."""
        candidates = [
            (key, entry)
            for key, entry in self._history_cache.items()
            if key[0] == ticker_name and not _expired(entry[0])
        ]
        if not candidates:
            return None
        widest = max(candidates, key=lambda item: item[0][2] - item[0][1])
        return widest[1][1]

    def _history(self, instrument_key: str, *, start: date, end: date) -> Any:
        """Daily bars for one window, cached briefly.

        The cache is keyed by the window as well as the ticker: one client is
        shared by the whole process, and a cache keyed by ticker alone answered
        a later, different window with the first window's frame. The short life
        is what makes a refresh actually re-ask Yahoo while still fetching the
        market index once per brief rather than once per symbol.
        """
        ticker_name = yahoo_ticker(instrument_key)
        key = (ticker_name, start, end)
        cached = self._history_cache.get(key)
        if cached is not None and not _expired(cached[0]):
            return cached[1]
        ticker = self._ticker(ticker_name)
        try:
            frame = ticker.history(
                start=start.isoformat(),
                end=end.isoformat(),
                interval="1d",
                auto_adjust=False,
                actions=True,
            )
        except Exception as exc:  # yfinance exposes no stable transport exception type.
            _raise_provider_error(exc)
        if frame is None or getattr(frame, "empty", False):
            return frame
        self._history_cache = {
            existing: entry
            for existing, entry in self._history_cache.items()
            if not _expired(entry[0])
        }
        self._history_cache[key] = (utcnow(), frame)
        return frame


HISTORY_CACHE_SECONDS = 120
"""How long a fetched frame may be reused.

Long enough that one brief fetches the market index once instead of once per
watched symbol; short enough that pressing Refresh reaches Yahoo again.
"""


def _expired(fetched_at: datetime) -> bool:
    return utcnow() - fetched_at > timedelta(seconds=HISTORY_CACHE_SECONDS)


def _raise_provider_error(exc: Exception) -> None:
    text = str(exc).lower()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        raise YahooRateLimited("Yahoo rate limit reached") from exc
    raise YahooUnavailable(f"Yahoo request failed: {exc}") from exc


def _yahoo_session() -> Any | None:
    """Use yfinance's supported session hook with a browser-like request profile.

    Yahoo's public endpoints rate-limit the default curl identity in some
    environments.  This is still the public, credential-free yfinance path;
    it does not bypass authentication or change the data source.
    """
    try:
        from curl_cffi import requests
    except ImportError:
        return None
    session = requests.Session(impersonate="chrome")
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            ),
            "Accept": "application/json",
        }
    )
    return session


def _ticker_factory(yfinance: Any, session: Any | None) -> Callable[[str], Any]:
    if session is None:
        return yfinance.Ticker
    return lambda ticker: yfinance.Ticker(ticker, session=session)


def _search_factory(yfinance: Any, session: Any | None) -> Callable[..., Any]:
    if session is None:
        return yfinance.Search
    return lambda query, max_results: yfinance.Search(
        query, max_results=max_results, session=session
    )


def _field(row: Any, name: str) -> Any:
    try:
        value = row[name]
    except (KeyError, TypeError, IndexError):
        return None
    # A MultiIndex column can leave a one-element Series here.  Selecting its
    # scalar keeps normalisation deterministic without depending on pandas.
    if hasattr(value, "iloc") and len(value) == 1:
        return value.iloc[0]
    return value


def _as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        converted = value.to_pydatetime()
        return converted.date() if isinstance(converted, datetime) else None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, (int, float)):
        try:
            stamp = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return stamp.astimezone(timezone.utc).replace(tzinfo=None) if stamp.tzinfo else stamp


__all__ = [
    "NIFTY_50",
    "SECTOR_TICKERS",
    "YahooClient",
    "YahooError",
    "YahooRateLimited",
    "YahooUnavailable",
    "normalize_actions",
    "normalize_history",
    "normalize_quote_from_history",
    "bse_symbols",
    "normalize_search_results",
    "sector_for",
    "symbol_from_ticker",
    "yahoo_ticker",
]
