"""Live Mode: switching a session onto real data, and refreshing it.

Mode is a property of the session's user, not a global switch, so a demo
session and a live one can run side by side against one database without ever
reading each other's instruments.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.config import SOURCE_LIVE, SOURCE_REPLAY, live_enabled
from app.db.base import get_db
from app.db.models import User
from app.services import watchlist
from app.sources import live
from app.sources.upstox import UpstoxError, UpstoxRateLimited

router = APIRouter(prefix="/api", tags=["live"])

MODES = {SOURCE_REPLAY, SOURCE_LIVE}


class ModeRequest(BaseModel):
    mode: str = Field(pattern="^(replay|live)$")


def _describe(user: User) -> dict:
    return {
        "mode": user.data_source,
        "live_available": live_enabled(),
        "live_reason": None if live_enabled() else "UPSTOX_ACCESS_TOKEN is not set",
    }


@router.get("/mode")
def current_mode(user: User = Depends(current_user)) -> dict:
    return _describe(user)


@router.post("/mode")
def set_mode(
    body: ModeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if body.mode == SOURCE_LIVE and not live_enabled():
        raise HTTPException(
            status_code=503,
            detail="Live mode needs UPSTOX_ACCESS_TOKEN to be set on the server.",
        )
    user.data_source = body.mode
    db.commit()
    return _describe(user)


@router.post("/live/refresh")
def refresh(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Re-poll quotes for the watchlist. One failure does not stop the rest."""
    if user.data_source != SOURCE_LIVE:
        raise HTTPException(status_code=400, detail="this session is not in live mode")
    try:
        outcome = live.refresh_watchlist(db, watchlist.watched_symbols(db, user.id))
    except live.LiveNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UpstoxRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except UpstoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"refreshed": outcome}
