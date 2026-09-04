"""Turn domain objects into the JSON the frontend will read.

Evidence is serialised in full rather than summarised. It is what answers "why
did you not flag this?", and a shape that only carried the flagged cases would
quietly reintroduce the filter the product exists to avoid.
"""

from __future__ import annotations

from app.api import copy
from app.domain.verdicts import Classification, Comparison, CorporateEvent, Evidence


def comparison_dict(comparison: Comparison | None) -> dict | None:
    if comparison is None:
        return None
    return {
        "label": comparison.label,
        "observed": comparison.observed,
        "percentile": comparison.percentile,
        "typical_abs": comparison.typical_abs,
        "sample_size": comparison.sample_size,
    }


def event_dict(event: CorporateEvent) -> dict:
    return {
        "symbol": event.symbol,
        "on_date": event.on_date.isoformat(),
        "kind": event.kind.value,
        "value": event.value,
        "detail": event.detail,
    }


def evidence_dict(evidence: Evidence) -> dict:
    return {
        "sessions_away": evidence.sessions_away,
        "adjusted_return": evidence.adjusted_return,
        "market_return": evidence.market_return,
        "sector_return": evidence.sector_return,
        "adjustment_factor": evidence.adjustment_factor,
        "own": comparison_dict(evidence.own),
        "vs_market": comparison_dict(evidence.vs_market),
        "vs_sector": comparison_dict(evidence.vs_sector),
        "sector_index": evidence.sector_index,
        "sector_available": evidence.sector_available,
        "events_in_window": [event_dict(e) for e in evidence.events_in_window],
        "events_upcoming": [event_dict(e) for e in evidence.events_upcoming],
        "volume_ratio": evidence.volume_ratio,
        "quote_age_seconds": evidence.quote_age_seconds,
        "quote_source": evidence.quote_source,
        "history_bars": evidence.history_bars,
    }


def card(
    item: Classification,
    *,
    names: dict[str, str],
    index_names: dict[str, str],
) -> dict:
    sector_name = index_names.get(item.evidence.sector_index or "")
    return {
        "symbol": item.symbol,
        "name": names.get(item.symbol, item.symbol),
        "verdict": item.verdict.value,
        "epistemic": item.epistemic.value,
        "epistemic_label": copy.EPISTEMIC_LABEL[item.epistemic],
        "reason": item.reason.value,
        "headline": copy.headline(item, sector_name=sector_name),
        "detail": copy.detail(item, sector_name=sector_name),
        "change": item.evidence.adjusted_return,
        "sessions_away": item.evidence.sessions_away,
        "snapshot_id": item.snapshot_id,
        "needs_attention": item.needs_attention,
    }
