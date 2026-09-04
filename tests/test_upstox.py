"""The Upstox adapter, tested without a token, a network or the real API.

The normalisers are pure over decoded JSON, so every payload shape is exercised
directly. The client is driven through httpx's MockTransport, which is the only
place a fake is needed.
"""

from __future__ import annotations

import gzip
import json
from datetime import date

import httpx
import pytest

from app.domain.verdicts import EventKind
from app.sources import upstox
from app.sources.upstox import (
    UpstoxClient,
    UpstoxNotConfigured,
    UpstoxRateLimited,
    UpstoxUnavailable,
    normalize_candles,
    normalize_corporate_actions,
    normalize_instruments,
    normalize_quote,
    search_instruments,
)

INSTRUMENT_ROWS = [
    {
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "trading_symbol": "RELIANCE",
        "name": "Reliance Industries Ltd",
        "instrument_key": "NSE_EQ|INE002A01018",
        "isin": "INE002A01018",
    },
    {
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "trading_symbol": "RELIABLE",
        "name": "Reliable Data Services",
        "instrument_key": "NSE_EQ|INE999A01011",
        "isin": "INE999A01011",
    },
    {
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "trading_symbol": "TCS",
        "name": "Tata Consultancy Services",
        "instrument_key": "NSE_EQ|INE467B01029",
        "isin": "INE467B01029",
    },
    # Not equities: a future and an index must never be watchable.
    {
        "segment": "NSE_FO",
        "instrument_type": "FUT",
        "trading_symbol": "RELIANCE24SEPFUT",
        "instrument_key": "NSE_FO|54321",
    },
    {
        "segment": "NSE_INDEX",
        "instrument_type": "INDEX",
        "trading_symbol": "NIFTY 50",
        "instrument_key": "NSE_INDEX|Nifty 50",
    },
]


def transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def client(handler) -> UpstoxClient:
    return UpstoxClient(token="test-token", transport=transport(handler))


# ---------------------------------------------------------------- instruments


def test_only_cash_equities_are_watchable():
    instruments = normalize_instruments(INSTRUMENT_ROWS)

    assert {i.symbol for i in instruments} == {"RELIANCE", "RELIABLE", "TCS"}


def test_instruments_carry_the_provider_key_and_isin():
    reliance = next(i for i in normalize_instruments(INSTRUMENT_ROWS) if i.symbol == "RELIANCE")

    assert reliance.instrument_key == "NSE_EQ|INE002A01018"
    assert reliance.isin == "INE002A01018"
    assert reliance.name == "Reliance Industries Ltd"


def test_a_duplicated_listing_is_stored_once():
    instruments = normalize_instruments(INSTRUMENT_ROWS + [INSTRUMENT_ROWS[0]])

    assert [i.symbol for i in instruments].count("RELIANCE") == 1


def test_sector_comes_from_the_explicit_map_not_a_guess():
    instruments = {i.symbol: i for i in normalize_instruments(INSTRUMENT_ROWS)}

    assert instruments["TCS"].sector_index == "NSE_INDEX|Nifty IT"
    # Unmapped: no sector at all rather than an invented peer group.
    assert instruments["RELIABLE"].sector_index is None


def test_an_exact_ticker_outranks_a_name_match():
    instruments = normalize_instruments(INSTRUMENT_ROWS)

    results = search_instruments(instruments, "RELIANCE")

    assert results[0].symbol == "RELIANCE"


def test_search_also_matches_company_names():
    instruments = normalize_instruments(INSTRUMENT_ROWS)

    results = search_instruments(instruments, "tata")

    assert [i.symbol for i in results] == ["TCS"]


def test_malformed_instrument_rows_are_skipped_not_fatal():
    assert normalize_instruments([None, 42, {"segment": "NSE_EQ"}]) == []


# -------------------------------------------------------------------- candles


def test_candles_are_returned_oldest_first():
    payload = {
        "data": {
            "candles": [
                ["2026-09-03T00:00:00+05:30", 10, 11, 9, 10.5, 1000, 0],
                ["2026-09-01T00:00:00+05:30", 9, 10, 8, 9.5, 800, 0],
                ["2026-09-02T00:00:00+05:30", 9.5, 10, 9, 9.8, 900, 0],
            ]
        }
    }

    bars = normalize_candles(payload)

    assert [bar.day for bar in bars] == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    assert bars[-1].close == pytest.approx(10.5)
    assert bars[-1].volume == 1000


def test_a_repeated_session_is_kept_once():
    payload = {
        "data": {
            "candles": [
                ["2026-09-01T00:00:00+05:30", 9, 10, 8, 9.5, 800, 0],
                ["2026-09-01T00:00:00+05:30", 9, 10, 8, 9.7, 810, 0],
            ]
        }
    }

    bars = normalize_candles(payload)

    assert len(bars) == 1


def test_a_malformed_candle_is_dropped_rather_than_priced():
    payload = {
        "data": {
            "candles": [
                ["2026-09-01T00:00:00+05:30", 9, 10, 8, 9.5, 800, 0],
                ["not-a-date", 1, 1, 1, 1, 1, 0],
                ["2026-09-02T00:00:00+05:30", 9, 10, 8, None, 800, 0],
                [1, 2],
            ]
        }
    }

    bars = normalize_candles(payload)

    assert [bar.day for bar in bars] == [date(2026, 9, 1)]


def test_an_empty_candle_response_is_no_bars_not_an_error():
    assert normalize_candles({"data": {"candles": []}}) == []
    assert normalize_candles({}) == []


# ---------------------------------------------------------------------- quote


def test_a_quote_is_matched_by_its_instrument_token():
    payload = {
        "data": {
            "NSE_EQ:RELIANCE": {
                "instrument_token": "NSE_EQ|INE002A01018",
                "last_price": 1420.5,
                "last_trade_time": "2026-09-04T15:29:59",
                "ohlc": {"close": 1400.0},
            },
            "NSE_EQ:TCS": {
                "instrument_token": "NSE_EQ|INE467B01029",
                "last_price": 3900.0,
                "last_trade_time": "2026-09-04T15:29:59",
            },
        }
    }

    quote = normalize_quote(payload, "NSE_EQ|INE002A01018")

    assert quote.price == pytest.approx(1420.5)
    assert quote.prev_close == pytest.approx(1400.0)


def test_a_single_entry_response_is_used_even_when_the_key_is_a_display_name():
    payload = {
        "data": {
            "NSE_EQ:RELIANCE": {
                "last_price": 1420.5,
                "last_trade_time": "2026-09-04T15:29:59",
            }
        }
    }

    assert normalize_quote(payload, "NSE_EQ|INE002A01018").price == pytest.approx(1420.5)


def test_a_quote_without_a_price_is_no_quote():
    payload = {"data": {"NSE_EQ:RELIANCE": {"last_trade_time": "2026-09-04T15:29:59"}}}

    assert normalize_quote(payload, "NSE_EQ|INE002A01018") is None


def test_a_quote_without_a_timestamp_reports_an_unknown_age():
    """An age that cannot be established must not be treated as current."""
    payload = {"data": {"NSE_EQ:RELIANCE": {"last_price": 1420.5}}}

    quote = normalize_quote(payload, "NSE_EQ|INE002A01018")

    assert quote.price == pytest.approx(1420.5)
    assert quote.event_time is None


# ----------------------------------------------------------- corporate actions


def test_corporate_actions_are_normalised_into_the_existing_kinds():
    payload = {
        "data": [
            {"ex_date": "2026-08-12", "purpose": "Stock split 1:2", "ratio": 2.0},
            {"ex_date": "2026-08-20", "purpose": "Final dividend", "dividend": 14.0},
            {"ex_date": "2026-07-31", "purpose": "Board meeting for results"},
            {"ex_date": "2026-09-01", "purpose": "Bonus issue", "ratio": 1.0},
        ]
    }

    events = normalize_corporate_actions(payload, "RELIANCE")

    assert [event.kind for event in events] == [
        EventKind.RESULTS,
        EventKind.SPLIT,
        EventKind.EX_DIVIDEND,
        EventKind.BONUS,
    ]
    assert events[1].value == pytest.approx(2.0)


def test_an_adjusting_action_without_a_value_is_dropped():
    """An action the adjustment cannot apply would misprice the anchor."""
    payload = {"data": [{"ex_date": "2026-08-12", "purpose": "Stock split"}]}

    assert normalize_corporate_actions(payload, "RELIANCE") == []


def test_an_undated_action_is_dropped():
    payload = {"data": [{"purpose": "Stock split 1:2", "ratio": 2.0}]}

    assert normalize_corporate_actions(payload, "RELIANCE") == []


def test_an_unrecognised_action_kind_is_ignored():
    payload = {"data": [{"ex_date": "2026-08-12", "purpose": "Postal ballot"}]}

    assert normalize_corporate_actions(payload, "RELIANCE") == []


# --------------------------------------------------------------------- client


def test_rate_limiting_is_reported_as_its_own_failure():
    api = client(lambda request: httpx.Response(429, json={"message": "slow down"}))

    with pytest.raises(UpstoxRateLimited):
        api.daily_candles("NSE_EQ|X", date(2026, 1, 1), date(2026, 9, 1))


def test_a_rejected_token_is_reported_as_a_configuration_problem():
    api = client(lambda request: httpx.Response(401, json={"message": "unauthorised"}))

    with pytest.raises(UpstoxNotConfigured):
        api.daily_candles("NSE_EQ|X", date(2026, 1, 1), date(2026, 9, 1))


def test_a_server_failure_is_reported_as_unavailable():
    api = client(lambda request: httpx.Response(503, text="upstream down"))

    with pytest.raises(UpstoxUnavailable):
        api.daily_candles("NSE_EQ|X", date(2026, 1, 1), date(2026, 9, 1))


def test_a_non_json_response_is_reported_rather_than_parsed():
    api = client(lambda request: httpx.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(UpstoxUnavailable):
        api.daily_candles("NSE_EQ|X", date(2026, 1, 1), date(2026, 9, 1))


def test_a_transport_failure_is_reported_as_unavailable():
    def explode(request):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(UpstoxUnavailable):
        client(explode).daily_candles("NSE_EQ|X", date(2026, 1, 1), date(2026, 9, 1))


def test_the_gzipped_instrument_list_is_decoded():
    body = gzip.compress(json.dumps(INSTRUMENT_ROWS).encode())
    api = client(lambda request: httpx.Response(200, content=body))

    assert {i.symbol for i in api.instruments()} == {"RELIANCE", "RELIABLE", "TCS"}


def test_the_instrument_list_is_fetched_once_per_process():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=INSTRUMENT_ROWS)

    api = client(handler)
    api.instruments()
    api.instruments()

    assert calls["n"] == 1


def test_a_missing_corporate_actions_endpoint_means_no_events_not_a_crash():
    """The analytics token may not expose this; that is not an error state."""
    api = client(lambda request: httpx.Response(404, json={"message": "not found"}))

    assert api.corporate_actions("NSE_EQ|X", "RELIANCE") == []


def test_the_token_is_sent_as_a_bearer_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": {"candles": []}})

    client(handler).daily_candles("NSE_EQ|X", date(2026, 1, 1), date(2026, 9, 1))

    assert seen["auth"] == "Bearer test-token"


def test_live_mode_without_a_token_refuses_to_construct_a_client():
    with pytest.raises(UpstoxNotConfigured):
        UpstoxClient(token=None)


def test_the_market_index_is_nifty_fifty():
    assert upstox.NIFTY_50 == "NSE_INDEX|Nifty 50"
    assert upstox.sector_for("HDFCBANK") == "NSE_INDEX|Nifty Bank"
    assert upstox.sector_for("NOTLISTED") is None
