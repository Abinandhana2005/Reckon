"""The Live Mode HTTP surface, and what it does with no token configured."""

from __future__ import annotations

import pytest

import app.config
from app.config import SOURCE_LIVE
from app.sources import live
from tests.test_live_mode import FakeUpstox
from tests.support import (  # noqa: F401
    _shared_database,
    db_factory,
    fresh_client,
    fresh_database,
    session_headers,
)


@pytest.fixture
def configured(monkeypatch):
    """A token is present, and the adapter is a stand-in."""
    monkeypatch.setattr(app.config, "UPSTOX_ACCESS_TOKEN", "test-token")
    fake = FakeUpstox()
    live.set_client(fake)
    yield fake
    live.set_client(None)


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(app.config, "UPSTOX_ACCESS_TOKEN", None)
    live.set_client(None)
    yield
    live.set_client(None)


def test_a_session_starts_on_fixture_data(fresh_client, unconfigured):
    headers = session_headers(fresh_client)

    body = fresh_client.get("/api/mode", headers=headers).json()

    assert body["mode"] == "replay"
    assert body["live_available"] is False


def test_live_mode_is_refused_when_no_token_is_configured(fresh_client, unconfigured):
    headers = session_headers(fresh_client)

    response = fresh_client.post("/api/mode", json={"mode": "live"}, headers=headers)

    assert response.status_code == 503
    assert "UPSTOX_ACCESS_TOKEN" in response.json()["detail"]


def test_the_reason_live_is_unavailable_is_stated_rather_than_hidden(fresh_client, unconfigured):
    headers = session_headers(fresh_client)

    body = fresh_client.get("/api/mode", headers=headers).json()

    assert body["live_reason"] == "UPSTOX_ACCESS_TOKEN is not set"


def test_health_reports_whether_live_is_available(fresh_client, unconfigured):
    assert fresh_client.get("/health").json()["live_available"] is False


def test_switching_to_live_is_allowed_once_a_token_is_configured(fresh_client, configured):
    headers = session_headers(fresh_client)

    body = fresh_client.post("/api/mode", json={"mode": "live"}, headers=headers).json()

    assert body["mode"] == SOURCE_LIVE
    assert body["live_available"] is True


def test_search_returns_real_instruments_in_live_mode(fresh_client, configured):
    headers = session_headers(fresh_client)
    fresh_client.post("/api/mode", json={"mode": "live"}, headers=headers)

    body = fresh_client.get("/api/symbols", params={"q": "TCS"}, headers=headers).json()

    assert body["source"] == SOURCE_LIVE
    assert [row["symbol"] for row in body["symbols"]] == ["TCS"]
    assert body["symbols"][0]["instrument_key"] == "NSE_EQ|INE467B01029"


def test_search_returns_fixture_issuers_in_replay_mode(fresh_client, configured):
    headers = session_headers(fresh_client)

    body = fresh_client.get("/api/symbols", params={"q": "meridian"}, headers=headers).json()

    assert body["source"] == "replay"
    assert [row["symbol"] for row in body["symbols"]] == ["MRDB"]


def test_a_real_instrument_can_be_added_and_briefed(fresh_client, configured):
    headers = session_headers(fresh_client)
    fresh_client.post("/api/mode", json={"mode": "live"}, headers=headers)

    added = fresh_client.post("/api/watchlist", json={"symbol": "TCS"}, headers=headers)
    brief = fresh_client.get("/api/brief", headers=headers).json()

    assert added.status_code == 201
    assert brief["counts"]["checked"] == 1
    assert brief["market"]["index"] == live.MARKET_CODE


def test_an_unlisted_symbol_is_reported_not_invented(fresh_client, configured):
    headers = session_headers(fresh_client)
    fresh_client.post("/api/mode", json={"mode": "live"}, headers=headers)

    response = fresh_client.post("/api/watchlist", json={"symbol": "NOSUCH"}, headers=headers)

    assert response.status_code == 404


def test_refresh_is_rejected_for_a_session_that_is_not_live(fresh_client, configured):
    headers = session_headers(fresh_client)

    assert fresh_client.post("/api/live/refresh", headers=headers).status_code == 400


def test_refresh_reports_each_symbols_outcome(fresh_client, configured):
    headers = session_headers(fresh_client)
    fresh_client.post("/api/mode", json={"mode": "live"}, headers=headers)
    fresh_client.post("/api/watchlist", json={"symbol": "TCS"}, headers=headers)

    body = fresh_client.post("/api/live/refresh", headers=headers).json()

    assert "TCS" in body["refreshed"]


def test_an_invalid_mode_is_refused(fresh_client, configured):
    headers = session_headers(fresh_client)

    assert fresh_client.post("/api/mode", json={"mode": "sideways"}, headers=headers).status_code == 422


def test_the_demo_session_stays_on_fixture_data_while_another_goes_live(fresh_client, configured):
    demo = session_headers(fresh_client)
    other = session_headers(fresh_client)
    for symbol in ("NWTC", "KVRB"):
        fresh_client.post("/api/watchlist", json={"symbol": symbol}, headers=demo)
    before = fresh_client.get("/api/brief", headers=demo).json()["counts"]

    fresh_client.post("/api/mode", json={"mode": "live"}, headers=other)
    fresh_client.post("/api/watchlist", json={"symbol": "TCS"}, headers=other)

    assert fresh_client.get("/api/brief", headers=demo).json()["counts"] == before
    assert fresh_client.get("/api/watchlist", headers=demo).json()["count"] == 2
