"""What a column's values look like. This is the system's contribution over the four baselines.

ADR-001 predeclares four baselines and all four are **header-string** methods: exact match,
normalised match, fuzzy match, and a curated synonym table. Every one of them is blind in the same
place — when a coverholder renames `Gross Premium` to `GWP (excl IPT)` or to `Col_4`, the header
stops carrying the answer.

The values do not stop carrying it. That column still parses as money in 99% of its cells, still
has two decimal places, and is still the **largest** of the money columns; the commission column is
still smaller than it and larger than the tax column. None of that is visible to a string matcher
and all of it survives a rename.

**Everything here is deterministic and cheap.** No sampling, no learning, no thresholds tuned on a
result. Each profiler counts what is in front of it and reports a fraction. A column is not
classified here at all — :mod:`bordereaux_reconciler.ingest.mapping` weighs these signals against
the adapter's declared shapes, and this module has no opinion about what any field means.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from bordereaux_reconciler.money import Currency, DecimalConvention, MoneyParseError, parse_amount

__all__ = ["ColumnProfile", "profile_column", "profile_grid"]

#: Date renderings that actually arrive. `DD/MM/YYYY` and `MM/DD/YYYY` are both here and are
#: deliberately **not** disambiguated: `03/04/2026` is March or April depending on who sent it, and
#: this module's job is to report that the column is temporal, not to decide which month it is.
#: Deciding that is the adapter's, from the coverholder's declared locale.
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$"),
    re.compile(r"^\d{4}[/.-]\d{1,2}$"),
    re.compile(r"^\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}$"),
    re.compile(r"^[A-Za-z]{3,9}\s+\d{4}$"),
    re.compile(r"^\d{4}-\d{2}$"),
)

#: Above this share of distinct values a column is not a category, it is data. Chosen as a round
#: number rather than fitted: nothing downstream tunes on it, and it only ever breaks ties.
_CATEGORICAL_DISTINCT_RATIO = 0.2
_CATEGORICAL_MAX_DISTINCT = 30

#: A column this much of which parses as money is a money column. High on purpose: a
#: reference column of bare integers would otherwise look monetary, and mistaking an
#: identifier for an amount is the expensive direction of that error.
MONEY_COLUMN_THRESHOLD = 0.9


@dataclass(frozen=True)
class ColumnProfile:
    """What one column's values look like, as counted fractions.

    `monetary` is computed under **both** decimal conventions and the better of the two is
    reported, alongside which one it was. That is not the convention-sniffing ADR-001 forbids: the
    engine still parses under the coverholder's *declared* convention, and this figure is only ever
    used to decide whether a column is money at all. A column that parses as money under either
    reading is money under both.
    """

    header: str
    non_empty: int
    monetary: float
    monetary_convention: DecimalConvention | None
    temporal: float
    distinct: int
    distinct_ratio: float
    categorical: bool
    #: Median magnitude of the parsed amounts, used only to rank money columns against each other.
    magnitude: float = 0.0
    #: Rank among this file's money columns, 0 being the largest. `None` when not a money column.
    #: Filled in by `profile_grid`, which is the only place that can see the other columns.
    magnitude_rank: int | None = None
    samples: tuple[str, ...] = field(default_factory=tuple)

    def matches_pattern(self, pattern: re.Pattern[str] | None) -> float:
        """Share of sampled values matching an adapter's declared identifier pattern."""
        if pattern is None or not self.samples:
            return 0.0
        return sum(bool(pattern.match(s)) for s in self.samples) / len(self.samples)


def _monetary_share(values: tuple[str, ...], convention: DecimalConvention) -> tuple[float, float]:
    """Share of values parsing as money under one convention, and their median magnitude."""
    amounts = []
    for value in values:
        try:
            amounts.append(abs(parse_amount(value, currency=Currency.GBP, convention=convention)))
        except (MoneyParseError, TypeError):
            continue
    if not values:
        return 0.0, 0.0
    if not amounts:
        return 0.0, 0.0
    amounts.sort()
    median = amounts[len(amounts) // 2]
    return len(amounts) / len(values), float(median)


def profile_column(header: str, values: tuple[str, ...], *, sample: int = 200) -> ColumnProfile:
    """Profile one column.

    `sample` caps the work on a large sheet. It is a head sample rather than a random one so that
    profiling is deterministic — kill condition A requires two runs to agree, and a random sample
    would make the mapping itself non-reproducible.
    """
    present = tuple(v for v in values if v.strip())[:sample]
    if not present:
        return ColumnProfile(
            header=header,
            non_empty=0,
            monetary=0.0,
            monetary_convention=None,
            temporal=0.0,
            distinct=0,
            distinct_ratio=0.0,
            categorical=False,
        )

    dot, dot_magnitude = _monetary_share(present, DecimalConvention.DOT_DECIMAL)
    comma, comma_magnitude = _monetary_share(present, DecimalConvention.COMMA_DECIMAL)
    if dot >= comma:
        monetary, convention, magnitude = dot, DecimalConvention.DOT_DECIMAL, dot_magnitude
    else:
        monetary, convention, magnitude = comma, DecimalConvention.COMMA_DECIMAL, comma_magnitude

    temporal = sum(any(p.match(v) for p in _DATE_PATTERNS) for v in present) / len(present)
    distinct = len(set(present))
    ratio = distinct / len(present)

    return ColumnProfile(
        header=header,
        non_empty=len(present),
        monetary=monetary,
        monetary_convention=convention if monetary > 0 else None,
        temporal=temporal,
        distinct=distinct,
        distinct_ratio=ratio,
        categorical=(
            ratio <= _CATEGORICAL_DISTINCT_RATIO and distinct <= _CATEGORICAL_MAX_DISTINCT
        ),
        magnitude=magnitude,
        samples=present[:20],
    )


def profile_grid(
    headers: tuple[str, ...], columns: dict[str, tuple[str, ...]]
) -> dict[str, ColumnProfile]:
    """Profile every column, then rank the money columns against each other.

    The ranking is the part a header cannot give you. Within one file the gross amount is larger
    than the commission and the commission is larger than the tax, essentially always — so once the
    money columns are known, their order carries most of the remaining information about which is
    which. Ranked by median magnitude descending, so rank 0 is the largest, and ties broken by
    header so the ranking is reproducible for kill condition A.
    """
    profiles = {header: profile_column(header, columns.get(header, ())) for header in headers}

    money = sorted(
        (p for p in profiles.values() if p.monetary >= MONEY_COLUMN_THRESHOLD),
        key=lambda p: (-p.magnitude, p.header),
    )
    ranks = {p.header: index for index, p in enumerate(money)}

    return {
        header: replace(profile, magnitude_rank=ranks.get(header))
        for header, profile in profiles.items()
    }
