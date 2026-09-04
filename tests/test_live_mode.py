"""Live Mode against a stand-in provider.

No test here touches the network or needs a token. The point being protected is
that live data arrives through the same tables, the same reader and the same
classifier as the fixture -- and that the two never see each other.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import SOURCE_LIVE, SOURCE_REPLAY
from app.db.models import DailyBar, IndexMeta, QuoteRow, Symbol, TradingDay
from app.domain.verdicts import Freshness, Quote, Verdict
from app.services import briefing, identity, watchlist
from app.sources import live, replay
from app.sources.upstox import Bar, Instrument, LiveQuote, UpstoxUnavailable
from tests.support import (  # noqa: F401
    _shared_database,
    fresh_database,
)

TODAY = date(2026, 9, 4)


def sessions(count: int, last: date = TODAY) -> list[date]:
    days: list[date] = []
    cursor = last
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def bars(days: list[date], start: float, step: float = 1.0) -> list[Bar]:
    return [
        Bar(day=day, close=round(start + index * step, 2), volume=1000 + index)
        for index, day in enumerate(days)
    ]


class FakeUpstox:
    """Stands in for UpstoxClient. Records calls so failures can be targeted."""

    def __init__(self, *, instruments=None, candles=None, quote=None, events=None):
        self.instrument_list = instruments or [
            Instrument("TCS", "Tata Consultancy Services", "NSE_EQ|INE467B01029",
                       "INE467B01029", "NSE_INDEX|Nifty IT"),
            Instrument("RELIABLE", "Reliable Data Services", "NSE_EQ|INE999A01011",
                       "INE999A01011", None),
        ]
        days = sessions(150)
        self._candles = candles or {
            "NSE_EQ|INE467B01029": bars(days, 3000.0, 2.0),
            "NSE_EQ|INE999A01011": bars(days, 500.0, 0.5),
            "NSE_INDEX|Nifty 50": bars(days, 24000.0, 5.0),
            "NSE_INDEX|Nifty IT": bars(days, 38000.0, 8.0),
        }
        self._quote = quote if quote is not None else LiveQuote(
            price=3400.0, event_time=datetime(2026, 9, 4, 15, 29), prev_close=3390.0
        )
        self._events = events or []
        self.calls: list[str] = []

    def instruments(self, refresh: bool = False):
        return self.instrument_list

    def search(self, query, limit=25):
        needle = query.upper()
        return [i for i in self.instrument_list if needle in i.symbol or needle in i.name.upper()]

    def find(self, symbol):
        return next((i for i in self.instrument_list if i.symbol == symbol.upper()), None)

    def daily_candles(self, instrument_key, start, end):
        self.calls.append(f"candles:{instrument_key}")
        result = self._candles.get(instrument_key)
        if isinstance(result, Exception):
            raise result
        return result or []

    def quote(self, instrument_key):
        self.calls.append(f"quote:{instrument_key}")
        if isinstance(self._quote, Exception):
            raise self._quote
        return self._quote

    def corporate_actions(self, instrument_key, symbol):
        return list(self._events)


@pytest.fixture
def api():
    fake = FakeUpstox()
    live.set_client(fake)
    yield fake
    live.set_client(None)


@pytest.fixture
def live_user(fresh_database, api):
    with fresh_database() as session:
        user, _ = identity.start_guest_session(session)
        user.data_source = SOURCE_LIVE
        session.commit()
        yield session, user


# ------------------------------------------------------------------- ingestion


def test_adding_a_live_instrument_stores_it_as_live(live_user):
    session, user = live_user

    live.add_instrument(session, "TCS")

    row = session.get(Symbol, "TCS")
    assert row.source == SOURCE_LIVE
    assert row.instrument_key == "NSE_EQ|INE467B01029"
    assert row.isin == "INE467B01029"


def test_history_market_and_sector_are_all_fetched(live_user, api):
    session, _ = live_user

    live.add_instrument(session, "TCS")

    assert "candles:NSE_EQ|INE467B01029" in api.calls
    assert "candles:NSE_INDEX|Nifty 50" in api.calls
    assert "candles:NSE_INDEX|Nifty IT" in api.calls


def test_enough_history_is_stored_for_the_classifier(live_user):
    session, _ = live_user

    live.add_instrument(session, "TCS")

    stored = session.scalar(
        select(func.count()).select_from(DailyBar).where(DailyBar.symbol == "TCS")
    )
    assert stored >= 70


def test_the_market_index_is_recorded_for_the_live_source(live_user):
    session, _ = live_user

    live.add_instrument(session, "TCS")

    market = session.get(IndexMeta, live.MARKET_CODE)
    assert market.is_market is True
    assert market.source == SOURCE_LIVE
    assert replay.market_index(session, SOURCE_LIVE) == live.MARKET_CODE


def test_a_symbol_with_no_mapped_sector_gets_none_rather_than_a_guess(live_user):
    session, _ = live_user

    live.add_instrument(session, "RELIABLE")

    assert session.get(Symbol, "RELIABLE").sector_index is None
    context = replay.load_context(session, "RELIABLE")
    assert context.has_sector is False


def test_a_mapped_symbol_is_compared_against_its_real_sector_index(live_user):
    session, _ = live_user

    live.add_instrument(session, "TCS")

    context = replay.load_context(session, "TCS")
    assert context.sector_index == live.index_code_for("NSE_INDEX|Nifty IT")
    assert context.has_sector is True


def test_sessions_the_index_does_not_share_are_not_stored(live_user, api):
    """A stock priced on a day its index was not cannot be compared."""
    session, _ = live_user
    days = sessions(150)
    api._candles["NSE_INDEX|Nifty 50"] = bars(days[:-3], 24000.0, 5.0)
    api._candles["NSE_INDEX|Nifty IT"] = bars(days[:-3], 38000.0, 8.0)

    live.add_instrument(session, "TCS")

    stored = set(session.scalars(select(DailyBar.day).where(DailyBar.symbol == "TCS")))
    assert days[-1] not in stored
    # Still loadable, which is the point: the reader never sees a hole.
    assert replay.load_context(session, "TCS").sessions


def test_an_unlisted_symbol_is_refused(live_user):
    session, _ = live_user

    with pytest.raises(live.LiveDataUnavailable):
        live.add_instrument(session, "NOSUCH")


def test_a_live_instrument_cannot_take_over_a_fixture_symbol(live_user):
    """The fixture's issuers are already in the table; live must not overwrite one."""
    session, _ = live_user
    live.set_client(
        FakeUpstox(
            instruments=[Instrument("NWTC", "Impostor Ltd", "NSE_EQ|FAKE", None, None)]
        )
    )

    with pytest.raises(live.LiveDataUnavailable):
        live.add_instrument(session, "NWTC")

    assert session.get(Symbol, "NWTC").source == SOURCE_REPLAY


# -------------------------------------------------------------------- freshness


def test_a_recent_quote_is_live(live_user, api):
    session, _ = live_user
    api._quote = LiveQuote(price=3400.0, event_time=live.utcnow(), prev_close=3390.0)

    live.add_instrument(session, "TCS")

    assert session.get(QuoteRow, "TCS").freshness == Freshness.LIVE.value


def test_an_old_quote_is_stale(live_user, api):
    session, _ = live_user
    api._quote = LiveQuote(
        price=3400.0, event_time=live.utcnow() - timedelta(days=10), prev_close=None
    )

    live.add_instrument(session, "TCS")

    assert session.get(QuoteRow, "TCS").freshness == Freshness.STALE.value


def test_a_provider_failure_falls_back_to_the_last_close_as_unavailable(live_user, api):
    """A price of unknown currency must not support inference."""
    session, _ = live_user
    live.add_instrument(session, "TCS")
    api._quote = UpstoxUnavailable("provider down")

    result = live.refresh_quote(session, "TCS")

    assert result is Freshness.UNAVAILABLE
    assert session.get(QuoteRow, "TCS").freshness == Freshness.UNAVAILABLE.value


def test_a_quote_without_a_timestamp_is_not_treated_as_current(live_user, api):
    session, _ = live_user
    live.add_instrument(session, "TCS")
    api._quote = LiveQuote(price=9999.0, event_time=None, prev_close=None)

    assert live.refresh_quote(session, "TCS") is Freshness.UNAVAILABLE


def test_an_aging_quote_goes_stale_at_read_time_without_a_background_job():
    quote = Quote(
        symbol="TCS",
        price=100.0,
        event_time=datetime(2026, 9, 1, 15, 30),
        freshness=Freshness.LIVE,
        source="upstox",
    )

    fresh = live.freshen(quote, datetime(2026, 9, 1, 16, 0))
    stale = live.freshen(quote, datetime(2026, 9, 8, 16, 0))

    assert fresh.freshness is Freshness.LIVE
    assert stale.freshness is Freshness.STALE


def test_refreshing_a_watchlist_survives_one_bad_symbol(live_user, api):
    session, _ = live_user
    live.add_instrument(session, "TCS")
    live.add_instrument(session, "RELIABLE")

    outcome = live.refresh_watchlist(session, ["TCS", "GHOST", "RELIABLE"])

    assert outcome["TCS"] in {f.value for f in Freshness}
    assert outcome["RELIABLE"] in {f.value for f in Freshness}
    assert outcome["GHOST"].startswith("unavailable")


# --------------------------------------------------------------------- the brief


def test_the_existing_classifier_runs_on_live_data(live_user):
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")

    brief = briefing.build_brief(session, user)

    assert brief["counts"]["checked"] == 1
    card = _cards(brief)["TCS"]
    assert card["verdict"] in {v.value for v in Verdict}
    detail = briefing.symbol_detail(session, user, "TCS")
    # Real numbers reached the classifier, not fixture ones.
    assert detail["evidence"]["history_bars"] >= 70
    assert detail["evidence"]["own"]["sample_size"] > 0


def test_a_live_brief_reports_the_live_market_index(live_user):
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")

    brief = briefing.build_brief(session, user)

    assert brief["market"]["index"] == live.MARKET_CODE
    assert brief["market"]["name"] == live.MARKET_NAME


def test_one_broken_symbol_does_not_break_the_whole_brief(live_user):
    """A provider that fails for one instrument must not cost the others."""
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    live.add_instrument(session, "RELIABLE")
    watchlist.add(session, user.id, "RELIABLE")
    session.execute(DailyBar.__table__.delete().where(DailyBar.symbol == "RELIABLE"))
    session.commit()

    brief = briefing.build_brief(session, user)

    assert brief["counts"]["checked"] == 2
    cards = _cards(brief)
    assert cards["RELIABLE"]["verdict"] == "CANT_SAY"
    assert cards["TCS"]["verdict"] != "CANT_SAY"


# ------------------------------------------------------------------- isolation


def test_live_sessions_do_not_move_the_moment_a_demo_brief_is_built_for(live_user):
    """The fixture calendar and the exchange's overlap; they must stay separate."""
    session, _ = live_user
    before = replay.replay_now(session, SOURCE_REPLAY)

    live.add_instrument(session, "TCS")

    assert replay.replay_now(session, SOURCE_REPLAY) == before
    assert replay.replay_now(session, SOURCE_LIVE) is not None


def test_a_replay_user_never_sees_live_instruments(live_user):
    session, _ = live_user
    live.add_instrument(session, "TCS")

    catalogue = {row.symbol for row in watchlist.list_symbols(session, source=SOURCE_REPLAY)}

    assert "TCS" not in catalogue
    assert "NWTC" in catalogue


def test_a_live_user_never_sees_fixture_instruments(live_user):
    session, _ = live_user
    live.add_instrument(session, "TCS")

    catalogue = {row.symbol for row in watchlist.list_symbols(session, source=SOURCE_LIVE)}

    assert catalogue == {"TCS"}


def test_a_demo_brief_is_unchanged_by_live_data(fresh_database, api):
    with fresh_database() as session:
        demo, _ = identity.start_guest_session(session)
        for symbol in ("NWTC", "KVRB", "VNTP"):
            watchlist.add(session, demo.id, symbol)
        before = briefing.build_brief(session, demo)["counts"]

        live_user, _ = identity.start_guest_session(session)
        live_user.data_source = SOURCE_LIVE
        session.commit()
        watchlist.add_for_user(session, live_user, "TCS")

        assert briefing.build_brief(session, demo)["counts"] == before


def test_the_two_calendars_are_stored_separately(live_user):
    session, _ = live_user
    live.add_instrument(session, "TCS")

    replay_days = session.scalar(
        select(func.count()).select_from(TradingDay).where(TradingDay.source == SOURCE_REPLAY)
    )
    live_days = session.scalar(
        select(func.count()).select_from(TradingDay).where(TradingDay.source == SOURCE_LIVE)
    )

    assert replay_days > 0
    assert live_days > 0


def _cards(brief: dict) -> dict[str, dict]:
    found = list(brief["needs_you"]) + brief["quiet"]["symbols"] + brief["cant_say"]["symbols"]
    for group in brief["explained"]["groups"]:
        found += group["symbols"]
    return {card["symbol"]: card for card in found}
