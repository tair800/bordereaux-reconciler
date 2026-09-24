"""The connection string a hosting provider hands out must work, everywhere it is read.

This file exists because of a deployment failure rather than a hypothetical. Render's
`fromDatabase: connectionString` emits `postgres://`, SQLAlchemy 2.0 removed that dialect name in
1.4, and `create_engine` raises `NoSuchModuleError` before a single query runs.

It was then the same defect twice: the application normalised the URL and `alembic/env.py` read the
environment variable itself, so the service started and the migrations it depends on could not. The
last two tests here are the ones that matter — they assert that **every** place a connection string
is read goes through one function, because a shim applied in one of two call sites is worse than no
shim at all. It looks fixed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from bordereaux_reconciler.store import DEFAULT_DATABASE_URL, database_url, normalise_database_url

ROOT = Path(__file__).resolve().parents[1]


class TestNormalisation:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            # What Render, Heroku and most managed dashboards emit.
            (
                "postgres://u:p@host:5432/db",
                "postgresql+psycopg://u:p@host:5432/db",
            ),
            # Accepted by SQLAlchemy but resolves to whichever DBAPI imports first, which is not a
            # decision that should depend on import order in a project whose money handling needs
            # the driver to return Decimal.
            (
                "postgresql://u:p@host:5432/db",
                "postgresql+psycopg://u:p@host:5432/db",
            ),
            # Already correct; must be untouched.
            (
                "postgresql+psycopg://u:p@host:5432/db",
                "postgresql+psycopg://u:p@host:5432/db",
            ),
            # Whitespace from a copied environment variable.
            (
                "  postgres://u:p@host/db  ",
                "postgresql+psycopg://u:p@host/db",
            ),
            # Query parameters survive: `sslmode=require` is not optional on a managed instance.
            (
                "postgres://u:p@host/db?sslmode=require",
                "postgresql+psycopg://u:p@host/db?sslmode=require",
            ),
        ],
    )
    def test_a_providers_scheme_becomes_the_tested_driver(self, given: str, expected: str) -> None:
        assert normalise_database_url(given) == expected

    def test_an_explicitly_chosen_driver_is_left_alone(self) -> None:
        """Rewriting `+asyncpg` to `+psycopg` would override a deliberate choice.

        The two drivers do not agree about how a `NUMERIC` column comes back, so silently swapping
        one for the other is precisely the class of change this project cannot afford to make
        invisibly.
        """
        asked_for = "postgresql+asyncpg://u:p@host/db"
        assert normalise_database_url(asked_for) == asked_for

    def test_the_normalised_url_actually_builds_an_engine(self) -> None:
        """The assertion that would have caught the original failure.

        Comparing strings proves the function rewrites text. Handing the result to `create_engine`
        proves SQLAlchemy accepts it, which is the thing that was untrue.
        """
        engine = create_engine(normalise_database_url("postgres://u:p@localhost:5432/db"))
        assert engine.dialect.driver == "psycopg"

    def test_the_raw_provider_scheme_really_is_rejected_without_the_shim(self) -> None:
        """Pins the reason this module exists. If SQLAlchemy ever accepts it again, say so here."""
        with pytest.raises(Exception, match="postgres"):
            create_engine("postgres://u:p@localhost:5432/db")

    def test_the_default_is_already_normal(self) -> None:
        assert normalise_database_url(DEFAULT_DATABASE_URL) == DEFAULT_DATABASE_URL


class TestEveryCallSiteGoesThroughIt:
    """A shim applied in one of two call sites is worse than none: it looks fixed."""

    def test_database_url_normalises_what_it_reads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BX_DATABASE_URL", "postgres://u:p@host/db")
        assert database_url() == "postgresql+psycopg://u:p@host/db"

    def test_alembic_env_does_not_read_the_environment_variable_itself(self) -> None:
        """Asserted over the AST, because this is exactly how the second copy appeared.

        `alembic/env.py` had `os.environ.get("BX_DATABASE_URL", DEFAULT_DATABASE_URL)`, which looks
        careful and bypasses the normaliser completely.
        """
        source = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
        literals = [
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert "BX_DATABASE_URL" not in literals, (
            "alembic/env.py names BX_DATABASE_URL directly. It must call "
            "`bordereaux_reconciler.store.database_url()`, which normalises a hosting provider's "
            "scheme; reading the variable here is how the migration runner and the application "
            "came to disagree about the same connection string."
        )

    def test_no_module_outside_the_store_reads_the_variable_directly(self) -> None:
        """One reader, so there is one place for the shim to live."""
        offenders: list[str] = []
        for path in sorted((ROOT / "src").rglob("*.py")):
            if path.name == "__init__.py" and path.parent.name == "store":
                continue
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if (
                    isinstance(node, ast.Constant)
                    and node.value == "BX_DATABASE_URL"
                    # `config.py` is allowed to name it: it is the settings object, it hands the
                    # value straight to `get_engine`, and `get_engine` normalises.
                    and path.name != "config.py"
                ):
                    offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, (
            f"these read BX_DATABASE_URL without normalising: {sorted(set(offenders))}"
        )


def test_get_engine_normalises_an_explicitly_passed_url() -> None:
    """A caller being careful — reading the variable themselves — must not be punished for it."""
    from bordereaux_reconciler.store import get_engine  # noqa: PLC0415

    engine = get_engine("postgres://u:p@localhost:5432/normalise-check")
    assert engine.dialect.driver == "psycopg"
