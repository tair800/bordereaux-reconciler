"""Regenerate the committed residency manifests from the committed Terraform inputs.

    python scripts/residency_manifest.py            # rewrite docs/residency/*.json
    python scripts/residency_manifest.py --check    # fail if any committed file is stale

`--check` is what CI runs. It is the same comparison kill condition H makes, available as a fast
lane that needs no corpus and no database, so a pull request that edits a region gets told
immediately rather than at the end of the evidence build.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from bordereaux_reconciler.evaluation.residency import (  # noqa: E402
    ENVIRONMENTS,
    render_manifest,
    write_manifests,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit non-zero if a committed manifest is out of date",
    )
    args = parser.parse_args(argv)

    if not args.check:
        for path in write_manifests(REPO_ROOT):
            print(f"wrote {path.relative_to(REPO_ROOT)}")
        return 0

    stale: list[str] = []
    for environment in ENVIRONMENTS:
        if not (REPO_ROOT / "infra" / "terraform" / "envs" / environment).is_dir():
            continue
        committed = REPO_ROOT / "docs" / "residency" / f"{environment}.json"
        if not committed.is_file():
            stale.append(f"{environment}: docs/residency/{environment}.json does not exist")
            continue
        document = json.loads(committed.read_text(encoding="utf-8"))
        if document.get("manifest") != render_manifest(REPO_ROOT, environment):
            stale.append(
                f"{environment}: the committed manifest disagrees with "
                f"infra/terraform/envs/{environment}/residency.auto.tfvars.json"
            )

    if stale:
        print("residency manifests are out of date:", file=sys.stderr)
        for line in stale:
            print(f"  {line}", file=sys.stderr)
        print("\nrun `python scripts/residency_manifest.py` and commit the result", file=sys.stderr)
        return 1

    print(f"residency manifests are current for {', '.join(ENVIRONMENTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
