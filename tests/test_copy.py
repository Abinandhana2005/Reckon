"""The wording rules, enforced against every string the app can produce.

Two failures matter in a financial product and neither is caught by a type
checker: claiming a cause from a correlation, and drifting into advice. Both are
one careless template away, so every reachable sentence is generated here and
checked, rather than the handful a happy-path test would exercise.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.api import copy
from app.domain.verdicts import (
    Classification,
    Comparison,
    CorporateEvent,
    Epistemic,
    EventKind,
    Evidence,
    Reason,
    Verdict,
)
from datetime import date

CASES = [
    (Verdict.CANT_SAY, Epistemic.UNKNOWN, Reason.UNTRUSTED_QUOTE),
    (Verdict.CANT_SAY, Epistemic.UNKNOWN, Reason.INSUFFICIENT_HISTORY),
    (Verdict.CANT_SAY, Epistemic.UNKNOWN, Reason.UNADJUSTABLE_ACTION),
    (Verdict.EVENT, Epistemic.KNOWN, Reason.EVENT_OCCURRED),
    (Verdict.EVENT, Epistemic.KNOWN, Reason.EVENT_UPCOMING),
    (Verdict.QUIET, Epistemic.INFERRED, Reason.WITHIN_OWN_RANGE),
    (Verdict.WITH_MARKET, Epistemic.INFERRED, Reason.TRACKED_MARKET),
    (Verdict.WITH_SECTOR, Epistemic.INFERRED, Reason.TRACKED_SECTOR),
    (Verdict.UNEXPLAINED, Epistemic.UNKNOWN, Reason.NO_EXPLANATION_FOUND),
]

EVENT = CorporateEvent(
    symbol="TEST",
    on_date=date(2026, 9, 1),
    kind=EventKind.RESULTS,
    detail="Quarterly results announced",
)


def build(verdict: Verdict, epistemic: Epistemic, reason: Reason) -> Classification:
    comparison = Comparison(
        label="x", observed=0.04, percentile=92.0, typical_abs=0.013, sample_size=60
    )
    evidence = Evidence(
        sessions_away=3,
        adjusted_return=-0.041,
        market_return=-0.036,
        sector_return=-0.04,
        adjustment_factor=0.5,
        own=comparison,
        vs_market=comparison,
        vs_sector=comparison,
        sector_index="BANKIDX",
        events_in_window=[EVENT] if reason is Reason.EVENT_OCCURRED else [],
        events_upcoming=[EVENT] if reason is Reason.EVENT_UPCOMING else [],
    )
    return Classification(
        symbol="TEST",
        verdict=verdict,
        epistemic=epistemic,
        reason=reason,
        evidence=evidence,
        snapshot_id="snap",
    )


def every_string() -> list[str]:
    produced: list[str] = []
    for verdict, epistemic, reason in CASES:
        item = build(verdict, epistemic, reason)
        produced += [
            copy.headline(item, sector_name="Banking"),
            copy.detail(item, sector_name="Banking"),
            copy.why_not_flagged(item, sector_name="Banking"),
            copy.start_here_line(item),
            copy.uncertainty_label(reason),
            copy.uncertainty_detail(reason),
        ]
        produced += [
            step["label"] for step in copy.decision_trace(item, sector_name="Banking")
        ]
        produced += [
            step["note"] for step in copy.decision_trace(item, sector_name="Banking")
        ]
    for size in (1, 2, 7):
        produced += [
            copy.sector_group_line("Banking", -0.04, size),
            copy.market_group_line(0.025, size),
            copy.quiet_line(size),
            copy.cant_say_line(size),
        ]
    for needs in (0, 3):
        produced.append(copy.accounting_line({"checked": 16, "needs_you": needs}))
    for counts in (
        {"checked": 16, "needs_you": 1, "explained": 12, "quiet": 2, "cant_say": 1},
        {"checked": 1, "needs_you": 0, "explained": 0, "quiet": 1, "cant_say": 0},
        {"checked": 3, "needs_you": 3, "explained": 0, "quiet": 0, "cant_say": 0},
        {"checked": 0, "needs_you": 0, "explained": 0, "quiet": 0, "cant_say": 0},
    ):
        produced.append(copy.silence_report(counts))
    produced += list(copy.EPISTEMIC_LABEL.values())
    return produced


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


@pytest.mark.parametrize("text", every_string())
def test_no_causal_or_advisory_language(text: str) -> None:
    offending = words(text) & copy.BANNED_WORDS
    assert not offending, f"banned wording {sorted(offending)} in: {text!r}"


@pytest.mark.parametrize(
    "verdict,reason",
    [(Verdict.WITH_MARKET, Reason.TRACKED_MARKET), (Verdict.WITH_SECTOR, Reason.TRACKED_SECTOR)],
)
def test_comparison_cards_say_they_are_not_a_cause(verdict, reason) -> None:
    item = build(verdict, Epistemic.INFERRED, reason)

    assert copy.COMPARISON_DISCLAIMER in copy.detail(item, sector_name="Banking")
    assert copy.COMPARISON_DISCLAIMER in copy.why_not_flagged(item, sector_name="Banking")


def test_unexplained_admits_it_does_not_know() -> None:
    item = build(Verdict.UNEXPLAINED, Epistemic.UNKNOWN, Reason.NO_EXPLANATION_FOUND)

    assert "We don't know why." in copy.detail(item)
    assert "not explained by" in copy.headline(item)


def test_every_epistemic_state_has_a_label() -> None:
    assert set(copy.EPISTEMIC_LABEL) == set(Epistemic)


def test_quiet_explains_itself_with_its_own_percentiles() -> None:
    item = build(Verdict.QUIET, Epistemic.INFERRED, Reason.WITHIN_OWN_RANGE)

    answer = copy.why_not_flagged(item)

    assert "percentile" in answer
    assert "60" in answer


def test_fixture_event_wording_is_also_clean() -> None:
    """Event headlines come from data, so the data has to obey the rules too."""
    fixture = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "market.json").read_text(
            encoding="utf-8"
        )
    )
    for event in fixture["events"]:
        assert not words(event["detail"]) & copy.BANNED_WORDS, event


def test_the_silence_report_accounts_for_every_symbol_it_was_given() -> None:
    line = copy.silence_report(
        {"checked": 16, "needs_you": 1, "explained": 12, "quiet": 2, "cant_say": 1}
    )

    assert line == (
        "16 stocks checked. 12 moved with the market or its sector, 2 were quiet, "
        "1 could not be evaluated and 1 needs your attention."
    )


@pytest.mark.parametrize("verdict,epistemic,reason", CASES)
def test_every_verdict_has_a_complete_decision_trace(verdict, epistemic, reason) -> None:
    """Six steps, always, whatever the verdict -- a skipped check is still shown."""
    trace = copy.decision_trace(build(verdict, epistemic, reason), sector_name="Banking")

    assert [step["key"] for step in trace] == [
        "data", "event", "own", "market", "sector", "verdict"
    ]
    assert all(step["note"] for step in trace)
    assert trace[-1]["status"] == copy.DECIDED


def test_a_cant_say_trace_stops_at_data_quality() -> None:
    trace = copy.decision_trace(
        build(Verdict.CANT_SAY, Epistemic.UNKNOWN, Reason.UNTRUSTED_QUOTE)
    )
    by_key = {step["key"]: step for step in trace}

    assert by_key["data"]["status"] == copy.DECIDED
    assert by_key["event"]["status"] == copy.SKIPPED
    assert by_key["market"]["status"] == copy.SKIPPED


def test_a_with_sector_trace_shows_the_market_check_passing_first() -> None:
    trace = copy.decision_trace(
        build(Verdict.WITH_SECTOR, Epistemic.INFERRED, Reason.TRACKED_SECTOR),
        sector_name="Banking",
    )
    by_key = {step["key"]: step for step in trace}

    assert by_key["market"]["status"] == copy.PASSED
    assert by_key["sector"]["status"] == copy.DECIDED
