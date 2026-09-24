"""The eleven named adversarial cases, each as something a failure report can point at.

ADR-001 names these by identifier, and the identifiers here are spelled exactly as it spells them —
the kill test compares the sets, so a near-miss like `duplicated_row` would fail the build rather
than silently under-report the corpus. That is the intended behaviour.

**Two kinds of case, and the difference matters.** Some are *rows*: a duplicate, a key conflict, a
transposition, an ambiguous date. Those are injected here and the record names the keys involved.
Others are *schemas*: a split deduction, a swapped gross and net, two tax bases in one file, an
abbreviated header set, a vocabulary nothing has seen. Those cannot be a row — the whole file is
the case — so the record names the headers instead. A corpus that only had row-level traps would
miss the ones that actually break schema mapping, which is the task with the model in it.

**Row indices are computed after every injection, never during.** Inserting a duplicate at index 40
shifts everything after it, so a record that remembered "40" while it was being made would point at
the wrong row by the time the next injector ran. :func:`locate` resolves keys to indices once, at
the end, against the final list.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from bordereaux_reconciler.corpus.rng import FixtureRandom
from bordereaux_reconciler.corpus.truth import TruthRow, money_str, quantise

__all__ = [
    "ROW_LEVEL_CASES",
    "SCHEMA_LEVEL_CASES",
    "CaseRecord",
    "inject",
    "locate",
    "schema_case",
]


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """One adversarial case, as the published ground truth records it."""

    case: str
    note: str
    keys: tuple[str, ...] = ()
    headers: tuple[str, ...] = ()
    rows: tuple[int, ...] = ()
    detail: Mapping[str, str] = field(default_factory=dict)
    #: What the carrier's ledger holds for these keys, when it differs from what the file says.
    #: Only the transposition case uses it, and only because that case is meaningless without it.
    intended: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "case": self.case,
            "note": self.note,
            "keys": list(self.keys),
            "headers": list(self.headers),
            "rows": list(self.rows),
            "detail": dict(self.detail),
            "intended": {k: dict(v) for k, v in self.intended.items()},
        }

    def with_rows(self, rows: tuple[int, ...]) -> CaseRecord:
        return CaseRecord(
            case=self.case,
            note=self.note,
            keys=self.keys,
            headers=self.headers,
            rows=rows,
            detail=self.detail,
            intended=self.intended,
        )


#: A row far enough into the file that a reader has stopped paying attention, which is where these
#: things are actually found. Fixed rather than random so the cases land somewhere predictable.
_TARGET_FRACTION: Final = 3

#: An injector takes the rows, a named random stream and a way to mint a fresh unused key, and
#: returns the rows it produced plus the record describing what it did.
type Injector = Callable[
    [list[TruthRow], FixtureRandom, Callable[[], str]], tuple[list[TruthRow], CaseRecord]
]


def _target(rows: Sequence[TruthRow], offset: int = 0) -> int:
    return len(rows) // _TARGET_FRACTION + offset


def _money(row: TruthRow) -> dict[str, str]:
    payload = {"gross": money_str(row.gross)}
    payload.update({name: money_str(value) for name, value in row.deductions.items()})
    if row.net is not None:
        payload["net"] = money_str(row.net)
    return payload


def _replace_money(
    row: TruthRow, *, gross: Decimal, deductions: Mapping[str, Decimal], net: Decimal | None
) -> TruthRow:
    return TruthRow(
        key=row.key,
        currency=row.currency,
        period=row.period,
        gross=gross,
        deductions=dict(deductions),
        net=net,
        attributes=dict(row.attributes),
    )


def _duplicate_row(
    rows: list[TruthRow], rng: FixtureRandom, mint: Callable[[], str]
) -> tuple[list[TruthRow], CaseRecord]:
    """The same line sent twice, byte for byte.

    The benign explanation — a coverholder pasted a block twice — and the expensive one — the same
    premium banked twice — look identical in the file, which is why ADR-001 gives `DUPLICATE` its
    own status instead of folding it into a match.
    """
    del rng, mint
    index = _target(rows)
    original = rows[index]
    return (
        [*rows[: index + 1], original, *rows[index + 1 :]],
        CaseRecord(
            case="duplicate_row",
            note="an exact repeat of the preceding line, every field identical including the key",
            keys=(original.key,),
            detail=_money(original),
        ),
    )


def _same_policy_different_premium(
    rows: list[TruthRow], rng: FixtureRandom, mint: Callable[[], str]
) -> tuple[list[TruthRow], CaseRecord]:
    """One key, two different amounts. Not a duplicate — a contradiction.

    Nothing in the file says which line supersedes the other, and a reconciler that picked one
    would be inventing an answer. ADR-001 has `AMBIGUOUS` for exactly this.
    """
    del mint
    index = _target(rows, offset=5)
    original = rows[index]
    premium = rng.cents(25_000, 4_800_000)
    tax = quantise(premium * Decimal("0.12"), original.currency)
    commission = quantise(premium * Decimal("0.15"), original.currency)
    gross = premium + tax
    conflicting = _replace_money(
        original,
        gross=gross,
        deductions={"tax": tax, "commission": commission},
        net=gross - tax - commission,
    )
    return (
        [*rows[: index + 1], conflicting, *rows[index + 1 :]],
        CaseRecord(
            case="same_policy_different_premium",
            note="the same policy reference twice with different money and nothing to say which "
            "line supersedes the other",
            keys=(original.key,),
            detail={
                "first_gross": money_str(original.gross),
                "second_gross": money_str(conflicting.gross),
            },
        ),
    )


def _same_amount_different_policy(
    rows: list[TruthRow], rng: FixtureRandom, mint: Callable[[], str]
) -> tuple[list[TruthRow], CaseRecord]:
    """Two different policies carrying identical money.

    A legitimate coincidence, and the trap for any matcher that falls back to amount when the key
    does not line up. It must stay two rows.
    """
    del rng
    index = _target(rows, offset=11)
    original = rows[index]
    twin = TruthRow(
        key=mint(),
        currency=original.currency,
        period=original.period,
        gross=original.gross,
        deductions=dict(original.deductions),
        net=original.net,
        attributes=dict(original.attributes),
    )
    return (
        [*rows[: index + 1], twin, *rows[index + 1 :]],
        CaseRecord(
            case="same_amount_different_policy",
            note="two distinct policies with identical gross, tax, commission and net — a "
            "coincidence, and the reason amount is never an identity",
            keys=(original.key, twin.key),
            detail=_money(original),
        ),
    )


def _totals_agree_rows_do_not(
    rows: list[TruthRow], rng: FixtureRandom, mint: Callable[[], str]
) -> tuple[list[TruthRow], CaseRecord]:
    """Two policies holding each other's money.

    Every column sums to what it should, so a file-level total check passes and reports nothing.
    Only a row-level comparison against the ledger finds it — which is why ADR-001 reconciles the
    declared total against the file's own rows *independently* of the row reconciliation, and why
    the corpus carries this case and its mirror image both.

    `intended` publishes what the carrier's ledger holds for the two keys, because the file alone
    cannot express "these are the wrong way round" — by construction it looks correct.
    """
    del rng, mint
    first, second = _target(rows), _target(rows, offset=7)
    a, b = rows[first], rows[second]
    swapped = list(rows)
    swapped[first] = _replace_money(a, gross=b.gross, deductions=b.deductions, net=b.net)
    swapped[second] = _replace_money(b, gross=a.gross, deductions=a.deductions, net=a.net)
    return (
        swapped,
        CaseRecord(
            case="totals_agree_rows_do_not",
            note="the two policies' amounts are transposed; every column total is unchanged, so "
            "the declared total reconciles and both rows are wrong",
            keys=(a.key, b.key),
            detail={"declared_total": "agrees with the file's own rows"},
            intended={a.key: _money(a), b.key: _money(b)},
        ),
    )


def _localised_date(
    rows: list[TruthRow], rng: FixtureRandom, mint: Callable[[], str]
) -> tuple[list[TruthRow], CaseRecord]:
    """A date that is valid under DD/MM and under MM/DD, with two different answers.

    `04/03/2026` is the fourth of March or the third of April and the cell cannot tell you which.
    The variant declares its format; a reader that guessed would be a month out on a policy
    inception, which moves a premium into the wrong reporting period.
    """
    del rng, mint
    index = _target(rows, offset=3)
    original = rows[index]
    year, month = original.period.split("-")
    iso = f"{year}-{month}-04"
    attributes = dict(original.attributes)
    date_attribute = "inception_date" if "inception_date" in attributes else "settlement_date"
    attributes[date_attribute] = iso
    adjusted = TruthRow(
        key=original.key,
        currency=original.currency,
        period=original.period,
        gross=original.gross,
        deductions=dict(original.deductions),
        net=original.net,
        attributes=attributes,
    )
    return (
        [*rows[:index], adjusted, *rows[index + 1 :]],
        CaseRecord(
            case="localised_date",
            note="a date whose day and month are both below thirteen, so it parses under either "
            "convention and means a different month in each",
            keys=(original.key,),
            detail={"iso": iso, "reads_as_under_mdy": f"{year}-04-{month}"},
        ),
    )


#: The cases that are rows. Order is fixed, because injections shift each other's indices and a
#: different order would produce a different — equally valid, but not byte-identical — corpus.
ROW_LEVEL_CASES: Final[dict[str, Injector]] = {
    "duplicate_row": _duplicate_row,
    "same_policy_different_premium": _same_policy_different_premium,
    "same_amount_different_policy": _same_amount_different_policy,
    "totals_agree_rows_do_not": _totals_agree_rows_do_not,
    "localised_date": _localised_date,
}

#: The cases that are whole schemas. There is no row to point at, so the record points at headers.
SCHEMA_LEVEL_CASES: Final[dict[str, str]] = {
    "split_commission": "one canonical deduction arrives as two source columns; mapping either "
    "one alone under-reports the commission and the row arithmetic still closes on the wrong "
    "number",
    "swapped_gross_net": "the header named for the premium holds the remittance and the header "
    "named for the total holds the gross, so header text and meaning point opposite ways",
    "tax_included_vs_excluded": "both a tax-inclusive and a tax-exclusive premium column are "
    "present; only the inclusive one is the canonical gross and the other maps to nothing",
    "abbreviated_header": "every header is contracted to three or four characters, which is "
    "beneath what a normalised or fuzzy string match can resolve",
    "unseen_synonym": "no header in this file appears anywhere else in the corpus, so a curated "
    "alias table has nothing to match and only the column's contents carry evidence",
    "rows_agree_total_does_not": "the declared total footer omits one policy, so the rows are "
    "right and the file's own sum is not",
}


def schema_case(case: str, headers: Sequence[str], detail: Mapping[str, str]) -> CaseRecord:
    """A record for a case that is the schema rather than a row."""
    return CaseRecord(
        case=case, note=SCHEMA_LEVEL_CASES[case], headers=tuple(headers), detail=dict(detail)
    )


def inject(
    case: str, rows: list[TruthRow], rng: FixtureRandom, mint: Callable[[], str]
) -> tuple[list[TruthRow], CaseRecord]:
    """Apply one row-level case, returning the new rows and the record that describes it."""
    return ROW_LEVEL_CASES[case](rows, rng, mint)


def locate(rows: Sequence[TruthRow], record: CaseRecord) -> CaseRecord:
    """Fill in the row indices, after every injection has finished moving things around."""
    if not record.keys:
        return record
    wanted = set(record.keys)
    return record.with_rows(tuple(i for i, row in enumerate(rows) if row.key in wanted))
