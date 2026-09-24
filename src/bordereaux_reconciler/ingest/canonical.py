"""Turning a mapped grid into canonical rows that remember where every value came from.

This is where claim 1's second half is actually delivered: **every** value written here carries a
:class:`~bordereaux_reconciler.domain.Lineage` naming the file's content hash, the sheet, the
spreadsheet row a person would scroll to, the source header, and the mapping version in force. Kill
condition C walks those records and fails the build on any that is incomplete.

**A row that cannot be read exactly is quarantined, never coerced.** There is no "treat unparseable
as zero", no "assume the missing tax was nil", and no fallback convention. Each of those would
produce a ledger that balanced while being wrong, which is worse than a ledger that refused to
exist — somebody acts on the first and investigates the second.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from decimal import Decimal

from bordereaux_reconciler.adapters import DEDUCTION, GROSS, KEY, NET, PERIOD, Adapter
from bordereaux_reconciler.domain import CanonicalRow, CellRef, Lineage, Tracked
from bordereaux_reconciler.ingest.read import Grid
from bordereaux_reconciler.money import (
    Currency,
    DecimalConvention,
    Money,
    MoneyParseError,
    parse_amount,
)

__all__ = [
    "CoverholderProfile",
    "IngestOutcome",
    "QuarantinedRow",
    "build_canonical_rows",
    "declared_total_from",
    "normalise_period",
    "sum_gross",
]

#: The largest number that can be a month. A component above it can only be a day, whatever the
#: coverholder declared about order.
MAX_MONTH = 12

#: Two-digit years below this are read as 20xx. Bordereaux are not sent for 1926.
CENTURY_PIVOT = 100

#: Month names, for the layouts that write `March 2026` or `15 Mar 2026`. Lowercased prefixes,
#: because `Sept` and `Sep` both arrive and arguing about it helps nobody.
_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("jan",),
            ("feb",),
            ("mar",),
            ("apr",),
            ("may",),
            ("jun",),
            ("jul",),
            ("aug",),
            ("sep", "sept"),
            ("oct",),
            ("nov",),
            ("dec",),
        ),
        start=1,
    )
    for name in names
}


@dataclass(frozen=True)
class CoverholderProfile:
    """Everything about a coverholder that a file does not carry and cannot be inferred.

    All of these are **declared**, and that is the point. ADR-001 forbids inferring the decimal
    convention from the values, and the same argument applies to the date order: `03/04/26` is
    March in Boston and April in Bristol, and the file does not say which. A system that guessed
    would be right most of the time, which is the worst possible failure rate — frequent enough to
    be trusted, rare enough that nobody checks.
    """

    coverholder: str
    currency: Currency
    convention: DecimalConvention = DecimalConvention.DOT_DECIMAL
    #: `True` when the coverholder writes day before month. Declared, never sniffed.
    day_first: bool = True
    mapping_version: int = 1


@dataclass(frozen=True)
class QuarantinedRow:
    """A row that could not be made canonical, and exactly why.

    Carries the spreadsheet coordinate so the reason can be shown against the cell rather than as a
    line number in a log nobody opens.
    """

    spreadsheet_row: int
    reason: str
    raw_value: str = ""


@dataclass(frozen=True)
class IngestOutcome:
    """What one file produced: canonical rows, and the rows that did not make it."""

    rows: tuple[CanonicalRow, ...]
    quarantined: tuple[QuarantinedRow, ...] = ()
    declared_total: Money | None = None
    notes: tuple[str, ...] = dataclass_field(default_factory=tuple)

    @property
    def clean(self) -> bool:
        return not self.quarantined


def normalise_period(raw: str, *, day_first: bool) -> str | None:
    """A date cell as `YYYY-MM`, or `None` if it is not a date this build recognises.

    Returns the **month**, not the day, because a bordereau reconciles by reporting period and
    keeping the day would split one period into thirty. The day is still in the lineage's raw value
    if anybody needs it.
    """
    text = raw.strip()
    if not text:
        return None

    if match := re.match(r"^(\d{4})[-/.](\d{1,2})(?:[-/.]\d{1,2})?$", text):
        return f"{match.group(1)}-{int(match.group(2)):02d}"

    # `01/2026`. A month and a four-digit year, which is how a reporting period is written when it
    # is not written as `YYYY-MM`. Unambiguous because the trailing component has four digits, so no
    # declared date order is consulted: there is nothing to decide.
    #
    # This was missing while `profile.py` already recognised the same rendering as temporal, so the
    # mapper would correctly identify the period column and the canonicaliser would then quarantine
    # every row in the file. Two modules disagreeing about what a date is, in a system whose whole
    # subject is disagreement between two records.
    if match := re.match(r"^(\d{1,2})[-/.](\d{4})$", text):
        month = int(match.group(1))
        return f"{int(match.group(2)):04d}-{month:02d}" if 1 <= month <= MAX_MONTH else None

    if match := re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", text):
        first, second, year = (int(g) for g in match.groups())
        # A component over twelve can only be a day, whatever the declared order says. Believing
        # the declaration over arithmetic would turn `25/03/2026` into month 25.
        if first > MAX_MONTH:
            month = second
        elif second > MAX_MONTH:
            month = first
        else:
            month = second if day_first else first
        year = year + 2000 if year < CENTURY_PIVOT else year
        return f"{year:04d}-{month:02d}" if 1 <= month <= MAX_MONTH else None

    if match := re.match(r"^(?:\d{1,2}\s+)?([A-Za-z]{3,9})\s+(\d{4})$", text):
        word = match.group(1).lower()
        named = _MONTHS.get(word[:4]) or _MONTHS.get(word[:3])
        return f"{int(match.group(2)):04d}-{named:02d}" if named else None

    return None


@dataclass(frozen=True)
class _RowContext:
    """Everything constant across one file's rows, gathered once.

    This exists so the per-row builder can be a module-level function with explicit arguments
    rather than three closures over the loop variable. The closures worked, and ruff was still
    right to object: a nested function capturing an iteration variable is correct only while nobody
    defers it, and "correct until somebody stores it in a list" is the kind of latent bug that
    surfaces during a refactor months later.
    """

    grid: Grid
    profile: CoverholderProfile
    adapter: Adapter
    roles: dict[str, str]
    by_field: dict[str, str]
    gross_field: str
    period_field: str | None
    key_field: str | None
    net_field: str | None


def _lineage(context: _RowContext, canonical: str, raw: str, row_number: int) -> Lineage:
    return Lineage(
        source_content_hash=context.grid.content_hash,
        cell=CellRef(
            sheet=context.grid.sheet,
            row=row_number,
            column=context.by_field.get(canonical, canonical),
        ),
        mapping_version=context.profile.mapping_version,
        raw_value=raw,
    )


def _money(context: _RowContext, values: dict[str, str], canonical: str) -> Money | None:
    """One monetary cell, or `None` when the column is absent or the cell is blank.

    `None` means "nobody said", which is not the same as zero and is never turned into one. What an
    absent value means is the caller's decision for its own field; this function declines to make
    it on their behalf.
    """
    raw = values.get(canonical, "")
    if not raw.strip():
        return None
    return Money(
        amount=parse_amount(
            raw, currency=context.profile.currency, convention=context.profile.convention
        ),
        currency=context.profile.currency,
    )


def _build_row(
    context: _RowContext, values: dict[str, str], row_number: int
) -> CanonicalRow | QuarantinedRow:
    """One data row: canonical, or quarantined with the reason. Never coerced into existing."""
    period = (
        normalise_period(values.get(context.period_field, ""), day_first=context.profile.day_first)
        if context.period_field
        else None
    )
    if period is None:
        return QuarantinedRow(
            spreadsheet_row=row_number,
            reason=(
                f"the period cell {values.get(context.period_field or '', '')!r} is not a date "
                f"this build recognises under the declared "
                f"{'day-first' if context.profile.day_first else 'month-first'} order"
            ),
            raw_value=values.get(context.period_field or "", ""),
        )

    if context.key_field and not values.get(context.key_field, "").strip():
        return QuarantinedRow(
            spreadsheet_row=row_number,
            reason=f"{context.key_field} is empty, so the row has no identity to reconcile on",
        )

    try:
        gross_value = _money(context, values, context.gross_field)
        if gross_value is None:
            return QuarantinedRow(
                spreadsheet_row=row_number,
                reason=(
                    f"{context.gross_field} is empty; a row with no gross amount has nothing to "
                    "reconcile"
                ),
            )

        deductions: dict[str, Tracked[Money]] = {}
        for name, role in context.roles.items():
            if role != DEDUCTION or name not in context.by_field:
                continue
            amount = _money(context, values, name)
            if amount is not None:
                deductions[name] = Tracked[Money](
                    value=amount, lineage=_lineage(context, name, values[name], row_number)
                )

        net_value = (
            _money(context, values, context.net_field)
            if context.net_field and context.net_field in context.by_field
            else None
        )
    except MoneyParseError as exc:
        return QuarantinedRow(
            spreadsheet_row=row_number,
            reason=(
                f"{exc.reason}. The declared convention is {context.profile.convention}; reading "
                "it the other way would change the amount, so the row is held rather than guessed "
                "at"
            ),
            raw_value=exc.raw,
        )

    identity_values = dict(values)
    if context.period_field:
        identity_values[context.period_field] = period

    return CanonicalRow(
        key=context.adapter.identity(identity_values),
        currency=context.profile.currency,
        period=period,
        gross=Tracked[Money](
            value=gross_value,
            lineage=_lineage(context, context.gross_field, values[context.gross_field], row_number),
        ),
        deductions=deductions,
        net=(
            Tracked[Money](
                value=net_value,
                lineage=_lineage(context, context.net_field, values[context.net_field], row_number),
            )
            if net_value is not None and context.net_field
            else None
        ),
        attributes={
            name: Tracked[str](
                value=values[name], lineage=_lineage(context, name, values[name], row_number)
            )
            for name, role in context.roles.items()
            if role not in {GROSS, DEDUCTION, NET} and name in context.by_field and values.get(name)
        },
    )


def build_canonical_rows(
    grid: Grid,
    mapping: dict[str, str],
    adapter: Adapter,
    profile: CoverholderProfile,
) -> IngestOutcome:
    """Every data row of one sheet, as canonical rows with complete lineage.

    `mapping` is source header to canonical field, already confirmed or deterministically resolved.
    This function does not decide a mapping and cannot — it is the step *after* that decision, and
    keeping the two apart is what lets a confirmed mapping be replayed over an old file and produce
    exactly the values it produced the first time.
    """
    roles = {field.name: field.role for field in adapter.fields}
    by_field = {canonical: header for header, canonical in mapping.items()}

    context = _RowContext(
        grid=grid,
        profile=profile,
        adapter=adapter,
        roles=roles,
        by_field=by_field,
        gross_field=next(n for n, r in roles.items() if r == GROSS),
        period_field=next((n for n, r in roles.items() if r == PERIOD), None),
        key_field=next((n for n, r in roles.items() if r == KEY), None),
        net_field=next((n for n, r in roles.items() if r == NET), None),
    )

    rows: list[CanonicalRow] = []
    quarantined: list[QuarantinedRow] = []

    for index in range(len(grid.rows)):
        values = {canonical: grid.cell(index, header) for canonical, header in by_field.items()}
        if not any(values.values()):
            continue  # a blank line in the sheet, not a row

        built = _build_row(context, values, grid.spreadsheet_row(index))
        if isinstance(built, QuarantinedRow):
            quarantined.append(built)
        else:
            rows.append(built)

    return IngestOutcome(rows=tuple(rows), quarantined=tuple(quarantined))


def declared_total_from(grid: Grid, header: str, profile: CoverholderProfile) -> Money | None:
    """A footer total, if the sheet carries one.

    Read from the last non-empty cell in the column rather than from a row labelled `Total`,
    because the label is written a dozen ways and its position is not. A file with no footer
    returns `None`, which :func:`~bordereaux_reconciler.reconcile.reconcile_totals` treats as "no
    total declared" rather than as zero.
    """
    column = [v for v in grid.column(header) if v.strip()]
    if not column:
        return None
    try:
        return Money(
            amount=parse_amount(
                column[-1], currency=profile.currency, convention=profile.convention
            ),
            currency=profile.currency,
        )
    except MoneyParseError:
        return None


def sum_gross(rows: tuple[CanonicalRow, ...], currency: Currency) -> Money:
    """The sum of a file's own gross amounts, at full precision."""
    total = Money(amount=Decimal(0), currency=currency)
    for row in rows:
        total = total + row.gross.value
    return total
