"""Startup upgrades the un-stamped local database without losing user state."""

from __future__ import annotations

from datetime import datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.db.migrate import PROJECT_ROOT, ensure_schema
from app.db.models import User, WatchlistItem


def _alembic_config(url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_legacy_unstamped_database_is_upgraded_in_place(tmp_path):
    path = tmp_path / "legacy.db"
    url = f"sqlite:///{path.as_posix()}"
    engine = create_engine(url)

    # This is the exact state of the reported database: simulation_state was
    # present, but the live-mode columns were not and Alembic had no marker.
    command.upgrade(_alembic_config(url), "a8c5a409897f")
    # Use the legacy columns here: importing today's ORM model is precisely
    # what exposes the missing-column error this test is meant to reproduce.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users (id, created_at) VALUES ('legacy-user', '2026-09-04')"
        )
        connection.exec_driver_sql(
            "INSERT INTO symbols (symbol, name, sector_index, isin, status, listed_on) "
            "VALUES ('MRDB', 'Meridian Bank', 'BANKIDX', NULL, 'ACTIVE', '2026-01-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO watchlist_items (user_id, symbol, added_at, note, sort_order) "
            "VALUES ('legacy-user', 'MRDB', '2026-09-04', 'keep this', 0)"
        )

    # Simulate the old create_all-only process by dropping version bookkeeping.
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE alembic_version")

    ensure_schema(engine)

    inspector = inspect(engine)
    assert "data_source" in {c["name"] for c in inspector.get_columns("users")}
    assert "source" in {c["name"] for c in inspector.get_columns("symbols")}
    assert "source" in {c["name"] for c in inspector.get_columns("trading_days")}
    with Session(engine) as db:
        assert db.get(User, "legacy-user").data_source == "replay"
        assert db.scalar(select(WatchlistItem.symbol)) == "MRDB"
