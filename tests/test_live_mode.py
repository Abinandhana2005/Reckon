"""Live Mode against a stand-in provider.

No test here touches the network or needs a token. The point being protected is
that live data arrives through the same tables, the same reader and the same
classifier as the fixture -- and that the two never see each other.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import SOURCE_LIVE, SOURCE_REPLAY
from app.db.models import (
    DailyBar,
    IndexMeta,
    QuoteRow,
    Symbol,
    TradingDay,
    UserSourceVisit,
    UserSymbolAnchor,
)
from app.domain.verdicts import Freshness, Quote, Verdict
from app.services import briefing, identity, visits, watchlist
from app.sources import live, replay
from app.sources.provider import Bar, Instrument, LiveQuote, ProviderUnavailable
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


class FakeProvider:
    """Stands in for a live provider client. Records calls so failures can be targeted.

    The vendor behind it is not the point: this module tests `app.sources.live`,
    the shared writer every provider goes through, so a fake conforming to the
    same small interface (find, search, daily_candles, quote, corporate_actions)
    is what every test here needs.
    """

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
    fake = FakeProvider()
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


def test_a_concurrent_insert_of_the_same_symbol_is_retried_not_raised(live_user):
    """Two users adding the same new live symbol at once must not 500.

    The second transaction to commit collides on the Symbol primary key. That
    is simulated here by making the first commit fail like a real unique
    violation would; `add_instrument` is expected to roll back and replay the
    whole fetch-and-store once, landing the row exactly as a solo add would.
    """
    session, _ = live_user
    real_commit = session.commit
    attempts = {"n": 0, "failed_once": False}

    def flaky_commit():
        attempts["n"] += 1
        if attempts["n"] == 1:
            attempts["failed_once"] = True
            raise IntegrityError("insert", {}, Exception("UNIQUE constraint failed: symbols.symbol"))
        real_commit()

    session.commit = flaky_commit

    row = live.add_instrument(session, "TCS")

    assert attempts["failed_once"] is True
    assert attempts["n"] > 1
    assert row.symbol == "TCS"
    assert session.get(Symbol, "TCS").source == SOURCE_LIVE
    assert session.get(Symbol, "TCS").instrument_key == "NSE_EQ|INE467B01029"


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
        FakeProvider(
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
    api._quote = ProviderUnavailable("provider down")

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
        source="yahoo",
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


def test_live_brief_uses_previous_visit_and_latest_completed_session(live_user):
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    session.add(
        UserSourceVisit(
            user_id=user.id, source=SOURCE_LIVE, last_open_at=datetime(2026, 9, 3, 12, 0)
        )
    )
    session.commit()

    visit = datetime(2026, 9, 4, 9, 0)
    brief = briefing.build_brief(session, user, now=visit)

    # The brief describes the previous visit; the clock moves only afterwards.
    assert brief["last_open_at"] == "2026-09-03T12:00:00"
    assert brief["as_of"] == "2026-09-03T10:00:00"
    assert brief["provenance"]["latest_completed_session_at"] == "2026-09-03T10:00:00"
    assert visits.previous(session, user.id, SOURCE_LIVE) == visit
    assert user.last_open_at == visit


def test_rereading_the_brief_in_one_sitting_does_not_reset_last_checked(live_user):
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")

    first = briefing.build_brief(session, user, now=datetime(2026, 9, 4, 9, 0))
    again = briefing.build_brief(session, user, now=datetime(2026, 9, 4, 9, 5))
    later = briefing.build_brief(session, user, now=datetime(2026, 9, 4, 11, 0))

    assert first["last_open_at"] is None
    # Five minutes later is the same sitting: the header still describes the
    # visit that started it, not an empty span.
    assert again["last_open_at"] == "2026-09-04T09:00:00"
    assert later["last_open_at"] == "2026-09-04T09:00:00"
    assert visits.previous(session, user.id, SOURCE_LIVE) == datetime(2026, 9, 4, 11, 0)


def test_the_visit_clock_is_kept_per_source(live_user):
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    briefing.build_brief(session, user, now=datetime(2026, 9, 4, 9, 0))

    user.data_source = SOURCE_REPLAY
    session.commit()
    watchlist.add(session, user.id, "NWTC")
    sample = briefing.build_brief(session, user)

    # Opening Sample must not tell the Live brief that the reader has been back.
    assert sample["last_open_at"] is None
    assert visits.previous(session, user.id, SOURCE_LIVE) == datetime(2026, 9, 4, 9, 0)
    assert visits.previous(session, user.id, SOURCE_REPLAY) is not None


def test_live_classifier_does_not_use_an_in_progress_quote(live_user, api):
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    api._quote = LiveQuote(
        price=9999.0,
        event_time=datetime(2026, 9, 4, 9, 30),
        prev_close=3400.0,
    )
    live.refresh_quote(session, "TCS")

    brief = briefing.build_brief(session, user, now=datetime(2026, 9, 4, 9, 0))
    detail = briefing.symbol_detail(session, user, "TCS", now=datetime(2026, 9, 4, 9, 0))

    assert brief["as_of"] == "2026-09-03T10:00:00"
    assert detail["as_of"] == "2026-09-03T10:00:00"
    assert detail["card"]["change"] != pytest.approx(9999.0 / 3400.0 - 1.0)


def test_failed_brief_load_does_not_advance_last_open(live_user, monkeypatch):
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    previous = datetime(2026, 9, 3, 12, 0)
    user.last_open_at = previous
    session.commit()

    def fail_payload(*args, **kwargs):
        raise RuntimeError("response assembly failed")

    monkeypatch.setattr(briefing, "_payload", fail_payload)
    with pytest.raises(RuntimeError):
        briefing.build_brief(session, user, now=datetime(2026, 9, 4, 12, 0))

    assert user.last_open_at == previous


def test_one_broken_symbol_does_not_break_the_whole_brief(live_user, api):
    """A provider that fails for one instrument must not cost the others."""
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    live.add_instrument(session, "RELIABLE")
    watchlist.add(session, user.id, "RELIABLE")
    session.execute(DailyBar.__table__.delete().where(DailyBar.symbol == "RELIABLE"))
    session.commit()
    # A live brief now syncs before classifying (see _sync_live_data), which
    # would otherwise re-fetch and heal these deleted bars from a provider
    # that is, in this test, still perfectly healthy. Making RELIABLE's own
    # fetch fail keeps this a genuine provider failure, which is what the test
    # means to exercise.
    api._candles["NSE_EQ|INE999A01011"] = ProviderUnavailable("boom")

    brief = briefing.build_brief(session, user)

    assert brief["counts"]["checked"] == 2
    cards = _cards(brief)
    assert cards["RELIABLE"]["verdict"] == "CANT_SAY"
    assert cards["TCS"]["verdict"] != "CANT_SAY"


# ------------------------------------------------------------- sync on open


def _add_next_session(api: FakeProvider, last_known: date, price: float) -> date:
    """Extend the market index and RELIABLE with one more session's bar.

    RELIABLE carries no sector, so extending just these two series is enough
    for the stock's own history to stay fully aligned after the new day lands.
    """
    new_day = last_known + timedelta(days=1)
    while new_day.weekday() >= 5:
        new_day += timedelta(days=1)
    api._candles["NSE_INDEX|Nifty 50"] = api._candles["NSE_INDEX|Nifty 50"] + [
        Bar(new_day, 24500.0, 6000)
    ]
    api._candles["NSE_EQ|INE999A01011"] = api._candles["NSE_EQ|INE999A01011"] + [
        Bar(new_day, price, 1500)
    ]
    return new_day


def test_opening_the_brief_fetches_a_newer_completed_session(live_user, api, monkeypatch):
    """The brief itself is the check -- no separate manual refresh is needed."""
    session, user = live_user
    watchlist.add_for_user(session, user, "RELIABLE")

    last_known = sessions(150)[-1]
    assert replay.latest_completed_session(session, live.utcnow(), SOURCE_LIVE) == (
        datetime.combine(last_known, live.NSE_CLOSE_UTC)
    )

    new_day = _add_next_session(api, last_known, price=520.0)
    fixed_now = datetime.combine(new_day, live.NSE_CLOSE_UTC) + timedelta(hours=1)
    monkeypatch.setattr(live, "utcnow", lambda: fixed_now)

    brief = briefing.build_brief(session, user, now=fixed_now)

    expected = datetime.combine(new_day, live.NSE_CLOSE_UTC)
    assert brief["as_of"] == expected.isoformat()
    assert replay.latest_completed_session(session, fixed_now, SOURCE_LIVE) == expected
    assert session.get(DailyBar, {"symbol": "RELIABLE", "day": new_day}) is not None


def test_opening_the_detail_view_also_fetches_a_newer_completed_session(
    live_user, api, monkeypatch
):
    """The Detail screen is a live entry point too, and must sync the same way."""
    session, user = live_user
    watchlist.add_for_user(session, user, "RELIABLE")

    last_known = sessions(150)[-1]
    new_day = _add_next_session(api, last_known, price=520.0)
    fixed_now = datetime.combine(new_day, live.NSE_CLOSE_UTC) + timedelta(hours=1)
    monkeypatch.setattr(live, "utcnow", lambda: fixed_now)

    detail = briefing.symbol_detail(session, user, "RELIABLE", now=fixed_now)

    expected = datetime.combine(new_day, live.NSE_CLOSE_UTC)
    assert detail["as_of"] == expected.isoformat()
    assert replay.latest_completed_session(session, fixed_now, SOURCE_LIVE) == expected


def test_a_brief_opened_mid_session_does_not_advance_past_the_last_close(
    live_user, api, monkeypatch
):
    """A still-trading session must never be read as a completed daily analysis."""
    session, user = live_user
    watchlist.add_for_user(session, user, "RELIABLE")

    last_known = sessions(150)[-1]
    new_day = _add_next_session(api, last_known, price=520.0)
    close_at = datetime.combine(new_day, live.NSE_CLOSE_UTC)
    mid_session = close_at - timedelta(hours=2)
    monkeypatch.setattr(live, "utcnow", lambda: mid_session)

    brief = briefing.build_brief(session, user, now=mid_session)

    expected = datetime.combine(last_known, live.NSE_CLOSE_UTC)
    assert brief["as_of"] == expected.isoformat()
    assert replay.latest_completed_session(session, mid_session, SOURCE_LIVE) == expected
    # The in-progress day must never reach storage as a completed session.
    assert session.get(TradingDay, {"day": new_day, "source": SOURCE_LIVE}) is None
    assert session.get(DailyBar, {"symbol": "RELIABLE", "day": new_day}) is None


def test_the_latest_available_quote_updates_on_a_live_brief_even_mid_session(live_user, api):
    """Analysis stays pinned to the last close; the displayed quote does not have to."""
    session, user = live_user
    watchlist.add_for_user(session, user, "RELIABLE")
    before = session.get(QuoteRow, "RELIABLE")
    before_price, before_fetched = before.price, before.ingested_at
    before_moment = replay.latest_completed_session(session, live.utcnow(), SOURCE_LIVE)

    # The price moved intraday since the symbol was added; nothing has closed.
    api._quote = LiveQuote(
        price=before_price + 5.0, event_time=live.utcnow(), prev_close=before_price
    )

    brief = briefing.build_brief(session, user)

    session.expire_all()
    after = session.get(QuoteRow, "RELIABLE")
    assert after.price == before_price + 5.0
    assert after.freshness == Freshness.LIVE.value
    assert after.ingested_at >= before_fetched
    assert brief["provenance"]["latest_available_quote_at"] == after.event_time.isoformat()
    # Analysis is unaffected: no session closed between the two visits.
    assert brief["provenance"]["latest_completed_session_at"] == before_moment.isoformat()


def test_a_sync_failure_on_open_does_not_break_the_brief(live_user, api):
    """A provider outage during the automatic sync must not fail the whole read."""
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    watchlist.add_for_user(session, user, "RELIABLE")

    before = briefing.build_brief(session, user)

    # The provider is entirely down for this visit.
    api._candles = {key: ProviderUnavailable("provider down") for key in api._candles}
    api._quote = ProviderUnavailable("provider down")

    after = briefing.build_brief(session, user)

    assert after["counts"]["checked"] == before["counts"]["checked"] == 2
    assert after["as_of"] == before["as_of"]
    assert _cards(after) == _cards(before)


def test_repeated_live_brief_views_never_move_the_anchor_only_ack_does(
    live_user, api, monkeypatch
):
    """Auto-syncing on open must not become a second way to advance an anchor."""
    session, user = live_user
    watchlist.add_for_user(session, user, "RELIABLE")
    original = session.get(UserSymbolAnchor, {"user_id": user.id, "symbol": "RELIABLE"})
    original_at, original_price = original.anchor_at, original.anchor_price

    last_known = sessions(150)[-1]
    new_day = _add_next_session(api, last_known, price=520.0)
    close_at = datetime.combine(new_day, live.NSE_CLOSE_UTC)

    # Mid-session, right at the close, and well after -- across all of them the
    # anchor must not move on its own.
    for moment in (
        close_at - timedelta(hours=2),
        close_at + timedelta(minutes=1),
        close_at + timedelta(hours=5),
    ):
        monkeypatch.setattr(live, "utcnow", lambda moment=moment: moment)
        briefing.build_brief(session, user, now=moment)
        session.expire_all()
        unmoved = session.get(UserSymbolAnchor, {"user_id": user.id, "symbol": "RELIABLE"})
        assert unmoved.anchor_at == original_at
        assert unmoved.anchor_price == original_price

    brief = briefing.build_brief(session, user, now=close_at + timedelta(hours=5))
    card = _cards(brief)["RELIABLE"]
    result = briefing.acknowledge(session, user, "RELIABLE", card["snapshot_id"])

    assert result["advanced"] is True
    assert result["anchor_at"] != original_at.isoformat()
    session.expire_all()
    moved = session.get(UserSymbolAnchor, {"user_id": user.id, "symbol": "RELIABLE"})
    assert moved.anchor_at != original_at


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
