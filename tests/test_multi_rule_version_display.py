"""Regression guard, added 2026-10-02: a delivery CAN legitimately contain
ReviewRecords classified under more than one rule_version (traced by the SAM
fork: review_service.run_review() self-heals the rule set to the latest
version unconditionally, which can race ahead of a delivery's own
verify_and_classify calls between repeat cycles — see
sam-fix-be/FORK-FINDINGS.md). This constructs that scenario directly (a real
upgrade race is hard to trigger deterministically in a test) and proves the
report's rule_versions_in_effect/display handles it correctly: shows BOTH
versions, not a max/first/most-common pick.
"""
from __future__ import annotations

import pytest

from test_review_id_concurrency import _seed_promoted_delivery, _promoted_refs

pytestmark = pytest.mark.asyncio


async def test_report_shows_all_distinct_rule_versions_present(db_required, monkeypatch):
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify
    from app.reports.data.delivery_processing_data import DeliveryProcessingDataService
    from app.tefca_registry import models as reg
    from sqlalchemy import select

    intake_id = await _seed_promoted_delivery(n=4)
    async with async_session_maker() as db:
        refs = await _promoted_refs(db, intake_id, 4)
    assert len(refs) == 4

    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id, actor="multi-version-fixture")
    assert result["verified"] == 4
    real_version = result["outcomes"][0]["rule_version"]
    assert real_version is not None

    # Simulate a prior cycle's record at an OLDER version (a real upgrade race
    # would produce exactly this shape: two ReviewRecords for the same
    # delivery, different classification_rule_version — constructing it
    # directly rather than engineering the race is the honest, documented
    # shortcut here).
    async with async_session_maker() as db:
        rows = (await db.execute(
            select(reg.ReviewRecord).where(
                reg.ReviewRecord.entity_id.in_(
                    [o["entity_id"] for o in result["outcomes"]])))).scalars().all()
        assert len(rows) == 4
        rows[0].classification_rule_version = (real_version - 1) if real_version > 1 else 99
        await db.commit()
        older_version = rows[0].classification_rule_version

    async with async_session_maker() as db:
        svc = DeliveryProcessingDataService(db, intake_id=intake_id)
        dataset = await svc.build_dataset()

    versions = dataset["analyst"]["rule_versions_in_effect"]
    assert set(versions) == {real_version, older_version}, (
        f"expected both {real_version} and {older_version} in "
        f"rule_versions_in_effect, got {versions}")

    from app.reports.generator import generate_report
    async with async_session_maker() as db:
        report = await generate_report(
            db, report_type="delivery_processing", generated_by="multi-version-test",
            persist=False, query_parameters={"intake_id": intake_id})
    html = report["html"]
    assert str(real_version) in html and str(older_version) in html, (
        "the rendered report must literally show both rule versions, not pick one")
    assert "more than one version classified records" in html, (
        "the multi-version explanatory note must render when >1 version is present")
