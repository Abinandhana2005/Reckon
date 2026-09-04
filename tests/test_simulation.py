"""The demo controls, and the guarantee that they change nothing until used.

Two things are being protected here. That the fixture's designed scenarios are
actually reachable through the API, and that reaching them leaves no trace on
the state a real user would come back to.
"""

from __future__ import annotations

import pytest

from app.db.models import SimulationState, UserSymbolAnchor
from app.services import identity, simulation, watchlist
from tests.support import (  # noqa: F401
    _shared_database,
    client,
    db,
    db_factory,
    fresh_client,
    fresh_database,
    session_headers,
    watch_all,
)

SCENARIO_TARGETS = [
    ("market-wide", "NWTC", "WITH_MARKET"),
    ("banking sector", "KVRB", "WITH_SECTOR"),
    ("single-stock", "VNTP", "UNEXPLAINED"),
    ("market rally", "ZNTH", "UNEXPLAINED"),
]


def brief(client, headers) -> dict:
    return client.get("/api/brief", headers=headers).json()


def cards(payload: dict) -> dict[str, dict]:
    found = list(payload["needs_you"]) + payload["quiet"]["symbols"] + payload["cant_say"]["symbols"]
    for group in payload["explained"]["groups"]:
        found += group["symbols"]
    return {card["symbol"]: card for card in found}


def test_the_fixture_scenarios_are_discoverable(client):
    body = client.get("/api/dev/scenarios").json()

    labels = " ".join(s["label"] for s in body["scenarios"])
    assert len(body["scenarios"]) == 4
    assert "market-wide fall" in labels
    assert set(body["modes"]) == {"stale", "disputed", "unavailable"}


@pytest.mark.parametrize("scenario,symbol,verdict", SCENARIO_TARGETS)
def test_each_designed_scenario_is_reachable_through_the_api(client, scenario, symbol, verdict):
    headers = session_headers(client)
    watch_all(client, headers)

    client.post(
        "/api/dev/simulate",
        json={"scenario": scenario, "anchor_sessions_ago": 1},
        headers=headers,
    )

    assert cards(brief(client, headers))[symbol]["verdict"] == verdict


@pytest.mark.parametrize("gap", [1, 3, 5])
def test_the_market_crash_compresses_at_every_gap(client, gap):
    """The demo's first moment: many symbols, one explanation."""
    headers = session_headers(client)
    symbols = watch_all(client, headers)
    client.post(
        "/api/dev/simulate",
        json={"scenario": "market-wide", "anchor_sessions_ago": gap},
        headers=headers,
    )

    payload = brief(client, headers)

    largest = max(g["size"] for g in payload["explained"]["groups"])
    assert largest >= len(symbols) // 2
    assert payload["counts"]["checked"] == len(symbols)


def test_time_travel_moves_the_moment_the_brief_describes(client):
    headers = session_headers(client)
    watch_all(client, headers)

    client.post("/api/dev/simulate", json={"scenario": "market-wide"}, headers=headers)
    payload = brief(client, headers)

    assert payload["as_of"].startswith("2026-07-31")
    assert payload["simulation"]["active"] is True
    assert payload["simulation"]["scenario"] == "market-wide"


def test_a_replayed_moment_only_sees_history_that_existed_then(client):
    """Baselines must not be built from sessions after the moment being shown."""
    headers = session_headers(client)
    client.post("/api/watchlist", json={"symbol": "NWTC"}, headers=headers)

    live = client.get("/api/symbols/NWTC/detail", headers=headers).json()
    client.post("/api/dev/simulate", json={"scenario": "market-wide"}, headers=headers)
    replayed = client.get("/api/symbols/NWTC/detail", headers=headers).json()

    assert replayed["evidence"]["history_bars"] < live["evidence"]["history_bars"]
    assert len(replayed["sparkline"]) <= len(live["sparkline"])
    assert replayed["sparkline"][-1]["date"] <= "2026-07-31"


def test_degrading_every_quote_makes_the_app_go_quiet(client):
    """Falling confidence has to reduce what is claimed, not restyle it."""
    headers = session_headers(client)
    symbols = watch_all(client, headers)

    client.post("/api/dev/simulate?mode=stale", headers=headers)
    payload = brief(client, headers)

    assert payload["counts"]["cant_say"] == len(symbols)
    assert payload["counts"]["needs_you"] == 0
    assert payload["counts"]["explained"] == 0
    for card in payload["cant_say"]["symbols"]:
        assert card["verdict"] == "CANT_SAY"
        assert card["epistemic_label"] == "Unknown"
        assert card["change"] is None


def test_one_symbol_can_be_degraded_on_its_own(client):
    headers = session_headers(client)
    watch_all(client, headers)

    client.post(
        "/api/dev/simulate",
        json={"scenario": "single-stock", "freshness": {"VNTP": "DISPUTED"}},
        headers=headers,
    )
    payload = brief(client, headers)

    assert payload["counts"]["needs_you"] == 0
    assert "VNTP" in {c["symbol"] for c in payload["cant_say"]["symbols"]}


def test_resetting_restores_the_brief_exactly(client):
    headers = session_headers(client)
    watch_all(client, headers)
    before = brief(client, headers)["counts"]

    client.post("/api/dev/simulate", json={"scenario": "market-wide"}, headers=headers)
    client.delete("/api/dev/simulate", headers=headers)

    assert brief(client, headers)["counts"] == before
    assert client.get("/api/dev/simulate", headers=headers).json()["active"] is False


def test_a_simulation_never_writes_to_a_real_anchor(db_factory):
    """Clearing has to give back exactly what was there, so nothing is stored."""
    with db_factory() as session:
        user, _ = identity.start_guest_session(session)
        watchlist.add(session, user.id, "NWTC", anchor_sessions_ago=3)
        from app.services import briefing

        before = session.get(UserSymbolAnchor, {"user_id": user.id, "symbol": "NWTC"}).anchor_at

        simulation.set_simulation(session, user.id, scenario="market-wide")
        briefing.build_brief(session, user)

        after = session.get(UserSymbolAnchor, {"user_id": user.id, "symbol": "NWTC"}).anchor_at
        assert after == before


def test_a_replayed_visit_does_not_move_the_narrative_clock(db_factory):
    with db_factory() as session:
        user, _ = identity.start_guest_session(session)
        watchlist.add(session, user.id, "NWTC")
        from app.services import briefing

        simulation.set_simulation(session, user.id, scenario="market-wide")
        briefing.build_brief(session, user)

        assert user.last_open_at is None


def test_a_simulation_belongs_to_one_user_only(client):
    watcher = session_headers(client)
    other = session_headers(client)
    watch_all(client, watcher)
    watch_all(client, other)
    baseline = brief(client, other)["counts"]

    client.post("/api/dev/simulate?mode=stale", headers=watcher)

    assert brief(client, other)["counts"] == baseline
    assert brief(client, watcher)["counts"]["cant_say"] > 0


def test_no_simulation_leaves_the_brief_untouched(client):
    headers = session_headers(client)
    watch_all(client, headers)

    payload = brief(client, headers)

    assert payload["simulation"]["active"] is False
    assert payload["simulation"]["as_of"] is None


def test_an_unknown_scenario_is_refused(client):
    headers = session_headers(client)

    response = client.post("/api/dev/simulate", json={"scenario": "nonsense"}, headers=headers)

    assert response.status_code == 404


def test_a_date_that_is_not_a_trading_session_is_refused(client):
    headers = session_headers(client)

    response = client.post(
        "/api/dev/simulate", json={"on_date": "2026-08-15"}, headers=headers
    )

    assert response.status_code == 404


def test_a_freshness_that_would_not_suppress_inference_is_refused(client):
    """Degrading to LIVE is not a degradation; offering it would be misleading."""
    headers = session_headers(client)

    response = client.post(
        "/api/dev/simulate", json={"freshness": {"VNTP": "LIVE"}}, headers=headers
    )

    assert response.status_code == 400


def test_an_unknown_mode_is_refused(client):
    headers = session_headers(client)

    assert client.post("/api/dev/simulate?mode=sideways", headers=headers).status_code == 400


def test_a_scenario_and_a_date_together_are_refused(client):
    headers = session_headers(client)

    response = client.post(
        "/api/dev/simulate",
        json={"scenario": "market-wide", "on_date": "2026-07-31"},
        headers=headers,
    )

    assert response.status_code == 400


def test_the_demo_controls_require_a_session(client):
    assert client.get("/api/dev/simulate").status_code == 401
    assert client.post("/api/dev/simulate").status_code == 401


def test_clearing_when_nothing_is_set_is_not_an_error(client):
    headers = session_headers(client)

    response = client.delete("/api/dev/simulate", headers=headers)

    assert response.status_code == 200
    assert response.json()["cleared"] is False


def test_setting_a_simulation_twice_replaces_it(db_factory):
    with db_factory() as session:
        user, _ = identity.start_guest_session(session)

        simulation.set_simulation(session, user.id, scenario="market-wide")
        simulation.set_simulation(session, user.id, scenario="market rally")

        assert session.query(SimulationState).filter_by(user_id=user.id).count() == 1
        assert simulation.get(session, user.id).scenario == "market rally"
