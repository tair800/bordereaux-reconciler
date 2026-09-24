"""The store, against a real PostgreSQL database.

Skipped when no database is reachable, and that skip is deliberate and narrow: these tests assert
properties of `NUMERIC` and of a primary key, and running them against SQLite would assert
properties of SQLite. A green suite on an engine that cannot represent an exact decimal is worse
than a skipped one, because it reads as evidence.

The skip does not weaken the kill criteria. `test_kill_criteria.py` grades `idempotency.json`, which
is produced by a build that had a database; if the database is missing, that artifact is missing,
and the kill test fails rather than skips.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, OperationalError

from bordereaux_reconciler.domain import CanonicalRow, CellRef, Lineage, Tracked
from bordereaux_reconciler.ingest.canonical import QuarantinedRow
from bordereaux_reconciler.money import Currency, Money
from bordereaux_reconciler.store import create_all, get_engine
from bordereaux_reconciler.store.ledger import (
    count_rows,
    ingest_file,
    ledger_rows_for,
    numeric_round_trip,
    record_audit,
    record_mapping_contract,
    reset,
)
from bordereaux_reconciler.store.schema import audit_event, ledger_row

GBP = Currency.GBP


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    try:
        candidate = get_engine()
        with candidate.connect() as connection:
            connection.execute(select(1))
    except (OperationalError, OSError) as exc:
        pytest.skip(
            f"no PostgreSQL reachable ({type(exc).__name__}). Run `docker compose up -d postgres`. "
            "These tests assert properties of NUMERIC and of a primary key; SQLite has neither."
        )
    create_all(candidate)
    yield candidate


@pytest.fixture(autouse=True)
def _clean(engine: Engine) -> Iterator[None]:
    reset(engine)
    yield
    reset(engine)


def _row(key: str, gross: str, *, net: str | None = None) -> CanonicalRow:
    lineage = Lineage(
        source_content_hash="b" * 64,
        cell=CellRef(sheet="Bordereau", row=2, column="Gross Premium"),
        mapping_version=1,
        raw_value=gross,
    )
    return CanonicalRow(
        key=key,
        currency=GBP,
        period="2026-01",
        gross=Tracked[Money](
            value=Money(amount=Decimal(gross), currency=GBP), lineage=lineage
        ),
        net=(
            Tracked[Money](value=Money(amount=Decimal(net), currency=GBP), lineage=lineage)
            if net
            else None
        ),
    )


def _ingest(engine: Engine, content_hash: str, rows: tuple[CanonicalRow, ...], **kwargs: object):
    return ingest_file(
        engine,
        content_hash=content_hash,
        coverholder="Northgate",
        filename="march.csv",
        family="insurance",
        mapping_version=1,
        period="2026-01",
        rows=rows,
        **kwargs,  # type: ignore[arg-type]
    )


class TestExactMoney:
    @pytest.mark.parametrize(
        "amount",
        ["0.01", "0.10", "0.30", "38867.16", "1234567890123.4567", "-12.34"],
    )
    def test_a_decimal_survives_a_round_trip_through_numeric(
        self, engine: Engine, amount: str
    ) -> None:
        stored = numeric_round_trip(engine, Decimal(amount), family="insurance")
        assert stored == Decimal(amount)

    def test_the_sum_that_breaks_a_float_does_not_break_here(self, engine: Engine) -> None:
        """`0.1 + 0.2` is `0.30000000000000004` as a double, and `0.30` as a NUMERIC."""
        a = numeric_round_trip(engine, Decimal("0.10"), family="insurance")
        b = numeric_round_trip(engine, Decimal("0.20"), family="insurance")
        assert a + b == Decimal("0.30")

    def test_deductions_are_stored_as_strings_not_json_numbers(self, engine: Engine) -> None:
        row = _row("A", "1000.00")
        row = row.model_copy(
            update={
                "deductions": {
                    "tax": Tracked[Money](
                        value=Money(amount=Decimal("120.10"), currency=GBP),
                        lineage=row.gross.lineage,
                    )
                }
            }
        )
        _ingest(engine, "c" * 64, (row,))
        stored = ledger_rows_for(engine, "c" * 64)
        assert stored[0]["deductions"] == {"tax": "120.10"}
        assert isinstance(stored[0]["deductions"]["tax"], str)


class TestIdempotentIngestion:
    def test_the_same_bytes_three_times_write_once(self, engine: Engine) -> None:
        rows = (_row("A", "1000.00"), _row("B", "2000.00"))
        first = _ingest(engine, "d" * 64, rows)
        second = _ingest(engine, "d" * 64, rows)
        third = _ingest(engine, "d" * 64, rows)

        assert first.rows_written == 2
        assert (second.already_present, third.already_present) == (True, True)
        assert (second.rows_written, third.rows_written) == (0, 0)
        assert len(ledger_rows_for(engine, "d" * 64)) == 2

    def test_a_repeat_is_recorded_in_the_audit_trail_rather_than_ignored(
        self, engine: Engine
    ) -> None:
        """A silently-skipped duplicate looks identical to a file that never arrived."""
        _ingest(engine, "e" * 64, (_row("A", "1000.00"),))
        _ingest(engine, "e" * 64, (_row("A", "1000.00"),))
        with engine.connect() as connection:
            events = [
                r.event
                for r in connection.execute(
                    select(audit_event.c.event).order_by(audit_event.c.id)
                )
            ]
        assert events == ["file_ingested", "ingest_skipped_duplicate"]

    def test_a_different_file_with_the_same_rows_is_not_a_duplicate(self, engine: Engine) -> None:
        """Identity is the file's bytes. Two coverholders may legitimately send the same row."""
        _ingest(engine, "f" * 64, (_row("A", "1000.00"),))
        second = _ingest(engine, "0" * 64, (_row("A", "1000.00"),))
        assert not second.already_present
        assert second.rows_written == 1


class TestDuplicateKeysAreHeldNotWritten:
    def test_a_repeated_key_goes_to_quarantine_instead_of_the_ledger(
        self, engine: Engine
    ) -> None:
        """The ledger is the accounting record, so it takes neither version of an ambiguous row."""
        rows = (_row("A", "1000.00"), _row("A", "1500.00"), _row("B", "2000.00"))
        result = _ingest(engine, "1" * 64, rows)

        assert result.rows_written == 1
        assert result.quarantined_written == 2
        stored = ledger_rows_for(engine, "1" * 64)
        assert [r["key"] for r in stored] == ["B"]

    def test_the_unique_constraint_still_refuses_a_caller_that_went_around_the_writer(
        self, engine: Engine
    ) -> None:
        """Belt and braces: the guarantee is in the schema, not only in `ingest_file`."""
        _ingest(engine, "2" * 64, (_row("A", "1000.00"),))
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                ledger_row.insert().values(
                    source_content_hash="2" * 64,
                    mapping_version=1,
                    key="A",
                    period="2026-01",
                    currency="GBP",
                    gross=Decimal("9999.99"),
                    deductions={},
                    net=None,
                    attributes={},
                    lineage=[],
                )
            )


class TestReplayIsAdditive:
    def test_a_new_mapping_version_inserts_rather_than_overwrites(self, engine: Engine) -> None:
        """Last month's numbers stay attributable to the mapping that produced them."""
        _ingest(engine, "3" * 64, (_row("A", "1000.00"),))
        with engine.begin() as connection:
            connection.execute(
                ledger_row.insert().values(
                    source_content_hash="3" * 64,
                    mapping_version=2,
                    key="A",
                    period="2026-01",
                    currency="GBP",
                    gross=Decimal("1010.00"),
                    deductions={},
                    net=None,
                    attributes={},
                    lineage=[],
                )
            )
        stored = ledger_rows_for(engine, "3" * 64)
        assert [(r["mapping_version"], r["gross"]) for r in stored] == [
            (1, "1000.0000"),
            (2, "1010.0000"),
        ]


class TestAuditTrail:
    def test_a_mapping_contract_records_who_confirmed_it(self, engine: Engine) -> None:
        record_mapping_contract(
            engine,
            coverholder="Northgate",
            version=1,
            family="insurance",
            columns={"Gross Premium": "gross_premium"},
            confirmed_by="j.okafor",
        )
        with engine.connect() as connection:
            events = connection.execute(
                select(audit_event.c.event, audit_event.c.actor)
            ).all()
        assert ("mapping_confirmed", "j.okafor") in [(e.event, e.actor) for e in events]

    def test_audit_events_accumulate(self, engine: Engine) -> None:
        for index in range(3):
            record_audit(
                engine,
                event="quarantine_reviewed",
                actor="a.reyes",
                subject=f"row-{index}",
                payload={"decision": "held"},
            )
        assert count_rows(engine)["audit_event"] == 3


def test_quarantined_rows_carry_their_reason(engine: Engine) -> None:
    _ingest(
        engine,
        "4" * 64,
        (_row("A", "1000.00"),),
        quarantined=(
            QuarantinedRow(
                spreadsheet_row=17,
                reason="the period cell 'TBC' is not a date this build recognises",
                raw_value="TBC",
            ),
        ),
    )
    assert count_rows(engine)["quarantine"] == 1
