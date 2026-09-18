"""
Deterministic linkage between a delivery and the reports generated from it.

ORDER OF WRITES, AND WHY
────────────────────────
    1. audit_logs row  (action=report_generated)   - the event of record
    2. rce_delivery_report_links rows              - one per stored artifact,
                                                     each pointing AT the audit row
    3. rce_delivery_stage_events REPORT_GENERATION - the job's own timeline

The audit row is written first because the link carries its id as a foreign
key: a link that named an audit event which did not exist would be exactly the
dangling reference this table exists to rule out. All writes share one
correlation id so they can be reassembled from any one of them.

ONE LINK PER ARTIFACT
─────────────────────
A report is issued in more than one rendering (HTML, CSV, PDF when the engine
is available), and each rendering is a separate content-addressed artifact with
its own hash. The link table's unique key is (report_id, artifact_id), so each
rendering gets its own row: "which file was handed to the reviewer" is then
answered by a row, not inferred from a format name. The model has no
`file_sha256`, `content_type` or `storage_backend` column and the migration is
not altered; those facts are read from `report_artifacts` through the
`artifact_id` join when a link is listed.

None of these writes may fail the generation. The analyst already has the
document; a secondary bookkeeping failure is logged loudly and reported in the
response as `delivery_link: {"written": false, ...}` rather than swallowed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

ACTION_GENERATED = "report_generated"
ACTION_DOWNLOADED = "report_downloaded"
#: A download that could NOT be served: registered bytes missing from the store,
#: an integrity failure, or a registry row nobody can find. Outcome `failure`.
ACTION_DOWNLOAD_FAILED = "report_download_failed"
EVENT_TYPE = "reporting"
RESOURCE_TYPE = "report"


async def _artifact_row_id(db, report_id: str, artifact: Optional[Dict[str, Any]]):
    """The `report_artifacts.id` behind a finalised artifact, or None.

    `finalize_artifact` returns `to_dict()`, which carries the registry row id
    as `id`; older callers may pass a dict without it, in which case the row is
    looked up by (report_id, content_type, rendered_sha256).
    """
    if not artifact or artifact.get("registered") is False:
        return None
    if artifact.get("id"):
        import uuid as _uuid

        try:
            return _uuid.UUID(str(artifact["id"]))
        except ValueError:
            pass
    try:
        from app.reports.data.artifact_registry import ReportArtifact

        stmt = select(ReportArtifact.id).where(ReportArtifact.report_id == report_id)
        if artifact.get("rendered_sha256"):
            stmt = stmt.where(ReportArtifact.rendered_sha256 == artifact["rendered_sha256"])
        if artifact.get("content_type"):
            stmt = stmt.where(ReportArtifact.content_type == artifact["content_type"])
        return (await db.execute(stmt.order_by(ReportArtifact.artifact_version.desc())
                                 .limit(1))).scalar()
    except Exception as exc:  # noqa: BLE001
        logger.info("artifact row id not resolved for %s: %s", report_id, exc)
        return None


async def record_report_generation(
    db, *, report_id: str, report_type: str, dataset: Dict[str, Any],
    template_version: str, generated_by: str, generated_by_id=None,
    artifact: Optional[Dict[str, Any]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    storage: Optional[Dict[str, Any]] = None,
    review_cycle_id: Optional[str] = None,
    scope_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Audit row, then one link per artifact, then stage event. Never raises.

    `artifacts` is the list of registry rows finalised for this report (HTML,
    CSV, PDF). `artifact` is the older single-HTML form and is folded into the
    list. With no registered artifact at all, one link row with a null
    `artifact_id` is still written so the report is findable from the job.
    `storage` carries `storage_backend` / `durable` / `pdf_unavailable_reason`
    from the finalisation step and is echoed on the summary and in the audit
    details.

    `scope_type` is `"DELIVERY"` (this report names one delivery's review
    cycle) or `"GLOBAL"` (the documented all-records default — no delivery
    identifier at all). The caller states it explicitly rather than this
    function inferring it from which fields happen to be present, so an
    audit reader never has to reverse-engineer scope from a null check.
    `review_cycle_id` is echoed for a DELIVERY-scoped report so the audit
    trail names the exact sample the report described, not only the
    delivery.

    THE ONE AUDIT EVENT FOR EVERY REPORT, NOT ONLY RCE TYPES: before
    2026-09-18 this was called only for report_type in RCE_TYPES
    (data_quality, intake, delivery_processing) — a delivery-scoped
    `verification` report (the type PR #76 itself delivery-scopes) produced
    NO audit_logs row at all. `job_id`/`intake_id`/`snapshot_id` are already
    optional here (see the early-return below when any is missing) — a
    GLOBAL report and a DELIVERY-scoped report with no persisted snapshot
    both already fall into that path safely; nothing about this function's
    existing failure policy needed to change to serve both.

    NOT IDEMPOTENT, BY DESIGN, MATCHING EVERY OTHER CALLER: each call is a
    real, distinct report generation and gets its own report_id and its own
    audit row — the same "insert, never update" contract the module
    docstring states for every write here. A client-side retry of one
    logical request is a caller concern (an idempotency key on the request,
    if ever needed); this function does not silently coalesce two calls
    into one row, because it cannot tell a legitimate second generation
    from a retry, and guessing wrongly in either direction is worse than a
    caller owning that decision.
    """
    from app.core import request_context
    from app.models.database import AuditLog
    from app.reports.data.artifact_registry import link_artifact_summary
    from app.tefca_registry.rce import traceability_models as tm

    registered = [a for a in (artifacts or []) if a and a.get("registered") is not False]
    if artifact and artifact.get("registered") is not False and \
            not any(a.get("id") == artifact.get("id") and a.get("content_type") ==
                    artifact.get("content_type") for a in registered):
        registered.insert(0, artifact)
    storage = dict(storage or {})

    delivery = dataset.get("delivery") or {}
    job_id = delivery.get("job_id")
    intake_id = delivery.get("intake_id")
    snapshot_id = dataset.get("snapshot_id")
    build_sha = request_context.build_sha()
    correlation_id = request_context.correlation_id()[:64]
    # Scope/actor/delivery/review-cycle facts only - never the dataset, HTML
    # or CSV content, and never PHI beyond the identifiers already named
    # elsewhere in the audit trail (job_id/intake_id are opaque UUIDs, not
    # patient or provider data).
    details = {
        "job_id": job_id, "intake_id": intake_id, "snapshot_id": snapshot_id,
        "review_cycle_id": review_cycle_id,
        "scope_type": scope_type or ("DELIVERY" if (job_id or intake_id) else "GLOBAL"),
        "template_version": template_version, "build_sha": build_sha,
        "report_type": report_type, "actor": generated_by,
        "artifacts": [{"id": a.get("id"), "content_type": a.get("content_type"),
                       "rendered_sha256": a.get("rendered_sha256"),
                       "size_bytes": a.get("size_bytes")} for a in registered],
        "storage_backend": storage.get("storage_backend"),
        "durable": storage.get("durable"),
        "pdf_unavailable_reason": storage.get("pdf_unavailable_reason"),
    }
    out: Dict[str, Any] = {"written": False, "audit_id": None, "link_id": None,
                           "link_ids": [], "stage_event_id": None, "job_id": job_id,
                           "intake_id": intake_id, "snapshot_id": snapshot_id,
                           "artifact_id": None, "artifact_ids": [],
                           "artifacts": [link_artifact_summary(a) for a in registered],
                           "storage_backend": storage.get("storage_backend"),
                           "durable": storage.get("durable"),
                           "storage_note": storage.get("storage_note"),
                           "pdf_unavailable_reason": storage.get("pdf_unavailable_reason"),
                           "correlation_id": correlation_id,
                           "reason": None}

    # 1. the event of record
    try:
        audit = AuditLog(
            user_id=generated_by_id, action=ACTION_GENERATED, event_type=EVENT_TYPE,
            outcome="success", resource_type=RESOURCE_TYPE, resource_id=report_id,
            details=details, correlation_id=correlation_id)
        db.add(audit)
        await db.flush()
        out["audit_id"] = str(audit.id)
    except Exception as exc:  # noqa: BLE001
        logger.error("report %s: audit row (report_generated) FAILED: %s", report_id, exc)
        await _rollback(db)
        out["reason"] = f"audit write failed: {type(exc).__name__}"
        return out

    # 2. the links (need a job, an intake and a snapshot: the table requires all three)
    if not (job_id and intake_id and snapshot_id):
        missing = [k for k, v in (("job_id", job_id), ("intake_id", intake_id),
                                  ("snapshot_id", snapshot_id)) if not v]
        out["reason"] = (f"no rce_delivery_report_links row: the delivery has no "
                         f"{', '.join(missing)}; the audit row stands alone")
        try:
            await db.commit()
            out["written"] = True
        except Exception as exc:  # noqa: BLE001
            logger.error("report %s: audit commit FAILED: %s", report_id, exc)
            await _rollback(db)
            out["reason"] = f"audit commit failed: {type(exc).__name__}"
        return out
    try:
        row_ids = []
        for a in registered:
            row_ids.append(await _artifact_row_id(db, report_id, a))
        row_ids = [r for r in row_ids if r is not None] or [None]
        for artifact_row_id in row_ids:
            link = tm.RceDeliveryReportLink(
                job_id=job_id, intake_id=intake_id, snapshot_id=snapshot_id,
                report_id=report_id, report_type=report_type,
                artifact_id=artifact_row_id,
                template_version=str(template_version)[:32],
                generation_audit_id=audit.id, generated_by=(generated_by or "SYSTEM")[:320],
                build_sha=build_sha, correlation_id=correlation_id)
            db.add(link)
            await db.flush()
            out["link_ids"].append(str(link.id))
            if link.artifact_id:
                out["artifact_ids"].append(str(link.artifact_id))
        out["link_id"] = out["link_ids"][0] if out["link_ids"] else None
        out["artifact_id"] = out["artifact_ids"][0] if out["artifact_ids"] else None
        await db.commit()
        out["written"] = True
    except Exception as exc:  # noqa: BLE001
        logger.error("report %s: rce_delivery_report_links write FAILED: %s", report_id, exc)
        await _rollback(db)
        out["reason"] = f"link write failed: {type(exc).__name__}"
        out["link_ids"], out["artifact_ids"] = [], []
        out["link_id"] = out["artifact_id"] = None
        return out

    # 3. the job's own timeline
    try:
        from app.tefca_registry.rce import stage_events

        event = await stage_events.record_instant(
            db, job_id, "REPORT_GENERATION", intake_id=intake_id,
            detail={"report_id": report_id, "report_type": report_type,
                    "snapshot_id": snapshot_id, "template_version": template_version,
                    "audit_id": out["audit_id"], "link_id": out["link_id"],
                    "link_ids": out["link_ids"], "artifact_ids": out["artifact_ids"],
                    "storage_backend": storage.get("storage_backend")},
            commit=True)
        out["stage_event_id"] = str(event.id)
    except Exception as exc:  # noqa: BLE001
        logger.error("report %s: REPORT_GENERATION stage event FAILED: %s", report_id, exc)
        await _rollback(db)
        out["reason"] = f"stage event write failed: {type(exc).__name__}"
    return out


async def record_report_download(db, *, report_id: str, report_type: Optional[str],
                                 fmt: str, actor: str, actor_id=None,
                                 extra: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """One `report_downloaded` audit row. Never raises; returns the audit id."""
    return await _download_event(db, ACTION_DOWNLOADED, "success", report_id=report_id,
                                 report_type=report_type, fmt=fmt, actor=actor,
                                 actor_id=actor_id, extra=extra)


async def record_report_download_failure(
        db, *, report_id: str, report_type: Optional[str], fmt: str, actor: str,
        actor_id=None, code: str, reason: str,
        extra: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """One `report_download_failed` audit row (outcome=failure). Never raises.

    `code` is the machine token the HTTP response carries (ARTIFACT_MISSING,
    ARTIFACT_INTEGRITY_FAILURE, ARTIFACT_NOT_REGISTERED) so the audit trail and
    the client agree on what happened. `reason` is a short controlled sentence,
    never an exception dump and never a storage locator.
    """
    return await _download_event(db, ACTION_DOWNLOAD_FAILED, "failure",
                                 report_id=report_id, report_type=report_type,
                                 fmt=fmt, actor=actor, actor_id=actor_id,
                                 extra={"code": code, "reason": reason, **(extra or {})})


async def _download_event(db, action: str, outcome: str, *, report_id, report_type,
                          fmt, actor, actor_id, extra) -> Optional[str]:
    from app.core import request_context
    from app.models.database import AuditLog

    try:
        row = AuditLog(
            user_id=actor_id, action=action, event_type=EVENT_TYPE,
            outcome=outcome, resource_type=RESOURCE_TYPE, resource_id=report_id,
            details={"format": fmt, "report_type": report_type, "actor": actor,
                     "build_sha": request_context.build_sha(), **(extra or {})},
            correlation_id=request_context.correlation_id()[:64])
        db.add(row)
        await db.commit()
        return str(row.id)
    except Exception as exc:  # noqa: BLE001
        logger.error("report %s: audit row (%s, %s) FAILED: %s",
                     report_id, action, fmt, exc)
        await _rollback(db)
        return None


# ── listings: link rows joined to the registry ───────────────────────────────

def _link_with_artifact(link, artifact) -> Dict[str, Any]:
    """One link row with the registry facts the model does not store.

    `file_sha256`, `content_type`, `size_bytes`, `storage_backend` and the
    verified download URL come from `report_artifacts`; a link whose artifact
    was never registered reports them as None and `durable: false`.
    """
    from app.reports.data.artifact_registry import (link_artifact_summary,
                                                    storage_durability)

    out = link.to_dict()
    if artifact is not None:
        summary = link_artifact_summary(artifact.to_dict())
        out.update({
            "file_sha256": summary["rendered_sha256"],
            "content_type": summary["content_type"],
            "size_bytes": summary["size_bytes"],
            "artifact_version": summary["artifact_version"],
            "storage_backend": summary["storage_backend"],
            "durable": summary["durable"],
            "storage_note": summary["storage_note"],
            "download_url": summary["download_url"],
            "artifact": summary,
        })
    else:
        out.update({"file_sha256": None, "content_type": None, "size_bytes": None,
                    "artifact_version": None,
                    **{k: (False if k == "durable" else None)
                       for k in ("storage_backend", "durable", "storage_note")},
                    "download_url": None, "artifact": None})
        out["storage_note"] = "No artifact registered for this link."
    return out


def _joined_stmt():
    from app.reports.data.artifact_registry import ReportArtifact
    from app.tefca_registry.rce import traceability_models as tm

    L = tm.RceDeliveryReportLink
    return L, (select(L, ReportArtifact)
               .outerjoin(ReportArtifact, ReportArtifact.id == L.artifact_id))


def _format_order():
    """Within one generation every link shares `generated_at` (one transaction,
    one `now()`), so the order inside a report must come from something
    stable: content type descending puts text/html before text/csv before
    application/pdf, and an unregistered link (no artifact) last."""
    from app.reports.data.artifact_registry import ReportArtifact

    return ReportArtifact.content_type.desc().nulls_last()


async def links_for_report(db, report_id: str) -> List[Dict[str, Any]]:
    L, stmt = _joined_stmt()
    rows = (await db.execute(
        stmt.where(L.report_id == report_id)
        .order_by(L.generated_at, _format_order(), L.id))).all()
    return [_link_with_artifact(link, artifact) for link, artifact in rows]


async def links_for_job(db, job_id) -> List[Dict[str, Any]]:
    L, stmt = _joined_stmt()
    rows = (await db.execute(
        stmt.where(L.job_id == job_id)
        .order_by(L.generated_at.desc(), L.report_id.desc(), _format_order(), L.id))).all()
    return [_link_with_artifact(link, artifact) for link, artifact in rows]


def group_links_by_report(links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Links for one job, folded to one entry per report with its artifacts."""
    reports: Dict[str, Dict[str, Any]] = {}
    for link in links:
        entry = reports.setdefault(link["report_id"], {
            "report_id": link["report_id"], "report_type": link["report_type"],
            "snapshot_id": link["snapshot_id"], "intake_id": link["intake_id"],
            "generated_at": link["generated_at"], "generated_by": link["generated_by"],
            "generation_audit_id": link["generation_audit_id"],
            "correlation_id": link["correlation_id"], "build_sha": link["build_sha"],
            "template_version": link["template_version"],
            "detail_url": f"/api/reports/{link['report_id']}",
            "artifacts": [],
        })
        if link.get("artifact"):
            entry["artifacts"].append(link["artifact"])
    return list(reports.values())


async def _rollback(db) -> None:
    try:
        await db.rollback()
    except Exception:  # noqa: BLE001
        pass
