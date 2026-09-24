"""Writing to the ledger, and the two ways a repeat ingestion is prevented from doing damage.

Kill condition B says a file ingested three times produces one canonical version. This module does
not *check* for that and then skip; it lets the database refuse, and treats the refusal as the
normal path rather than as an error:

- :func:`ingest_file` inserts `ingested_file` keyed on the content hash. On a repeat that is a
  primary-key conflict, the insert is skipped by the database, and the function returns
  ``already_present`` without writing a single ledger row. A code-level `SELECT` first and `INSERT`
  second would leave a window between them that two workers can both pass through.
- `ledger_row` is unique on (content hash, mapping version, key), so even a caller that went around
  `ingest_file` could not create a second copy of the same row under the same mapping.

**Replay is additive.** Re-deriving a period under a *new* mapping version inserts a new generation
of rows rather than updating the old ones, because last month's numbers have already been reported
to someone and must stay attributable to the mapping that produced them. Correcting history in place
is how a reconciliation system loses the ability to explain what it said.

Every monetary value crosses this boundary as a :class:`decimal.Decimal` and lands in a `NUMERIC`
column. Nothing here accepts, produces or passes through a float, and `test_store.py` asserts it by
reading the values back out and comparing them exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bordereaux_reconciler.domain import CanonicalRow
from bordereaux_reconciler.ingest.canonical import QuarantinedRow
from bordereaux_reconciler.store.schema import (
    audit_event,
    ingested_file,
    ledger_row,
    mapping_contract,
    quarantine,
)

__all__ = [
    "IngestResult",
    "ingest_file",
    "ledger_rows_for",
    "record_audit",
    "record_mapping_contract",
]


@dataclass(frozen=True)
class IngestResult:
    """What one ingestion attempt did. `already_present` is a success, not a failure."""

    content_hash: str
    already_present: bool
    rows_written: int
    quarantined_written: int

    @property
    def wrote_anything(self) -> bool:
        return bool(self.rows_written or self.quarantined_written)


def _deductions_json(row: CanonicalRow) -> dict[str, str]:
    """Deductions as name -> exact decimal **string**.

    A JSON number is a double in every parser worth naming. `{"tax": 120.10}` survives a round trip
    through Postgres's JSON type and does not survive one through `json.loads`, and the failure is
    silent — which is the only kind this project treats as unacceptable.
    """
    return {name: str(tracked.value.amount) for name, tracked in sorted(row.deductions.items())}


def _lineage_json(row: CanonicalRow) -> list[dict[str, Any]]:
    return [
        {
            "source_content_hash": lineage.source_content_hash,
            "sheet": lineage.cell.sheet,
            "row": lineage.cell.row,
            "column": lineage.cell.column,
            "mapping_version": lineage.mapping_version,
            "raw_value": lineage.raw_value,
        }
        for lineage in row.lineage()
    ]


def _split_repeated_keys(
    rows: tuple[CanonicalRow, ...],
) -> tuple[list[CanonicalRow], list[tuple[CanonicalRow, int]]]:
    """Separate the rows with a unique identity from the rows whose identity repeats.

    A bordereau that lists the same policy and period twice with different premium is a real and
    common thing, and there is nothing in the file that says which line supersedes the other. The
    ledger is the accounting record, so it takes neither: writing the first would be a decision
    nobody made, writing both would make every later total ambiguous, and the unique constraint on
    (content hash, mapping version, key) means the database would refuse the second anyway.

    They are not dropped. They go to quarantine with the count, and the reconciliation report still
    reports them as DUPLICATE — the row is visible, it simply is not yet money.
    """
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.key] = counts.get(row.key, 0) + 1
    return (
        [row for row in rows if counts[row.key] == 1],
        [(row, counts[row.key]) for row in rows if counts[row.key] > 1],
    )


def ingest_file(
    engine: Engine,
    *,
    content_hash: str,
    coverholder: str,
    filename: str,
    family: str,
    mapping_version: int,
    period: str | None,
    rows: tuple[CanonicalRow, ...],
    quarantined: tuple[QuarantinedRow, ...] = (),
    status: str = "accepted",
    object_key: str | None = None,
) -> IngestResult:
    """Record a file and its canonical rows. Ingesting the same bytes again writes nothing.

    The whole function runs in one transaction. A crash between the file row and the ledger rows
    would otherwise leave a content hash recorded as ingested with no rows behind it, and every
    later attempt at that file would be skipped as a duplicate of something that does not exist —
    a file silently and permanently lost, which is the worst outcome available here.
    """
    unique, repeated = _split_repeated_keys(rows)
    held = list(quarantined) + [
        QuarantinedRow(
            spreadsheet_row=row.gross.lineage.cell.row,
            reason=(
                f"the key {row.key!r} appears {count} times in this file. The ledger is the "
                "accounting record and has to be unambiguous, so no version of this row is written "
                "until somebody says which one supersedes the other. The reconciliation report "
                "still shows it, as DUPLICATE."
            ),
            raw_value=str(row.gross.value.amount),
        )
        for row, count in repeated
    ]

    with engine.begin() as connection:
        inserted = connection.execute(
            pg_insert(ingested_file)
            .values(
                content_hash=content_hash,
                coverholder=coverholder,
                filename=filename,
                family=family,
                mapping_version=mapping_version,
                period=period,
                row_count=len(unique),
                quarantined_count=len(held),
                status=status,
                object_key=object_key,
            )
            .on_conflict_do_nothing(index_elements=["content_hash"])
            .returning(ingested_file.c.content_hash)
        ).first()

        if inserted is None:
            # The database declined, which is the answer. Nothing below runs, so a re-ingestion
            # cannot append rows, cannot re-open a resolved quarantine, and cannot change a number
            # anybody has already been shown.
            connection.execute(
                insert(audit_event).values(
                    at=datetime.now(tz=UTC),
                    event="ingest_skipped_duplicate",
                    actor="system",
                    subject=content_hash,
                    payload={"filename": filename, "coverholder": coverholder},
                )
            )
            return IngestResult(
                content_hash=content_hash,
                already_present=True,
                rows_written=0,
                quarantined_written=0,
            )

        if unique:
            connection.execute(
                insert(ledger_row),
                [
                    {
                        "source_content_hash": content_hash,
                        "mapping_version": mapping_version,
                        "key": row.key,
                        "period": row.period,
                        "currency": str(row.currency),
                        "gross": row.gross.value.amount,
                        "deductions": _deductions_json(row),
                        "net": row.net.value.amount if row.net else None,
                        "attributes": {n: t.value for n, t in sorted(row.attributes.items())},
                        "lineage": _lineage_json(row),
                    }
                    for row in unique
                ],
            )

        if held:
            connection.execute(
                insert(quarantine),
                [
                    {
                        "source_content_hash": content_hash,
                        "spreadsheet_row": row.spreadsheet_row,
                        "reason": row.reason,
                        "raw_value": row.raw_value,
                    }
                    for row in held
                ],
            )

        connection.execute(
            insert(audit_event).values(
                at=datetime.now(tz=UTC),
                event="file_ingested",
                actor="system",
                subject=content_hash,
                payload={
                    "filename": filename,
                    "coverholder": coverholder,
                    "rows": len(unique),
                    "quarantined": len(held),
                    "repeated_keys_held": len(repeated),
                    "mapping_version": mapping_version,
                    "status": status,
                },
            )
        )

    return IngestResult(
        content_hash=content_hash,
        already_present=False,
        rows_written=len(unique),
        quarantined_written=len(held),
    )


def ledger_rows_for(engine: Engine, content_hash: str) -> list[dict[str, Any]]:
    """Every stored row for one file, newest mapping version last, for comparison across runs."""
    with engine.connect() as connection:
        result = connection.execute(
            select(
                ledger_row.c.key,
                ledger_row.c.mapping_version,
                ledger_row.c.period,
                ledger_row.c.currency,
                ledger_row.c.gross,
                ledger_row.c.deductions,
                ledger_row.c.net,
                ledger_row.c.lineage,
            )
            .where(ledger_row.c.source_content_hash == content_hash)
            .order_by(ledger_row.c.mapping_version, ledger_row.c.key)
        )
        return [
            {
                "key": row.key,
                "mapping_version": row.mapping_version,
                "period": row.period,
                "currency": row.currency,
                # `str(Decimal)` rather than `float`. The column is NUMERIC and the driver returns a
                # Decimal; rendering it any other way here would discard the exactness the column
                # type exists to preserve, one line before it is compared.
                "gross": str(row.gross),
                "deductions": row.deductions,
                "net": str(row.net) if row.net is not None else None,
                "lineage": row.lineage,
            }
            for row in result
        ]


def record_mapping_contract(
    engine: Engine,
    *,
    coverholder: str,
    version: int,
    family: str,
    columns: dict[str, str],
    confirmed_by: str,
) -> None:
    """Store a confirmed mapping. `confirmed_by` is a person, and the column is not nullable.

    A mapping that nobody confirmed is a mapping a model proposed, and ADR-001 forbids one of those
    reaching the ledger. Making the column required is how that is enforced at the only layer where
    enforcement is not optional.
    """
    digest = json.dumps(columns, sort_keys=True)
    with engine.begin() as connection:
        connection.execute(
            pg_insert(mapping_contract)
            .values(
                coverholder=coverholder,
                version=version,
                family=family,
                columns=columns,
                confirmed_by=confirmed_by,
                confirmed_at=datetime.now(tz=UTC),
                digest=f"{abs(hash(digest)):032x}"[:32],
            )
            .on_conflict_do_nothing(index_elements=["coverholder", "version"])
        )
        connection.execute(
            insert(audit_event).values(
                at=datetime.now(tz=UTC),
                event="mapping_confirmed",
                actor=confirmed_by,
                subject=f"{coverholder}@v{version}",
                payload={"columns": columns, "family": family},
            )
        )


def record_audit(
    engine: Engine, *, event: str, actor: str, subject: str, payload: dict[str, Any]
) -> None:
    """Append one audit event. There is no update and no delete, here or anywhere else."""
    with engine.begin() as connection:
        connection.execute(
            insert(audit_event).values(
                at=datetime.now(tz=UTC), event=event, actor=actor, subject=subject, payload=payload
            )
        )


def count_rows(engine: Engine) -> dict[str, int]:
    """Row counts per table, for the idempotency artifact and the console's header."""
    with engine.connect() as connection:
        return {
            table.name: connection.execute(select(func.count()).select_from(table)).scalar_one()
            for table in (ingested_file, ledger_row, quarantine, audit_event, mapping_contract)
        }


def reset(engine: Engine) -> None:
    """Empty every table. Used by the evidence lane and by tests; never exposed over HTTP.

    Deliberately not a `DROP`: the schema is owned by Alembic, and a helper that could drop it would
    let a test leave a database whose shape no longer matches its migration history.
    """
    with engine.begin() as connection:
        for table in (audit_event, quarantine, ledger_row, mapping_contract, ingested_file):
            connection.execute(delete(table))


def numeric_round_trip(engine: Engine, amount: Decimal, *, family: str) -> Decimal:
    """Write one amount and read it back. The exactness claim, checkable in one call.

    Exists so `test_store.py` can assert the property against the real column type rather than
    against a mock — including the values that make binary floating point fail, like `0.1 + 0.2`.

    `family` is a parameter rather than a default. It was `family="insurance"`, and that one string
    was the only place a family name had reached the engine — caught by kill condition G's leak
    scan, in a helper nobody would have thought to look in.
    """
    with engine.begin() as connection:
        connection.execute(
            insert(ingested_file).values(
                content_hash=f"roundtrip-{amount}",
                coverholder="roundtrip",
                filename="roundtrip",
                family=family,
                mapping_version=1,
                period=None,
                row_count=0,
                quarantined_count=0,
                status="accepted",
            )
        )
        connection.execute(
            insert(ledger_row).values(
                source_content_hash=f"roundtrip-{amount}",
                mapping_version=1,
                key="roundtrip",
                period="2026-01",
                currency="GBP",
                gross=amount,
                deductions={},
                net=None,
                attributes={},
                lineage=[],
            )
        )
        stored = connection.execute(
            select(ledger_row.c.gross).where(
                ledger_row.c.source_content_hash == f"roundtrip-{amount}"
            )
        ).scalar_one()
    return Decimal(str(stored))
