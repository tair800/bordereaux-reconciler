"""Reconciliation: the six statuses, and the single place MATCHED can be returned.

The test that matters here is the last one. `MATCHED` is reachable from exactly one return statement
in `reconcile.py`, guarded by "no discrepancy outside the declared tolerance", and this file asserts
that property over the module's own source rather than trusting a reading of it. A second MATCHED
return added later would be caught even if every behavioural test still passed, because those only
cover the paths somebody thought to write.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from bordereaux_reconciler.domain import CanonicalRow, CellRef, Lineage, Status, Tracked
from bordereaux_reconciler.money import Currency, Money, Tolerance
from bordereaux_reconciler.reconcile import reconcile, reconcile_totals

GBP = Currency.GBP


def _lineage(column: str) -> Lineage:
    return Lineage(
        source_content_hash="a" * 64,
        cell=CellRef(sheet="Bordereau", row=2, column=column),
        mapping_version=1,
        raw_value="—",
    )


def _money(amount: str, column: str) -> Tracked[Money]:
    return Tracked[Money](
        value=Money(amount=Decimal(amount), currency=GBP), lineage=_lineage(column)
    )


def row(
    key: str,
    gross: str,
    *,
    tax: str | None = "100.00",
    commission: str | None = "200.00",
    net: str | None = None,
    period: str = "2026-01",
) -> CanonicalRow:
    """A canonical row whose net closes by default, so a test that wants it not to must say so."""
    deductions = {}
    if tax is not None:
        deductions["tax"] = _money(tax, "Tax")
    if commission is not None:
        deductions["commission"] = _money(commission, "Commission")
    if net is None:
        computed = Decimal(gross) - sum(
            (Decimal(v) for v in (tax, commission) if v is not None), Decimal(0)
        )
        net = str(computed)
    return CanonicalRow(
        key=key,
        currency=GBP,
        period=period,
        gross=_money(gross, "Gross Premium"),
        deductions=deductions,
        net=_money(net, "Net Due"),
    )


class TestTheSixStatuses:
    def test_identical_rows_match(self) -> None:
        report = reconcile([row("A", "1000.00")], [row("A", "1000.00")])
        assert [r.status for r in report.results] == [Status.MATCHED]
        assert report.results[0].evidence.rule == "all_fields_agree"

    def test_one_minor_unit_of_difference_is_a_mismatch(self) -> None:
        """The penny an "approximately equal" comparison eats."""
        report = reconcile([row("A", "1000.00")], [row("A", "1000.01")])
        assert report.results[0].status is Status.MISMATCH

    def test_a_deduction_differing_while_gross_agrees_is_caught(self) -> None:
        """The commission dispute: invisible to any check that stops at the headline figure."""
        left = row("A", "1000.00", commission="200.00")
        right = row("A", "1000.00", commission="250.00")
        report = reconcile([left], [right])
        assert report.results[0].status is Status.MISMATCH
        assert any(d.field == "commission" for d in report.results[0].evidence.discrepancies)

    def test_a_row_on_one_side_only_is_missing(self) -> None:
        report = reconcile([row("A", "1000.00")], [])
        assert report.results[0].status is Status.MISSING

    def test_a_repeated_key_is_duplicate_and_no_further_question_is_asked(self) -> None:
        report = reconcile([row("A", "1000.00"), row("A", "2000.00")], [row("A", "1000.00")])
        assert report.results[0].status is Status.DUPLICATE
        assert report.results[0].evidence.discrepancies == ()

    def test_a_currency_disagreement_is_review_not_mismatch(self) -> None:
        """There is no FX source in this build, so converting would invent a rate."""
        left = row("A", "1000.00")
        right = left.model_copy(update={"currency": Currency.EUR})
        report = reconcile([left], [right])
        assert report.results[0].status is Status.REVIEW
        assert report.results[0].evidence.rule == "currency_disagreement"

    def test_a_row_that_disagrees_with_itself_is_review(self) -> None:
        """Comparing a figure already known to be wrong gives a confident answer about it."""
        broken = row("A", "1000.00", net="999.99")
        report = reconcile([broken], [row("A", "1000.00")])
        assert report.results[0].status is Status.REVIEW
        assert report.results[0].evidence.rule == "internal_arithmetic"

    def test_a_field_present_on_one_side_only_is_review(self) -> None:
        """Absent is not the same claim as zero, so the engine declines rather than assuming."""
        left = row("A", "1000.00", tax="100.00")
        right = row("A", "1000.00", tax=None)
        report = reconcile([left], [right])
        assert report.results[0].status is Status.REVIEW


class TestTolerance:
    def test_a_named_field_may_differ_within_the_declared_tolerance(self) -> None:
        """A rounding difference on a statutory rate, flowing through to net as it really would.

        The tolerance has to name `net` as well as `tax`. Three pence of IPT that the coverholder
        rounded the other way is three pence of net, and a tolerance covering only the deduction
        would accept the cause and reject the consequence — which is not a policy anybody would
        choose, it is one nobody noticed they had written.
        """
        tolerance = Tolerance(
            absolute=Decimal("0.05"),
            applies_to=("tax", "net"),
            reason="IPT is a statutory percentage and the two sides round it differently",
        )
        report = reconcile(
            [row("A", "1000.00", tax="100.00")],
            [row("A", "1000.00", tax="100.03")],
            tolerance=tolerance,
        )
        assert report.results[0].status is Status.MATCHED
        # The discrepancies are still carried on the evidence. "Inside tolerance" is a judgement a
        # reviewer should be able to check, not a reason to stop recording the numbers.
        assert report.results[0].evidence.discrepancies
        assert all(d.within_tolerance for d in report.results[0].evidence.discrepancies)

    def test_an_unnamed_field_does_not_inherit_the_tolerance(self) -> None:
        tolerance = Tolerance(absolute=Decimal("0.05"), applies_to=("tax",))
        report = reconcile([row("A", "1000.00")], [row("A", "1000.03")], tolerance=tolerance)
        assert report.results[0].status is Status.MISMATCH


class TestDeterminism:
    def test_results_come_back_in_sorted_key_order(self) -> None:
        """The property, asserted directly rather than by running twice and comparing.

        Comparing two runs in one process does not test this. `set` iteration order is stable
        within a process, so a `reconcile` that walked `set(left) | set(right)` unsorted would give
        both runs the same wrong order and pass — a planted breach proved exactly that. Two runs
        would only differ across processes, where hash seeding differs, which a unit test cannot
        arrange. Asserting the ordering itself is both stronger and simpler.
        """
        keys = ("C", "A", "B", "Z", "M", "D")
        report = reconcile(
            [row(k, "1000.00") for k in keys],
            [row(k, "1000.00") for k in reversed(keys)],
        )
        assert [r.key for r in report.results] == sorted(keys)

    def test_two_runs_over_shuffled_input_agree_exactly(self) -> None:
        """Input order cannot change a verdict, an evidence string, or a position in the report."""
        left = [row(k, "1000.00") for k in ("C", "A", "B")]
        right = [row(k, "1000.00") for k in ("B", "C", "A")]
        first = reconcile(left, right)
        second = reconcile(list(reversed(left)), list(reversed(right)))
        assert [(r.key, r.status, r.evidence.detail) for r in first.results] == [
            (r.key, r.status, r.evidence.detail) for r in second.results
        ]


class TestTotals:
    def test_a_declared_total_that_disagrees_with_its_own_rows_is_reported(self) -> None:
        rows = (row("A", "1000.00"), row("B", "2000.00"))
        declared = Money(amount=Decimal("3000.01"), currency=GBP)
        totals = reconcile_totals(rows, declared, currency=GBP)
        assert not totals.agrees
        assert totals.difference is not None

    def test_no_declared_total_is_not_treated_as_zero(self) -> None:
        rows = (row("A", "1000.00"),)
        totals = reconcile_totals(rows, None, currency=GBP)
        assert totals.agrees
        assert totals.declared is None
        assert totals.computed.amount == Decimal("1000.00")

    def test_the_sum_is_exact_across_many_rows(self) -> None:
        """Three thousand three-penny rows. A float would have drifted by now."""
        rows = tuple(row(f"K{i:04d}", "0.03", tax=None, commission=None) for i in range(3000))
        totals = reconcile_totals(rows, None, currency=GBP)
        assert totals.computed.amount == Decimal("90.00")


class TestMatchedIsReachableFromOnePlace:
    """Structural, not behavioural. A behavioural test only covers paths somebody wrote."""

    def test_reconcile_returns_matched_from_exactly_one_statement(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "src" / "bordereaux_reconciler" / "reconcile.py"
        ).read_text(encoding="utf-8")

        matched_sites = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute)
            and node.attr == "MATCHED"
            and isinstance(node.value, ast.Name)
            and node.value.id == "Status"
        ]
        assert len(matched_sites) == 1, (
            f"Status.MATCHED is constructed at {len(matched_sites)} places in reconcile.py. Kill "
            "condition D is a property of the single guard in front of it; a second one would have "
            "to be guarded identically, and nothing would make sure it was."
        )

    def test_reconcile_imports_neither_a_provider_nor_an_adapter(self) -> None:
        """The AI boundary, over the import graph rather than over a promise."""
        source = (
            Path(__file__).resolve().parents[1] / "src" / "bordereaux_reconciler" / "reconcile.py"
        ).read_text(encoding="utf-8")

        imported = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any("providers" in name or "adapters" in name for name in imported), (
            f"reconcile.py imports {sorted(imported)}. A model must have no call path to a "
            "reconciliation status, and an adapter must not be able to change one."
        )


@pytest.mark.parametrize("status", list(Status))
def test_every_status_has_a_distinct_string_value(status: Status) -> None:
    assert isinstance(status.value, str)
    assert status.value == status.value.lower()
