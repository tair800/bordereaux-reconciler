"""Reading the corpus, and building the carrier's side of the reconciliation.

A bordereau reconciles **against something**. The corpus gives one side — the file a coverholder
sends — and this module builds the other: the carrier's own record of what those policies should
say, which in the real product comes from a policy administration system or from cash actually
received.

The carrier's ledger starts as the ground truth and then has a **declared set of discrepancies
injected into it**, deterministically, from the corpus seed. That injection is what makes kill
condition D measurable rather than circular. Without it, the carrier side would be identical to the
truth, every row would legitimately be MATCHED, and "zero false MATCHED" would be a statement about
a test that could not fail. With it, the evaluation knows the status every single row *must*
produce, before the reconciler is asked, and a row that comes back MATCHED when the injection says
it cannot be is an unambiguous false positive on money.

Each injection is also chosen to be the kind that actually happens:

- a premium out by **one minor unit** — the transposition, the rounding convention, the penny that
  an "approximately equal" comparison eats and an exact one catches;
- a premium out materially — the wrong policy version, the mid-term adjustment nobody applied;
- a deduction changed while the gross agrees — the commission dispute, which is the single most
  common real disagreement in delegated authority and is invisible to any check that looks only at
  the headline figure;
- a row on one side and not the other, in both directions — the late addition and the cancellation;
- a carrier row whose own net no longer equals its gross minus its deductions, which must produce
  REVIEW rather than either verdict: comparing a figure already known to be wrong would give a
  confident answer about the wrong number.

The first five injections keep the carrier's row **internally consistent** — moving a gross moves
the net with it, and moving a deduction moves the net the other way. That is both realistic and
necessary: an inconsistent row is one the reconciler is right to refuse, so injecting one by
accident would have measured the arithmetic guard while appearing to measure the comparison. The
sixth breaks that consistency on purpose, and expects exactly that refusal.

Nothing here is random at run time. :class:`~bordereaux_reconciler.corpus.rng.FixtureRandom` is
seeded from the variant id, so the same variant gets the same injections on every machine and on
every run, which kill condition A then has something to be true about.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from bordereaux_reconciler.adapters import KEY, PERIOD, Adapter
from bordereaux_reconciler.corpus.rng import FixtureRandom
from bordereaux_reconciler.domain import CanonicalRow, CellRef, Lineage, Status, Tracked
from bordereaux_reconciler.ingest.canonical import CoverholderProfile
from bordereaux_reconciler.money import MINOR_UNITS, Currency, DecimalConvention, Money

__all__ = [
    "INJECTION_KINDS",
    "CarrierLedger",
    "Injection",
    "VariantCase",
    "build_carrier_ledger",
    "load_cases",
]

#: What each injection kind must produce. The reconciler is never shown this; the evaluation
#: compares against it afterwards, which is the only ordering that makes it a test.
INJECTION_KINDS: Final[dict[str, Status]] = {
    "gross_off_by_one_minor_unit": Status.MISMATCH,
    "gross_materially_different": Status.MISMATCH,
    "deduction_changed_gross_agrees": Status.MISMATCH,
    "absent_from_carrier": Status.MISSING,
    "carrier_only": Status.MISSING,
    "carrier_row_disagrees_with_itself": Status.REVIEW,
}

#: How many rows of each kind are injected per variant. Small relative to the file, as real
#: discrepancy rates are — an evaluation where a third of the rows disagree measures a situation
#: nobody is in, and makes a reconciler that flags everything look good.
_PER_KIND: Final = 3

#: The date orders that mean day-before-month. Declared per coverholder, never sniffed from values.
_DAY_FIRST_FORMATS: Final = frozenset({"DD/MM/YYYY", "D Month YYYY"})

#: A carrier ledger row is not read from a spreadsheet, so its lineage names the system that
#: produced it rather than a cell. The field is not optional and should not be: a ledger value whose
#: origin is unrecorded is exactly what this project refuses to allow on the bordereau side, and
#: exempting the other side would be a hole in the same claim.
_CARRIER_SHEET: Final = "carrier_system_of_record"


@dataclass(frozen=True)
class Injection:
    """One deliberate disagreement, and the status it obliges the reconciler to report."""

    key: str
    kind: str
    expected: Status
    detail: str


@dataclass(frozen=True)
class CarrierLedger:
    """The carrier's side, plus the answer key the evaluation scores against."""

    rows: tuple[CanonicalRow, ...]
    injections: tuple[Injection, ...]

    @property
    def expected_status(self) -> dict[str, Status]:
        return {injection.key: injection.expected for injection in self.injections}


@dataclass(frozen=True)
class VariantCase:
    """One corpus variant: the file, its declared conventions, and its ground truth."""

    variant_id: str
    family: str
    held_out: bool
    unmappable: bool
    source: Path
    sheet: str
    profile: CoverholderProfile
    #: Source header -> the truth's canonical path (`gross`, `deductions.tax`, `attributes.peril`).
    column_mapping: dict[str, str]
    truth_rows: tuple[dict[str, Any], ...]
    declared_total: str | None
    adversarial_cases: tuple[str, ...]

    def identity(self, truth_row: dict[str, Any], adapter: Adapter) -> str:
        """The reconciliation key, computed by **the adapter**, for both sides.

        The corpus records a policy reference; the insurance adapter reconciles on policy reference
        *and* period, because the same policy recurs monthly with a different premium as
        endorsements are written. If the evaluation built the carrier's side on the bare reference
        while the pipeline built the bordereau's on the composite, the two sides would share no key
        at all — every row would come back MISSING, and "zero false MATCHED" would be true of a run
        that reconciled nothing. Asking the adapter is the only way the two sides cannot drift.
        """
        by_role = {f.role: f.name for f in adapter.fields}
        return adapter.identity(
            {
                by_role.get(KEY, "key"): truth_row["key"],
                by_role.get(PERIOD, "period"): truth_row["period"],
            }
        )

    def truth_by_key(self, adapter: Adapter) -> dict[str, dict[str, Any]]:
        return {self.identity(row, adapter): row for row in self.truth_rows}

    def duplicate_keys(self, adapter: Adapter) -> frozenset[str]:
        """Identities the file repeats. The reconciler owes DUPLICATE on these, not a guess."""
        seen: set[str] = set()
        repeated: set[str] = set()
        for row in self.truth_rows:
            key = self.identity(row, adapter)
            if key in seen:
                repeated.add(key)
            seen.add(key)
        return frozenset(repeated)


def _profile_from(truth: dict[str, Any]) -> CoverholderProfile:
    return CoverholderProfile(
        coverholder=truth["variant_id"],
        currency=Currency(truth["currency"]),
        convention=DecimalConvention(truth["decimal_convention"]),
        day_first=truth["date_format"] in _DAY_FIRST_FORMATS,
        mapping_version=1,
    )


def load_cases(corpus_dir: Path) -> tuple[dict[str, Any], tuple[VariantCase, ...]]:
    """The manifest and every variant in it, in manifest order."""
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    held_out = set(manifest["holdout"])
    unmappable = set(manifest["unmappable"])

    cases: list[VariantCase] = []
    for record in manifest["variants"]:
        variant_id = record["variant_id"]
        truth_path = corpus_dir / record["truth"]
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        cases.append(
            VariantCase(
                variant_id=variant_id,
                family=truth["family"],
                held_out=variant_id in held_out,
                unmappable=variant_id in unmappable,
                source=corpus_dir / truth["source_file"],
                sheet=truth["sheet_name"],
                profile=_profile_from(truth),
                column_mapping=dict(truth["column_mapping"]),
                truth_rows=tuple(truth["rows"]),
                declared_total=truth["declared_total"],
                adversarial_cases=tuple(c["case"] for c in truth["adversarial_cases"]),
            )
        )
    return manifest, tuple(cases)


def _money(raw: str, currency: Currency) -> Money:
    """A ground-truth amount. `Decimal(str)` because the truth stores strings for this reason."""
    return Money(amount=Decimal(raw), currency=currency)


def _lineage(variant_id: str, key: str, column: str) -> Lineage:
    return Lineage(
        source_content_hash=f"carrier:{variant_id}",
        cell=CellRef(sheet=_CARRIER_SHEET, row=1, column=column),
        mapping_version=1,
        raw_value=key,
    )


def _carrier_row(case: VariantCase, truth: dict[str, Any], adapter: Adapter) -> CanonicalRow:
    currency = case.profile.currency
    key = case.identity(truth, adapter)
    return CanonicalRow(
        key=key,
        currency=currency,
        period=truth["period"],
        gross=Tracked[Money](
            value=_money(truth["gross"], currency), lineage=_lineage(case.variant_id, key, "gross")
        ),
        deductions={
            name: Tracked[Money](
                value=_money(amount, currency),
                lineage=_lineage(case.variant_id, key, f"deductions.{name}"),
            )
            for name, amount in truth.get("deductions", {}).items()
        },
        net=(
            Tracked[Money](
                value=_money(truth["net"], currency), lineage=_lineage(case.variant_id, key, "net")
            )
            if truth.get("net")
            else None
        ),
    )


def _move_net(row: CanonicalRow, delta: Decimal) -> dict[str, Any]:
    """The update that keeps `net == gross - deductions` true after something moved."""
    if row.net is None:
        return {}
    moved = Money(amount=row.net.value.amount + delta, currency=row.currency)
    return {"net": row.net.model_copy(update={"value": moved})}


def _replace_gross(row: CanonicalRow, delta: Decimal, *, keep_consistent: bool) -> CanonicalRow:
    """Move the gross, and by default move the net with it.

    `keep_consistent=False` is the one injection that deliberately leaves the row disagreeing with
    itself, so that the arithmetic guard has something to catch.
    """
    moved = Money(amount=row.gross.value.amount + delta, currency=row.currency)
    update: dict[str, Any] = {"gross": row.gross.model_copy(update={"value": moved})}
    if keep_consistent:
        update |= _move_net(row, delta)
    return row.model_copy(update=update)


def _replace_first_deduction(row: CanonicalRow, delta: Decimal) -> tuple[CanonicalRow, str]:
    """Move a deduction up, and the net down by the same amount. Gross is untouched.

    This is the commission dispute, and it is the reason a reconciler may not stop at the headline
    figure: gross agrees to the penny on both sides, and the money actually due the carrier does
    not.
    """
    name = sorted(row.deductions)[0]
    tracked = row.deductions[name]
    moved = Money(amount=tracked.value.amount + delta, currency=row.currency)
    deductions = dict(row.deductions)
    deductions[name] = tracked.model_copy(update={"value": moved})
    update: dict[str, Any] = {"deductions": deductions}
    update |= _move_net(row, -delta)
    return row.model_copy(update=update), name


def build_carrier_ledger(case: VariantCase, adapter: Adapter) -> CarrierLedger:
    """The carrier's record, with a declared set of disagreements injected into it.

    Only rows with a unique key are eligible to carry an injection. Perturbing a row whose key the
    file already repeats would produce a status the reconciler could justify two ways — DUPLICATE
    because the key repeats, MISMATCH because the money moved — and an answer key that admits two
    answers is not one.
    """
    rows = {
        case.identity(truth, adapter): _carrier_row(case, truth, adapter)
        for truth in case.truth_rows
    }
    duplicates = case.duplicate_keys(adapter)
    rng = FixtureRandom(case.variant_id, "carrier_ledger")

    eligible = sorted(key for key in rows if key not in duplicates)
    injections: list[Injection] = []

    needed = _PER_KIND * len(INJECTION_KINDS)
    if len(eligible) < needed * 2:
        # Never injected into more than half a file. A variant too small to carry the full set gets
        # a proportionate one rather than a distorted discrepancy rate.
        needed = max(len(eligible) // 2, 0)
    per_kind = max(needed // len(INJECTION_KINDS), 1) if eligible else 0
    chosen = rng.sample(eligible, min(per_kind * len(INJECTION_KINDS), len(eligible)))

    for index, key in enumerate(chosen):
        kind = sorted(INJECTION_KINDS)[index % len(INJECTION_KINDS)]
        row = rows[key]

        if kind == "gross_off_by_one_minor_unit":
            delta = Decimal(1).scaleb(-MINOR_UNITS[case.profile.currency])
            rows[key] = _replace_gross(row, delta, keep_consistent=True)
            detail = f"carrier gross is {delta} above the bordereau's"
        elif kind == "gross_materially_different":
            delta = rng.cents(50_00, 900_00)
            rows[key] = _replace_gross(row, delta, keep_consistent=True)
            detail = f"carrier gross is {delta} above the bordereau's"
        elif kind == "carrier_row_disagrees_with_itself":
            if row.net is None:
                # `mkt_03_no_net_column` carries no net, so there is no internal arithmetic to
                # break and no REVIEW to demand. Injecting anyway would move the gross and then
                # expect REVIEW for a row that can only be a MISMATCH — the answer key would be
                # wrong, and the engine would be marked down for being right.
                continue
            rows[key] = _replace_gross(row, rng.cents(10_00, 300_00), keep_consistent=False)
            detail = "the carrier's own net no longer equals its gross minus its deductions"
        elif kind == "deduction_changed_gross_agrees":
            if not row.deductions:
                continue
            rows[key], name = _replace_first_deduction(row, rng.cents(5_00, 200_00))
            detail = f"gross agrees; the carrier's {name} does not"
        elif kind == "absent_from_carrier":
            del rows[key]
            detail = "in the bordereau, not in the carrier's ledger"
        else:  # carrier_only — the bordereau row is removed further down the pipeline
            detail = "in the carrier's ledger, not in the bordereau"

        injections.append(
            Injection(key=key, kind=kind, expected=INJECTION_KINDS[kind], detail=detail)
        )

    return CarrierLedger(
        rows=tuple(rows[key] for key in sorted(rows)), injections=tuple(injections)
    )
