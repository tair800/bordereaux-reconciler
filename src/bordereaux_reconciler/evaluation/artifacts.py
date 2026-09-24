"""The eight evidence files `tests/test_kill_criteria.py` reads, and how each one is earned.

The kill test was committed before any of this existed and has not been edited since; these are the
measurements it grades. That ordering is the only thing that makes the numbers worth reading, so
nothing here decides what counts as passing — the thresholds live in the test, and this module's
job is to produce honest inputs to them.

Each artifact records not only its result but **how the result was obtained**, including where the
method falls short. `residency.json` is the clearest case: it can prove the committed manifest
agrees with the committed Terraform inputs, and it cannot prove either one describes a running
deployment, because this build has no Azure subscription. The artifact says so in its own body
rather than in a footnote somebody might not reach.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from bordereaux_reconciler.adapters import known_families
from bordereaux_reconciler.evaluation.corpusio import INJECTION_KINDS, VariantCase, load_cases
from bordereaux_reconciler.evaluation.run import (
    VariantRun,
    canonical_digest_input,
    run_variant,
    status_digest_input,
)
from bordereaux_reconciler.store import create_all, get_engine
from bordereaux_reconciler.store.ledger import count_rows, ingest_file, ledger_rows_for, reset

__all__ = ["build_all"]

#: Exactly the fields `test_C_every_canonical_cell_carries_its_full_lineage` requires. Named here so
#: a change to the lineage contract breaks the test rather than quietly narrowing what is checked.
LINEAGE_REQUIRED = ("source_content_hash", "sheet", "row", "column", "mapping_version")

#: The wording `test_D_the_false_matched_check_is_defined_against_the_declared_tolerance` pins. If
#: the definition ever drifts, the zero above it stops meaning what the test was written to mean, so
#: the test asserts the sentence as well as the number.
FALSE_MATCHED_DEFINITION = (
    "reported MATCHED while a reconciled field differs by more than the declared tolerance"
)

#: Modules that make up the reconciliation engine: everything a bordereau passes through that is
#: **not** an adapter. Kill condition G is the claim that none of them knows what insurance is.
ENGINE_MODULES = (
    "domain.py",
    "money.py",
    "reconcile.py",
    "ingest/read.py",
    "ingest/profile.py",
    "ingest/mapping.py",
    "ingest/canonical.py",
    "store/schema.py",
    "store/ledger.py",
)

#: Ingestions per file when proving kill condition B. The test requires at least three.
REINGESTIONS = 3


def _write(directory: Path, name: str, payload: dict[str, Any]) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _provenance() -> dict[str, Any]:
    """Enough to tell whether two runs of this are comparable at all."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    return {
        "commit": commit or "unknown",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "is_synthetic_corpus": True,
    }


# ------------------------------------------------------------------------------ A: determinism


def _determinism(cases: tuple[VariantCase, ...], runs: int = 2) -> dict[str, Any]:
    """Reconcile the whole corpus `runs` times and compare the canonical values and the statuses.

    Two separate digests rather than one over everything. A single digest would say "something
    changed"; these say whether the *values* moved or only the *verdicts*, and those have different
    causes — a value that differs between runs is a parsing bug, a status that differs while the
    values hold is a comparison or an ordering bug.
    """
    canonical_digests: list[str] = []
    status_digests: list[str] = []

    for _ in range(runs):
        canonical: list[Any] = []
        statuses: list[Any] = []
        for case in cases:
            run = run_variant(case)
            canonical.append({case.variant_id: canonical_digest_input(run)})
            statuses.append({case.variant_id: status_digest_input(run)})
        canonical_digests.append(_digest(canonical))
        status_digests.append(_digest(statuses))

    return {
        "kill_condition": "A",
        "runs": runs,
        "variants": len(cases),
        "canonical_digests": canonical_digests,
        "status_digests": status_digests,
        "canonical_digest_stable": len(set(canonical_digests)) == 1,
        "status_digest_stable": len(set(status_digests)) == 1,
        "method": (
            "the whole corpus is read, mapped, canonicalised and reconciled once per run in a "
            "fresh "
            ""
            "pass; every canonical amount is hashed as str(Decimal) so 10.50 and 10.5 differ, "
            "because they do"
        ),
    }


# ------------------------------------------------------------------ B: idempotent re-ingestion


def _idempotency(engine: Engine, runs: list[VariantRun]) -> dict[str, Any]:
    """Ingest every mappable file three times into a real PostgreSQL database and compare.

    Against Postgres rather than an in-memory stand-in, because the guarantee under test is a
    primary key. A fake that returned "already present" from a Python dictionary would be testing
    the fake.
    """
    reset(engine)
    per_file: list[dict[str, Any]] = []
    offenders = 0
    changed = 0

    for run in runs:
        if not run.mappable:
            continue
        first_snapshot: list[dict[str, Any]] | None = None
        results = []
        for _ in range(REINGESTIONS):
            result = ingest_file(
                engine,
                content_hash=run.content_hash,
                coverholder=run.case.variant_id,
                filename=run.case.source.name,
                family=run.case.family,
                mapping_version=run.case.profile.mapping_version,
                period=run.rows[0].period if run.rows else None,
                rows=run.rows,
                status="accepted",
            )
            results.append(result)
            snapshot = ledger_rows_for(engine, run.content_hash)
            if first_snapshot is None:
                first_snapshot = snapshot
            elif snapshot != first_snapshot:
                changed += 1

        versions = len({_digest(ledger_rows_for(engine, run.content_hash))})
        wrote_after_first = sum(1 for r in results[1:] if r.wrote_anything)
        if wrote_after_first or versions > 1:
            offenders += 1
        per_file.append(
            {
                "variant_id": run.case.variant_id,
                "content_hash": run.content_hash,
                "rows_written_first_time": results[0].rows_written,
                "writes_after_the_first": wrote_after_first,
                "stored_rows": len(first_snapshot or []),
            }
        )

    counts = count_rows(engine)
    return {
        "kill_condition": "B",
        "engine": engine.url.render_as_string(hide_password=True),
        "ingestions_per_file": REINGESTIONS,
        "files": len(per_file),
        "files_with_more_than_one_canonical_version": offenders,
        "ledger_rows_changed_after_first_ingestion": changed,
        "table_counts": counts,
        "per_file": per_file,
        "method": (
            "each file is ingested three times through the same writer the service uses; the "
            "content hash is the primary key of ingested_file, so the second and third attempts "
            "are refused by the database rather than skipped by a check the caller could forget"
        ),
    }


# ---------------------------------------------------------------------------------- C: lineage


def _lineage(runs: list[VariantRun]) -> dict[str, Any]:
    """Walk every canonical value of every row and demand a complete lineage record."""
    checked = 0
    missing: list[dict[str, Any]] = []

    for run in runs:
        for row in run.rows:
            for lineage in row.lineage():
                checked += 1
                gaps = [
                    name
                    for name, value in (
                        ("source_content_hash", lineage.source_content_hash),
                        ("sheet", lineage.cell.sheet),
                        ("row", lineage.cell.row),
                        ("column", lineage.cell.column),
                        ("mapping_version", lineage.mapping_version),
                    )
                    if value in (None, "", 0)
                ]
                if gaps or not lineage.is_complete:
                    missing.append(
                        {
                            "variant_id": run.case.variant_id,
                            "key": row.key,
                            "cell": str(lineage.cell),
                            "missing": gaps or ["is_complete() returned False"],
                        }
                    )

    return {
        "kill_condition": "C",
        "cells_checked": checked,
        "required_fields": list(LINEAGE_REQUIRED),
        "cells_missing_lineage": len(missing),
        "examples_missing": missing[:10],
        "method": (
            "every Tracked value on every canonical row — gross, each deduction, net and each "
            "attribute — is inspected individually; a row is not sampled and a field is not "
            "assumed "
            ""
            "to inherit its neighbour's provenance"
        ),
    }


# ------------------------------------------------------------- D and E: the scored hold-out


def _reconciliation_block(runs: list[VariantRun]) -> dict[str, Any]:
    rows = sum(len(r.results) for r in runs)
    false_matched = [fm for run in runs for fm in run.false_matches]
    status_errors = [
        {
            "variant_id": run.case.variant_id,
            "key": key,
            "expected": str(expected),
            "reported": str(
                next((x.status for x in run.results if x.key == key), "not reconciled")
            ),
        }
        for run in runs
        for key, expected in sorted(run.expected.items())
        if next((x.status for x in run.results if x.key == key), None) != expected
    ]
    return {
        "rows": rows,
        "false_matched": len(false_matched),
        "false_matched_definition": FALSE_MATCHED_DEFINITION,
        "false_matched_examples": [fm.as_json() for fm in false_matched[:20]],
        "injected_discrepancies": sum(len(r.ledger.injections) for r in runs),
        "injection_kinds": {k: str(v) for k, v in sorted(INJECTION_KINDS.items())},
        "status_disagreements": len(status_errors),
        "status_disagreement_examples": status_errors[:20],
    }


def _mapping_block(runs: list[VariantRun]) -> dict[str, Any]:
    correct = sum(r.system_mapping.correct for r in runs)
    total = sum(r.system_mapping.total for r in runs)
    baselines = {
        name: {
            "accuracy": (
                sum(r.baseline_mappings[name].correct for r in runs)
                / sum(r.baseline_mappings[name].total for r in runs)
            ),
            "correct": sum(r.baseline_mappings[name].correct for r in runs),
            "total": sum(r.baseline_mappings[name].total for r in runs),
        }
        for name in sorted(runs[0].baseline_mappings)
    }
    return {
        "system": {
            "accuracy": correct / total if total else 0.0,
            "correct": correct,
            "total": total,
            "errors": [
                {"variant_id": r.case.variant_id, "wrong": list(r.system_mapping.wrong)}
                for r in runs
                if r.system_mapping.wrong
            ],
        },
        "baselines": baselines,
        "definition": (
            "scored over the adapter's canonical fields, not the file's columns: for each field "
            "the "
            ""
            "mapper is correct when it assigns the column ground truth assigns, and when it "
            "assigns "
            ""
            "nothing to a field the file does not contain"
        ),
    }


def _evaluation(runs: list[VariantRun]) -> dict[str, Any]:
    held_out = [r for r in runs if r.case.held_out]
    development = [r for r in runs if not r.case.held_out]

    return {
        "kill_conditions": ["D", "E"],
        "holdout": {
            "variants": [r.case.variant_id for r in held_out],
            "reconciliation": _reconciliation_block(held_out),
            "mapping": _mapping_block(held_out),
        },
        "development": {
            "variants": [r.case.variant_id for r in development],
            "reconciliation": _reconciliation_block(development),
            "mapping": _mapping_block(development),
        },
        "scored_once": (
            "the hold-out membership is fixed by blake2b(variant_id) and by the unseen-vocabulary "
            "flag, neither of which consults a score. See DECISIONS.md ADR-002 for what was "
            "observed before this run and what was changed on the strength of development data"
        ),
    }


# ------------------------------------------------------------------------------- F: abstention


def _abstention(runs: list[VariantRun]) -> dict[str, Any]:
    unmappable = [r for r in runs if r.case.unmappable]
    quarantined = [r for r in unmappable if not r.mappable]
    return {
        "kill_condition": "F",
        "unmappable_fixtures": len(unmappable),
        "quarantined": len(quarantined),
        "ledger_rows_written_for_unmappable": sum(len(r.rows) for r in unmappable),
        "detail": [
            {
                "variant_id": r.case.variant_id,
                "abstained": not r.mappable,
                "missing_required": list(r.mapping.missing_required),
                "reason": r.quarantine_reasons[0] if r.quarantine_reasons else "",
            }
            for r in unmappable
        ],
        "method": (
            "abstention is triggered by a required canonical field having no column assigned to "
            "it, "
            ""
            "not by a confidence score falling below a number — a threshold is something that can "
            "be lowered, and a missing required field is not"
        ),
    }


# ------------------------------------------------------------------------------ G: portability


def _family_vocabulary() -> set[str]:
    """Words that would show a family had leaked out of its adapter pack."""
    from bordereaux_reconciler.adapters import get_adapter  # noqa: PLC0415 - avoids a cycle

    words = {"insurance", "marketplace", "coverholder", "bordereau"}
    for family in known_families():
        words.add(family)
        for field in get_adapter(family).fields:
            words.add(field.name)
    # `bordereau` and `coverholder` name the product and its counterparty, not the insurance
    # domain model, and they appear in the engine's prose and parameter names by design. The leak
    # this looks for is a *field* or a *family* — `gross_premium` in reconcile.py would mean the
    # comparison had been written against one vocabulary.
    return {w for w in words if w not in {"bordereau", "coverholder", "period", "key"}}


def _engine_vocabulary_hits(source: str, vocabulary: set[str]) -> list[str]:
    """Family words appearing in a module's **executable code**, not in its prose.

    Parsed rather than grepped, and the difference decides whether this check is worth having. A
    substring scan reported `category` inside `categorical`, and reported every docstring that
    explains why gross outranks commission — fifteen hits, none of them a leak. A scan that cries
    wolf fifteen times gets muted, and then the one real hit goes unnoticed too.

    So: docstrings are dropped, comments never enter the AST, and identifiers and string
    literals are matched whole. What remains is the actual claim — that no engine module names
    a family or one of its canonical fields in code it runs.
    """
    import ast  # noqa: PLC0415 - only needed here

    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
        elif isinstance(node, ast.arg):
            tokens.add(node.arg)
        elif isinstance(node, ast.keyword | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            name = node.arg if isinstance(node, ast.keyword) else node.name
            if name:
                tokens.add(name)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            # A string literal is code here: `family="insurance"` is exactly the leak this looks
            # for, and it is a constant rather than an identifier.
            tokens.update(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", node.value.lower()))

    lowered = {token.lower() for token in tokens}
    return sorted(word for word in vocabulary if word in lowered)


def _portability(runs: list[VariantRun], package_root: Path) -> dict[str, Any]:
    vocabulary = _family_vocabulary()
    leaks: list[str] = []
    digests: dict[str, str] = {}

    for relative in ENGINE_MODULES:
        path = package_root / relative
        source = path.read_text(encoding="utf-8")
        digests[relative] = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        leaks.extend(f"{relative}: {word}" for word in _engine_vocabulary_hits(source, vocabulary))

    engine_digest = _digest(digests)
    non_insurance = [r for r in runs if r.case.family != "insurance"]
    insurance = [r for r in runs if r.case.family == "insurance"]

    return {
        "kill_condition": "G",
        "families": list(known_families()),
        "non_insurance_rows_reconciled": sum(len(r.results) for r in non_insurance),
        "insurance_rows_reconciled": sum(len(r.results) for r in insurance),
        "engine_modules_touched_outside_adapters": leaks,
        "shared_engine_digest": engine_digest,
        "insurance_engine_digest": engine_digest,
        "engine_module_digests": digests,
        "vocabulary_checked": sorted(vocabulary),
        "method": (
            "both families run through one loaded copy of the engine in one process, so the "
            "digests "
            "are equal by construction; the falsifiable part is the leak scan, which parses each "
            "engine module, discards its docstrings, and looks for any registered family name or "
            "canonical field name among the identifiers and string literals of the code it runs"
        ),
    }


# -------------------------------------------------------------------------------- H: residency


def _residency(repo_root: Path) -> dict[str, Any]:
    """Compare each committed residency manifest against the committed Terraform inputs.

    **What this proves and what it does not.** It proves that the manifest under `docs/residency/`
    was rendered from the same `residency.auto.tfvars.json`, the same `region_jurisdictions.json`
    and the same `residency_statement.md` that Terraform reads, so changing a region in the tfvars
    and forgetting to regenerate the manifest fails the build. That is the drift that actually
    happens.

    It does not prove the manifest describes a running deployment. There is no Azure subscription in
    this build, nothing has been applied, and `terraform output` has never produced a value here.
    Anyone reading `matches_module: true` should read it as "the committed document and the
    committed configuration agree", never as "this is where the data is".
    """
    from bordereaux_reconciler.evaluation.residency import render_manifest  # noqa: PLC0415

    environments: dict[str, Any] = {}
    docs = repo_root / "docs" / "residency"
    roots = sorted((repo_root / "infra" / "terraform" / "envs").glob("*/"))

    for root in roots:
        if not (root / "residency.auto.tfvars.json").is_file():
            continue
        name = root.name
        rendered = render_manifest(repo_root, name)
        committed_path = docs / f"{name}.json"
        committed = (
            json.loads(committed_path.read_text(encoding="utf-8"))
            if committed_path.is_file()
            else None
        )
        environments[name] = {
            "matches_module": committed is not None and committed.get("manifest") == rendered,
            "committed_manifest_present": committed is not None,
            "declared_region": rendered["platform"]["region"],
            "declared_jurisdiction": rendered["platform"]["region_facts"].get("jurisdiction"),
            "inference_topologies": sorted(rendered["inference"]),
            "rendered_digest": _digest(rendered),
        }

    return {
        "kill_condition": "H",
        "environments": environments,
        "proves": (
            "the committed manifest was rendered from the same committed inputs Terraform reads, "
            "so "
            ""
            "a region changed in one place and not the other fails the build"
        ),
        "does_not_prove": (
            "that any of this is deployed. No Azure subscription exists in this build, nothing has "
            "been applied, and `terraform output` has never run. A region in a configuration is a "
            "request to a cloud provider, not a measurement of where bytes came to rest, and "
            "selecting one asserts no regulatory compliance of any kind"
        ),
    }


# ----------------------------------------------------------------------------- the corpus card


def _corpus(manifest: dict[str, Any], runs: list[VariantRun]) -> dict[str, Any]:
    return {
        "generator_version": manifest["generator_version"],
        "seed": manifest["seed"],
        "generated_at": manifest["generated_at"],
        "families": manifest["families"],
        "is_synthetic": True,
        "notice": manifest["notice"],
        "schema_variants": manifest["schema_variants"],
        "canonical_rows": manifest["canonical_rows"],
        "held_out_variants": manifest["held_out_variants"],
        "holdout_rule": manifest["holdout_rule"],
        "holdout": manifest["holdout"],
        "adversarial_cases": manifest["adversarial_cases"],
        "unmappable": manifest["unmappable"],
        "corpus_digest": manifest["corpus_digest"],
        "rows_ingested_by_the_pipeline": sum(len(r.rows) for r in runs),
        "rows_quarantined_by_the_pipeline": sum(r.quarantined_rows for r in runs),
    }


# -------------------------------------------------------------------------------------- build


@dataclass(frozen=True)
class BuildReport:
    artifacts: dict[str, Path]
    false_matched: int
    holdout_mapping_accuracy: float


def build_all(corpus_dir: Path, artifacts_dir: Path, repo_root: Path) -> BuildReport:
    """Run the whole corpus once and write every artifact. The evidence lane's single entry "
    "point."""
    manifest, cases = load_cases(corpus_dir)
    runs = [run_variant(case) for case in cases]
    package_root = repo_root / "src" / "bordereaux_reconciler"

    engine = get_engine()
    create_all(engine)

    provenance = _provenance()
    payloads = {
        "determinism.json": _determinism(cases),
        "idempotency.json": _idempotency(engine, runs),
        "lineage.json": _lineage(runs),
        "evaluation.json": _evaluation(runs),
        "abstention.json": _abstention(runs),
        "portability.json": _portability(runs, package_root),
        "residency.json": _residency(repo_root),
        "corpus.json": _corpus(manifest, runs),
    }

    written: dict[str, Path] = {}
    for name, payload in payloads.items():
        written[name] = _write(artifacts_dir, name, {**payload, "provenance": provenance})

    evaluation = payloads["evaluation.json"]
    return BuildReport(
        artifacts=written,
        false_matched=evaluation["holdout"]["reconciliation"]["false_matched"],
        holdout_mapping_accuracy=evaluation["holdout"]["mapping"]["system"]["accuracy"],
    )
