"""The canonical truth the corpus is generated *from*, and the shape it is published in.

**Truth comes first and the spreadsheet comes second.** Every file under ``data/generated/`` is a
rendering of a row list that already existed, so "what should the reconciler have produced" is never
reverse-engineered from the file it is grading. A corpus whose labels were derived from its own
inputs measures a parser against itself.

**Every monetary value leaves this module as a string.** ``Decimal`` inside, `str` on the way out,
and never a JSON number at any point. `json.dumps(Decimal(...))` does not even work without a
custom encoder, and the obvious fix — `float(amount)` — is precisely the loss this project exists
to prevent. A ground truth of `1234.56` that a consumer reads back as `1234.5599999999999` would
make every exactness claim downstream unprovable, so the string is the contract and the tests in
the consuming lane can assume it absolutely.

**The arithmetic closes by construction.** `net == gross - sum(deductions)` holds exactly for every
row this module builds, because :class:`~bordereaux_reconciler.domain.CanonicalRow` makes that the
rule both families share and a fixture set that violated it accidentally would be indistinguishable
from one that violates it deliberately. The rows that *do* break it are broken on purpose, by
:mod:`bordereaux_reconciler.corpus.adversarial`, and are named there.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from bordereaux_reconciler.corpus.rng import FixtureRandom
from bordereaux_reconciler.money import Currency, parse_amount

__all__ = [
    "Family",
    "TruthRow",
    "insurance_key",
    "insurance_rows",
    "marketplace_key",
    "marketplace_rows",
    "quantise",
    "verify_shape_matches_domain",
]

Family = Literal["insurance", "marketplace"]

#: The top-level canonical fields the published ground truth carries. Checked against
#: :class:`~bordereaux_reconciler.domain.CanonicalRow` at generation time rather than trusted.
CANONICAL_FIELDS: Final = frozenset(
    {"key", "currency", "period", "gross", "deductions", "net", "attributes"}
)

#: Synthetic tax rates. Named as rates for readability only — they are fixture parameters, not a
#: statement about any jurisdiction's statutory rate, and nothing downstream reads them.
_TAX_RATES: Final[dict[Currency, Decimal]] = {
    Currency.GBP: Decimal("0.12"),
    Currency.EUR: Decimal("0.09"),
    Currency.USD: Decimal("0.0475"),
}

_COMMISSION_RATES: Final = (
    Decimal("0.100"),
    Decimal("0.125"),
    Decimal("0.150"),
    Decimal("0.200"),
    Decimal("0.225"),
    Decimal("0.250"),
)

_FEE_RATES: Final = (Decimal("0.08"), Decimal("0.10"), Decimal("0.12"), Decimal("0.15"))

#: Invented organisations. Deliberately not the names of real MGAs or coverholders: the corpus is
#: synthetic and must not read as though a real firm's book had been copied into it.
_COVERHOLDERS: Final = (
    "Northgate Underwriting",
    "Harbour Line MGA",
    "Pennine Risk Partners",
    "Calderon Speciality",
    "Dunmore Binding Authority",
)

_PERILS: Final = (
    "Fire",
    "Flood",
    "Theft",
    "Escape of Water",
    "Storm",
    "Subsidence",
    "Accidental Damage",
    "Business Interruption",
)

#: A PII-shaped column, so the PII-minimisation path has something to minimise — but on the
#: `.invalid` TLD, which RFC 2606 reserves precisely so that it can never resolve to anybody.
_CONTACT_DOMAIN: Final = "example.invalid"

_CHANNELS: Final = ("web", "mobile-app", "partner-api", "retail-kiosk")
_CATEGORIES: Final = ("homewares", "apparel", "electronics", "garden", "pet-supplies")

#: The latest day-of-month any generated date uses. February exists; a fixture that generated the
#: 30th of it would fail to parse for reasons that have nothing to do with schema mapping.
_LAST_SAFE_DAY: Final = 28

#: One refund in this many marketplace rows, on the variants that declare them.
_REFUND_ODDS: Final = 12


@dataclass(frozen=True, slots=True)
class TruthRow:
    """One canonical row, exactly as the published ground truth describes it.

    This is deliberately **not** :class:`~bordereaux_reconciler.domain.CanonicalRow`. That type
    requires a :class:`~bordereaux_reconciler.domain.Lineage` on every value — a content hash, a
    sheet, a row, a column and a mapping version — and none of those exist until a file has been
    ingested. Truth is what the file *means*; lineage is what the ingestion did. Building the
    former out of the latter's type would force this module to invent provenance for values that
    have not been read from anywhere yet.

    :func:`verify_shape_matches_domain` is the guard against the two drifting apart.
    """

    key: str
    currency: Currency
    period: str
    gross: Decimal
    deductions: Mapping[str, Decimal]
    net: Decimal | None
    attributes: Mapping[str, str]

    def to_json(self) -> dict[str, object]:
        """The published shape. Every amount a string; `net` may be `null`, nothing else may."""
        return {
            "key": self.key,
            "currency": str(self.currency),
            "period": self.period,
            "gross": money_str(self.gross),
            "deductions": {name: money_str(value) for name, value in self.deductions.items()},
            "net": None if self.net is None else money_str(self.net),
            "attributes": dict(self.attributes),
        }

    def with_attributes(self, names: frozenset[str], *, keep_net: bool) -> TruthRow:
        """This row reduced to what one variant's file actually carries.

        A variant whose source file has no peril column must not have a peril in its ground truth:
        the mapping evaluation is scored against the true column mapping, and a truth that claimed
        a field no column produced would mark a correct mapping wrong.
        """
        return TruthRow(
            key=self.key,
            currency=self.currency,
            period=self.period,
            gross=self.gross,
            deductions=dict(self.deductions),
            net=self.net if keep_net else None,
            attributes={k: v for k, v in self.attributes.items() if k in names},
        )


def money_str(value: Decimal) -> str:
    """An amount as the published string: fixed point, two decimals, no exponent, no separators.

    `str(Decimal("1E+2"))` is `"1E+2"`, which is arithmetically right and useless to a consumer
    expecting `"100.00"`. Formatting explicitly removes the question.
    """
    amount = quantise(value)
    sign = "-" if amount < 0 else ""
    whole, _, fraction = f"{abs(amount):f}".partition(".")
    return f"{sign}{whole}.{fraction.ljust(2, '0')[:2]}"


def quantise(value: Decimal, currency: Currency = Currency.GBP) -> Decimal:
    """Round to the currency's minor unit using **the engine's own** rounding boundary.

    :func:`~bordereaux_reconciler.money.parse_amount` accepts a `Decimal` and returns it quantised;
    routing through it means the corpus and the reconciler can never round differently. A private
    copy of `ROUND_HALF_UP` here would be the same arithmetic today and a penny of unexplained
    disagreement the first time one of the two changed.
    """
    return parse_amount(value, currency=currency)


def verify_shape_matches_domain() -> None:
    """Fail generation if `CanonicalRow` no longer has the fields the ground truth publishes.

    A subset check, not equality: another lane adding a field is not this lane's problem, but a
    field being renamed or removed silently invalidates every truth file on disk, and a corpus that
    is wrong in a way nobody notices is worse than no corpus.
    """
    from bordereaux_reconciler.domain import CanonicalRow  # noqa: PLC0415

    missing = CANONICAL_FIELDS - set(CanonicalRow.model_fields)
    if missing:
        raise RuntimeError(
            "the published ground truth shape no longer matches CanonicalRow; these canonical "
            f"fields have gone: {sorted(missing)}. Regenerating would publish labels the engine "
            "cannot express."
        )


def insurance_key(period: str, ordinal: int) -> str:
    """A policy reference. The period is in the key because coverholders write it that way."""
    return f"POL-{period.replace('-', '')}-{ordinal:05d}"


def marketplace_key(period: str, ordinal: int) -> str:
    """An order reference. No period: a marketplace order id is global, not per statement."""
    del period  # kept for signature symmetry with insurance_key, which the callers rely on
    return f"ORD-{ordinal:08d}"


def _date_in(period: str, rng: FixtureRandom) -> str:
    year, month = (int(part) for part in period.split("-"))
    return dt.date(year, month, rng.integer(1, _LAST_SAFE_DAY)).isoformat()


def insurance_rows(
    rng: FixtureRandom, *, count: int, currency: Currency, period: str
) -> list[TruthRow]:
    """A month of delegated-authority premium lines.

    The arithmetic is the domain's, not a placeholder: tax is charged on the premium and added to
    what the insured pays, commission is retained by the coverholder out of that premium, and what
    is left is due to the carrier. So canonical `gross` is the full amount charged, `deductions`
    are the tax and the commission, and `net` is the remittance — which makes
    `net == gross - tax - commission` true by construction and true for the right reason.
    """
    tax_rate = _TAX_RATES[currency]
    rows: list[TruthRow] = []
    for ordinal in range(1, count + 1):
        premium = rng.cents(25_000, 4_800_000)
        tax = quantise(premium * tax_rate, currency)
        commission = quantise(premium * rng.choice(_COMMISSION_RATES), currency)
        gross = premium + tax
        rows.append(
            TruthRow(
                key=insurance_key(period, ordinal),
                currency=currency,
                period=period,
                gross=gross,
                deductions={"tax": tax, "commission": commission},
                net=gross - tax - commission,
                attributes={
                    "coverholder": rng.choice(_COVERHOLDERS),
                    "peril": rng.choice(_PERILS),
                    "inception_date": _date_in(period, rng),
                    "broker_contact": f"contact.{ordinal:05d}@{_CONTACT_DOMAIN}",
                },
            )
        )
    return rows


def marketplace_rows(
    rng: FixtureRandom, *, count: int, currency: Currency, period: str, refunds: bool = False
) -> list[TruthRow]:
    """A settlement statement: gross sales, the platform's fee, and what the seller is paid.

    The same three slots carry it, which is the portability claim in data rather than in prose. A
    refund is a genuinely negative line — gross, fee and payout all flip sign — because that is how
    settlement statements report one, and because a reconciler that assumed money is positive is a
    reconciler that will one day report a credit as a match.
    """
    rows: list[TruthRow] = []
    for ordinal in range(1, count + 1):
        gross = rng.cents(500, 350_000)
        fee = quantise(gross * rng.choice(_FEE_RATES), currency)
        if refunds and rng.chance(1, _REFUND_ODDS):
            gross, fee = -gross, -fee
        rows.append(
            TruthRow(
                key=marketplace_key(period, 10_000_000 + ordinal),
                currency=currency,
                period=period,
                gross=gross,
                deductions={"fee": fee},
                net=gross - fee,
                attributes={
                    "seller_id": f"SLR-{rng.integer(1, 900):05d}",
                    "channel": rng.choice(_CHANNELS),
                    "category": rng.choice(_CATEGORIES),
                    "settlement_date": _date_in(period, rng),
                },
            )
        )
    return rows
