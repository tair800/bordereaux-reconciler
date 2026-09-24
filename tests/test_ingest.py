"""Reading a file: period normalisation, value-shape profiling, mapping, and quarantine.

The mapping tests assert the property that justifies the whole project — that a column whose
*header* says nothing useful can still be identified from what it *contains* — and they assert it
against the four predeclared baselines rather than in the abstract, because "better than nothing"
is not a claim worth making.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bordereaux_reconciler.adapters import get_adapter, known_families
from bordereaux_reconciler.ingest.canonical import (
    CoverholderProfile,
    build_canonical_rows,
    normalise_period,
)
from bordereaux_reconciler.ingest.mapping import BASELINES, normalise_header, propose_mapping
from bordereaux_reconciler.ingest.profile import profile_column, profile_grid
from bordereaux_reconciler.ingest.read import read_grid
from bordereaux_reconciler.money import Currency, DecimalConvention

INSURANCE = get_adapter("insurance")


class TestPeriodNormalisation:
    @pytest.mark.parametrize(
        ("raw", "day_first", "expected"),
        [
            ("2026-01", True, "2026-01"),
            ("2026-01-15", True, "2026-01"),
            ("15/01/2026", True, "2026-01"),
            ("01/15/2026", False, "2026-01"),
            ("01/2026", True, "2026-01"),
            ("12/2026", True, "2026-12"),
            ("January 2026", True, "2026-01"),
            ("Sept 2026", True, "2026-09"),
            ("15 Mar 2026", True, "2026-03"),
        ],
    )
    def test_recognised_renderings(self, raw: str, day_first: bool, expected: str) -> None:
        assert normalise_period(raw, day_first=day_first) == expected

    def test_the_declared_order_decides_an_ambiguous_date(self) -> None:
        """`03/04/2026` is March in Boston and April in Bristol. The file does not say which."""
        assert normalise_period("03/04/2026", day_first=True) == "2026-04"
        assert normalise_period("03/04/2026", day_first=False) == "2026-03"

    def test_arithmetic_overrules_the_declaration_when_it_must(self) -> None:
        """A component above twelve can only be a day, whatever the coverholder declared."""
        assert normalise_period("25/03/2026", day_first=False) == "2026-03"

    @pytest.mark.parametrize("raw", ["", "TBC", "n/a", "13/2026", "not a date", "2026"])
    def test_unrecognised_text_returns_none_rather_than_a_guess(self, raw: str) -> None:
        assert normalise_period(raw, day_first=True) is None


class TestValueShapeProfiling:
    def test_a_money_column_is_recognised_under_either_convention(self) -> None:
        dot = profile_column("X", ("1,234.56", "987.00", "12.10"))
        comma = profile_column("X", ("1.234,56", "987,00", "12,10"))
        assert dot.monetary > 0.9
        assert comma.monetary > 0.9

    def test_a_reference_column_is_not_money(self) -> None:
        """Bare integers would otherwise look monetary, and reading an id as an amount is costly."""
        profile = profile_column("Ref", ("POL-202601-00001", "POL-202601-00002"))
        assert profile.monetary < 0.5

    def test_a_reporting_period_is_near_constant_and_a_transaction_date_is_not(self) -> None:
        """The signal that separates two columns which are both temporal and both look similar."""
        period = profile_column("Per", tuple(["2026-01"] * 150))
        dates = profile_column("Inc", tuple(f"2026-01-{d % 28 + 1:02d}" for d in range(150)))
        assert period.near_constant
        assert not dates.near_constant
        assert period.temporal == dates.temporal == 1.0

    def test_money_columns_are_ranked_by_size_within_one_file(self) -> None:
        """The evidence no header can give: gross is the largest, tax the smallest."""
        profiles = profile_grid(
            ("A", "B", "C"),
            {
                "A": ("1000.00", "2000.00", "3000.00"),
                "B": ("200.00", "400.00", "600.00"),
                "C": ("100.00", "200.00", "300.00"),
            },
        )
        assert profiles["A"].magnitude_rank == 0
        assert profiles["B"].magnitude_rank == 1
        assert profiles["C"].magnitude_rank == 2


class TestHeaderNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Gross Premium", "gross premium"),
            ("  GROSS   PREMIUM  ", "gross premium"),
            ("Gross_Premium", "gross premium"),
            ("Gross-Premium", "gross premium"),
            ("Gross Premium (GBP)", "gross premium gbp"),
        ],
    )
    def test_headers_reduce_to_comparable_form(self, raw: str, expected: str) -> None:
        assert normalise_header(raw) == expected


class TestMapping:
    def test_the_four_predeclared_baselines_are_all_present(self) -> None:
        """Dropping the strongest baseline is how kill condition E would be won dishonestly."""
        assert set(BASELINES) == {
            "exact_header",
            "normalised_header",
            "fuzzy_header",
            "curated_synonyms",
        }

    def test_shape_beats_a_misleading_header(self) -> None:
        """The case the project exists for: the headers are swapped, the magnitudes are not.

        `Total Payable` is the gross and `Premium` is the net. Every header-string method reads
        `Premium` as the gross premium; the size ranking does not, because a net is never the
        largest money column in a file that also carries its gross.
        """
        headers = ("Policy Reference", "Period", "Total Payable", "Commission", "Tax", "Premium")
        columns = {
            "Policy Reference": tuple(f"POL-202601-{i:05d}" for i in range(20)),
            "Period": tuple(["2026-01"] * 20),
            "Total Payable": tuple(f"{1000 + i * 10}.00" for i in range(20)),
            "Commission": tuple(f"{150 + i}.00" for i in range(20)),
            "Tax": tuple(f"{90 + i}.00" for i in range(20)),
            "Premium": tuple(f"{760 + i * 8}.00" for i in range(20)),
        }
        profiles = profile_grid(headers, columns)
        outcome = propose_mapping(headers, profiles, INSURANCE)
        assigned = {p.canonical_field: p.source_header for p in outcome.proposals}

        assert assigned["gross_premium"] == "Total Payable"
        assert assigned["net_premium"] == "Premium"

    def test_a_file_missing_a_required_field_is_not_mappable(self) -> None:
        """Abstention is triggered by a missing required field, not by a score below a number."""
        headers = ("Q1", "Q2", "Q3")
        columns = {h: tuple(f"{i * 100}.00" for i in range(20)) for h in headers}
        outcome = propose_mapping(headers, profile_grid(headers, columns), INSURANCE)
        assert not outcome.mappable
        assert outcome.missing_required

    def test_the_same_input_maps_identically_twice(self) -> None:
        headers = ("Policy Reference", "Period", "Gross Premium")
        columns = {
            "Policy Reference": ("POL-202601-00001",),
            "Period": ("2026-01",),
            "Gross Premium": ("1000.00",),
        }
        profiles = profile_grid(headers, columns)
        first = propose_mapping(headers, profiles, INSURANCE).as_columns()
        second = propose_mapping(headers, profiles, INSURANCE).as_columns()
        assert first == second


class TestQuarantineNeverCoerces:
    @pytest.fixture
    def grid_path(self, tmp_path: Path) -> Path:
        path = tmp_path / "bordereau.csv"
        path.write_text(
            "Policy Reference,Period,Gross Premium\n"
            "POL-202601-00001,2026-01,1000.00\n"
            "POL-202601-00002,2026-01,not-a-number\n"
            "POL-202601-00003,TBC,2000.00\n"
            "POL-202601-00004,2026-01,\n",
            encoding="utf-8",
        )
        return path

    def test_unreadable_rows_are_held_and_readable_ones_are_not(self, grid_path: Path) -> None:
        grid = read_grid(grid_path, sheet="Bordereau")
        mapping = {
            "Policy Reference": "policy_reference",
            "Period": "period",
            "Gross Premium": "gross_premium",
        }
        profile = CoverholderProfile(
            coverholder="Test", currency=Currency.GBP, convention=DecimalConvention.DOT_DECIMAL
        )
        outcome = build_canonical_rows(grid, mapping, INSURANCE, profile)

        assert len(outcome.rows) == 1
        assert len(outcome.quarantined) == 3
        assert not outcome.clean

    def test_every_quarantine_reason_names_the_spreadsheet_row(self, grid_path: Path) -> None:
        """A reason a technician cannot act on is a reason nobody acts on."""
        grid = read_grid(grid_path, sheet="Bordereau")
        outcome = build_canonical_rows(
            grid,
            {
                "Policy Reference": "policy_reference",
                "Period": "period",
                "Gross Premium": "gross_premium",
            },
            INSURANCE,
            CoverholderProfile(coverholder="T", currency=Currency.GBP),
        )
        for held in outcome.quarantined:
            assert held.spreadsheet_row >= 2
            assert len(held.reason) > 30

    def test_an_unreadable_amount_never_becomes_zero(self, grid_path: Path) -> None:
        grid = read_grid(grid_path, sheet="Bordereau")
        outcome = build_canonical_rows(
            grid,
            {
                "Policy Reference": "policy_reference",
                "Period": "period",
                "Gross Premium": "gross_premium",
            },
            INSURANCE,
            CoverholderProfile(coverholder="T", currency=Currency.GBP),
        )
        assert all(row.gross.value.amount != 0 for row in outcome.rows)


class TestLineage:
    def test_every_canonical_value_carries_its_cell(self, tmp_path: Path) -> None:
        path = tmp_path / "b.csv"
        path.write_text(
            "Policy Reference,Period,Gross Premium,Commission\n"
            "POL-202601-00001,2026-01,1000.00,150.00\n",
            encoding="utf-8",
        )
        grid = read_grid(path, sheet="Bordereau")
        outcome = build_canonical_rows(
            grid,
            {
                "Policy Reference": "policy_reference",
                "Period": "period",
                "Gross Premium": "gross_premium",
                "Commission": "commission",
            },
            INSURANCE,
            CoverholderProfile(coverholder="T", currency=Currency.GBP),
        )
        row = outcome.rows[0]
        assert row.lineage()
        for lineage in row.lineage():
            assert lineage.is_complete
            assert lineage.cell.row == 2
            assert lineage.source_content_hash == grid.content_hash

    def test_the_lineage_keeps_the_raw_text_before_normalisation(self, tmp_path: Path) -> None:
        """`38,867.16` with its separator, so a reader can find the same cell in the file."""
        path = tmp_path / "b.csv"
        path.write_text(
            "Policy Reference,Period,Gross Premium\nPOL-202601-00001,2026-01,\"38,867.16\"\n",
            encoding="utf-8",
        )
        grid = read_grid(path, sheet="Bordereau")
        outcome = build_canonical_rows(
            grid,
            {
                "Policy Reference": "policy_reference",
                "Period": "period",
                "Gross Premium": "gross_premium",
            },
            INSURANCE,
            CoverholderProfile(coverholder="T", currency=Currency.GBP),
        )
        assert outcome.rows[0].gross.lineage.raw_value == "38,867.16"


class TestAdapterBoundary:
    def test_both_families_are_registered(self) -> None:
        assert set(known_families()) == {"insurance", "marketplace"}

    def test_the_two_families_have_different_field_counts(self) -> None:
        """A second adapter that mirrored the first would not test generality at all."""
        insurance = len(get_adapter("insurance").fields)
        marketplace = len(get_adapter("marketplace").fields)
        assert insurance != marketplace

    def test_each_family_computes_its_own_identity(self) -> None:
        insurance_key = get_adapter("insurance").identity(
            {"policy_reference": "POL-1", "period": "2026-01"}
        )
        marketplace_key = get_adapter("marketplace").identity(
            {"order_reference": "ORD-1", "period": "2026-01"}
        )
        assert insurance_key == "POL-1|2026-01"
        assert marketplace_key != insurance_key

    def test_an_unknown_family_raises(self) -> None:
        with pytest.raises(KeyError):
            get_adapter("shipping")
