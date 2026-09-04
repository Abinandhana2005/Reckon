"""Domain types for the classification of a single watched symbol.

Every symbol on a watchlist receives exactly one Verdict. The set is mutually
exclusive and collectively exhaustive: there is no path through the classifier
that produces no verdict, which is what lets the product account for every
symbol rather than emitting a filtered subset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class Verdict(str, Enum):
    CANT_SAY = "CANT_SAY"
    EVENT = "EVENT"
    QUIET = "QUIET"
    WITH_MARKET = "WITH_MARKET"
    WITH_SECTOR = "WITH_SECTOR"
    UNEXPLAINED = "UNEXPLAINED"


class Epistemic(str, Enum):
    """How the verdict was arrived at. Surfaced in the UI on every card.

    KNOWN is a sourced fact. INFERRED is a comparison against a baseline and
    must never be phrased causally. UNKNOWN is an explicit absence of evidence.
    """

    KNOWN = "KNOWN"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class Freshness(str, Enum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"
    CLOSED = "CLOSED"
    DISPUTED = "DISPUTED"
    UNAVAILABLE = "UNAVAILABLE"


UNTRUSTED_FRESHNESS = frozenset(
    {Freshness.STALE, Freshness.DISPUTED, Freshness.UNAVAILABLE}
)


class EventKind(str, Enum):
    EX_DIVIDEND = "EX_DIVIDEND"
    SPLIT = "SPLIT"
    BONUS = "BONUS"
    RESULTS = "RESULTS"


ADJUSTING_EVENTS = frozenset({EventKind.EX_DIVIDEND, EventKind.SPLIT, EventKind.BONUS})


class Reason(str, Enum):
    """Why the classifier reached its verdict. Maps 1:1 to a copy template.

    Templates are kept out of the domain layer so that the classifier stays a
    pure function over numbers and the user-facing wording can be reviewed for
    causal language independently.
    """

    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    UNTRUSTED_QUOTE = "UNTRUSTED_QUOTE"
    UNADJUSTABLE_ACTION = "UNADJUSTABLE_ACTION"
    EVENT_OCCURRED = "EVENT_OCCURRED"
    EVENT_UPCOMING = "EVENT_UPCOMING"
    WITHIN_OWN_RANGE = "WITHIN_OWN_RANGE"
    TRACKED_MARKET = "TRACKED_MARKET"
    TRACKED_SECTOR = "TRACKED_SECTOR"
    NO_EXPLANATION_FOUND = "NO_EXPLANATION_FOUND"


@dataclass(frozen=True)
class CorporateEvent:
    symbol: str
    on_date: date
    kind: EventKind
    value: float | None = None
    detail: str = ""

    @property
    def adjusts_price(self) -> bool:
        return self.kind in ADJUSTING_EVENTS


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    event_time: datetime
    freshness: Freshness
    source: str


@dataclass(frozen=True)
class Anchor:
    """The user's own reference point for a symbol.

    Advanced only by an explicit acknowledgement, and only to the snapshot the
    user was actually shown. Never to `now`.
    """

    symbol: str
    at: datetime
    price: float
    snapshot_id: str | None = None


@dataclass(frozen=True)
class Comparison:
    """One percentile comparison, retained so the UI can show its working."""

    label: str
    observed: float
    percentile: float
    typical_abs: float
    sample_size: int


@dataclass
class Evidence:
    """Everything the classifier looked at, kept whatever the verdict.

    This is what makes "why did you NOT flag this?" possible: the reasoning for
    a suppressed symbol is retained rather than discarded at the filter step.
    """

    sessions_away: int
    adjusted_return: float | None = None
    market_return: float | None = None
    sector_return: float | None = None
    adjustment_factor: float = 1.0
    own: Comparison | None = None
    vs_market: Comparison | None = None
    vs_sector: Comparison | None = None
    sector_index: str | None = None
    sector_available: bool = True
    events_in_window: list[CorporateEvent] = field(default_factory=list)
    events_upcoming: list[CorporateEvent] = field(default_factory=list)
    volume_ratio: float | None = None
    quote_age_seconds: float | None = None
    quote_source: str | None = None
    history_bars: int = 0


@dataclass(frozen=True)
class Classification:
    symbol: str
    verdict: Verdict
    epistemic: Epistemic
    reason: Reason
    evidence: Evidence
    snapshot_id: str

    @property
    def needs_attention(self) -> bool:
        return self.verdict in (Verdict.EVENT, Verdict.UNEXPLAINED)
