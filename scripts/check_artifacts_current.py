"""Fail if the committed evidence no longer matches what this checkout produces.

    python scripts/check_artifacts_current.py

The artifacts under `artifacts/` are committed on purpose: they are what the README cites and what a
reader browsing the repository sees without running anything. That only stays honest if they are
current, and "somebody will remember to rebuild them" is not a mechanism. This is the mechanism.

**`provenance` is excluded from the comparison, and only `provenance`.** It records the commit, the
Python patch version and the operating system the build ran on, all of which legitimately differ
between a laptop and a CI runner. Including it would make this check fail on every machine that is
not the one that last ran it, which is the fastest way to teach everybody to ignore a red build.
Every substantive number — accuracies, counts, digests, false-MATCHED examples — is compared.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

ARTIFACTS = REPO_ROOT / "artifacts"

#: The only key a fresh build is allowed to change. Everything else is a measurement.
VOLATILE = ("provenance",)


def _substantive(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in VOLATILE}


def _differences(committed: Any, fresh: Any, path: str = "") -> list[str]:
    """Every leaf where the two disagree, named by its path so a failure is actionable."""
    if isinstance(committed, dict) and isinstance(fresh, dict):
        out: list[str] = []
        for key in sorted(set(committed) | set(fresh)):
            if key not in committed:
                out.append(f"{path}.{key}: only in the fresh build ({fresh[key]!r})")
            elif key not in fresh:
                out.append(f"{path}.{key}: only in the committed copy ({committed[key]!r})")
            else:
                out.extend(_differences(committed[key], fresh[key], f"{path}.{key}"))
        return out
    if isinstance(committed, list) and isinstance(fresh, list):
        if len(committed) != len(fresh):
            return [f"{path}: {len(committed)} entries committed, {len(fresh)} fresh"]
        return [
            d
            for index, (a, b) in enumerate(zip(committed, fresh, strict=True))
            for d in _differences(a, b, f"{path}[{index}]")
        ]
    if committed != fresh:
        return [f"{path}: committed {committed!r}, fresh {fresh!r}"]
    return []


def main() -> int:
    from bordereaux_reconciler.evaluation.artifacts import build_all  # noqa: PLC0415
    from bordereaux_reconciler.evaluation.residency import write_manifests  # noqa: PLC0415

    corpus = REPO_ROOT / "data" / "generated"
    if not (corpus / "manifest.json").is_file():
        print("no corpus; run `python scripts/generate_corpus.py` first", file=sys.stderr)
        return 2

    committed = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(ARTIFACTS.glob("*.json"))
    }
    if not committed:
        print("no committed artifacts to compare against", file=sys.stderr)
        return 2

    # Into a scratch directory, so a mismatch does not overwrite the committed evidence before
    # anybody has seen what differed.
    scratch = REPO_ROOT / ".artifacts-check"
    write_manifests(REPO_ROOT)
    build_all(corpus, scratch, REPO_ROOT)
    fresh = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(scratch.glob("*.json"))
    }

    stale: dict[str, list[str]] = {}
    for name in sorted(set(committed) | set(fresh)):
        if name not in committed or name not in fresh:
            stale[name] = ["the file exists on only one side"]
            continue
        found = _differences(_substantive(committed[name]), _substantive(fresh[name]))
        if found:
            stale[name] = found

    for path in scratch.glob("*.json"):
        path.unlink()
    scratch.rmdir()

    if stale:
        print("the committed evidence is out of date:\n", file=sys.stderr)
        for name, found in stale.items():
            print(f"  {name}", file=sys.stderr)
            for line in found[:8]:
                print(f"    {line}", file=sys.stderr)
            if len(found) > 8:
                print(f"    ... and {len(found) - 8} more", file=sys.stderr)
        print(
            "\nRun `make artifacts` and commit the result. The README cites these numbers, so a "
            "stale artifact is a published figure that is no longer true.",
            file=sys.stderr,
        )
        return 1

    print(f"the committed evidence matches a fresh build ({len(committed)} artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
