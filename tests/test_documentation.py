"""The README may not cite a number that no build produced, or link to a file that is not there.

ADR-001 forbids publishing an unmeasured figure. That is easy to honour on the day it is written and
hard to honour six months later, when a threshold has moved and the README still quotes the old one
— which is precisely how a repository ends up making a claim its own evidence contradicts.

So every headline figure in the README is checked against the artifact that produced it, by reading
both. The figures are listed here rather than scraped, because a scraper that found nothing would
pass silently and this file would become decoration.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
ARTIFACTS = ROOT / "artifacts"


def _artifact(name: str) -> dict[str, Any]:
    path = ARTIFACTS / f"{name}.json"
    if not path.is_file():
        pytest.skip(f"{name}.json has not been built; run `make artifacts`")
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def test_every_relative_link_in_the_readme_resolves() -> None:
    """A broken link in the first thing anybody reads is the cheapest possible own goal."""
    links = re.findall(r"\]\((?!https?://)([^)]+)\)", README)
    assert links, "no relative links found; this test would pass on an empty README"
    missing = [link for link in links if not (ROOT / link.split("#")[0]).exists()]
    assert not missing, f"README links to files that do not exist: {missing}"


class TestTheHeadlineFiguresAreMeasured:
    def test_the_hold_out_mapping_accuracy(self) -> None:
        evaluation = _artifact("evaluation")
        measured = f"{evaluation['holdout']['mapping']['system']['accuracy']:.4f}"
        assert measured in README, f"the README does not quote the measured {measured}"

    def test_the_best_baseline_it_is_compared_against(self) -> None:
        """The comparison is worthless if the README quotes a weaker baseline than the best one."""
        evaluation = _artifact("evaluation")
        best = max(b["accuracy"] for b in evaluation["holdout"]["mapping"]["baselines"].values())
        assert f"{best:.4f}" in README

    def test_the_false_matched_count(self) -> None:
        evaluation = _artifact("evaluation")
        assert evaluation["holdout"]["reconciliation"]["false_matched"] == 0
        assert "0 false MATCHED" in README

    @pytest.mark.parametrize(
        ("artifact", "keys", "template"),
        [
            ("evaluation", ("holdout", "reconciliation", "rows"), "{:,}"),
            ("evaluation", ("holdout", "reconciliation", "injected_discrepancies"), "{:,}"),
            ("evaluation", ("development", "reconciliation", "rows"), "{:,}"),
            ("lineage", ("cells_checked",), "{:,}"),
            ("corpus", ("canonical_rows",), "{:,}"),
            ("corpus", ("schema_variants",), "{}"),
            ("portability", ("non_insurance_rows_reconciled",), "{:,}"),
            ("idempotency", ("files",), "{}"),
        ],
    )
    def test_a_quoted_count_matches_its_artifact(
        self, artifact: str, keys: tuple[str, ...], template: str
    ) -> None:
        node: Any = _artifact(artifact)
        for key in keys:
            node = node[key]
        rendered = template.format(node)
        assert rendered in README, (
            f"the README does not contain {rendered!r} from {artifact}.json{list(keys)}. Either "
            "the figure moved and the README was not updated, or the README is quoting a number "
            "nothing measured."
        )


class TestTheReadmeDoesNotOverclaim:
    @pytest.mark.parametrize(
        "forbidden",
        [
            "production-ready",
            "battle-tested",
            "enterprise-grade",
            "state-of-the-art",
            "99.9%",
        ],
    )
    def test_no_unearned_superlative(self, forbidden: str) -> None:
        assert forbidden.lower() not in README.lower()

    def test_it_says_the_corpus_is_synthetic(self) -> None:
        """ADR-001 forbids describing generated fixtures as real-world data."""
        assert "ynthetic" in README

    def test_it_says_nothing_is_deployed_to_azure(self) -> None:
        """The Terraform validates and has never been applied. A reader must not infer otherwise."""
        assert "Nothing has been applied" in README or "never run" in README

    def test_it_says_no_live_model_arm_ran(self) -> None:
        assert "No live model arm runs in this build" in README

    def test_it_discloses_the_hold_out_observation(self) -> None:
        """ADR-002 is the disclosure. The README must point at it rather than bury it."""
        assert "0.7826" in README
        assert "ADR-002" in README


def test_the_known_limitations_are_in_the_readme() -> None:
    """The two variants that return REVIEW on every row are a real gap, and it is stated."""
    for phrase in ("ins_04_split_commission_usd", "ins_05_tax_basis_ambiguous"):
        assert phrase in README, f"the README does not mention the {phrase} limitation"
