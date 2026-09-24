"""Bring a fresh database up to head and fill it with the synthetic corpus, once.

    python scripts/seed_demo.py

Run at container start. The public demo needs a populated database to show anything, and on a free
hosting plan there is nowhere else to put the work: a Render pre-deploy command and a one-off job
are both paid features, so the alternative to seeding at boot is a console whose every screen reads
zero.

**That exemption is a property of the free plan, not of the design.** `docker-compose.yml` runs
migrations as a separate `migrate` service precisely so the serving process does not need permission
to create a table, and the note there about replicas racing each other through the same DDL is still
correct. It does not apply here because the free plan runs exactly one instance. Anything with more
than one replica must go back to the compose arrangement, and this script would then be the job it
runs rather than the entrypoint it is.

**Seeding is idempotent, and not by checking a flag.** The corpus is generated from a committed
seed, so the files are byte-identical on every boot, so their content hashes are identical, so
`ingest_file` is refused by the primary key on `ingested_file`. A redeploy therefore writes nothing
and changes no figure anybody has already been shown — which is kill condition B, demonstrated on
every restart rather than only in the evidence lane.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import OperationalError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from bordereaux_reconciler.corpus import build_corpus  # noqa: E402
from bordereaux_reconciler.evaluation.corpusio import load_cases  # noqa: E402
from bordereaux_reconciler.evaluation.run import run_variant  # noqa: E402
from bordereaux_reconciler.ingest.canonical import QuarantinedRow  # noqa: E402
from bordereaux_reconciler.store import database_url, get_engine  # noqa: E402
from bordereaux_reconciler.store.ledger import (  # noqa: E402
    count_rows,
    ingest_file,
    record_mapping_contract,
)
from bordereaux_reconciler.store.schema import ingested_file  # noqa: E402

#: How long to wait for the database before giving up and serving anyway.
#:
#: Long enough for a freshly provisioned managed Postgres, which can take most of a minute to accept
#: its first connection — a container that gave up in five seconds would make a first deploy look
#: like a code failure. Short enough that a container with no database at all still binds its port
#: promptly: this blocks uvicorn, and a web service that does not listen for two minutes is
#: indistinguishable from one that has crashed, to a platform health check and to a person.
CONNECT_TIMEOUT_SECONDS = 60
CONNECT_INTERVAL_SECONDS = 3

#: Who the seeded mapping contracts are attributed to. **Not** a person's name: these were confirmed
#: by a seeding script, and `confirmed_by` exists to record who took responsibility. Writing a
#: plausible human name here would put a false attestation in an audit table, which is a worse lie
#: than an ugly string.
SEED_ACTOR = "seed-script (synthetic demo data, not a human approval)"


def _log(message: str) -> None:
    print(f"[seed] {message}", flush=True)


def _wait_for_database() -> Engine:
    engine = get_engine()
    deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with engine.connect() as connection:
                connection.execute(select(1))
            return engine
        except (OperationalError, OSError) as exc:
            last = exc
            time.sleep(CONNECT_INTERVAL_SECONDS)
    raise SystemExit(f"[seed] the database never accepted a connection: {last}")


#: Exit code used when the database never arrived. The entrypoint ignores it and starts uvicorn
#: regardless — `/healthz` then reports `degraded`, which is a more useful thing to serve than
#: nothing at all.


def _migrate() -> None:
    """`alembic upgrade head`, in process.

    Through Alembic's Python API rather than by shelling out, because the image has no shell
    dependency on the CLI and a subprocess failure here is harder to report usefully than an
    exception. The migrations are the same ones `make migrate` and CI run.
    """
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url())
    command.upgrade(config, "head")


def _already_seeded(engine: Engine) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(select(func.count()).select_from(ingested_file)).scalar_one()
        )


def _seed(engine: Engine) -> None:
    # Into a temporary directory. The image's working directory is owned by root and the process
    # runs as an unprivileged user, which is the right way round — the corpus is derived data and
    # has no business being written next to the code that derives it.
    with tempfile.TemporaryDirectory(prefix="bordereaux-corpus-") as scratch:
        corpus = Path(scratch) / "generated"
        _log("generating the synthetic corpus from its committed seed")
        build_corpus(corpus)

        manifest, cases = load_cases(corpus)
        _log(f"{len(cases)} schema variants, {manifest['canonical_rows']} canonical rows")

        for case in cases:
            run = run_variant(case)
            result = ingest_file(
                engine,
                content_hash=run.content_hash,
                coverholder=case.variant_id,
                filename=case.source.name,
                family=case.family,
                mapping_version=case.profile.mapping_version,
                period=run.rows[0].period if run.rows else None,
                rows=run.rows,
                quarantined=tuple(
                    QuarantinedRow(spreadsheet_row=index + 2, reason=reason)
                    for index, reason in enumerate(run.quarantine_reasons)
                ),
                status="accepted" if run.mappable else "quarantined",
            )
            if run.mappable:
                record_mapping_contract(
                    engine,
                    coverholder=case.variant_id,
                    version=case.profile.mapping_version,
                    family=case.family,
                    columns=run.mapping.as_columns(),
                    confirmed_by=SEED_ACTOR,
                )
            wrote = (
                "already present" if result.already_present else f"{result.rows_written:4d} rows"
            )
            _log(f"  {case.variant_id:34s} {wrote}  {result.quarantined_written} held")


def main() -> int:
    if os.environ.get("BX_SKIP_SEED", "").strip().lower() in {"1", "true", "yes"}:
        _log("BX_SKIP_SEED is set; not touching the database")
        return 0

    _log(f"waiting for {get_engine().url.render_as_string(hide_password=True)}")
    engine = _wait_for_database()

    _log("applying migrations")
    _migrate()

    if _already_seeded(engine):
        _log("the ledger already holds data; nothing to do")
    else:
        _seed(engine)

    _log(f"ready: {count_rows(engine)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
