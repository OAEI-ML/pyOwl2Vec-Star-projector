from __future__ import annotations

import json
from pathlib import Path

from pyowl2vec_star_projector import Edge
from tools.compare_exact_baselines import _compare_projection

ROOT = Path(__file__).resolve().parents[1]


def test_exact_projection_comparison_preserves_order_and_classifies_differences() -> None:
    first = Edge("a", "r", "b")
    second = Edge("b", "r", "c")
    expected = [list(first.as_tuple()), list(second.as_tuple())]
    equal = _compare_projection("fixture", (first, second), expected)
    assert equal["ordered_equal"] is True
    assert equal["missing_count"] == equal["unexpected_count"] == 0
    assert equal["observed_sha256"] == equal["expected_sha256"]

    reordered = _compare_projection("fixture", (second, first), expected)
    assert reordered["ordered_equal"] is False
    assert reordered["missing_count"] == reordered["unexpected_count"] == 0

    changed = _compare_projection("fixture", (first,), expected)
    assert changed["ordered_equal"] is False
    assert changed["missing_count"] == 1
    assert changed["unexpected_count"] == 0


def test_committed_exact_comparison_evidence_has_no_unclassified_difference() -> None:
    evidence = json.loads(
        (ROOT / "reports/p6/evidence/exact-baselines.json").read_text(encoding="utf-8")
    )
    assert evidence["passed"] is True
    assert evidence["difference_classification"].startswith("none")
    assert len(evidence["fixtures"]) == 2
    for fixture in evidence["fixtures"]:
        assert fixture["load_calls"] == 1
        assert fixture["snapshot_identity_preserved"] is True
        assert fixture["snapshot_unchanged"] is True
        assert all(projection["ordered_equal"] for projection in fixture["projections"])
