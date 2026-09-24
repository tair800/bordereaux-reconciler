"""Break the code on purpose, one defect at a time, and check the suite notices.

    python scripts/plant_breaches.py            # every breach
    python scripts/plant_breaches.py --list     # what would be planted
    python scripts/plant_breaches.py --only id  # one of them

A passing test suite is evidence that the tests pass. It is not evidence that they would fail. This
script closes that gap the only way it can be closed: it edits the source to introduce a specific,
named defect, runs the tests that are supposed to catch it, and reports a breach as **escaped** if
they still pass.

Every breach is a plausible mistake rather than a strawman. Deleting a function would be caught by
anything; the ones here are the edits a tired engineer makes on a Friday — a `ROUND_HALF_EVEN`
because it is the Python default, an `abs(a - b) < 0.01` because exact comparison felt fussy, an
`or` where an `and` was meant in a permission check.

**Every edit is reverted**, including when a test run crashes, the script is interrupted, or a
breach cannot be applied. The original bytes are held in memory and written back in a `finally`,
and the script re-verifies the restoration at the end and shouts if anything is still modified.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "bordereaux_reconciler"


@dataclass(frozen=True)
class Breach:
    """One planted defect, and the tests that must go red because of it."""

    identifier: str
    #: What a reviewer should understand went wrong, in one sentence.
    description: str
    path: Path
    old: str
    new: str
    #: Passed to pytest. Narrow on purpose: "the suite failed" is weaker evidence than "the test
    #: written for this failed", and a breach caught only by an unrelated test is a breach whose
    #: intended guard does not work.
    expect_failure_in: tuple[str, ...]


BREACHES: tuple[Breach, ...] = (
    Breach(
        identifier="bankers-rounding",
        description=(
            "round money half-to-even, which is Python's default and every finance system's "
            "opposite — a penny a month, for ever"
        ),
        path=SRC / "money.py",
        old="return value.quantize(exponent, rounding=ROUND_HALF_UP)",
        new="return value.quantize(exponent)",
        expect_failure_in=("tests/test_money.py::TestMoneyArithmetic",),
    ),
    Breach(
        identifier="float-money",
        description="accept a float amount, reintroducing binary representation error",
        path=SRC / "money.py",
        old="""    def _refuse_float(cls, value: Any) -> Any:
        if isinstance(value, float):""",
        new="""    def _refuse_float(cls, value: Any) -> Any:
        if isinstance(value, complex):""",
        expect_failure_in=("tests/test_money.py::TestTheFloatBan",),
    ),
    Breach(
        identifier="approximate-comparison",
        description=(
            "treat amounts within a penny as equal regardless of the declared tolerance — the "
            "single change that would put false MATCHED above zero"
        ),
        path=SRC / "money.py",
        old="            return difference.amount == 0",
        new='            return abs(difference.amount) <= Decimal("0.01")',
        expect_failure_in=(
            "tests/test_money.py::TestTolerance",
            "tests/test_reconcile.py::TestTheSixStatuses",
        ),
    ),
    Breach(
        identifier="tolerance-covers-everything",
        description=(
            "let a tolerance apply to fields it does not name, so widening it for tax silently "
            "widens it for gross"
        ),
        path=SRC / "money.py",
        old="        return field in self.applies_to",
        new="        return True",
        expect_failure_in=("tests/test_money.py::TestTolerance",),
    ),
    Breach(
        identifier="quarantine-becomes-zero",
        description=(
            "treat an unreadable amount as zero instead of holding the row — a ledger that "
            "balances while being wrong"
        ),
        path=SRC / "ingest" / "canonical.py",
        old="""    raw = values.get(canonical, "")
    if not raw.strip():
        return None""",
        new="""    raw = values.get(canonical, "")
    if not raw.strip():
        return Money(amount=Decimal(0), currency=context.profile.currency)""",
        expect_failure_in=("tests/test_ingest.py::TestQuarantineNeverCoerces",),
    ),
    Breach(
        identifier="period-guessing",
        description=(
            "sniff the date order from the values instead of using the coverholder's declaration, "
            "which is right most of the time and therefore never checked"
        ),
        path=SRC / "ingest" / "canonical.py",
        old="            month = second if day_first else first",
        new="            month = min(first, second)",
        expect_failure_in=("tests/test_ingest.py::TestPeriodNormalisation",),
    ),
    Breach(
        identifier="lineage-dropped",
        description="stop recording lineage completeness, so a value nobody can trace passes",
        path=SRC / "domain.py",
        old="""    def is_complete(self) -> bool:""",
        new="""    def is_complete(self) -> bool:
        return True

    @property
    def _unused_is_complete(self) -> bool:""",
        expect_failure_in=("tests/test_kill_criteria.py::test_C",),
    ),
    Breach(
        identifier="write-gate-or",
        description=(
            "use `or` where `and` was meant in the write gate, so an approver token overrules "
            "read-only mode"
        ),
        path=SRC / "config.py",
        old="        return not self.read_only and bool(self.approver_token)",
        new="        return not self.read_only or bool(self.approver_token)",
        expect_failure_in=("tests/test_api.py::TestTheWriteGate",),
    ),
    Breach(
        identifier="unvalidated-proposal",
        description=(
            "store a model's proposed field name without checking it against the adapter, so an "
            "invented field reaches the mapping table"
        ),
        path=SRC / "providers.py",
        old="        if proposal.canonical_field not in known_fields:",
        new="        if False:",
        expect_failure_in=("tests/test_providers.py::TestValidationIsTheContainmentBoundary",),
    ),
    Breach(
        identifier="duplicate-key-written",
        description=(
            "write both versions of a repeated key to the ledger, making every later total "
            "ambiguous"
        ),
        path=SRC / "store" / "ledger.py",
        old="        [row for row in rows if counts[row.key] == 1],",
        new="        list(rows),",
        expect_failure_in=("tests/test_store.py::TestDuplicateKeysAreHeldNotWritten",),
    ),
    Breach(
        identifier="reingestion-appends",
        description=(
            "let a repeat ingestion write rows anyway, so a file re-sent doubles a month's premium"
        ),
        path=SRC / "store" / "ledger.py",
        old="        if inserted is None:",
        new="        if inserted is None and False:",
        expect_failure_in=("tests/test_store.py::TestIdempotentIngestion",),
    ),
    Breach(
        identifier="mapping-ignores-shape",
        description=(
            "score a mapping on the header alone, discarding the value-shape evidence that is the "
            "project's entire contribution over the four baselines"
        ),
        path=SRC / "ingest" / "mapping.py",
        old="        return 1.0 - (1.0 - self.header_score) * (1.0 - self.shape_score)",
        new="        return self.header_score",
        expect_failure_in=("tests/test_ingest.py::TestMapping::test_shape_beats_a_misleading_header",),
    ),
    Breach(
        identifier="no-abstention",
        description=(
            "declare every file mappable, so an unmappable layout produces a confident ledger "
            "instead of a quarantine"
        ),
        path=SRC / "ingest" / "mapping.py",
        old="        return not self.missing_required",
        new="        return True",
        expect_failure_in=(
            "tests/test_ingest.py::TestMapping::test_a_file_missing_a_required_field_is_not_mappable",
        ),
    ),
    Breach(
        identifier="nondeterministic-order",
        description=(
            "walk reconciliation keys in set order rather than sorted, so two runs can disagree"
        ),
        path=SRC / "reconcile.py",
        old="    for key in sorted(set(left) | set(right)):",
        new="    for key in set(left) | set(right):",
        expect_failure_in=("tests/test_reconcile.py::TestDeterminism",),
    ),
    Breach(
        identifier="second-matched-return",
        description=(
            "add a second place MATCHED can be returned, so kill condition D stops being a "
            "property of one guard"
        ),
        path=SRC / "reconcile.py",
        old="    outside = [d for d in discrepancies if not d.within_tolerance]",
        new="""    if len(discrepancies) > 99:
        return ReconciliationResult(
            key=bordereau.key,
            status=Status.MATCHED,
            evidence=Evidence(rule="shortcut", detail="too many fields to check"),
        )
    outside = [d for d in discrepancies if not d.within_tolerance]""",
        expect_failure_in=(
            "tests/test_reconcile.py::TestMatchedIsReachableFromOnePlace",
        ),
    ),
    Breach(
        identifier="ai-reaches-reconciliation",
        description=(
            "import the provider port into the reconciliation engine, creating a call path from a "
            "model to a status"
        ),
        path=SRC / "reconcile.py",
        old="from bordereaux_reconciler.domain import (",
        new=(
            "from bordereaux_reconciler.providers import MappingProvider  # noqa: F401\n"
            "from bordereaux_reconciler.domain import ("
        ),
        expect_failure_in=(
            "tests/test_providers.py::TestTheImportGraph",
            "tests/test_reconcile.py::TestMatchedIsReachableFromOnePlace",
        ),
    ),
    Breach(
        identifier="family-leaks-into-engine",
        description=(
            "name an insurance field inside the reconciliation engine, so the domain escapes its "
            "adapter pack and kill condition G becomes false"
        ),
        path=SRC / "reconcile.py",
        old="    left, right = _duplicates(bordereau), _duplicates(ledger)",
        new=(
            '    _special_case = "gross_premium"  # the leak\n'
            "    left, right = _duplicates(bordereau), _duplicates(ledger)"
        ),
        expect_failure_in=("tests/test_kill_criteria.py::test_G",),
    ),
    Breach(
        identifier="corpus-not-reproducible",
        description=(
            "seed the fixture generator from a counter rather than from what the stream is for, so "
            "inserting one variant re-rolls the whole corpus"
        ),
        path=SRC / "corpus" / "rng.py",
        old='        self.name = "|".join(str(part) for part in parts)',
        new='        self.name = "|".join(str(part) for part in parts) + str(len(parts))',
        expect_failure_in=("tests/test_kill_criteria.py::test_A",),
    ),
    Breach(
        identifier="residency-drifts",
        description=(
            "change a declared region without regenerating the committed manifest, which is the "
            "drift kill condition H exists to catch"
        ),
        path=REPO_ROOT / "infra" / "terraform" / "envs" / "dev" / "residency.auto.tfvars.json",
        old='"location": "swedencentral"',
        new='"location": "uksouth"',
        expect_failure_in=("tests/test_kill_criteria.py::test_H",),
    ),
)


def _run(selector: str) -> bool:
    """True when pytest reports a failure for this selector, which is what a breach must cause."""
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", selector, "-x", "-q", "--no-header"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode != 0


def _plant(breach: Breach) -> tuple[bool, str]:
    """Apply one breach, run its guards, and restore the file whatever happens."""
    # Bytes, not text. `read_text`/`write_text` translate newlines, so restoring a file that way
    # leaves it content-identical and byte-different — which shows up as a modified file in every
    # later `git status` and, on a CRLF checkout, as a diff on every line of the file.
    original = breach.path.read_bytes()
    decoded = original.decode("utf-8")

    # Anchors in this file are written with a bare newline; a Windows checkout has CRLF.
    # Without this translation every multi-line anchor silently failed to apply, and the
    # script reported the breach as unplantable rather than as escaped — which is the more
    # dangerous of the two: it reads as a maintenance chore instead of a hole in the evidence.
    crlf = "\r\n"
    newline = crlf if crlf in decoded else "\n"
    anchor = breach.old.replace("\n", newline)
    replacement = breach.new.replace("\n", newline)

    if anchor not in decoded:
        return False, "could not be applied: the anchor text is no longer in the file"

    try:
        breach.path.write_bytes(decoded.replace(anchor, replacement, 1).encode("utf-8"))
        caught = [selector for selector in breach.expect_failure_in if _run(selector)]
    finally:
        # Unconditional. A breach left in the working tree because a test run crashed is a far worse
        # outcome than a breach that escaped, and the `finally` is the only thing standing between
        # this script and that.
        breach.path.write_bytes(original)
    if breach.path.read_bytes() != original:
        return False, "RESTORATION FAILED: the file does not match its original bytes"

    if len(caught) == len(breach.expect_failure_in):
        return True, f"caught by {', '.join(breach.expect_failure_in)}"
    escaped = set(breach.expect_failure_in) - set(caught)
    return False, f"ESCAPED {', '.join(sorted(escaped))}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the breaches and exit")
    parser.add_argument("--only", help="plant a single breach by identifier")
    args = parser.parse_args(argv)

    selected = [b for b in BREACHES if not args.only or b.identifier == args.only]
    if args.only and not selected:
        print(f"no breach called {args.only!r}", file=sys.stderr)
        return 2

    if args.list:
        for breach in selected:
            print(f"{breach.identifier:28s} {breach.description}")
        return 0

    print(f"planting {len(selected)} breaches\n")
    results: list[tuple[Breach, bool, str]] = []
    for index, breach in enumerate(selected, start=1):
        print(f"[{index:2d}/{len(selected)}] {breach.identifier:28s} ", end="", flush=True)
        caught, detail = _plant(breach)
        results.append((breach, caught, detail))
        print("caught" if caught else f"** {detail} **")

    escaped = [(b, d) for b, caught, d in results if not caught]
    unrestored = [b.identifier for b, _, detail in results if "RESTORATION FAILED" in detail]
    print()
    print(f"{len(results) - len(escaped)}/{len(results)} caught")
    if unrestored:
        # Deliberately not a `git status` check. This runs in a tree that legitimately carries
        # uncommitted work, and comparing against HEAD reported every such file as damage — which
        # is the sort of false alarm that gets a guard ignored. `_plant` compares each file against
        # the exact bytes it read before touching it, which is the question actually being asked.
        print(f"{chr(10)}!! not restored to their original bytes: {unrestored}", file=sys.stderr)
        return 3
    if escaped:
        print("\nescaped:", file=sys.stderr)
        for breach, detail in escaped:
            print(f"  {breach.identifier}: {breach.description}\n    {detail}", file=sys.stderr)
        return 1
    print("every planted defect was caught by the test written for it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
