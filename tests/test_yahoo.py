"""Yahoo adapter tests: no network, no token, no provider side effects."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
from sqlalchemy import select

from app.config import SOURCE_LIVE
from app.db.models import DailyBar, Symbol
from app.domain.verdicts import EventKind, Freshness
from app.services import identity, watchlist
from app.sources import live, yahoo
from app.sources.provider import Bar, Instrument, LiveQuote
from tests.support import fresh_database


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Close": [100.0, 101.5, 99.0],
            "Volume": [1000, 1200, 900],
            "Dividends": [0.0, 2.0, 0.0],
            "Stock Splits": [0.0, 0.0, 2.0],
        },
        index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
    )


def test_yahoo_ticker_mapping_supports_symbols_and_indices():
    assert yahoo.yahoo_ticker("TCS") == "TCS.NS"
    assert yahoo.yahoo_ticker("RELIANCE.NS") == "RELIANCE.NS"
    assert yahoo.yahoo_ticker("NSE_INDEX|Nifty 50") == "^NSEI"
    assert yahoo.yahoo_ticker("NSE_INDEX|Nifty IT") == "^CNXIT"


def test_search_normalizer_keeps_nse_equities_and_orders_exact_match():
    results = yahoo.normalize_search_results(
        {
            "quotes": [
                {"symbol": "TCS.NS", "shortname": "TCS", "quoteType": "EQUITY"},
                {"symbol": "TCS", "shortname": "Not NSE", "quoteType": "EQUITY"},
                {"symbol": "TCS.NS", "shortname": "Duplicate", "quoteType": "EQUITY"},
                {"symbol": "^NSEI", "shortname": "Index", "quoteType": "INDEX"},
            ]
        },
        "TCS",
    )

    assert [item.symbol for item in results] == ["TCS"]
    assert results[0].instrument_key == "TCS.NS"


def test_search_does_not_promote_unknown_typed_text_to_an_nse_equity():
    client = yahoo.YahooClient(
        ticker_factory=lambda _ticker: None,
        search_factory=lambda *args, **kwargs: {"quotes": []},
    )

    assert client.search("HAHA") == []


def test_history_normalizer_is_oldest_first_and_drops_bad_rows():
    frame = _history().copy()
    frame.loc[pd.Timestamp("2026-01-07"), "Close"] = None

    bars = yahoo.normalize_history(frame)

    assert bars == [
        Bar(date(2026, 1, 2), 100.0, 1000),
        Bar(date(2026, 1, 5), 101.5, 1200),
        Bar(date(2026, 1, 6), 99.0, 900),
    ]


def test_a_closed_session_becomes_a_settled_close():
    quote = yahoo.normalize_quote_from_history(
        _history(), "TCS", now=datetime(2026, 1, 7, 6, 0)
    )

    assert quote.price == 99.0
    assert quote.event_time == datetime(2026, 1, 6, 10, 0)
    assert quote.settled_close is True


def test_a_session_still_trading_is_an_observation_not_a_close():
    """Yahoo returns a bar for today while the market is open.

    Stamping that at the closing bell would put the timestamp in the future and
    describe a mid-session price as settled -- the one thing the freshness model
    must never say.
    """
    mid_session = datetime(2026, 1, 6, 6, 0)  # 11:30 IST, hours before the bell

    quote = yahoo.normalize_quote_from_history(_history(), "TCS", now=mid_session)

    assert quote.price == 99.0
    assert quote.event_time == mid_session
    assert quote.settled_close is False
    # The previous settled close is still the right comparison point.
    assert quote.prev_close == 101.5


def test_an_empty_frame_yields_no_quote_rather_than_a_guess():
    assert yahoo.normalize_quote_from_history(None, "TCS") is None


def test_actions_normalizer_retains_only_adjustment_safe_yahoo_actions():
    events = yahoo.normalize_actions(_history(), "TCS")

    assert [(event.on_date, event.kind, event.value) for event in events] == [
        (date(2026, 1, 5), EventKind.EX_DIVIDEND, 2.0),
        (date(2026, 1, 6), EventKind.SPLIT, 2.0),
    ]


def test_unknown_symbol_has_no_invented_sector_context():
    assert yahoo.sector_for("A_REAL_BUT_UNMAPPED_NSE_SYMBOL") is None


def test_yahoo_transport_failure_is_typed():
    def broken_ticker(_ticker):
        raise OSError("network unavailable")

    client = yahoo.YahooClient(ticker_factory=broken_ticker, search_factory=lambda *args, **kwargs: {"quotes": []})

    try:
        client.daily_candles("TCS.NS", date(2026, 1, 1), date(2026, 2, 1))
    except yahoo.YahooUnavailable as exc:
        assert "Yahoo request failed" in str(exc)
    else:
        raise AssertionError("provider failure was not surfaced as YahooUnavailable")


class FakeTicker:
    def __init__(self, ticker: str):
        self.ticker = ticker
        self.fast_info = {
            "lastPrice": 3400.0,
            "regularMarketTime": datetime(2026, 9, 4, 15, 29, tzinfo=timezone.utc),
            "previousClose": 3390.0,
        }

    def history(self, **kwargs):
        return _history()


def test_client_searches_the_raw_term_and_normalizes_history_quote_and_actions():
    calls: list[str] = []

    def search_factory(query, max_results):
        calls.append(query)
        return {"quotes": [{"symbol": "TCS.NS", "longname": "Tata Consultancy Services", "quoteType": "EQUITY"}]}

    client = yahoo.YahooClient(ticker_factory=FakeTicker, search_factory=search_factory)
    instrument = client.find("TCS")

    assert instrument == Instrument("TCS", "Tata Consultancy Services", "TCS.NS", None, "NSE_INDEX|Nifty IT")
    # The typed term goes to Yahoo unchanged. Appending .NS first is what broke
    # company-name search, because Yahoo matches nothing for "TATA CONSULTANCY.NS".
    assert calls == ["TCS"]
    assert len(client.daily_candles("TCS.NS", date(2026, 1, 1), date(2026, 2, 1))) == 3
    assert client.quote("TCS.NS").price == 99.0
    assert client.corporate_actions("TCS.NS", "TCS")[0].kind is EventKind.EX_DIVIDEND


def test_a_quote_costs_one_request_and_never_asks_fast_info():
    """fast_info carries no trade timestamp for NSE tickers.

    A quote built from it could not establish its own age, so the client fell
    through to the history call anyway -- one wasted request per symbol on every
    refresh, against a provider that rate-limits.
    """
    calls: list[str] = []

    class CountingTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def fast_info(self):
            raise AssertionError("fast_info must not be requested")

        def history(self, **kwargs):
            calls.append("history")
            return _history()

    client = yahoo.YahooClient(
        ticker_factory=CountingTicker, search_factory=lambda *a, **k: {"quotes": []}
    )

    assert client.quote("TCS.NS") is not None
    assert calls == ["history"]


def test_company_name_search_reaches_yahoo_unsuffixed():
    calls: list[str] = []

    def search_factory(query, max_results):
        calls.append(query)
        return {
            "quotes": [
                {"symbol": "TCS.NS", "longname": "Tata Consultancy Services Limited",
                 "quoteType": "EQUITY", "exchange": "NSI"},
                {"symbol": "TCS.BO", "longname": "Tata Consultancy Services Limited",
                 "quoteType": "EQUITY", "exchange": "BSE"},
            ]
        }

    client = yahoo.YahooClient(ticker_factory=FakeTicker, search_factory=search_factory)
    found = client.search("Tata Consultancy")

    assert calls == ["TATA CONSULTANCY"]
    assert [item.symbol for item in found] == ["TCS"]


def test_a_bare_ticker_that_finds_nothing_is_retried_with_the_nse_suffix():
    calls: list[str] = []

    def search_factory(query, max_results):
        calls.append(query)
        if query == "TCS":
            return {"quotes": [{"symbol": "TCS.DE", "quoteType": "EQUITY"}]}
        return {"quotes": [{"symbol": "TCS.NS", "shortname": "TCS", "quoteType": "EQUITY"}]}

    client = yahoo.YahooClient(ticker_factory=FakeTicker, search_factory=search_factory)
    found = client.search("TCS")

    assert calls == ["TCS", "TCS.NS"]
    assert [item.symbol for item in found] == ["TCS"]


def test_search_keeps_provider_relevance_order_but_hoists_an_exact_ticker():
    payload = {
        "quotes": [
            {"symbol": "RELINFRA.NS", "longname": "Reliance Infrastructure", "quoteType": "EQUITY"},
            {"symbol": "RELIANCE.NS", "longname": "Reliance Industries", "quoteType": "EQUITY"},
            {"symbol": "RHFL.NS", "longname": "Reliance Home Finance", "quoteType": "EQUITY"},
        ]
    }

    exact = yahoo.normalize_search_results(payload, "RELIANCE")
    assert [item.symbol for item in exact] == ["RELIANCE", "RELINFRA", "RHFL"]

    by_name = yahoo.normalize_search_results(payload, "RELIANCE INDUSTRIES")
    assert [item.symbol for item in by_name] == ["RELINFRA", "RELIANCE", "RHFL"]


def test_a_quote_derived_from_a_daily_bar_is_stamped_at_the_closing_bell_in_utc():
    quote = yahoo.normalize_quote_from_history(_history(), "TCS")

    # 15:30 IST is 10:00 UTC. Stamping 15:30 naive dated a settled close five
    # and a half hours into the future of the session it came from.
    assert quote.event_time == datetime(2026, 1, 6, 10, 0)


def test_yahoo_live_writer_keeps_demo_symbols_and_live_symbols_separate(fresh_database, monkeypatch):
    monkeypatch.setattr("app.config.LIVE_PROVIDER", "yahoo")
    fake = _FakeYahooProvider()
    live.set_client(fake)
    try:
        with fresh_database() as db:
            demo, _ = identity.start_guest_session(db)
            live_user, _ = identity.start_guest_session(db)
            live_user.data_source = SOURCE_LIVE
            db.commit()
            watchlist.add(db, demo.id, "NWTC")
            watchlist.add_for_user(db, live_user, "TCS")

            assert db.get(Symbol, "NWTC").source == "replay"
            assert db.get(Symbol, "TCS").source == SOURCE_LIVE
            assert db.scalar(
                select(DailyBar.source).where(DailyBar.symbol == "TCS").limit(1)
            ) == "yahoo"
            assert {item.symbol for item in watchlist.watched(db, demo.id)} == {"NWTC"}
            assert {item.symbol for item in watchlist.watched(db, live_user.id)} == {"TCS"}
    finally:
        live.set_client(None)


class _FakeYahooProvider:
    def __init__(self):
        days = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
        self._bars = [Bar(day, 100.0 + index, 1000) for index, day in enumerate(days)]

    def find(self, symbol):
        if symbol == "TCS":
            return Instrument("TCS", "Tata Consultancy Services", "TCS.NS", None, None)
        return None

    def daily_candles(self, key, start, end):
        return self._bars

    def quote(self, key):
        return LiveQuote(103.0, datetime(2026, 1, 6, 15, 29), 102.0)

    def corporate_actions(self, key, symbol):
        return []


def test_a_name_search_resolves_a_bombay_only_result_onto_nse():
    """Yahoo answers "Infosys" with INFY.BO and no NSE row at all."""
    calls: list[str] = []

    def search_factory(query, max_results):
        calls.append(query)
        if query == "INFOSYS":
            return {"quotes": [
                {"symbol": "INFY", "exchange": "NYQ", "quoteType": "EQUITY",
                 "longname": "Infosys Limited"},
                {"symbol": "INFY.BO", "exchange": "BSE", "quoteType": "EQUITY",
                 "longname": "Infosys Limited"},
            ]}
        if query == "INFY.NS":
            return {"quotes": [
                {"symbol": "INFY.NS", "exchange": "NSI", "quoteType": "EQUITY",
                 "longname": "Infosys Limited"},
            ]}
        # Yahoo matches nothing for "INFOSYS.NS", which is what sends the
        # search on to the cross-listing step.
        return {"quotes": []}

    client = yahoo.YahooClient(ticker_factory=FakeTicker, search_factory=search_factory)
    found = client.search("Infosys")

    assert [item.symbol for item in found] == ["INFY"]
    assert found[0].instrument_key == "INFY.NS"
    # The NSE ticker is confirmed with Yahoo rather than assumed from the BSE one.
    assert calls == ["INFOSYS", "INFOSYS.NS", "INFY.NS"]


def test_a_bombay_listing_with_no_nse_counterpart_is_not_invented():
    def search_factory(query, max_results):
        if query.endswith(".NS"):
            return {"quotes": []}
        return {"quotes": [
            {"symbol": "ONLYBSE.BO", "exchange": "BSE", "quoteType": "EQUITY"},
        ]}

    client = yahoo.YahooClient(ticker_factory=FakeTicker, search_factory=search_factory)

    assert client.search("Only Bombay") == []


def test_bse_symbols_ignores_non_equities_and_other_exchanges():
    payload = {"quotes": [
        {"symbol": "INFY.BO", "quoteType": "EQUITY"},
        {"symbol": "SOMEFUND.BO", "quoteType": "MUTUALFUND"},
        {"symbol": "INFY", "quoteType": "EQUITY"},
        {"symbol": "TCS.NS", "quoteType": "EQUITY"},
    ]}

    assert yahoo.bse_symbols(payload) == ["INFY"]
