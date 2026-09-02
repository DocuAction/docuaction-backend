"""Create the official review cycle for a delivery — the link that was missing.

WHAT INDEPENDENT REVIEW FOUND
─────────────────────────────
The workflow is stated as

    delivery → ready → REVIEW CYCLE → QHIN assignment → analyst → QA

and GO 1 wrote "Review Cycles (existing)". It was not. Three facts, each
verified against source:

  * `qhin_sampling.finalize_plan` — the approved per-QHIN methodology, the only
    thing that may draw an official sample — was called by NO route. The
    frontend's "Create review cycle" linked to a page that posts to the legacy
    `/api/v1/tefca/cycles`, which knows nothing about a delivery, a
    reconciliation verdict or a QHIN stratum.
  * `SampleEntity.review_id` was written by NOTHING. A drawn plan's members
    never acquired a review case, so `plan_completion` reported every member as
    `no_review_case` forever, and the analyst queue never saw the sample.
  * `POST /samples` — the generic Cochran sampler over the whole registry, at
    contributor — was the only reachable sampler, and it has no reconciliation
    gate and no QHIN strata.

This module is the missing transition, and only that. It introduces no
sampling, no classification and no decision vocabulary. It calls the three
functions that already own those and writes the two links that nothing wrote.

THE GATE
────────
Reconciliation must have PASSED. Not run — passed. A delivery whose A–F
populations do not close is not a population, and drawing a sample from it
would make the sample exactly as unaccountable as the delivery. The check is
performed here, server-side, on every call; the dashboard's disabled button is
a courtesy, not the control.

IDEMPOTENT, AND SERIALISED PER DELIVERY
───────────────────────────────────────
`finalize_plan` returns the plan already drawn rather than redrawing, so asking
twice yields one sample. Verification is then run ONLY for members whose
`review_id` is still NULL, so asking twice yields one review case per member.
The work is bounded per call (`batch`) because verification calls external
Government sources per entity and a 3,000-member sample is minutes of upstream
traffic; the caller repeats until `remaining` is zero.

`verify_and_classify` commits internally, which would release a
transaction-scoped advisory lock mid-way and let a second caller verify the
same members before the first had linked them. So the lock here is
SESSION-scoped (`pg_advisory_lock`), held across that commit and released in
`finally`. One cycle-creation per delivery at a time, no matter how many
Program Managers click.

WHAT EVERY REVIEW CASE CREATED HERE CARRIES
───────────────────────────────────────────
    ReviewRecord.sample_id                       → the plan it belongs to
    verification_results.source_intake_id        → the delivery
    verification_results.queue_source            → RCE_SAMPLE_REVIEW
    verification_results.sample_id               → the plan (again, for filters)
    SampleEntity.review_id                       → back-link, so completion works

`source_intake_id` is the key `case_assignment._queue` and `qhin_workload`
scope by. A review case without it is invisible to both, which is precisely
what was wrong before.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text

from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m

logger = logging.getLogger(__name__)

#: The queue_source stamped on every review case created from an official
#: sample. Distinct from `dq_review_bridge.QUEUE_SOURCE` (RCE_DQ_HUMAN_REQUIRED)
#: so the two populations can be told apart in the same queue.
QUEUE_SOURCE = "RCE_SAMPLE_REVIEW"

#: Members verified per call. Bounded because each one is several upstream
#: Government-source calls; see the module docstring.
DEFAULT_BATCH = 200
MAX_BATCH = 1000


class CycleRefused(RuntimeError):
    """The cycle could not be created, and the reason is stated."""


async def create_review_cycle(
    db, intake_id, *, user,
    review_type: str = "quarterly",
    confidence: float = 0.95, margin: float = 0.05, proportion: float = 0.5,
    use_fpc: bool = True, include_held: bool = False,
    seed: Optional[int] = None, sample_name: Optional[str] = None,
    batch: int = DEFAULT_BATCH,
    ip_address: Optional[str] = None,
) -> Dict[str, Any]:
    """Gate → draw (or reuse) the plan → verify unlinked members → link.

    Returns the plan, how many members are linked, how many remain, and the
    verification summary for this call. Call again until `remaining` is 0.
    """
    from app.tefca_registry.qhin_sampling import SamplingRefused, finalize_plan
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify
    from app.tefca_registry.rce.reconciliation import reconcile_delivery

    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise CycleRefused(f"No delivery {intake_id}")

    actor_id, actor_email = reg_audit.actor_of(user)
    batch = max(1, min(int(batch or DEFAULT_BATCH), MAX_BATCH))

    # ── the gate ─────────────────────────────────────────────────────────────
    recon = await reconcile_delivery(db, intake_id)
    if not recon.get("passed"):
        failed = [c["check"] for c in recon.get("checks", []) if not c["passed"]]
        reg_audit.record(
            db, "review_cycle_refused", None, actor_id=actor_id,
            actor_email=actor_email, ip_address=ip_address,
            metadata={"source_intake_id": str(intake_id),
                      "reason": "reconciliation_not_passed",
                      "failed_checks": failed[:10]})
        await db.commit()
        raise CycleRefused(
            f"Delivery {intake_id} has not passed reconciliation; a review "
            f"cycle cannot be drawn from a population that does not close. "
            f"Failed: {', '.join(failed[:5]) or 'see reconciliation'}.")

    # ── one creator per delivery at a time ───────────────────────────────────
    lock_key = f"review_cycle:{intake_id}"
    await db.execute(text("select pg_advisory_lock(hashtext(:k))"), {"k": lock_key})
    try:
        try:
            plan = await finalize_plan(
                db, intake_id, review_type=review_type, confidence=confidence,
                margin=margin, proportion=proportion, use_fpc=use_fpc,
                include_held=include_held, seed=seed, actor=actor_email,
                actor_id=actor_id, sample_name=sample_name)
        except SamplingRefused as exc:
            await db.rollback()
            raise CycleRefused(str(exc))
        sample_id = uuid.UUID(plan["sample_id"])
        # finalize_plan flushes but does not commit; the plan is made durable
        # here, BEFORE verification, so a verification failure part-way cannot
        # roll the official sample back and let the next attempt redraw it.
        await db.commit()

        unlinked = (await db.execute(
            select(reg.SampleEntity)
            .where(reg.SampleEntity.sample_id == sample_id,
                   reg.SampleEntity.review_id.is_(None))
            .order_by(reg.SampleEntity.stratum, reg.SampleEntity.entity_id)
        )).scalars().all()
        total_unlinked = len(unlinked)
        this_call = unlinked[:batch]

        verified: Dict[str, Any] = {"requested": 0, "verified": 0,
                                    "unresolved": [], "bucket_counts": {}}
        linked = 0
        if this_call:
            refs = await _refs_for(db, intake_id,
                                   [member.entity_id for member in this_call])
            member_by_entity = {member.entity_id: member for member in this_call}
            ref_to_entity = {ref: eid for eid, ref in refs.items()}

            result = await verify_and_classify(
                db, list(refs.values()), intake_id=intake_id,
                actor=actor_email or "SYSTEM")
            verified = {k: result.get(k) for k in
                        ("requested", "verified", "unresolved", "bucket_counts")}

            # verify_and_classify has committed its ReviewRecords. Link them.
            for outcome in result.get("outcomes", []):
                entity_id = ref_to_entity.get(outcome.get("entity_ref"))
                member = member_by_entity.get(entity_id)
                review_id = outcome.get("review_id")
                if member is None or not review_id:
                    continue
                record = (await db.execute(
                    select(reg.ReviewRecord)
                    .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
                if record is None:
                    continue
                record.sample_id = sample_id
                # Replaced, not mutated: SQLAlchemy does not see an in-place
                # change to a JSONB dict.
                record.verification_results = {
                    **(record.verification_results or {}),
                    "source_intake_id": str(intake_id),
                    "queue_source": QUEUE_SOURCE,
                    "sample_id": str(sample_id),
                }
                member.review_id = review_id
                member.review_status = "in_review"
                linked += 1

            reg_audit.record(
                db, "review_cycle_members_verified", None, actor_id=actor_id,
                actor_email=actor_email, ip_address=ip_address,
                metadata={"source_intake_id": str(intake_id),
                          "sample_id": str(sample_id),
                          "requested": verified.get("requested"),
                          "verified": verified.get("verified"),
                          "linked": linked,
                          "unresolved": len(verified.get("unresolved") or [])})
            await db.commit()

        if not plan.get("already_finalized"):
            reg_audit.record(
                db, "review_cycle_created", None, actor_id=actor_id,
                actor_email=actor_email, ip_address=ip_address,
                metadata={"source_intake_id": str(intake_id),
                          "sample_id": str(sample_id),
                          "review_type": review_type,
                          "sample_size": plan.get("sample_size"),
                          "population_size": plan.get("population_size"),
                          "reconciliation_passed": True})
            await db.commit()
    finally:
        try:
            await db.execute(text("select pg_advisory_unlock(hashtext(:k))"),
                             {"k": lock_key})
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("advisory unlock failed for %s: %s", lock_key,
                           type(exc).__name__)

    remaining = max(total_unlinked - linked, 0)
    return {
        "intake_id": str(intake_id),
        "plan": plan,
        "reconciliation_passed": True,
        "members": plan.get("membership_count"),
        "linked_this_call": linked,
        "remaining": remaining,
        "complete": remaining == 0,
        "verification": verified,
        "batch": batch,
        "note": ("The sample is frozen; asking again never redraws it. "
                 "Verification runs only for members not yet linked to a "
                 "review case, so repeated calls converge and never duplicate. "
                 "Call again until remaining is 0."),
    }


async def read_review_cycle(db, intake_id) -> Dict[str, Any]:
    """The plans drawn for this delivery and how complete each one is."""
    from app.tefca_registry.qhin_sampling import get_plan, plan_completion

    samples = (await db.execute(
        select(reg.ReviewSample)
        .where(reg.ReviewSample.strata_config["source_intake_id"].astext
               == str(intake_id))
        .order_by(reg.ReviewSample.drawn_at.desc()))).scalars().all()

    plans: List[Dict[str, Any]] = []
    for sample in samples:
        plan = await get_plan(db, sample.id)
        unlinked = (await db.execute(
            select(reg.SampleEntity.id)
            .where(reg.SampleEntity.sample_id == sample.id,
                   reg.SampleEntity.review_id.is_(None)))).all()
        plan["unlinked_members"] = len(unlinked)
        plan["completion"] = await plan_completion(db, sample.id)
        plans.append(plan)

    return {"intake_id": str(intake_id), "plans": plans, "count": len(plans)}


async def _refs_for(db, intake_id, entity_ids: List[Any]) -> Dict[Any, str]:
    """entity_id -> rce_org_oid, the reference `resolve_entity` matches on.

    Read from THIS delivery's curated records so the reference is the one the
    delivery actually carried for that organisation.
    """
    if not entity_ids:
        return {}
    rows = (await db.execute(
        select(m.RceCuratedRecord.canonical_entity_id,
               m.RceCuratedRecord.rce_org_oid)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.canonical_entity_id.in_(entity_ids),
               m.RceCuratedRecord.rce_org_oid.isnot(None)))).all()
    out: Dict[Any, str] = {}
    for entity_id, ref in rows:
        out.setdefault(entity_id, ref)
    return out
