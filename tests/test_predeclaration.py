"""The guard that stops the kill test from being predeclared and then quietly never run.

Two layers, because one is not enough. `conftest.py` asks pytest what it is about to run — that is
the guard. This file is the second line: it catches what collection cannot see, such as a condition
deleted from the file, a threshold edited downwards, or an assertion that stops reading the
artifacts.

**Everything here is parsed, not grepped.** Project 5's first version of this searched the source
text for `importorskip` and fired on the sentence that explained the skip had been removed — a guard
made wrong by prose. Reading the tree asks the question that matters.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KILL_TEST = ROOT / "tests" / "test_kill_criteria.py"

#: The conditions ADR-001 fixed before implementation. Declared once; both guards read it.
_CONDITIONS = (
    "test_A_two_runs_produce_identical_canonical_values_and_statuses",
    "test_B_re_ingesting_the_same_file_changes_nothing",
    "test_C_every_canonical_cell_carries_its_full_lineage",
    "test_D_no_false_matched_on_the_held_out_set",
    "test_D_the_false_matched_check_is_defined_against_the_declared_tolerance",
    "test_E_mapping_beats_the_best_predeclared_baseline_on_unseen_layouts",
    "test_E_all_four_predeclared_baselines_were_scored",
    "test_F_an_unmappable_schema_is_quarantined_rather_than_guessed",
    "test_G_the_non_insurance_fixtures_run_through_the_same_engine",
    "test_H_the_committed_residency_manifest_matches_the_terraform_module",
    "test_the_corpus_meets_the_size_contract",
    "test_the_corpus_declares_its_generator_and_seed",
    "test_the_adversarial_cases_are_all_present",
)

#: The numbers ADR-001 fixed. Editing one is a visible change to this file, not a tweak.
_THRESHOLDS = {
    "MAX_FALSE_MATCHED": 0,
    "MIN_MAPPING_ACCURACY": 0.90,
    "MIN_SCHEMA_VARIANTS": 12,
    "MIN_HELD_OUT_VARIANTS": 4,
    "MIN_CANONICAL_ROWS": 2_000,
    "REINGESTIONS": 3,
}

#: Ways a test file can switch itself off, matched against parsed calls and decorators.
_SKIPPING = frozenset(
    {
        "pytest.importorskip",
        "importorskip",
        "pytest.skip",
        "pytest.xfail",
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
    }
)


def _tree() -> ast.Module:
    return ast.parse(KILL_TEST.read_text(encoding="utf-8"))


def _dotted(node: ast.expr) -> str:
    """`pytest.mark.skipif` from the attribute chain, or `""` if the node is not a plain name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def test_the_kill_test_still_names_every_predeclared_condition() -> None:
    """ADR-001 fixes eight conditions across thirteen assertions. Losing one is losing it."""
    defined = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    missing = [name for name in _CONDITIONS if name not in defined]

    assert not missing, f"the predeclared kill test lost: {', '.join(missing)}"


def test_the_thresholds_are_the_ones_adr_001_fixed() -> None:
    """The numbers themselves, read from the tree rather than from the text."""
    constants = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in _tree().body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    wrong = {
        name: (constants.get(name), expected)
        for name, expected in _THRESHOLDS.items()
        if constants.get(name) != expected
    }

    assert not wrong, f"a predeclared threshold moved (found, expected): {wrong}"


def test_the_false_matched_threshold_is_exactly_zero() -> None:
    """Called out on its own because it is the one number with no acceptable non-zero value."""
    constants = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in _tree().body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }

    assert constants.get("MAX_FALSE_MATCHED") == 0, (
        "ADR-001 fixes a false MATCHED at zero. A non-zero rate of silently agreeing about money "
        "that does not agree is not a weaker version of this project; it is a different one."
    )


def test_the_predeclaration_skip_does_not_outlive_the_package() -> None:
    """Once `bordereaux_reconciler` imports, the kill test must actually run."""
    if importlib.util.find_spec("bordereaux_reconciler") is None:
        pytest.skip("the package does not exist yet; the kill test is still predeclared")

    found: list[str] = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and (name := _dotted(node.func)) in _SKIPPING:
            found.append(f"{name}() at line {node.lineno}")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if (name := _dotted(target)) in _SKIPPING:
                    found.append(f"@{name} on {node.name}")

    assert not found, (
        "bordereaux_reconciler is importable, so nothing in the kill test may disable itself. "
        f"Found: {', '.join(found)}."
    )


def test_every_condition_reads_a_committed_artifact() -> None:
    """ADR-001: a component that grades itself is not evidence.

    Each condition must reach `_load`, which reads a file from `artifacts/`. A kill test that
    imported the reconciler and asked it how it did would pass against a reconciler that reported
    success and measured nothing.
    """
    bodies = {
        node.name: node
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    silent = [
        name
        for name in _CONDITIONS
        if name in bodies
        and not [
            child
            for child in ast.walk(bodies[name])
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_load"
        ]
    ]

    assert not silent, f"these conditions no longer read a committed artifact: {', '.join(silent)}"


def test_the_kill_test_does_not_import_the_reconciler() -> None:
    """The stronger form of the rule above: it may not reach the thing it is grading at all.

    `importorskip` is the one permitted reference, and only while the package does not exist —
    `test_the_predeclaration_skip_does_not_outlive_the_package` retires it.
    """
    imported = [
        alias.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (node.names if isinstance(node, ast.Import) else [node])
        if str(getattr(alias, "name", None) or getattr(node, "module", "")).startswith(
            "bordereaux_reconciler"
        )
    ]

    assert not imported, (
        f"the kill test imports the code it grades: {imported}. Every condition must be asserted "
        "against a committed artifact instead."
    )
