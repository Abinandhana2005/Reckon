"""Guest identity.

Persistence needs an owner for a watchlist and an anchor, and nothing more.
There is no sign-in: a session token names a guest user, which is the least
identity that still keeps every piece of state on the server.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import SESSION_TTL_DAYS
from app.db.models import SessionToken, User


class InvalidSession(LookupError):
    """The presented token is unknown or has expired."""


def start_guest_session(db: Session, *, now: datetime | None = None) -> tuple[User, str]:
    moment = now or utcnow()
    user = User(created_at=moment)
    db.add(user)
    db.flush()

    token = secrets.token_urlsafe(32)
    db.add(
        SessionToken(
            token=token,
            user_id=user.id,
            created_at=moment,
            expires_at=moment + timedelta(days=SESSION_TTL_DAYS),
        )
    )
    db.commit()
    return user, token


def user_for_token(db: Session, token: str | None, *, now: datetime | None = None) -> User:
    if not token:
        raise InvalidSession("no session token presented")

    moment = now or utcnow()
    row = db.scalar(select(SessionToken).where(SessionToken.token == token))
    if row is None or row.expires_at <= moment:
        raise InvalidSession("session token is unknown or expired")

    user = db.get(User, row.user_id)
    if user is None:
        raise InvalidSession("session token has no user")
    return user
