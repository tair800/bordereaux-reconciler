"""Reading a coverholder file into a grid of cells that remember where they came from.

Everything downstream needs two things this module is the only place to get: the **content hash of
the file** and the **coordinate of every cell**. Claim 1 needs both — the hash is what makes
re-ingestion idempotent, and the coordinates are what make a canonical value traceable back to a
spreadsheet cell somebody can open.

**Every cell is read as text.** Not as a number, not as a date, not as anything a library guessed.
`openpyxl` will happily hand back a `float` for a currency cell and a `datetime` for something that
was written `03/04/2026`, and both of those are decisions — the float has already lost exactness,
and the datetime has already chosen between March and April. Those decisions belong to the parser,
under a declared convention, where they can be refused. So the reader's job is to lose nothing:
`str` in, `str` out, and the raw text is carried all the way to the lineage record.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Grid", "content_hash", "read_grid"]


@dataclass(frozen=True)
class Grid:
    """One sheet of a source file, as text, with its coordinates intact.

    `header_row` is 1-based and is the spreadsheet's own numbering, so a lineage record points at
    the row a person sees when they open the file rather than at an offset into a list.
    """

    sheet: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    header_row: int = 1
    content_hash: str = ""

    def column(self, header: str) -> tuple[str, ...]:
        """Every value under one header, in order. Missing header gives an empty tuple."""
        if header not in self.headers:
            return ()
        index = self.headers.index(header)
        return tuple(row[index] if index < len(row) else "" for row in self.rows)

    def cell(self, row_index: int, header: str) -> str:
        """One cell by zero-based data row and header name."""
        if header not in self.headers:
            return ""
        index = self.headers.index(header)
        row = self.rows[row_index]
        return row[index] if index < len(row) else ""

    def spreadsheet_row(self, row_index: int) -> int:
        """The row number a person sees in Excel, from a zero-based data index."""
        return self.header_row + 1 + row_index


def content_hash(path: Path) -> str:
    """SHA-256 of the file's bytes.

    Of the **bytes**, deliberately, not of the parsed content. A coverholder who re-sends a file
    with one cell corrected has sent a different file and it must be ingested; a coverholder who
    re-sends the identical file — which happens constantly, because mail clients retry and people
    forward things — has not, and ingesting it again would double the month's premium.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean(value: object) -> str:
    """One cell as text, losing nothing and deciding nothing."""
    if value is None:
        return ""
    if isinstance(value, float):
        # openpyxl hands back floats for numeric cells. Rendering with repr would give
        # `1234.5600000000001`; this keeps the shortest exact decimal representation, which is what
        # the cell displayed. The value is still re-parsed under a declared convention downstream.
        return format(value, "f").rstrip("0").rstrip(".") if value % 1 else str(int(value))
    return str(value).strip()


def read_grid(path: Path, *, sheet: str | None = None, encoding: str = "utf-8-sig") -> Grid:
    """A source file as a :class:`Grid`.

    CSV and XLSX, because those are what actually arrive. `utf-8-sig` by default because Excel on
    Windows writes a BOM and a header read as `\\ufeffPolicy Ref` matches no synonym at all — an
    hour of somebody's life the first time it happens.
    """
    digest = content_hash(path)
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return _read_xlsx(path, sheet, digest)
    return _read_csv(path, sheet or path.stem, digest, encoding)


def _read_csv(path: Path, sheet: str, digest: str, encoding: str) -> Grid:
    text = path.read_text(encoding=encoding, errors="replace")
    # `csv.Sniffer` is deliberately not used. It guesses a delimiter from a sample, and a file whose
    # first rows happen to contain semicolons in a description field gets read into the wrong shape
    # silently. Comma or semicolon is decided by which produces more columns on the header line,
    # which is inspectable and wrong loudly rather than quietly.
    first = text.splitlines()[0] if text.splitlines() else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    records = [tuple(_clean(c) for c in row) for row in reader if any(str(c).strip() for c in row)]
    if not records:
        return Grid(sheet=sheet, headers=(), rows=(), content_hash=digest)
    return Grid(sheet=sheet, headers=records[0], rows=tuple(records[1:]), content_hash=digest)


def _read_xlsx(path: Path, sheet: str | None, digest: str) -> Grid:
    from openpyxl import load_workbook  # noqa: PLC0415 - a dev-time dependency, imported on use

    # `data_only=True` reads the cached value of a formula rather than the formula text. A
    # bordereau whose total is `=SUM(D2:D400)` has a number a person saw; reading `=SUM(...)` and
    # trying to parse it as money would quarantine every file built in Excel.
    book = load_workbook(path, read_only=True, data_only=True)
    worksheet = book[sheet] if sheet else book[book.sheetnames[0]]
    records = [
        tuple(_clean(cell) for cell in row)
        for row in worksheet.iter_rows(values_only=True)
        if any(cell is not None and str(cell).strip() for cell in row)
    ]
    book.close()
    if not records:
        return Grid(sheet=worksheet.title, headers=(), rows=(), content_hash=digest)
    return Grid(
        sheet=worksheet.title, headers=records[0], rows=tuple(records[1:]), content_hash=digest
    )
