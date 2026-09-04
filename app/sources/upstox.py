"""The Upstox adapter: everything provider-specific lives here.

Split deliberately in two. The normalisers below are pure functions over
decoded JSON, so every shape this file has to cope with is testable without a
token, a network or a mock server. The client is the only part that speaks
HTTP, and it is the only part a test has to stand in for.

Nothing here knows what a verdict is. It turns Upstox payloads into the same
bars, quotes and events the fixture produces, and stops.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from app.config import (
    UPSTOX_ACCESS_TOKEN,
    UPSTOX_BASE_URL,
    UPSTOX_INSTRUMENTS_URL,
    UPSTOX_TIMEOUT_SECONDS,
)
from app.domain.verdicts import CorporateEvent, EventKind

NIFTY_50 = "NSE_INDEX|Nifty 50"

# Upstox does not tell us a stock's sector, so it is stated here rather than
# guessed. A symbol outside this map gets no sector index at all, which the
# classifier already reports as an unavailable comparison -- that is honest,
# where inferring a sector from whatever else the user happens to watch would
# manufacture a peer group that does not exist.
SECTOR_INDICES: dict[str, str] = {
    "NSE_INDEX|Nifty Bank": "Nifty Bank",
    "NSE_INDEX|Nifty IT": "Nifty IT",
    "NSE_INDEX|Nifty Pharma": "Nifty Pharma",
    "NSE_INDEX|Nifty FMCG": "Nifty FMCG",
    "NSE_INDEX|Nifty Auto": "Nifty Auto",
    "NSE_INDEX|Nifty Metal": "Nifty Metal",
    "NSE_INDEX|Nifty Energy": "Nifty Energy",
}

SYMBOL_SECTORS: dict[str, str] = {
    # Banking and financials
    "HDFCBANK": "NSE_INDEX|Nifty Bank",
    "ICICIBANK": "NSE_INDEX|Nifty Bank",
    "SBIN": "NSE_INDEX|Nifty Bank",
    "KOTAKBANK": "NSE_INDEX|Nifty Bank",
    "AXISBANK": "NSE_INDEX|Nifty Bank",
    "INDUSINDBK": "NSE_INDEX|Nifty Bank",
    "BANKBARODA": "NSE_INDEX|Nifty Bank",
    "PNB": "NSE_INDEX|Nifty Bank",
    "FEDERALBNK": "NSE_INDEX|Nifty Bank",
    "IDFCFIRSTB": "NSE_INDEX|Nifty Bank",
    # Information technology
    "TCS": "NSE_INDEX|Nifty IT",
    "INFY": "NSE_INDEX|Nifty IT",
    "WIPRO": "NSE_INDEX|Nifty IT",
    "HCLTECH": "NSE_INDEX|Nifty IT",
    "TECHM": "NSE_INDEX|Nifty IT",
    "LTIM": "NSE_INDEX|Nifty IT",
    "MPHASIS": "NSE_INDEX|Nifty IT",
    "PERSISTENT": "NSE_INDEX|Nifty IT",
    # Pharmaceuticals
    "SUNPHARMA": "NSE_INDEX|Nifty Pharma",
    "CIPLA": "NSE_INDEX|Nifty Pharma",
    "DRREDDY": "NSE_INDEX|Nifty Pharma",
    "DIVISLAB": "NSE_INDEX|Nifty Pharma",
    "AUROPHARMA": "NSE_INDEX|Nifty Pharma",
    "LUPIN": "NSE_INDEX|Nifty Pharma",
    "TORNTPHARM": "NSE_INDEX|Nifty Pharma",
    # Consumer goods
    "HINDUNILVR": "NSE_INDEX|Nifty FMCG",
    "ITC": "NSE_INDEX|Nifty FMCG",
    "NESTLEIND": "NSE_INDEX|Nifty FMCG",
    "BRITANNIA": "NSE_INDEX|Nifty FMCG",
    "DABUR": "NSE_INDEX|Nifty FMCG",
    "MARICO": "NSE_INDEX|Nifty FMCG",
    "GODREJCP": "NSE_INDEX|Nifty FMCG",
    "TATACONSUM": "NSE_INDEX|Nifty FMCG",
    # Automotive
    "MARUTI": "NSE_INDEX|Nifty Auto",
    "TATAMOTORS": "NSE_INDEX|Nifty Auto",
    "M&M": "NSE_INDEX|Nifty Auto",
    "BAJAJ-AUTO": "NSE_INDEX|Nifty Auto",
    "HEROMOTOCO": "NSE_INDEX|Nifty Auto",
    "EICHERMOT": "NSE_INDEX|Nifty Auto",
    "TVSMOTOR": "NSE_INDEX|Nifty Auto",
    # Metals
    "TATASTEEL": "NSE_INDEX|Nifty Metal",
    "JSWSTEEL": "NSE_INDEX|Nifty Metal",
    "HINDALCO": "NSE_INDEX|Nifty Metal",
    "VEDL": "NSE_INDEX|Nifty Metal",
    "SAIL": "NSE_INDEX|Nifty Metal",
    "JINDALSTEL": "NSE_INDEX|Nifty Metal",
    # Energy
    "RELIANCE": "NSE_INDEX|Nifty Energy",
    "ONGC": "NSE_INDEX|Nifty Energy",
    "NTPC": "NSE_INDEX|Nifty Energy",
    "POWERGRID": "NSE_INDEX|Nifty Energy",
    "BPCL": "NSE_INDEX|Nifty Energy",
    "IOC": "NSE_INDEX|Nifty Energy",
    "TATAPOWER": "NSE_INDEX|Nifty Energy",
    "COALINDIA": "NSE_INDEX|Nifty Energy",
}


class UpstoxError(RuntimeError):
    """The provider could not answer. Carries no verdict of its own."""


class UpstoxNotConfigured(UpstoxError):
    """Live mode was asked for without a token."""


class UpstoxRateLimited(UpstoxError):
    """Too many requests. The caller should back off, not retry in a loop."""


class UpstoxUnavailable(UpstoxError):
    """A transport or server-side failure."""


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    instrument_key: str
    isin: str | None
    sector_index: str | None

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "instrument_key": self.instrument_key,
            "isin": self.isin,
            "sector_index": self.sector_index,
        }


@dataclass(frozen=True)
class Bar:
    day: date
    close: float
    volume: int


@dataclass(frozen=True)
class LiveQuote:
    price: float
    # None when the provider sent no timestamp. The caller must not invent one:
    # an age that cannot be established is what CANT_SAY exists for.
    event_time: datetime | None
    prev_close: float | None


# --------------------------------------------------------------------------
# Normalisers: pure, and the only place Upstox's field names are understood.
# --------------------------------------------------------------------------


def sector_for(symbol: str) -> str | None:
    return SYMBOL_SECTORS.get(symbol.upper())


def normalize_instruments(payload: Any) -> list[Instrument]:
    """Turn the NSE instrument master into equities we can watch.

    Filters to cash-segment equities: the master also carries derivatives and
    indices, and neither is something a person puts on a watchlist.
    """
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    instruments: list[Instrument] = []
    seen: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        segment = (row.get("segment") or "").upper()
        kind = (row.get("instrument_type") or "").upper()
        if segment != "NSE_EQ" or kind not in {"EQ", "EQUITY"}:
            continue

        symbol = (row.get("trading_symbol") or row.get("tradingsymbol") or "").strip().upper()
        key = (row.get("instrument_key") or "").strip()
        if not symbol or not key or symbol in seen:
            continue
        seen.add(symbol)

        instruments.append(
            Instrument(
                symbol=symbol,
                name=(row.get("name") or symbol).strip(),
                instrument_key=key,
                isin=(row.get("isin") or None),
                sector_index=sector_for(symbol),
            )
        )
    return instruments


def search_instruments(instruments: list[Instrument], query: str, limit: int = 25) -> list[Instrument]:
    """Ticker matches first, then company names, so an exact ticker leads."""
    needle = (query or "").strip().upper()
    if not needle:
        return instruments[:limit]

    exact = [i for i in instruments if i.symbol == needle]
    prefix = [i for i in instruments if i.symbol.startswith(needle) and i.symbol != needle]
    named = [
        i
        for i in instruments
        if needle in i.name.upper() and i not in exact and i not in prefix
    ]
    return (exact + prefix + named)[:limit]


def normalize_candles(payload: Any) -> list[Bar]:
    """Upstox candles are [timestamp, open, high, low, close, volume, oi].

    Returned newest-first by the API; the classifier wants oldest-first, and a
    malformed row is dropped rather than allowed to become a false price.
    """
    candles = (payload or {}).get("data", {}).get("candles") or []
    bars: list[Bar] = []
    for candle in candles:
        if not isinstance(candle, (list, tuple)) or len(candle) < 6:
            continue
        stamp = _parse_datetime(candle[0])
        close = _as_float(candle[4])
        if stamp is None or close is None:
            continue
        bars.append(Bar(day=stamp.date(), close=close, volume=int(_as_float(candle[5]) or 0)))

    bars.sort(key=lambda bar: bar.day)
    # The same session can arrive twice across a paged range; the last one wins.
    deduped: dict[date, Bar] = {bar.day: bar for bar in bars}
    return [deduped[day] for day in sorted(deduped)]


def normalize_quote(payload: Any, instrument_key: str) -> LiveQuote | None:
    """Read one instrument out of a market-quote response.

    Upstox keys the response by a display form of the instrument key rather
    than the key itself, so the entry is matched on its own instrument_token
    where present and by key shape otherwise.
    """
    data = (payload or {}).get("data") or {}
    entry = _match_quote_entry(data, instrument_key)
    if entry is None:
        return None

    price = _as_float(entry.get("last_price"))
    if price is None:
        return None

    ohlc = entry.get("ohlc") or {}
    stamp = (
        _parse_datetime(entry.get("last_trade_time"))
        or _parse_datetime(entry.get("timestamp"))
    )
    return LiveQuote(
        price=price,
        # A quote with no timestamp is not treated as current; the caller
        # decides, and the freshness model turns "unknown age" into CANT_SAY.
        event_time=stamp,
        prev_close=_as_float(ohlc.get("close")),
    )


def normalize_corporate_actions(payload: Any, symbol: str) -> list[CorporateEvent]:
    """Normalise whatever corporate actions the provider returns.

    Only the three adjustment-safe kinds plus results are kept. Anything whose
    kind or date cannot be read is dropped: an event the adjustment cannot use
    is worse than no event, because it would silently misprice the anchor.
    """
    rows = (payload or {}).get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("corporate_actions") or rows.get("actions") or []

    events: list[CorporateEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = _event_kind(row)
        on_date = _parse_date(
            row.get("ex_date") or row.get("exDate") or row.get("record_date") or row.get("date")
        )
        if kind is None or on_date is None:
            continue

        value = _as_float(row.get("value") or row.get("ratio") or row.get("dividend"))
        if kind in {EventKind.SPLIT, EventKind.BONUS, EventKind.EX_DIVIDEND} and value is None:
            continue

        events.append(
            CorporateEvent(
                symbol=symbol,
                on_date=on_date,
                kind=kind,
                value=value,
                detail=str(row.get("purpose") or row.get("description") or kind.value),
            )
        )
    return sorted(events, key=lambda event: event.on_date)


_KIND_WORDS = (
    (EventKind.SPLIT, ("split", "sub-division", "subdivision")),
    (EventKind.BONUS, ("bonus",)),
    (EventKind.EX_DIVIDEND, ("dividend",)),
    (EventKind.RESULTS, ("result", "board meeting", "earnings")),
)


def _event_kind(row: dict) -> EventKind | None:
    text = " ".join(
        str(row.get(field) or "")
        for field in ("purpose", "description", "type", "corporate_action_type", "subject")
    ).lower()
    for kind, words in _KIND_WORDS:
        if any(word in text for word in words):
            return kind
    return None


def _match_quote_entry(data: dict, instrument_key: str) -> dict | None:
    for entry in data.values():
        if isinstance(entry, dict) and entry.get("instrument_token") == instrument_key:
            return entry
    # Fall back to the display form: "NSE_EQ|INE002A01018" becomes "NSE_EQ:RELIANCE",
    # so no key comparison is possible and a single-instrument request is assumed.
    if len(data) == 1:
        only = next(iter(data.values()))
        return only if isinstance(only, dict) else None
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Stored naive throughout, matching the fixture's session timestamps.
    return parsed.replace(tzinfo=None)


def _parse_date(value: Any) -> date | None:
    stamp = _parse_datetime(value)
    if stamp is not None:
        return stamp.date()
    if isinstance(value, str):
        for form in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(value, form).date()
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------
# Client: the only part that speaks HTTP.
# --------------------------------------------------------------------------


class UpstoxClient:
    """A thin, synchronous Upstox reader.

    On-demand only. There is no polling loop and no scheduler, because the
    product asks a question when a user opens it and not before.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = UPSTOX_BASE_URL,
        instruments_url: str = UPSTOX_INSTRUMENTS_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = token or UPSTOX_ACCESS_TOKEN
        if not self._token:
            raise UpstoxNotConfigured("UPSTOX_ACCESS_TOKEN is not set")
        self._base_url = base_url.rstrip("/")
        self._instruments_url = instruments_url
        self._transport = transport
        self._instruments: list[Instrument] | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=UPSTOX_TIMEOUT_SECONDS,
            transport=self._transport,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
        )

    def _get(self, url: str, params: dict | None = None) -> Any:
        try:
            with self._client() as client:
                response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise UpstoxUnavailable(f"could not reach Upstox: {exc}") from exc

        if response.status_code == 429:
            raise UpstoxRateLimited("Upstox rate limit reached")
        if response.status_code in {401, 403}:
            raise UpstoxNotConfigured("Upstox rejected the access token")
        if response.status_code >= 400:
            raise UpstoxUnavailable(f"Upstox returned {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            raise UpstoxUnavailable("Upstox returned a response that was not JSON") from exc

    def instruments(self, *, refresh: bool = False) -> list[Instrument]:
        """The NSE instrument master, fetched once per process.

        Upstox publishes this as a file rather than offering a search endpoint,
        so search is done locally over it. It is a few megabytes and changes
        daily, which makes one fetch per process the right trade.
        """
        if self._instruments is not None and not refresh:
            return self._instruments

        try:
            with self._client() as client:
                response = client.get(self._instruments_url)
                response.raise_for_status()
                body = response.content
        except httpx.HTTPError as exc:
            raise UpstoxUnavailable(f"could not fetch the instrument list: {exc}") from exc

        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise UpstoxUnavailable("the instrument list was not JSON") from exc

        self._instruments = normalize_instruments(payload)
        return self._instruments

    def search(self, query: str, limit: int = 25) -> list[Instrument]:
        return search_instruments(self.instruments(), query, limit)

    def find(self, symbol: str) -> Instrument | None:
        wanted = symbol.strip().upper()
        return next((i for i in self.instruments() if i.symbol == wanted), None)

    def daily_candles(self, instrument_key: str, start: date, end: date) -> list[Bar]:
        url = (
            f"{self._base_url}/historical-candle/{instrument_key}/day/"
            f"{end.isoformat()}/{start.isoformat()}"
        )
        return normalize_candles(self._get(url))

    def quote(self, instrument_key: str) -> LiveQuote | None:
        url = f"{self._base_url}/market-quote/quotes"
        return normalize_quote(self._get(url, {"instrument_key": instrument_key}), instrument_key)

    def corporate_actions(self, instrument_key: str, symbol: str) -> list[CorporateEvent]:
        """Corporate actions, where the token's plan exposes them.

        Upstox does not document a corporate-actions endpoint for the analytics
        token, so a refusal here is expected rather than exceptional: it is
        reported as "no events on record", never as "no events happened".
        """
        url = f"{self._base_url}/corporate-actions"
        try:
            payload = self._get(url, {"instrument_key": instrument_key})
        except UpstoxError:
            return []
        return normalize_corporate_actions(payload, symbol)
