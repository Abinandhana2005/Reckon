"""The HTTP surface, including the failures a stranger will hit first."""

from __future__ import annotations

import pytest
from sqlalchemy import update

from app.db.models import QuoteRow
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


def test_health_reports_a_seeded_database(client):
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["seeded"] is True


def test_the_brief_requires_a_session(client):
    assert client.get("/api/brief").status_code == 401


def test_an_unknown_token_is_refused(client):
    response = client.get("/api/brief", headers={"X-Session-Token": "not-a-token"})

    assert response.status_code == 401


def test_a_guest_session_is_enough_to_persist_a_watchlist(client):
    headers = session_headers(client)

    client.post("/api/watchlist", json={"symbol": "NWTC"}, headers=headers)

    body = client.get("/api/watchlist", headers=headers).json()
    assert [item["symbol"] for item in body["items"]] == ["NWTC"]


def test_every_watched_symbol_appears_in_the_brief(client):
    headers = session_headers(client)
    symbols = watch_all(client, headers)

    brief = client.get("/api/brief", headers=headers).json()

    assert brief["counts"]["checked"] == len(symbols)
    assert brief["counts"]["checked"] == sum(
        brief["counts"][k] for k in ("needs_you", "explained", "quiet", "cant_say")
    )


def test_the_brief_names_every_symbol_it_counted(client):
    headers = session_headers(client)
    symbols = set(watch_all(client, headers))

    brief = client.get("/api/brief", headers=headers).json()

    shown = {c["symbol"] for c in _cards(brief)}
    assert shown == symbols


def test_every_card_carries_an_epistemic_label(client):
    headers = session_headers(client)
    watch_all(client, headers)

    brief = client.get("/api/brief", headers=headers).json()

    for card in _cards(brief):
        assert card["epistemic_label"] in {"Known", "Inferred", "Unknown"}
        assert card["headline"]


def test_an_empty_watchlist_still_produces_a_brief(client):
    headers = session_headers(client)

    brief = client.get("/api/brief", headers=headers).json()

    assert brief["counts"]["checked"] == 0
    assert "accounted for" in brief["accounting_line"]


def test_adding_the_same_symbol_twice_is_idempotent(client):
    headers = session_headers(client)

    client.post("/api/watchlist", json={"symbol": "NWTC"}, headers=headers)
    client.post("/api/watchlist", json={"symbol": "NWTC"}, headers=headers)

    assert client.get("/api/watchlist", headers=headers).json()["count"] == 1


def test_removing_a_symbol_twice_is_not_an_error(client):
    headers = session_headers(client)
    client.post("/api/watchlist", json={"symbol": "NWTC"}, headers=headers)

    first = client.delete("/api/watchlist/NWTC", headers=headers)
    second = client.delete("/api/watchlist/NWTC", headers=headers)

    assert first.json()["removed"] is True
    assert second.status_code == 200
    assert second.json()["removed"] is False


def test_adding_an_unknown_symbol_is_refused(client):
    headers = session_headers(client)

    response = client.post("/api/watchlist", json={"symbol": "NOSUCH"}, headers=headers)

    assert response.status_code == 404


def test_symbol_search_matches_name_as_well_as_ticker(client):
    body = client.get("/api/symbols", params={"q": "meridian"}).json()

    assert [s["symbol"] for s in body["symbols"]] == ["MRDB"]


def test_detail_answers_why_a_symbol_was_not_flagged(client):
    headers = session_headers(client)
    watch_all(client, headers)
    brief = client.get("/api/brief", headers=headers).json()
    quiet = brief["quiet"]["symbols"][0]["symbol"]

    detail = client.get(f"/api/symbols/{quiet}/detail", headers=headers).json()

    assert detail["card"]["verdict"] == "QUIET"
    assert "percentile" in detail["why_not_flagged"]
    assert detail["evidence"]["own"]["sample_size"] > 0
    assert detail["sparkline"]


def test_detail_is_refused_for_a_symbol_not_watched(client):
    headers = session_headers(client)

    response = client.get("/api/symbols/NWTC/detail", headers=headers)

    assert response.status_code == 404


def test_acknowledging_through_the_api_advances_the_anchor(client):
    headers = session_headers(client)
    client.post(
        "/api/watchlist",
        json={"symbol": "NWTC", "anchor_sessions_ago": 5},
        headers=headers,
    )
    brief = client.get("/api/brief", headers=headers).json()
    card = next(c for c in _cards(brief) if c["symbol"] == "NWTC")

    first = client.post(
        "/api/brief/ack",
        json={"symbol": "NWTC", "snapshot_id": card["snapshot_id"]},
        headers=headers,
    ).json()
    second = client.post(
        "/api/brief/ack",
        json={"symbol": "NWTC", "snapshot_id": card["snapshot_id"]},
        headers=headers,
    ).json()

    assert first["advanced"] is True
    assert second["advanced"] is False


def test_acknowledging_a_snapshot_never_shown_is_refused(client):
    headers = session_headers(client)
    client.post("/api/watchlist", json={"symbol": "NWTC"}, headers=headers)
    client.get("/api/brief", headers=headers)

    response = client.post(
        "/api/brief/ack",
        json={"symbol": "NWTC", "snapshot_id": "0000000000000000"},
        headers=headers,
    )

    assert response.status_code == 404


def test_a_stale_quote_turns_a_symbol_grey_instead_of_flagging_it(fresh_client, fresh_database):
    """Falling confidence has to reduce what the app claims, not restyle it."""
    headers = session_headers(fresh_client)
    fresh_client.post("/api/watchlist", json={"symbol": "VNTP"}, headers=headers)

    with fresh_database() as session:
        session.execute(update(QuoteRow).where(QuoteRow.symbol == "VNTP").values(freshness="STALE"))
        session.commit()

    brief = fresh_client.get("/api/brief", headers=headers).json()

    assert [c["symbol"] for c in brief["cant_say"]["symbols"]] == ["VNTP"]
    assert brief["counts"]["needs_you"] == 0
    card = brief["cant_say"]["symbols"][0]
    assert card["verdict"] == "CANT_SAY"
    assert card["change"] is None


def test_a_second_brief_reports_the_gap_since_the_first(client):
    headers = session_headers(client)
    client.post("/api/watchlist", json={"symbol": "NWTC"}, headers=headers)

    first = client.get("/api/brief", headers=headers).json()
    second = client.get("/api/brief", headers=headers).json()

    assert first["last_open_at"] is None
    assert second["last_open_at"] is not None
    assert second["sessions_since_last_open"] == 0


def _cards(brief: dict) -> list[dict]:
    cards = list(brief["needs_you"]) + brief["quiet"]["symbols"] + brief["cant_say"]["symbols"]
    for group in brief["explained"]["groups"]:
        cards += group["symbols"]
    return cards
