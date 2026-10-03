"""AP-001: `GET /api/reports/{report_id}` is a metadata/provenance endpoint
(<2s gate). It must not transfer full record-level arrays (e.g. annex rows) --
those already have a dedicated annex/CSV download route. `_lightweight_dataset`
is the function that bounds it; this proves it summarises long lists without
disturbing anything else in the shape.
"""
from __future__ import annotations

from app.reports.routes import _DATASET_LIST_PREVIEW_LIMIT, _lightweight_dataset


def test_short_lists_pass_through_unchanged():
    small = {"progress": {"annex_rows": [{"npi": "x"} for _ in range(3)]},
             "totals": {"verified": 10, "not_found": 2}}
    assert _lightweight_dataset(small) == small


def test_long_list_is_summarised_not_transferred():
    rows = [{"npi": str(i)} for i in range(_DATASET_LIST_PREVIEW_LIMIT + 1)]
    out = _lightweight_dataset({"progress": {"annex_rows": rows}})
    summary = out["progress"]["annex_rows"]
    assert summary["_truncated"] is True
    assert summary["count"] == len(rows)
    # the actual row payload must not appear anywhere in the summary
    assert "npi" not in str(summary)


def test_boundary_length_is_not_truncated():
    rows = [{"npi": str(i)} for i in range(_DATASET_LIST_PREVIEW_LIMIT)]
    out = _lightweight_dataset({"rows": rows})
    assert out["rows"] == rows


def test_nested_long_lists_at_any_depth_are_summarised():
    rows = [{"x": i} for i in range(_DATASET_LIST_PREVIEW_LIMIT + 5)]
    out = _lightweight_dataset({"a": {"b": {"c": rows}}})
    assert out["a"]["b"]["c"]["_truncated"] is True
    assert out["a"]["b"]["c"]["count"] == len(rows)


def test_scalars_and_empty_values_pass_through():
    assert _lightweight_dataset(None) is None
    assert _lightweight_dataset({}) == {}
    assert _lightweight_dataset([]) == []
    assert _lightweight_dataset("x") == "x"
    assert _lightweight_dataset(42) == 42
