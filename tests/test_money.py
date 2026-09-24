"""Money: the float ban, the two decimal conventions, and what a tolerance may and may not do.

These are the tests that matter most in the project, because everything downstream is arithmetic on
what this module returns. A reconciler that reads `1.234,56` as `1.23` will confidently report a
mismatch on a figure that agrees, and a reconciler that reads it as `1.23456` will confidently
report agreement on a figure that does not.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bordereaux_reconciler.money import (
    EXACT,
    Currency,
    DecimalConvention,
    Money,
    MoneyParseError,
    Tolerance,
    parse_amount,
)


class TestTheFloatBan:
    """A float may not enter this module, by any door."""

    def test_parse_amount_refuses_a_float(self) -> None:
        with pytest.raises(TypeError):
            parse_amount(1234.56, currency=Currency.GBP)  # type: ignore[arg-type]

    def test_money_refuses_a_float_amount(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            Money(amount=1234.56, currency=Currency.GBP)  # type: ignore[arg-type]

    def test_tolerance_refuses_a_float(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            Tolerance(absolute=0.01, applies_to=("gross",))  # type: ignore[arg-type]

    def test_the_canonical_float_failure_does_not_occur(self) -> None:
        """`0.1 + 0.2 != 0.3` in binary floating point. Here it must."""
        total = parse_amount("0.10", currency=Currency.GBP) + parse_amount(
            "0.20", currency=Currency.GBP
        )
        assert total == Decimal("0.30")
        assert str(total) == "0.30"


class TestDecimalConventions:
    """`1.234,56` and `1,234.56` are the same number written by different people."""

    @pytest.mark.parametrize(
        ("raw", "convention", "expected"),
        [
            ("1,234.56", DecimalConvention.DOT_DECIMAL, "1234.56"),
            ("1.234,56", DecimalConvention.COMMA_DECIMAL, "1234.56"),
            ("1234.56", DecimalConvention.DOT_DECIMAL, "1234.56"),
            ("1234,56", DecimalConvention.COMMA_DECIMAL, "1234.56"),
            ("1 234,56", DecimalConvention.COMMA_DECIMAL, "1234.56"),
            ("38,867.16", DecimalConvention.DOT_DECIMAL, "38867.16"),
        ],
    )
    def test_declared_convention_is_honoured(
        self, raw: str, convention: DecimalConvention, expected: str
    ) -> None:
        assert parse_amount(raw, currency=Currency.GBP, convention=convention) == Decimal(expected)

    def test_the_same_text_is_a_different_number_under_each_convention(self) -> None:
        """The reason the convention is declared and never sniffed.

        `1.234` is one thousand two hundred and thirty-four to a German broker and one and a bit to
        a British one. No amount of looking at the value decides which; only the sender knows.
        """
        dot = parse_amount("1.234", currency=Currency.EUR, convention=DecimalConvention.DOT_DECIMAL)
        comma = parse_amount(
            "1.234", currency=Currency.EUR, convention=DecimalConvention.COMMA_DECIMAL
        )
        # One euro and twenty-three cents, against one thousand two hundred and thirty-four euros.
        # A thousandfold difference decided by nothing in the file.
        assert dot == Decimal("1.23")
        assert comma == Decimal("1234.00")
        assert comma == dot * 1000 + Decimal("4.00")

    def test_ambiguous_grouping_is_refused_rather_than_guessed(self) -> None:
        """A separator that cannot be a decimal point under the declared convention."""
        with pytest.raises(MoneyParseError):
            parse_amount(
                "1,234,56", currency=Currency.GBP, convention=DecimalConvention.DOT_DECIMAL
            )

    def test_parenthesised_negatives_are_negative(self) -> None:
        """Accounting notation. Reading `(12.34)` as positive turns a credit into a payment."""
        assert parse_amount("(12.34)", currency=Currency.GBP) == Decimal("-12.34")

    def test_a_non_breaking_space_is_still_a_thousands_separator(self) -> None:
        # A literal U+00A0 in the test string, matching what Excel emits. ruff flags it as an
        # ambiguous character, which is exactly why the parser has to handle it: it is invisible
        # in a spreadsheet and breaks any parser that only strips ASCII space.
        nbsp = "1 234,56"  # noqa: RUF001
        assert parse_amount(
            nbsp, currency=Currency.EUR, convention=DecimalConvention.COMMA_DECIMAL
        ) == Decimal("1234.56")

    @pytest.mark.parametrize("raw", ["", "   ", "n/a", "TBC", "-", "abc"])
    def test_text_that_is_not_an_amount_raises_rather_than_returning_zero(self, raw: str) -> None:
        """Zero is a claim about money. "I could not read this" is not the same claim."""
        with pytest.raises(MoneyParseError):
            parse_amount(raw, currency=Currency.GBP)


class TestMoneyArithmetic:
    def test_currencies_do_not_mix(self) -> None:
        gbp = Money(amount=Decimal("10.00"), currency=Currency.GBP)
        eur = Money(amount=Decimal("10.00"), currency=Currency.EUR)
        with pytest.raises(ValueError, match="curren"):
            _ = gbp + eur

    def test_addition_keeps_full_precision(self) -> None:
        a = Money(amount=Decimal("0.005"), currency=Currency.GBP)
        b = Money(amount=Decimal("0.005"), currency=Currency.GBP)
        assert (a + b).amount == Decimal("0.010")

    def test_rounding_on_parse_is_half_up_not_bankers(self) -> None:
        """Python's `Decimal` rounds 1.005 to 1.00 by default. Invoices do not.

        `ROUND_HALF_EVEN` is the better statistical choice and the wrong commercial one: every
        finance system a coverholder uses rounds half away from zero, and a reconciler that
        disagreed with all of them by a penny would raise a discrepancy a month, for ever.
        """
        assert parse_amount("1.005", currency=Currency.GBP) == Decimal("1.01")
        assert parse_amount("1.015", currency=Currency.GBP) == Decimal("1.02")
        assert parse_amount("2.675", currency=Currency.GBP) == Decimal("2.68")


class TestTolerance:
    def test_the_default_is_exact(self) -> None:
        assert EXACT.absolute == Decimal("0.00")
        assert EXACT.applies_to == ()

    def test_a_negative_tolerance_is_refused(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            Tolerance(absolute=Decimal("-0.01"))

    def test_a_tolerance_covers_only_the_fields_it_names(self) -> None:
        """The property that stops a tolerance widening silently onto a field nobody discussed."""
        tolerance = Tolerance(
            absolute=Decimal("0.05"), applies_to=("tax",), reason="rounding on a statutory rate"
        )
        assert tolerance.covers("tax")
        assert not tolerance.covers("gross")

    def test_an_unnamed_field_requires_exact_agreement_even_under_a_tolerance(self) -> None:
        difference = Money(amount=Decimal("0.01"), currency=Currency.GBP)
        tolerance = Tolerance(absolute=Decimal("0.05"), applies_to=("tax",))
        # Forgetting to list a field makes the check stricter, never looser. That is the direction
        # an omission has to fail in when the subject is money.
        assert not tolerance.accepts("gross", difference)
        assert tolerance.accepts("tax", difference)
        assert tolerance.accepts("gross", Money(amount=Decimal("0"), currency=Currency.GBP))
