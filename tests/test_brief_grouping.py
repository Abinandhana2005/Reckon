"""Grouping is accountable: everything classified comes out again, exactly once.

These assert what the brief contains, never that something was absent. A test
reading "nothing was flagged" would also pass against a pipeline that produced
nothing at all.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.domain.brief import assemble
from app.domain.verdicts import (
    Classification,
    Comparison,
    Epistemic,
    Evidence,
    Reason,
    Verdict,
)

VERDICT_SETUP = {
    Verdict.EVENT: (Epistemic.KNOWN, Reason.EVENT_OCCURRED),
    Verdict.UNEXPLAINED: (Epistemic.UNKNOWN, Reason.NO_EXPLANATION_FOUND),
    Verdict.WITH_MARKET: (Epistemic.INFERRED, Reason.TRACKED_MARKET),
    Verdict.WITH_SECTOR: (Epistemic.INFERRED, Reason.TRACKED_SECTOR),
    Verdict.QUIET: (Epistemic.INFERRED, Reason.WITHIN_OWN_RANGE),
    Verdict.CANT_SAY: (Epistemic.UNKNOWN, Reason.UNTRUSTED_QUOTE),
}


def make(
    symbol: str,
    verdict: Verdict,
    *,
    sector: str | None = None,
    change: float = 0.01,
) -> Classification:
    epistemic, reason = VERDICT_SETUP[verdict]
    evidence = Evidence(
        sessions_away=1,
        adjusted_return=change,
        market_return=0.0,
        sector_return=-0.04 if sector else None,
        sector_index=sector,
        sector_available=sector is not None,
    )
    return Classification(
        symbol=symbol,
        verdict=verdict,
        epistemic=epistemic,
        reason=reason,
        evidence=evidence,
        snapshot_id=f"snap-{symbol}",
    )


def test_every_classification_lands_in_exactly_one_bucket():
    items = [
        make("AAA", Verdict.EVENT),
        make("BBB", Verdict.UNEXPLAINED),
        make("CCC", Verdict.WITH_MARKET),
        make("DDD", Verdict.WITH_SECTOR, sector="BANKIDX"),
        make("EEE", Verdict.QUIET),
        make("FFF", Verdict.CANT_SAY),
    ]

    brief = assemble(items)

    assert brief.accounted_for == len(items)
    assert brief.counts["checked"] == 6
    placed = (
        [c.symbol for c in brief.needs_you]
        + [c.symbol for c in brief.explained]
        + [c.symbol for c in brief.quiet]
        + [c.symbol for c in brief.cant_say]
    )
    assert sorted(placed) == ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


def test_needs_you_is_events_and_unexplained_only():
    brief = assemble(
        [
            make("AAA", Verdict.EVENT),
            make("BBB", Verdict.UNEXPLAINED),
            make("CCC", Verdict.WITH_MARKET),
            make("DDD", Verdict.QUIET),
        ]
    )
    assert [c.symbol for c in brief.needs_you] == ["AAA", "BBB"]


def test_cant_say_is_uncertainty_and_never_an_alert():
    brief = assemble([make("AAA", Verdict.CANT_SAY, change=0.9)])

    assert [c.symbol for c in brief.cant_say] == ["AAA"]
    assert brief.needs_you == []
    assert brief.counts["needs_you"] == 0


def test_symbols_moving_with_one_sector_compress_into_one_group():
    items = [make(s, Verdict.WITH_SECTOR, sector="BANKIDX") for s in ("AAA", "BBB", "CCC")]
    items.append(make("DDD", Verdict.WITH_SECTOR, sector="ITIDX"))

    brief = assemble(items, sector_returns={"BANKIDX": -0.04, "ITIDX": -0.01})

    assert len(brief.sector_groups) == 2
    banking = brief.sector_groups[0]
    assert banking.sector_index == "BANKIDX"
    assert banking.size == 3
    assert banking.index_return == pytest.approx(-0.04)


def test_groups_are_ordered_by_size_so_the_largest_explanation_leads():
    items = [make("AAA", Verdict.WITH_SECTOR, sector="ITIDX")]
    items += [make(s, Verdict.WITH_SECTOR, sector="BANKIDX") for s in ("BBB", "CCC")]

    brief = assemble(items)

    assert [g.sector_index for g in brief.sector_groups] == ["BANKIDX", "ITIDX"]


def test_needs_you_leads_with_the_largest_move():
    brief = assemble(
        [
            make("SMALL", Verdict.UNEXPLAINED, change=0.01),
            make("LARGE", Verdict.UNEXPLAINED, change=-0.09),
        ]
    )
    assert [c.symbol for c in brief.needs_you] == ["LARGE", "SMALL"]


def test_an_empty_watchlist_is_accounted_for_rather_than_undefined():
    brief = assemble([])

    assert brief.accounted_for == 0
    assert brief.counts == {
        "checked": 0,
        "needs_you": 0,
        "explained": 0,
        "quiet": 0,
        "cant_say": 0,
    }
