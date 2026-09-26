"""P12 reconciliation — the evidence-completeness check recognises BOTH
evidence shapes a review_record can legitimately carry, and only counts a
`tefca_verifications` row as evidence when its status is substantive.

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
that key and was never migrated onto it.

THE AUTHORITATIVE STATUS TAXONOMY (not invented for this fix — read from the
platform's own declarations):
  - `TefcaVerification`'s class docstring names five states verbatim:
    "verified | not_found | not_checked | unavailable | failed", and states
    the load-bearing rule: `unavailable` (a third party's outage) "must
    never count against an entity", while `not_found` (source reached, no
    record) "must" — collapsing them "converts an outage into a finding".
  - `bucket_classifier.VERIFICATION_STATES = (VERIFIED, NOT_FOUND,
    NOT_CHECKED, UNAVAILABLE, FAILED)` is the same five, as importable
    constants (`= "verified"/"not_found"/"not_checked"/"unavailable"/
    "failed"`).
  - `review_service.probe_sources` produces exactly these five plus one
    connector-specific literal, `excluded` (an active OIG LEIE exclusion
    hit; `clear` is LEIE's positive counterpart but is rewritten to
    `verified` by `run_review` before the row is persisted, so `clear`
    never itself reaches `tefca_verifications.verification_status`).
    `bucket_classifier.py`'s own rule conditions treat `not_found` and
    `excluded` as real classification inputs (`not_found` drives specific
    B1/B2 rules; `excluded` alone drives B4 disqualification) — i.e. the
    platform's own business rules already rely on both as substantive
    determinations, not as absence-of-evidence.
  - `not_checked` (`review_service.NO_CONNECTOR`: no connector built, no
    NPI to look up, or not applicable) and `failed` (an uncaught exception
    calling the connector — a bug in this code, not a fact about the
    entity) are both, by the platform's own words, non-evidence: a
    disclosed non-attempt and a technical failure respectively, never a
    statement about the entity.

QUALIFYING PREDICATE SELECTED: `verification_status IN ('verified',
'not_found', 'excluded')`. Anything else — `not_checked`, `unavailable`,
`failed`, or any future/unknown value — is non-qualifying BY DEFAULT (an
inclusion list, not an exclusion list), so a new non-evidence status can
never silently start counting as evidence.

This module proves: a review is "without evidence" only if it has BOTH no
populated `dimensions` array AND no `tefca_verifications` row whose status
is in that qualifying set.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from app.tefca_registry import models as reg
from app.tefca_registry.rce import reconciliation

from test_human_review_workflow import _seed, rolled_back_db  # noqa: F401


def _legacy_shaped_verification_results(**sources) -> dict:
    """The exact shape `review_service.run_review` writes — no `dimensions`
    key, ever, by design; a genuinely different evidence model."""
    return {
        "fields": {"npi": "1234567890"},
        "sources": sources or {"nppes": {"status": "verified"}},
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
    """`sources`: iterable of (source_name, verification_status)."""
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


async def _evidence_check(db, intake_id) -> dict:
    result = await reconciliation.reconcile_delivery(db, intake_id)
    return next(c for c in result["checks"]
               if c["check"] == "Every determination traces to evidence")


class TestQualifyingStatusesPass:
    """Each named substantive status, alone, is enough - both positive
    (`verified`) and negative (`not_found`, `excluded`) evidence."""

    async def test_a_verified_observation_alone_qualifies(self, rolled_back_db):
        db = rolled_back_db
        intake_id = await _seed(db, "QUALVERIFIED", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        review_id = await _add_review(
            db, entity_id,
            verification_results=_legacy_shaped_verification_results(
                nppes={"status": "verified"}))
        await _add_verification_rows(db, entity_id, review_id,
                                     [("nppes", "verified")])
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is True, check["detail"]
        assert "0 review(s)" in check["detail"]

    async def test_a_not_found_observation_alone_qualifies(self, rolled_back_db):
        """`not_found` is documented as evidence the model says "must" count
        against an entity - a real negative answer, not an absence."""
        db = rolled_back_db
        intake_id = await _seed(db, "QUALNOTFOUND", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        review_id = await _add_review(
            db, entity_id,
            verification_results=_legacy_shaped_verification_results(
                nppes={"status": "not_found"}))
        await _add_verification_rows(db, entity_id, review_id,
                                     [("nppes", "not_found")])
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is True, check["detail"]

    async def test_an_excluded_observation_alone_qualifies(self, rolled_back_db):
        """`excluded` (an active OIG LEIE hit) is the single most significant
        negative finding the model has - it alone drives B4 disqualification
        in bucket_classifier.py's own rules, so it must count as evidence."""
        db = rolled_back_db
        intake_id = await _seed(db, "QUALEXCLUDED", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        review_id = await _add_review(
            db, entity_id,
            verification_results=_legacy_shaped_verification_results(
                oig_leie={"status": "excluded"}))
        await _add_verification_rows(db, entity_id, review_id,
                                     [("oig_leie", "excluded")])
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is True, check["detail"]

    async def test_the_six_real_reviews_pattern_still_passes(self, rolled_back_db):
        """The reproduction of the actual reported symptom: three real
        sources verified, three correctly not_checked - mixed, exactly as
        observed on the live delivery."""
        db = rolled_back_db
        intake_id = await _seed(db, "SIXPATTERN", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        review_id = await _add_review(
            db, entity_id, verification_results=_legacy_shaped_verification_results(
                nppes={"status": "verified"}, pecos={"status": "verified"},
                oig_leie={"status": "verified"}, sam_gov={"status": "not_checked"},
                state_registry={"status": "not_checked"}, irs={"status": "not_checked"}))
        await _add_verification_rows(
            db, entity_id, review_id,
            [("nppes", "verified"), ("pecos", "verified"), ("oig_leie", "verified"),
             ("sam_gov", "not_checked"), ("state_registry", "not_checked"),
             ("irs", "not_checked")])
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is True, check["detail"]
        assert "0 review(s)" in check["detail"]


class TestNonQualifyingStatusesStillFail:
    """A review whose ONLY audit rows are non-substantive must still read as
    unevidenced - the fix widens what counts as evidence, it does not accept
    a row merely for existing."""

    async def test_only_not_checked_rows_still_fails(self, rolled_back_db):
        db = rolled_back_db
        intake_id = await _seed(db, "ONLYNOTCHECKED", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        review_id = await _add_review(
            db, entity_id,
            verification_results=_legacy_shaped_verification_results(
                sam_gov={"status": "not_checked"}, irs={"status": "not_checked"}))
        await _add_verification_rows(
            db, entity_id, review_id,
            [("sam_gov", "not_checked"), ("irs", "not_checked")])
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is False
        assert "1 review(s)" in check["detail"]

    async def test_only_unavailable_or_failed_rows_still_fails(self, rolled_back_db):
        """`unavailable` (a reached-and-errored outage) and `failed` (an
        uncaught exception in this code) are both, by the model's own
        words, never a fact about the entity."""
        db = rolled_back_db
        intake_id = await _seed(db, "ONLYUNAVAIL", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        review_id = await _add_review(
            db, entity_id,
            verification_results=_legacy_shaped_verification_results(
                nppes={"status": "unavailable"}, pecos={"status": "failed"}))
        await _add_verification_rows(
            db, entity_id, review_id,
            [("nppes", "unavailable"), ("pecos", "failed")])
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is False
        assert "1 review(s)" in check["detail"]

    async def test_no_verification_rows_at_all_still_fails(self, rolled_back_db):
        db = rolled_back_db
        intake_id = await _seed(db, "NOROWS", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        await _add_review(db, entity_id, verification_results={"queue_source": None})
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is False
        assert "1 review(s)" in check["detail"]

    async def test_null_verification_results_and_no_rows_still_fails(self, rolled_back_db):
        db = rolled_back_db
        intake_id = await _seed(db, "NULLRESULTS", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        await _add_review(db, entity_id, verification_results=None)
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is False


class TestPopulatedDimensionsPathIsUnaffected:
    """The original, modern evidence signal - a non-empty `dimensions` array
    from `verify_and_classify` - must keep passing on its own, even with no
    qualifying (or no) `tefca_verifications` rows at all."""

    async def test_populated_dimensions_passes_with_no_verification_rows(self, rolled_back_db):
        db = rolled_back_db
        intake_id = await _seed(db, "DIMSONLY", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        await _add_review(
            db, entity_id,
            verification_results={
                "dimensions": [{"dimension": "IDENTITY", "disposition": "PASS"}],
                "applicability": {}, "sufficiency": {}, "data_quality_flags": [],
            })
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is True, check["detail"]

    async def test_populated_dimensions_passes_even_with_only_non_qualifying_rows(
            self, rolled_back_db):
        """A modern arc_pipeline review always writes one `tefca_verifications`
        row stamped `verified` unconditionally, but the check must not
        *depend* on that: dimensions alone is sufficient, and a (synthetic,
        contrived) non-qualifying row alongside it must not flip a
        dimensions-evidenced review to unevidenced."""
        db = rolled_back_db
        intake_id = await _seed(db, "DIMSPLUSBAD", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        review_id = await _add_review(
            db, entity_id,
            verification_results={
                "dimensions": [{"dimension": "IDENTITY", "disposition": "PASS"}],
            })
        await _add_verification_rows(db, entity_id, review_id,
                                     [("rce_arc_pipeline", "unavailable")])
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is True, check["detail"]


class TestWorkItemExclusionIsUnaffected:
    async def test_a_dq_bridge_work_item_is_still_excluded_not_reclassified(self, rolled_back_db):
        """The pre-existing 2026-09-16 exclusion for open work items must be
        untouched: they still pass by being excluded, not by acquiring a
        qualifying tefca_verifications row they never had."""
        db = rolled_back_db
        intake_id = await _seed(db, "WORKITEM", n=2)
        entity_id = (await _entities_for(db, intake_id))[0]

        await _add_review(
            db, entity_id,
            verification_results={"queue_source": "RCE_DQ_HUMAN_REQUIRED"},
            classification_bucket=None, classification_rule=None)
        await db.commit()

        check = await _evidence_check(db, intake_id)
        assert check["passed"] is True


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
        review_id = await _add_review(db, broken_entity, verification_results={})
        await _add_verification_rows(db, broken_entity, review_id,
                                     [("nppes", "unavailable")])
        await db.commit()

        broken_check = await _evidence_check(db, broken_intake)
        clean_check = await _evidence_check(db, clean_intake)

        assert broken_check["passed"] is False
        assert clean_check["passed"] is True
        assert "0 review(s)" in clean_check["detail"]
