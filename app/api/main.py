"""The application. One process, one database, no background workers.

Replay mode makes no outbound calls and needs no scheduler, so startup is the
whole lifecycle: ensure the schema, seed the fixture if the database is empty,
serve. Seeding is idempotent, which is what makes `uvicorn app.api.main:app` a
complete instruction rather than the second half of one.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.db.base import Base, SessionLocal, engine, get_db
from app.db.seed import is_seeded, seed
from app.api.routes import briefing, session, watchlist


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if not is_seeded(db):
            seed(db)
    yield


app = FastAPI(
    title="Reckon",
    version="0.1.0",
    summary="A watchlist that accounts for every symbol, including the quiet ones.",
    lifespan=lifespan,
)

app.include_router(session.router)
app.include_router(watchlist.router)
app.include_router(briefing.router)


@app.get("/health", tags=["ops"])
def health(db: Session = Depends(get_db)) -> dict:
    # Takes the request-scoped session like every other route. Opening its own
    # would report on a different database than the one actually serving reads.
    return {
        "status": "ok",
        "seeded": is_seeded(db),
        "database": DATABASE_URL.split("://", 1)[0],
    }
