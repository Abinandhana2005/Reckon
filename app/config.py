"""Process configuration, read once from the environment.

SQLite is the default so the app starts with no external service. Postgres is
the production target and is selected purely by DATABASE_URL, which is why
nothing below this module writes dialect-specific SQL.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

FIXTURE_PATH = Path(
    os.environ.get("RECKON_FIXTURE", str(PROJECT_ROOT / "fixtures" / "market.json"))
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"sqlite:///{(PROJECT_ROOT / 'reckon.db').as_posix()}"
)

SESSION_HEADER = "X-Session-Token"
SESSION_TTL_DAYS = 30

# A newly watched symbol needs a reference point. One session back means the
# first brief a user sees describes the most recent session rather than nothing.
DEFAULT_ANCHOR_SESSIONS_AGO = 1
