"""The Live Mode HTTP surface, and what it does when Yahoo is unavailable."""

from __future__ import annotations

import pytest

import app.config
from app.config import SOURCE_LIVE
from app.sources import live
from tests.test_live_mode import FakeProvider
from tests.support import (  # noqa: F401
    _shared_database,
    db_factory,
    fresh_client,
    fresh_database,
    session_headers,
)


@pytest.fixture
def configured():
    """Yahoo is the default provider, and yfinance is a real dependency here.

    Live mode is enabled without any monkeypatching; only the client is
    replaced, so nothing in these tests reaches the network.
    """
    fake = FakeProvider()
    live.set_client(fake)
    yield fake
    live.set_client(None)


@pytest.fixture
def unconfigured(monkeypatch):
    """The real way live mode goes unavailable: yfinance is not installed."""
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
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
    assert "yfinance" in response.json()["detail"]


def test_the_reason_live_is_unavailable_is_stated_rather_than_hidden(fresh_client, unconfigured):
    headers = session_headers(fresh_client)

    body = fresh_client.get("/api/mode", headers=headers).json()

    assert body["live_reason"] == "yfinance is not installed"


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


def test_one_real_session_keeps_sample_and_live_watchlists_source_scoped(fresh_client, configured):
    headers = session_headers(fresh_client)
    fresh_client.post("/api/watchlist", json={"symbol": "NWTC"}, headers=headers)

    fresh_client.post("/api/mode", json={"mode": "live"}, headers=headers)
    assert fresh_client.get("/api/watchlist", headers=headers).json()["count"] == 0
    fresh_client.post("/api/watchlist", json={"symbol": "TCS"}, headers=headers)
    assert [item["symbol"] for item in fresh_client.get("/api/watchlist", headers=headers).json()["items"]] == ["TCS"]

    fresh_client.post("/api/mode", json={"mode": "replay"}, headers=headers)
    assert [item["symbol"] for item in fresh_client.get("/api/watchlist", headers=headers).json()["items"]] == ["NWTC"]


def test_switching_source_leaves_each_watchlist_where_it_was(fresh_client, configured):
    """Switching is a change of subject, not a change of contents.

    The frontend keeps the reader on the Watchlist across this; the contract it
    relies on is that each source still answers with its own instruments.
    """
    client, headers = fresh_client, session_headers(fresh_client)

    client.post("/api/watchlist", json={"symbol": "NWTC"}, headers=headers)
    client.post("/api/mode", json={"mode": "live"}, headers=headers)
    client.post("/api/watchlist", json={"symbol": "TCS"}, headers=headers)

    live_items = client.get("/api/watchlist", headers=headers).json()
    assert live_items["source"] == "live"
    assert [item["symbol"] for item in live_items["items"]] == ["TCS"]

    client.post("/api/mode", json={"mode": "replay"}, headers=headers)
    sample_items = client.get("/api/watchlist", headers=headers).json()
    assert sample_items["source"] == "replay"
    assert [item["symbol"] for item in sample_items["items"]] == ["NWTC"]

    # And back again, unchanged.
    client.post("/api/mode", json={"mode": "live"}, headers=headers)
    assert [
        item["symbol"]
        for item in client.get("/api/watchlist", headers=headers).json()["items"]
    ] == ["TCS"]


def test_the_catalogue_offered_follows_the_selected_source(fresh_client, configured):
    client, headers = fresh_client, session_headers(fresh_client)

    client.post("/api/mode", json={"mode": "replay"}, headers=headers)
    sample = client.get("/api/symbols", headers=headers).json()
    client.post("/api/mode", json={"mode": "live"}, headers=headers)
    live_hits = client.get("/api/symbols?q=TCS", headers=headers).json()

    assert sample["source"] == "replay"
    assert "NWTC" in {row["symbol"] for row in sample["symbols"]}
    assert live_hits["source"] == "live"
    assert "NWTC" not in {row["symbol"] for row in live_hits["symbols"]}


def test_an_unrouted_api_path_is_a_404_not_the_app_shell(fresh_client, configured):
    """A mistyped endpoint answered 200 with a page of HTML."""
    response = fresh_client.get("/api/no-such-endpoint")

    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")
