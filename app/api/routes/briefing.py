"""The brief, the drill-down, and the acknowledgement that moves an anchor."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db.base import get_db
from app.db.models import User
from app.services import briefing, watchlist
from app.services.briefing import UnknownSnapshot
from app.sources.replay import NoMarketData, UnknownSymbol

router = APIRouter(prefix="/api", tags=["brief"])


class AckRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    snapshot_id: str = Field(min_length=1, max_length=32)


@router.get("/brief")
def get_brief(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    try:
        return briefing.build_brief(db, user)
    except NoMarketData as exc:
        raise HTTPException(status_code=503, detail=f"market data unavailable: {exc}") from exc


@router.post("/brief/ack")
def acknowledge(
    body: AckRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    try:
        return briefing.acknowledge(db, user, body.symbol.upper(), body.snapshot_id)
    except UnknownSnapshot as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/symbols/{symbol}/detail")
def symbol_detail(
    symbol: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    code = symbol.upper()
    if code not in watchlist.watched_symbols(db, user.id):
        raise HTTPException(status_code=404, detail=f"{code} is not on your watchlist")
    try:
        return briefing.symbol_detail(db, user, code)
    except (UnknownSymbol, NoMarketData) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
