"""The operational view of one official ONC/RCE delivery.

WHAT THIS IS FOR
────────────────
Data Operations registers a delivery and then needs one question answered
repeatedly: where is it, and can review start. Answering that today means
calling six endpoints — the intake, its integrity, its runs, its issues, its
curated statuses and its reconciliation — and knowing how to combine them. This
assembles that once, server-side, so the page polls ONE thing.

EVERY NUMBER HERE IS MEASURED, NONE IS INVENTED
───────────────────────────────────────────────
The counts come from `reconcile_delivery`, which recomputes every population
from the rows themselves, and from `curated_status_counts`, which is the
delivery's own status vocabulary — CLEAN, CORRECTED, HELD, REJECTED. This module
maps those onto the operational words the dashboard shows and says so
explicitly in `classification_basis`; it does not define a new status, a new
severity or a new bucket. If a delivery ever carries a status this mapping does
not know, it appears under `other` with its real name rather than being folded
into a bucket where it does not belong.

`unexplained` is `corrections.unexplained` from reconciliation — corrections
with no recorded authority. It is displayed because zero is the only acceptable
value and a number that is only ever zero is worth showing precisely so that a
non-zero one is impossible to miss.

WHY IT READS THE JOB AND THE DELIVERY SEPARATELY
────────────────────────────────────────────────
A delivery ingested through the pre-existing synchronous route has no job row,
and that is not an error. The job supplies stage and progress WHILE processing;
Area 1 and reconciliation supply the truth AFTERWARDS. So a finished delivery
reports identically whether or not a job ever existed, and a delivery with no
job simply has no live progress to show.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Delivery record_status -> the operational word the dashboard shows.
#:
#: These are presentation labels over the EXISTING vocabulary, not a second
#: vocabulary. The real status travels beside each count so nothing is lost.
STATUS_PRESENTATION = {
    "CLEAN": "ready",
    "CORRECTED": "warnings",
    "HELD": "held",
    "REJECTED": "excluded",
}

#: What a caller may do next, and the condition for each. Returned as data so
#: the UI does not re-implement the policy in JavaScript.
ACTION_VIEW_EXCEPTIONS = "VIEW_EXCEPTIONS"
ACTION_CREATE_REVIEW_CYCLE = "CREATE_REVIEW_CYCLE"


async def delivery_dashboard(db, intake_id) -> Dict[str, Any]:
    """One delivery, assembled for the operational screen."""
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import repository as repo

    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        return {}

    job = await jobs.job_for_intake(db, intake.id)
    recon = await _reconciliation(db, intake.id)
    integrity = await _integrity(db, intake.id)

    populations = (recon or {}).get("populations") or {}
    status_counts = (recon or {}).get("curated_status_counts") or {}
    corrections = (recon or {}).get("corrections") or {}

    received = populations.get("A_source_records_received")
    if received is None:
        received = await repo.count_source_records(db, intake.id)
    accounted = populations.get("D_curated_records") or 0

    classified, other = _classify(status_counts)

    stages = _stages(job, recon, integrity)
    status = _status(job, recon)

    return {
        "intake_id": str(intake.id),
        "delivery_label": intake.delivery_label,
        "original_filename": intake.original_filename,
        "sha256": intake.sha256,
        "received_at": intake.received_at,
        "received_by": intake.received_by,
        "record_count_declared": intake.record_count,
        "duplicate_content": intake.duplicate_content,
        "duplicate_of_intake_id": (str(intake.duplicate_of_intake_id)
                                   if intake.duplicate_of_intake_id else None),
        "schema_drift": bool((intake.source_metadata or {}).get("schema_drift")),
        "government_reference": (job.government_reference if job else None),
        "notes": (job.notes if job else None),

        "status": status,
        "job": job.to_dict() if job else None,

        "counts": {
            "records_received": received,
            "records_accounted": accounted,
            "records_processed": (job.records_processed if job else None),
            "records_remaining": _remaining(received, job, status),
            **classified,
            "other_statuses": other,
            "unexplained": corrections.get("unexplained"),
            "corrections_total": corrections.get("total"),
        },
        "classification_basis": (
            "Counts are the delivery's own record_status values as recomputed "
            "by reconciliation. 'ready' is CLEAN, 'warnings' is CORRECTED, "
            "'held' is HELD, 'excluded' is REJECTED. Any other status is "
            "reported under other_statuses under its real name."),

        "stages": stages,
        "reconciliation": {
            "run": bool(recon),
            "passed": (recon or {}).get("passed"),
            "populations": populations,
            "rule_execution": (recon or {}).get("rule_execution") or {},
        },
        "integrity": integrity,
        "actions": _actions(status, recon, status_counts),
    }


# ── assembly ─────────────────────────────────────────────────────────────────

def _classify(status_counts: Dict[str, int]):
    """Split the delivery's statuses into the presentation words, keeping the rest.

    An unmapped status is NOT dropped and NOT folded into a neighbour. It is
    returned under its own name, because a status this code has never seen is
    exactly the thing an operator must be told about rather than have averaged
    away.
    """
    out = {"ready": 0, "warnings": 0, "held": 0, "excluded": 0}
    other: Dict[str, int] = {}
    for status, count in (status_counts or {}).items():
        key = STATUS_PRESENTATION.get(status)
        if key:
            out[key] += int(count)
        else:
            other[status] = int(count)
    return out, other


def _remaining(received, job, status) -> Optional[int]:
    """Rows still to process, or None when the question does not apply.

    Only meaningful WHILE processing. A finished delivery has no remainder, and
    reporting one as zero next to a failed run would read as success.
    """
    if status.get("state") != "PROCESSING":
        return None
    if not isinstance(received, int) or job is None:
        return None
    processed = job.records_processed
    if not isinstance(processed, int):
        return None
    return max(received - processed, 0)


def _status(job, recon) -> Dict[str, Any]:
    """The single word the screen leads with, plus why it says that.

    READY_FOR_REVIEW requires reconciliation to have PASSED, not merely to have
    run. A delivery whose populations do not close is not ready for review
    however cleanly its stages completed — that is the entire purpose of the
    gate, and a dashboard that said READY over a failed gate would defeat it.
    """
    if job is not None and job.state in ("QUEUED", "RUNNING"):
        return {"state": "PROCESSING", "stage": job.stage,
                "detail": "The delivery is being processed."}
    if job is not None and job.state == "FAILED":
        return {"state": "FAILED", "stage": job.stage,
                "detail": job.error_reason or "Processing did not complete."}
    if recon is None:
        return {"state": "NOT_RECONCILED", "stage": None,
                "detail": ("The delivery exists in Area 1 but reconciliation "
                           "has not been run, so readiness is unknown.")}
    if recon.get("passed"):
        return {"state": "READY_FOR_REVIEW", "stage": "READY_FOR_REVIEW",
                "detail": ("Reconciliation passed: the A-F populations close "
                           "exactly. A review cycle may be created.")}
    return {"state": "RECONCILIATION_FAILED", "stage": "RECONCILIATION",
            "detail": ("The A-F populations do not close. Review must not "
                       "start from a population that cannot be accounted for.")}


def _stages(job, recon, integrity) -> Dict[str, Dict[str, Any]]:
    """Per-stage state for the checklist the operator reads down the page.

    Four values only: VERIFIED/PASS/COMPLETE for done, RUNNING for in flight,
    PENDING for not yet reached, and HELD or FAILED where the stage said so.
    Nothing here is inferred from elapsed time.
    """
    detail = (job.stage_detail or {}) if job else {}
    order = ["PARSING", "QUALITY", "CURATION", "PROMOTION", "VERIFICATION",
             "RECONCILIATION"]
    current = job.stage if job else None
    running = bool(job and job.state == "RUNNING")

    out: Dict[str, Dict[str, Any]] = {
        "INTEGRITY": {
            "state": ("VERIFIED" if integrity.get("raw_lines_intact")
                      else ("FAILED" if integrity.get("checked")
                            else "PENDING")),
            "note": integrity.get("verdict"),
        },
    }
    for name in order:
        observed = detail.get(name)
        if isinstance(observed, dict) and observed.get("completed"):
            state = "COMPLETE"
        elif isinstance(observed, dict) and observed.get("held"):
            state = "HELD"
        elif isinstance(observed, dict) and observed.get("error"):
            state = "FAILED"
        elif running and name == current:
            state = "RUNNING"
        elif job is None:
            # No job ran. Reconciliation is the only stage whose completion can
            # still be established from storage, so it is the only one that may
            # claim anything.
            state = "COMPLETE" if (name == "RECONCILIATION" and recon) else "UNKNOWN"
        else:
            state = "PENDING"
        entry: Dict[str, Any] = {"state": state}
        if isinstance(observed, dict):
            entry.update({k: v for k, v in observed.items()
                          if k not in ("completed",)})
        out[name] = entry

    if recon:
        out["RECONCILIATION"]["state"] = "PASS" if recon.get("passed") else "FAIL"
    return out


def _actions(status, recon, status_counts) -> Dict[str, Dict[str, Any]]:
    """What the operator may do from here, and why not when not.

    Returned as data with an explicit reason for every refusal. A disabled
    button with no explanation is the thing that generates a support call.
    """
    ready = status.get("state") == "READY_FOR_REVIEW"
    return {
        ACTION_VIEW_EXCEPTIONS: {
            "available": True,
            "reason": None,
            "note": ("The Issue Ledger is readable at any point, including "
                     "while processing and after a failure."),
        },
        ACTION_CREATE_REVIEW_CYCLE: {
            "available": ready,
            "reason": (None if ready else status.get("detail")),
            "note": ("A review cycle draws the official per-QHIN sample from "
                     "this delivery's promoted population."),
        },
    }


# ── the two reads that may legitimately be absent ────────────────────────────

async def _reconciliation(db, intake_id) -> Optional[Dict[str, Any]]:
    """Reconciliation, or None if it cannot be computed.

    Never raises to the caller. A dashboard that 500s because one of its six
    panels could not be built is less useful than one that renders five and
    says the sixth is unavailable.
    """
    from app.tefca_registry.rce.reconciliation import reconcile_delivery

    try:
        return await reconcile_delivery(db, intake_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("dashboard reconciliation unavailable for %s: %s",
                    intake_id, exc)
        return None


async def _integrity(db, intake_id) -> Dict[str, Any]:
    """The Area 1 integrity verdict, in the same shape the existing route uses."""
    from app.tefca_registry.rce import repository as repo

    try:
        hashes = await repo.verify_record_hashes(db, intake_id)
        stored = await repo.verify_stored_file(db, intake_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("dashboard integrity unavailable for %s: %s", intake_id, exc)
        return {"checked": False, "verdict": "Integrity could not be verified."}
    intact = bool(hashes.get("intact")) and stored.get("intact") is not False
    return {
        "checked": True,
        "records_checked": hashes.get("records_checked"),
        "record_hash_mismatches": hashes.get("mismatches"),
        "raw_lines_intact": hashes.get("intact"),
        "original_file_intact": stored.get("intact"),
        "verdict": (
            "Area 1 is intact: every stored raw line still hashes to the value "
            "recorded at intake, and the preserved original file is unchanged."
            if intact
            else "Area 1 integrity check FAILED — see the mismatch counts."),
    }
