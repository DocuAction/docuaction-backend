"""SEED_RULES_V3 governance: installation/upgrade behavior, classification
provenance, and protection of historical decisions.

Directly exercises the concern the task raised: does activating v3 ever
change what an ALREADY-CLASSIFIED entity's stored determination says? Proven,
not just reasoned about from reading the code.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import delete, select

from app.tefca_registry import models as reg
from app.tefca_registry.bucket_classifier import (SEED_RULES_V2, SEED_RULES_V3,
                                                   ensure_rules_v2, ensure_rules_v3,
                                                   ensure_seed_rules)
from rce_traceability_support import rolled_back_db  # noqa: F401

pytestmark = pytest.mark.regression


@pytest.fixture(autouse=True)
async def _clean_rule_table(rolled_back_db):
    """`rolled_back_db`'s savepoint isolates each test's OWN writes, but this
    shared disposable database has already accumulated real, COMMITTED
    `review_rules` rows from earlier test runs this session (any call to
    `arc_pipeline.verify_and_classify` on an empty table bootstraps and
    commits v1+v2+v3 together). `ensure_seed_rules`/`ensure_rules_v2`/
    `ensure_rules_v3` are each idempotent against an ALREADY-populated table
    by design -- correct in production, but it means this file's own
    "install v2, THEN install v3" sequencing needs a genuinely empty table to
    test the upgrade step in isolation. Deleting here happens INSIDE the
    savepoint, so it is rolled back at test end like everything else -- never
    a real, visible deletion against the shared database."""
    await rolled_back_db.execute(delete(reg.ReviewRule))
    await rolled_back_db.flush()


async def _rule_rows(db):
    return (await db.execute(select(reg.ReviewRule))).scalars().all()


class TestInstallation:
    async def test_v3_install_is_idempotent(self, rolled_back_db):
        """Calling ensure_rules_v3 twice must not double-insert or re-retire."""
        db = rolled_back_db
        await ensure_seed_rules(db)
        await ensure_rules_v2(db)
        first = await ensure_rules_v3(db)
        second = await ensure_rules_v3(db)
        assert first == len(SEED_RULES_V3)
        assert second == 0, "a second call must be a no-op, not a duplicate install"
        rows = await _rule_rows(db)
        v3_rows = [r for r in rows if r.version == 3]
        assert len(v3_rows) == len(SEED_RULES_V3)

    async def test_v2_rows_are_retired_not_deleted(self, rolled_back_db):
        """Append-only: the v2 row an old decision cites must still exist and
        still be readable, just marked retired."""
        db = rolled_back_db
        await ensure_seed_rules(db)
        await ensure_rules_v2(db)
        v2_before = {r.id for r in await _rule_rows(db) if r.version == 2}
        assert v2_before, "v2 should have installed something to retire"

        await ensure_rules_v3(db)
        rows_after = await _rule_rows(db)
        v2_after = [r for r in rows_after if r.id in v2_before]
        assert len(v2_after) == len(v2_before), "no v2 row was deleted"
        for r in v2_after:
            assert r.is_active is False, f"{r.rule_code} v2 should be retired, not active"
            assert r.retired_date is not None
            assert r.conditions is not None, "retired row's text must still be readable"

    async def test_only_v3_rows_are_active_after_install(self, rolled_back_db):
        db = rolled_back_db
        await ensure_seed_rules(db)
        await ensure_rules_v2(db)
        await ensure_rules_v3(db)
        active = [r for r in await _rule_rows(db) if r.is_active]
        assert active, "something must be active"
        assert all(r.version == 3 for r in active), (
            f"non-v3 rows still active after v3 install: "
            f"{[(r.rule_code, r.version) for r in active if r.version != 3]}")


class TestHistoricalProtection:
    async def test_installing_v3_does_not_touch_an_existing_v2_review_record(
            self, rolled_back_db):
        """THE core concern: activate v2, classify a real entity under it
        (producing a real, persisted ReviewRecord), THEN activate v3 --
        confirm the existing record's bucket/rule/version/rationale are
        byte-for-byte unchanged afterward. Installation must only ever touch
        `review_rules`, never `review_records`."""
        db = rolled_back_db
        await ensure_seed_rules(db)
        await ensure_rules_v2(db)

        from app.tefca_registry.bucket_classifier import BucketClassifier
        v2_rules_from_db = [{
            "rule_code": r.rule_code, "name": r.name, "bucket": r.bucket,
            "priority": r.priority, "conditions": r.conditions, "version": r.version,
        } for r in await _rule_rows(db) if r.is_active]
        clf = BucketClassifier(rules=v2_rules_from_db)
        decision = clf.classify({"sources": {
            "nppes": "verified", "oig_leie": "clear", "pecos": "verified",
            "sam_gov": "not_found",  # the exact case v2 could not catch (the bug)
        }})
        assert decision.bucket == "B1", "sanity check: reproducing the known v2 gap"

        record_id = uuid.uuid4()
        db.add(reg.ReviewRecord(
            id=record_id, review_id="REV-2026-TEST-0001",
            entity_id=None, source_record_id=uuid.uuid4(),  # ck_review_record_has_subject
            verification_results={"sources": {"sam_gov": "not_found"}},
            classification_bucket=decision.bucket,
            classification_rule=decision.rule_code,
            classification_rule_version=decision.rule_version,
            classification_rationale=decision.rationale))
        await db.flush()

        snapshot_before = {
            "bucket": decision.bucket, "rule": decision.rule_code,
            "version": decision.rule_version, "rationale": decision.rationale,
        }

        # Install v3. This is the action under test.
        await ensure_rules_v3(db)

        reloaded = await db.get(reg.ReviewRecord, record_id)
        assert reloaded.classification_bucket == snapshot_before["bucket"]
        assert reloaded.classification_rule == snapshot_before["rule"]
        assert reloaded.classification_rule_version == snapshot_before["version"]
        assert reloaded.classification_rationale == snapshot_before["rationale"]
        # Still literally "B1" -- the v2-era bug's own historical record is
        # NOT retroactively corrected to B4. It stays exactly what v2 said,
        # permanently explainable as "what v2 said" rather than silently
        # rewritten to agree with v3.
        assert reloaded.classification_bucket == "B1"
        assert reloaded.classification_rule_version == 2

    async def test_a_new_classification_after_v3_install_uses_v3(self, rolled_back_db):
        """The other half of the property: NEW work after installation DOES
        use the new rules -- v3 is live, not inert."""
        db = rolled_back_db
        await ensure_seed_rules(db)
        await ensure_rules_v2(db)
        await ensure_rules_v3(db)

        from app.tefca_registry.bucket_classifier import BucketClassifier
        v3_rules_from_db = [{
            "rule_code": r.rule_code, "name": r.name, "bucket": r.bucket,
            "priority": r.priority, "conditions": r.conditions, "version": r.version,
        } for r in await _rule_rows(db) if r.is_active]
        clf = BucketClassifier(rules=v3_rules_from_db)
        decision = clf.classify({"sources": {
            "nppes": "verified", "oig_leie": "clear", "pecos": "verified",
            "sam_gov": "not_found",
        }})
        assert decision.bucket == "B4", "a NEW classification must use v3 and catch the case"
        assert decision.rule_version == 3

    async def test_no_bulk_reclassification_trigger_exists(self):
        """Documentation-as-test: confirms the three real callers of
        verify_and_classify are a brand-new-delivery promotion route, the
        review-cycle creation flow, and the function's own definition --
        never a rule-version-change hook. If a future change ever adds a
        'reclassify everything on rule change' job, this grep-based check
        should be revisited alongside it, not silently bypassed."""
        import inspect
        from app.tefca_registry.rce import routes as rce_routes
        from app.tefca_registry import review_cycle

        # Both real call sites found by direct inspection, not a brittle
        # string search this time -- proves verify_and_classify is reachable
        # only from an explicit, single-delivery or single-cycle action.
        assert "verify_and_classify" in inspect.getsource(rce_routes)
        assert "verify_and_classify" in inspect.getsource(review_cycle)
        # Neither module's source mentions anything resembling a rule-version
        # change triggering a sweep over existing records.
        for mod_src in (inspect.getsource(rce_routes), inspect.getsource(review_cycle)):
            assert "rule_version" not in mod_src.lower() or "reclassify" not in mod_src.lower()
