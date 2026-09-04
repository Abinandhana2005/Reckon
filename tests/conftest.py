from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from app.domain.classifier import ClassificationRequest, SymbolContext
from app.domain.verdicts import Anchor, CorporateEvent, EventKind, Freshness, Quote

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "market.json"
MARKET_CLOSE = time(15, 30)


@pytest.fixture(scope="session")
def market() -> dict:
    return json.loads(FIXTURE.read_text())


def _closes(rows: list[dict]) -> tuple[list[date], list[float]]:
    days = [date.fromisoformat(row["date"]) for row in rows]
    return days, [row["close"] for row in rows]


def build_request(
    market: dict,
    symbol: str,
    sessions_away: int,
    *,
    freshness: Freshness = Freshness.LIVE,
    up_to: int | None = None,
    price_override: float | None = None,
) -> ClassificationRequest:
    """Assemble a request for `symbol` as seen `sessions_away` sessions later.

    `up_to` truncates the series so a scenario day can be made "today".
    """
    rows = market["prices"][symbol]
    if up_to is not None:
        cutoff = market["sessions"][up_to]
        rows = [r for r in rows if r["date"] <= cutoff]

    days, closes = _closes(rows)
    index_rows = {
        code: {r["date"]: r["close"] for r in series}
        for code, series in market["indices"].items()
    }

    market_closes = [index_rows[market["market_index"]][d.isoformat()] for d in days]
    sector_index = market["symbols"][symbol]["sector_index"]
    sector_closes = (
        [index_rows[sector_index][d.isoformat()] for d in days] if sector_index else None
    )

    context = SymbolContext(
        symbol=symbol,
        sessions=days,
        closes=closes,
        market_closes=market_closes,
        sector_index=sector_index,
        sector_closes=sector_closes,
        volume_ratio=None,
    )

    anchor_position = len(days) - 1 - sessions_away
    now = datetime.combine(days[-1], MARKET_CLOSE)
    price = price_override if price_override is not None else closes[-1]

    return ClassificationRequest(
        context=context,
        anchor=Anchor(
            symbol=symbol,
            at=datetime.combine(days[anchor_position], MARKET_CLOSE),
            price=closes[anchor_position],
        ),
        quote=Quote(
            symbol=symbol,
            price=price,
            event_time=now - timedelta(seconds=5),
            freshness=freshness,
            source="replay",
        ),
        now=now,
        events=[
            CorporateEvent(
                symbol=e["symbol"],
                on_date=date.fromisoformat(e["on_date"]),
                kind=EventKind(e["kind"]),
                value=e["value"],
                detail=e["detail"],
            )
            for e in market["events"]
            if e["symbol"] == symbol
        ],
    )


def session_index(market: dict, iso_day: str) -> int:
    return market["sessions"].index(iso_day)


def scenario_day(market: dict, label: str) -> int:
    for day, name in market["meta"]["scenarios"].items():
        if label in name:
            return session_index(market, day)
    raise KeyError(label)
