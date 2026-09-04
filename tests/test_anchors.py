"""Anchors, acknowledgements and the two clocks.

The guarantees here are the ones that make the app safe on more than one
device: an anchor only ever moves forward, and only to the snapshot the user
actually saw.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models import User, UserSymbolAnchor, VerdictRow
from app.services import briefing, identity, watchlist
from app.services.briefing import UnknownSnapshot
from app.sources import replay
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

SYMBOL = "NWTC"


@pytest.fixture
def user_db(db_factory):
    with db_factory() as session:
        user, _ = identity.start_guest_session(session)
        watchlist.add(session, user.id, SYMBOL, anchor_sessions_ago=5)
        yield session, user


def anchor_for(session, user) -> UserSymbolAnchor:
    return session.get(UserSymbolAnchor, {"user_id": user.id, "symbol": SYMBOL})


def test_reading_a_brief_moves_the_narrative_clock_but_not_the_anchor(user_db):
    session, user = user_db
    before = anchor_for(session, user).anchor_at

    briefing.build_brief(session, user)

    assert user.last_open_at == replay.replay_now(session)
    assert anchor_for(session, user).anchor_at == before


def test_acknowledging_moves_the_anchor_to_the_snapshot_shown(user_db):
    session, user = user_db
    brief = briefing.build_brief(session, user)
    card = next(c for c in _all_cards(brief) if c["symbol"] == SYMBOL)

    result = briefing.acknowledge(session, user, SYMBOL, card["snapshot_id"])

    assert result["advanced"] is True
    quote = replay.load_quote(session, SYMBOL)
    anchor = anchor_for(session, user)
    assert anchor.anchor_at == quote.event_time
    assert anchor.anchor_price == pytest.approx(quote.price)
    assert anchor.anchor_snapshot_id == card["snapshot_id"]


def test_acknowledging_twice_is_a_no_op_rather_than_an_error(user_db):
    session, user = user_db
    brief = briefing.build_brief(session, user)
    snapshot = next(c for c in _all_cards(brief) if c["symbol"] == SYMBOL)["snapshot_id"]

    first = briefing.acknowledge(session, user, SYMBOL, snapshot)
    second = briefing.acknowledge(session, user, SYMBOL, snapshot)

    assert first["advanced"] is True
    assert second["advanced"] is False
    assert second["anchor_at"] == first["anchor_at"]


def test_an_anchor_never_moves_backwards(user_db):
    """A stale acknowledgement must not rewind an anchor already ahead of it.

    This is the multi-device case: a second device acknowledges an older
    snapshot after the first has already moved on.
    """
    session, user = user_db
    brief = briefing.build_brief(session, user)
    snapshot = next(c for c in _all_cards(brief) if c["symbol"] == SYMBOL)["snapshot_id"]
    briefing.acknowledge(session, user, SYMBOL, snapshot)
    ahead = anchor_for(session, user).anchor_at

    row = session.scalar(
        select(VerdictRow).where(
            VerdictRow.user_id == user.id,
            VerdictRow.symbol == SYMBOL,
            VerdictRow.snapshot_id == snapshot,
        )
    )
    row.snapshot_at = ahead - timedelta(days=3)
    row.snapshot_price = 1.0
    session.commit()

    result = briefing.acknowledge(session, user, SYMBOL, snapshot)

    assert result["advanced"] is False
    assert anchor_for(session, user).anchor_at == ahead
    assert anchor_for(session, user).anchor_price != pytest.approx(1.0)


def test_acknowledging_an_unseen_snapshot_is_refused(user_db):
    session, user = user_db
    briefing.build_brief(session, user)

    with pytest.raises(UnknownSnapshot):
        briefing.acknowledge(session, user, SYMBOL, "never-shown")


def test_one_users_acknowledgement_does_not_touch_another(db_factory):
    with db_factory() as session:
        first, _ = identity.start_guest_session(session)
        second, _ = identity.start_guest_session(session)
        for user in (first, second):
            watchlist.add(session, user.id, SYMBOL, anchor_sessions_ago=5)

        brief = briefing.build_brief(session, first)
        snapshot = next(c for c in _all_cards(brief) if c["symbol"] == SYMBOL)["snapshot_id"]
        before = session.get(
            UserSymbolAnchor, {"user_id": second.id, "symbol": SYMBOL}
        ).anchor_at

        briefing.acknowledge(session, first, SYMBOL, snapshot)

        after = session.get(
            UserSymbolAnchor, {"user_id": second.id, "symbol": SYMBOL}
        ).anchor_at
        assert after == before


def test_re_adding_a_symbol_keeps_the_anchor_it_already_had(user_db):
    """Otherwise removing and re-adding would silently mark everything as seen."""
    session, user = user_db
    original = anchor_for(session, user).anchor_at

    watchlist.remove(session, user.id, SYMBOL)
    watchlist.add(session, user.id, SYMBOL, anchor_sessions_ago=0)

    assert anchor_for(session, user).anchor_at == original


def test_adding_the_same_symbol_twice_stores_one_row(user_db):
    session, user = user_db

    watchlist.add(session, user.id, SYMBOL, note="second")

    assert watchlist.watched_symbols(session, user.id).count(SYMBOL) == 1


def _all_cards(brief: dict) -> list[dict]:
    cards = list(brief["needs_you"]) + brief["quiet"]["symbols"] + brief["cant_say"]["symbols"]
    for group in brief["explained"]["groups"]:
        cards += group["symbols"]
    return cards
