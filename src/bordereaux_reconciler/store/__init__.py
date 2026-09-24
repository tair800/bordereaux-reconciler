"""The database: schema, writer, and the engine factory everything else asks for a connection.

PostgreSQL, not SQLite, and the reason is the project's central claim rather than a preference.
Postgres's `NUMERIC` is an exact decimal type; SQLite has no decimal type at all and SQLAlchemy
emulates one through a C double. A build that demonstrated exact money on SQLite would be
demonstrating it on an engine that cannot provide it, and the demonstration would be worthless
precisely where it mattered.
"""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine

from bordereaux_reconciler.store.schema import METADATA

__all__ = [
    "DEFAULT_DATABASE_URL",
    "METADATA",
    "create_all",
    "database_url",
    "get_engine",
    "normalise_database_url",
]

#: The local compose service. Overridden by `BX_DATABASE_URL` everywhere it matters, and there is no
#: production default here on purpose: a connection string that works by accident is how a test run
#: ends up writing to somebody's real database.
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://bordereaux:bordereaux_local_only@127.0.0.1:15437/bordereaux"
)

#: The driver every connection is made through. Named once so the normaliser below and the default
#: above cannot drift apart.
_DRIVER = "postgresql+psycopg"

#: Schemes a hosting provider hands out that SQLAlchemy 2.0 will not accept as written.
#:
#: `postgres://` is the one that matters. It is what Render's `fromDatabase: connectionString`
#: emits, what Heroku emits, and what most managed Postgres dashboards show in their "copy this"
#: box — and SQLAlchemy removed it as a dialect name in 1.4, so `create_engine` raises
#: `NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:postgres` before a single query runs.
#: `postgresql://` is accepted but resolves to whichever DBAPI is installed first, which is not a
#: decision that should be made by import order in a project whose money handling depends on the
#: driver returning `Decimal` rather than `float`.
_REWRITTEN_SCHEMES = ("postgres://", "postgresql://")


def normalise_database_url(url: str) -> str:
    """Coerce a hosting provider's connection string onto the driver this project is tested with.

    This exists because of a specific, verified failure rather than as defensive habit: deploying
    the committed Render blueprint hands `BX_DATABASE_URL` a `postgres://` string, and SQLAlchemy
    2.0 refuses it outright. Without this the service cannot start at all, and the failure is not
    subtle — it is a plugin-loading error at engine construction, long before anything reaches the
    database.

    A scheme that already names a driver is left exactly as it is. Rewriting
    `postgresql+asyncpg://` into `postgresql+psycopg://` would silently change which DBAPI a caller
    explicitly asked for, and the two do not agree about how a `NUMERIC` column comes back.
    """
    stripped = url.strip()
    for scheme in _REWRITTEN_SCHEMES:
        if stripped.startswith(scheme):
            return f"{_DRIVER}://{stripped[len(scheme) :]}"
    return stripped


def database_url() -> str:
    return normalise_database_url(os.environ.get("BX_DATABASE_URL", DEFAULT_DATABASE_URL))


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    """A pooled engine. Cached per URL so the console and the workers share one pool.

    An explicitly passed URL is normalised too. A caller reading a connection string out of the
    environment themselves would otherwise hit exactly the failure `normalise_database_url` exists
    to prevent, in a call that looks like it is being careful.
    """
    return create_engine(
        normalise_database_url(url) if url else database_url(),
        pool_pre_ping=True,
        future=True,
    )


def create_all(engine: Engine) -> None:
    """Create the schema directly, for tests and the evidence lane.

    Alembic owns the schema for anything long-lived; this exists so a test database can be stood up
    in one call without shelling out to a migration runner. `alembic upgrade head` and this function
    are kept in agreement by `test_migrations.py`, which builds a database each way and compares the
    two shapes — a comment promising they agree would not survive the first schema change.
    """
    METADATA.create_all(engine)
