"""The insurance bordereau vocabulary. Everything domain-specific about premium lives here.

The synonym lists are the boring, valuable part: they are what a delegated-authority team otherwise
keeps in somebody's head. `GWP`, `Gross Prem (excl tax)`, `Written Premium` and `Prem. Gross` are
all the same column, and a coverholder will use whichever their broking system emits.

The shapes are the part the four predeclared baselines cannot use. `IPT` and `Commission` are both
money, both two decimal places, both smaller than gross — but commission is conventionally a share
of gross and IPT a statutory rate, so their magnitude ranks are stable even when their headers are
not. A header-string method has to guess between them; a shape-aware one does not.
"""

from __future__ import annotations

from bordereaux_reconciler.adapters import (
    ATTRIBUTE,
    DEDUCTION,
    GROSS,
    KEY,
    NET,
    PERIOD,
    CanonicalField,
    Shape,
    register,
)


class InsuranceAdapter:
    family = "insurance"

    fields = (
        CanonicalField(
            name="policy_reference",
            role=KEY,
            label="Policy reference",
            synonyms=(
                "policy reference",
                "policy ref",
                "policy no",
                "policy number",
                "policyref",
                "certificate number",
                "cert no",
                "risk reference",
                "unique market reference",
                "umr",
                "pol ref",
                "policy id",
            ),
            shape=Shape(pattern=r"^[A-Z]{2,4}[-/]?\d{4,10}$", monetary=0.0),
        ),
        CanonicalField(
            name="period",
            role=PERIOD,
            label="Reporting period",
            synonyms=(
                "period",
                "reporting period",
                "month",
                "bordereau month",
                "statement period",
                "inception date",
                "effective date",
                "transaction date",
                "booking date",
            ),
            shape=Shape(temporal=0.9, monetary=0.0),
        ),
        CanonicalField(
            name="gross_premium",
            role=GROSS,
            label="Gross written premium",
            synonyms=(
                "gross premium",
                "gross written premium",
                "gwp",
                "written premium",
                "premium gross",
                "prem gross",
                "gross prem",
                "gross prem excl tax",
                "gross premium excluding ipt",
                "total premium",
                "premium",
            ),
            shape=Shape(monetary=0.99, magnitude_rank=0),
        ),
        CanonicalField(
            name="tax",
            role=DEDUCTION,
            label="Insurance premium tax",
            required=False,
            synonyms=(
                "ipt",
                "insurance premium tax",
                "tax",
                "premium tax",
                "ipt amount",
                "tax amount",
                "govt levy",
                "government levy",
            ),
            shape=Shape(monetary=0.99, magnitude_rank=2),
        ),
        CanonicalField(
            name="commission",
            role=DEDUCTION,
            label="Commission",
            required=False,
            synonyms=(
                "commission",
                "brokerage",
                "commission amount",
                "comm",
                "comm amount",
                "broker commission",
                "coverholder commission",
                "acquisition cost",
            ),
            shape=Shape(monetary=0.99, magnitude_rank=1),
        ),
        CanonicalField(
            name="net_premium",
            role=NET,
            label="Net due to carrier",
            required=False,
            synonyms=(
                "net premium",
                "net due",
                "net to carrier",
                "net",
                "nett premium",
                "premium net of commission",
                "net amount due",
                "settlement amount",
            ),
            shape=Shape(monetary=0.99, magnitude_rank=0),
        ),
        CanonicalField(
            name="insured_name",
            role=ATTRIBUTE,
            label="Insured",
            required=False,
            synonyms=(
                "insured",
                "insured name",
                "client",
                "client name",
                "policyholder",
                "assured",
            ),
            shape=Shape(monetary=0.0, categorical=False),
        ),
        CanonicalField(
            name="peril",
            role=ATTRIBUTE,
            label="Peril",
            required=False,
            synonyms=("peril", "cover", "coverage", "class", "class of business", "product", "lob"),
            shape=Shape(monetary=0.0, categorical=True),
        ),
    )

    def identity(self, values: dict[str, str]) -> str:
        """Policy reference plus period.

        Not the policy reference alone. The same policy appears in consecutive monthly bordereaux
        with different premium as endorsements are written, and treating those as one row would
        report every endorsement as a `MISMATCH` against last month's figure — a reconciler that
        cried wolf on normal business would be switched off within a month.
        """
        return f"{values.get('policy_reference', '')}|{values.get('period', '')}"


ADAPTER = register(InsuranceAdapter())
