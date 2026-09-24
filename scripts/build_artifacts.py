"""Run the whole corpus and write the eight evidence artifacts `tests/test_kill_criteria.py` grades.

    python scripts/build_artifacts.py

Needs a PostgreSQL database — `docker compose up -d postgres` provides one — because kill condition
B is a claim about a primary key and proving it anywhere else would prove it about something else.

This prints the two numbers that decide whether the project stands: false MATCHED rows on the
hold-out, which ADR-001 fixes at zero, and hold-out mapping accuracy, which has to beat every one of
the four predeclared baselines and clear 0.90 on its own. It does not decide whether those pass —
the thresholds live in the test, which was committed before any of this code existed.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from bordereaux_reconciler.evaluation.artifacts import build_all  # noqa: E402
from bordereaux_reconciler.evaluation.residency import write_manifests  # noqa: E402


def main() -> int:
    corpus = REPO_ROOT / "data" / "generated"
    if not (corpus / "manifest.json").is_file():
        print(
            "no corpus found. Run `python scripts/generate_corpus.py` first — it is rebuilt from a "
            "committed seed rather than kept in git.",
            file=sys.stderr,
        )
        return 1

    write_manifests(REPO_ROOT)
    report = build_all(corpus, REPO_ROOT / "artifacts", REPO_ROOT)

    print(f"wrote {len(report.artifacts)} artifacts to artifacts/")
    for name in sorted(report.artifacts):
        print(f"  {name}")
    print()
    print(f"  false MATCHED on the hold-out   {report.false_matched}")
    print(f"  hold-out mapping accuracy       {report.holdout_mapping_accuracy:.4f}")
    print()
    print(
        "run `pytest tests/test_kill_criteria.py` to grade these against the committed thresholds"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
