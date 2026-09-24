"""Build the synthetic bordereaux corpus, or prove that building it twice changes nothing.

    python scripts/generate_corpus.py                      # into data/generated/
    python scripts/generate_corpus.py --out /tmp/corpus    # somewhere else
    python scripts/generate_corpus.py --verify-determinism # two builds, compared byte for byte

The corpus is **synthetic**. ADR-001 fixes that it may never be described as real-world labelled
data, and every file this writes says so in its own body.

`--verify-determinism` is the honest form of the determinism claim: it builds the whole corpus
twice into two temporary directories and diffs every byte of every file, then exits non-zero if
anything differs. Asserting reproducibility in a docstring costs nothing and proves nothing; this
costs two builds and proves it, which is the trade ADR-001 makes everywhere else too.

The output directory is **emptied first**. A corpus half from one generator version and half from
another is worse than no corpus, because it looks complete.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    # So the script runs from a checkout without an editable install, which is what a reviewer
    # cloning the repository will actually have.
    sys.path.insert(0, str(REPO_ROOT / "src"))

from bordereaux_reconciler.corpus import (  # noqa: E402
    GENERATOR_VERSION,
    SEED,
    build_corpus,
    verify_determinism,
)

DEFAULT_OUT = REPO_ROOT / "data" / "generated"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default: {DEFAULT_OUT})"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=SEED,
        help=f"the seed published in the manifest (default: {SEED}); changing it changes the "
        "entire corpus, which is the point of publishing it",
    )
    parser.add_argument(
        "--verify-determinism",
        action="store_true",
        help="build twice into temporary directories, diff every byte, and exit non-zero on any "
        "difference. Does not touch --out.",
    )
    parser.add_argument("--quiet", action="store_true", help="print nothing on success")
    return parser.parse_args(argv)


def _report(manifest: dict[str, object], out: Path) -> None:
    print(f"corpus written to {out}")
    print(f"  generator {manifest['generator_version']}  seed {manifest['seed']}")
    print(f"  schema variants   {manifest['schema_variants']}")
    print(f"  canonical rows    {manifest['canonical_rows']}")
    print(f"  held-out variants {manifest['held_out_variants']}  {manifest['holdout']}")
    print(f"  unmappable        {manifest['unmappable']}")
    print(f"  adversarial cases {manifest['adversarial_cases']}")
    print(f"  corpus digest     {manifest['corpus_digest']}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.verify_determinism:
        report = verify_determinism(seed=args.seed)
        print(f"determinism: {report.runs} builds, {report.files_compared} files each")
        print(f"  digests: {', '.join(sorted(set(report.digests)))}")
        if report.identical:
            print("  IDENTICAL — every file byte for byte, in both runs")
            return 0
        print(f"  DIFFERENT — {len(report.differing_files)} file(s): {report.differing_files[:5]}")
        return 1

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    manifest = build_corpus(out, seed=args.seed)

    if not args.quiet:
        _report(manifest, out)
    if manifest["generator_version"] != GENERATOR_VERSION:  # pragma: no cover - defensive
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
