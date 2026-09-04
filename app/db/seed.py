"""Load the deterministic fixture into the database.

Seeding is idempotent: a database that already holds symbols is left alone
unless `replace` is asked for, so restarting the app cannot double-insert bars
or resurrect a symbol the user removed from a watchlist.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import FIXTURE_PATH
from app.db.models import (
    CorporateEventRow,
    DailyBar,
    IndexBar,
    IndexMeta,
    QuoteRow,
    Symbol,
    TradingDay,
)

MARKET_CLOSE = time(15, 30)

MARKET_DATA_TABLES = (
    QuoteRow,
    CorporateEventRow,
    DailyBar,
    IndexBar,
    IndexMeta,
    TradingDay,
    Symbol,
)


def load_fixture(path: Path = FIXTURE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_seeded(db: Session) -> bool:
    return db.scalar(select(func.count()).select_from(Symbol)) > 0


def seed(db: Session, fixture: dict | None = None, *, replace: bool = False) -> dict[str, int]:
    if is_seeded(db) and not replace:
        return {"skipped": 1}

    market = fixture if fixture is not None else load_fixture()

    if replace:
        # Ordered child-first so foreign keys hold while the reference data is
        # swapped. User rows are untouched: a reseed refreshes the market, not
        # anyone's watchlist.
        for table in MARKET_DATA_TABLES:
            db.execute(delete(table))
        db.flush()

    sessions = [date.fromisoformat(d) for d in market["sessions"]]
    db.add_all(
        TradingDay(day=day, close_at=datetime.combine(day, MARKET_CLOSE))
        for day in sessions
    )

    sector_names = market["sector_names"]
    market_code = market["market_index"]
    for code in market["indices"]:
        db.add(
            IndexMeta(
                index_code=code,
                name=sector_names.get(code, "Market"),
                is_market=code == market_code,
            )
        )

    for code, rows in market["indices"].items():
        db.add_all(
            IndexBar(index_code=code, day=date.fromisoformat(r["date"]), close=r["close"])
            for r in rows
        )

    for symbol, meta in market["symbols"].items():
        db.add(
            Symbol(
                symbol=symbol,
                name=meta["name"],
                sector_index=meta["sector_index"],
                listed_on=date.fromisoformat(meta["listed_on"]),
            )
        )

    # Parent rows must reach the database before the bars, quotes and events
    # that reference them. The ORM only dependency-orders mappers joined by a
    # relationship(), and these models are wired with plain foreign keys, so
    # without this flush the children can be inserted first.
    db.flush()

    bars = 0
    for symbol, rows in market["prices"].items():
        db.add_all(
            DailyBar(
                symbol=symbol,
                day=date.fromisoformat(r["date"]),
                close=r["close"],
                volume=r["volume"],
            )
            for r in rows
        )
        bars += len(rows)

        last, previous = rows[-1], rows[-2]
        db.add(
            QuoteRow(
                symbol=symbol,
                price=last["close"],
                event_time=datetime.combine(date.fromisoformat(last["date"]), MARKET_CLOSE),
                ingested_at=utcnow(),
                source="replay",
                # Replayed daily bars are settled closing prices, not a live
                # feed. Saying CLOSED rather than LIVE keeps the freshness label
                # true; both are trusted, so inference is unaffected.
                freshness="CLOSED",
                prev_close=previous["close"],
            )
        )

    for event in market["events"]:
        db.add(
            CorporateEventRow(
                symbol=event["symbol"],
                occurred_on=date.fromisoformat(event["on_date"]),
                kind=event["kind"],
                value=event["value"],
                detail=event["detail"],
            )
        )

    db.commit()
    return {
        "symbols": len(market["symbols"]),
        "sessions": len(sessions),
        "bars": bars,
        "events": len(market["events"]),
    }


def main() -> None:
    from app.db.base import Base, SessionLocal, engine

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        print(seed(db, replace=True))


if __name__ == "__main__":
    main()
