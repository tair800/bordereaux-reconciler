"""The non-insurance fixture family: marketplace seller settlement statements.

This exists to falsify claim 2. If reconciling it needed one change outside `adapters/`, the
portability claim would be decoration, and kill condition G is what says so.

It is a genuinely different vocabulary — orders rather than policies, a platform fee rather than
tax and commission, a payout rather than a net due — mapped onto the same three money slots. That
it fits without the engine noticing is the entire point: the engine sums `deductions` and compares
`net`, and has no opinion about what a marketplace is.

The asymmetry is deliberate too. Insurance rows carry **two** deductions and marketplace rows carry
**one**, so a reconciler that had quietly assumed a fixed number of deduction columns would break
here rather than in production.
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


class MarketplaceAdapter:
    family = "marketplace"

    fields: tuple[CanonicalField, ...] = (
        CanonicalField(
            name="order_reference",
            role=KEY,
            label="Order reference",
            synonyms=(
                "order reference",
                "order id",
                "order no",
                "order number",
                "transaction id",
                "txn id",
                "settlement reference",
                "order ref",
                "sales order",
            ),
            shape=Shape(pattern=r"^[A-Z]{2,4}[-/]?\d{4,10}$", monetary=0.0),
        ),
        CanonicalField(
            name="period",
            role=PERIOD,
            label="Settlement period",
            synonyms=(
                "period",
                "settlement period",
                "month",
                "payout period",
                "statement month",
                "order date",
                "settlement date",
                "transaction date",
            ),
            shape=Shape(temporal=0.9, monetary=0.0),
        ),
        CanonicalField(
            name="gross_sales",
            role=GROSS,
            label="Gross sales",
            synonyms=(
                "gross sales",
                "gross",
                "item total",
                "order total",
                "gross revenue",
                "sales value",
                "gross amount",
                "total sales",
                "merchandise value",
            ),
            shape=Shape(monetary=0.99, magnitude_rank=0),
        ),
        CanonicalField(
            name="fee",
            role=DEDUCTION,
            label="Marketplace fee",
            required=False,
            synonyms=(
                "fee",
                "marketplace fee",
                "platform fee",
                "commission fee",
                "selling fee",
                "referral fee",
                "service fee",
                "fees",
            ),
            shape=Shape(monetary=0.99, magnitude_rank=1),
        ),
        CanonicalField(
            name="payout",
            role=NET,
            label="Seller payout",
            required=False,
            synonyms=(
                "payout",
                "seller payout",
                "net payout",
                "amount payable",
                "net proceeds",
                "settlement amount",
                "net",
                "disbursement",
            ),
            shape=Shape(monetary=0.99, magnitude_rank=0),
        ),
        CanonicalField(
            name="seller_name",
            role=ATTRIBUTE,
            label="Seller",
            required=False,
            synonyms=("seller", "seller name", "merchant", "merchant name", "vendor", "store"),
            shape=Shape(monetary=0.0, categorical=False),
        ),
        CanonicalField(
            name="category",
            role=ATTRIBUTE,
            label="Category",
            required=False,
            synonyms=("category", "product category", "department", "segment", "item category"),
            shape=Shape(monetary=0.0, categorical=True),
        ),
    )

    def identity(self, values: dict[str, str]) -> str:
        """Order reference plus period, for the same reason the insurance adapter uses both."""
        return f"{values.get('order_reference', '')}|{values.get('period', '')}"


ADAPTER = register(MarketplaceAdapter())
