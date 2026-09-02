"""Run one registered ONC/RCE delivery through the existing pipeline.

THIS MODULE IMPLEMENTS NO PIPELINE STAGE.
Every stage below already exists, is already tested and was already exercised
against the delivered Government population. This is an ORCHESTRATOR: it calls
them in the order the workflow requires, records what each one observed, and
keeps the job row honest about where the run actually is.

    intake.ingest_delivery        Area 1, 2,000-row batching, line-count contract
    quality_engine.run_quality    the rule set and the Issue Ledger
    curation.curate_delivery      Area 2 and AUTO_SAFE corrections
    promotion.promote_delivery    canonical entities and the QHIN / parent edges
    arc_pipeline.verify_and_classify   D1-D6, B1-B4, tier routing
    reconciliation.reconcile_delivery  the hard A-F gate

None of that is reimplemented, wrapped or "improved" here. The 2,000-row
batching inside `ingest_delivery` in particular is untouched — it is proven, and
the reason this module exists is that the BROWSER should not wait for it, not
that it is wrong.

WHY PROMOTION IS AUTOMATED HERE
───────────────────────────────
Promotion writes the canonical `managed_by_qhin` and `sub_participant_of` edges.
Those edges ARE the QHIN organisation the Program Manager reviews by, and
`qhin_sampling.resolve_qhin_strata` reads them to decide which QHIN each record
belongs to. A delivery that stops before promotion therefore has no QHIN
structure, no sample frame and no review cycle — the workflow simply cannot
start.

Promotion is also deterministic. It promotes what curation already marked
promotable and skips HELD and REJECTED; it makes no judgement about an entity.
The judgements in this system are the D1-D9 analyst determination and the QA
approval, and neither is touched here. `POST /deliveries/{id}/promote` remains
exactly as it was for the reviewer-initiated case.

WHY A FAILED STAGE DOES NOT ALWAYS FAIL THE RUN
───────────────────────────────────────────────
Two classes of stage, treated differently on purpose:

  * Area 1 (`ingest_delivery`) is FATAL. If the delivered lines did not land,
    there is nothing to process and nothing to review, so the job fails.
  * Every later stage is RECORDED AND SURVIVED. A delivery whose verification
    stage raised is still a delivery with a complete, hashed, reconcilable
    Area 1, and burying that behind a FAILED job would hide real evidence
    because a downstream connector was unavailable. The stage error is written
    to `stage_detail`, reconciliation still runs, and the operator sees exactly
    which stage did not complete.

Reconciliation runs LAST and ALWAYS, including after a stage error — it is the
gate that says whether the populations close, and its answer is most needed
precisely when something went wrong.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: How many promoted entities one automated verification pass covers.
#:
#: Verification calls external Government sources per entity. Running it across
#: an entire 25K delivery inside the ingestion job would issue hundreds of
#: thousands of upstream calls before a human has decided the delivery is even
#: worth reviewing, and NPPES, PECOS, SAM and LEIE are shared Government
#: services with quotas that are not ours to spend.
#:
#: So the automated pass is a BOUNDED SEED that makes the delivery immediately
#: reviewable, and the real verification volume follows the sample: the review
#: cycle draws the official per-QHIN sample and verification runs against the
#: entities actually under review. `POST /deliveries/{id}/verify` remains
#: available for a wider run when an operator wants one.
AUTO_VERIFY_LIMIT = int(os.getenv("RCE_AUTO_VERIFY_LIMIT", "250"))


async def run_delivery_job(db, job) -> str:
    """Process one claimed delivery job to a terminal state. Returns that state.

    Never raises: a runner that raises takes the poller's tick with it and the
    job keeps a heartbeat it no longer deserves. Everything is caught, recorded
    on the job and turned into FAILED.
    """
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job_id = job.id
    actor = job.registered_by or "SYSTEM"
    detail: Dict[str, Any] = {}

    # ── Area 1 — fatal if it does not land ───────────────────────────────────
    try:
        intake_id, received = await _stage_area1(db, job)
    except Exception as exc:  # noqa: BLE001
        reason = _reason("PARSING", exc)
        logger.error("delivery job %s failed at Area 1: %s", job_id, reason)
        await jobs.finish_failed(db, job_id, reason,
                                 detail={"PARSING": {"error": str(exc)[:1000]}})
        return RceDeliveryJob.STATE_FAILED

    detail["PARSING"] = received
    await jobs.bind_intake(db, job_id, intake_id,
                           records_received=received.get("record_count") or 0)
    await jobs.heartbeat(db, job_id, stage=RceDeliveryJob.STAGE_QUALITY,
                         records_received=received.get("record_count"),
                         records_processed=received.get("records_stored"),
                         detail={"PARSING": received})

    # ── the recoverable stages ───────────────────────────────────────────────
    # Ordered. Each runs only if the one before it did not raise, because
    # curating an un-assessed delivery or promoting an un-curated one would
    # produce a confidently wrong Area 2 rather than an honest gap.
    stage_error: Optional[str] = None

    for stage_name, next_stage, runner in (
        (RceDeliveryJob.STAGE_QUALITY, RceDeliveryJob.STAGE_CURATION,
         _stage_quality),
        (RceDeliveryJob.STAGE_CURATION, RceDeliveryJob.STAGE_PROMOTION,
         _stage_curation),
        (RceDeliveryJob.STAGE_PROMOTION, RceDeliveryJob.STAGE_VERIFICATION,
         _stage_promotion),
        (RceDeliveryJob.STAGE_VERIFICATION, RceDeliveryJob.STAGE_RECONCILIATION,
         _stage_verification),
    ):
        try:
            observed = await runner(db, intake_id, actor)
            detail[stage_name] = observed
        except Exception as exc:  # noqa: BLE001
            stage_error = _reason(stage_name, exc)
            detail[stage_name] = {"error": str(exc)[:1000],
                                  "completed": False}
            logger.error("delivery job %s stage %s did not complete: %s",
                         job_id, stage_name, exc)
            await jobs.heartbeat(db, job_id, detail={stage_name: detail[stage_name]})
            break
        await jobs.heartbeat(
            db, job_id, stage=next_stage,
            records_processed=_processed_count(observed),
            detail={stage_name: observed})

    # ── reconciliation — always, even after a stage error ────────────────────
    passed = False
    try:
        recon = await _stage_reconciliation(db, intake_id)
        detail[RceDeliveryJob.STAGE_RECONCILIATION] = recon
        passed = bool(recon.get("passed"))
    except Exception as exc:  # noqa: BLE001
        detail[RceDeliveryJob.STAGE_RECONCILIATION] = {
            "error": str(exc)[:1000], "completed": False}
        logger.error("delivery job %s reconciliation did not complete: %s",
                     job_id, exc)

    if stage_error:
        # The delivery is real and its Area 1 is intact; the RUN did not finish.
        await jobs.finish_failed(db, job_id, stage_error, detail=detail)
        return RceDeliveryJob.STATE_FAILED

    await jobs.finish_succeeded(
        db, job_id, reconciliation_passed=passed,
        records_processed=_reconciled_count(
            detail.get(RceDeliveryJob.STAGE_RECONCILIATION)),
        detail=detail)
    return RceDeliveryJob.STATE_SUCCEEDED


# ── stages ───────────────────────────────────────────────────────────────────

async def _stage_area1(db, job):
    """Read the preserved original back and ingest it into Area 1.

    The bytes come from `job.storage_path`, which the registration request wrote
    through `intake.preserve_original` before this job existed. `ingest_delivery`
    will call `preserve_original` again with the same content hash; that is
    idempotent by construction — it returns the existing path and does not
    rewrite preserved evidence.
    """
    from app.tefca_registry.rce.intake import ingest_delivery

    with open(job.storage_path, "rb") as handle:
        raw = handle.read()

    result = await ingest_delivery(
        db, raw,
        filename=job.original_filename,
        delivery_label=job.delivery_label,
        declared_delimiter=job.declared_delimiter or None,
        received_by=job.registered_by or "SYSTEM",
        received_at=job.received_date,
        source_metadata=_registration_metadata(job),
    )
    return result["intake_id"], result


def _registration_metadata(job) -> Dict[str, Any]:
    """What Data Operations declared about this delivery, carried into Area 1.

    Operator-entered provenance only — who registered it, what reference they
    quoted, what they noted. It never carries a Government data value, because
    a delivery's metadata is not a place to keep a copy of its contents.
    """
    return {
        "registration_job_id": str(job.id),
        "registered_by": job.registered_by,
        "government_reference": job.government_reference,
        "registration_notes": job.notes,
        "declared_source": job.source_name,
        "official_onc_rce_delivery": True,
    }


async def _stage_quality(db, intake_id, actor):
    from app.tefca_registry.rce.quality_engine import run_quality_engine

    result = await run_quality_engine(db, intake_id, executed_by=actor)
    return {
        "completed": True,
        "run_id": result.get("run_id"),
        "records_evaluated": result.get("records_evaluated"),
        "every_record_evaluated": result.get("every_record_evaluated"),
        "issues_generated": result.get("issues_generated"),
        "rules_executed": result.get("rules_executed"),
        "rules_failed": result.get("rules_failed") or [],
        "rule_set_version": result.get("rule_set_version"),
    }


async def _stage_curation(db, intake_id, actor):
    from app.tefca_registry.rce.curation import curate_delivery

    result = await curate_delivery(db, intake_id, curated_by=actor)
    return {
        "completed": True,
        "source_records": result.get("source_records"),
        "curated_records": result.get("curated_records"),
        "every_source_record_curated": result.get("every_source_record_curated"),
        "status_counts": result.get("status_counts") or {},
        "auto_safe_corrections_applied": result.get(
            "auto_safe_corrections_applied"),
        "transformation_version": result.get("transformation_version"),
    }


async def _stage_promotion(db, intake_id, actor):
    """Promote, or record honestly that promotion was refused.

    `promote_delivery` raises ValueError when the delivery carries schema drift,
    and that refusal is CORRECT — promoting an unreconciled schema would
    mis-assign values into the canonical registry. It is reported as a held
    stage rather than an error, because nothing went wrong: the pipeline
    declined to guess, which is what it is for.
    """
    from app.tefca_registry.rce.promotion import promote_delivery

    try:
        result = await promote_delivery(db, intake_id, actor=actor)
    except ValueError as exc:
        return {"completed": False, "held": True, "reason": str(exc)[:1000],
                "note": ("Promotion was declined, not failed. The delivery is "
                         "preserved and curated; resolve the stated condition "
                         "and promote through the existing route.")}
    return {
        "completed": True,
        "curated_records": result.get("curated_records"),
        "entities_created": result.get("entities_created"),
        "entities_updated": result.get("entities_updated"),
        "qhin_entities": result.get("qhin_entities"),
        "relationships_managed_by_qhin": result.get(
            "relationships_managed_by_qhin"),
        "relationships_sub_participant_of": result.get(
            "relationships_sub_participant_of"),
        "unresolved_parents": result.get("unresolved_parents"),
        "not_promoted_by_status": result.get("not_promoted_by_status"),
    }


async def _stage_verification(db, intake_id, actor):
    """A bounded automated verification seed. See AUTO_VERIFY_LIMIT."""
    from sqlalchemy import select

    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    refs: List[str] = list((await db.execute(
        select(m.RceCuratedRecord.rce_org_oid)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.canonical_entity_id.isnot(None))
        .order_by(m.RceCuratedRecord.rce_org_oid)
        .limit(AUTO_VERIFY_LIMIT))).scalars().all())
    if not refs:
        return {"completed": True, "requested": 0, "verified": 0,
                "note": ("Nothing was promoted, so there is nothing to verify. "
                         "This is a consequence of the stages above, not a "
                         "verification failure.")}

    result = await verify_and_classify(db, refs, intake_id=intake_id,
                                       actor=actor)
    return {
        "completed": True,
        "requested": result.get("requested"),
        "verified": result.get("verified"),
        "unresolved": result.get("unresolved"),
        "bucket_counts": result.get("bucket_counts") or {},
        "tier_counts": result.get("tier_counts") or {},
        "bounded": True,
        "limit": AUTO_VERIFY_LIMIT,
        "note": ("A bounded automated seed. The official verification volume "
                 "follows the review cycle's per-QHIN sample; this exists so a "
                 "delivery is immediately reviewable without spending shared "
                 "Government source quota across the whole population."),
    }


async def _stage_reconciliation(db, intake_id):
    from app.tefca_registry.rce.reconciliation import reconcile_delivery

    result = await reconcile_delivery(db, intake_id)
    return {
        "completed": True,
        "passed": bool(result.get("passed")),
        "populations": result.get("populations") or {},
        "curated_status_counts": result.get("curated_status_counts") or {},
        "corrections": result.get("corrections") or {},
        "rule_execution": result.get("rule_execution") or {},
    }


# ── helpers ──────────────────────────────────────────────────────────────────

def _processed_count(observed: Dict[str, Any]) -> Optional[int]:
    """The most meaningful "rows handled" number a stage produced, if any."""
    if not isinstance(observed, dict):
        return None
    for key in ("curated_records", "records_evaluated", "verified"):
        value = observed.get(key)
        if isinstance(value, int):
            return value
    return None


def _reconciled_count(recon: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(recon, dict):
        return None
    populations = recon.get("populations") or {}
    value = populations.get("D_curated_records")
    return value if isinstance(value, int) else None


def _reason(stage: str, exc: Exception) -> str:
    """A controlled failure string.

    Names the stage and the exception TYPE, and includes the message only
    because these are our own domain errors carrying operator-actionable text
    (`IntakeError`, `LineCountMismatch`, `ValueError` from promotion). It is
    truncated, and it is the only place an exception's text reaches a caller.
    """
    return f"{stage} did not complete: {type(exc).__name__}: {str(exc)[:800]}"
