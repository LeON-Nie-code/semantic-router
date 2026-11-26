"""Keep the FactCheck split intervention and the false-positive cap honest."""

from pathlib import Path

import pytest


@pytest.fixture
def tools_path(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))


def test_revised_train_preserves_class_counts(tools_path):
    from collections import Counter

    from factcheck_data import CORRECTIONS, NEGATIVE, POSITIVE, revised_train

    rows = [
        {
            "id": key,
            "text": "Original prompt",
            "label": POSITIVE if label == NEGATIVE else NEGATIVE,
        }
        for key, label in CORRECTIONS.items()
    ] + [
        {"id": str(i), "text": f"Existing negative {i}", "label": NEGATIVE}
        for i in range(100)
    ]
    revised = revised_train(rows)
    assert Counter(r["label"] for r in revised) == Counter(r["label"] for r in rows)
    labels = {r["id"]: r["label"] for r in revised}
    assert len(labels) == len(rows)
    assert all(labels[key] == label for key, label in CORRECTIONS.items())


def test_threshold_keeps_tied_scores_on_the_same_side(tools_path):
    from factcheck_data import NEGATIVE, POSITIVE
    from factcheck_eval import threshold_at_fpr

    rows = [(0.9, POSITIVE), (0.8, NEGATIVE), (0.8, NEGATIVE), (0.6, NEGATIVE)]
    assert threshold_at_fpr(rows, 0.5) == 0.9


def test_suite_keeps_unicode_line_separators_inside_jsonl_rows(tools_path, tmp_path):
    import json

    from factcheck_data import suite_rows

    rows = [{"id": "example", "text": "First\u2028Second\u0085Third"}]
    path = tmp_path / "source" / "fact_check" / "test.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")
    assert suite_rows(tmp_path, "test") == rows
