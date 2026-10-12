"""Regression guard, added 2026-10-02: proves `generate_report()` for
`delivery_processing` reads ONLY already-persisted evidence/classification
(`ReviewRecord`, `TEFCADimensionEvidence`, disposition/finding/identifier
ledgers) and never re-invokes `verify_and_classify` or makes a live call to
any of NPPES/LEIE/SAM/CMS PPEF at render time.

Method: process a delivery through the real pipeline once (so its evidence
IS persisted), THEN monkeypatch all four connector classes so any further
call raises unconditionally, then generate the report and assert it succeeds
with content identical to a baseline generation from before the connectors
were broken. If report generation instead failed, or silently produced
different content, that would mean rendering has a live dependency on
external evidence — exactly the defect this test exists to catch.
"""
from __future__ import annotations

import pytest

from test_review_id_concurrency import _seed_promoted_delivery, _promoted_refs

pytestmark = pytest.mark.asyncio


async def test_report_generation_has_zero_live_connector_dependency(db_required, monkeypatch):
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify
    from app.reports.generator import generate_report

    intake_id = await _seed_promoted_delivery(n=3)
    async with async_session_maker() as db:
        refs = await _promoted_refs(db, intake_id, 3)
    assert len(refs) == 3

    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id, actor="report-live-dep-check")
    assert result["verified"] == 3

    # Baseline: generate once, with real connectors still intact (though
    # nothing should call them — this just establishes the "before" content).
    async with async_session_maker() as db:
        baseline = await generate_report(
            db, report_type="delivery_processing", generated_by="test-baseline",
            persist=False, query_parameters={"intake_id": intake_id})

    # Now make every connector explode on any call.
    async def _boom(*a, **k):
        raise RuntimeError("SYNTHETIC: connector must never be called during report generation")

    import app.Tefca.connectors as c
    for cls_name in ("NPPESConnector", "OIGLEIEConnector", "SAMGovConnector"):
        cls = getattr(c, cls_name)
        for method in ("lookup_by_npi", "lookup_by_name", "verify", "check_exclusions"):
            if hasattr(cls, method):
                monkeypatch.setattr(cls, method, _boom)
    import app.Tefca.cms_ppef as cp
    monkeypatch.setattr(cp.CMSDataAPIClient, "fetch_all", _boom)
    monkeypatch.setattr(cp.CMSDataAPIClient, "probe", _boom)
    # Belt and suspenders: the shared fetch helper itself.
    monkeypatch.setattr(c, "_get_with_retry", _boom)

    async with async_session_maker() as db:
        after = await generate_report(
            db, report_type="delivery_processing", generated_by="test-baseline",
            persist=False, query_parameters={"intake_id": intake_id})

    import re

    def _strip_generation_timestamp(html: str) -> str:
        # The ONLY expected difference between two generations of the SAME
        # persisted evidence is the "generated at" timestamp baked into the
        # HTML (dcterms.modified / the report header) — real wall-clock time
        # each call runs, not a function of the evidence. Strip ISO-8601
        # timestamps before comparing so this proof isn't defeated by that
        # expected, harmless non-determinism.
        html = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00", "<TS>", html)
        # The Report Provenance table prints the SAME generation time as "YYYY-MM-DD HH:MM:SS" (space, no offset). The two
        # generations run a second or more apart, so that value legitimately differs whenever they straddle a second boundary
        # (root cause of the intermittent failure, measured 2026-10-10: the only diff line was
        # "Report generated (UTC) ... 07:26:04" vs "07:26:05"). It is normalised like the ISO form above; every other byte
        # of the report is still compared.
        html = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?=</td>)", "<TS>", html)
        # report_id is sequential per generation (DA-ARC-YYYY-NNN) — a new,
        # expected value every call, not a function of the evidence.
        html = re.sub(r"DA-ARC-\d{4}-\d+", "<REPORT-ID>", html)
        return html

    assert _strip_generation_timestamp(after["html"]) == _strip_generation_timestamp(baseline["html"]), (
        "report HTML content (ignoring generation timestamps) changed after "
        "connectors were made to always raise — report generation must not "
        "depend on a live connector call")

    def _strip_dataset_timestamps(d):
        if isinstance(d, dict):
            return {k: _strip_dataset_timestamps(v) for k, v in d.items()
                    if k not in ("generation_timestamp", "generated_at", "report_id")}
        if isinstance(d, list):
            return [_strip_dataset_timestamps(v) for v in d]
        return d

    assert _strip_dataset_timestamps(after["dataset"]) == _strip_dataset_timestamps(baseline["dataset"]), (
        "report dataset content (ignoring generation timestamps) changed "
        "after connectors were made to always raise")
