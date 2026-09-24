"""Deciding which source column is which canonical field, and the four baselines it must beat.

ADR-001 predeclares the comparison: exact header match, normalised header match, fuzzy header
match, and a curated synonym table. All four are in this file, next to the system, because a
baseline kept in the evaluator drifts from the thing it is supposed to be a baseline *for*.

**The system's contribution is that a shape can veto a header.** Every baseline here answers "what
does this column appear to be called"; the system also asks "what is actually in it", and when the
two disagree the values win. That is not a preference — it is the only safe direction. A column
headed `Premium` that contains dates is not a premium column, and mapping it because the header was
persuasive is how a reconciler ends up comparing a date to an amount and reporting a mismatch
nobody can explain.

**Below a declared floor it maps nothing.** Kill condition F requires a deliberately unmappable
file to be quarantined rather than guessed at, and that is this module refusing to assign rather
than some later component noticing the results look odd.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

from bordereaux_reconciler.adapters import Adapter, CanonicalField
from bordereaux_reconciler.domain import MappingConfidence, MappingProposal, UnmappedColumn
from bordereaux_reconciler.ingest.profile import ColumnProfile

__all__ = [
    "BASELINES",
    "MappingOutcome",
    "normalise_header",
    "propose_mapping",
]

#: Below this combined score nothing is assigned. A column that scores under it becomes an
#: `UnmappedColumn`, and a *required* field left unmapped quarantines the file. Set once, before
#: any result existed, and not tuned afterwards — ADR-001 fixes that.
ASSIGNMENT_FLOOR = 0.55

#: Fuzzy ratio below which a header carries no information at all. The fuzzy baseline uses the same
#: number, so the baseline is not handicapped relative to the system.
FUZZY_FLOOR = 80

#: A column must look this monetary for a money field to accept it. The veto threshold.
MONETARY_VETO = 0.5

#: What a field's declared shape has to assert before it counts as a claim about the values.
#: A field declaring `monetary=0.99` is saying its column is money; one declaring nothing is
#: saying nothing, and the difference decides whether a mismatch is a veto or a shrug.
DECLARED_SHAPE = 0.9

#: How much of a column has to look like a date before a date field will accept it.
TEMPORAL_VETO = 0.5

#: How much each kind of shape evidence is worth. A declared identifier pattern is the strongest
#: claim a field can make about its values and a bare 'is/is not categorical' is the weakest,
#: and treating them as equal is what let a field asserting nothing win a column from one
#: asserting a pattern it matched exactly.
WEIGHT_PATTERN = 3.0
WEIGHT_MONETARY = 2.0
WEIGHT_TEMPORAL = 2.0
WEIGHT_MAGNITUDE = 2.0
WEIGHT_CATEGORICAL = 1.0
#: As heavy as the monetary and temporal checks. Two temporal columns in one file are common —
#: the reporting month and the date something happened — and this is the only declared shape
#: that separates them, so it has to weigh as much as the one they share.
WEIGHT_NEAR_CONSTANT = 2.0

#: Total declared weight at which a field is treated as having fully described its values.
SPECIFICITY_FULL = 3.0


def normalise_header(header: str) -> str:
    """A header reduced to comparable form.

    Accents folded because a Spanish coverholder writes `Comisión`; punctuation and spacing dropped
    because `Gross Prem.` and `gross_prem` and `GROSS PREM` are the same column three ways. The
    normalised form is what the second and fourth baselines compare, and what the system's header
    evidence starts from.
    """
    folded = unicodedata.normalize("NFKD", header)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()


# --------------------------------------------------------------------------- the four baselines


def _exact_header(
    headers: Sequence[str], adapter: Adapter, profiles: Mapping[str, ColumnProfile]
) -> dict[str, str | None]:
    """Baseline 1. The source header equals a canonical field name, character for character."""
    names = {field.name: field.name for field in adapter.fields}
    return {header: names.get(header) for header in headers}


def _normalised_header(
    headers: Sequence[str], adapter: Adapter, profiles: Mapping[str, ColumnProfile]
) -> dict[str, str | None]:
    """Baseline 2. The same, after `normalise_header` on both sides."""
    names = {normalise_header(field.name): field.name for field in adapter.fields}
    return {header: names.get(normalise_header(header)) for header in headers}


def _fuzzy_header(
    headers: Sequence[str], adapter: Adapter, profiles: Mapping[str, ColumnProfile]
) -> dict[str, str | None]:
    """Baseline 3. Best token-set ratio against the canonical field names, above a cut-off."""
    out: dict[str, str | None] = {}
    for header in headers:
        normalised = normalise_header(header)
        best_field, best_score = None, 0.0
        for field in adapter.fields:
            score = fuzz.token_set_ratio(normalised, normalise_header(field.name))
            if score > best_score:
                best_field, best_score = field.name, score
        out[header] = best_field if best_score >= FUZZY_FLOOR else None
    return out


def _curated_synonyms(
    headers: Sequence[str], adapter: Adapter, profiles: Mapping[str, ColumnProfile]
) -> dict[str, str | None]:
    """Baseline 4, and the one that matters. The adapter's hand-written alias table, nothing else.

    This is what a competent team actually builds, and beating it is the honest bar. It has the
    *same* synonym lists the system uses — the system is not given a better dictionary, it is given
    the values as well.
    """
    table = {
        normalise_header(synonym): field.name
        for field in adapter.fields
        for synonym in (*field.synonyms, field.name)
    }
    return {header: table.get(normalise_header(header)) for header in headers}


#: The four, by the names ADR-001 fixed. `test_E_all_four_predeclared_baselines_were_scored`
#: asserts the evaluator scored exactly these.
BASELINES: dict[
    str, Callable[[Sequence[str], Adapter, Mapping[str, ColumnProfile]], dict[str, str | None]]
] = {
    "exact_header": _exact_header,
    "normalised_header": _normalised_header,
    "fuzzy_header": _fuzzy_header,
    "curated_synonyms": _curated_synonyms,
}


# --------------------------------------------------------------------------------- the system


@dataclass(frozen=True)
class _Score:
    """One (column, field) pairing and why it scored what it did."""

    header: str
    field: str
    header_score: float
    shape_score: float
    vetoed: bool
    reason: str

    @property
    def combined(self) -> float:
        """Noisy-OR over the two signals: `1 - (1 - header)(1 - shape)`.

        **Either signal alone must be able to carry an assignment**, and an average cannot do that.
        The first version here averaged the two, which meant a file whose headers were `Col_A` …
        `Col_F` scored at most 0.5 however perfectly its values matched — so the system mapped
        nothing and tied with the four header-string baselines at 0/6 on exactly the case it exists
        to win. Averaging says "both must agree"; what is true is "either is evidence".

        A veto is not a low score, it is a zero: a field that declares itself money and a column
        that is not money is a wrong answer, and no amount of header agreement may outvote it.
        """
        if self.vetoed:
            return 0.0
        return 1.0 - (1.0 - self.header_score) * (1.0 - self.shape_score)


def _header_evidence(header: str, field: CanonicalField) -> tuple[float, str]:
    """How much the column's *name* suggests this field."""
    normalised = normalise_header(header)
    synonyms = {normalise_header(s) for s in (*field.synonyms, field.name)}
    if normalised in synonyms:
        return 1.0, f"header {header!r} is a declared synonym"
    best = max(
        (fuzz.token_set_ratio(normalised, synonym) for synonym in synonyms),
        default=0.0,
    )
    if best >= FUZZY_FLOOR:
        return float(best) / 100.0, f"header {header!r} is {best:.0f}% similar to a synonym"
    return 0.0, f"header {header!r} matches no synonym"


def _shape_evidence(profile: ColumnProfile, field: CanonicalField) -> tuple[float, bool, str]:
    r"""How much the column's *contents* suggest this field, and whether they rule it out.

    Two things this has to get right, and the first version got neither.

    **The veto.** A field that declares itself monetary, against a column that is not monetary, is
    not a weak match — it is a wrong one, and no header agreement may outvote it. That is the
    `True` in the middle of the return tuple.

    **Specificity.** The evidence is a *weighted* mean, and it is then scaled by how much the field
    actually claimed. A field declaring a `^[A-Z]{2,4}-\d{4,10}$` identifier pattern is making a
    strong, falsifiable claim; a field declaring only "not categorical" is making almost none. The
    first version averaged the checks and scaled by nothing, so `insured_name` — which asserts
    nothing but "text, not a category" — scored a perfect 1.00 against a column of policy
    references and won it on an alphabetical tie-break from `policy_reference`, which had matched
    its pattern exactly. Averaging measures how well the few claims made held up; it does not
    measure how much was claimed, and here the second is what distinguishes the fields.
    """
    shape = field.shape
    weighted: list[tuple[float, float]] = []
    notes: list[str] = []

    if shape.monetary is not None and shape.monetary >= DECLARED_SHAPE:
        if profile.monetary < MONETARY_VETO:
            return (
                0.0,
                True,
                (
                    f"only {profile.monetary:.0%} of the values parse as an amount, and "
                    f"{field.name} is a money field"
                ),
            )
        weighted.append((profile.monetary, WEIGHT_MONETARY))
        notes.append(f"{profile.monetary:.0%} of values parse as money")
    elif shape.monetary == 0.0 and profile.monetary >= DECLARED_SHAPE:
        return 0.0, True, f"the values are money and {field.name} is not a money field"

    if shape.temporal is not None and shape.temporal > 0:
        if profile.temporal < TEMPORAL_VETO:
            return (
                0.0,
                True,
                (
                    f"only {profile.temporal:.0%} of the values look like dates, and "
                    f"{field.name} is a date field"
                ),
            )
        weighted.append((profile.temporal, WEIGHT_TEMPORAL))
        notes.append(f"{profile.temporal:.0%} of values look like dates")

    if (pattern := shape.compiled()) is not None:
        share = profile.matches_pattern(pattern)
        weighted.append((share, WEIGHT_PATTERN))
        notes.append(f"{share:.0%} of values match the declared identifier pattern")

    if shape.categorical is not None:
        agree = profile.categorical == shape.categorical
        weighted.append((1.0 if agree else 0.0, WEIGHT_CATEGORICAL))
        notes.append("categorical as expected" if agree else "not categorical as expected")

    if shape.near_constant is not None:
        agree = profile.near_constant == shape.near_constant
        weighted.append((1.0 if agree else 0.0, WEIGHT_NEAR_CONSTANT))
        notes.append(
            f"{profile.distinct} distinct value(s) across the column, "
            f"{'as expected' if agree else 'which is not what this field expects'}"
        )

    if shape.magnitude_rank is not None and profile.magnitude_rank is not None:
        # The signal a header cannot give: within one file, gross is the largest money column,
        # commission the next, tax the smallest. Exact rank agreement is strong evidence; one rank
        # out is weak; further out is none.
        distance = abs(shape.magnitude_rank - profile.magnitude_rank)
        weighted.append(({0: 1.0, 1: 0.4}.get(distance, 0.0), WEIGHT_MAGNITUDE))
        notes.append(
            f"it is money column #{profile.magnitude_rank} by size and {field.name} expects "
            f"#{shape.magnitude_rank}"
        )

    if not weighted:
        return 0.0, False, "the field declares no shape to check"

    total_weight = sum(w for _, w in weighted)
    quality = sum(value * w for value, w in weighted) / total_weight
    # Scaled by how much was claimed, capped at one. A field that declares a lot and matches it all
    # reaches 1.0; a field that declares one weak thing cannot, however well that one thing held.
    specificity = min(1.0, total_weight / SPECIFICITY_FULL)
    return quality * specificity, False, "; ".join(notes)


@dataclass(frozen=True)
class MappingOutcome:
    """What the system concluded about one file's columns."""

    proposals: tuple[MappingProposal, ...]
    unmapped: tuple[UnmappedColumn, ...]
    #: Required canonical fields nothing was assigned to. Non-empty means the file is quarantined.
    missing_required: tuple[str, ...]

    @property
    def mappable(self) -> bool:
        """Whether a ledger may be produced from this file at all. Kill condition F turns on it."""
        return not self.missing_required

    def as_columns(self) -> dict[str, str]:
        return {p.source_header: p.canonical_field for p in self.proposals}


def propose_mapping(
    headers: Sequence[str],
    profiles: Mapping[str, ColumnProfile],
    adapter: Adapter,
) -> MappingOutcome:
    """Map a file's columns onto the adapter's canonical fields, deterministically.

    Assignment is greedy over the full score matrix, highest first, with each canonical field taken
    at most once. Greedy rather than optimal because an operator has to be able to read the reason a
    column was assigned, and "it scored highest and the field was free" is a sentence; the
    assignment a Hungarian algorithm produces is correct and unexplainable, which is the wrong trade
    in a system whose entire output is evidence.

    Ties break on `(-score, header, field)` so two runs agree — kill condition A again.
    """
    scores: list[_Score] = []
    for header in headers:
        profile = profiles.get(header)
        for field in adapter.fields:
            header_score, header_reason = _header_evidence(header, field)
            if profile is None:
                scores.append(_Score(header, field.name, header_score, 0.0, False, header_reason))
                continue
            shape_score, vetoed, shape_reason = _shape_evidence(profile, field)
            scores.append(
                _Score(
                    header=header,
                    field=field.name,
                    header_score=header_score,
                    shape_score=shape_score,
                    vetoed=vetoed,
                    reason=(
                        f"vetoed: {shape_reason}" if vetoed else f"{header_reason}; {shape_reason}"
                    ),
                )
            )

    # Strongest combined evidence first, then the strongest *single* signal, and only then the
    # alphabet. The middle terms are not decoration. Noisy-OR saturates: once either signal reaches
    # 1.0 the combined score is 1.0 whatever the other one says, so a column whose header matches no
    # synonym at all can tie with one that matches exactly — and sorting on the header text next
    # would settle a question about evidence by spelling. `Inception Date` beat `Period` for the
    # reporting-period field that way, on every layout that carried both.
    scores.sort(key=lambda s: (-s.combined, -s.header_score, -s.shape_score, s.header, s.field))

    taken_fields: set[str] = set()
    taken_headers: set[str] = set()
    proposals: list[MappingProposal] = []

    for score in scores:
        if score.combined < ASSIGNMENT_FLOOR:
            break
        if score.header in taken_headers or score.field in taken_fields:
            continue
        taken_headers.add(score.header)
        taken_fields.add(score.field)
        proposals.append(
            MappingProposal(
                source_header=score.header,
                canonical_field=score.field,
                confidence=MappingConfidence.DETERMINISTIC,
                rationale=(
                    f"{score.reason} (header {score.header_score:.2f}, "
                    f"shape {score.shape_score:.2f})"
                )[:400],
            )
        )

    best_by_header: dict[str, _Score] = {}
    for score in scores:
        if (
            score.header not in best_by_header
            or score.combined > best_by_header[score.header].combined
        ):
            best_by_header[score.header] = score

    unmapped = tuple(
        UnmappedColumn(
            header=header,
            reason=(
                f"best candidate {best_by_header[header].field} scored "
                f"{best_by_header[header].combined:.2f}, below the {ASSIGNMENT_FLOOR} floor: "
                f"{best_by_header[header].reason}"
            )[:400]
            if header in best_by_header
            else "no candidate field",
            considered=tuple(
                s.field for s in sorted(scores, key=lambda s: -s.combined) if s.header == header
            )[:3],
        )
        for header in headers
        if header not in taken_headers
    )

    missing_required = tuple(
        field.name for field in adapter.fields if field.required and field.name not in taken_fields
    )

    return MappingOutcome(
        proposals=tuple(proposals), unmapped=unmapped, missing_required=missing_required
    )
