"""Each test targets a failure mode that would produce a wrong or misleading
answer for a user, not a line of code.

Every assertion is on a verdict. None asserts that nothing was emitted: a test
that checks for the absence of an alert passes just as happily when the
pipeline is broken as when it is correct.
"""

from __future__ import annotations

import pytest

from app.domain.classifier import classify
from app.domain.verdicts import Epistemic, Freshness, Reason, Verdict
from tests.conftest import build_request, scenario_day


def test_market_wide_fall_is_explained_not_flagged(market):
    """A large fall that the whole market shared is not about the company."""
    day = scenario_day(market, "market-wide")
    result = classify(build_request(market, "NWTC", sessions_away=1, up_to=day))

    assert result.verdict is Verdict.WITH_MARKET
    assert result.reason is Reason.TRACKED_MARKET
    assert result.evidence.adjusted_return < -0.02
    assert not result.needs_attention


def test_market_wide_fall_produces_no_statistical_false_positives(market):
    """The output must stay small on exactly the day a user is most anxious.

    A threshold-and-rank design lights up on a crash. Classification compresses
    instead, because the moves share an explanation. Confirmed events are a
    separate matter: a fact is not a false positive and is meant to surface on
    a crash day like any other, so only UNEXPLAINED is counted here.
    """
    day = scenario_day(market, "market-wide")
    verdicts = {
        symbol: classify(build_request(market, symbol, 1, up_to=day)).verdict
        for symbol in market["symbols"]
    }
    unexplained = [s for s, v in verdicts.items() if v is Verdict.UNEXPLAINED]
    with_market = [s for s, v in verdicts.items() if v is Verdict.WITH_MARKET]

    assert unexplained == [], f"crash day produced false positives: {unexplained}"
    assert len(with_market) >= len(market["symbols"]) // 2, (
        "a market-wide fall should collapse most of the watchlist into one line"
    )


def test_sector_fall_on_a_flat_market_is_attributed_to_the_sector(market):
    day = scenario_day(market, "sector fall")
    result = classify(build_request(market, "KVRB", sessions_away=1, up_to=day))

    assert result.verdict is Verdict.WITH_SECTOR
    assert result.epistemic is Epistemic.INFERRED
    assert result.evidence.vs_sector.percentile < 90.0
    assert result.evidence.vs_market.percentile >= 90.0


def test_stock_specific_move_with_no_event_is_unexplained(market):
    """The system must say it does not know rather than reach for a cause."""
    day = scenario_day(market, "single-stock")
    result = classify(build_request(market, "VNTP", sessions_away=1, up_to=day))

    assert result.verdict is Verdict.UNEXPLAINED
    assert result.epistemic is Epistemic.UNKNOWN
    assert result.reason is Reason.NO_EXPLANATION_FOUND
    assert result.needs_attention


def test_flat_stock_during_a_market_rally_is_not_quiet(market):
    """QUIET requires being normal both absolutely and against the market.

    A stock that sat still through a rally has a tiny absolute move, and a
    naive small-move-means-quiet rule would hide it.
    """
    day = scenario_day(market, "rally")
    result = classify(build_request(market, "ZNTH", sessions_away=1, up_to=day))

    assert result.verdict is not Verdict.QUIET
    assert abs(result.evidence.adjusted_return) < 0.005
    assert result.evidence.vs_market.percentile >= 90.0


def test_split_reads_as_no_change_not_a_collapse(market):
    """Without adjustment a 1:2 split shows roughly -50%, which is simply wrong."""
    day = scenario_day(market, "sector fall")
    result = classify(build_request(market, "MRDB", sessions_away=1, up_to=day))

    assert result.verdict is Verdict.EVENT
    assert result.evidence.adjustment_factor == pytest.approx(0.5)
    assert result.evidence.adjusted_return > -0.15


def test_announced_results_are_reported_as_a_known_fact(market):
    day = scenario_day(market, "market-wide")
    result = classify(build_request(market, "KVRB", sessions_away=1, up_to=day))

    assert result.verdict is Verdict.EVENT
    assert result.epistemic is Epistemic.KNOWN
    assert result.reason is Reason.EVENT_OCCURRED


def test_a_scheduled_event_outranks_a_quiet_price(market):
    """Deserving attention now is often about what is coming, not what moved."""
    result = classify(build_request(market, "SETL", sessions_away=1))

    assert result.verdict is Verdict.EVENT
    assert result.reason is Reason.EVENT_UPCOMING
    assert result.needs_attention


def test_insufficient_history_refuses_to_judge(market):
    """A recent listing has no baseline, so no comparison is honest."""
    result = classify(build_request(market, "ARDB", sessions_away=1))

    assert result.verdict is Verdict.CANT_SAY
    assert result.reason is Reason.INSUFFICIENT_HISTORY
    assert result.evidence.own is None


def test_stale_quote_suppresses_inference_entirely(market):
    """Falling confidence must make the system quieter, not more confident."""
    day = scenario_day(market, "single-stock")
    result = classify(
        build_request(market, "VNTP", sessions_away=1, up_to=day, freshness=Freshness.STALE)
    )

    assert result.verdict is Verdict.CANT_SAY
    assert result.reason is Reason.UNTRUSTED_QUOTE
    assert result.evidence.adjusted_return is None


def test_disputed_sources_produce_no_verdict_rather_than_an_average(market):
    """Averaging two disagreeing prices invents a price that never traded."""
    result = classify(build_request(market, "NWTC", sessions_away=1, freshness=Freshness.DISPUTED))
    assert result.verdict is Verdict.CANT_SAY


def test_missing_sector_is_disclosed_not_silently_ignored(market):
    """ZNTH has no sector index, so a sector check was never performed."""
    result = classify(build_request(market, "ZNTH", sessions_away=1))

    assert result.evidence.sector_available is False
    assert result.evidence.vs_sector is None


def test_longer_absence_widens_the_baseline_it_is_judged_against(market):
    """A five-session move must be compared with five-session history."""
    one = classify(build_request(market, "NWTC", sessions_away=1))
    five = classify(build_request(market, "NWTC", sessions_away=5))

    assert one.evidence.sessions_away == 1
    assert five.evidence.sessions_away == 5
    assert five.evidence.own.typical_abs > one.evidence.own.typical_abs


def test_every_symbol_receives_exactly_one_verdict(market):
    """Completeness is the product claim: nothing is silently dropped."""
    results = [classify(build_request(market, s, 3)) for s in market["symbols"]]

    assert len(results) == len(market["symbols"])
    assert all(isinstance(r.verdict, Verdict) for r in results)


def test_snapshot_id_tracks_the_quote_the_user_was_shown(market):
    """Acknowledgement is keyed to this, so a newer change stays unread."""
    shown = classify(build_request(market, "NWTC", sessions_away=1))
    moved = classify(build_request(market, "NWTC", sessions_away=1, price_override=9999.0))

    assert shown.snapshot_id != moved.snapshot_id
