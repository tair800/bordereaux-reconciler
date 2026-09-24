"""The predeclared kill test. Written before the package existed.

ADR-001 fixes eight conditions, A-H, any of which fails the project. This file asserts them against
the **committed artifacts** rather than against anything the reconciler reports about itself: a
component that grades itself is not evidence.

It opens with an `importorskip` because it is committed before `bordereaux_reconciler` exists. That
skip is a pre-registration device with a short life — `test_predeclaration.py` fails the build the
moment the package is importable and the skip is still here, and `conftest.py` refuses the whole
session if anything disables these tests by a mark. Two earlier projects in this portfolio shipped a
guard that checked only a literal string and stayed green under `pytest.mark.skip`; that is not
repeated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# The `pytest.importorskip` that stood here is gone, and its removal is enforced rather than
# remembered: `test_predeclaration.py` fails the build if any skipping construct survives in this
# file once `bordereaux_reconciler` is importable. A predeclared test is allowed to skip while it
# has nothing to grade; the moment it does, a skip would make it decoration.

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

#: ADR-001 kill condition D. The one number in this file that is not negotiable in either direction.
MAX_FALSE_MATCHED = 0

#: ADR-001 kill condition E. An absolute floor beneath the comparison, so beating a weak baseline
#: is not enough on its own.
MIN_MAPPING_ACCURACY = 0.90

#: ADR-001's corpus contract.
MIN_SCHEMA_VARIANTS = 12
MIN_HELD_OUT_VARIANTS = 4
MIN_CANONICAL_ROWS = 2_000

#: How many times a file is re-ingested when proving condition B.
REINGESTIONS = 3


def _load(name: str) -> Any:
    path = ARTIFACTS / name
    if not path.is_file():
        pytest.fail(f"{name} is missing; run `make artifacts`")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------- A: determinism


def test_A_two_runs_produce_identical_canonical_values_and_statuses() -> None:
    """Reconciliation that is not reproducible is not evidence of anything."""
    determinism = _load("determinism.json")

    assert determinism["runs"] >= 2, "determinism has to be measured across at least two runs"
    assert determinism["canonical_digest_stable"] is True, (
        f"ADR-001 kill condition A: canonical values differed between runs — "
        f"{determinism['canonical_digests']}"
    )
    assert determinism["status_digest_stable"] is True, (
        f"ADR-001 kill condition A: row statuses differed between runs — "
        f"{determinism['status_digests']}"
    )


# --------------------------------------------------------------------- B: idempotent re-ingestion


def test_B_re_ingesting_the_same_file_changes_nothing() -> None:
    """Claim 1, first half. A corrected file re-sent is the normal case, not the exception."""
    report = _load("idempotency.json")

    assert report["ingestions_per_file"] >= REINGESTIONS, (
        f"each file must be ingested at least {REINGESTIONS} times to prove this"
    )
    assert report["files"] >= 1
    offenders = report["files_with_more_than_one_canonical_version"]
    assert offenders == 0, (
        f"ADR-001 kill condition B: {offenders} file(s) produced more than one canonical version"
    )
    assert report["ledger_rows_changed_after_first_ingestion"] == 0, (
        "ADR-001 kill condition B: a repeat ingestion changed the ledger"
    )


# ----------------------------------------------------------------------------- C: lineage


def test_C_every_canonical_cell_carries_its_full_lineage() -> None:
    """Claim 1, second half. A value nobody can trace to a cell is a value nobody can audit."""
    report = _load("lineage.json")
    required = {"source_content_hash", "sheet", "row", "column", "mapping_version"}

    assert report["cells_checked"] >= MIN_CANONICAL_ROWS, (
        f"only {report['cells_checked']} cells inspected"
    )
    assert set(report["required_fields"]) == required, (
        f"the lineage contract changed: {sorted(report['required_fields'])}"
    )
    assert report["cells_missing_lineage"] == 0, (
        f"ADR-001 kill condition C: {report['cells_missing_lineage']} canonical cells without "
        f"complete lineage; examples {report['examples_missing'][:3]}"
    )


# --------------------------------------------------------------------- D: the one that is zero


def test_D_no_false_matched_on_the_held_out_set() -> None:
    """Silently reporting money as agreeing when it does not has no acceptable rate."""
    evaluation = _load("evaluation.json")
    held_out = evaluation["holdout"]["reconciliation"]

    assert held_out["rows"] > 0, "an empty hold-out cannot fail this"
    assert held_out["false_matched"] <= MAX_FALSE_MATCHED, (
        f"ADR-001 kill condition D: {held_out['false_matched']} false MATCHED rows on the "
        f"hold-out, threshold {MAX_FALSE_MATCHED}. Examples: "
        f"{held_out['false_matched_examples'][:3]}"
    )


def test_D_the_false_matched_check_is_defined_against_the_declared_tolerance() -> None:
    """A definition that drifted would make the zero above meaningless."""
    evaluation = _load("evaluation.json")

    assert evaluation["holdout"]["reconciliation"]["false_matched_definition"] == (
        "reported MATCHED while a reconciled field differs by more than the declared tolerance"
    )


# --------------------------------------------------------------- E: mapping beats the baselines


def test_E_mapping_beats_the_best_predeclared_baseline_on_unseen_layouts() -> None:
    """Measured on schema variants the system has never been tuned against."""
    evaluation = _load("evaluation.json")
    system = evaluation["holdout"]["mapping"]["system"]["accuracy"]
    baselines = evaluation["holdout"]["mapping"]["baselines"]
    best = max(b["accuracy"] for b in baselines.values())
    best_name = max(baselines, key=lambda k: baselines[k]["accuracy"])

    assert system > best, (
        f"ADR-001 kill condition E: system mapping accuracy {system:.4f} does not beat "
        f"{best_name} at {best:.4f}"
    )
    assert system >= MIN_MAPPING_ACCURACY, (
        f"ADR-001 kill condition E: system mapping accuracy {system:.4f} is below the "
        f"absolute floor {MIN_MAPPING_ACCURACY}"
    )


def test_E_all_four_predeclared_baselines_were_scored() -> None:
    """Dropping the strongest baseline is how this criterion would be won dishonestly."""
    evaluation = _load("evaluation.json")
    scored = set(evaluation["holdout"]["mapping"]["baselines"])

    assert scored == {
        "exact_header",
        "normalised_header",
        "fuzzy_header",
        "curated_synonyms",
    }, f"the predeclared baseline set changed: {sorted(scored)}"


# --------------------------------------------------------------------------- F: abstention


def test_F_an_unmappable_schema_is_quarantined_rather_than_guessed() -> None:
    """A reconciler that always produces an answer produces wrong ones."""
    report = _load("abstention.json")

    assert report["unmappable_fixtures"] >= 1, "there has to be a fixture it cannot map"
    assert report["quarantined"] == report["unmappable_fixtures"], (
        f"ADR-001 kill condition F: {report['unmappable_fixtures'] - report['quarantined']} "
        "unmappable file(s) produced a ledger instead of a quarantine"
    )
    assert report["ledger_rows_written_for_unmappable"] == 0


# --------------------------------------------------------------------------- G: portability


def test_G_the_non_insurance_fixtures_run_through_the_same_engine() -> None:
    """Claim 2. The domain lives in an adapter pack or the claim is decoration."""
    report = _load("portability.json")

    assert report["non_insurance_rows_reconciled"] > 0, "the fixture set reconciled nothing"
    assert report["engine_modules_touched_outside_adapters"] == [], (
        f"ADR-001 kill condition G: the non-insurance run needed changes outside the adapter "
        f"package: {report['engine_modules_touched_outside_adapters']}"
    )
    assert report["shared_engine_digest"] == report["insurance_engine_digest"], (
        "the two families did not run the same engine build"
    )


# ------------------------------------------------------------------- H: the residency manifest


def test_H_the_committed_residency_manifest_matches_the_terraform_module() -> None:
    """A residency claim about clicked infrastructure is unfalsifiable; this is the falsifier."""
    report = _load("residency.json")

    assert report["environments"], "no environment declared a residency manifest"
    drifted = [e for e, r in report["environments"].items() if not r["matches_module"]]
    assert not drifted, (
        f"ADR-001 kill condition H: the committed manifest disagrees with the Terraform module "
        f"for {drifted}"
    )


# --------------------------------------------------------------------- the corpus contract


def test_the_corpus_meets_the_size_contract() -> None:
    corpus = _load("corpus.json")

    assert corpus["schema_variants"] >= MIN_SCHEMA_VARIANTS, (
        f"only {corpus['schema_variants']} schema variants, contract {MIN_SCHEMA_VARIANTS}"
    )
    assert corpus["canonical_rows"] >= MIN_CANONICAL_ROWS, (
        f"only {corpus['canonical_rows']} canonical rows, contract {MIN_CANONICAL_ROWS}"
    )
    assert corpus["held_out_variants"] >= MIN_HELD_OUT_VARIANTS, (
        f"only {corpus['held_out_variants']} held-out variants, contract {MIN_HELD_OUT_VARIANTS}"
    )


def test_the_corpus_declares_its_generator_and_seed() -> None:
    """ADR-001: synthetic and openly so. A generated corpus nobody can regenerate is a claim."""
    corpus = _load("corpus.json")

    for key in ("generator_version", "seed", "generated_at", "families"):
        assert key in corpus, f"corpus.json has no `{key}`"
    assert corpus["is_synthetic"] is True, (
        "the corpus must declare itself synthetic; ADR-001 forbids calling it real-world data"
    )


def test_the_adversarial_cases_are_all_present() -> None:
    """Named in ADR-001 as real test cases rather than README decoration."""
    corpus = _load("corpus.json")
    required = {
        "same_policy_different_premium",
        "same_amount_different_policy",
        "duplicate_row",
        "split_commission",
        "swapped_gross_net",
        "tax_included_vs_excluded",
        "localised_date",
        "abbreviated_header",
        "unseen_synonym",
        "totals_agree_rows_do_not",
        "rows_agree_total_does_not",
    }
    present = set(corpus["adversarial_cases"])

    assert required <= present, f"adversarial cases missing: {sorted(required - present)}"
