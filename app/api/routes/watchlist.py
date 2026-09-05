"""Manage: search the catalogue, add, remove."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, optional_user
from app.config import DEFAULT_ANCHOR_SESSIONS_AGO, SOURCE_LIVE, SOURCE_REPLAY
from app.db.base import get_db
from app.db.models import User
from app.services import watchlist
from app.sources import live
from app.sources.replay import UnknownSymbol
from app.sources.provider import ProviderError, ProviderRateLimited

router = APIRouter(prefix="/api", tags=["watchlist"])


class AddRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    note: str | None = Field(default=None, max_length=500)
    anchor_sessions_ago: int = Field(default=DEFAULT_ANCHOR_SESSIONS_AGO, ge=0, le=60)


@router.get("/symbols")
def search_symbols(
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> dict:
    """The instruments this session can add.

    In live mode this is the exchange's real equity list, searched through the
    provider. In replay mode it is the fixture's issuers. The two are never
    mixed, so nothing can be added that has no data behind it.
    """
    source = user.data_source if user else SOURCE_REPLAY
    if source == SOURCE_LIVE:
        try:
            return {"source": SOURCE_LIVE, "symbols": live.search(q or "")}
        except live.LiveNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ProviderRateLimited as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=f"Live provider: {exc}") from exc

    rows = watchlist.list_symbols(db, q, source=source)
    return {
        "source": source,
        "symbols": [
            {
                "symbol": row.symbol,
                "name": row.name,
                "sector_index": row.sector_index,
                "status": row.status,
            }
            for row in rows
        ],
    }


@router.get("/watchlist")
def get_watchlist(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    items = watchlist.watched(db, user.id, source=user.data_source)
    # The company name travels with the row. Without it the watchlist screen has
    # to fetch the whole catalogue just to label what the user already follows,
    # which in live mode would mean a provider search per page load.
    names = watchlist.names_for(db, [item.symbol for item in items])
    sectors = watchlist.sectors_for(db, [item.symbol for item in items])
    return {
        "count": len(items),
        "source": user.data_source,
        "items": [
            {
                "symbol": i.symbol,
                "name": names.get(i.symbol, i.symbol),
                # The sector the brief compares this stock against, or None when
                # no usable index is held for it. Shown rather than inferred.
                "sector": sectors.get(i.symbol),
                "note": i.note,
                "added_at": i.added_at.isoformat(),
            }
            for i in items
        ],
    }


@router.post("/watchlist", status_code=status.HTTP_201_CREATED)
def add_to_watchlist(
    body: AddRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    try:
        item = watchlist.add_for_user(
            db,
            user,
            body.symbol.upper(),
            note=body.note,
            anchor_sessions_ago=body.anchor_sessions_ago,
        )
    except UnknownSymbol as exc:
        raise HTTPException(status_code=404, detail=f"unknown symbol: {exc}") from exc
    except live.LiveDataUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except live.LiveNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"Live provider: {exc}") from exc
    return {"symbol": item.symbol, "note": item.note, "added_at": item.added_at.isoformat()}


@router.delete("/watchlist/{symbol}")
def remove_from_watchlist(
    symbol: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    removed = watchlist.remove(db, user.id, symbol.upper(), source=user.data_source)
    return {"symbol": symbol.upper(), "removed": removed}
