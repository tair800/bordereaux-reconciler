"""Building the corpus, and proving it builds the same way twice.

The order of operations is the whole design, and it only works one way round:

1. **Truth first.** Rows are generated in the canonical shape with the arithmetic already closing.
2. **Adversarial cases second**, applied to those rows, each recorded by the name ADR-001 gives it.
3. **Projection third** — the row is reduced to what this variant's file actually carries, so a
   variant with no peril column has no peril in its ground truth and a variant with no payout
   column has a `null` net rather than a label no column could have produced.
4. **Rendering last**, and only then does a decimal convention, a date format or a currency symbol
   exist anywhere.

Reversing any two of those steps would mean deriving the labels from the file, which is the one
thing a ground truth may never be.

**Determinism is a property this module can be asked to demonstrate.** :func:`verify_determinism`
builds the whole corpus twice into two directories and compares every byte of every file. It is not
a test of the generator's intentions; it is a diff.
"""

from __future__ import annotations

import hashlib
import itertools
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from bordereaux_reconciler.corpus import adversarial as cases
from bordereaux_reconciler.corpus.manifest import (
    GENERATED_AT,
    GENERATOR_VERSION,
    SEED,
    VariantRecord,
    assemble,
    verify_split,
)
from bordereaux_reconciler.corpus.render import (
    opaque_table,
    source_table,
    write_csv,
    write_json,
    write_xlsx,
)
from bordereaux_reconciler.corpus.rng import FixtureRandom
from bordereaux_reconciler.corpus.truth import (
    TruthRow,
    insurance_key,
    insurance_rows,
    marketplace_key,
    marketplace_rows,
    money_str,
    verify_shape_matches_domain,
)
from bordereaux_reconciler.corpus.variants import VARIANTS, FileFormat, TotalMode, VariantSpec

__all__ = ["MIN_HELD_OUT_VARIANTS", "DeterminismReport", "build_corpus", "verify_determinism"]

#: ADR-001's hold-out contract: at least four whole variants, never rows.
MIN_HELD_OUT_VARIANTS: Final = 4

#: The canonical field prefix that marks a mapped attribute, e.g. `attributes.peril`.
_ATTRIBUTE_PREFIX: Final = "attributes."

#: Where minted keys start, relative to a variant's row count. Far enough past the last generated
#: ordinal that a case which adds rows can never collide with one that is already there.
_MINT_OFFSET: Final = 1_000

#: Marketplace order numbers start here, so an order reference never looks like a row index.
_ORDER_BASE: Final = 10_000_000


@dataclass(frozen=True, slots=True)
class DeterminismReport:
    """What two full builds from the same seed actually produced. A diff, not an assertion."""

    runs: int
    files_compared: int
    differing_files: tuple[str, ...]
    digests: tuple[str, ...]

    @property
    def identical(self) -> bool:
        return not self.differing_files and len(set(self.digests)) == 1


def build_corpus(out_dir: Path, *, seed: int = SEED) -> dict[str, object]:
    """Generate every variant under `out_dir` and write the manifest. Returns the manifest."""
    verify_shape_matches_domain()
    unseen_headers = verify_split(VARIANTS, minimum=MIN_HELD_OUT_VARIANTS)

    records: list[VariantRecord] = []
    hashes: dict[str, str] = {}
    seen_cases: set[str] = set()

    for spec in VARIANTS:
        rows, case_records = _rows_for(spec, seed)
        seen_cases.update(record.case for record in case_records)
        _write_source(out_dir, spec, rows, seed)
        _write_truth(out_dir, spec, rows, case_records, seed)

        for relative in (spec.path, spec.truth_path):
            hashes[relative] = _sha256(out_dir / relative)
        records.append(
            VariantRecord(
                spec=spec,
                rows=len(rows),
                source_sha256=hashes[spec.path],
                truth_sha256=hashes[spec.truth_path],
                adversarial=tuple(record.case for record in case_records),
            )
        )

    manifest = assemble(
        records, hashes=hashes, adversarial=sorted(seen_cases), unseen_headers=unseen_headers
    )
    write_json(out_dir / "manifest.json", manifest)
    return manifest


def _rows_for(spec: VariantSpec, seed: int) -> tuple[list[TruthRow], list[cases.CaseRecord]]:
    """This variant's canonical rows and its adversarial records. Nothing about its file format."""
    if spec.unmappable:
        # No canonical rows at all: the correct outcome for this file is a quarantine, and a ground
        # truth would be grading the system on a question it is supposed to refuse to answer.
        return [], []

    rng = FixtureRandom(seed, GENERATOR_VERSION, spec.variant_id)
    rows, records = _apply_cases(spec, _base_rows(spec, rng), rng)
    located = [cases.locate(rows, record) for record in records]
    return _project(spec, rows), located


def _base_rows(spec: VariantSpec, rng: FixtureRandom) -> list[TruthRow]:
    if spec.family == "insurance":
        return insurance_rows(rng, count=spec.rows, currency=spec.currency, period=spec.period)
    return marketplace_rows(
        rng, count=spec.rows, currency=spec.currency, period=spec.period, refunds=spec.refunds
    )


def _apply_cases(
    spec: VariantSpec, rows: list[TruthRow], rng: FixtureRandom
) -> tuple[list[TruthRow], list[cases.CaseRecord]]:
    """Row-level cases in catalogue order, then the schema-level ones.

    Catalogue order rather than any other order, because injections shift each other's positions: a
    different order would produce a corpus that is equally valid and not byte-identical, and the
    point of a committed generator is that there is one answer.
    """
    mint = _minter(spec)
    records: list[cases.CaseRecord] = []
    for case in spec.adversarial:
        if case in cases.ROW_LEVEL_CASES:
            rows, record = cases.inject(case, rows, rng, mint)
            records.append(record)
        else:
            records.append(cases.schema_case(case, _case_headers(spec, case), _case_detail(spec)))
    return rows, records


def _minter(spec: VariantSpec) -> Callable[[], str]:
    """Fresh keys for the cases that add rows, numbered past the file so they cannot collide."""
    base = _ORDER_BASE if spec.family != "insurance" else 0
    ordinals = itertools.count(base + spec.rows + _MINT_OFFSET)
    make = insurance_key if spec.family == "insurance" else marketplace_key
    return lambda: make(spec.period, next(ordinals))


def _case_headers(spec: VariantSpec, case: str) -> tuple[str, ...]:
    """Which headers a schema-level case is about.

    For a split deduction it is the two columns that share a canonical field; for a swapped or
    ambiguous pair it is the money columns, because the confusion is between them; otherwise the
    case *is* the vocabulary, so it is the whole header row.
    """
    if case == "split_commission":
        return tuple(c.header for c in spec.columns if c.field == "deductions.commission")
    if case in {"swapped_gross_net", "tax_included_vs_excluded"}:
        return tuple(c.header for c in spec.columns if c.producer.startswith("money:"))
    return tuple(c.header for c in spec.columns)


def _case_detail(spec: VariantSpec) -> dict[str, str]:
    return {
        "variant": spec.variant_id,
        "true_mapping_published_in": spec.truth_path,
        "unmapped_headers": ", ".join(spec.unmapped_headers) or "none",
    }


def _project(spec: VariantSpec, rows: list[TruthRow]) -> list[TruthRow]:
    """Reduce each row to the fields this variant's file actually carries.

    The attribute set comes from the columns, not from the family: two insurance variants with
    different column sets have different ground truths, and a truth claiming a coverholder name the
    file never contained would mark a correct mapping wrong.
    """
    mapped = {
        column.field.removeprefix(_ATTRIBUTE_PREFIX)
        for column in spec.columns
        if column.field is not None and column.field.startswith(_ATTRIBUTE_PREFIX)
    }
    return [row.with_attributes(frozenset(mapped), keep_net=spec.carries_net) for row in rows]


def _write_source(out_dir: Path, spec: VariantSpec, rows: list[TruthRow], seed: int) -> None:
    if spec.unmappable:
        rng = FixtureRandom(seed, GENERATOR_VERSION, spec.variant_id, "opaque")
        headers, cells = opaque_table(spec, rng)
    else:
        headers, cells = source_table(spec, rows)

    path = out_dir / spec.path
    if spec.file_format is FileFormat.XLSX:
        write_xlsx(path, spec.sheet_name, headers, cells)
    else:
        write_csv(path, headers, cells, bom=spec.byte_order_mark)


def _write_truth(
    out_dir: Path,
    spec: VariantSpec,
    rows: list[TruthRow],
    records: list[cases.CaseRecord],
    seed: int,
) -> None:
    """The ground truth, beside the file it describes. Every amount a string; `net` may be null."""
    payload: dict[str, object] = {
        "variant_id": spec.variant_id,
        "family": spec.family,
        "is_synthetic": True,
        "generator_version": GENERATOR_VERSION,
        "generated_at": GENERATED_AT,
        "seed": seed,
        "source_file": spec.path,
        "sheet_name": spec.sheet_name,
        "currency": str(spec.currency),
        "period": spec.period,
        "decimal_convention": str(spec.decimal),
        "date_format": str(spec.dates),
        "period_format": str(spec.periods),
        "currency_style": str(spec.currency_style),
        "expected_outcome": "quarantine" if spec.unmappable else "reconcile",
        "column_mapping": spec.column_mapping,
        "unmapped_headers": list(spec.unmapped_headers),
        "declared_total": _declared_total(spec, rows),
        "adversarial_cases": [record.to_json() for record in records],
        "rows": [row.to_json() for row in rows],
    }
    write_json(out_dir / spec.truth_path, payload)


def _declared_total(spec: VariantSpec, rows: list[TruthRow]) -> dict[str, object] | None:
    """What the footer says, what the rows say, and whether the two agree.

    Published as two figures rather than as a verdict, because ADR-001 treats a total that
    disagrees with its own rows and a total that agrees while the rows are wrong as two different
    findings, and a single boolean would collapse them into one.
    """
    if spec.total is TotalMode.NONE or not rows:
        return None
    counted = rows[:-1] if spec.total is TotalMode.DISAGREES else rows
    declared = sum((row.gross for row in counted), Decimal(0))
    actual = sum((row.gross for row in rows), Decimal(0))
    return {
        "declared_gross": money_str(declared),
        "rows_sum_gross": money_str(actual),
        "agrees_with_rows": declared == actual,
        "omitted_key": rows[-1].key if spec.total is TotalMode.DISAGREES else None,
        "footer_row_follows_the_data": True,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_determinism(*, seed: int = SEED, runs: int = 2) -> DeterminismReport:
    """Build the corpus `runs` times into separate directories and compare every byte.

    The corpus digest alone would detect a difference, but it would not say *which* file moved, and
    a determinism failure nobody can localise is a determinism failure nobody will fix. So the
    comparison is file by file and the report names the offenders.
    """
    workspace = Path(tempfile.mkdtemp(prefix="bdx-corpus-determinism-"))
    try:
        roots: list[Path] = []
        digests: list[str] = []
        for index in range(runs):
            root = workspace / f"run{index}"
            digests.append(str(build_corpus(root, seed=seed)["corpus_digest"]))
            roots.append(root)

        reference = roots[0]
        names = sorted(
            path.relative_to(reference).as_posix()
            for path in reference.rglob("*")
            if path.is_file()
        )
        differing = {
            name
            for name in names
            for other in roots[1:]
            if not (other / name).is_file()
            or (other / name).read_bytes() != (reference / name).read_bytes()
        }
        return DeterminismReport(
            runs=runs,
            files_compared=len(names),
            differing_files=tuple(sorted(differing)),
            digests=tuple(digests),
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
