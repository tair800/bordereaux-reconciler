"""One variant, end to end, plus the two things the kill criteria measure about it.

The pipeline here is the product's, not a test double: read the file, profile its columns, propose a
mapping, build canonical rows, reconcile against the carrier's ledger. If this module had its own
parsing or its own comparison, every number downstream would describe code that does not ship.

**Mapping accuracy is scored over the adapter's canonical fields, not over the file's columns.** For
each field the adapter declares, the system is right when it assigns the column the ground truth
assigns — *and* when it assigns nothing to a field the file does not contain. Scoring per column
would quietly award marks for the columns nobody has to get right, and would give no credit at all
for correct abstention, which in this domain is half the job: `mkt_03_no_net_column` has no payout
column, and mapping something to `payout` there is a real error that a per-column score would not
notice.

**A false MATCHED is defined against the answer key, never against the reconciler's own opinion.**
:mod:`~bordereaux_reconciler.evaluation.corpusio` decides what every row must produce before this
module runs; a row that comes back MATCHED when the key says MISMATCH or MISSING is counted, named
and reported with its amounts. That is the number ADR-001 fixes at zero, and it is the only number
in the project with no acceptable non-zero value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from bordereaux_reconciler.adapters import (
    ATTRIBUTE,
    DEDUCTION,
    GROSS,
    KEY,
    NET,
    PERIOD,
    Adapter,
    get_adapter,
)
from bordereaux_reconciler.domain import CanonicalRow, ReconciliationResult, Status
from bordereaux_reconciler.evaluation.corpusio import (
    CarrierLedger,
    VariantCase,
    build_carrier_ledger,
)
from bordereaux_reconciler.ingest.canonical import build_canonical_rows, sum_gross
from bordereaux_reconciler.ingest.mapping import BASELINES, MappingOutcome, propose_mapping
from bordereaux_reconciler.ingest.profile import profile_grid
from bordereaux_reconciler.ingest.read import read_grid
from bordereaux_reconciler.money import EXACT, Tolerance
from bordereaux_reconciler.reconcile import reconcile

__all__ = [
    "FalseMatch",
    "MappingScore",
    "VariantRun",
    "expected_field_columns",
    "run_variant",
    "score_mapping",
]


@dataclass(frozen=True)
class MappingScore:
    """How one mapper did on one file, and which fields it got wrong."""

    correct: int
    total: int
    wrong: tuple[str, ...] = ()

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass(frozen=True)
class FalseMatch:
    """A row reported as agreeing when it does not. Carried with its amounts, deliberately.

    A count alone invites the reader to treat it as a rate. These are printed in full by the
    evaluation and by the console, because the failure this project exists to prevent should be the
    most visible thing in any report that contains one.
    """

    variant_id: str
    key: str
    expected: str
    reported: str
    injection: str
    detail: str

    def as_json(self) -> dict[str, str]:
        return {
            "variant_id": self.variant_id,
            "key": self.key,
            "expected_status": self.expected,
            "reported_status": self.reported,
            "injected_discrepancy": self.injection,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class VariantRun:
    """Everything one variant produced, for every artifact that needs any of it."""

    case: VariantCase
    adapter: Adapter
    mapping: MappingOutcome
    rows: tuple[CanonicalRow, ...]
    quarantined_rows: int
    quarantine_reasons: tuple[str, ...]
    results: tuple[ReconciliationResult, ...]
    ledger: CarrierLedger
    expected: dict[str, Status]
    false_matches: tuple[FalseMatch, ...]
    rows_lost_to_quarantine: tuple[str, ...]
    content_hash: str
    system_mapping: MappingScore
    baseline_mappings: dict[str, MappingScore] = field(default_factory=dict)

    @property
    def mappable(self) -> bool:
        return self.mapping.mappable


def expected_field_columns(case: VariantCase, adapter: Adapter) -> dict[str, str | None]:
    """The truth's mapping, expressed in the adapter's field names.

    The corpus records roles (`gross`, `deductions.tax`) so its ground truth stays family-neutral;
    the adapter names them (`gross_premium`, `tax`). Translating by **role** rather than by a
    hand-written table is what keeps a third adapter from needing an edit here.

    A canonical path with no field in this adapter resolves to `None`: the file has a column with no
    canonical home, and leaving it unmapped is the correct answer rather than a missed one.
    """
    by_role = {f.role: f.name for f in adapter.fields if f.role in {KEY, PERIOD, GROSS, NET}}
    deductions = {f.name for f in adapter.fields if f.role == DEDUCTION}
    attributes = {f.name for f in adapter.fields if f.role == ATTRIBUTE}

    expected: dict[str, str | None] = {f.name: None for f in adapter.fields}
    for header, path in case.column_mapping.items():
        target: str | None = None
        if path in {"key", "period", "gross", "net"}:
            target = by_role.get({"key": KEY, "period": PERIOD, "gross": GROSS, "net": NET}[path])
        elif path.startswith("deductions."):
            name = path.split(".", 1)[1]
            target = name if name in deductions else None
        elif path.startswith("attributes."):
            name = path.split(".", 1)[1]
            target = name if name in attributes else None
        if target is not None:
            expected[target] = header
    return expected


def score_mapping(expected: dict[str, str | None], actual: dict[str, str | None]) -> MappingScore:
    """Compare two field -> column assignments over the adapter's whole field set."""
    wrong = [
        f"{field_name}: expected {expected[field_name]!r}, got {actual.get(field_name)!r}"
        for field_name in sorted(expected)
        if actual.get(field_name) != expected[field_name]
    ]
    return MappingScore(correct=len(expected) - len(wrong), total=len(expected), wrong=tuple(wrong))


def _invert(columns: dict[str, str | None], adapter: Adapter) -> dict[str, str | None]:
    """A baseline's column -> field answer, as field -> column.

    First claim wins on a collision, walking headers in sorted order so the result is reproducible.
    A baseline that assigns two columns to one field has made an error either way; resolving it
    deterministically means the comparison measures the baseline rather than dictionary ordering.
    """
    inverted: dict[str, str | None] = {f.name: None for f in adapter.fields}
    for header in sorted(columns):
        target = columns[header]
        if target is not None and target in inverted and inverted[target] is None:
            inverted[target] = header
    return inverted


def run_variant(case: VariantCase, *, tolerance: Tolerance = EXACT) -> VariantRun:
    """Read, map, canonicalise and reconcile one variant against the carrier's ledger."""
    adapter = get_adapter(case.family)
    grid = read_grid(case.source, sheet=case.sheet)
    profiles = profile_grid(grid.headers, {h: grid.column(h) for h in grid.headers})
    mapping = propose_mapping(grid.headers, profiles, adapter)

    expected_columns = expected_field_columns(case, adapter)
    system_score = score_mapping(
        expected_columns,
        _invert({p.source_header: p.canonical_field for p in mapping.proposals}, adapter),
    )
    baseline_scores = {
        name: score_mapping(expected_columns, _invert(fn(grid.headers, adapter, profiles), adapter))
        for name, fn in BASELINES.items()
    }

    if not mapping.mappable:
        # Kill condition F. Nothing is written, nothing is reconciled, and the missing required
        # fields are the reason — not a low score, which would be a threshold somebody could move.
        return VariantRun(
            case=case,
            adapter=adapter,
            mapping=mapping,
            rows=(),
            quarantined_rows=0,
            quarantine_reasons=(
                f"no column could be mapped to required field(s) "
                f"{', '.join(mapping.missing_required)}",
            ),
            results=(),
            ledger=CarrierLedger(rows=(), injections=()),
            expected={},
            false_matches=(),
            rows_lost_to_quarantine=(),
            content_hash=grid.content_hash,
            system_mapping=system_score,
            baseline_mappings=baseline_scores,
        )

    outcome = build_canonical_rows(grid, mapping.as_columns(), adapter, case.profile)
    ledger = build_carrier_ledger(case, adapter)

    # The `carrier_only` injection is a row the carrier has and the coverholder never sent, so it
    # is removed from the bordereau side here rather than in the ledger builder.
    carrier_only = {i.key for i in ledger.injections if i.kind == "carrier_only"}
    bordereau = tuple(row for row in outcome.rows if row.key not in carrier_only)

    truth_by_key = case.truth_by_key(adapter)
    expected = dict.fromkeys(truth_by_key, Status.MATCHED)
    for key in case.duplicate_keys(adapter):
        expected[key] = Status.DUPLICATE
    expected.update(ledger.expected_status)

    report = reconcile(bordereau, ledger.rows, tolerance=tolerance)

    ingested_keys = {row.key for row in bordereau}
    lost = tuple(
        sorted(
            key
            for key in truth_by_key
            if key not in ingested_keys
            and key not in carrier_only
            and expected[key] != Status.MISSING
        )
    )

    injection_by_key = {i.key: i for i in ledger.injections}
    false_matches = tuple(
        FalseMatch(
            variant_id=case.variant_id,
            key=result.key,
            expected=str(expected.get(result.key, Status.MATCHED)),
            reported=str(result.status),
            injection=(
                injection_by_key[result.key].kind if result.key in injection_by_key else "none"
            ),
            detail=(
                injection_by_key[result.key].detail
                if result.key in injection_by_key
                else "the row was reported as agreeing although the answer key says otherwise"
            ),
        )
        for result in report.results
        if result.status is Status.MATCHED
        and expected.get(result.key, Status.MATCHED) is not Status.MATCHED
    )

    return VariantRun(
        case=case,
        adapter=adapter,
        mapping=mapping,
        rows=outcome.rows,
        quarantined_rows=len(outcome.quarantined),
        quarantine_reasons=tuple(q.reason for q in outcome.quarantined),
        results=report.results,
        ledger=ledger,
        expected=expected,
        false_matches=false_matches,
        rows_lost_to_quarantine=lost,
        content_hash=grid.content_hash,
        system_mapping=system_score,
        baseline_mappings=baseline_scores,
    )


def canonical_digest_input(run: VariantRun) -> list[dict[str, Any]]:
    """Every canonical value and every status, flattened, for the determinism digest.

    Money is rendered with `str(Decimal)` so `10.50` and `10.5` hash differently. They *are*
    different — one has been quantised to the minor unit and the other has not — and a digest that
    could not tell them apart would pass over exactly the drift it exists to detect.
    """
    payload: list[dict[str, Any]] = []
    for row in sorted(run.rows, key=lambda r: (r.key, str(r.gross.value.amount))):
        payload.append(
            {
                "key": row.key,
                "period": row.period,
                "currency": str(row.currency),
                "gross": str(row.gross.value.amount),
                "deductions": {n: str(t.value.amount) for n, t in sorted(row.deductions.items())},
                "net": str(row.net.value.amount) if row.net else None,
                "attributes": {n: t.value for n, t in sorted(row.attributes.items())},
            }
        )
    return payload


def status_digest_input(run: VariantRun) -> list[list[str]]:
    """Every reconciliation status with its evidence rule, for the same reason."""
    return [
        [result.key, str(result.status), result.evidence.rule]
        for result in sorted(run.results, key=lambda r: r.key)
    ]


def total_gross(run: VariantRun) -> Decimal:
    if not run.rows:
        return Decimal(0)
    return sum_gross(run.rows, run.case.profile.currency).amount
