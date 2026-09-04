"""The migration and the models must describe the same database.

Startup uses create_all for a local run while production upgrades through
Alembic. That is two descriptions of one schema, so something has to keep them
honest: this applies the migrations to an empty database and compares the
result against the mappers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import app.config
from app.db.base import Base
from app.db import models  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    # alembic/env.py reads the URL from app.config at run time.
    monkeypatch.setattr(app.config, "DATABASE_URL", url)

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(config, "head")

    return create_engine(url)


def test_migrations_create_every_mapped_table(migrated):
    tables = set(inspect(migrated).get_table_names()) - {"alembic_version"}

    assert tables == set(Base.metadata.tables)


def test_migrated_columns_match_the_models(migrated):
    inspector = inspect(migrated)

    for name, table in Base.metadata.tables.items():
        migrated_columns = {c["name"] for c in inspector.get_columns(name)}
        assert migrated_columns == set(table.columns.keys()), name


def test_migrated_primary_keys_match_the_models(migrated):
    """Composite keys are the idempotency guarantee; a drifted one loses it."""
    inspector = inspect(migrated)

    for name, table in Base.metadata.tables.items():
        migrated_pk = set(inspector.get_pk_constraint(name)["constrained_columns"])
        assert migrated_pk == {c.name for c in table.primary_key.columns}, name
