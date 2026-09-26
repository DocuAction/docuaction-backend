"""P12 reconciliation — the evidence-completeness check recognises BOTH
evidence shapes a review_record can legitimately carry.

BACKGROUND (root-caused 2026-09-26 against the live September 2026 delivery,
job 0930826c-970e-419d-ab8d-f05bb4f99116)
──────────────────────────────────────────────────────────────────────────
"Every determination traces to evidence" flagged 6 of 258 reviews against
that delivery's promoted entities. All six were created 2026-09-15 through
2026-09-17 — before the delivery existed — by the single-entity Priority
Review / ad-hoc Verify action (`app.tefca_registry.review_service.run_review`,
reachable today from `POST /priority-review` and the entity detail page's
verify action), NOT by the bulk delivery-verification sampler
(`arc_pipeline.verify_and_classify`). That path's `verification_results`
shape is {fields, sources, address_match, confidence_score,
entity_resolution} and has never carried a `dimensions` array; it predates
that key and was never migrated onto it. Every one of the six DOES have at
least one `tefca_verifications` audit row (the platform's own definition of
"what an auditor needs to retrace a decision") — three real sources verified
(nppes/pecos/oig_leie), the other three (sam_gov/state_registry/irs)
correctly marked not_checked/not-implemented, exactly the same "unavailable
must never count against an entity" rule the rest of the platform already
follows. This is real, non-fabricated evidence in a shape the check did not
recognise — not missing evidence.

This module proves the fix: a review is only "without evidence" now if it
has BOTH no `dimensions` array AND no `tefca_verifications` row at all.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.tefca_registry import models as reg
from app.tefca_registry.rce import reconciliation

from test_human_review_workflow import _seed, rolled_back_db  # noqa: F401

SYN = "SYNTHETIC-EVSHAPE"


def _legacy_shaped_verification_results() -> dict:
    """The exact shape `review_service.run_review` writes — no `dimensions`
    key, ever, by design; a genuinely different evidence model."""
    return {
        "fields": {"npi": "1234567890"},
        "sources": {
            "nppes": {"status": "verified"},
            "pecos": {"status": "verified"},
            "oig_leie": {"status": "verified"},
            "sam_gov": {"status": "not_checked"},
            "state_registry": {"status": "not_checked",
                               "reason": "Connector not implemented"},
            "irs": {"status": "not_checked",
                   "reason": "Not applicable"},
        },
        "address_match": {"method": "skipped", "reason": "not evaluated"},
        "confidence_score": None,
        "entity_resolution": {"status": "resolved"},
    }


async def _add_review(db, entity_id, *, verification_results,
                       classification_bucket="B1", classification_rule="RULE-001"):
    review_id = f"REV-TEST-{uuid.uuid4().hex[:8]}"
    db.add(reg.ReviewRecord(
        id=uuid.uuid4(), review_id=review_id, entity_id=entity_id,
        verification_results=verification_results,
        classification_bucket=classification_bucket,
        classification_rule=classification_rule,
        classification_rule_version=2,
        created_at=datetime.utcnow(),
    ))
    await db.flush()
    return review_id


async def _add_verification_rows(db, entity_id, review_id, sources):
    for source, status in sources:
        db.add(reg.TefcaVerification(
            id=uuid.uuid4(), entity_id=entity_id, review_id=review_id,
            source=source, lookup_identifier="test", verification_status=status,
            detail="synthetic test row", data_source_label=source))
    await db.flush()


async def _entities_for(db, intake_id):
    from sqlalchemy import select

    from app.tefca_registry.rce import models as m

    rows = (await db.execute(
        select(m.RceCuratedRecord.canonical_entity_id)
        .where(m.RceCuratedRecord.source_intake_id == intake_id))).scalars().all()
    return list(rows)


class TestLegacyShapedReviewIsRecognisedAsEvidenced:
    """The exact six-review failure pattern: legacy shape + real audit rows."""

    async def test_a_legacy_shaped_review_with_an_audit_row_passes(self, rolled_back_db):
        db = rolled_back_db
        intake_id = await _seed(db, "LEGACYPASS", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        review_id = await _add_review(
            db, entity_id, verification_results=_legacy_shaped_verification_results())
        await _add_verification_rows(
            db, entity_id, review_id,
            [("nppes", "verified"), ("pecos", "verified"), ("oig_leie", "verified"),
             ("sam_gov", "not_checked"), ("state_registry", "not_checked"),
             ("irs", "not_checked")])
        await db.commit()

        result = await reconciliation.reconcile_delivery(db, intake_id)
        evidence_check = next(c for c in result["checks"]
                              if c["check"] == "Every determination traces to evidence")
        assert evidence_check["passed"] is True, evidence_check["detail"]
        assert "0 review(s)" in evidence_check["detail"]

    async def test_the_exact_dimensions_predicate_alone_would_still_flag_it(self, rolled_back_db):
        """Documents WHY the six reviews were flagged: read literally, the
        `dimensions`-only predicate (the pre-fix check) is still true for a
        legacy-shaped review. This is not a claim the code regressed to that
        state - it is the reproduction of the reported symptom, asserted
        directly against the same review row the test above proves is
        correctly recognised as evidenced end-to-end."""
        from sqlalchemy import text

        db = rolled_back_db
        intake_id = await _seed(db, "LEGACYSHAPE", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]
        review_id = await _add_review(
            db, entity_id, verification_results=_legacy_shaped_verification_results())
        await _add_verification_rows(db, entity_id, review_id, [("nppes", "verified")])
        await db.commit()

        dimensions_only = await db.scalar(text(
            "SELECT count(*) FROM review_records r WHERE r.review_id = :rid "
            "AND (r.verification_results IS NULL "
            "     OR r.verification_results->'dimensions' IS NULL "
            "     OR jsonb_array_length(r.verification_results->'dimensions') = 0)"
        ).bindparams(rid=review_id))
        assert dimensions_only == 1, (
            "a legacy-shaped review has no `dimensions` key by design - "
            "this is exactly the symptom the fix reclassifies using "
            "tefca_verifications, not a claim that dimensions exist")


class TestAGenuinelyUnevidencedReviewStillFails:
    """The fix widens what counts as evidence; it must not blanket-suppress a
    review that truly has neither shape - the negative/isolation case."""

    async def test_no_dimensions_and_no_audit_row_still_fails(self, rolled_back_db):
        db = rolled_back_db
        intake_id = await _seed(db, "NOEVIDENCE", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        await _add_review(db, entity_id, verification_results={"queue_source": None})
        await db.commit()

        result = await reconciliation.reconcile_delivery(db, intake_id)
        evidence_check = next(c for c in result["checks"]
                              if c["check"] == "Every determination traces to evidence")
        assert evidence_check["passed"] is False
        assert "1 review(s)" in evidence_check["detail"]

    async def test_null_verification_results_and_no_audit_row_still_fails(self, rolled_back_db):
        db = rolled_back_db
        intake_id = await _seed(db, "NULLRESULTS", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        await _add_review(db, entity_id, verification_results=None)
        await db.commit()

        result = await reconciliation.reconcile_delivery(db, intake_id)
        evidence_check = next(c for c in result["checks"]
                              if c["check"] == "Every determination traces to evidence")
        assert evidence_check["passed"] is False

    async def test_a_dq_bridge_work_item_is_still_excluded_not_reclassified(self, rolled_back_db):
        """The pre-existing 2026-09-16 exclusion for open work items must be
        untouched: they still pass by being excluded, not by suddenly
        acquiring a tefca_verifications row they never had."""
        db = rolled_back_db
        intake_id = await _seed(db, "WORKITEM", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        await _add_review(
            db, entity_id,
            verification_results={"queue_source": "RCE_DQ_HUMAN_REQUIRED"},
            classification_bucket=None, classification_rule=None)
        await db.commit()

        result = await reconciliation.reconcile_delivery(db, intake_id)
        evidence_check = next(c for c in result["checks"]
                              if c["check"] == "Every determination traces to evidence")
        assert evidence_check["passed"] is True


class TestIsolationAcrossDeliveries:
    """A review problem on one intake's entities must never affect another
    intake's reconciliation verdict - the join is scoped by entity_id IN
    (this intake's own curated records), never globally."""

    async def test_an_unevidenced_review_on_one_intake_does_not_affect_another(
            self, rolled_back_db):
        db = rolled_back_db
        broken_intake = await _seed(db, "ISOBROKEN", n=2)
        clean_intake = await _seed(db, "ISOCLEAN", n=2)

        broken_entity = (await _entities_for(db, broken_intake))[0]
        await _add_review(db, broken_entity, verification_results={})
        await db.commit()

        broken_result = await reconciliation.reconcile_delivery(db, broken_intake)
        clean_result = await reconciliation.reconcile_delivery(db, clean_intake)

        broken_check = next(c for c in broken_result["checks"]
                            if c["check"] == "Every determination traces to evidence")
        clean_check = next(c for c in clean_result["checks"]
                           if c["check"] == "Every determination traces to evidence")
        assert broken_check["passed"] is False
        assert clean_check["passed"] is True
        assert "0 review(s)" in clean_check["detail"]
