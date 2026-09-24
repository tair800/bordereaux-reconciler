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

__all__ = ["DEFAULT_DATABASE_URL", "METADATA", "create_all", "database_url", "get_engine"]

#: The local compose service. Overridden by `BX_DATABASE_URL` everywhere it matters, and there is no
#: production default here on purpose: a connection string that works by accident is how a test run
#: ends up writing to somebody's real database.
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://bordereaux:bordereaux_local_only@127.0.0.1:15437/bordereaux"
)


def database_url() -> str:
    return os.environ.get("BX_DATABASE_URL", DEFAULT_DATABASE_URL)


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    """A pooled engine. Cached per URL so the console and the workers share one pool."""
    return create_engine(url or database_url(), pool_pre_ping=True, future=True)


def create_all(engine: Engine) -> None:
    """Create the schema directly, for tests and the evidence lane.

    Alembic owns the schema for anything long-lived; this exists so a test database can be stood up
    in one call without shelling out to a migration runner. `alembic upgrade head` and this function
    are kept in agreement by `test_migrations.py`, which builds a database each way and compares the
    two shapes — a comment promising they agree would not survive the first schema change.
    """
    METADATA.create_all(engine)
