"""Alembic environment.

The database URL comes from app.config rather than alembic.ini, so migrations
and the running app can never disagree about which database they mean.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import DATABASE_URL
from app.db.base import Base

# Imported for the side effect of registering every mapper on Base.metadata,
# which is what autogenerate and the schema test compare against.
from app.db import models  # noqa: F401

config = context.config
# Keep an explicit URL supplied by the CLI or startup migration. The generated
# alembic.ini contains a placeholder, which must fall back to app.config.
configured_url = config.get_main_option("sqlalchemy.url")
database_url = (
    configured_url
    if configured_url and configured_url != "driver://user:pass@localhost/dbname"
    else DATABASE_URL
)
config.set_main_option("sqlalchemy.url", database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER most things in place; batch mode rewrites the
        # table instead, so one migration script works on both backends.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A programmatic caller (`app.db.migrate.ensure_schema`) shares its own,
    # already-authenticated connection here rather than letting this module
    # open one from `sqlalchemy.url`. That URL was, for that caller, built
    # from a re-serialized engine URL -- which SQLAlchemy renders with the
    # password masked as `***` -- so reusing the real connection is what
    # keeps a programmatic run from authenticating with that mask. The CLI
    # (`alembic upgrade head`) sets no such connection and is unaffected.
    shared_connection = config.attributes.get("connection")
    if shared_connection is not None:
        _run_with(shared_connection)
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run_with(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
