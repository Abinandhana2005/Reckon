"""Assemble a brief, and move the two clocks that decide what it says.

Briefs are computed on read. Nothing here is precomputed or cached, because a
verdict is only meaningful against one particular user's anchor, and storing it
would mean invalidating it every time that anchor moved.

The two clocks are moved in different places on purpose:

  `users.last_open_at` moves here, whenever a brief is read. It is narrative.
  `user_symbol_anchor.anchor_at` moves only in `acknowledge`, and only forward,
  and only to the snapshot the user was actually shown.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.api import copy, serialize
from app.db.models import DailyBar, Symbol, UserSymbolAnchor, VerdictRow
from app.domain.brief import Brief, assemble
from app.domain.classifier import ClassificationRequest, classify
from app.domain.verdicts import Anchor, Classification, Quote
from app.services import simulation as simulation_service
from app.services import watchlist
from app.services.simulation import NO_SIMULATION, Simulation
from app.sources import replay

SPARKLINE_SESSIONS = 60


class UnknownSnapshot(LookupError):
    """Acknowledged a snapshot this user was never shown."""


class BriefAccountingError(RuntimeError):
    """A symbol went missing between classification and presentation.

    The product's central claim is that every watched symbol is accounted for.
    Dropping one silently is the failure mode worth crashing over.
    """


def classify_symbol(
    db: Session,
    user_id: str,
    symbol: str,
    now: datetime,
    simulation: Simulation = NO_SIMULATION,
) -> tuple[Classification, Anchor, Quote]:
    anchor = _anchor_for(db, user_id, symbol, simulation)
    quote = replay.load_quote(
        db, symbol, as_of=simulation.as_of, freshness=simulation.freshness_for(symbol)
    )
    request = ClassificationRequest(
        context=replay.load_context(db, symbol, as_of=simulation.as_of),
        anchor=anchor,
        quote=quote,
        now=now,
        events=replay.load_events(db, symbol),
    )
    return classify(request), anchor, quote


def _anchor_for(
    db: Session, user_id: str, symbol: str, simulation: Simulation
) -> Anchor:
    """The user's stored anchor, or a computed one while a simulation is running.

    A simulated anchor is deliberately not written down. The demo needs a
    starting point for a moment the user was never actually at, and storing it
    would overwrite where they really were.
    """
    if simulation.anchor_sessions_ago is not None:
        at, price = replay.session_close(
            db,
            symbol,
            sessions_ago=simulation.anchor_sessions_ago,
            as_of=simulation.as_of,
        )
        return Anchor(symbol=symbol, at=at, price=price)

    row = watchlist.ensure_anchor(db, user_id, symbol)
    return Anchor(
        symbol=symbol,
        at=row.anchor_at,
        price=row.anchor_price,
        snapshot_id=row.anchor_snapshot_id,
    )


def build_brief(
    db: Session,
    user,
    *,
    now: datetime | None = None,
    simulation: Simulation | None = None,
) -> dict:
    active = simulation if simulation is not None else simulation_service.get(db, user.id)
    moment = now or active.as_of or replay.replay_now(db)
    symbols = watchlist.watched_symbols(db, user.id)

    classifications: list[Classification] = []
    for symbol in symbols:
        item, anchor, quote = classify_symbol(db, user.id, symbol, moment, active)
        _record(db, user.id, item, anchor.at, quote)
        classifications.append(item)

    market_code = replay.market_index(db)
    brief = assemble(
        classifications,
        market_return=_median(c.evidence.market_return for c in classifications),
        sector_returns=_sector_returns(classifications),
    )
    if brief.accounted_for != len(classifications):
        raise BriefAccountingError(
            f"{len(classifications)} classified but {brief.accounted_for} presented"
        )

    previously_opened = user.last_open_at
    sessions_since = replay.sessions_between(db, previously_opened, moment)

    # Read before the clock moves, so the header describes the span the user was
    # away rather than an empty one.
    market_since_last_open = (
        replay.index_return(db, market_code, previously_opened, moment)
        if previously_opened
        else None
    )

    # The narrative clock records real visits. A replayed moment is not one, and
    # stamping it would leave the clock behind the user's actual last look.
    if not active.active:
        user.last_open_at = moment
    db.commit()

    return _payload(
        db,
        brief,
        as_of=moment,
        last_open_at=previously_opened,
        sessions_since_last_open=sessions_since,
        market_index=market_code,
        market_return_since_last_open=market_since_last_open,
        simulation=active,
    )


def acknowledge(db: Session, user, symbol: str, snapshot_id: str) -> dict:
    """Advance an anchor to a snapshot the user was shown, never past it.

    The guard in the UPDATE is the whole race-condition defence. If a newer
    quote arrived between render and tap, this ack names the older snapshot, the
    WHERE clause matches nothing, and the newer change stays unread instead of
    being marked seen by a tap that never saw it. The same guard makes a
    repeated ack a no-op rather than an error.
    """
    row = db.scalar(
        select(VerdictRow).where(
            VerdictRow.user_id == user.id,
            VerdictRow.symbol == symbol,
            VerdictRow.snapshot_id == snapshot_id,
        )
    )
    if row is None:
        raise UnknownSnapshot(f"{symbol} has no snapshot {snapshot_id} for this user")

    watchlist.ensure_anchor(db, user.id, symbol)
    result = db.execute(
        update(UserSymbolAnchor)
        .where(
            UserSymbolAnchor.user_id == user.id,
            UserSymbolAnchor.symbol == symbol,
            UserSymbolAnchor.anchor_at < row.snapshot_at,
        )
        .values(
            anchor_at=row.snapshot_at,
            anchor_price=row.snapshot_price,
            anchor_adj=row.snapshot_adj,
            anchor_snapshot_id=row.snapshot_id,
        )
    )
    db.commit()

    anchor = db.get(UserSymbolAnchor, {"user_id": user.id, "symbol": symbol})
    return {
        "symbol": symbol,
        "advanced": result.rowcount > 0,
        "anchor_at": anchor.anchor_at.isoformat(),
        "anchor_price": anchor.anchor_price,
        "anchor_snapshot_id": anchor.anchor_snapshot_id,
    }


def symbol_detail(
    db: Session,
    user,
    symbol: str,
    *,
    now: datetime | None = None,
    simulation: Simulation | None = None,
) -> dict:
    active = simulation if simulation is not None else simulation_service.get(db, user.id)
    moment = now or active.as_of or replay.replay_now(db)
    item, anchor, quote = classify_symbol(db, user.id, symbol, moment, active)
    _record(db, user.id, item, anchor.at, quote)
    db.commit()

    names = _names(db)
    index_names = replay.index_names(db)
    sector_name = index_names.get(item.evidence.sector_index or "")

    statement = select(DailyBar.day, DailyBar.close).where(DailyBar.symbol == symbol)
    if active.as_of is not None:
        statement = statement.where(DailyBar.day <= active.as_of.date())
    bars = list(
        db.execute(statement.order_by(DailyBar.day.desc()).limit(SPARKLINE_SESSIONS)).all()
    )

    return {
        "card": serialize.card(item, names=names, index_names=index_names),
        "why_not_flagged": copy.why_not_flagged(item, sector_name=sector_name),
        "evidence": serialize.evidence_dict(item.evidence),
        "sector_name": sector_name,
        "anchor": {
            "at": anchor.at.isoformat(),
            "price": anchor.price,
            "snapshot_id": anchor.snapshot_id,
        },
        "sparkline": [
            {"date": day.isoformat(), "close": close} for day, close in reversed(bars)
        ],
        "as_of": moment.isoformat(),
        "simulation": simulation_service.describe(db, active),
    }


def _record(
    db: Session,
    user_id: str,
    item: Classification,
    anchor_at: datetime,
    quote: Quote,
) -> None:
    """Keep what the user was shown, keyed by the snapshot it was shown for."""
    existing = db.scalar(
        select(VerdictRow).where(
            VerdictRow.user_id == user_id,
            VerdictRow.symbol == item.symbol,
            VerdictRow.snapshot_id == item.snapshot_id,
        )
    )
    values = {
        "computed_for_anchor": anchor_at,
        "verdict": item.verdict.value,
        "epistemic": item.epistemic.value,
        "reason": item.reason.value,
        "evidence_json": json.dumps(serialize.evidence_dict(item.evidence)),
        "snapshot_adj": item.evidence.adjustment_factor,
        "computed_at": utcnow(),
    }
    if existing is not None:
        for key, value in values.items():
            setattr(existing, key, value)
        return

    db.add(
        VerdictRow(
            user_id=user_id,
            symbol=item.symbol,
            snapshot_id=item.snapshot_id,
            # Taken from the quote the snapshot id was derived from, so a
            # replayed moment records the price that was on screen then.
            snapshot_at=quote.event_time,
            snapshot_price=quote.price,
            **values,
        )
    )
    db.flush()


def _sector_returns(classifications: list[Classification]) -> dict[str, float | None]:
    """One representative return per sector, from the members' own windows.

    Members can hold different anchors, so there is no single sector window to
    quote. The median of what the members actually saw avoids inventing one.
    """
    grouped: dict[str, list[float]] = {}
    for item in classifications:
        code = item.evidence.sector_index
        if code and item.evidence.sector_return is not None:
            grouped.setdefault(code, []).append(item.evidence.sector_return)
    return {code: statistics.median(values) for code, values in grouped.items()}


def _median(values) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None


def _names(db: Session) -> dict[str, str]:
    return dict(db.execute(select(Symbol.symbol, Symbol.name)).all())


def _payload(
    db: Session,
    brief: Brief,
    *,
    as_of: datetime,
    last_open_at: datetime | None,
    sessions_since_last_open: int,
    market_index: str,
    market_return_since_last_open: float | None,
    simulation: Simulation,
) -> dict:
    names = _names(db)
    index_names = replay.index_names(db)

    def cards(items: list[Classification]) -> list[dict]:
        return [serialize.card(i, names=names, index_names=index_names) for i in items]

    groups = [
        {
            "kind": "SECTOR",
            "sector_index": group.sector_index,
            "sector_name": index_names.get(group.sector_index, group.sector_index),
            "index_return": group.index_return,
            "size": group.size,
            "line": copy.sector_group_line(
                index_names.get(group.sector_index, group.sector_index),
                group.index_return,
                group.size,
            ),
            "symbols": cards(group.members),
        }
        for group in brief.sector_groups
    ]
    if brief.with_market:
        groups.append(
            {
                "kind": "MARKET",
                "sector_index": None,
                "sector_name": index_names.get(market_index, "the market"),
                "index_return": brief.market_return,
                "size": len(brief.with_market),
                "line": copy.market_group_line(brief.market_return, len(brief.with_market)),
                "symbols": cards(brief.with_market),
            }
        )

    counts = brief.counts
    return {
        "as_of": as_of.isoformat(),
        "simulation": simulation_service.describe(db, simulation),
        "last_open_at": last_open_at.isoformat() if last_open_at else None,
        "sessions_since_last_open": sessions_since_last_open,
        "market": {
            "index": market_index,
            "name": index_names.get(market_index, "Market"),
            "return_since_last_open": market_return_since_last_open,
        },
        "counts": counts,
        "accounting_line": copy.accounting_line(counts),
        "needs_you": cards(brief.needs_you),
        "explained": {"count": len(brief.explained), "groups": groups},
        "quiet": {
            "count": len(brief.quiet),
            "line": copy.quiet_line(len(brief.quiet)),
            "symbols": cards(brief.quiet),
        },
        "cant_say": {
            "count": len(brief.cant_say),
            "line": copy.cant_say_line(len(brief.cant_say)),
            "symbols": cards(brief.cant_say),
        },
    }
