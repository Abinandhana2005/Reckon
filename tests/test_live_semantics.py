"""What Live Mode claims about time, coverage and provenance.

These are the failures that do not raise. A stale sector index that silently
truncated a stock's history, a settled close stamped five and a half hours into
its own future, a partial bar for a session still trading -- each of them left
the app answering confidently with the wrong number, which is worse for this
product than answering CANT_SAY.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import SOURCE_LIVE, SOURCE_REPLAY
from app.db.models import DailyBar, QuoteRow, Symbol, TradingDay
from app.domain.verdicts import Freshness
from app.services import briefing, identity, simulation, watchlist
from app.sources import live, nse, replay
from app.sources.provider import Bar, Instrument, LiveQuote
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
    """A provider whose series can be shortened or failed per instrument."""

    def __init__(
        self,
        *,
        sector_days: int | None = None,
        visible: int | None = None,
        sector_gap: int | None = None,
    ):
        days = sessions(150)
        self.days = days
        self.visible = visible or len(days)
        self.sector_days = sector_days
        self.sector_gap = sector_gap
        self.extra: list[Bar] = []
        self.quote_value = LiveQuote(
            price=3400.0,
            event_time=datetime.combine(days[-1], live.NSE_CLOSE_UTC),
            prev_close=3390.0,
            settled_close=True,
        )
        self.calls: list[str] = []

    def find(self, symbol):
        if symbol.upper() != "TCS":
            return None
        return Instrument("TCS", "Tata Consultancy Services", "TCS.NS", None, "NSE_INDEX|Nifty IT")

    def search(self, query, limit=25):
        found = self.find(query)
        return [found] if found else []

    def daily_candles(self, instrument_key, start, end):
        self.calls.append(f"candles:{instrument_key}")
        window = self.days[: self.visible]
        if instrument_key == "TCS.NS":
            return bars(window, 3000.0, 2.0) + self.extra
        if instrument_key == "NSE_INDEX|Nifty 50":
            return bars(window, 24000.0, 5.0) + self.extra
        if instrument_key == "NSE_INDEX|Nifty IT":
            sector = window if self.sector_days is None else window[: self.sector_days]
            if self.sector_gap is not None:
                sector = [d for i, d in enumerate(sector) if i != self.sector_gap]
            return bars(sector, 38000.0, 8.0)
        return []

    def quote(self, instrument_key):
        self.calls.append(f"quote:{instrument_key}")
        return self.quote_value

    def corporate_actions(self, instrument_key, symbol):
        return []


@pytest.fixture
def provider():
    fake = FakeProvider()
    live.set_client(fake)
    yield fake
    live.set_client(None)


@pytest.fixture
def live_user(fresh_database, provider):
    with fresh_database() as session:
        user, _ = identity.start_guest_session(session)
        user.data_source = SOURCE_LIVE
        session.commit()
        yield session, user


# ------------------------------------------------------------------- coverage


def test_a_sector_that_stopped_publishing_is_dropped_not_used_to_truncate(fresh_database):
    """The regression that made RELIANCE unusable.

    Nifty Energy had not published for seven weeks. Intersecting the stock's
    bars against it cut the stock's own history back to the sector's last good
    day, and the brief then read a seven-week-old close as the latest session.
    """
    fake = FakeProvider(sector_days=100)
    live.set_client(fake)
    try:
        with fresh_database() as session:
            user, _ = identity.start_guest_session(session)
            user.data_source = SOURCE_LIVE
            session.commit()

            watchlist.add_for_user(session, user, "TCS")

            stored = session.scalar(
                select(func.count()).select_from(DailyBar).where(DailyBar.symbol == "TCS")
            )
            assert stored == len(fake.days)
            assert session.scalar(
                select(func.max(DailyBar.day)).where(DailyBar.symbol == "TCS")
            ) == fake.days[-1]
            # The comparison that cannot be made is declared missing, not faked.
            assert session.get(Symbol, "TCS").sector_index is None
            assert replay.load_context(session, "TCS").has_sector is False
    finally:
        live.set_client(None)


def test_a_sector_missing_one_interior_session_is_kept(fresh_database):
    """A hole in the middle costs one session from the baseline, and no more.

    Only the end of the series can move the moment a brief is computed for, so
    only the end is treated as disqualifying.
    """
    fake = FakeProvider(sector_gap=40)
    live.set_client(fake)
    try:
        with fresh_database() as session:
            user, _ = identity.start_guest_session(session)
            user.data_source = SOURCE_LIVE
            session.commit()

            watchlist.add_for_user(session, user, "TCS")

            assert session.get(Symbol, "TCS").sector_index is not None
            stored = session.scalar(
                select(func.count()).select_from(DailyBar).where(DailyBar.symbol == "TCS")
            )
            assert stored == len(fake.days) - 1
            assert session.scalar(
                select(func.max(DailyBar.day)).where(DailyBar.symbol == "TCS")
            ) == fake.days[-1]
            assert replay.load_context(session, "TCS").has_sector is True
    finally:
        live.set_client(None)


def test_a_covering_sector_is_still_used(live_user):
    session, user = live_user

    watchlist.add_for_user(session, user, "TCS")

    assert session.get(Symbol, "TCS").sector_index == live.index_code_for("NSE_INDEX|Nifty IT")


# ---------------------------------------------------------------------- time


def test_the_live_calendar_closes_at_the_indian_bell_expressed_in_utc(live_user):
    session, user = live_user

    watchlist.add_for_user(session, user, "TCS")

    row = session.scalar(
        select(TradingDay)
        .where(TradingDay.source == SOURCE_LIVE)
        .order_by(TradingDay.day.desc())
        .limit(1)
    )
    assert row.close_at.time() == live.NSE_CLOSE_UTC
    assert replay.session_close_at(row.day, SOURCE_LIVE).time() == live.NSE_CLOSE_UTC


def test_a_session_that_has_not_closed_is_not_a_completed_session():
    """Yahoo answers a history request during the session with a partial bar."""
    now = datetime(2026, 9, 3, 6, 0)  # 11:30 IST, mid-session
    settled = Bar(day=date(2026, 9, 2), close=100.0, volume=1)
    in_progress = Bar(day=date(2026, 9, 3), close=101.0, volume=1)

    kept = [
        bar
        for bar in (settled, in_progress)
        if datetime.combine(bar.day, live.NSE_CLOSE_UTC) <= now
    ]

    assert kept == [settled]
    # And the writer applies exactly that rule against the wall clock.
    future = Bar(day=live.utcnow().date() + timedelta(days=1), close=9999.0, volume=1)
    assert future not in live.completed_sessions([future])


def test_a_partial_bar_never_reaches_storage(live_user, provider):
    session, user = live_user
    provider.extra = [Bar(day=live.utcnow().date() + timedelta(days=1), close=9999.0, volume=1)]

    watchlist.add_for_user(session, user, "TCS")

    stored = set(session.scalars(select(DailyBar.day).where(DailyBar.symbol == "TCS")))
    assert provider.extra[0].day not in stored
    assert 9999.0 not in set(
        session.scalars(select(DailyBar.close).where(DailyBar.symbol == "TCS"))
    )


def test_a_settled_close_is_recorded_closed_rather_than_live(live_user):
    session, user = live_user

    watchlist.add_for_user(session, user, "TCS")

    # Trusted either way. Only one of them may be described to a reader as the
    # current market, and this one is Thursday's close.
    assert session.get(QuoteRow, "TCS").freshness == Freshness.CLOSED.value


def test_market_state_follows_regular_exchange_hours():
    # 09:15 IST is 03:45 UTC; 15:30 IST is 10:00 UTC.
    assert nse.market_state(datetime(2026, 9, 3, 6, 0)) == nse.OPEN
    assert nse.market_state(datetime(2026, 9, 3, 3, 0)) == nse.CLOSED
    assert nse.market_state(datetime(2026, 9, 3, 10, 0)) == nse.CLOSED
    # Saturday.
    assert nse.market_state(datetime(2026, 9, 5, 6, 0)) == nse.CLOSED


# --------------------------------------------------------------------- refresh


def test_refreshing_extends_the_price_series_not_only_the_quote(fresh_database):
    """Sessions close while nobody is looking.

    Without a top-up the stored bars stop on the day each symbol was added, and
    the brief ends up analysing a session those symbols hold no price for.
    """
    fake = FakeProvider(visible=145)
    live.set_client(fake)
    try:
        with fresh_database() as session:
            user, _ = identity.start_guest_session(session)
            user.data_source = SOURCE_LIVE
            session.commit()
            watchlist.add_for_user(session, user, "TCS")
            before = session.scalar(
                select(func.max(DailyBar.day)).where(DailyBar.symbol == "TCS")
            )
            assert before == fake.days[144]

            # Five more sessions have closed since.
            fake.visible = 150

            live.refresh_watchlist(session, ["TCS"])

            after = session.scalar(
                select(func.max(DailyBar.day)).where(DailyBar.symbol == "TCS")
            )
            assert after == fake.days[-1]
            # The calendar moved with it, so the brief's endpoint is a session
            # this symbol actually holds a price for.
            assert after in set(
                session.scalars(
                    select(TradingDay.day).where(TradingDay.source == SOURCE_LIVE)
                )
            )
    finally:
        live.set_client(None)


# ------------------------------------------------------------------ provenance


def test_provenance_separates_the_moments_it_reports(live_user):
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")

    provenance = briefing.build_brief(session, user)["provenance"]

    assert provenance["provider"] == "Yahoo Finance"
    assert provenance["data_fetched_at"] is not None
    assert provenance["latest_completed_session_at"] is not None
    assert provenance["latest_available_quote_at"] is not None
    assert provenance["market_state"] in {nse.OPEN, nse.CLOSED}
    assert provenance["stale_symbols"] == []


def test_an_untrusted_quote_is_named_in_provenance_rather_than_averaged_away(live_user, provider):
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    provider.quote_value = LiveQuote(
        price=3400.0, event_time=datetime(2026, 1, 1, 10, 0), prev_close=3390.0
    )
    live.refresh_quote(session, "TCS")

    provenance = briefing.build_brief(session, user)["provenance"]

    assert provenance["stale_symbols"] == ["TCS"]
    assert provenance["latest_available_quote_at"] is None


def test_sample_data_provenance_never_looks_live(fresh_database):
    with fresh_database() as session:
        user, _ = identity.start_guest_session(session)
        watchlist.add(session, user.id, "NWTC")

        brief = briefing.build_brief(session, user)

    provenance = brief["provenance"]
    assert provenance["provider"] == "Sample fixture"
    # The session the analysis ran through is a real fact about sample data and
    # is stated. Everything that would imply a provider was contacted is absent
    # rather than filled in with something plausible.
    assert provenance["latest_completed_session_at"] == brief["as_of"]
    assert "data_fetched_at" not in provenance
    assert "latest_available_quote_at" not in provenance
    assert "market_state" not in provenance
    assert brief["data_source"] == SOURCE_REPLAY


# -------------------------------------------------------------------- isolation


def test_a_demo_scenario_resolves_against_the_fixture_calendar_only(fresh_database):
    """Both calendars cover real weekdays and they overlap.

    An unscoped lookup could answer a demo scenario with a session that came
    from the exchange, and the two calendars do not even close at the same
    moment.
    """
    with fresh_database() as session:
        fixture_day = session.scalar(
            select(TradingDay.day).where(TradingDay.source == SOURCE_REPLAY).limit(1)
        )
        session.add(
            TradingDay(
                day=fixture_day,
                source=SOURCE_LIVE,
                close_at=datetime.combine(fixture_day, live.NSE_CLOSE_UTC),
            )
        )
        session.commit()

        resolved = simulation.session_close_at(session, fixture_day)

    assert resolved == replay.session_close_at(fixture_day, SOURCE_REPLAY)
    assert resolved.time() != live.NSE_CLOSE_UTC


# ----------------------------------------------------------------- the payload


def test_the_brief_names_one_place_to_start_and_accounts_for_the_rest(fresh_database):
    with fresh_database() as session:
        user, _ = identity.start_guest_session(session)
        for symbol in ("MRDB", "KVRB", "NWTC", "ORBS"):
            watchlist.add(session, user.id, symbol)

        brief = briefing.build_brief(session, user)

    counts = brief["counts"]
    assert counts["checked"] == 4
    assert brief["silence_report"].startswith("4 stocks checked.")
    if counts["needs_you"]:
        # Start Here is the head of needs_you, not a separate ranking.
        assert brief["start_here"]["card"]["symbol"] == brief["needs_you"][0]["symbol"]
        assert brief["start_here"]["line"]
    else:
        assert brief["start_here"] is None


def test_uncertainty_is_grouped_by_stated_reason(fresh_database):
    with fresh_database() as session:
        user, _ = identity.start_guest_session(session)
        watchlist.add(session, user.id, "ARDB")

        brief = briefing.build_brief(session, user)

    reasons = brief["cant_say"]["reasons"]
    assert brief["cant_say"]["count"] == 1
    assert [entry["reason"] for entry in reasons] == ["INSUFFICIENT_HISTORY"]
    assert reasons[0]["symbols"] == ["ARDB"]
    assert reasons[0]["label"] and reasons[0]["detail"]


def test_the_detail_view_carries_a_complete_decision_trace(fresh_database):
    with fresh_database() as session:
        user, _ = identity.start_guest_session(session)
        watchlist.add(session, user.id, "MRDB")

        detail = briefing.symbol_detail(session, user, "MRDB")

    assert [step["key"] for step in detail["decision_trace"]] == [
        "data", "event", "own", "market", "sector", "verdict"
    ]


# ------------------------------------------------------ the one quote writer


def test_a_newer_quote_replaces_the_stored_one(live_user, provider):
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    stored = session.get(QuoteRow, "TCS")
    original = stored.price

    provider.quote_value = LiveQuote(
        price=original + 10,
        event_time=stored.event_time + timedelta(minutes=1),
        prev_close=original,
    )
    live.refresh_quote(session, "TCS")

    assert session.get(QuoteRow, "TCS").price == pytest.approx(original + 10)


def test_a_late_quote_does_not_overwrite_a_newer_one(live_user, provider):
    """Out-of-order delivery must not rewind the price the reader is shown."""
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    stored = session.get(QuoteRow, "TCS")
    original = stored.price

    provider.quote_value = LiveQuote(
        price=original + 999,
        event_time=stored.event_time - timedelta(days=1),
        prev_close=None,
    )
    live.refresh_quote(session, "TCS")

    assert session.get(QuoteRow, "TCS").price == pytest.approx(original)


def test_a_late_but_stale_answer_still_ages_the_price_it_could_not_replace(
    live_user, provider
):
    """The one thing a late quote may change is how much we trust the stored one."""
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    stored = session.get(QuoteRow, "TCS")
    original = stored.price

    provider.quote_value = LiveQuote(
        price=original + 999,
        event_time=datetime(2020, 1, 1, 10, 0),
        prev_close=None,
    )
    live.refresh_quote(session, "TCS")

    row = session.get(QuoteRow, "TCS")
    assert row.price == pytest.approx(original)
    assert row.freshness == Freshness.STALE.value


def test_re_polling_the_same_session_lands_so_the_price_can_age(live_user, provider):
    """Not new information about the price, but new information about its age."""
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")
    stored = session.get(QuoteRow, "TCS")
    first_fetch = stored.ingested_at

    live.refresh_quote(session, "TCS")

    assert session.get(QuoteRow, "TCS").ingested_at >= first_fetch


# ------------------------------------------------------- while the market is open


MID_SESSION = datetime(2026, 9, 4, 6, 0)
"""11:30 IST on a Friday: the exchange is trading and the bell is hours away."""


def test_a_brief_built_while_the_market_trades_ends_at_the_last_closed_session(
    live_user, provider
):
    session, user = live_user
    # Yesterday's settled close is what is on record when the bell rings.
    provider.quote_value = LiveQuote(
        price=3148.0,
        event_time=datetime.combine(date(2026, 9, 3), live.NSE_CLOSE_UTC),
        prev_close=3140.0,
        settled_close=True,
    )
    watchlist.add_for_user(session, user, "TCS")

    # Mid-session, Yahoo answers with the price so far, observed now.
    provider.quote_value = LiveQuote(price=3210.0, event_time=MID_SESSION, prev_close=3148.0)
    live.refresh_quote(session, "TCS")

    brief = briefing.build_brief(session, user, now=MID_SESSION)
    provenance = brief["provenance"]

    analysis = datetime.fromisoformat(provenance["latest_completed_session_at"])
    quote_at = datetime.fromisoformat(provenance["latest_available_quote_at"])

    assert provenance["market_state"] == nse.OPEN
    # The analysis ends at a session that has closed...
    assert analysis <= MID_SESSION
    assert analysis.time() == live.NSE_CLOSE_UTC
    # ...while the newer quote is reported beside it rather than folded into it.
    assert quote_at > analysis
    assert provenance["quote_freshness"] == Freshness.LIVE.value


def test_provenance_describes_the_moment_the_brief_was_built_for(live_user):
    """Not the moment the request happened to run.

    A brief assembled for an explicit moment has to describe the exchange as it
    stood then, or a replayed brief reports whatever the wall clock says now.
    """
    session, user = live_user
    watchlist.add_for_user(session, user, "TCS")

    trading = briefing.build_brief(session, user, now=MID_SESSION)
    after_hours = briefing.build_brief(
        session, user, now=datetime(2026, 9, 4, 14, 0)
    )

    assert trading["provenance"]["market_state"] == nse.OPEN
    assert after_hours["provenance"]["market_state"] == nse.CLOSED


def test_adding_a_symbol_someone_else_already_watches_brings_it_up_to_date(fresh_database):
    """Market data is shared; staleness must not be.

    The second reader to add a symbol was handed the first reader's copy of it,
    however many sessions had closed in between.
    """
    fake = FakeProvider(visible=145)
    live.set_client(fake)
    try:
        with fresh_database() as session:
            first, _ = identity.start_guest_session(session)
            first.data_source = SOURCE_LIVE
            session.commit()
            watchlist.add_for_user(session, first, "TCS")
            assert session.scalar(
                select(func.max(DailyBar.day)).where(DailyBar.symbol == "TCS")
            ) == fake.days[144]

            # Five sessions close before anyone else asks for it.
            fake.visible = 150

            second, _ = identity.start_guest_session(session)
            second.data_source = SOURCE_LIVE
            session.commit()
            watchlist.add_for_user(session, second, "TCS")

            assert session.scalar(
                select(func.max(DailyBar.day)).where(DailyBar.symbol == "TCS")
            ) == fake.days[-1]
            # And it is still one shared instrument, not a second copy.
            assert session.get(Symbol, "TCS").source == SOURCE_LIVE
    finally:
        live.set_client(None)
