"""CM-12's real precondition, proved against a real (rolled-back) database — 2026-10-28.

WHY THIS FILE EXISTS
─────────────────────
CM-12 asks the case-update importer to demonstrate "several matches" (needs_review) for an
ambiguous identity. The originally-planned fixture — two rows sharing one (intake_id,
source_rce_id) pair WITHIN ONE DELIVERY — cannot reach that state at all: `field_map.py` maps
the delivered `id` column directly to `rce_org_oid` (they are the same value), and
`quality_rules.py`'s SCH-003 holds a duplicate `id` within one delivery as CRITICAL/HUMAN_REQUIRED
before either row can reach `canonical_entity_id` — so one delivery can promote AT MOST one entity
per `rce_org_oid`. "Several matches" can only come from `_case_identity_resolver`
(`review_routes.py`) finding MORE THAN ONE `ReviewRecord` for the SAME `entity_id` — and that
resolver's own `ReviewRecord` lookup is not scoped to one intake, by design.

THE ACTUAL, LEGITIMATE PRECONDITION, PROVED HERE
──────────────────────────────────────────────────
One entity CAN legitimately carry more than one `ReviewRecord` the moment the SAME organisation
(same `rce_org_oid`) is delivered in TWO SEPARATE deliveries and each delivery's own review cycle
happens to draw it. Nothing in the schema prevents it (`review_records.entity_id` carries only a
plain, non-unique index — `idx_review_records_entity` — never a uniqueness constraint), and
`review_cycle.create_review_cycle` is the ordinary, supported, audited way to draw a cycle; calling
it once per delivery is calling it correctly, not abusing it. This test reaches that state using
ONLY the already-existing, already-tested pipeline functions this suite uses everywhere else
(`seed_intake` -> `run_quality_and_curation` -> `promote_delivery` -> `create_review_cycle`) against
TWO synthetic deliveries that share one `rce_org_oid` — no ad hoc SQL, no duplicate-id-within-one-
delivery fixture, and nothing written outside the rolled-back outer transaction.

Determinism, not luck: each delivery here has a population of exactly ONE eligible record.
`CochranSampler.calculate_sample_size` returns `max(1, min(population_size, ceil(n)))`, which is
always exactly 1 when `population_size == 1` — the lone record is drawn into its own delivery's
sample with certainty, not merely with high probability under some seed.
"""
from __future__ import annotations

import uuid

import pytest

from app.tefca_registry import models as reg
from app.tefca_registry.review_cycle import create_review_cycle
from app.tefca_registry.review_routes import _case_identity_resolver
from app.tefca_registry.rce import case_update_import as cui
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, curated_by_oid, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)


class _User:
    email = "qa-cm12@docuaction.io"
    id = None
    role = "program_manager"


@pytest.fixture
async def entity_sampled_twice(rolled_back_db):
    """One entity, independently promoted and independently sampled from TWO
    separate deliveries. Returns (db, shared_oid, entity_id, intake_a, intake_b,
    review_id_a, review_id_b)."""
    db = rolled_back_db

    # Same `arc` on both calls -> identical id/TEFCAID/HCID/name: the ordinary
    # "re-delivered next month, nothing about this org's identifiers changed"
    # case. A DIFFERENT TEFCAID/HCID for the same rce_org_oid is a genuine
    # identifier conflict (`promotion._identifier_conflicts`) and correctly
    # HELDs the record -- that is a different, already-covered scenario
    # (the NPI-008/IDENTIFIER_EXISTING_VALUE_CONFLICT family), not this one.
    #
    # The two deliveries' raw bytes must NOT be fully identical, though: the
    # fixture's own `seed_intake` registers a `RceDeliveryJob` keyed by a
    # content hash, and `uq_rce_delivery_job_active_identity` (a real,
    # intentional guard against double-registering one file while it is still
    # in flight) refuses a second ACTIVE job for byte-identical content -- this
    # fixture's two jobs are never finalised to terminal, so both are active at
    # once. A trivial non-identifier field (address_line) makes the second
    # delivery's bytes genuinely different, same as any two real monthly files
    # would be, without touching anything `_identifier_conflicts` compares.
    rows_a = make_rows(1, arc="9.99.777.81")
    rows_b = make_rows(1, arc="9.99.777.81", address_line="1 Synthetic Way, Suite 2")
    shared_oid = rows_a[0]["id"]

    intake_a, _ = await seed_intake(db, rows_a)
    await run_quality_and_curation(db, intake_a)
    await promote_delivery(db, intake_a, actor=SYN)
    curated_a = await curated_by_oid(db, intake_a, shared_oid)
    assert curated_a.record_status != "HELD", "the shared-oid record must promote cleanly, not be held"
    assert curated_a.canonical_entity_id is not None
    entity_id = curated_a.canonical_entity_id

    intake_b, _ = await seed_intake(db, rows_b)
    await run_quality_and_curation(db, intake_b)
    await promote_delivery(db, intake_b, actor=SYN)
    curated_b = await curated_by_oid(db, intake_b, shared_oid)
    assert curated_b.record_status != "HELD", "the re-delivered record must MATCH, not be held"
    # Same organisation, same registry entity -- the designed MATCHED/UPDATED path, not a new entity.
    assert curated_b.canonical_entity_id == entity_id

    cycle_a = await create_review_cycle(db, intake_a, user=_User(), review_type="quarterly",
                                        confidence=0.95, margin=0.10)
    cycle_b = await create_review_cycle(db, intake_b, user=_User(), review_type="quarterly",
                                        confidence=0.95, margin=0.10)
    assert cycle_a["linked_this_call"] == 1 and cycle_a["remaining"] == 0
    assert cycle_b["linked_this_call"] == 1 and cycle_b["remaining"] == 0

    from sqlalchemy import select
    review_ids = sorted((await db.execute(
        select(reg.ReviewRecord.review_id).where(reg.ReviewRecord.entity_id == entity_id))).scalars().all())
    assert len(review_ids) == 2, (
        "the whole point of this fixture: one entity, two independently-drawn review cases")

    return db, shared_oid, entity_id, intake_a, intake_b, review_ids[0], review_ids[1]


async def test_one_entity_can_legitimately_carry_two_review_records(entity_sampled_twice):
    """Q1/Q2 (doc 58/59 follow-up): proves the precondition itself is reachable through the
    ordinary, supported review-cycle-draw operation -- not asserted from reading the code alone."""
    db, shared_oid, entity_id, intake_a, intake_b, rev_a, rev_b = entity_sampled_twice
    assert rev_a != rev_b
    assert rev_a.startswith("REV-") and rev_b.startswith("REV-")


async def test_case_identity_resolver_reports_both_reviews_not_one_guessed(entity_sampled_twice):
    """Q3: the resolver identifies ReviewRecords (by review_id), via entity_id -- never by
    guessing a source record or silently preferring one review over the other."""
    db, shared_oid, entity_id, intake_a, intake_b, rev_a, rev_b = entity_sampled_twice
    resolve = _case_identity_resolver(db)

    matches = await resolve({"intake_id": str(intake_a), "source_rce_id": shared_oid})
    assert sorted(matches) == sorted([rev_a, rev_b])

    # The SAME ambiguity is visible from the OTHER delivery too -- it is a property of the
    # entity, not of which delivery's row happens to be cited.
    matches_from_b = await resolve({"intake_id": str(intake_b), "source_rce_id": shared_oid})
    assert sorted(matches_from_b) == sorted([rev_a, rev_b])


async def test_ambiguous_identity_is_needs_review_not_rejected_and_nothing_is_applied(entity_sampled_twice):
    """Q4: the exact point of rejection. `preview_rows` (case_update_import.py) routes a
    multi-match identity to needs_review -- a DIFFERENT disposition from `rejected` (reserved for
    zero matches / malformed identity) -- and `plan_apply` then refuses to apply it, same as any
    other non-accepted row. The ambiguity is never silently resolved by picking a review_id."""
    db, shared_oid, entity_id, intake_a, intake_b, rev_a, rev_b = entity_sampled_twice
    resolve = _case_identity_resolver(db)

    preview = await cui.preview_rows(
        [{"intake_id": str(intake_a), "source_rce_id": shared_oid,
          "analyst_note": "two cases exist for this organisation"}],
        resolve)
    row = preview["rows"][0]
    assert row["disposition"] == cui.DISP_NEEDS_REVIEW
    assert sorted(row["matched_review_ids"]) == sorted([rev_a, rev_b])
    assert "2 cases match this identity" in row["reasons"][0]
    assert "nothing is applied" in row["reasons"][0]
    assert preview["counts"] == {"accepted": 0, "unchanged": 0, "rejected": 0, "needs_review": 1}

    plan = cui.plan_apply(preview)
    assert plan["assignments"] == [] and plan["staged_not_applied"] == []
    assert len(plan["not_applicable"]) == 1
    assert plan["not_applicable"][0]["disposition"] == cui.DISP_NEEDS_REVIEW
    assert plan["reconciles"] is True
