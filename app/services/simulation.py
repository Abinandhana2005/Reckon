"""Demo overrides: replay a past session, and degrade a quote on demand.

The fixture contains four designed scenarios, and every one of them happened on
a session that is no longer the latest. Without a way to move the moment being
replayed, the API can only ever describe the last session, and the cases the
product was built to demonstrate stay unreachable through it.

Two rules keep this from leaking into normal behaviour:

  Nothing here changes how a verdict is reached. A simulation only decides
  which prices the classifier is handed, and the classifier is the same pure
  function either way.

  Nothing here writes to a user's real state. Anchors are computed for the
  simulated moment and passed in, never stored, so clearing a simulation
  restores exactly what was there before it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import DEFAULT_ANCHOR_SESSIONS_AGO, FIXTURE_PATH
from app.db.models import SimulationState, TradingDay
from app.domain.verdicts import Freshness

ALL_SYMBOLS = "*"

# Degrading a quote must suppress inference rather than soften it, so only the
# states the classifier treats as untrusted are offerable.
SIMULATABLE_FRESHNESS = {
    Freshness.STALE,
    Freshness.DISPUTED,
    Freshness.UNAVAILABLE,
}


class UnknownScenario(LookupError):
    """Asked for a scenario the fixture does not define."""


class NoSuchSession(LookupError):
    """Asked to replay a date that is not a trading session."""


@dataclass(frozen=True)
class Simulation:
    """What a brief should pretend about the world. Empty means: nothing."""

    as_of: datetime | None = None
    anchor_sessions_ago: int | None = None
    freshness: dict[str, str] = field(default_factory=dict)
    scenario: str | None = None

    @property
    def active(self) -> bool:
        return self.as_of is not None or bool(self.freshness)

    def freshness_for(self, symbol: str) -> Freshness | None:
        override = self.freshness.get(symbol) or self.freshness.get(ALL_SYMBOLS)
        return Freshness(override) if override else None


NO_SIMULATION = Simulation()


def scenarios() -> dict[str, str]:
    """The fixture's designed moments, as {iso date: label}.

    Read from the fixture rather than the database because these are demo
    metadata, not market data, and the production schema should not carry them.
    """
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return dict(fixture["meta"]["scenarios"])


def resolve_scenario(label: str) -> date:
    for day, name in scenarios().items():
        if label.lower() in name.lower():
            return date.fromisoformat(day)
    raise UnknownScenario(label)


def session_close_at(db: Session, day: date) -> datetime:
    close_at = db.scalar(select(TradingDay.close_at).where(TradingDay.day == day))
    if close_at is None:
        raise NoSuchSession(str(day))
    return close_at


def get(db: Session, user_id: str) -> Simulation:
    row = db.get(SimulationState, user_id)
    if row is None:
        return NO_SIMULATION
    return Simulation(
        as_of=row.as_of,
        anchor_sessions_ago=row.anchor_sessions_ago,
        freshness=json.loads(row.freshness_json),
        scenario=row.scenario,
    )


def set_simulation(
    db: Session,
    user_id: str,
    *,
    scenario: str | None = None,
    on_date: date | None = None,
    anchor_sessions_ago: int | None = None,
    freshness: dict[str, str] | None = None,
) -> Simulation:
    day = resolve_scenario(scenario) if scenario else on_date
    as_of = session_close_at(db, day) if day else None

    # A moment without an anchor is not a scenario: the comparison needs a
    # starting point, and one session back is what "since you last looked" means
    # for a judge stepping onto a scenario day.
    sessions_ago = anchor_sessions_ago
    if as_of is not None and sessions_ago is None:
        sessions_ago = DEFAULT_ANCHOR_SESSIONS_AGO

    row = db.get(SimulationState, user_id)
    if row is None:
        row = SimulationState(user_id=user_id)
        db.add(row)

    row.as_of = as_of
    row.anchor_sessions_ago = sessions_ago
    row.freshness_json = json.dumps(_validated(freshness or {}))
    row.scenario = scenario
    row.updated_at = utcnow()
    db.commit()

    return get(db, user_id)


def clear(db: Session, user_id: str) -> bool:
    """Remove the override. Clearing when nothing is set is not an error."""
    row = db.get(SimulationState, user_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def describe(db: Session, simulation: Simulation) -> dict:
    return {
        "active": simulation.active,
        "scenario": simulation.scenario,
        "as_of": simulation.as_of.isoformat() if simulation.as_of else None,
        "anchor_sessions_ago": simulation.anchor_sessions_ago,
        "freshness": simulation.freshness,
    }


def _validated(freshness: dict[str, str]) -> dict[str, str]:
    allowed = {f.value for f in SIMULATABLE_FRESHNESS}
    for symbol, state in freshness.items():
        if state not in allowed:
            raise ValueError(
                f"{state!r} is not a simulatable freshness; use one of {sorted(allowed)}"
            )
    return {symbol.upper() if symbol != ALL_SYMBOLS else symbol: s for symbol, s in freshness.items()}
