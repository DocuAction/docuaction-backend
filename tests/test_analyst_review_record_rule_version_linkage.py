"""Regression guard, added 2026-10-02: `_analyst()` in
`delivery_processing_data.py` used to find review records by
`source_record_id` only. `arc_pipeline.verify_and_classify` (the bulk
delivery-processing path this report is FOR) never sets `source_record_id` on
the `ReviewRecord` it creates — only `entity_id` — so every review record
from a delivery processed that way was silently invisible to this report,
including its classification rule and rule-set version. This test proves the
fix: a delivery run through the real bulk pipeline produces review records
that `build_dataset()` actually finds, each carrying a non-null
classification rule and version."""
from __future__ import annotations

import pytest

from test_review_id_concurrency import _seed_promoted_delivery, _promoted_refs

pytestmark = pytest.mark.asyncio


async def test_arc_pipeline_review_records_appear_with_rule_version(db_required, monkeypatch):
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify
    from app.reports.data.delivery_processing_data import DeliveryProcessingDataService

    intake_id = await _seed_promoted_delivery(n=3)
    async with async_session_maker() as db:
        refs = await _promoted_refs(db, intake_id, 3)
    assert len(refs) == 3

    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id, actor="rule-version-proof")
    assert result["verified"] == 3
    for o in result["outcomes"]:
        assert o["rule_version"] is not None, "arc_pipeline must record a rule version per outcome"

    async with async_session_maker() as db:
        svc = DeliveryProcessingDataService(db, intake_id=intake_id)
        dataset = await svc.build_dataset()

    analyst = dataset["analyst"]
    assert analyst["review_records_total"] == 3, (
        f"expected all 3 arc_pipeline-created review records to be found via the "
        f"entity_id linkage, got {analyst['review_records_total']}")
    assert analyst["rule_versions_in_effect"], "rule_versions_in_effect must be non-empty"
    for r in analyst["review_records"]:
        assert r["classification_rule_version"] is not None, (
            f"review record {r['review_id']} is missing its classification_rule_version")
        assert r["classification_rule"] is not None
