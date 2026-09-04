"""Assign one verdict to one symbol, for one user, at one moment.

This is a pure function. It performs no I/O, reads no clock, and holds no state:
the caller supplies the time, the prices and the anchor. That is what makes the
whole surface testable against designed scenarios rather than against whatever
the market happened to do today.

The precedence below is the product's definition of meaningful change, executed:

    a fact outranks a comparison, a comparison outranks a statistic,
    and doubt about the data outranks all of them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.adjustments import UnadjustableAction, adjustment_factor
from app.domain.statistics import (
    MIN_HISTORY_BARS,
    UNUSUAL_PERCENTILE,
    WINDOW_COUNT,
    paired_differences,
    percentile_rank,
    typical_magnitude,
    window_returns,
)
from app.domain.verdicts import (
    UNTRUSTED_FRESHNESS,
    Anchor,
    Classification,
    Comparison,
    CorporateEvent,
    Epistemic,
    Evidence,
    Quote,
    Reason,
    Verdict,
)

MAX_GAP_SESSIONS = 10
"""Longest gap we build a baseline for.

A longer absence is still reported, but compared against a 10-session baseline
and labelled as such, because a 40-session window would leave only 20 non-
overlapping observations to compare against.
"""

UPCOMING_EVENT_SESSIONS = 3


@dataclass(frozen=True)
class SymbolContext:
    """Market data for one symbol, plus the indices it is compared against."""

    symbol: str
    sessions: list[date]
    closes: list[float]
    market_closes: list[float]
    sector_index: str | None = None
    sector_closes: list[float] | None = None
    volume_ratio: float | None = None

    def __post_init__(self) -> None:
        if len(self.sessions) != len(self.closes):
            raise ValueError("sessions and closes must align")
        if len(self.market_closes) != len(self.closes):
            raise ValueError("market closes must align with symbol closes")
        if self.sector_closes is not None and len(self.sector_closes) != len(self.closes):
            raise ValueError("sector closes must align with symbol closes")

    @property
    def has_sector(self) -> bool:
        return self.sector_index is not None and self.sector_closes is not None


@dataclass(frozen=True)
class ClassificationRequest:
    context: SymbolContext
    anchor: Anchor
    quote: Quote
    now: datetime
    events: list[CorporateEvent]


def classify(request: ClassificationRequest) -> Classification:
    context = request.context
    anchor_index = _index_of_anchor(context.sessions, request.anchor.at.date())
    sessions_away = _sessions_away(context.sessions, anchor_index)

    evidence = Evidence(
        sessions_away=sessions_away,
        sector_index=context.sector_index,
        sector_available=context.has_sector,
        volume_ratio=context.volume_ratio,
        quote_age_seconds=(request.now - request.quote.event_time).total_seconds(),
        quote_source=request.quote.source,
        history_bars=max(anchor_index, 0),
    )
    snapshot = _snapshot_id(request)

    if request.quote.freshness in UNTRUSTED_FRESHNESS:
        return _cant_say(request, evidence, snapshot, Reason.UNTRUSTED_QUOTE)

    window = min(max(sessions_away, 1), MAX_GAP_SESSIONS)
    if anchor_index < 0 or anchor_index < MIN_HISTORY_BARS:
        return _cant_say(request, evidence, snapshot, Reason.INSUFFICIENT_HISTORY)

    window_events = [
        event
        for event in request.events
        if _in_window(event.on_date, context.sessions, anchor_index)
    ]
    evidence.events_in_window = window_events
    evidence.events_upcoming = _upcoming(request.events, context.sessions[-1])

    try:
        factor = adjustment_factor(
            [e for e in window_events if e.adjusts_price],
            dict(zip(context.sessions, context.closes)),
        )
    except UnadjustableAction:
        return _cant_say(request, evidence, snapshot, Reason.UNADJUSTABLE_ACTION)

    evidence.adjustment_factor = factor
    effective_anchor = request.anchor.price * factor
    evidence.adjusted_return = request.quote.price / effective_anchor - 1.0
    evidence.market_return = _index_return(context.market_closes, anchor_index)
    if context.has_sector:
        evidence.sector_return = _index_return(context.sector_closes, anchor_index)

    if window_events:
        return _classified(request, evidence, snapshot, Verdict.EVENT, Epistemic.KNOWN, Reason.EVENT_OCCURRED)
    if evidence.events_upcoming:
        return _classified(request, evidence, snapshot, Verdict.EVENT, Epistemic.KNOWN, Reason.EVENT_UPCOMING)

    baseline = _baseline_slice(context, anchor_index, window)
    evidence.own = _compare(evidence.adjusted_return, baseline.own, "own history")
    evidence.vs_market = _compare(
        evidence.adjusted_return - evidence.market_return, baseline.vs_market, "vs market"
    )
    if context.has_sector:
        evidence.vs_sector = _compare(
            evidence.adjusted_return - evidence.sector_return,
            baseline.vs_sector,
            "vs sector",
        )

    own_normal = evidence.own.percentile < UNUSUAL_PERCENTILE
    market_normal = evidence.vs_market.percentile < UNUSUAL_PERCENTILE

    # QUIET requires both: a stock that sat still through a market-wide rally
    # has a small absolute move but an unusual gap to the market, and is not quiet.
    if own_normal and market_normal:
        return _classified(request, evidence, snapshot, Verdict.QUIET, Epistemic.INFERRED, Reason.WITHIN_OWN_RANGE)
    if market_normal:
        return _classified(request, evidence, snapshot, Verdict.WITH_MARKET, Epistemic.INFERRED, Reason.TRACKED_MARKET)
    if evidence.vs_sector is not None and evidence.vs_sector.percentile < UNUSUAL_PERCENTILE:
        return _classified(request, evidence, snapshot, Verdict.WITH_SECTOR, Epistemic.INFERRED, Reason.TRACKED_SECTOR)

    return _classified(
        request, evidence, snapshot, Verdict.UNEXPLAINED, Epistemic.UNKNOWN, Reason.NO_EXPLANATION_FOUND
    )


@dataclass(frozen=True)
class _Baseline:
    own: list[float]
    vs_market: list[float]
    vs_sector: list[float]


def _baseline_slice(context: SymbolContext, anchor_index: int, window: int) -> _Baseline:
    """Build baselines from the sessions strictly before the anchor.

    Excluding the current window keeps the move being judged out of the
    distribution it is being judged against.
    """
    start = max(anchor_index - (WINDOW_COUNT + window - 1), 0)
    end = anchor_index + 1

    own = window_returns(context.closes[start:end], window)
    market = window_returns(context.market_closes[start:end], window)
    sector = (
        window_returns(context.sector_closes[start:end], window)
        if context.has_sector
        else []
    )
    return _Baseline(
        own=own,
        vs_market=paired_differences(own, market),
        vs_sector=paired_differences(own, sector) if sector else [],
    )


def _compare(observed: float, history: list[float], label: str) -> Comparison:
    return Comparison(
        label=label,
        observed=observed,
        percentile=percentile_rank(observed, history),
        typical_abs=typical_magnitude(history),
        sample_size=len(history),
    )


def _index_of_anchor(sessions: list[date], anchor_date: date) -> int:
    """Position of the last session at or before the anchor date."""
    for position in range(len(sessions) - 1, -1, -1):
        if sessions[position] <= anchor_date:
            return position
    return -1


def _sessions_away(sessions: list[date], anchor_index: int) -> int:
    if anchor_index < 0:
        return 0
    return len(sessions) - 1 - anchor_index


def _index_return(closes: list[float], anchor_index: int) -> float:
    return closes[-1] / closes[anchor_index] - 1.0


def _in_window(day: date, sessions: list[date], anchor_index: int) -> bool:
    return sessions[anchor_index] < day <= sessions[-1]


UPCOMING_HORIZON_DAYS = 5
"""Calendar days standing in for UPCOMING_EVENT_SESSIONS trading sessions.

The trading calendar only runs to the last observed session, so a forward
horizon has to be measured in calendar days. Five covers three sessions across
a weekend and errs towards showing an event slightly early rather than late.
"""


def _upcoming(events: list[CorporateEvent], last_session: date) -> list[CorporateEvent]:
    return [
        event
        for event in events
        if last_session < event.on_date <= last_session + timedelta(days=UPCOMING_HORIZON_DAYS)
    ]


def _snapshot_id(request: ClassificationRequest) -> str:
    """Identifies exactly what the user was shown.

    Acknowledgements carry this back so the anchor advances to the state the
    user actually saw. A change arriving between render and tap keeps its own
    snapshot and stays unread.
    """
    payload = f"{request.context.symbol}|{request.quote.event_time.isoformat()}|{request.quote.price:.4f}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _cant_say(
    request: ClassificationRequest, evidence: Evidence, snapshot: str, reason: Reason
) -> Classification:
    return Classification(
        symbol=request.context.symbol,
        verdict=Verdict.CANT_SAY,
        epistemic=Epistemic.UNKNOWN,
        reason=reason,
        evidence=evidence,
        snapshot_id=snapshot,
    )


def _classified(
    request: ClassificationRequest,
    evidence: Evidence,
    snapshot: str,
    verdict: Verdict,
    epistemic: Epistemic,
    reason: Reason,
) -> Classification:
    return Classification(
        symbol=request.context.symbol,
        verdict=verdict,
        epistemic=epistemic,
        reason=reason,
        evidence=evidence,
        snapshot_id=snapshot,
    )
