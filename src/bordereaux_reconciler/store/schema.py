"""The database, and the constraints that make claim 1 true rather than intended.

Five tables. The interesting part is not what they hold, it is which guarantees live **in the
schema** rather than in the code that writes to it:

- `ingested_file` is keyed on the file's **content hash**. Re-ingesting a byte-identical file is a
  primary-key collision, not a code path that has to remember to check — which is why kill condition
  B can ask for a file to be ingested three times and expect one canonical version.
- `ledger_row` carries `(source_content_hash, mapping_version, key)` as its unique constraint.
  Replaying a period under a **new** mapping version therefore inserts rather than overwrites, and
  last month's ledger stays attributable to the mapping that produced it. A schema that made
  `key` alone unique would make replay destructive and the old values unexplainable.
- Monetary columns are `NUMERIC`, never `DOUBLE PRECISION`. Postgres's `numeric` is exact; the
  moment a premium is stored as a float the project's central claim is gone, and no amount of
  care in Python gets it back.
- `audit_event` has no update or delete path in this package at all. It is append-only because the
  question it answers — "who approved this mapping, and what did the model see" — is worthless if
  the answer can be edited afterwards.

PostgreSQL rather than SQLite because `SKILL_MATRIX.md` gives this project `PostgreSQL modelling and
migrations` and because `NUMERIC` behaves the same way in tests and in production, which is the
whole reason for choosing it.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)

__all__ = [
    "METADATA",
    "audit_event",
    "ingested_file",
    "ledger_row",
    "mapping_contract",
    "quarantine",
]

#: A naming convention so Alembic generates stable constraint names. Without it, autogenerate
#: invents names that differ between runs and every migration contains spurious drops.
METADATA = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

#: Scale and precision for every monetary column. 18 digits with 4 decimals holds any premium anyone
#: will send and leaves two spare decimal places below the minor unit, so an intermediate value that
#: has not been quantised yet still stores exactly rather than being silently rounded on the way in.
MONEY = Numeric(precision=18, scale=4)


ingested_file = Table(
    "ingested_file",
    METADATA,
    # The content hash **is** the identity. Not a surrogate id with a hash column beside it: making
    # it the primary key is what turns "we should check for duplicates" into "the database will
    # not accept one".
    Column("content_hash", String(64), primary_key=True),
    Column("coverholder", String(128), nullable=False),
    Column("filename", String(512), nullable=False),
    Column("family", String(32), nullable=False),
    Column("mapping_version", Integer, nullable=False),
    Column("period", String(7), nullable=True),
    Column("row_count", Integer, nullable=False, server_default="0"),
    Column("quarantined_count", Integer, nullable=False, server_default="0"),
    # `accepted` or `quarantined`. A file whose required fields could not be mapped is recorded as
    # having arrived — the fact that it was rejected is itself evidence somebody may need — but it
    # contributes no ledger rows.
    Column("status", String(16), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("object_key", String(512), nullable=True),
)


mapping_contract = Table(
    "mapping_contract",
    METADATA,
    Column("coverholder", String(128), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("family", String(32), nullable=False),
    Column("columns", JSON, nullable=False),
    Column("confirmed_by", String(128), nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("digest", String(32), nullable=False),
)


ledger_row = Table(
    "ledger_row",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "source_content_hash",
        String(64),
        ForeignKey("ingested_file.content_hash"),
        nullable=False,
    ),
    Column("mapping_version", Integer, nullable=False),
    Column("key", String(256), nullable=False),
    Column("period", String(7), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("gross", MONEY, nullable=False),
    # A JSON object of name -> exact decimal **as a string**. JSON numbers are IEEE 754 doubles in
    # every parser worth naming, so storing `{"tax": 120.00}` would reintroduce the float this
    # project spent a module eliminating.
    Column("deductions", JSON, nullable=False),
    Column("net", MONEY, nullable=True),
    Column("attributes", JSON, nullable=False),
    #: One lineage record per canonical value, so a cell can be traced without re-reading the file.
    Column("lineage", JSON, nullable=False),
    UniqueConstraint(
        "source_content_hash",
        "mapping_version",
        "key",
        name="one_row_per_key_per_mapping_version",
    ),
)


quarantine = Table(
    "quarantine",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "source_content_hash",
        String(64),
        ForeignKey("ingested_file.content_hash"),
        nullable=False,
    ),
    Column("spreadsheet_row", Integer, nullable=False),
    Column("reason", Text, nullable=False),
    Column("raw_value", Text, nullable=False, server_default=""),
    # Set when a person has looked. Quarantine is a queue, not a bin: a row nobody ever reviews is
    # premium nobody ever collected.
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("resolved_by", String(128), nullable=True),
)


audit_event = Table(
    "audit_event",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("event", String(64), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("subject", String(256), nullable=False),
    # Residency records land here too: provider, model, region, jurisdiction, payload size. Claim 3
    # needs a per-request record and this is where it is durable.
    Column("payload", JSON, nullable=False),
)
