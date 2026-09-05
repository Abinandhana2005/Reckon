"""Assemble a brief, and move the two clocks that decide what it says.

Briefs are computed on read. Nothing here is precomputed or cached, because a
verdict is only meaningful against one particular user's anchor, and storing it
would mean invalidating it every time that anchor moved.

The clocks are moved in different places on purpose:

  The visit clock (`app.services.visits`) moves here, after a brief has been
  assembled successfully, and only when the reader has genuinely been away. It
  is narrative: "you last checked on Tuesday".
  `user_symbol_anchor.anchor_at` moves only in `acknowledge`, and only forward,
  and only to the snapshot the user was actually shown.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import SOURCE_LIVE, live_provider_name
from app.api import copy, serialize
from app.db.models import DailyBar, QuoteRow, Symbol, UserSymbolAnchor, VerdictRow
from app.domain.brief import Brief, assemble
from app.domain.classifier import ClassificationRequest, classify
from app.domain.verdicts import (
    Anchor,
    Classification,
    Epistemic,
    Evidence,
    Quote,
    Reason,
    Verdict,
)
from app.services import simulation as simulation_service
from app.services import visits
from app.services import watchlist
from app.services.simulation import NO_SIMULATION, Simulation
from app.sources import live, nse, replay

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
    source = replay.load_symbol(db, symbol).source
    quote_as_of = simulation.as_of
    if source == SOURCE_LIVE and quote_as_of is None:
        # The authoritative Live brief ends at the latest completed session;
        # never classify it with an in-progress quote from the wall clock.
        quote_as_of = now
    quote = replay.load_quote(
        db, symbol, as_of=quote_as_of, freshness=simulation.freshness_for(symbol)
    )
    # A live quote is fetched on demand, so it can age between fetches. Judging
    # that here lets an aging price stop supporting inference on its own,
    # through the freshness model that already exists, with no background job.
    if source == SOURCE_LIVE and simulation.freshness_for(symbol) is None:
        quote = live.freshen(quote, now)
    request = ClassificationRequest(
        # The context is truncated to the same moment as the quote. Leaving it
        # open-ended let a Live brief price the stock at the last completed
        # close while measuring the market and sector against a bar for a
        # session still in progress.
        context=replay.load_context(db, symbol, as_of=quote_as_of),
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


def _sync_live_data(db: Session, user_id: str, source: str) -> None:
    """Bring stored live data up to date before resolving the latest completed session.

    Reckon fetches on demand rather than on a schedule, so without this call
    "the latest completed session" is bounded by whenever someone last pressed
    Refresh or added a symbol -- not by whenever this reader actually looked.
    Calling it here, before that resolution, is what makes opening the brief
    itself the check "what changed since I last looked".

    A fetch failure must not break the read: `refresh_watchlist` already leaves
    a symbol's stored data untouched and reports the failure per symbol rather
    than raising, and an in-progress session's bar is dropped before it ever
    reaches storage (`live.completed_sessions`), so this call can neither break
    the brief nor let a still-trading session masquerade as a completed one.
    """
    live.refresh_watchlist(db, watchlist.watched_symbols(db, user_id, source=source))


def build_brief(
    db: Session,
    user,
    *,
    now: datetime | None = None,
    simulation: Simulation | None = None,
) -> dict:
    active = simulation if simulation is not None else simulation_service.get(db, user.id)
    source = user.data_source
    visit_at = now or utcnow()
    if active.active:
        moment = active.as_of or replay.replay_now(db, source)
    elif source == SOURCE_LIVE:
        _sync_live_data(db, user.id, source)
        moment = replay.latest_completed_session(db, visit_at, source)
    else:
        # Sample data has a synthetic clock so its deterministic scenarios do
        # not drift with the wall clock. It is not presented as live timing.
        moment = now or replay.replay_now(db, source)
    symbols = watchlist.watched_symbols(db, user.id, source=source)

    classifications: list[Classification] = []
    for symbol in symbols:
        try:
            item, anchor, quote = classify_symbol(db, user.id, symbol, moment, active)
        except (replay.NoMarketData, replay.UnknownSymbol, live.LiveDataUnavailable) as exc:
            # A provider that failed for one instrument must not cost the reader
            # the other fifteen. The symbol stays in the brief and stays
            # accounted for; it simply carries no claim.
            classifications.append(_no_data(symbol, exc))
            continue
        _record(db, user.id, item, anchor.at, quote)
        classifications.append(item)

    market_code = replay.market_index(db, source)
    brief = assemble(
        classifications,
        market_return=_median(c.evidence.market_return for c in classifications),
        sector_returns=_sector_returns(classifications),
    )
    if brief.accounted_for != len(classifications):
        raise BriefAccountingError(
            f"{len(classifications)} classified but {brief.accounted_for} presented"
        )

    # Read before the visit is recorded, and read for this source only: a
    # Sample visit stamped in the fixture's calendar says nothing about how long
    # the reader has been away from the live market.
    previously_opened = visits.previous(db, user.id, source)
    sessions_since = replay.sessions_between(db, previously_opened, moment, source)

    # Read before the clock moves, so the header describes the span the user was
    # away rather than an empty one.
    market_since_last_open = (
        replay.index_return(db, market_code, previously_opened, moment)
        if previously_opened
        else None
    )

    # Build the response fully before moving the narrative clock. A failed
    # brief read must leave the previous visit available for the next attempt.
    payload = _payload(
        db,
        brief,
        as_of=moment,
        visit_at=visit_at,
        last_open_at=previously_opened,
        sessions_since_last_open=sessions_since,
        market_index=market_code,
        market_return_since_last_open=market_since_last_open,
        simulation=active,
        source=source,
        symbols=symbols,
    )
    # The narrative clock records real visits. A replayed moment is not one, and
    # stamping it would leave the clock behind the user's actual last look.
    if not active.active:
        visits.record(db, user, source, visit_at if source == SOURCE_LIVE else moment)
    db.commit()
    return payload


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
    if active.active:
        moment = now or active.as_of or replay.replay_now(db, user.data_source)
    elif user.data_source == SOURCE_LIVE:
        _sync_live_data(db, user.id, user.data_source)
        moment = replay.latest_completed_session(db, now or utcnow(), SOURCE_LIVE)
    else:
        moment = now or replay.replay_now(db, user.data_source)
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
        "decision_trace": copy.decision_trace(item, sector_name=sector_name),
        "as_of": moment.isoformat(),
        "data_source": user.data_source,
        "provenance": _provenance(
            db,
            [symbol],
            source=user.data_source,
            latest_completed=moment,
            now=now or utcnow(),
        ),
        # Reading the detail is not a visit, so this reports the stored one and
        # does not move it.
        "last_open_at": _iso(visits.previous(db, user.id, user.data_source)),
        "simulation": simulation_service.describe(db, active),
    }


def _no_data(symbol: str, reason: Exception) -> Classification:
    """A symbol with no usable data at all.

    The classifier cannot run without prices, so this is assembled here rather
    than by it -- but it lands in the same taxonomy, in the slot that exists for
    exactly this: we cannot say. Nothing is inferred and no number is invented.
    """
    return Classification(
        symbol=symbol,
        verdict=Verdict.CANT_SAY,
        epistemic=Epistemic.UNKNOWN,
        reason=Reason.UNTRUSTED_QUOTE,
        evidence=Evidence(sessions_away=0, quote_source=f"unavailable: {reason}"),
        snapshot_id=hashlib.sha256(f"nodata|{symbol}".encode()).hexdigest()[:16],
    )


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
    visit_at: datetime,
    last_open_at: datetime | None,
    sessions_since_last_open: int,
    market_index: str,
    market_return_since_last_open: float | None,
    simulation: Simulation,
    source: str,
    symbols: list[str],
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
    start_here = brief.needs_you[0] if brief.needs_you else None
    return {
        "as_of": as_of.isoformat(),
        "data_source": source,
        "provenance": _provenance(
            db, symbols, source=source, latest_completed=as_of, now=visit_at
        ),
        "simulation": simulation_service.describe(db, simulation),
        "last_open_at": last_open_at.isoformat() if last_open_at else None,
        "sessions_since_last_open": sessions_since_last_open,
        "market": {
            "index": market_index,
            "name": index_names.get(market_index, "Market"),
            "return_since_last_open": market_return_since_last_open,
            # The market's move across the window the verdicts were actually
            # computed against. It differs from the line above whenever the
            # anchor and the last visit are not the same moment, which is the
            # normal case: acknowledgements move one and opening moves the other.
            "return_over_window": brief.market_return,
        },
        "counts": counts,
        "accounting_line": copy.accounting_line(counts),
        # The one item worth opening first. Taken from the top of `needs_you`,
        # which is already ordered by the size of the move, rather than from a
        # second scoring system that would have to be justified separately.
        "start_here": (
            {
                "card": serialize.card(start_here, names=names, index_names=index_names),
                "line": copy.start_here_line(start_here),
            }
            if start_here is not None
            else None
        ),
        "silence_report": copy.silence_report(counts),
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
            # Grouped by why, so "couldn't evaluate" is a set of stated reasons
            # rather than a shrug.
            "reasons": _uncertainty_reasons(brief.cant_say),
        },
    }


def _uncertainty_reasons(items: list[Classification]) -> list[dict]:
    grouped: dict[Reason, list[str]] = {}
    for item in items:
        grouped.setdefault(item.reason, []).append(item.symbol)
    return [
        {
            "reason": reason.value,
            "label": copy.uncertainty_label(reason),
            "detail": copy.uncertainty_detail(reason),
            "symbols": sorted(symbols),
        }
        for reason, symbols in sorted(grouped.items(), key=lambda pair: pair[0].value)
    ]


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _provenance(
    db: Session,
    symbols: list[str],
    *,
    source: str,
    latest_completed: datetime,
    now: datetime,
) -> dict:
    """Expose timing facts without making Sample data look live.

    `now` is the moment this brief is being built for, passed in rather than
    read from the clock here: a brief assembled for an explicit moment must
    describe the exchange as it stood then, not as it stands during the call.
    """
    if source != SOURCE_LIVE:
        # Sample data has no fetch and no exchange, so those fields stay absent
        # rather than being filled with plausible-looking values. Which session
        # the analysis ran through is still a real fact about it, and the reader
        # needs it to make sense of the numbers.
        return {
            "provider": "Sample fixture",
            "latest_completed_session_at": latest_completed.isoformat(),
        }

    rows = list(
        db.scalars(select(QuoteRow).where(QuoteRow.symbol.in_(symbols)))
    ) if symbols else []
    fetched = [row.ingested_at for row in rows if row.ingested_at is not None]
    # A trusted observation is one Reckon would classify from. A stale or
    # unavailable quote is still stored, and still shown, but it is not what
    # "latest available quote" means.
    observed = [
        row.event_time
        for row in rows
        if row.event_time is not None and row.freshness in {"LIVE", "CLOSED"}
    ]
    freshness = sorted({row.freshness for row in rows})
    return {
        "provider": f"{live_provider_name().replace('_', ' ').title()} Finance",
        "data_fetched_at": _iso(max(fetched) if fetched else None),
        "latest_completed_session_at": latest_completed.isoformat(),
        "latest_available_quote_at": _iso(max(observed) if observed else None),
        "quote_freshness": freshness[0] if len(freshness) == 1 else "MIXED",
        # Regular exchange hours, used for wording only. Which session the
        # analysis ends at comes from the observed calendar, never from this.
        "market_state": nse.market_state(now),
        "stale_symbols": sorted(
            row.symbol for row in rows if row.freshness in {"STALE", "UNAVAILABLE", "DISPUTED"}
        ),
    }
