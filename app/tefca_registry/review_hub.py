"""Review Hub — one read-only overview of the upload and review surfaces.

WHAT THIS IS
────────────
`GET /api/tefca/hub/overview` answers, in one call, the questions the hub page
(`/tefca-arc/insights`) opens with: what may I do here, what has been uploaded,
where is each run, and what does the persisted evidence say about one
delivery's readiness for a report.

IT IS A READ. It registers nothing, enqueues nothing, recomputes nothing that
is then stored, and writes no audit row. Opening the page must never start
processing; `tests/test_review_hub_2026_11_13.py` holds that with a statement
counter on the session.

IT DOES NOT ENFORCE ANYTHING, AND IT DOES NOT GRANT ANYTHING
───────────────────────────────────────────────────────────
Every act keeps the gate it already has, in the route that performs it:
registering an official delivery, staging and approving an IQVIA snapshot,
applying case updates, analyst determinations, QA and report release. The
`capabilities` list REPORTS those floors so the page can say "requires Program
Manager" instead of offering a button that will be refused. The floors are
read from the routes' own constants where they exist, and a test compares each
one with the dependency on the real route, so this list cannot drift into a
second, different answer.

FIELD-LEVEL AUTHORIZATION, SAME RULE AS THE JOB DETAIL ENDPOINT
───────────────────────────────────────────────────────────────
The route floor is viewer, because the module shell replaces a whole page on
any 403 (PermissionBoundary) and a hub that 403s is a hub nobody below the
floor can read. Blocks that need more say so in `availability` and are `null`:
IQVIA inventory needs the reviewer floor and the licensed-source flag, exactly
as `iqvia_routes` requires.

READINESS IS EVIDENCE, NOT A GATE
─────────────────────────────────
The readiness block restates stored rows: the job, the latest reconciliation
snapshot, the two-axis status the delivery list already derives, and the
verified report links. Each item carries the time of the row it came from, or
UNKNOWN when no row exists, or UNVERIFIED when this read cannot establish it.
It never says a report may be released: release is decided by the existing
human gates, which this module neither evaluates nor replaces.

AI ASSISTANCE
─────────────
The mode is read from live configuration (`entity_resolver.resolution_mode`),
never hardcoded. Whether a provider agreement covers this data is NOT something
the application can know; the block says so and records it as an evidence gap.

WHY NOT `workspace`
───────────────────
`tefca_registry/workspace.py` (the analyst case view) and
`rce/analyst_workspace.py` already own that word.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ROLE_HIERARCHY, canonical_role, require_role, role_at_least

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tefca/hub", tags=["TEFCA Review Hub"])

HUB_VERSION = "1.0.0"
#: Carried on every response. The hub describes work in progress.
STATUS_LABEL = "DRAFT / NOT FINAL / INDEPENDENT QA PENDING"

MET = "MET"
NOT_MET = "NOT_MET"
IN_PROGRESS = "IN_PROGRESS"
UNKNOWN = "UNKNOWN"
UNVERIFIED = "UNVERIFIED"

#: How many recent deliveries the overview lists. `status_for_jobs` costs a
#: fixed number of grouped queries per page, so this bounds the read.
MAX_DELIVERIES = 10

ROLE_LABELS = {
    "viewer": "Viewer", "contributor": "Contributor", "manager": "Manager",
    "reviewer": "Reviewer", "senior_analyst": "Senior Analyst", "qalead": "QA Lead",
    "program_manager": "Program Manager", "admin": "Administrator",
}


def _iso(value) -> Optional[str]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def _item(key: str, label: str, state: str, detail: str, *, as_of=None,
          basis: str) -> Dict[str, Any]:
    return {"key": key, "label": label, "state": state, "detail": detail,
            "as_of": _iso(as_of), "basis": basis}


# ── capabilities ─────────────────────────────────────────────────────────────

def capability_floors() -> List[Dict[str, Any]]:
    """The acts the hub links to and the role floor of the route that performs
    each. Read from the routes' own constants where they export one."""
    from app.tefca_registry.rce.delivery_routes import DATA_OPERATIONS_ROLE
    from app.tefca_registry.rce.source_matching import LICENSED_ACCESS_ROLE

    return [
        {"key": "register_official_delivery",
         "label": "Register an official ONC/RCE delivery",
         "required_role": DATA_OPERATIONS_ROLE, "licensed": False,
         "page": "/tefca-arc/deliveries",
         "api": ("POST", "/api/tefca/rce/official-deliveries"),
         "note": "Delimited text (CSV, pipe or tab). Spreadsheet files are not accepted here."},
        {"key": "upload_reference_file",
         "label": "Upload an IQVIA reference file",
         "required_role": LICENSED_ACCESS_ROLE, "licensed": True,
         "page": "/tefca-arc/iqvia",
         "api": ("POST", "/api/tefca/rce/iqvia/uploads"),
         "note": "Chunked and resumable. Only the person who starts an upload can add to it or complete it."},
        {"key": "approve_reference_snapshot",
         "label": "Approve a staged IQVIA snapshot",
         "required_role": "qalead", "licensed": True,
         "page": "/tefca-arc/iqvia",
         "api": ("POST", "/api/tefca/rce/iqvia/snapshots/{snapshot_id}/approve"),
         "note": "Must be a different person from the one who registered the snapshot."},
        {"key": "preview_case_updates",
         "label": "Preview an Excel/CSV file of case updates",
         "required_role": "reviewer", "licensed": False,
         "page": "/tefca-arc/cases/import",
         "api": ("POST", "/api/tefca/arc/cases/import/preview"),
         "note": "Preview only. A spreadsheet cannot approve, release or reclassify anything."},
        {"key": "apply_case_ownership",
         "label": "Apply ownership changes from a previewed file",
         "required_role": "senior_analyst", "licensed": False,
         "page": "/tefca-arc/cases/import",
         "api": ("POST", "/api/tefca/arc/cases/import/apply"),
         "note": "Ownership only. Notes and proposed corrections are staged, never applied."},
    ]


def _capabilities(user) -> List[Dict[str, Any]]:
    from app.tefca_registry.rce.source_matching import licensed_source_enabled

    licensed_on = licensed_source_enabled()
    out = []
    for cap in capability_floors():
        role_ok = role_at_least(user, cap["required_role"])
        if not role_ok:
            allowed, reason = False, f"requires_role:{cap['required_role']}"
        elif cap["licensed"] and not licensed_on:
            allowed, reason = False, "not_configured"
        else:
            allowed, reason = True, "available"
        out.append({
            "key": cap["key"], "label": cap["label"], "allowed": allowed,
            "availability": reason, "required_role": cap["required_role"],
            "required_role_label": ROLE_LABELS.get(cap["required_role"], cap["required_role"]),
            "page": cap["page"], "note": cap["note"]})
    return out


# ── mode ─────────────────────────────────────────────────────────────────────

def _mode() -> Dict[str, Any]:
    from app.tefca_registry.entity_resolver import MODE_DISABLED, resolution_mode

    try:
        mode = resolution_mode()
    except Exception:  # noqa: BLE001 - an unreadable setting is reported, not guessed
        mode = None
    return {
        "processing": "deterministic",
        "processing_note": ("Uploads, preflight, comparison, classification and reports "
                            "are rule-based. No step on this page calls an AI service."),
        "ai_assistance": {
            "state": (UNKNOWN if mode is None
                      else "DISABLED" if mode == MODE_DISABLED else mode.upper()),
            "configured_by": "AI_ENTITY_RESOLUTION",
            "scope": "Entity-resolution suggestions only, advisory, when enabled.",
            "provider_agreement_evidence": UNVERIFIED,
            "provider_agreement_note": (
                "The application cannot establish whether a provider agreement covers "
                "this data. No executed agreement is on file with the application's "
                "records; treat this as an open evidence item, not as a finding either way."),
            "protected_identifiers": "EIN, TIN and FEIN values are never sent to an AI service.",
        },
    }


# ── deliveries ───────────────────────────────────────────────────────────────

async def _deliveries(db, limit: int):
    """(rows, items). Items carry no delivered value: filename, hash, counts,
    stage names and the two-axis status the delivery list already shows."""
    from app.tefca_registry.rce import delivery_jobs as jobs

    rows = list(await jobs.list_jobs(db, limit=limit, state=None, offset=0))
    derived: List[Optional[Dict[str, Any]]] = [None] * len(rows)
    if rows:
        try:
            derived = list(await jobs.status_for_jobs(db, rows))
        except Exception as exc:  # noqa: BLE001 - a status failure must not empty the list
            logger.info("hub: delivery status unavailable: %s", type(exc).__name__)
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
    items = []
    for row, status in zip(rows, derived):
        d = row.to_dict()
        items.append({
            "job_id": d["job_id"], "delivery_label": d.get("delivery_label"),
            "original_filename": d.get("original_filename"),
            "sha256": d.get("sha256"), "file_size_bytes": d.get("file_size_bytes"),
            "state": d.get("state"), "stage": d.get("stage"),
            "attempt_count": d.get("attempt_count"),
            "received_on": d.get("received_on"),
            "created_at": d.get("created_at"), "started_at": d.get("started_at"),
            "completed_at": d.get("completed_at"), "failed_at": d.get("failed_at"),
            "error_reason": d.get("error_reason"),
            "intake_id": d.get("intake_id"),
            "processing_outcome": (status or {}).get("processing_outcome"),
            "review_state": (status or {}).get("review_state"),
            "status_available": status is not None,
            "detail_page": f"/tefca-arc/deliveries/detail/?job={d['job_id']}",
        })
    return rows, derived, items


# ── IQVIA inventory ──────────────────────────────────────────────────────────

async def _iqvia(db, user) -> Dict[str, Any]:
    from app.core.config import settings
    from app.tefca_registry.rce import iqvia_routes
    from app.tefca_registry.rce.source_matching import licensed_access_allowed

    decision = licensed_access_allowed(user)
    if not decision["allowed"]:
        return {"availability": decision["availability"], "status": None,
                "page": "/tefca-arc/iqvia"}
    try:
        total, page = await iqvia_routes._fetch_snapshot_page(
            db, limit=iqvia_routes._SNAPSHOT_LIST_MAX_LIMIT, offset=0,
            source_system=None, include_successors=False)
        status = iqvia_routes.derive_iqvia_source_status(
            page, total=total,
            consumption_enabled=bool(getattr(settings, "ENABLE_IQVIA_AFFILIATION_CONSUMPTION", False)))
    except Exception as exc:  # noqa: BLE001
        logger.info("hub: IQVIA inventory unavailable: %s", type(exc).__name__)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"availability": "unavailable", "status": None, "page": "/tefca-arc/iqvia"}
    return {"availability": "available", "status": status, "page": "/tefca-arc/iqvia"}


# ── readiness ────────────────────────────────────────────────────────────────

async def _readiness(db, job, status: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Seven statements about ONE delivery, each from a stored row."""
    from app.tefca_registry.rce import reconciliation, status_model

    d = job.to_dict()
    items: List[Dict[str, Any]] = []

    items.append(_item(
        "original_preserved", "Original file preserved",
        MET if d.get("sha256") else UNKNOWN,
        (f"{d.get('original_filename') or 'file'}, {d.get('file_size_bytes') or 0} bytes, "
         f"SHA-256 {str(d.get('sha256') or '')[:12]}…" if d.get("sha256")
         else "No hash is recorded for this registration."),
        as_of=d.get("created_at"), basis="delivery job row"))

    state = d.get("state")
    if state == "SUCCEEDED":
        items.append(_item("processing", "Automatic processing", MET,
                           "Processing completed.", as_of=d.get("completed_at"),
                           basis="delivery job row"))
    elif state == "FAILED":
        items.append(_item("processing", "Automatic processing", NOT_MET,
                           f"Processing stopped at stage {d.get('stage') or 'unknown'}: "
                           f"{d.get('error_reason') or 'no reason recorded'}",
                           as_of=d.get("failed_at"), basis="delivery job row"))
    elif state in ("QUEUED", "RUNNING"):
        items.append(_item("processing", "Automatic processing", IN_PROGRESS,
                           f"{'Waiting to start' if state == 'QUEUED' else 'Running'}"
                           f"{', stage ' + d['stage'] if d.get('stage') else ''}.",
                           as_of=d.get("started_at") or d.get("created_at"),
                           basis="delivery job row"))
    else:
        items.append(_item("processing", "Automatic processing", UNKNOWN,
                           f"Job state {state!r} is not one this panel recognises.",
                           basis="delivery job row"))

    snapshot = None
    try:
        snapshot = await reconciliation.latest_snapshot(db, job.id)
    except Exception as exc:  # noqa: BLE001
        logger.info("hub: reconciliation snapshot unavailable: %s", type(exc).__name__)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        items.append(_item("reconciliation", "Record reconciliation", UNKNOWN,
                           "The stored reconciliation snapshot could not be read.",
                           basis="reconciliation snapshot"))
    else:
        if snapshot is None:
            items.append(_item("reconciliation", "Record reconciliation", UNKNOWN,
                               "No reconciliation snapshot is stored for this delivery.",
                               basis="reconciliation snapshot"))
        elif getattr(snapshot, "reconstructed", False):
            items.append(_item("reconciliation", "Record reconciliation", UNVERIFIED,
                               f"Snapshot {snapshot.sequence} was reconstructed after the run; "
                               f"it reports {'a pass' if snapshot.passed else 'a failure'} for "
                               f"{snapshot.received} records received.",
                               as_of=snapshot.created_at, basis="reconciliation snapshot"))
        elif snapshot.passed:
            items.append(_item("reconciliation", "Record reconciliation", MET,
                               f"Snapshot {snapshot.sequence}: every one of {snapshot.received} "
                               f"records received is accounted for.",
                               as_of=snapshot.created_at, basis="reconciliation snapshot"))
        else:
            items.append(_item("reconciliation", "Record reconciliation", NOT_MET,
                               f"Snapshot {snapshot.sequence} did not reconcile: "
                               f"{snapshot.failure_reason or 'no reason recorded'}",
                               as_of=snapshot.created_at, basis="reconciliation snapshot"))

    outcome = (status or {}).get("processing_outcome")
    if not outcome:
        items.append(_item("exceptions", "Processing exceptions", UNKNOWN,
                           "The processing outcome could not be derived for this read.",
                           basis="status model"))
    else:
        clean = outcome.get("value") == status_model.OUTCOME_CLEAN
        pending = outcome.get("value") in (status_model.OUTCOME_QUEUED,
                                           status_model.OUTCOME_PROCESSING)
        items.append(_item("exceptions", "Processing exceptions",
                           MET if clean else IN_PROGRESS if pending else NOT_MET,
                           f"{outcome.get('value')}"
                           f"{': ' + str(outcome['detail']) if outcome.get('detail') else ''}",
                           basis="status model"))

    review = (status or {}).get("review_state")
    if not review:
        items.append(_item("human_review", "Analyst and QA review", UNKNOWN,
                           "The review state could not be derived for this read.",
                           basis="status model"))
    else:
        done = review.get("value") in (status_model.REVIEW_QA_APPROVED,
                                       status_model.REVIEW_CLOSED)
        items.append(_item("human_review", "Analyst and QA review",
                           MET if done else NOT_MET,
                           f"{review.get('value')}"
                           f"{': ' + str(review['detail']) if review.get('detail') else ''}",
                           basis="status model"))

    try:
        from app.reports.data.delivery_report_links import links_for_job

        links = await links_for_job(db, job.id)
    except Exception as exc:  # noqa: BLE001
        logger.info("hub: report links unavailable: %s", type(exc).__name__)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        items.append(_item("report_generated", "Report generated", UNKNOWN,
                           "The report links for this delivery could not be read.",
                           basis="verified report links"))
    else:
        report_ids = sorted({link["report_id"] for link in links})
        latest = max((link.get("generated_at") for link in links if link.get("generated_at")),
                     default=None)
        if report_ids:
            items.append(_item("report_generated", "Report generated", MET,
                               f"{len(report_ids)} report(s) linked to this delivery: "
                               f"{', '.join(report_ids[:5])}"
                               f"{' …' if len(report_ids) > 5 else ''}. "
                               "A generated report is a draft until it is released.",
                               as_of=latest, basis="verified report links"))
        else:
            items.append(_item("report_generated", "Report generated", NOT_MET,
                               "No report is linked to this delivery.",
                               basis="verified report links"))

    items.append(_item(
        "release", "Independent QA and report release", UNVERIFIED,
        "Not evaluated here. Release is decided by the QA and Program Manager gates on "
        "the report itself; this panel neither checks nor replaces them.",
        basis="not read by this panel"))

    counts: Dict[str, int] = {}
    for it in items:
        counts[it["state"]] = counts.get(it["state"], 0) + 1
    return {
        "job_id": d["job_id"], "delivery_label": d.get("delivery_label"),
        "items": items, "counts": counts,
        "statement": ("Restates stored evidence for one delivery. It is not an approval "
                      "and does not permit a report to be released."),
        "detail_page": f"/tefca-arc/deliveries/detail/?job={d['job_id']}",
        "reports_page": "/tefca-arc/reports",
    }


# ── the route ────────────────────────────────────────────────────────────────

@router.get("/overview", summary="Review Hub overview (read-only)")
async def overview(
    response: Response,
    job_id: Optional[str] = Query(
        None, description="Delivery job to show readiness for. Default: the most recent."),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Reads only. Never registers, enqueues, recomputes-and-stores or audits."""
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce.delivery_routes import _as_uuid

    response.headers["Cache-Control"] = "no-store"
    rows, derived, items = await _deliveries(db, MAX_DELIVERIES)

    selected, selected_status = None, None
    if job_id is not None:
        wanted = _as_uuid(job_id)
        if wanted is None:
            raise HTTPException(404, f"No delivery job {job_id}")
        for row, status in zip(rows, derived):
            if row.id == wanted:
                selected, selected_status = row, status
                break
        if selected is None:
            selected = await jobs.get_job(db, wanted)
            if selected is None:
                raise HTTPException(404, f"No delivery job {job_id}")
            try:
                selected_status = (await jobs.status_for_jobs(db, [selected]))[0]
            except Exception:  # noqa: BLE001
                selected_status = None
    elif rows:
        selected, selected_status = rows[0], derived[0]

    readiness = await _readiness(db, selected, selected_status) if selected is not None else None
    role = canonical_role(getattr(user, "role", None))
    return {
        "hub_version": HUB_VERSION,
        "status_label": STATUS_LABEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "caller": {"role": role if role in ROLE_HIERARCHY else None,
                   "role_label": ROLE_LABELS.get(role)},
        "mode": _mode(),
        "capabilities": _capabilities(user),
        "deliveries": {"items": items, "listed": len(items), "limit": MAX_DELIVERIES,
                       "page": "/tefca-arc/deliveries"},
        "iqvia": await _iqvia(db, user),
        "readiness": readiness,
        "readiness_note": (None if readiness is not None
                           else "No delivery has been registered, so there is nothing to assess."),
    }
