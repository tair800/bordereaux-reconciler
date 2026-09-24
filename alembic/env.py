"""Alembic's entry point. Reads the URL from the environment, never from a committed file.

`target_metadata` is the same `MetaData` the application uses, so `alembic revision --autogenerate`
compares the database against the real schema rather than against a second declaration that would
drift from it.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from bordereaux_reconciler.store import database_url
from bordereaux_reconciler.store.schema import METADATA

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# `database_url()`, not `os.environ.get("BX_DATABASE_URL", ...)`. This read the variable itself and
# it was the same defect twice: a hosting provider hands out `postgres://`, SQLAlchemy 2.0 refuses
# it, and the application normalised it while the migration runner did not — so the service started
# and the migrations it depends on could not. Found by running the real container against a real
# empty database with the URL a provider actually emits, which is the only way that class of bug
# shows up.
config.set_main_option("sqlalchemy.url", database_url())

target_metadata = METADATA


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # On by default here and off by default in Alembic. A column that quietly changed from
            # NUMERIC to DOUBLE PRECISION is the single worst thing that could happen to this
            # schema, and autogenerate would not mention it otherwise.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
