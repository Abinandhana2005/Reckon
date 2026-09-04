"""The application. One process, one database, no background workers.

Replay mode makes no outbound calls and needs no scheduler, so startup is the
whole lifecycle: ensure the schema, seed the fixture if the database is empty,
serve. Seeding is idempotent, which is what makes `uvicorn app.api.main:app` a
complete instruction rather than the second half of one.

The built frontend is served from this same process. One origin means no CORS
configuration and no second deployable.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.config import DATABASE_URL, DEV_ENDPOINTS_ENABLED, WEB_DIST, live_enabled
from app.db.base import Base, SessionLocal, engine, get_db
from app.db.seed import is_seeded, seed
from app.api.routes import briefing, dev, live, session, watchlist


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if not is_seeded(db):
            seed(db)
    yield


def mount_frontend(application: FastAPI, web_dist: Path) -> bool:
    """Serve the built frontend, if one has been built.

    Registered after the API routes so those keep their paths, and conditional
    because `web/dist` only exists once the frontend build has run. Unknown
    paths fall back to index.html rather than 404, which is what a client-side
    router needs for a deep link to survive a refresh.
    """
    index = web_dist / "index.html"
    if not index.is_file():
        return False

    assets = web_dist / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")

    @application.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = web_dist / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    return True


def create_app(
    *,
    dev_endpoints: bool = DEV_ENDPOINTS_ENABLED,
    web_dist: Path = WEB_DIST,
) -> FastAPI:
    application = FastAPI(
        title="Reckon",
        version="0.1.0",
        summary="A watchlist that accounts for every symbol, including the quiet ones.",
        lifespan=lifespan,
    )

    application.include_router(session.router)
    application.include_router(watchlist.router)
    application.include_router(briefing.router)
    application.include_router(live.router)
    if dev_endpoints:
        application.include_router(dev.router)

    @application.get("/health", tags=["ops"])
    def health(db: Session = Depends(get_db)) -> dict:
        # Takes the request-scoped session like every other route. Opening its
        # own would report on a different database than the one serving reads.
        return {
            "status": "ok",
            "seeded": is_seeded(db),
            "database": DATABASE_URL.split("://", 1)[0],
            "dev_endpoints": dev_endpoints,
            "frontend": (web_dist / "index.html").is_file(),
            "live_available": live_enabled(),
        }

    mount_frontend(application, web_dist)
    return application


app = create_app()
