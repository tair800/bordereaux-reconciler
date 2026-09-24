"""Exact money, and the reasons every part of it is the way it is.

**Binary floating point is banned from this project's financial path.** Not discouraged — banned,
and the ban is enforced rather than remembered: :func:`parse_amount` refuses a `float` argument at
runtime, and :class:`Money` has no constructor that accepts one. A reconciler that reports two
amounts as equal because `0.1 + 0.2` happened to land inside an epsilon is not a reconciler, it is a
coin flip with a spreadsheet attached.

**Parsing is where bordereaux actually break, so parsing is strict.** Coverholder files arrive with
`1.234,56` and `1,234.56` and `(1,234.56)` and `£1 234,56` and `1.234,56 EUR`, and the difference
between the first two is three orders of magnitude. Every one of those is a different decimal
convention, and guessing between them is how a reconciliation silently loses money. So a convention
is **declared per coverholder** and applied; it is never inferred from the value in front of us.
`parse_amount` raises rather than guess, and a raise becomes a quarantine reason with the offending
cell attached — which is the correct outcome, because somebody has to look.

**Rounding happens at exactly two boundaries**, named in ADR-001: reading a source cell, and
computing a declared total. Nowhere else. Intermediate arithmetic keeps full precision, because
rounding in the middle of a sum is how a total drifts a penny from its own rows and then somebody
spends an afternoon on it.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final, Self

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = [
    "Currency",
    "DecimalConvention",
    "Money",
    "MoneyParseError",
    "Tolerance",
    "parse_amount",
]


class Currency(StrEnum):
    """The currencies this build reconciles. Each carries a minor-unit scale.

    Deliberately a closed set. An unknown currency code is a quarantine reason, not a default to
    two decimal places — JPY has none and KWD has three, and a reconciler that assumed two would be
    wrong by a factor of a thousand on a Kuwaiti dinar without anything looking unusual.
    """

    GBP = "GBP"
    EUR = "EUR"
    USD = "USD"


#: Minor-unit scale per currency. All three are 2; the mapping exists so that adding JPY (0) or
#: KWD (3) is a data change rather than a search for hard-coded twos.
MINOR_UNITS: Final[dict[Currency, int]] = {
    Currency.GBP: 2,
    Currency.EUR: 2,
    Currency.USD: 2,
}


class DecimalConvention(StrEnum):
    """How a source file writes thousands and decimals.

    **Declared per coverholder, never inferred.** `1.234` is one thousand two hundred and thirty
    four in Germany and one and a bit in the UK, and no amount of looking at that single value
    tells you which. Sniffing the convention from the data is the single most expensive-looking
    cleverness available here, and it is not implemented on purpose.
    """

    #: `1,234.56` — comma groups, dot decimal.
    DOT_DECIMAL = "dot_decimal"
    #: `1.234,56` — dot groups, comma decimal.
    COMMA_DECIMAL = "comma_decimal"


class MoneyParseError(ValueError):
    """A source cell could not be read as an exact amount under the declared convention."""

    def __init__(self, raw: str, convention: DecimalConvention, reason: str) -> None:
        self.raw = raw
        self.convention = convention
        self.reason = reason
        super().__init__(f"cannot parse {raw!r} under {convention}: {reason}")


#: Currency symbols and codes stripped before parsing. The code is validated separately against the
#: declared currency; it is not taken from the cell, because a cell that disagrees with the file's
#: declared currency is a finding rather than an instruction.
#: RUF001: the U+00A0 is deliberate, not a typo. Excel writes non-breaking spaces into
#: currency cells and a class that only stripped ordinary spaces would reject those rows.
_SYMBOLS: Final = re.compile(
    r"[£$€\s ]|(?:GBP|EUR|USD)",  # noqa: RUF001
    re.IGNORECASE,
)

#: A trailing or leading minus, or the accounting parenthesis form `(1,234.56)`.
_PARENTHESISED: Final = re.compile(r"^\((.*)\)$")

#: How many digits a thousands group has, in every convention this build accepts.
GROUP_DIGITS: Final = 3

#: What may remain once symbols and grouping are gone: optional sign, digits, one dot.
_CLEAN: Final = re.compile(r"^-?\d+(?:\.\d+)?$")


def parse_amount(
    raw: str | Decimal | int,
    *,
    currency: Currency,
    convention: DecimalConvention = DecimalConvention.DOT_DECIMAL,
) -> Decimal:
    """One source cell as an exact `Decimal`, or `MoneyParseError`.

    Args:
        raw: The cell, as a string. `Decimal` and `int` pass through, because a generator or a
            database may hand over an already-exact value. **`float` is refused** — see below.
        currency: The file's declared currency, which fixes the rounding scale.
        convention: The coverholder's declared decimal convention. Never inferred from `raw`.

    Raises:
        MoneyParseError: The cell is empty, malformed, or ambiguous under the declared convention.
        TypeError: `raw` is a `float`. This is the ban, made operative. A float has already lost
            the exactness the rest of this module exists to preserve, so accepting one and
            converting it would launder the loss rather than prevent it.
    """
    if isinstance(raw, float):
        raise TypeError(
            "money is never parsed from a float: by the time a value is a float the exactness is "
            "already gone, and converting it here would hide that rather than prevent it. Pass the "
            "source string."
        )
    if isinstance(raw, Decimal | int):
        return _quantise(Decimal(raw), currency)

    text = raw.strip()
    if not text:
        raise MoneyParseError(raw, convention, "the cell is empty")

    negative = False
    if match := _PARENTHESISED.match(text):
        # Accounting negatives. `(1,234.56)` is -1234.56 in every bordereau that uses the form.
        negative, text = True, match.group(1)

    text = _SYMBOLS.sub("", text)
    if text.endswith("-"):  # trailing-minus, as some ledger exports write it
        negative, text = True, text[:-1]

    text = _degroup(text, convention, raw)

    if not _CLEAN.match(text):
        raise MoneyParseError(raw, convention, f"{text!r} is not a plain decimal after cleaning")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - _CLEAN makes this unreachable
        raise MoneyParseError(raw, convention, "not a decimal") from exc

    return _quantise(-value if negative else value, currency)


def _degroup(text: str, convention: DecimalConvention, raw: str) -> str:
    """Remove group separators and normalise the decimal mark to a dot.

    The strictness here is the point. Under `DOT_DECIMAL`, a comma may only ever be a group
    separator, so `1,23` is refused rather than read as one and twenty-three hundredths — it is
    almost certainly a file in the other convention, and reading it the lenient way would turn
    €123,45 into €12,345 without a word.
    """
    if convention is DecimalConvention.DOT_DECIMAL:
        group, point = ",", "."
    else:
        group, point = ".", ","

    if text.count(point) > 1:
        raise MoneyParseError(raw, convention, f"more than one {point!r} decimal mark")

    body, _, fraction = text.partition(point)
    if group in fraction:
        raise MoneyParseError(raw, convention, f"group separator {group!r} after the decimal mark")

    groups = body.split(group)
    if len(groups) > 1:
        # `1,2345` is not a thousands grouping in any convention; it is the other convention's
        # decimal mark wearing a disguise.
        lead, *rest = groups
        if (
            not lead.lstrip("-")
            or len(lead.lstrip("-")) > GROUP_DIGITS
            or any(len(g) != GROUP_DIGITS for g in rest)
        ):
            raise MoneyParseError(
                raw, convention, f"{body!r} is not grouped in threes under this convention"
            )

    return f"{''.join(groups)}.{fraction}" if fraction else "".join(groups)


def _quantise(value: Decimal, currency: Currency) -> Decimal:
    """Round to the currency's minor unit, half up.

    One of ADR-001's two declared rounding boundaries. Half-up rather than banker's rounding
    because that is what the finance teams these files come from use, and matching their arithmetic
    matters more than matching a statistician's.
    """
    exponent = Decimal(1).scaleb(-MINOR_UNITS[currency])
    return value.quantize(exponent, rounding=ROUND_HALF_UP)


class Money(BaseModel):
    """An exact amount in a known currency.

    Frozen, and arithmetic between currencies raises rather than converts. There is no FX rate
    source in this build, and inventing one — even a plausible one — would put a number nobody
    measured into a ledger.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: Decimal
    currency: Currency

    @field_validator("amount", mode="before")
    @classmethod
    def _refuse_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("Money.amount is never built from a float; pass a Decimal or a string")
        return value

    @classmethod
    def of(
        cls,
        raw: str | Decimal | int,
        currency: Currency,
        convention: DecimalConvention = DecimalConvention.DOT_DECIMAL,
    ) -> Self:
        return cls(
            amount=parse_amount(raw, currency=currency, convention=convention), currency=currency
        )

    def _same_currency(self, other: Money) -> None:
        if self.currency is not other.currency:
            raise ValueError(
                f"refusing to combine {self.currency} and {other.currency}: this build has no FX "
                "rate source, and a reconciliation across currencies is an exception rather than a "
                "conversion"
            )

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __abs__(self) -> Money:
        return Money(amount=abs(self.amount), currency=self.currency)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


class Tolerance(BaseModel):
    """How far apart two amounts may be and still count as agreeing.

    **Zero by default, and a non-zero value is a deliberate act that is recorded on every row it
    touches.** ADR-001 forbids any model or heuristic from choosing this; it is configuration, and
    `applies_to` names the fields it covers so that widening it silently to a field nobody discussed
    is not possible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    absolute: Decimal = Decimal("0.00")
    applies_to: tuple[str, ...] = ()
    reason: str = "exact match required"

    @field_validator("absolute", mode="before")
    @classmethod
    def _refuse_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("a tolerance is never a float; pass a Decimal or a string")
        return value

    @field_validator("absolute")
    @classmethod
    def _must_not_be_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("a negative tolerance would make every comparison pass")
        return value

    def covers(self, field: str) -> bool:
        return field in self.applies_to

    def accepts(self, field: str, difference: Money) -> bool:
        """Whether a difference on `field` is inside this tolerance.

        A field the tolerance does not name requires exact agreement, whatever `absolute` says.
        That is the direction that fails safe: forgetting to list a field makes the check stricter,
        not looser.
        """
        if not self.covers(field):
            return difference.amount == 0
        return abs(difference.amount) <= self.absolute


#: The default: everything exact, nothing forgiven. ADR-001's declared starting point.
EXACT: Final = Tolerance()
