"""What a market-data provider has to produce, and how it may fail.

Yahoo is the sole live-data adapter, and the writer above it consumes exactly
one vocabulary. That vocabulary lives in its own module rather than inside the
adapter, so a future adapter would share this shape rather than inventing its
own.

Nothing here knows what a verdict is. It is bars, instruments, quotes, the
sector each NSE symbol is compared against, and the two ways a provider can
fail to answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

NIFTY_50 = "NSE_INDEX|Nifty 50"
"""The market index, named the way the app stores it.

Each adapter translates this into whatever its own API calls the Nifty 50.
"""


class ProviderError(RuntimeError):
    """The provider could not answer. Carries no verdict of its own."""


class ProviderRateLimited(ProviderError):
    """Too many requests. The caller should back off, not retry in a loop."""


class ProviderUnavailable(ProviderError):
    """A transport, server-side or response-shape failure."""


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
    # True when the price is a session's settled close rather than a price
    # observed while the market was trading. Both are trusted; only one may be
    # described to a reader as the current market.
    settled_close: bool = False


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


# No provider tells us a stock's sector, so it is stated here rather than
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


def sector_for(symbol: str) -> str | None:
    return SYMBOL_SECTORS.get(symbol.upper())


__all__ = [
    "NIFTY_50",
    "SECTOR_INDICES",
    "SYMBOL_SECTORS",
    "Bar",
    "Instrument",
    "LiveQuote",
    "ProviderError",
    "ProviderRateLimited",
    "ProviderUnavailable",
    "sector_for",
]
