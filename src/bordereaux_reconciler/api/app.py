"""The operator console. Six server-rendered screens, and one write path that is off by default.

Server-rendered rather than a single-page app, and the reason is the audience rather than taste: the
person who uses this is a delegated-authority technician reconciling a month's premium, on a
corporate laptop, and every screen here is a table with evidence attached. A bundler would add a
build step, a second language and a hydration story to a product whose hardest problem is that
`1.234,56` means two different numbers.

**The false MATCHED banner is the first thing on the overview and it is unconditional.** It renders
whether the count is zero or not, because a warning that only appears when something is wrong
teaches nobody where to look, and the whole point of ADR-001 kill condition D is that this number is
the one a reader should check first.

**Writes fail closed.** `BX_READ_ONLY` defaults to true and `BX_APPROVER_TOKEN` has no default, so a
deployment that configured nothing serves the console and rejects every approval with 403. The
public demo runs exactly that way. `require_writes` is a dependency rather than a check inside each
handler so that adding a write endpoint without the guard takes a deliberate omission rather than
forgetting a line.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine, desc, func, select

from bordereaux_reconciler.adapters import get_adapter, known_families
from bordereaux_reconciler.config import Settings, get_settings
from bordereaux_reconciler.store import get_engine
from bordereaux_reconciler.store.schema import (
    audit_event,
    ingested_file,
    ledger_row,
    mapping_contract,
    quarantine,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(
    title="Bordereaux Reconciler",
    description=(
        "Reconciles delegated-authority bordereaux against a carrier ledger. Exact decimal money, "
        "cell-level lineage, and a model that may propose a column mapping and nothing else."
    ),
    version="0.1.0",
)


def settings() -> Settings:
    return get_settings()


def engine(config: Annotated[Settings, Depends(settings)]) -> Engine:
    return get_engine(config.database_url)


def require_writes(
    config: Annotated[Settings, Depends(settings)],
    x_approver_token: Annotated[str | None, Header()] = None,
) -> str:
    """The write gate. Returns the approver's identity, or refuses.

    The identity comes from the token, never from a header the caller also controls. An
    `X-Approved-By` that the client filled in would put an attacker's chosen name in the audit trail
    beside an action they took, which is worse than no name at all.

    Comparison is over bytes with `secrets.compare_digest`, so a wrong token takes the same time as
    a right one regardless of where it first differs.
    """
    import secrets  # noqa: PLC0415 - used once, here

    # `config.writes_enabled`, not a second copy of the same condition. This read
    # `config.read_only or not config.approver_token`, which says the same thing today and is a
    # separate place for it to stop saying it — a planted breach that flipped `writes_enabled` to
    # `or` left this gate closed and the header chip open, so the console would have advertised a
    # capability the API refused. One policy, one expression.
    expected = config.approver_token
    # `expected is None` is redundant at runtime — `writes_enabled` already requires a token — and
    # it is what lets the type checker narrow, which is worth one clause for a comparison that must
    # never be handed a `None`.
    if not config.writes_enabled or expected is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "this deployment is read-only. Confirming a mapping changes how a coverholder's "
                "premium is read, so it is not something an anonymous visitor does."
            ),
        )
    if not x_approver_token or not secrets.compare_digest(
        x_approver_token.encode(), expected.encode()
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="approver token rejected")
    return f"approver:{expected[:4]}…"


def _artifacts(config: Settings) -> dict[str, Any]:
    """Whatever evidence has been built. Missing is normal, not an error.

    A checkout that has not run `make artifacts` should still serve the console; the overview says
    the evidence has not been built rather than failing, because a blank screen teaches nobody that
    a command exists.
    """
    directory = Path(config.artifacts_dir)
    loaded: dict[str, Any] = {}
    for name in (
        "evaluation",
        "determinism",
        "idempotency",
        "lineage",
        "abstention",
        "portability",
        "residency",
        "corpus",
    ):
        path = directory / f"{name}.json"
        if path.is_file():
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _context(config: Settings, **extra: Any) -> dict[str, Any]:
    """The variables every template needs. `request` is not among them.

    Starlette injects it from the first positional argument of `TemplateResponse`; passing it in the
    context as well is the deprecated call shape and types as an error under strict mypy.
    """
    return {
        "settings": config,
        "environment": config.environment,
        "writes_enabled": config.writes_enabled,
        **extra,
    }


@app.get("/healthz", include_in_schema=False)
def healthz(
    db: Annotated[Engine, Depends(engine)],
    config: Annotated[Settings, Depends(settings)],
) -> JSONResponse:
    """Liveness plus the two facts an operator actually needs from a health check.

    Whether the database answers, and whether this instance can write. A health endpoint that
    returned `{"status": "ok"}` and nothing else would be green on an instance that had silently
    come up with writes enabled in production.
    """
    try:
        with db.connect() as connection:
            connection.execute(select(func.count()).select_from(ingested_file)).scalar_one()
        database = "ok"
    except Exception as exc:  # a health check reports failures, it does not raise them
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "database": type(exc).__name__},
        )
    return JSONResponse(
        {
            "status": "ok",
            "database": database,
            "read_only": config.read_only,
            "writes_enabled": config.writes_enabled,
            "environment": config.environment,
            "families": list(known_families()),
        }
    )


@app.get("/", response_class=HTMLResponse)
def overview(
    request: Request,
    db: Annotated[Engine, Depends(engine)],
    config: Annotated[Settings, Depends(settings)],
) -> HTMLResponse:
    """Screen 1. What has been ingested, and the evidence, with false MATCHED at the top."""
    with db.connect() as connection:
        counts = {
            "files": connection.execute(
                select(func.count()).select_from(ingested_file)
            ).scalar_one(),
            "ledger_rows": connection.execute(
                select(func.count()).select_from(ledger_row)
            ).scalar_one(),
            "quarantined": connection.execute(
                select(func.count())
                .select_from(quarantine)
                .where(quarantine.c.resolved_at.is_(None))
            ).scalar_one(),
            "audit_events": connection.execute(
                select(func.count()).select_from(audit_event)
            ).scalar_one(),
        }
        files = (
            connection.execute(
                select(ingested_file).order_by(desc(ingested_file.c.ingested_at)).limit(25)
            )
            .mappings()
            .all()
        )

    return TEMPLATES.TemplateResponse(
        request,
        "overview.html",
        _context(config, counts=counts, files=files, artifacts=_artifacts(config)),
    )


@app.get("/files/{content_hash}", response_class=HTMLResponse)
def file_detail(
    content_hash: str,
    request: Request,
    db: Annotated[Engine, Depends(engine)],
    config: Annotated[Settings, Depends(settings)],
) -> HTMLResponse:
    """Screen 2. One file: what was read from it, and what was held back."""
    with db.connect() as connection:
        record = (
            connection.execute(
                select(ingested_file).where(ingested_file.c.content_hash == content_hash)
            )
            .mappings()
            .first()
        )
        if record is None:
            raise HTTPException(status_code=404, detail="no file with that content hash")
        rows = (
            connection.execute(
                select(ledger_row)
                .where(ledger_row.c.source_content_hash == content_hash)
                .order_by(ledger_row.c.key)
                .limit(500)
            )
            .mappings()
            .all()
        )
        held = (
            connection.execute(
                select(quarantine)
                .where(quarantine.c.source_content_hash == content_hash)
                .order_by(quarantine.c.spreadsheet_row)
            )
            .mappings()
            .all()
        )

    return TEMPLATES.TemplateResponse(
        request,
        "file_detail.html",
        _context(config, file=record, rows=rows, quarantined=held),
    )


@app.get("/rows/{row_id}", response_class=HTMLResponse)
def row_lineage(
    row_id: int,
    request: Request,
    db: Annotated[Engine, Depends(engine)],
    config: Annotated[Settings, Depends(settings)],
) -> HTMLResponse:
    """Screen 3. One row, every value traced to the cell it came from.

    This screen is claim 1 made visible. A technician who disputes a figure sees the sheet, the row
    number as the spreadsheet numbers it, the source header verbatim, and the raw text before any
    normalisation — enough to open the original file and look at the same cell.
    """
    with db.connect() as connection:
        row = (
            connection.execute(select(ledger_row).where(ledger_row.c.id == row_id))
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="no ledger row with that id")
        source = (
            connection.execute(
                select(ingested_file).where(
                    ingested_file.c.content_hash == row["source_content_hash"]
                )
            )
            .mappings()
            .first()
        )

    return TEMPLATES.TemplateResponse(
        request, "row_lineage.html", _context(config, row=row, source=source)
    )


@app.get("/quarantine", response_class=HTMLResponse)
def quarantine_queue(
    request: Request,
    db: Annotated[Engine, Depends(engine)],
    config: Annotated[Settings, Depends(settings)],
) -> HTMLResponse:
    """Screen 4. The rows that were not turned into money, and exactly why.

    A queue rather than a bin. A row nobody reviews is premium nobody collected, so the count is on
    the overview and the reasons are full sentences rather than error codes.
    """
    with db.connect() as connection:
        held = (
            connection.execute(
                select(quarantine, ingested_file.c.filename, ingested_file.c.coverholder)
                .join(
                    ingested_file, quarantine.c.source_content_hash == ingested_file.c.content_hash
                )
                .where(quarantine.c.resolved_at.is_(None))
                .order_by(quarantine.c.source_content_hash, quarantine.c.spreadsheet_row)
                .limit(500)
            )
            .mappings()
            .all()
        )

    return TEMPLATES.TemplateResponse(
        request, "quarantine.html", _context(config, quarantined=held)
    )


@app.get("/mappings", response_class=HTMLResponse)
def mappings(
    request: Request,
    db: Annotated[Engine, Depends(engine)],
    config: Annotated[Settings, Depends(settings)],
) -> HTMLResponse:
    """Screen 5. Confirmed mappings, with the adapter's canonical fields beside them.

    Every mapping here has a person's name on it. `confirmed_by` is not nullable in the schema, so a
    mapping a model proposed and nobody accepted cannot be in this table.
    """
    with db.connect() as connection:
        contracts = (
            connection.execute(
                select(mapping_contract).order_by(
                    mapping_contract.c.coverholder, desc(mapping_contract.c.version)
                )
            )
            .mappings()
            .all()
        )

    families = {family: get_adapter(family).fields for family in known_families()}
    return TEMPLATES.TemplateResponse(
        request, "mappings.html", _context(config, contracts=contracts, families=families)
    )


@app.get("/evidence", response_class=HTMLResponse)
def evidence(request: Request, config: Annotated[Settings, Depends(settings)]) -> HTMLResponse:
    """Screen 6. The eight kill-criteria artifacts, rendered rather than described.

    Every number on this page comes from a file in `artifacts/`, and the page says which. A claim a
    reader cannot trace to a build output is a claim this project is not entitled to make.
    """
    return TEMPLATES.TemplateResponse(
        request, "evidence.html", _context(config, artifacts=_artifacts(config))
    )


@app.post("/mappings/{coverholder}/confirm")
def confirm_mapping(
    coverholder: str,
    payload: dict[str, Any],
    approver: Annotated[str, Depends(require_writes)],
    db: Annotated[Engine, Depends(engine)],
) -> JSONResponse:
    """The only write path in the console, and the only place a proposal becomes a contract.

    Validated against the adapter's canonical field set before anything is stored, because a
    proposal naming a field that does not exist is not a mapping to be corrected later — it is input
    that should never have reached the table.
    """
    from bordereaux_reconciler.store.ledger import record_mapping_contract  # noqa: PLC0415

    family = str(payload.get("family", ""))
    if family not in known_families():
        raise HTTPException(status_code=422, detail=f"unknown family {family!r}")

    columns = payload.get("columns")
    if not isinstance(columns, dict) or not columns:
        raise HTTPException(status_code=422, detail="columns must be a non-empty object")

    known = {field.name for field in get_adapter(family).fields}
    unknown = sorted(set(columns.values()) - known)
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"not canonical fields of the {family} adapter: {unknown}"
        )

    version = int(payload.get("version", 1))
    record_mapping_contract(
        db,
        coverholder=coverholder,
        version=version,
        family=family,
        columns={str(k): str(v) for k, v in columns.items()},
        confirmed_by=approver,
    )
    return JSONResponse(
        {"coverholder": coverholder, "version": version, "confirmed_by": approver}, status_code=201
    )
