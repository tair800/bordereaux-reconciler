"""The schema-variant catalogue: every way a coverholder file differs from the canonical shape.

**Each variant is a named, documented perturbation combination, not a random draw.** Eighteen of
them, which is not many — and deliberately so. A generator that emitted two hundred layouts would
be measuring the same four or five difficulties over and over and reporting the repetition as
coverage. What makes a corpus hard is that each variant breaks something *different*, and that
every break has a name a failure report can cite.

The perturbations ADR-001 names, and where they live:

| perturbation | variants |
|---|---|
| renamed / legacy column names | `ins_02`, `mkt_04` |
| reordered columns | `ins_03`, `mkt_04` |
| absent optional columns | `ins_02`, `mkt_02`, `mkt_03` |
| localised date formats | all four formats appear; `ins_10` is ambiguous on purpose |
| currency formatting | symbol, code suffix, and a non-breaking space (`ins_10`) |
| decimal conventions | `1,234.56` and `1.234,56`, and **the variant declares which** |
| split vs combined premium and tax | `ins_04` (commission), `ins_05` (premium/tax) |
| net vs gross representation | `ins_06` swaps their meanings; `mkt_03` omits net entirely |
| duplicated rows | `ins_07` |
| abbreviated headers | `ins_03`, `mkt_02` |

**A variant declares its decimal convention; nothing infers it.** ADR-001 forbids inferring it and
:mod:`bordereaux_reconciler.money` refuses to, because `1.234` is a thousand in one convention and
one in the other and no amount of staring at the cell resolves that. The declaration lives here, in
the fixture definition, so the corpus can be read by a consumer that obeys the same rule.

Two variants are **deliberately unmappable** and carry `unmappable=True`. They exist for ADR-001's
kill condition F: a reconciler that always produces an answer produces wrong ones, so there has to
be a file it must refuse. They are not malformed — they are perfectly valid spreadsheets that
simply are not bordereaux, which is what actually arrives when somebody attaches the wrong tab.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from bordereaux_reconciler.corpus.truth import Family
from bordereaux_reconciler.money import Currency, DecimalConvention

__all__ = [
    "VARIANTS",
    "ColumnSpec",
    "CurrencyStyle",
    "DateFormat",
    "FileFormat",
    "PeriodFormat",
    "TotalMode",
    "VariantSpec",
]


class DateFormat(StrEnum):
    """How a variant writes a date. ADR-001 requires all four to appear somewhere in the corpus."""

    DMY_SLASH = "DD/MM/YYYY"
    MDY_SLASH = "MM/DD/YYYY"
    ISO = "YYYY-MM-DD"
    WRITTEN_MONTH = "D Month YYYY"


class PeriodFormat(StrEnum):
    """How a variant writes the reporting month. The ground truth is always `YYYY-MM`."""

    ISO_MONTH = "YYYY-MM"
    SLASH = "MM/YYYY"
    WRITTEN = "Month YYYY"


class CurrencyStyle(StrEnum):
    """How an amount wears its currency.

    `SYMBOL_NBSP` is not a curiosity. Excel writes U+00A0 between a symbol and a figure, so files
    from a finance team routinely contain it, and a parser that only stripped ordinary spaces
    rejects every row in the file for a reason nobody can see by looking.
    """

    NONE = "none"
    SYMBOL_PREFIX = "symbol_prefix"
    CODE_SUFFIX = "code_suffix"
    SYMBOL_NBSP = "symbol_nbsp"


class FileFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class TotalMode(StrEnum):
    """Whether the file carries a declared total footer, and whether it tells the truth.

    ADR-001 reconciles a declared total against the file's own rows *independently* of the row
    reconciliation, because the two disagree in either direction and each direction is a different
    finding. Both directions are in the corpus.
    """

    NONE = "none"
    AGREES = "agrees_with_rows"
    DISAGREES = "disagrees_with_rows"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column of a source file: what it is called, what it means, and what fills it.

    `field` is the **true canonical target**, and `None` means the column is deliberately
    unmapped — a decoy, a derivable duplicate, or an artefact of the coverholder's own export.
    Those are not oversights, and recording them as `None` rather than dropping them from the
    catalogue is what lets the mapping evaluation distinguish "correctly left alone" from "missed".

    `producer` says which value goes in the cell, which is not always the same question: a split
    commission has two columns whose `field` is the same canonical deduction, and a decoy column
    has a real value and no field at all.
    """

    header: str
    field: str | None
    producer: str


@dataclass(frozen=True, slots=True)
class VariantSpec:
    """One coverholder layout, fixed before anything was generated or scored."""

    variant_id: str
    family: Family
    currency: Currency
    period: str
    rows: int
    decimal: DecimalConvention
    dates: DateFormat
    periods: PeriodFormat
    currency_style: CurrencyStyle
    file_format: FileFormat
    sheet_name: str
    columns: tuple[ColumnSpec, ...]
    perturbations: tuple[str, ...]
    #: Adversarial case identifiers this variant carries, spelled exactly as ADR-001 spells them.
    adversarial: tuple[str, ...] = ()
    total: TotalMode = TotalMode.NONE
    #: Negatives as `(1,234.56)` rather than `-1,234.56`. Accounting exports do both.
    parenthesised_negatives: bool = False
    #: A UTF-8 byte-order mark, which is what Excel writes when a user saves a CSV on Windows.
    byte_order_mark: bool = False
    refunds: bool = False
    #: Every header is unseen elsewhere in the corpus. Forces the variant into the hold-out — see
    #: :mod:`bordereaux_reconciler.corpus.manifest` for why that clause exists.
    vocabulary_is_unseen: bool = False
    #: Nothing in this file maps to anything. The correct outcome is a quarantine, not a ledger.
    unmappable: bool = False
    notes: str = ""
    attributes: frozenset[str] = field(default_factory=frozenset)

    @property
    def column_mapping(self) -> dict[str, str]:
        """The true mapping, `source header -> canonical field`. What mapping accuracy is scored on.

        Two headers may share a value — that is a split deduction, and it is the correct answer
        rather than a collision.
        """
        return {c.header: c.field for c in self.columns if c.field is not None}

    @property
    def unmapped_headers(self) -> tuple[str, ...]:
        """Headers that correctly map to nothing. Carried, never silently dropped."""
        return tuple(c.header for c in self.columns if c.field is None)

    @property
    def carries_net(self) -> bool:
        return any(c.field == "net" for c in self.columns)

    @property
    def path(self) -> str:
        """The generated file's path, relative to the corpus root, always POSIX.

        POSIX separators even on Windows: the manifest is a committed artifact compared byte for
        byte between runs and, eventually, between machines, and a backslash would make the corpus
        digest platform-dependent for no reason.
        """
        return f"{self.family}/{self.variant_id}.{self.file_format}"

    @property
    def truth_path(self) -> str:
        return f"{self.family}/{self.variant_id}.truth.json"


# --------------------------------------------------------------------------- producers
#
# A producer names the value that fills a cell. The renderer dispatches on the prefix; the
# vocabulary is small on purpose, because every entry here is a branch somebody has to read.

KEY: Final = "key"
PERIOD: Final = "period"
CURRENCY: Final = "currency"
GROSS: Final = "money:gross"
NET: Final = "money:net"
TAX: Final = "money:deduction:tax"
COMMISSION: Final = "money:deduction:commission"
FEE: Final = "money:deduction:fee"
#: `gross` minus the tax: the premium as a file in the "excluding tax" convention states it.
PREMIUM_EXCL_TAX: Final = "money:premium_excl_tax"
COMMISSION_A: Final = "money:commission_part:0"
COMMISSION_B: Final = "money:commission_part:1"


def _date(attribute: str) -> str:
    return f"date:{attribute}"


def _text(attribute: str) -> str:
    return f"text:{attribute}"


def _opaque(kind: str) -> str:
    return f"opaque:{kind}"


def _cols(*specs: tuple[str, str | None, str]) -> tuple[ColumnSpec, ...]:
    return tuple(ColumnSpec(header=h, field=f, producer=p) for h, f, p in specs)


# --------------------------------------------------------------------------- the catalogue

VARIANTS: Final[tuple[VariantSpec, ...]] = (
    VariantSpec(
        variant_id="ins_01_canonical_gbp",
        family="insurance",
        currency=Currency.GBP,
        period="2026-01",
        rows=150,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.ISO,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.NONE,
        file_format=FileFormat.CSV,
        sheet_name="Bordereau",
        attributes=frozenset({"coverholder", "peril", "inception_date"}),
        columns=_cols(
            ("Policy Reference", "key", KEY),
            ("Period", "period", PERIOD),
            ("Currency", "currency", CURRENCY),
            ("Inception Date", "attributes.inception_date", _date("inception_date")),
            ("Coverholder", "attributes.coverholder", _text("coverholder")),
            ("Peril", "attributes.peril", _text("peril")),
            ("Gross Premium", "gross", GROSS),
            ("Insurance Premium Tax", "deductions.tax", TAX),
            ("Commission", "deductions.commission", COMMISSION),
            ("Net Due to Carrier", "net", NET),
        ),
        perturbations=("baseline",),
        notes="The layout everything else is a deviation from. Not an easy case — a reference one.",
    ),
    VariantSpec(
        variant_id="ins_02_legacy_names_eur",
        family="insurance",
        currency=Currency.EUR,
        period="2026-01",
        rows=140,
        decimal=DecimalConvention.COMMA_DECIMAL,
        dates=DateFormat.DMY_SLASH,
        periods=PeriodFormat.SLASH,
        currency_style=CurrencyStyle.CODE_SUFFIX,
        file_format=FileFormat.CSV,
        sheet_name="Premium BDX",
        attributes=frozenset({"coverholder", "inception_date"}),
        columns=_cols(
            ("Risk Reference", "key", KEY),
            ("Underwriting Period", "period", PERIOD),
            ("Settlement Currency", "currency", CURRENCY),
            ("Date of Inception", "attributes.inception_date", _date("inception_date")),
            ("Coverholder Name", "attributes.coverholder", _text("coverholder")),
            ("Gross Written Premium", "gross", GROSS),
            ("IPT Amount", "deductions.tax", TAX),
            ("Commission Payable", "deductions.commission", COMMISSION),
            ("Settlement Amount", "net", NET),
        ),
        perturbations=(
            "legacy_column_names",
            "comma_decimal_convention",
            "dmy_dates",
            "currency_code_suffix",
            "absent_optional_columns",
        ),
        notes="The vocabulary a 2011 binder template uses, with no peril column at all.",
    ),
    VariantSpec(
        variant_id="ins_03_abbreviated_reordered",
        family="insurance",
        currency=Currency.GBP,
        period="2026-02",
        rows=145,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.ISO,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.SYMBOL_PREFIX,
        file_format=FileFormat.XLSX,
        sheet_name="BDX",
        attributes=frozenset({"inception_date"}),
        columns=_cols(
            ("Net Due", "net", NET),
            ("GWP", "gross", GROSS),
            ("Pol Ref", "key", KEY),
            ("Comm", "deductions.commission", COMMISSION),
            ("IPT", "deductions.tax", TAX),
            ("Ccy", "currency", CURRENCY),
            ("Per", "period", PERIOD),
            ("Inc Dt", "attributes.inception_date", _date("inception_date")),
        ),
        perturbations=("abbreviated_headers", "reordered_columns", "xlsx", "currency_symbol"),
        adversarial=("abbreviated_header",),
        notes="Net first, key third. Column order carries no information and the engine may not "
        "assume it does.",
    ),
    VariantSpec(
        variant_id="ins_04_split_commission_usd",
        family="insurance",
        currency=Currency.USD,
        period="2026-02",
        rows=150,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.MDY_SLASH,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.NONE,
        file_format=FileFormat.CSV,
        sheet_name="Bordereau",
        attributes=frozenset({"coverholder", "inception_date"}),
        columns=_cols(
            ("Policy Ref", "key", KEY),
            ("Reporting Month", "period", PERIOD),
            ("Currency", "currency", CURRENCY),
            ("Effective Date", "attributes.inception_date", _date("inception_date")),
            ("Coverholder", "attributes.coverholder", _text("coverholder")),
            ("Gross Premium", "gross", GROSS),
            ("Surplus Lines Tax", "deductions.tax", TAX),
            ("Broker Commission", "deductions.commission", COMMISSION_A),
            ("Sub-Agent Commission", "deductions.commission", COMMISSION_B),
            ("Net Due", "net", NET),
        ),
        perturbations=("split_deduction_columns", "mdy_dates"),
        adversarial=("split_commission",),
        notes="Two columns, one canonical deduction. Mapping either alone loses money silently.",
    ),
    VariantSpec(
        variant_id="ins_05_tax_basis_ambiguous",
        family="insurance",
        currency=Currency.GBP,
        period="2026-03",
        rows=140,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.DMY_SLASH,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.SYMBOL_PREFIX,
        file_format=FileFormat.CSV,
        sheet_name="Bordereau",
        attributes=frozenset({"inception_date"}),
        columns=_cols(
            ("Policy Reference", "key", KEY),
            ("Period", "period", PERIOD),
            ("Currency", "currency", CURRENCY),
            ("Inception", "attributes.inception_date", _date("inception_date")),
            ("Premium (excl IPT)", None, PREMIUM_EXCL_TAX),
            ("IPT", "deductions.tax", TAX),
            ("Total Due (incl IPT)", "gross", GROSS),
            ("Commission", "deductions.commission", COMMISSION),
            ("Net to Carrier", "net", NET),
        ),
        perturbations=("combined_and_split_premium_tax", "decoy_unmapped_column"),
        adversarial=("tax_included_vs_excluded",),
        notes="Both bases present. Only the tax-inclusive column is canonical gross; the "
        "tax-exclusive one is derivable and maps to nothing, which is the answer a header-only "
        "method gets wrong.",
    ),
    VariantSpec(
        variant_id="ins_06_swapped_gross_net",
        family="insurance",
        currency=Currency.GBP,
        period="2026-03",
        rows=135,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.ISO,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.NONE,
        file_format=FileFormat.CSV,
        sheet_name="Bordereau",
        attributes=frozenset({"inception_date"}),
        columns=_cols(
            ("Policy Reference", "key", KEY),
            ("Period", "period", PERIOD),
            ("Currency", "currency", CURRENCY),
            ("Inception Date", "attributes.inception_date", _date("inception_date")),
            ("Premium", "net", NET),
            ("Tax", "deductions.tax", TAX),
            ("Commission", "deductions.commission", COMMISSION),
            ("Total Payable", "gross", GROSS),
        ),
        perturbations=("net_vs_gross_representation", "misleading_header_semantics"),
        adversarial=("swapped_gross_net",),
        notes="`Premium` here is the remittance and `Total Payable` is the gross. Header text says "
        "the opposite; the row arithmetic says which is which, and only the arithmetic is right.",
    ),
    VariantSpec(
        variant_id="ins_07_duplicates_and_conflicts",
        family="insurance",
        currency=Currency.GBP,
        period="2026-04",
        rows=160,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.ISO,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.NONE,
        file_format=FileFormat.CSV,
        sheet_name="Bordereau",
        attributes=frozenset({"coverholder", "inception_date"}),
        columns=_cols(
            ("Policy Reference", "key", KEY),
            ("Period", "period", PERIOD),
            ("Currency", "currency", CURRENCY),
            ("Inception Date", "attributes.inception_date", _date("inception_date")),
            ("Coverholder", "attributes.coverholder", _text("coverholder")),
            ("Gross Premium", "gross", GROSS),
            ("Insurance Premium Tax", "deductions.tax", TAX),
            ("Commission", "deductions.commission", COMMISSION),
            ("Net Due to Carrier", "net", NET),
        ),
        perturbations=("duplicated_rows", "conflicting_keys", "colliding_amounts"),
        adversarial=(
            "duplicate_row",
            "same_policy_different_premium",
            "same_amount_different_policy",
        ),
        notes="The three identity traps in one file, because in practice they arrive together.",
    ),
    VariantSpec(
        variant_id="ins_08_total_footer_disagrees",
        family="insurance",
        currency=Currency.GBP,
        period="2026-04",
        rows=150,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.DMY_SLASH,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.SYMBOL_PREFIX,
        file_format=FileFormat.CSV,
        sheet_name="Bordereau",
        attributes=frozenset({"inception_date"}),
        columns=_cols(
            ("Policy Reference", "key", KEY),
            ("Period", "period", PERIOD),
            ("Currency", "currency", CURRENCY),
            ("Inception Date", "attributes.inception_date", _date("inception_date")),
            ("Gross Premium", "gross", GROSS),
            ("Insurance Premium Tax", "deductions.tax", TAX),
            ("Commission", "deductions.commission", COMMISSION),
            ("Net Due to Carrier", "net", NET),
        ),
        total=TotalMode.DISAGREES,
        perturbations=("declared_total_footer",),
        adversarial=("rows_agree_total_does_not",),
        notes="The rows are right and the footer is not: one policy is missing from the "
        "coverholder's own sum.",
    ),
    VariantSpec(
        variant_id="ins_09_total_agrees_rows_transposed",
        family="insurance",
        currency=Currency.EUR,
        period="2026-05",
        rows=150,
        decimal=DecimalConvention.COMMA_DECIMAL,
        dates=DateFormat.DMY_SLASH,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.CODE_SUFFIX,
        file_format=FileFormat.CSV,
        sheet_name="Bordereau",
        attributes=frozenset({"inception_date"}),
        columns=_cols(
            ("Policy Reference", "key", KEY),
            ("Period", "period", PERIOD),
            ("Currency", "currency", CURRENCY),
            ("Inception Date", "attributes.inception_date", _date("inception_date")),
            ("Gross Premium", "gross", GROSS),
            ("IPT", "deductions.tax", TAX),
            ("Commission", "deductions.commission", COMMISSION),
            ("Net Due to Carrier", "net", NET),
        ),
        total=TotalMode.AGREES,
        perturbations=(
            "declared_total_footer",
            "transposed_row_amounts",
            "comma_decimal_convention",
        ),
        adversarial=("totals_agree_rows_do_not",),
        notes="The footer reconciles perfectly and two policies have each other's money. This is "
        "the case a total-level check is structurally blind to, which is why ADR-001 reconciles "
        "totals and rows independently.",
    ),
    VariantSpec(
        variant_id="ins_10_localised_dates_nbsp",
        family="insurance",
        currency=Currency.EUR,
        period="2026-05",
        rows=145,
        decimal=DecimalConvention.COMMA_DECIMAL,
        dates=DateFormat.DMY_SLASH,
        periods=PeriodFormat.WRITTEN,
        currency_style=CurrencyStyle.SYMBOL_NBSP,
        file_format=FileFormat.XLSX,
        sheet_name="Bordereau 05-2026",
        attributes=frozenset({"coverholder", "inception_date"}),
        columns=_cols(
            ("Policy Reference", "key", KEY),
            ("Accounting Period", "period", PERIOD),
            ("Currency", "currency", CURRENCY),
            ("Inception Date", "attributes.inception_date", _date("inception_date")),
            ("Coverholder", "attributes.coverholder", _text("coverholder")),
            ("Gross Premium", "gross", GROSS),
            ("IPT", "deductions.tax", TAX),
            ("Commission", "deductions.commission", COMMISSION),
            ("Net Due to Carrier", "net", NET),
        ),
        perturbations=(
            "dmy_dates",
            "comma_decimal_convention",
            "non_breaking_space_currency",
            "written_month_period",
            "xlsx",
        ),
        adversarial=("localised_date",),
        notes="Every amount has a U+00A0 in it and one row's date is valid under both DD/MM and "
        "MM/DD with different answers.",
    ),
    VariantSpec(
        variant_id="ins_11_unseen_vocabulary",
        family="insurance",
        currency=Currency.GBP,
        period="2026-06",
        rows=140,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.WRITTEN_MONTH,
        periods=PeriodFormat.WRITTEN,
        currency_style=CurrencyStyle.CODE_SUFFIX,
        file_format=FileFormat.CSV,
        sheet_name="Statement",
        byte_order_mark=True,
        vocabulary_is_unseen=True,
        attributes=frozenset({"coverholder", "inception_date"}),
        columns=_cols(
            ("Contract Reference", "key", KEY),
            ("Accounting Month", "period", PERIOD),
            ("Denomination", "currency", CURRENCY),
            ("Attachment Date", "attributes.inception_date", _date("inception_date")),
            ("Delegated Authority", "attributes.coverholder", _text("coverholder")),
            ("Consideration", "gross", GROSS),
            ("Fiscal Charge", "deductions.tax", TAX),
            ("Intermediary Remuneration", "deductions.commission", COMMISSION),
            ("Remittance", "net", NET),
        ),
        perturbations=(
            "unseen_vocabulary",
            "written_month_dates",
            "utf8_byte_order_mark",
            "currency_code_suffix",
        ),
        adversarial=("unseen_synonym",),
        notes="Not one of these nine headers appears anywhere else in the corpus. A curated alias "
        "table has never seen them, which is the point of holding this variant out.",
    ),
    VariantSpec(
        variant_id="ins_12_unmappable_pivot",
        family="insurance",
        currency=Currency.GBP,
        period="2026-06",
        rows=60,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.ISO,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.NONE,
        file_format=FileFormat.CSV,
        sheet_name="Summary",
        unmappable=True,
        columns=_cols(
            ("Region", None, _opaque("region")),
            ("Band", None, _opaque("band")),
            ("Q1", None, _opaque("quarter")),
            ("Q2", None, _opaque("quarter")),
            ("Q3", None, _opaque("quarter")),
            ("Q4", None, _opaque("quarter")),
            ("Var %", None, _opaque("percent")),
        ),
        perturbations=("unmappable_aggregate_export",),
        notes="A quarterly pivot from the same workbook, attached by mistake. It has numbers and "
        "no policy, no currency and no period — so there is nothing to map and the only correct "
        "behaviour is to quarantine it.",
    ),
    VariantSpec(
        variant_id="mkt_01_canonical_usd",
        family="marketplace",
        currency=Currency.USD,
        period="2026-03",
        rows=150,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.MDY_SLASH,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.NONE,
        file_format=FileFormat.CSV,
        sheet_name="Settlement",
        attributes=frozenset({"seller_id", "channel", "settlement_date"}),
        columns=_cols(
            ("Order Reference", "key", KEY),
            ("Settlement Period", "period", PERIOD),
            ("Currency", "currency", CURRENCY),
            ("Settlement Date", "attributes.settlement_date", _date("settlement_date")),
            ("Seller ID", "attributes.seller_id", _text("seller_id")),
            ("Channel", "attributes.channel", _text("channel")),
            ("Gross Sales", "gross", GROSS),
            ("Marketplace Fee", "deductions.fee", FEE),
            ("Seller Payout", "net", NET),
        ),
        perturbations=("baseline", "non_insurance_family", "mdy_dates"),
        notes="Claim 2's reference layout: the same three money slots with none of the insurance "
        "vocabulary anywhere in the file.",
    ),
    VariantSpec(
        variant_id="mkt_02_abbreviated_eur",
        family="marketplace",
        currency=Currency.EUR,
        period="2026-03",
        rows=140,
        decimal=DecimalConvention.COMMA_DECIMAL,
        dates=DateFormat.DMY_SLASH,
        periods=PeriodFormat.SLASH,
        currency_style=CurrencyStyle.CODE_SUFFIX,
        file_format=FileFormat.XLSX,
        sheet_name="Sheet1",
        attributes=frozenset({"seller_id", "settlement_date"}),
        columns=_cols(
            ("Ord Ref", "key", KEY),
            ("Per", "period", PERIOD),
            ("Ccy", "currency", CURRENCY),
            ("Sttl Dt", "attributes.settlement_date", _date("settlement_date")),
            ("Slr", "attributes.seller_id", _text("seller_id")),
            ("Gross", "gross", GROSS),
            ("Fee", "deductions.fee", FEE),
            ("Payout", "net", NET),
        ),
        perturbations=(
            "abbreviated_headers",
            "comma_decimal_convention",
            "absent_optional_columns",
            "xlsx",
        ),
        notes="`Per` and `Ccy` mean the same here as in ins_03, which is the cross-family "
        "overlap a domain-neutral engine is supposed to exploit.",
    ),
    VariantSpec(
        variant_id="mkt_03_no_net_column",
        family="marketplace",
        currency=Currency.GBP,
        period="2026-04",
        rows=145,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.ISO,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.SYMBOL_PREFIX,
        file_format=FileFormat.CSV,
        sheet_name="Settlement",
        attributes=frozenset({"seller_id", "category", "settlement_date"}),
        columns=_cols(
            ("Order Reference", "key", KEY),
            ("Settlement Period", "period", PERIOD),
            ("Currency", "currency", CURRENCY),
            ("Settlement Date", "attributes.settlement_date", _date("settlement_date")),
            ("Seller ID", "attributes.seller_id", _text("seller_id")),
            ("Category", "attributes.category", _text("category")),
            ("Gross Sales", "gross", GROSS),
            ("Marketplace Fee", "deductions.fee", FEE),
        ),
        perturbations=("absent_optional_columns", "net_absent"),
        notes="No payout column at all, so the ground truth's `net` is null — which is exactly the "
        "case the canonical shape allows a null for, and not a missing label.",
    ),
    VariantSpec(
        variant_id="mkt_04_legacy_reordered_refunds",
        family="marketplace",
        currency=Currency.USD,
        period="2026-04",
        rows=150,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.MDY_SLASH,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.NONE,
        file_format=FileFormat.CSV,
        sheet_name="Remittance",
        parenthesised_negatives=True,
        refunds=True,
        attributes=frozenset({"settlement_date"}),
        columns=_cols(
            ("Net Remittance", "net", NET),
            ("Transaction Id", "key", KEY),
            ("Commission Charged", "deductions.fee", FEE),
            ("Merchandise Value", "gross", GROSS),
            ("Statement Period", "period", PERIOD),
            ("Currency Code", "currency", CURRENCY),
            ("Disbursement Date", "attributes.settlement_date", _date("settlement_date")),
        ),
        perturbations=(
            "legacy_column_names",
            "reordered_columns",
            "negative_refund_rows",
            "accounting_parentheses",
        ),
        notes="Refunds are negative on every money column and written as `(12.34)`. A parser that "
        "read that as positive would report a credit as a payment.",
    ),
    VariantSpec(
        variant_id="mkt_05_unseen_vocabulary",
        family="marketplace",
        currency=Currency.GBP,
        period="2026-05",
        rows=140,
        decimal=DecimalConvention.COMMA_DECIMAL,
        dates=DateFormat.WRITTEN_MONTH,
        periods=PeriodFormat.WRITTEN,
        currency_style=CurrencyStyle.CODE_SUFFIX,
        file_format=FileFormat.CSV,
        sheet_name="Cycle",
        vocabulary_is_unseen=True,
        attributes=frozenset({"settlement_date"}),
        columns=_cols(
            ("Settlement Line", "key", KEY),
            ("Cycle", "period", PERIOD),
            ("Unit of Account", "currency", CURRENCY),
            ("Value Date", "attributes.settlement_date", _date("settlement_date")),
            ("Consideration Gross", "gross", GROSS),
            ("Platform Charge", "deductions.fee", FEE),
            ("Balance Transferred", "net", NET),
        ),
        perturbations=(
            "unseen_vocabulary",
            "written_month_dates",
            "comma_decimal_convention",
            "currency_code_suffix",
        ),
        adversarial=("unseen_synonym",),
        notes="The non-insurance half of the unseen-vocabulary hold-out. A GBP marketplace file "
        "written in comma-decimal, which is the combination a header-only method has no defence "
        "against.",
    ),
    VariantSpec(
        variant_id="mkt_06_unmappable_positional",
        family="marketplace",
        currency=Currency.USD,
        period="2026-05",
        rows=50,
        decimal=DecimalConvention.DOT_DECIMAL,
        dates=DateFormat.ISO,
        periods=PeriodFormat.ISO_MONTH,
        currency_style=CurrencyStyle.NONE,
        file_format=FileFormat.CSV,
        sheet_name="export",
        unmappable=True,
        columns=_cols(
            ("field_0", None, _opaque("token")),
            ("field_1", None, _opaque("token")),
            ("field_2", None, _opaque("count")),
            ("field_3", None, _opaque("count")),
            ("field_4", None, _opaque("token")),
            ("field_5", None, _opaque("band")),
            ("field_6", None, _opaque("count")),
        ),
        perturbations=("unmappable_positional_export",),
        notes="A positional dump with the header row replaced by indices. The values are opaque "
        "tokens and bare counts, so neither the header nor the value shape identifies anything.",
    ),
)
