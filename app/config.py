"""Process configuration, read once from the environment.

SQLite is the default so the app starts with no external service. Postgres is
the production target and is selected purely by DATABASE_URL, which is why
nothing below this module writes dialect-specific SQL.
"""

from __future__ import annotations

import os
import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

FIXTURE_PATH = Path(
    os.environ.get("RECKON_FIXTURE", str(PROJECT_ROOT / "fixtures" / "market.json"))
)


def _normalize_database_url(url: str) -> str:
    """Route a bare postgres URL to the driver this app actually installs.

    requirements.txt pins ``psycopg`` (v3), but SQLAlchemy's default dialect
    for a plain ``postgresql://`` -- and for the ``postgres://`` scheme Neon
    and Heroku both hand out -- is ``psycopg2``, which is not installed. Left
    unnormalized, a deployment configured with the connection string exactly
    as the provider gives it crashes on the first request with
    ``ModuleNotFoundError: No module named 'psycopg2'``.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


DATABASE_URL = _normalize_database_url(
    os.environ.get("DATABASE_URL", f"sqlite:///{(PROJECT_ROOT / 'reckon.db').as_posix()}")
)

SESSION_HEADER = "X-Session-Token"
SESSION_TTL_DAYS = 30

# The demo endpoints under /api/dev exist to drive the fixture's designed
# scenarios. They are inert until a simulation is set, and can be switched off
# entirely for a deployment that should not expose them.
DEV_ENDPOINTS_ENABLED = os.environ.get("RECKON_DEV_ENDPOINTS", "1") not in {"0", "false", "False"}

# Where the built frontend is served from. Absent until the Vite build runs,
# which is why serving it is conditional rather than assumed.
WEB_DIST = Path(os.environ.get("RECKON_WEB_DIST", str(PROJECT_ROOT / "web" / "dist")))

# Which market data a user's watchlist is built from. Stored per user, so a
# demo session and a live one can share a database without seeing each other's
# instruments, sessions or indices.
SOURCE_REPLAY = "replay"
SOURCE_LIVE = "live"

# Live mode is configured entirely by this variable. Absent means live mode is
# simply not offered; nothing else in the app changes. Yahoo is the only
# supported live provider.
LIVE_PROVIDER = os.environ.get("RECKON_LIVE_PROVIDER", "yahoo").strip().lower()

# Enough history for the classifier's 60 rolling windows plus its 70-bar floor.
LIVE_HISTORY_SESSIONS = int(os.environ.get("RECKON_LIVE_HISTORY_SESSIONS", "220"))

# A live quote older than this is not a current price. The existing freshness
# model turns that into CANT_SAY rather than a stale-but-confident answer.
LIVE_QUOTE_STALE_HOURS = float(os.environ.get("RECKON_LIVE_QUOTE_STALE_HOURS", "36"))


def live_enabled() -> bool:
    if LIVE_PROVIDER == "yahoo":
        return importlib.util.find_spec("yfinance") is not None
    return False


def live_provider_name() -> str:
    return LIVE_PROVIDER if LIVE_PROVIDER == "yahoo" else "unknown"


def live_unavailable_reason() -> str | None:
    if live_enabled():
        return None
    if LIVE_PROVIDER == "yahoo":
        return "yfinance is not installed"
    return f"unsupported live provider: {LIVE_PROVIDER}"


# How long a gap between brief reads counts as leaving and coming back.
# "Last checked" means the last time someone opened Reckon, so re-reading the
# brief a minute later must not overwrite the visit being described.
VISIT_IDLE_MINUTES = int(os.environ.get("RECKON_VISIT_IDLE_MINUTES", "30"))

# A newly watched symbol needs a reference point. One session back means the
# first brief a user sees describes the most recent session rather than nothing.
DEFAULT_ANCHOR_SESSIONS_AGO = 1
