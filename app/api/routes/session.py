"""Guest sessions. No credentials are collected, so there is nothing to verify."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.config import SESSION_HEADER
from app.db.base import get_db
from app.db.models import User
from app.services.identity import start_guest_session

router = APIRouter(prefix="/api", tags=["session"])


@router.post("/session", status_code=201)
def create_session(db: Session = Depends(get_db)) -> dict:
    user, token = start_guest_session(db)
    return {"user_id": user.id, "session_token": token, "header": SESSION_HEADER}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return {
        "user_id": user.id,
        "last_open_at": user.last_open_at.isoformat() if user.last_open_at else None,
    }
