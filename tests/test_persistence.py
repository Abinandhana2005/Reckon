"""Seeding and the quote write path."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import DailyBar, IndexMeta, QuoteRow, Symbol
from app.db.seed import seed
from app.sources import replay
from tests.support import (  # noqa: F401
    _shared_database,
    db,
    db_factory,
    fresh_database,
)


def test_seeding_twice_does_not_duplicate_market_data(fresh_database):
    with fresh_database() as session:
        before = session.scalar(select(func.count()).select_from(DailyBar))

        result = seed(session)

        assert result == {"skipped": 1}
        assert session.scalar(select(func.count()).select_from(DailyBar)) == before


def test_the_market_index_is_recorded_not_guessed(db):
    code = replay.market_index(db)
    row = db.get(IndexMeta, code)

    assert row.is_market is True
    assert db.scalar(
        select(func.count()).select_from(IndexMeta).where(IndexMeta.is_market)
    ) == 1


def test_every_symbol_has_bars_and_a_quote(db):
    symbols = list(db.scalars(select(Symbol.symbol)))

    for symbol in symbols:
        assert db.get(QuoteRow, symbol) is not None
        assert db.scalar(
            select(func.count()).select_from(DailyBar).where(DailyBar.symbol == symbol)
        ) > 0


def test_now_is_taken_from_the_data_not_the_wall_clock(db):
    days = replay.trading_days(db)

    assert replay.replay_now(db).date() == days[-1]


def test_an_unknown_symbol_is_reported_rather_than_returning_nothing(db):
    with pytest.raises(replay.UnknownSymbol):
        replay.load_symbol(db, "NOSUCH")
