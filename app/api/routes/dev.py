"""Demo controls.

Mounted only when RECKON_DEV_ENDPOINTS is on, and inert until a simulation is
set: with no state stored, every other route behaves exactly as it does without
this module present.

These exist because the fixture's designed scenarios all happened on sessions
that are no longer the latest, and a demo that cannot reach them can only show
the app describing an ordinary day.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db.base import get_db
from app.db.models import User
from app.services import simulation
from app.services.simulation import NoSuchSession, UnknownScenario

router = APIRouter(prefix="/api/dev", tags=["dev"])

MODES = {
    "stale": "STALE",
    "disputed": "DISPUTED",
    "unavailable": "UNAVAILABLE",
}


class SimulateRequest(BaseModel):
    scenario: str | None = Field(default=None, max_length=64)
    on_date: date | None = None
    anchor_sessions_ago: int | None = Field(default=None, ge=0, le=60)
    freshness: dict[str, str] | None = None


@router.get("/scenarios")
def list_scenarios(db: Session = Depends(get_db)) -> dict:
    return {
        "scenarios": [
            {"date": day, "label": label} for day, label in sorted(simulation.scenarios().items())
        ],
        "modes": sorted(MODES),
        "usage": (
            "POST /api/dev/simulate with {\"scenario\": \"market-wide\", "
            "\"anchor_sessions_ago\": 1}, or ?mode=stale to degrade quotes. "
            "DELETE /api/dev/simulate to reset."
        ),
    }


@router.get("/simulate")
def current(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    return simulation.describe(db, simulation.get(db, user.id))


@router.post("/simulate")
def simulate(
    body: SimulateRequest | None = None,
    mode: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    request = body or SimulateRequest()
    freshness = dict(request.freshness or {})

    if mode is not None:
        if mode not in MODES:
            raise HTTPException(
                status_code=400, detail=f"unknown mode {mode!r}; use one of {sorted(MODES)}"
            )
        # A bare ?mode= degrades the whole watchlist, which is the quickest way
        # to show the app going quiet as its data stops being trustworthy.
        freshness[simulation.ALL_SYMBOLS] = MODES[mode]

    if request.scenario and request.on_date:
        raise HTTPException(status_code=400, detail="give a scenario or a date, not both")

    try:
        state = simulation.set_simulation(
            db,
            user.id,
            scenario=request.scenario,
            on_date=request.on_date,
            anchor_sessions_ago=request.anchor_sessions_ago,
            freshness=freshness,
        )
    except UnknownScenario as exc:
        raise HTTPException(status_code=404, detail=f"unknown scenario: {exc}") from exc
    except NoSuchSession as exc:
        raise HTTPException(status_code=404, detail=f"not a trading session: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return simulation.describe(db, state)


@router.delete("/simulate")
def reset(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    cleared = simulation.clear(db, user.id)
    return {"cleared": cleared, **simulation.describe(db, simulation.get(db, user.id))}
