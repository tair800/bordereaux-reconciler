"""Turning canonical truth into the file a coverholder actually sends, and writing it byte-stably.

Three things here are load-bearing.

**Every monetary cell is text, in both formats.** In CSV that is unavoidable; in XLSX it is a
decision. An `.xlsx` numeric cell *is* an IEEE-754 double in the file format itself, so writing
`1234.56` as a number would put binary floating point into the corpus — the exact loss this project
exists to prevent, in the fixtures that are meant to prove it prevented. Text cells are also what
real bordereaux contain, because the currency symbols and thousands separators force them to be.
(A variant with genuinely numeric cells would be a worthwhile fourteenth perturbation. It is not
built, and it is not claimed.)

**Byte-identical output means defeating three clocks.** A CSV is easy: fixed line terminator, fixed
encoding. An XLSX is a zip, and both openpyxl and :mod:`zipfile` reach for the wall clock —
`docProps/core.xml` gets a created and modified timestamp, and every zip entry gets an mtime from
`time.localtime()`. So the document properties are pinned and the finished archive is rewritten
with a fixed entry timestamp and a stable entry order. Without that rewrite, two runs a second
apart produce different bytes and the determinism claim quietly becomes a determinism hope.

**The formatting is the perturbation.** A variant's declared decimal convention, currency style and
date format are applied here and nowhere else, which is what keeps the declaration in
:mod:`bordereaux_reconciler.corpus.variants` the single source of truth for how a file reads.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import zipfile
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final

from openpyxl import Workbook  # type: ignore[import-untyped]

from bordereaux_reconciler.corpus.rng import FixtureRandom
from bordereaux_reconciler.corpus.truth import TruthRow, quantise
from bordereaux_reconciler.corpus.variants import (
    CurrencyStyle,
    DateFormat,
    PeriodFormat,
    TotalMode,
    VariantSpec,
)
from bordereaux_reconciler.money import Currency, DecimalConvention

__all__ = [
    "format_amount",
    "format_date",
    "format_period",
    "opaque_table",
    "source_table",
    "write_csv",
    "write_json",
    "write_xlsx",
]

_SYMBOLS: Final[dict[Currency, str]] = {
    Currency.GBP: "£",
    Currency.EUR: "€",
    Currency.USD: "$",
}

#: Written as an escape rather than the character itself, so nobody has to trust that an invisible
#: byte in this file is the one it claims to be. Excel puts this between a currency symbol and a
#: figure, and a corpus without one would never exercise the parser path that handles it.
_NBSP: Final = " "

_MONTHS: Final = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_GROUP: Final = 3

#: Pinned so the archive bytes do not move. 1980-01-01 is the zip epoch — the earliest timestamp
#: the format can store, and the conventional choice for reproducible archives.
_ZIP_EPOCH: Final = (1980, 1, 1, 0, 0, 0)
_DOC_TIMESTAMP: Final = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt.UTC)
_DOC_AUTHOR: Final = "bordereaux-reconciler synthetic corpus generator"

#: The label the declared-total footer puts in the first column.
_TOTAL_LABEL: Final = "TOTAL"

#: Opaque fixture vocabularies for the unmappable variants. Plausible spreadsheet contents that
#: identify nothing: no key, no currency, no period, no amount that can be tied to a policy.
_REGIONS: Final = ("North", "South", "Midlands", "Scotland", "Wales", "London")
_BANDS: Final = ("A", "B", "C", "D")
_TOKENS: Final = ("XJ", "PQ", "MT", "RV", "KD", "LS")


# ------------------------------------------------------------------------------- formatting


def format_amount(value: Decimal, spec: VariantSpec) -> str:
    """One amount, written the way this variant's coverholder writes it.

    Grouping, decimal mark, symbol placement and negative form all come from the variant's own
    declarations. Nothing is inferred from the number, which is the same rule
    :mod:`bordereaux_reconciler.money` applies from the other direction: the file says how it
    should be read, and neither side guesses.
    """
    amount = quantise(value, spec.currency)
    negative = amount < 0
    whole, _, fraction = f"{abs(amount):f}".partition(".")
    body = _group(whole, spec.decimal) + _point(spec.decimal) + fraction.ljust(2, "0")[:2]
    body = _dress(body, spec)
    if not negative:
        return body
    return f"({body})" if spec.parenthesised_negatives else f"-{body}"


def _group(whole: str, convention: DecimalConvention) -> str:
    separator = "," if convention is DecimalConvention.DOT_DECIMAL else "."
    digits = list(whole)
    for position in range(len(digits) - _GROUP, 0, -_GROUP):
        digits.insert(position, separator)
    return "".join(digits)


def _point(convention: DecimalConvention) -> str:
    return "." if convention is DecimalConvention.DOT_DECIMAL else ","


def _dress(body: str, spec: VariantSpec) -> str:
    symbol = _SYMBOLS[spec.currency]
    match spec.currency_style:
        case CurrencyStyle.SYMBOL_PREFIX:
            return f"{symbol}{body}"
        case CurrencyStyle.SYMBOL_NBSP:
            return f"{symbol}{_NBSP}{body}"
        case CurrencyStyle.CODE_SUFFIX:
            return f"{body} {spec.currency}"
        case _:
            return body


def format_date(iso: str, style: DateFormat) -> str:
    """A date in the variant's format. Month names are a fixed table, never `strftime`.

    `%B` asks the C library for a localised month name, so the same generator would emit `March` on
    one machine and `märz` on another. A corpus that depends on the host's locale is not
    reproducible, whatever its seed says.
    """
    day, month, year = _parts(iso)
    match style:
        case DateFormat.DMY_SLASH:
            return f"{day:02d}/{month:02d}/{year}"
        case DateFormat.MDY_SLASH:
            return f"{month:02d}/{day:02d}/{year}"
        case DateFormat.WRITTEN_MONTH:
            return f"{day} {_MONTHS[month - 1]} {year}"
        case _:
            return iso


def _parts(iso: str) -> tuple[int, int, int]:
    date = dt.date.fromisoformat(iso)
    return date.day, date.month, date.year


def format_period(period: str, style: PeriodFormat) -> str:
    year, month = (int(part) for part in period.split("-"))
    match style:
        case PeriodFormat.SLASH:
            return f"{month:02d}/{year}"
        case PeriodFormat.WRITTEN:
            return f"{_MONTHS[month - 1]} {year}"
        case _:
            return period


# ---------------------------------------------------------------------------------- the table


def source_table(spec: VariantSpec, rows: Sequence[TruthRow]) -> tuple[list[str], list[list[str]]]:
    """The headers and cells of a mappable variant's file, plus its declared total if it has one."""
    headers = [column.header for column in spec.columns]
    cells = [[_cell(column.producer, row, spec) for column in spec.columns] for row in rows]
    footer = _total_row(spec, rows)
    if footer is not None:
        cells.append(footer)
    return headers, cells


def _cell(producer: str, row: TruthRow, spec: VariantSpec) -> str:
    kind, _, argument = producer.partition(":")
    match kind:
        case "key":
            return row.key
        case "period":
            return format_period(row.period, spec.periods)
        case "currency":
            return str(row.currency)
        case "date":
            return format_date(row.attributes[argument], spec.dates)
        case "text":
            # Indexed rather than `.get`: the attribute projection keeps exactly what the columns
            # ask for, so a missing one is a catalogue bug and an empty cell would hide it.
            return row.attributes[argument]
        case "money":
            return format_amount(_amount(argument, row), spec)
        case _:  # pragma: no cover - the catalogue is fixed and validated at generation time
            raise ValueError(f"no such producer: {producer!r}")


def _amount(argument: str, row: TruthRow) -> Decimal:
    """Which Decimal a money producer means.

    The split-commission halves are derived from the row rather than drawn, and the second half is
    the remainder rather than a second rounding — so the two columns sum to the canonical deduction
    exactly, every time, which is the only version of this perturbation worth shipping. A split
    that was a penny out would be testing the tolerance machinery instead of the mapping.
    """
    name, _, index = argument.partition(":")
    match name:
        case "gross":
            return row.gross
        case "net":
            return row.net if row.net is not None else row.gross
        case "premium_excl_tax":
            return row.gross - row.deductions["tax"]
        case "deduction":
            return row.deductions[index]
        case "commission_part":
            whole = row.deductions["commission"]
            first = quantise(whole * Decimal("0.6"), row.currency)
            return first if index == "0" else whole - first
        case _:  # pragma: no cover - as above
            raise ValueError(f"no such amount: {argument!r}")


def _total_row(spec: VariantSpec, rows: Sequence[TruthRow]) -> list[str] | None:
    """A declared total footer, honest or otherwise.

    `DISAGREES` drops the last row out of the sum, which is what a coverholder's own spreadsheet
    does when somebody appends a line below the `SUM()` range. It is the most common way a real
    bordereau's footer stops agreeing with its rows, and it is off by a whole policy rather than by
    a rounding penny — a difference no tolerance should ever absorb.
    """
    if spec.total is TotalMode.NONE or not rows:
        return None
    counted = rows[:-1] if spec.total is TotalMode.DISAGREES else rows
    footer: list[str] = []
    for column in spec.columns:
        kind, _, argument = column.producer.partition(":")
        if kind != "money":
            footer.append(_TOTAL_LABEL if column.field == "key" else "")
            continue
        footer.append(format_amount(sum((_amount(argument, r) for r in counted), Decimal(0)), spec))
    return footer


def opaque_table(spec: VariantSpec, rng: FixtureRandom) -> tuple[list[str], list[list[str]]]:
    """An unmappable variant's file: real-looking contents that correspond to no canonical row.

    Generated independently of any truth rows, because there are none — the published ground truth
    for these variants is an empty row list and an expectation of quarantine. Inventing truth for a
    file the system is supposed to refuse would be grading it on a question nobody asked.
    """
    headers = [column.header for column in spec.columns]
    cells = [
        [_opaque_cell(column.producer.partition(":")[2], rng) for column in spec.columns]
        for _ in range(spec.rows)
    ]
    return headers, cells


def _opaque_cell(kind: str, rng: FixtureRandom) -> str:
    match kind:
        case "region":
            return rng.choice(_REGIONS)
        case "band":
            return rng.choice(_BANDS)
        case "quarter":
            return f"{rng.integer(1_000, 99_000)}"
        case "percent":
            return f"{rng.integer(-40, 60)}%"
        case "token":
            return f"{rng.choice(_TOKENS)}{rng.integer(100_000, 999_999)}"
        case _:
            return f"{rng.integer(0, 5_000)}"


# ---------------------------------------------------------------------------------- writing


def write_csv(
    path: Path, headers: Sequence[str], rows: Sequence[Sequence[str]], *, bom: bool
) -> None:
    """A CSV whose bytes do not depend on the host.

    `newline=""` plus an explicit `\\n` terminator, because :mod:`csv` defaults to `\\r\\n` and the
    file object would otherwise translate it again on Windows — giving `\\r\\r\\n` and a corpus
    whose bytes differ by platform. The optional BOM is a perturbation, not an accident: it is what
    Excel writes when a user saves a CSV, and it is the first three bytes of a header that
    otherwise looks fine.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    encoding = "utf-8-sig" if bom else "utf-8"
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)


def write_xlsx(
    path: Path, sheet: str, headers: Sequence[str], rows: Sequence[Sequence[str]]
) -> None:
    """An XLSX with every clock pinned, written through a deterministic re-zip."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(list(headers))
    for row in rows:
        worksheet.append(list(row))

    workbook.properties.creator = _DOC_AUTHOR
    workbook.properties.lastModifiedBy = _DOC_AUTHOR
    workbook.properties.created = _DOC_TIMESTAMP
    workbook.properties.modified = _DOC_TIMESTAMP

    buffer = io.BytesIO()
    workbook.save(buffer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_stable_zip(buffer.getvalue()))


def _stable_zip(raw: bytes) -> bytes:
    """Rewrite an archive with a fixed entry timestamp, order and mode.

    openpyxl builds the workbook correctly and stamps every entry with `time.localtime()`, so two
    runs a second apart differ in bytes while being identical in content. Sorting the names as well
    removes the other source of drift, since dictionary iteration order is only stable within one
    interpreter version.
    """
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(raw)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for name in sorted(source.namelist()):
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            target.writestr(info, source.read(name))
    return out.getvalue()


def write_json(path: Path, payload: object) -> None:
    """JSON with a fixed separator, a trailing newline and no `\\r`, so the bytes are the bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
