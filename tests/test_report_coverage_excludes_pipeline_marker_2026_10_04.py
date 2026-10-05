"""The per-source verification table never counts a process marker as a
verified source (2026-10-04, Part B).

`arc_pipeline` writes one tefca_verifications row per entity with
source="rce_arc_pipeline", status="verified" -- "the pipeline ran" -- for
every bucket, B4 included. Grouped as a source it rendered as
"rce_arc_pipeline: 100% verified". No DB needed: the service is driven with
a stub session returning the grouped rows it would have read.
"""
from __future__ import annotations

import pytest

from app.reports.data.report_data_service import (NON_SOURCE_VERIFICATION_ROWS,
                                                   ReportDataService)


class _Result:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _StubDb:
    def __init__(self, rows): self._rows = rows
    async def execute(self, *_a, **_k): return _Result(self._rows)


@pytest.mark.asyncio
async def test_pipeline_marker_is_not_reported_as_a_verified_source():
    rows = [("rce_arc_pipeline", "verified", 40),   # 40 entities processed, any bucket
            ("nppes", "verified", 30), ("nppes", "not_found", 6), ("nppes", "unavailable", 4),
            ("oig_leie", "verified", 37), ("oig_leie", "excluded", 3)]
    svc = ReportDataService(_StubDb(rows))
    out = await svc.get_verification_coverage(None)

    names = [s["source"] for s in out["sources"]]
    assert "rce_arc_pipeline" not in names
    assert names == ["nppes", "oig_leie"]
    assert out["pipeline_runs"]["rows"] == {"rce_arc_pipeline": 40}
    assert "Not a verification result" in out["pipeline_runs"]["note"]
    nppes = next(s for s in out["sources"] if s["source"] == "nppes")
    assert nppes["counts"]["verified"] == 30 and nppes["total"] == 40
    assert "rce_arc_pipeline" in NON_SOURCE_VERIFICATION_ROWS


@pytest.mark.asyncio
async def test_only_pipeline_markers_is_insufficient_data_not_full_coverage():
    svc = ReportDataService(_StubDb([("rce_arc_pipeline", "verified", 12)]))
    out = await svc.get_verification_coverage(None)
    assert out["sources"] == [] and out["insufficient_data"] is True
