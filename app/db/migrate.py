"""Make an existing database safe to use with the current models.

Fresh databases can be created from the metadata, but ``create_all`` is not an
upgrade mechanism: it will not add ``users.data_source`` to a database made by
an earlier version of Reckon. Local development uses an un-stamped SQLite file
by default, so startup needs a small compatibility bridge for that file while
Alembic remains the source of truth for the actual schema changes.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INITIAL_REVISION = "b1a573c914fc"
SIMULATION_REVISION = "a8c5a409897f"
HEAD_REVISION = "0f3de7537fad"


def _config(engine: Engine) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    # Alembic's ConfigParser treats '%' as interpolation syntax. Escaping it
    # here keeps passwords containing '%' valid in a Postgres URL as well.
    config.set_main_option("sqlalchemy.url", str(engine.url).replace("%", "%%"))
    return config


def _revision_for_unstamped_schema(engine: Engine) -> str | None:
    """Return the latest migration represented by an old un-stamped database.

    Reckon versions before startup migrations used ``create_all`` and therefore
    have no ``alembic_version`` row. The presence of ``simulation_state`` is a
    reliable boundary for the two known legacy shapes; the final column check
    recognizes a database already created from the current models.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not tables:
        return None
    if "users" not in tables or "symbols" not in tables:
        raise RuntimeError(
            "database has an incomplete Reckon schema; restore it or point "
            "DATABASE_URL at a clean database"
        )

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    symbol_columns = {column["name"] for column in inspector.get_columns("symbols")}
    trading_day_columns = {column["name"] for column in inspector.get_columns("trading_days")}
    if {
        "data_source" in user_columns,
        "source" in symbol_columns,
        "source" in trading_day_columns,
    } == {True}:
        return HEAD_REVISION
    if "simulation_state" in tables:
        return SIMULATION_REVISION
    return INITIAL_REVISION


def ensure_schema(engine: Engine) -> None:
    """Upgrade fresh, current, and legacy databases to the migration head."""
    inspector = inspect(engine)
    if not inspector.get_table_names():
        # Alembic creates the schema and version marker for a genuinely fresh
        # database. This avoids a future startup silently diverging from the
        # migrations used in deployment.
        command.upgrade(_config(engine), "head")
        return

    if "alembic_version" not in inspector.get_table_names():
        revision = _revision_for_unstamped_schema(engine)
        if revision == HEAD_REVISION:
            # A database made with the current metadata is already compatible;
            # only its bookkeeping is missing.
            command.stamp(_config(engine), HEAD_REVISION)
        elif revision is not None:
            command.stamp(_config(engine), revision)

    _remove_orphaned_batch_tables(engine)
    command.upgrade(_config(engine), "head")


def _remove_orphaned_batch_tables(engine: Engine) -> None:
    """Remove empty Alembic scratch tables left by an interrupted SQLite run.

    Alembic's SQLite batch mode creates ``_alembic_tmp_*`` before swapping it
    into place. If the process is interrupted after creation, the next startup
    gets ``table already exists`` and can never retry the migration. We only
    remove a scratch table when its original table is still present; otherwise
    stopping with a clear error protects data that may be waiting in the temp
    table for manual recovery.
    """
    names = set(inspect(engine).get_table_names())
    scratch = sorted(name for name in names if name.startswith("_alembic_tmp_"))
    if not scratch:
        return

    for name in scratch:
        original = name.removeprefix("_alembic_tmp_")
        if original not in names:
            raise RuntimeError(
                f"database contains an orphaned migration table {name!r} without "
                f"its original {original!r}; restore a backup before retrying"
            )
        quoted = name.replace('"', '""')
        with engine.begin() as connection:
            connection.execute(text(f'DROP TABLE "{quoted}"'))
