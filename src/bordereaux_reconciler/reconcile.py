"""The deterministic reconciliation engine. No model, no adapter, no domain vocabulary.

This module imports :mod:`bordereaux_reconciler.domain` and :mod:`bordereaux_reconciler.money` and
nothing else from the project. In particular it **does not import the provider package**, so there
is no call path from a model to a reconciliation status even for a caller who wanted one — ADR-001
says the model never decides a status, and an import graph is a stronger statement of that than a
docstring. `test_ai_boundary.py` asserts the graph.

**The order of the checks is the design**, and it is ordered by how much each one knows:

1. **Duplicates** first, because a key that appears twice on one side makes every later question
   ambiguous. Answering "does this row match?" when there are two of it is answering the wrong
   question.
2. **Presence** next: a row on one side only cannot have a discrepancy, it has an absence, and
   those are different findings with different remedies.
3. **Each side's own arithmetic** before the two sides are compared at all. A file whose `net`
   disagrees with its own `gross` minus deductions is internally inconsistent, and reconciling it
   against the ledger would be comparing a number to a number when one of them is already known to
   be wrong. That is a `REVIEW`: the engine can tell something is wrong and cannot tell which
   figure is the wrong one, which is exactly the situation a person is for.
4. **Field comparison** last, and only then can a row be `MATCHED`.

**`MATCHED` is returned from exactly one place in this file**, guarded by "no discrepancy is outside
tolerance". Kill condition D — zero false matches — is a property of that single return statement,
which is why there is only one.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from bordereaux_reconciler.domain import (
    CanonicalRow,
    Discrepancy,
    Evidence,
    ReconciliationResult,
    Status,
)
from bordereaux_reconciler.money import EXACT, Currency, Money, Tolerance

__all__ = [
    "BatchTotals",
    "ReconciliationReport",
    "reconcile",
    "reconcile_totals",
]


class BatchTotals(BaseModel):
    """A file's declared total against the sum of its own rows.

    Reconciled **independently** of the row-level comparison against the ledger, because the two
    can disagree in either direction and each direction means something different:

    - rows agree with the ledger but the declared total does not → the coverholder's own footer is
      wrong, and the data is fine;
    - the declared total agrees but the rows do not → the total was computed from a different set
      of rows than the ones sent, which usually means a row was dropped in transit.

    A reconciler that only compared one of these would report the second case as clean.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    declared: Money | None
    computed: Money
    agrees: bool
    difference: Money | None
    rows_summed: int
    note: str


class ReconciliationReport(BaseModel):
    """Everything one reconciliation run produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: tuple[ReconciliationResult, ...]
    totals: BatchTotals | None = None
    tolerance: Tolerance

    def by_status(self) -> dict[str, int]:
        counts = Counter(r.status.value for r in self.results)
        return {status.value: counts.get(status.value, 0) for status in Status}

    def false_matched(self) -> tuple[ReconciliationResult, ...]:
        """Rows reported `MATCHED` with a discrepancy outside tolerance.

        ADR-001 kill condition D counts this and requires zero. It is computed here, from the
        results, rather than by the evaluator asking the engine whether it did well — a component
        that grades itself is not evidence.
        """
        return tuple(r for r in self.results if r.is_false_matched_candidate)


def _duplicates(rows: Iterable[CanonicalRow]) -> dict[str, list[CanonicalRow]]:
    grouped: dict[str, list[CanonicalRow]] = defaultdict(list)
    for row in rows:
        grouped[row.key].append(row)
    return grouped


def _compare_money(
    bordereau: CanonicalRow, ledger: CanonicalRow, tolerance: Tolerance
) -> tuple[list[Discrepancy], list[str]]:
    """Every money field on either side, compared. Returns discrepancies and one-sided field names.

    The **union** of field names, not the intersection. Reconciling only the fields both sides
    happen to carry would silently stop checking a deduction the moment one side stopped reporting
    it, which is the exact circumstance in which somebody should be told.

    A field present on one side only is *not* treated as a comparison against zero. "Absent" and
    "zero" are different claims — a missing commission column means nobody said what the commission
    was, not that it was nil — so it is returned separately and sends the row to `REVIEW`.
    """
    left, right = bordereau.money_fields(), ledger.money_fields()
    discrepancies: list[Discrepancy] = []
    one_sided: list[str] = []

    for field in sorted(set(left) | set(right)):
        if field not in left or field not in right:
            one_sided.append(field)
            continue
        difference = left[field] - right[field]
        discrepancies.append(
            Discrepancy(
                field=field,
                bordereau=left[field],
                ledger=right[field],
                difference=difference,
                within_tolerance=tolerance.accepts(field, difference),
                tolerance_applied=(
                    f"{tolerance.absolute} ({tolerance.reason})"
                    if tolerance.covers(field)
                    else "exact match required"
                ),
            )
        )
    return discrepancies, one_sided


def _reconcile_pair(
    bordereau: CanonicalRow, ledger: CanonicalRow, tolerance: Tolerance
) -> ReconciliationResult:
    """One matched pair of rows, once duplication and presence are already settled."""
    if bordereau.currency is not ledger.currency:
        # Not a mismatch of amount — a mismatch of unit. There is no FX rate source in this build,
        # and converting here would put a number nobody measured into the comparison.
        return ReconciliationResult(
            key=bordereau.key,
            status=Status.REVIEW,
            bordereau_row=bordereau,
            ledger_row=ledger,
            evidence=Evidence(
                rule="currency_disagreement",
                detail=(
                    f"the bordereau reports {bordereau.currency} and the ledger {ledger.currency}; "
                    "this build has no FX rate source, so a person decides rather than the engine "
                    "inventing a rate"
                ),
            ),
        )

    for side, row in (("bordereau", bordereau), ("ledger", ledger)):
        if row.internal_arithmetic_holds() is False:
            return ReconciliationResult(
                key=bordereau.key,
                status=Status.REVIEW,
                bordereau_row=bordereau,
                ledger_row=ledger,
                evidence=Evidence(
                    rule="internal_arithmetic",
                    detail=(
                        f"the {side} row does not agree with itself: net "
                        f"{row.net.value if row.net else '-'} against gross minus "
                        f"deductions {row.net_of_deductions()}. Comparing a figure that is already "
                        "known to be wrong would produce a confident answer about the wrong number."
                    ),
                ),
            )

    discrepancies, one_sided = _compare_money(bordereau, ledger, tolerance)

    if one_sided:
        return ReconciliationResult(
            key=bordereau.key,
            status=Status.REVIEW,
            bordereau_row=bordereau,
            ledger_row=ledger,
            evidence=Evidence(
                rule="field_present_on_one_side",
                detail=(
                    f"{', '.join(one_sided)} appears on one side only. Absent is not "
                    "the same claim "
                    "as zero, so the engine declines rather than assuming which was meant."
                ),
                discrepancies=tuple(discrepancies),
            ),
        )

    outside = [d for d in discrepancies if not d.within_tolerance]
    if outside:
        return ReconciliationResult(
            key=bordereau.key,
            status=Status.MISMATCH,
            bordereau_row=bordereau,
            ledger_row=ledger,
            evidence=Evidence(
                rule="field_difference",
                detail="; ".join(f"{d.field} differs by {d.difference}" for d in outside),
                discrepancies=tuple(discrepancies),
            ),
        )

    # The only place this function returns MATCHED. Kill condition D is a property of this guard:
    # every discrepancy reaching here is inside the declared tolerance, and `discrepancies` is
    # carried on the evidence so a reviewer sees what "inside tolerance" meant on this row.
    return ReconciliationResult(
        key=bordereau.key,
        status=Status.MATCHED,
        bordereau_row=bordereau,
        ledger_row=ledger,
        evidence=Evidence(
            rule="all_fields_agree",
            detail=(
                "every reconciled field agrees exactly"
                if all(d.difference.amount == 0 for d in discrepancies)
                else f"every reconciled field agrees within {tolerance.absolute}"
            ),
            discrepancies=tuple(discrepancies),
        ),
    )


def reconcile(
    bordereau: Sequence[CanonicalRow],
    ledger: Sequence[CanonicalRow],
    *,
    tolerance: Tolerance = EXACT,
) -> ReconciliationReport:
    """Reconcile a bordereau against the ledger, deterministically.

    Deterministic in the strong sense kill condition A tests: the result depends only on the two
    inputs and the tolerance, the keys are walked in sorted order, and nothing consults a clock, a
    random source or a set iteration order. Two runs over the same input produce identical statuses
    and identical evidence strings.
    """
    left, right = _duplicates(bordereau), _duplicates(ledger)
    results: list[ReconciliationResult] = []

    for key in sorted(set(left) | set(right)):
        mine, theirs = left.get(key, []), right.get(key, [])

        if len(mine) > 1 or len(theirs) > 1:
            side = "bordereau" if len(mine) > 1 else "ledger"
            results.append(
                ReconciliationResult(
                    key=key,
                    status=Status.DUPLICATE,
                    bordereau_row=mine[0] if mine else None,
                    ledger_row=theirs[0] if theirs else None,
                    evidence=Evidence(
                        rule="repeated_key",
                        detail=(
                            f"{max(len(mine), len(theirs))} rows share this key in the {side}. "
                            "Every later question about this row is ambiguous until that is "
                            "resolved, so none of them is asked."
                        ),
                        candidate_keys=(key,),
                    ),
                )
            )
            continue

        if not mine or not theirs:
            missing_from = "bordereau" if not mine else "ledger"
            present = (theirs or mine)[0]
            results.append(
                ReconciliationResult(
                    key=key,
                    status=Status.MISSING,
                    bordereau_row=mine[0] if mine else None,
                    ledger_row=theirs[0] if theirs else None,
                    evidence=Evidence(
                        rule="absent_on_one_side",
                        detail=(
                            f"present in the {'ledger' if not mine else 'bordereau'} and absent "
                            f"from the {missing_from}; {present.gross.value} is unaccounted for"
                        ),
                    ),
                )
            )
            continue

        results.append(_reconcile_pair(mine[0], theirs[0], tolerance))

    return ReconciliationReport(results=tuple(results), tolerance=tolerance)


def reconcile_totals(
    rows: Sequence[CanonicalRow],
    declared: Money | None,
    *,
    currency: Currency,
) -> BatchTotals:
    """A file's declared total against the sum of its own rows.

    The sum is computed at full precision and compared exactly. ADR-001 names computing a declared
    total as one of the two places rounding is allowed, and that rounding happens once, here, at the
    end — not per row, because rounding per row is how a total drifts a penny from its own detail
    and somebody loses an afternoon to it.
    """
    running = Money(amount=Decimal(0), currency=currency)
    for row in rows:
        running = running + row.gross.value

    if declared is None:
        return BatchTotals(
            declared=None,
            computed=running,
            agrees=True,
            difference=None,
            rows_summed=len(rows),
            note=(
                "the file declares no total, which is not a finding: several real layouts carry "
                "none, and inventing one to check would be checking our own arithmetic"
            ),
        )

    difference = declared - running
    return BatchTotals(
        declared=declared,
        computed=running,
        agrees=difference.amount == 0,
        difference=difference,
        rows_summed=len(rows),
        note=(
            "the declared total agrees with the sum of the rows sent"
            if difference.amount == 0
            else (
                f"the declared total is {difference} away from the sum of the {len(rows)} rows "
                "sent, which usually means a row was dropped between the coverholder's system and "
                "the file"
            )
        ),
    )
