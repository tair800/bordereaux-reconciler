"""The synthetic bordereaux corpus: generator, ground truth, and the manifest an evaluator reads.

**This corpus is synthetic and openly so.** No public delegated-authority bordereaux corpus exists
that is safely redistributable — real ones carry commercial terms and, routinely, personal data —
so ADR-001 fixes that this project generates its own from a committed seed and **never describes
the result as real-world labelled data**. Every artifact this package writes says `is_synthetic`
in its own body, so the disclaimer travels with the files rather than living only in a README that
a consumer may never open.

What it produces, under a directory the caller names (`data/generated/` by convention, and
gitignored, because a corpus rebuilt from a committed seed is not something to keep in history):

- one source file per schema variant, as a coverholder would send it — CSV or XLSX;
- one ground-truth JSON beside each, carrying the canonical rows **with every monetary value as a
  string**, and the true `source header -> canonical field` mapping the mapping evaluation is
  scored against;
- one `manifest.json`, which is the only file the evaluation lane needs to read.

The single most important property: **a monetary value is never a JSON number.** `1234.56` read
back through a float is `1234.5599999999999`, and a ground truth that had already lost exactness
could not prove a reconciler preserved it.
"""

from __future__ import annotations

from bordereaux_reconciler.corpus.generate import (
    DeterminismReport,
    build_corpus,
    verify_determinism,
)
from bordereaux_reconciler.corpus.manifest import (
    GENERATED_AT,
    GENERATOR_VERSION,
    HOLDOUT_RULE,
    SEED,
    is_held_out,
)
from bordereaux_reconciler.corpus.variants import VARIANTS

__all__ = [
    "GENERATED_AT",
    "GENERATOR_VERSION",
    "HOLDOUT_RULE",
    "SEED",
    "VARIANTS",
    "DeterminismReport",
    "build_corpus",
    "is_held_out",
    "verify_determinism",
]
