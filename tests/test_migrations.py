"""Alembic and `create_all` must produce the same database, and both must keep money exact.

`store/__init__.py` says these two agree. A comment saying so would not survive the first schema
change, so this builds a database each way, in separate PostgreSQL schemas, and compares the shapes
column by column.

The second test is the one that matters most in the whole suite. `NUMERIC` is exact and
`DOUBLE PRECISION` is not, and a migration that quietly changed one to the other would break the
project's central claim while every behavioural test stayed green — because Python would still hand
`Decimal` objects to SQLAlchemy and get plausible-looking numbers back.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, MetaData, create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from bordereaux_reconciler.store import create_all, database_url
from bordereaux_reconciler.store.schema import METADATA

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Two throwaway PostgreSQL schemas, so neither construction disturbs the working database.
FROM_MIGRATIONS = "shape_migrations"
FROM_METADATA = "shape_metadata"

#: Every monetary column in the schema, by table. Listed rather than discovered so that adding a
#: money column without adding it here is a visible omission rather than an invisible gap.
MONEY_COLUMNS = {
    "ledger_row": ("gross", "net"),
}


def _engine_or_skip() -> Engine:
    try:
        engine = create_engine(database_url())
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except (OperationalError, OSError) as exc:
        pytest.skip(f"no PostgreSQL reachable ({type(exc).__name__})")
    return engine


@pytest.fixture(scope="module")
def shapes() -> Iterator[tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]]:
    """Build the schema both ways and return the two shapes."""
    engine = _engine_or_skip()

    with engine.begin() as connection:
        for schema in (FROM_MIGRATIONS, FROM_METADATA):
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    # Alembic, through its real CLI rather than through a Python API that might diverge from what
    # an operator runs.
    environment = dict(os.environ)
    environment["BX_DATABASE_URL"] = database_url()
    environment["PGOPTIONS"] = f"-c search_path={FROM_MIGRATIONS}"
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{completed.stdout}\n{completed.stderr}")

    # `create_all`, into the other schema.
    metadata = MetaData(schema=FROM_METADATA, naming_convention=METADATA.naming_convention)
    for table in METADATA.tables.values():
        table.to_metadata(metadata, schema=FROM_METADATA)
    metadata.create_all(engine)

    inspector = inspect(engine)

    def shape(schema: str) -> dict[str, dict[str, str]]:
        return {
            table: {
                column["name"]: str(column["type"])
                for column in inspector.get_columns(table, schema=schema)
            }
            for table in sorted(inspector.get_table_names(schema=schema))
            if table != "alembic_version"
        }

    yield shape(FROM_MIGRATIONS), shape(FROM_METADATA)

    with engine.begin() as connection:
        for schema in (FROM_MIGRATIONS, FROM_METADATA):
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_the_migrations_and_the_metadata_produce_the_same_tables(
    shapes: tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]],
) -> None:
    migrations, metadata = shapes
    assert set(migrations) == set(metadata), (
        "`alembic upgrade head` and `create_all` disagree about which tables exist. The migrations "
        "own the schema; run `alembic revision --autogenerate` and commit the result."
    )


def test_every_column_has_the_same_type_either_way(
    shapes: tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]],
) -> None:
    migrations, metadata = shapes
    differences = [
        f"{table}.{column}: migrations say {migrations[table][column]}, metadata says {declared}"
        for table, columns in metadata.items()
        for column, declared in columns.items()
        if migrations.get(table, {}).get(column) != declared
    ]
    assert not differences, "the two constructions disagree:\n  " + "\n  ".join(differences)


@pytest.mark.parametrize(
    ("table", "column"),
    [(table, column) for table, columns in MONEY_COLUMNS.items() for column in columns],
)
def test_money_is_numeric_and_never_a_float_type(
    shapes: tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]],
    table: str,
    column: str,
) -> None:
    """The one that would break the central claim while every behavioural test stayed green.

    Python would still hand `Decimal` objects to SQLAlchemy and get plausible numbers back from a
    `DOUBLE PRECISION` column. The failure would surface as a penny nobody could explain, months
    later, in a reconciliation somebody had already signed off.
    """
    migrations, _ = shapes
    declared = migrations[table][column]
    assert declared.startswith("NUMERIC"), (
        f"{table}.{column} is {declared}. Money is NUMERIC or the project's central claim is gone, "
        "and no amount of care in Python gets it back."
    )
    assert "DOUBLE" not in declared and "FLOAT" not in declared and "REAL" not in declared


def test_create_all_is_idempotent() -> None:
    """The evidence lane calls it on a database that may already have the schema."""
    engine = _engine_or_skip()
    create_all(engine)
    create_all(engine)


def test_there_is_exactly_one_migration_head() -> None:
    """Two heads mean a merge nobody performed, and `upgrade head` becomes ambiguous."""
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "BX_DATABASE_URL": database_url()},
    )
    heads = [line for line in completed.stdout.splitlines() if "(head)" in line]
    assert len(heads) == 1, f"expected one migration head, found {len(heads)}: {heads}"
