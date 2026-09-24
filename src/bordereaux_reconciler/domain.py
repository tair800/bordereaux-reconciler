"""The types. Each one exists because a claim in ADR-001 needs it to be structurally true.

Three of them carry the weight.

:class:`Lineage` is on **every** canonical value, not on the row. Claim 1 says every canonical value
points back to the source cell and mapping version that produced it, and a row-level provenance
record cannot say which of eleven columns a disputed figure came from. Putting it on the value makes
the claim checkable cell by cell, which is what kill condition C counts.

:class:`CanonicalRow` holds :class:`~bordereaux_reconciler.money.Money`, never a number. A field
typed `Decimal` would let a float through a `float()` call somewhere; a field typed `Money` carries
its currency with it, so a GBP amount cannot be silently reconciled against a EUR one. Its money
slots are named `gross`, `deductions` and `net` rather than `premium`, `tax` and `commission`,
because claim 2 says the engine is domain-neutral and an engine whose field names only parse in one
industry has not been ported.

:class:`MappingProposal` is the **only** thing the model is allowed to produce, and it is
deliberately incapable of expressing a monetary value — there is no amount field on it and no free
-form payload. ADR-001 says the model never touches money; this type is that sentence made
unenforceable-by-accident rather than unenforced-by-policy.
"""

from __future__ import annotations

import enum
import hashlib

from pydantic import BaseModel, ConfigDict, Field

from bordereaux_reconciler.money import Currency, Money

__all__ = [
    "CanonicalRow",
    "CellRef",
    "Discrepancy",
    "Evidence",
    "Lineage",
    "MappingConfidence",
    "MappingContract",
    "MappingProposal",
    "ReconciliationResult",
    "Status",
    "UnmappedColumn",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Status(enum.StrEnum):
    """The six ADR-001 fixed, and no others.

    ``REVIEW`` is not a hedge and not a failure mode. It is the engine saying a person must decide,
    and ADR-001 fixes that nothing is written to the ledger while a row sits in it. A reconciler
    that must answer on every row answers wrongly on some, and in this domain a wrong answer is
    money.
    """

    MATCHED = "matched"
    MISMATCH = "mismatch"
    MISSING = "missing"
    DUPLICATE = "duplicate"
    AMBIGUOUS = "ambiguous"
    REVIEW = "review"


class MappingConfidence(enum.StrEnum):
    """How a source column came to be mapped.

    The distinction that matters is ``CONFIRMED`` versus everything else: only a human confirmation
    turns a proposal into deterministic configuration, and only deterministic configuration lets a
    later file from the same coverholder be reconciled with no model call at all.
    """

    #: A human confirmed it. It is now versioned configuration.
    CONFIRMED = "confirmed"
    #: Deterministic evidence alone was conclusive — header synonym plus value shape agreeing.
    DETERMINISTIC = "deterministic"
    #: A model proposed it and nobody has confirmed it yet. Cannot produce a ledger.
    PROPOSED = "proposed"
    #: Nothing was conclusive. Cannot produce a ledger.
    UNRESOLVED = "unresolved"


class CellRef(_Frozen):
    """Where in a source file a value came from.

    `sheet` is a name rather than an index because coverholders reorder tabs between months, and a
    lineage record that said "sheet 2" would quietly point at a different sheet next time.
    """

    sheet: str
    row: int = Field(description="1-based, as a spreadsheet numbers it, including the header row")
    column: str = Field(description="the source header text, verbatim, before any normalisation")

    def __str__(self) -> str:
        return f"{self.sheet}!{self.column}:{self.row}"


class Lineage(_Frozen):
    """The full provenance of one canonical value. Kill condition C counts the fields on this.

    `source_content_hash` is the hash of the **file**, not of the row: it is what makes re-ingestion
    idempotent and what lets a value be traced back to a specific delivery of a specific file, even
    after the coverholder sends a corrected version with the same name.
    """

    source_content_hash: str
    cell: CellRef
    mapping_version: int
    #: The cell exactly as it was read, before parsing. A disputed figure is argued about with this,
    #: not with the parsed value — the parse is the thing under dispute.
    raw_value: str

    @property
    def is_complete(self) -> bool:
        """Every field kill condition C requires, present and non-empty."""
        return bool(
            self.source_content_hash
            and self.cell.sheet
            and self.cell.row > 0
            and self.cell.column
            and self.mapping_version > 0
        )


class Tracked[T](_Frozen):
    """A value and where it came from.

    Generic so that a `Money` keeps its lineage without the lineage having to know about money, and
    so that a date or an identifier is tracked the same way. The alternative — a parallel dictionary
    of lineage keyed by field name — drifts the first time somebody adds a field.
    """

    value: T
    lineage: Lineage


class CanonicalRow(_Frozen):
    """One reconcilable line, in the shape the engine works on.

    **The money fields are `gross`, `deductions` and `net`, and that is the portability claim made
    structural.** An earlier draft named them `premium`, `tax` and `commission`, which is the
    insurance vocabulary wearing a neutral hat: a marketplace settlement has no premium, and an
    engine whose field names only make sense in one domain has not been ported, it has been
    renamed.

    The three slots are the arithmetic both families actually share:

    | | insurance bordereau | marketplace settlement |
    |---|---|---|
    | `gross` | gross written premium | gross sales |
    | `deductions` | `{"tax": IPT, "commission": ...}` | `{"fee": marketplace fee}` |
    | `net` | net due to the carrier | seller payout |

    Which gives the engine a rule worth having in both: **`net` must equal `gross` minus the sum of
    `deductions`**, checked exactly, on each side independently, before the two sides are compared
    at all. A file whose own arithmetic does not close is a finding regardless of what the ledger
    says.

    `deductions` is a mapping rather than fields so the engine never learns a domain's vocabulary —
    it sums the values and compares them by key. The adapter supplies the keys and their display
    labels.
    """

    #: The reconciliation key. Whatever the adapter declares identity to be.
    key: str
    currency: Currency
    period: str = Field(description="the reporting period, normalised to YYYY-MM")

    gross: Tracked[Money]
    deductions: dict[str, Tracked[Money]] = Field(default_factory=dict)
    net: Tracked[Money] | None = None

    #: Non-monetary canonical attributes the adapter mapped, each tracked. Kept as a mapping rather
    #: than as fields so the engine never needs to know a domain's vocabulary.
    attributes: dict[str, Tracked[str]] = Field(default_factory=dict)

    def money_fields(self) -> dict[str, Money]:
        """The fields reconciliation compares, named once so no caller can forget one."""
        fields = {"gross": self.gross.value}
        fields.update({name: t.value for name, t in self.deductions.items()})
        if self.net is not None:
            fields["net"] = self.net.value
        return fields

    def net_of_deductions(self) -> Money:
        """`gross` minus every deduction, computed exactly and without intermediate rounding.

        ADR-001 fixes that rounding happens only when a source cell is read and when a declared
        total is computed. This is neither, so it does not round: a sum that rounded at each step
        would drift from the file's own stated net by a penny and turn a clean row into an
        exception.
        """
        total = self.gross.value
        for deduction in self.deductions.values():
            total = total - deduction.value
        return total

    def internal_arithmetic_holds(self) -> bool | None:
        """Whether this row's own `net` agrees with its `gross` and deductions.

        `None` when the row declares no net, which is not a failure — several real layouts simply
        do not carry one, and inventing one to check would be checking our own arithmetic.
        """
        if self.net is None:
            return None
        return self.net.value == self.net_of_deductions()

    def lineage(self) -> list[Lineage]:
        """Every lineage record on this row. What kill condition C walks."""
        records = [self.gross.lineage]
        records += [t.lineage for t in self.deductions.values()]
        if self.net is not None:
            records.append(self.net.lineage)
        records += [t.lineage for t in self.attributes.values()]
        return records


class Discrepancy(_Frozen):
    """One field that did not agree, with both sides and the gap.

    `within_tolerance` is recorded even when it is `False`, so a row's evidence always says whether
    a tolerance was consulted. ADR-001 requires a non-zero tolerance to be visible on every row it
    touches, and a field that only appeared when it mattered would be invisible exactly when a
    reviewer is looking for it.
    """

    field: str
    bordereau: Money
    ledger: Money
    difference: Money
    within_tolerance: bool
    tolerance_applied: str


class Evidence(_Frozen):
    """Why a row got the status it got, in terms a person can argue with.

    A status without this is an assertion. `rule` names the deterministic rule that fired, and
    `detail` is the sentence a reviewer reads first.
    """

    rule: str
    detail: str
    discrepancies: tuple[Discrepancy, ...] = ()
    candidate_keys: tuple[str, ...] = ()


class ReconciliationResult(_Frozen):
    """The answer for one row, and everything that produced it."""

    key: str
    status: Status
    evidence: Evidence
    bordereau_row: CanonicalRow | None = None
    ledger_row: CanonicalRow | None = None

    @property
    def is_false_matched_candidate(self) -> bool:
        """`MATCHED` while a discrepancy is recorded outside tolerance.

        This is kill condition D's definition expressed on the object rather than in a script, so
        the evaluator and the engine cannot drift apart about what a false match is.
        """
        return self.status is Status.MATCHED and any(
            not d.within_tolerance for d in self.evidence.discrepancies
        )


class UnmappedColumn(_Frozen):
    """A source column nothing claimed, and what was considered.

    Carried rather than dropped, because a column nobody mapped is the most likely place for an
    unreported premium to be hiding, and a mapping UI that does not show it cannot be reviewed.
    """

    header: str
    reason: str
    considered: tuple[str, ...] = ()


class MappingProposal(_Frozen):
    """What the model is allowed to say. Nothing about money, by construction.

    There is no amount, no total, no tolerance and no status on this type, and there is no
    free-form payload through which one could travel. A provider arm returns these or it returns
    nothing; :mod:`bordereaux_reconciler.reconcile` does not import the provider package at all, so
    there is no path from a model to a ledger value even if a future caller wanted one.
    """

    source_header: str
    canonical_field: str
    confidence: MappingConfidence
    rationale: str = Field(max_length=400)

    #: Which arm produced it and where that arm ran. The residency record is attached to the
    #: proposal rather than logged separately so it cannot be lost between the two.
    provider: str = "deterministic"
    region: str = "local"


class MappingContract(_Frozen):
    """A confirmed, versioned mapping for one coverholder. Deterministic configuration.

    Versioned rather than mutable because ADR-001's replay story needs it: a period can be
    re-processed under a **new** mapping version and the old canonical values remain attributable to
    the old one. A contract that was edited in place would make last month's ledger unexplainable.
    """

    coverholder: str
    version: int = Field(ge=1)
    # A plain string, not `Literal["insurance", "marketplace"]`. It was the latter, and kill
    # condition G caught it: pinning the families here means a third adapter pack cannot be
    # added without editing the engine's own domain model, which is the precise thing claim 2
    # says is not true. The value is checked against the adapter registry at the boundary that
    # knows what adapters exist; this module deliberately does not.
    family: str = Field(min_length=1)
    #: source header -> canonical field.
    columns: dict[str, str]
    confirmed_by: str
    confirmed_at: str

    def digest(self) -> str:
        """A stable identity for this mapping, for lineage and for replay comparisons."""
        payload = "|".join(f"{k}={v}" for k, v in sorted(self.columns.items()))
        return hashlib.sha256(f"{self.coverholder}|{self.version}|{payload}".encode()).hexdigest()[
            :16
        ]
