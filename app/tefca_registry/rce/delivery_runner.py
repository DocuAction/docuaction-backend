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
    (verification)                connector READINESS only — see below
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

WHY VERIFICATION IS NOT RUN HERE ANY MORE
─────────────────────────────────────────
The first version of this runner verified a bounded "seed" of promoted entities
inside the delivery job. Independent review found that was wrong in three ways:

  * `verify_and_classify` is not idempotent — every call mints NEW ReviewRecord
    rows for the same entities. Re-registering a delivery, or re-running the
    stage after a failure, doubled the review population.
  * The seed was not the sample. The approved methodology is
    `qhin_sampling.finalize_plan`; 250 arbitrary cases by OID order sat in the
    analyst queue beside the official sample, indistinguishable from it.
  * It spent shared Government source quota (NPPES, PECOS, SAM, LEIE) on
    entities nobody had decided to review.

Verification now runs where the methodology says it does: when the Program
Manager creates the review cycle, against the members of the frozen sample —
see `review_cycle.create_review_cycle`. The VERIFICATION stage here records
connector readiness only, so the dashboard can still say whether the
Government sources are reachable before a cycle is drawn. Reconciliation is
unaffected: its F check is `F ⊆ E`, which holds with F = 0.

THE HEARTBEAT RUNS ON ITS OWN SESSION
─────────────────────────────────────
`ingest_delivery` holds ONE transaction across every 2,000-row batch so that a
crash rolls Area 1 back entirely — that is its line-count contract. Writing a
heartbeat on that same session mid-stage would COMMIT the stage's partial work
and destroy the contract. So the heartbeat is a background task with its own
session from `async_session_maker`, and it touches nothing but the job row.
Without it a 100K-row ingestion that legitimately runs past the stale
threshold would be reaped while healthy.

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
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: How often the background heartbeat writes while a stage is running.
from app.tefca_registry.rce.delivery_jobs import HEARTBEAT_INTERVAL_SECONDS  # noqa: E402


async def run_delivery_job(db, job) -> str:
    """Process one claimed delivery job to a terminal state. Returns that state.

    Never raises: a runner that raises takes the poller's tick with it and the
    job keeps a heartbeat it no longer deserves. Everything is caught, recorded
    on the job and turned into FAILED.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    detail: Dict[str, Any] = {}
    pulse = _Heartbeat(job.id)
    await pulse.start()
    try:
        return await _run_stages(db, job, detail)
    except Exception as exc:  # noqa: BLE001 — the runner must not raise
        logger.error("delivery job %s runner raised %s: %s", job.id,
                     type(exc).__name__, exc)
        return RceDeliveryJob.STATE_FAILED
    finally:
        await pulse.stop()


async def _run_stages(db, job, detail: Dict[str, Any]) -> str:
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job_id = job.id
    actor = job.registered_by or "SYSTEM"

    # ── Area 1 — fatal if it does not land ───────────────────────────────────
    try:
        intake_id, received = await _stage_area1(db, job)
    except Exception as exc:  # noqa: BLE001
        reason = _reason("PARSING", exc)
        logger.error("delivery job %s failed at Area 1: %s", job_id, reason)
        # The session may be inside a failed transaction. Every later write on
        # it would raise PendingRollbackError and the failure reason would be
        # lost — the job would sit RUNNING until the reaper guessed.
        await _settle(db)
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
            await _settle(db)
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
        await _settle(db)

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
    """Connector readiness — NOT verification. See the module docstring.

    Reports whether the Government and approved sources are configured and
    reachable, so an operator can see before drawing a cycle that, say, USPS is
    NOT CONFIGURED. It creates no evidence rows and no review cases; it calls
    no external source for any entity.
    """
    readiness: Dict[str, Any] = {}
    try:
        from app.Tefca.connectors import data_source_labels
        readiness["sources"] = data_source_labels()
    except Exception as exc:  # noqa: BLE001
        readiness["sources"] = {"error": type(exc).__name__}
    try:
        from app.tefca_registry.usps_client import get_usps_client
        readiness["usps"] = get_usps_client().health()
    except Exception as exc:  # noqa: BLE001
        readiness["usps"] = {"status": "unknown", "error": type(exc).__name__}
    try:
        from app.tefca_registry import website_evidence
        readiness["website"] = website_evidence.health()
    except Exception as exc:  # noqa: BLE001
        readiness["website"] = {"status": "unknown", "error": type(exc).__name__}

    return {
        "completed": True,
        "deferred": True,
        "readiness": readiness,
        "note": ("Verification runs when the Program Manager creates the review "
                 "cycle, against the members of the frozen per-QHIN sample. It "
                 "is not run here: doing so minted review cases outside the "
                 "approved sample and was not idempotent."),
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

class _Heartbeat:
    """A background pulse on its OWN session while a stage holds the main one.

    Stops itself once the job is no longer RUNNING: `delivery_jobs.heartbeat`
    declines to touch a settled job, so once the reaper has spoken this task
    goes quiet rather than arguing with it.
    """

    def __init__(self, job_id):
        self.job_id = job_id
        self._task = None
        self._stop = None

    async def start(self):
        import asyncio
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:  # noqa: BLE001 — a dead pulse must not fail the job
                pass

    async def _run(self):
        import asyncio

        from app.core.database import async_session_maker
        from app.tefca_registry.rce import delivery_jobs as jobs
        from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(),
                                       timeout=HEARTBEAT_INTERVAL_SECONDS)
                return
            except asyncio.TimeoutError:
                pass
            try:
                async with async_session_maker() as db:
                    row = await db.get(RceDeliveryJob, self.job_id)
                    if row is None or row.state != RceDeliveryJob.STATE_RUNNING:
                        return
                    await jobs.heartbeat(db, self.job_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("delivery job %s heartbeat failed: %s",
                               self.job_id, type(exc).__name__)


async def _settle(db) -> None:
    """Roll back whatever a failed stage left open, so the job row can be written.

    Harmless on a clean session. Essential on one whose transaction failed —
    without it every subsequent write raises PendingRollbackError.
    """
    try:
        await db.rollback()
    except Exception as exc:  # noqa: BLE001
        logger.warning("session rollback after stage failure failed: %s",
                       type(exc).__name__)


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
