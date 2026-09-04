"""Request-scoped dependencies."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import SESSION_HEADER
from app.db.base import get_db
from app.db.models import User
from app.services.identity import InvalidSession, user_for_token


def current_user(
    db: Session = Depends(get_db),
    x_session_token: str | None = Header(default=None, alias=SESSION_HEADER),
) -> User:
    try:
        return user_for_token(db, x_session_token)
    except InvalidSession as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{exc}. Start one with POST /api/session.",
        ) from exc
