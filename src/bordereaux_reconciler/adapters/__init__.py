"""The adapter boundary. Everything a domain knows lives on one side of this line.

Claim 2 in ADR-001 says the identical engine reconciles insurance bordereaux and marketplace seller
settlements with adapter-level changes only. That claim is worth nothing as a sentence, so it is
made structural: :mod:`bordereaux_reconciler.reconcile` imports **this package's protocol and
nothing else from it**, and kill condition G fails the build if reconciling the non-insurance
fixtures required a change anywhere outside `adapters/`.

What lives here is everything a reconciler would otherwise be tempted to hard-code:

- the canonical field names for a family, and which of them are money;
- the header synonyms a coverholder might write for each;
- the **shape** each field's values take, which is the evidence a header-string matcher cannot use;
- how a row's identity is built;
- the display labels a person reads.

What deliberately does **not** live here: tolerance, rounding, statuses, and any arithmetic. Those
are the engine's, they are identical for every domain, and a domain that wanted its own arithmetic
would be a domain this project should refuse.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Adapter",
    "CanonicalField",
    "FieldRole",
    "Shape",
    "get_adapter",
    "known_families",
]


class FieldRole(str):
    """Where a canonical field sits in the gross / deductions / net arithmetic.

    A plain string subclass rather than an enum because adapters name their own deductions — the
    engine needs to know *that* `tax` is a deduction, not what a tax is.
    """


#: The three roles the engine understands. Everything else is an attribute.
GROSS: FieldRole = FieldRole("gross")
DEDUCTION: FieldRole = FieldRole("deduction")
NET: FieldRole = FieldRole("net")
ATTRIBUTE: FieldRole = FieldRole("attribute")
KEY: FieldRole = FieldRole("key")
PERIOD: FieldRole = FieldRole("period")


class Shape(BaseModel):
    """What a column's *values* look like, independent of what its header says.

    This is the system's actual contribution over the four predeclared baselines, all of which are
    header-string methods. A coverholder who renames `Gross Premium` to `GWP (excl IPT)` defeats
    exact, normalised and fuzzy matching and usually defeats a synonym table too — but the column
    still contains money, still has two decimal places, and is still the largest of the money
    columns. That is recoverable evidence, and none of the baselines can see it.

    Every predicate is deliberately cheap and deterministic. Nothing here samples, learns or
    thresholds on anything but the counts in front of it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Fraction of non-empty cells that parse as an amount under the declared convention.
    monetary: float | None = None
    #: Fraction that parse as a date under any of the declared formats.
    temporal: float | None = None
    #: Fraction matching this pattern, when the field has a recognisable identifier form.
    pattern: str | None = None
    #: Fraction drawn from a small closed vocabulary — a categorical column.
    categorical: bool | None = None
    #: Rough rank among the money columns by magnitude: 0 is the largest. Gross is almost always 0.
    magnitude_rank: int | None = None

    def compiled(self) -> re.Pattern[str] | None:
        return re.compile(self.pattern) if self.pattern else None


class CanonicalField(BaseModel):
    """One field in a family's canonical vocabulary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    role: str
    label: str = Field(description="what a person reads in the console")
    required: bool = True
    #: Header spellings a coverholder might actually write. Lowercased, punctuation-stripped.
    synonyms: tuple[str, ...] = ()
    #: What the values look like. The evidence the baselines cannot use.
    shape: Shape = Shape()


@runtime_checkable
class Adapter(Protocol):
    """Everything the engine needs to know about a domain, and nothing more.

    A `Protocol` rather than a base class so an adapter is a plain module-level object with no
    inheritance and no framework. The engine type-checks against this and never imports a concrete
    adapter, which is what makes kill condition G checkable by looking at the import graph.
    """

    family: str
    fields: tuple[CanonicalField, ...]

    def identity(self, values: dict[str, str]) -> str:
        """The reconciliation key for a row, from its canonical values.

        Named `identity` rather than `key` because the interesting part is the *policy*: which
        combination of fields makes two rows the same row. Getting that wrong produces `DUPLICATE`
        and `AMBIGUOUS` findings, which is why it belongs to the domain and not to the engine.
        """
        ...


_REGISTRY: dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    _REGISTRY[adapter.family] = adapter
    return adapter


def get_adapter(family: str) -> Adapter:
    if family not in _REGISTRY:
        raise KeyError(f"no adapter for family {family!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[family]


def known_families() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def _load_builtin_adapters() -> None:
    """Import the adapters that ship with the project so the registry is populated.

    At the bottom, and in a function, because the adapter modules import the names defined above.
    A reader who wants to add a family adds a module and a line here; nothing else in the codebase
    learns that the family exists.
    """
    from bordereaux_reconciler.adapters import insurance, marketplace  # noqa: F401, PLC0415


_load_builtin_adapters()
