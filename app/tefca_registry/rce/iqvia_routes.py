"""IQVIA Release-1 application journey: select source -> stage -> validate ->
reconcile/match -> review -> publish.

Wraps `iqvia_import.py`/`iqvia_match.py`/`source_matching.py` with an HTTP
layer; invents no new business logic of its own. Every route is gated by
`licensed_source_enabled()`/`licensed_access_allowed()` (unchanged, existing
functions) before anything else runs.

UPLOAD MECHANISM: CHUNKED, RESUMABLE, NEVER THE WHOLE FILE IN MEMORY
----------------------------------------------------------------------
The real HCP_ADDR extract is ~3.8GB; a single-request multipart upload of a
file that size is not realistic. `POST /uploads` registers an upload and
returns a `chunk_size`-based plan; the browser reads the file with
`Blob.slice()` (a view, not a copy) and `PUT`s each chunk independently to
`/uploads/{upload_id}/chunks/{index}`. Each chunk request holds only ONE
chunk's bytes in memory, server-side and browser-side, written directly to
its byte OFFSET in a server-local temp file -- the whole file is never
buffered anywhere at once, on either side. `GET /uploads/{upload_id}` reports
exactly which chunk indices are still missing, so an interrupted transfer
resumes by re-sending only those -- never a full restart. `POST .../complete`
verifies every chunk arrived, then hands the assembled file to the SAME
registration/enqueue path a server-local path would have used -- no second
import code path.

DURABLE, DATABASE-BACKED STATE (2026-10-03 -- replaces the earlier in-memory
design)
----------------------------------------------------------------------------
Every fact a resume or a restart needs now lives in the database, not this
process's memory:

  `iqvia_upload_session` / `iqvia_upload_chunk` (see `iqvia_upload_models.py`)
      which chunks have actually arrived, for ANY worker process to answer a
      `GET /uploads/{id}` -- a restart loses nothing; a retried chunk is
      `ON CONFLICT DO NOTHING`, never a duplicate.
  `iqvia_import_job` (same module)
      the staging/import work itself is a durable job, claimed by
      `iqvia_import_scheduler.py`'s poller via `SELECT ... FOR UPDATE SKIP
      LOCKED` -- not a request-scoped `BackgroundTasks` call that dies with
      the process that happened to receive the `/complete` request. A
      partial unique index makes at-most-one-active-import-per-snapshot a
      database guarantee, not a code promise. A worker that dies mid-import
      is recovered by the scheduler's reaper (stale heartbeat -> requeue,
      bounded by `IqviaImportJob.MAX_ATTEMPTS`, then FAILED for a human).

The underlying importer (`iqvia_import._import_csv`) was ALREADY correctly
resumable at the row level (`ON CONFLICT DO NOTHING` keyed by
`(source_snapshot_id, source_record_key)`) -- calling it again with the same
`snapshot_id` only inserts whatever rows are still missing. The gap this
closes is that nothing called it again after a process restart; now the
scheduler does, automatically, via the durable job queue above.

`approve_snapshot` still refuses (409) a snapshot whose import has not
completed and reconciled -- unchanged, and now additionally true across a
restart: an import that was mid-run when the process died stays
`import_status: "staging"` (or gets requeued, still staging) until the
scheduler's resumed run actually finishes, so a partial import can never be
approved regardless of how many restarts happened along the way.

BACKGROUND EXECUTION
-------------------------
No longer FastAPI `BackgroundTasks` -- see above. `iqvia_import_scheduler.py`
(poll every 5s, reap stale jobs every 60s) is the durable equivalent of
`export_scheduler.py`, following the same pattern deliberately.

PROGRESS AND REJECTED-ROW REPORTING
---------------------------------------
`SourceSnapshot.metadata_` (JSONB, already exists) carries a `progress` key
written periodically during import (rows_read/rows_staged/rows_rejected so
far) via `iqvia_import`'s own `progress_cb` hook, and a terminal
`import_summary` key on completion -- no new table, no raw row content,
ever.
"""
from __future__ import annotations

import logging
import math
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import request_context
from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_role
from app.core.upload_security import safe_existing_path
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import iqvia_match as im
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from app.tefca_registry.rce import iqvia_upload_models as um
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce import source_matching as sx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tefca/rce/iqvia", tags=["IQVIA Release 1"])

_SOURCE_SYSTEM = {
    "hco": sm.SOURCE_IQVIA_HCO,
    "hcp": sm.SOURCE_IQVIA_HCP,
    "affiliation": sm.SOURCE_IQVIA_AFFILIATION,
}
_IMPORTER = {
    "hco": ii.import_hco_csv,
    "hcp": ii.import_hcp_csv,
    "affiliation": ii.import_affiliation_csv,
}

_DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024  # 8MB
_UPLOAD_DIR = Path(tempfile.gettempdir()) / "docuaction-iqvia-uploads"


def _require_licensed_access(user) -> None:
    """One gate, called first by every route below. Raises the SAME
    availability envelope shape the delivery routes already use (never a
    bare 403 with no explanation), without inventing a new access model."""
    decision = sx.licensed_access_allowed(user)
    if not decision["allowed"]:
        raise HTTPException(403, detail={
            "error": decision["reason"], "availability": decision["availability"],
        })


def _require_known_source(source: str) -> str:
    if source not in _SOURCE_SYSTEM:
        raise HTTPException(422, f"source must be one of {sorted(_SOURCE_SYSTEM)}; got {source!r}")
    return source


async def _audit(db: AsyncSession, user, *, action: str, outcome: str,
                 resource_type: str, resource_id, details: Optional[Dict[str, Any]] = None,
                 commit: bool = True) -> None:
    """One row in the platform's canonical audit trail (`audit_logs`) per
    journey act -- the SAME mechanism `reports/routes.py` uses for report
    release decisions, not a new table. `event_type="data_import"` is the
    Audit Trail UI's existing filter bucket for ingestion. `outcome` records
    refusals as first-class facts (`rejected`/`blocked`), because a maker/
    checker refusal or an affiliation-unavailable refusal is an auditable
    event in its own right, not an absence of one. Never raises: an audit
    write failing must not turn a successful act into a 500, so it logs and
    moves on (the same best-effort posture the bulletin audit uses)."""
    from app.models.database import AuditLog

    try:
        db.add(AuditLog(
            user_id=getattr(user, "id", None),
            action=action,
            event_type="data_import",
            outcome=outcome,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            details={
                "actor": getattr(user, "email", None) or "UNKNOWN",
                "actor_role": str(getattr(user, "role", "") or ""),
                "request_id": request_context.get("request_id"),
                **(details or {}),
            },
            correlation_id=request_context.correlation_id(),
        ))
        if commit:
            await db.commit()
    except Exception:  # noqa: BLE001 -- audit must never mask the act's own outcome
        logger.exception("IQVIA audit write failed for %s (%s)", action, outcome)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass


class StageRequest(BaseModel):
    file_path: str = Field(..., description=(
        "Server-local path to the delivered CSV (operator-placed; see module "
        "docstring for why this is not a browser upload for a file this size)."))
    label: str = Field(..., max_length=200)


class ApproveRequest(BaseModel):
    approval_ref: str = Field(..., max_length=120)


async def _register_and_enqueue(
    db: AsyncSession, *, source: str, file_path: str, label: str, created_by: str,
    upload_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """Register a PENDING snapshot synchronously (so the caller has an id to
    poll immediately), then enqueue a durable `IqviaImportJob` -- never a
    request-scoped background task. One registration/enqueue path shared by
    both the server-local `/sources/{source}/stage` route and the chunked
    `/uploads/{id}/complete` route, so there is exactly one way an IQVIA
    import ever gets started.

    `file_path` is CLIENT-SUPPLIED on the stage route (reviewer role, not
    admin — see `stage_source`): resolved and confined to the configured
    IQVIA drop directory or the server's own chunked-upload temp directory,
    never opened outside either (CodeQL py/path-injection)."""
    path = safe_existing_path(file_path, settings.IQVIA_IMPORT_DIR, _UPLOAD_DIR)

    file_sha = ii.file_sha256(path)
    # Duplicate submission: the SAME bytes under the SAME label for the SAME
    # source is already registered once (`uq_source_snapshot_identity`, a
    # partial unique index on original registrations). Before this check a
    # second submission reached the index and surfaced as an unhandled 500;
    # worse, with a different label it would have silently created a second
    # usable snapshot of identical content. Refuse explicitly and point at
    # the existing registration -- never a 500, never a silent duplicate.
    existing = (await db.execute(
        select(sm.SourceSnapshot)
        .where(sm.SourceSnapshot.source_system == _SOURCE_SYSTEM[source],
               sm.SourceSnapshot.sha256 == file_sha,
               sm.SourceSnapshot.supersedes_snapshot_id.is_(None))
        .order_by(sm.SourceSnapshot.created_at.asc()).limit(1))).scalars().first()
    if existing is not None:
        raise HTTPException(409, detail={
            "error": (f"this exact file (sha256 {file_sha[:12]}...) is already registered "
                      f"for {source} as snapshot {existing.id} (label {existing.snapshot_label!r}, "
                      f"status {existing.status}); a second registration of identical "
                      "content is refused -- resume or review the existing snapshot instead"),
            "code": "DUPLICATE_SNAPSHOT",
            "existing_snapshot_id": str(existing.id),
            "existing_label": existing.snapshot_label,
            "existing_status": existing.status,
            "existing_import_status": (existing.metadata_ or {}).get("import_status")})

    try:
        snapshot = await sx.register_snapshot(
            db, source_system=_SOURCE_SYSTEM[source], label=label, sha256=file_sha,
            record_count=0, received_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc),
            created_by=created_by,
            metadata={"original_path": str(path), "import_status": "staging"})
    except IntegrityError:
        # Lost a race with a concurrent identical submission between the
        # pre-check above and the insert: same answer, same code.
        await db.rollback()
        raise HTTPException(409, detail={
            "error": "this exact file was registered concurrently by another request",
            "code": "DUPLICATE_SNAPSHOT"})

    job = await jobs.enqueue_import_job(
        db, snapshot_id=snapshot.id, source=source, file_path=str(path),
        label=label, created_by=created_by, upload_id=upload_id)

    return {"snapshot_id": str(snapshot.id), "source": source, "status": "PENDING",
           "import_status": "staging", "job_id": str(job.id), "job_state": job.state}


class StartUploadRequest(BaseModel):
    source: str
    label: str = Field(..., max_length=200)
    total_size: int = Field(..., gt=0, description="Exact file size in bytes.")
    chunk_size: int = Field(default=_DEFAULT_CHUNK_SIZE, gt=0, le=64 * 1024 * 1024)


@router.post("/uploads", status_code=201,
            summary="Start a chunked upload; returns the chunk plan to upload against")
async def start_upload(body: StartUploadRequest, db: AsyncSession = Depends(get_db),
                       user=Depends(require_role("reviewer"))):
    _require_licensed_access(user)
    _require_known_source(body.source)
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    total_chunks = math.ceil(body.total_size / body.chunk_size)
    upload_id = uuid.uuid4()
    temp_path = _UPLOAD_DIR / f"{upload_id}.part"
    temp_path.touch()

    session = await jobs.create_upload_session(
        db, source=body.source, label=body.label, total_size=body.total_size,
        chunk_size=body.chunk_size, total_chunks=total_chunks, temp_path=str(temp_path),
        created_by=getattr(user, "email", None) or "UNKNOWN")
    await _audit(db, user, action="IQVIA_UPLOAD_STARTED", outcome="success",
                 resource_type="iqvia_upload", resource_id=session.id,
                 details={"source": body.source, "label": body.label,
                          "total_size": body.total_size, "total_chunks": total_chunks})
    return {"upload_id": str(session.id), "total_chunks": total_chunks,
           "chunk_size": body.chunk_size}


@router.put("/uploads/{upload_id}/chunks/{index}",
           summary="Upload one chunk -- safe to retry, order-independent")
async def upload_chunk(upload_id: uuid.UUID, index: int, request: Request,
                       db: AsyncSession = Depends(get_db),
                       user=Depends(require_role("reviewer"))):
    """Each chunk writes to its own byte OFFSET in the assembled file, so
    chunks may arrive in any order and a retried chunk simply overwrites the
    same bytes -- never a duplicate, never corrupts a neighboring chunk.
    Which chunks have arrived is recorded in the database (`iqvia_upload_chunk`),
    not this process's memory, so a restart between two chunk PUTs loses
    nothing of the resume contract."""
    _require_licensed_access(user)
    session = await jobs.get_upload_session(db, upload_id)
    if session is None:
        raise HTTPException(404, detail={
            "error": f"no upload {upload_id} (unknown, or already completed)",
            "code": "UPLOAD_NOT_FOUND"})
    if not (0 <= index < session.total_chunks):
        raise HTTPException(422, f"chunk index out of range 0..{session.total_chunks-1}")
    chunk_bytes = await request.body()
    offset = index * session.chunk_size
    with open(session.temp_path, "r+b") as fh:
        fh.seek(offset)
        fh.write(chunk_bytes)
    await jobs.record_chunk_received(db, upload_id=upload_id, chunk_index=index)
    received = await jobs.received_chunk_indices(db, upload_id)
    return {"received_chunks": len(received), "total_chunks": session.total_chunks,
           "missing_chunks": session.total_chunks - len(received)}


@router.get("/uploads/{upload_id}",
           summary="Which chunks are still missing -- the resume contract")
async def upload_status(upload_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                        user=Depends(require_role("reviewer"))):
    _require_licensed_access(user)
    session = await jobs.get_upload_session(db, upload_id)
    if session is None:
        raise HTTPException(404, f"no upload {upload_id}")
    received = await jobs.received_chunk_indices(db, upload_id)
    missing = sorted(set(range(session.total_chunks)) - received)
    return {"upload_id": str(upload_id), "received_chunks": len(received),
           "total_chunks": session.total_chunks,
           "missing_chunk_indices": missing[:200],  # capped -- a status probe, not a full dump
           "missing_chunk_count": len(missing), "complete": not missing,
           "status": session.status}


@router.post("/uploads/{upload_id}/complete", status_code=202,
            summary="Finalize an upload and enqueue staging -- same path as a server-local stage")
async def complete_upload(upload_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                          user=Depends(require_role("reviewer"))):
    _require_licensed_access(user)
    session = await jobs.get_upload_session(db, upload_id)
    if session is None:
        raise HTTPException(404, f"no upload {upload_id}")
    if session.snapshot_id is not None:
        # Idempotent re-completion: the durable session row is never deleted
        # (unlike the old in-memory `_UPLOAD_STATE`, which made a second
        # `/complete` call on an already-registered upload a clean 404 by
        # construction). Found this pass, during combined continuous-
        # workflow testing: a second real call re-ran registration and hit
        # `source_snapshot`'s own identity-uniqueness constraint as an
        # unhandled 500, instead of being recognized as the same request
        # repeated. Return the already-registered result instead of
        # attempting to register a second snapshot for the same upload.
        job = await jobs.latest_job_for_snapshot(db, session.snapshot_id)
        await _audit(db, user, action="IQVIA_UPLOAD_COMPLETE_REPEATED", outcome="success",
                     resource_type="iqvia_upload", resource_id=upload_id,
                     details={"snapshot_id": str(session.snapshot_id),
                              "note": "idempotent repeat of an already-completed upload"})
        return {"snapshot_id": str(session.snapshot_id), "source": session.source,
                "status": "PENDING", "import_status": "staging",
                "job_id": str(job.id) if job else None,
                "job_state": job.state if job else None,
                "already_completed": True}
    received = await jobs.received_chunk_indices(db, upload_id)
    missing = sorted(set(range(session.total_chunks)) - received)
    if missing:
        raise HTTPException(409, detail={
            "error": f"{len(missing)} chunk(s) still missing; cannot complete",
            "code": "UPLOAD_INCOMPLETE", "missing_chunk_indices": missing[:200]})
    actual_size = Path(session.temp_path).stat().st_size
    if actual_size != session.total_size:
        raise HTTPException(409, detail={
            "error": f"assembled file is {actual_size} bytes, declared total_size was "
                     f"{session.total_size} -- refusing to stage a size mismatch",
            "code": "UPLOAD_SIZE_MISMATCH"})
    result = await _register_and_enqueue(
        db, source=session.source, file_path=session.temp_path, label=session.label,
        created_by=session.created_by, upload_id=upload_id)
    await jobs.mark_upload_complete(db, upload_id, snapshot_id=uuid.UUID(result["snapshot_id"]))
    await _audit(db, user, action="IQVIA_UPLOAD_COMPLETED", outcome="success",
                 resource_type="iqvia_snapshot", resource_id=result["snapshot_id"],
                 details={"upload_id": str(upload_id), "source": session.source,
                          "label": session.label, "job_id": result["job_id"]})
    return result


@router.post("/sources/{source}/stage", status_code=202,
            summary="Stage a delivered IQVIA extract from a server-local file")
async def stage_source(
    source: str, body: StageRequest, db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    """Registers a PENDING snapshot immediately (so the caller has an id to
    poll) and enqueues a durable import job -- see `_register_and_enqueue`.
    The snapshot stays PENDING -- nothing reads it -- until a separate
    `/approve` call."""
    _require_licensed_access(user)
    source = _require_known_source(source)
    result = await _register_and_enqueue(
        db, source=source, file_path=body.file_path, label=body.label,
        created_by=getattr(user, "email", None) or "UNKNOWN")
    await _audit(db, user, action="IQVIA_SNAPSHOT_STAGED", outcome="success",
                 resource_type="iqvia_snapshot", resource_id=result["snapshot_id"],
                 details={"source": source, "label": body.label, "job_id": result["job_id"],
                          "origin": "server_local_path"})
    return result


@router.get("/snapshots/{snapshot_id}",
           summary="Snapshot status, progress, and reconciliation")
async def snapshot_status(
    snapshot_id: uuid.UUID, db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    _require_licensed_access(user)
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(404, f"no snapshot {snapshot_id}")
    out: Dict[str, Any] = {
        "snapshot_id": str(snapshot.id), "source_system": snapshot.source_system,
        "status": snapshot.status, "label": snapshot.snapshot_label,
        "record_count": snapshot.record_count,
        "import_status": (snapshot.metadata_ or {}).get("import_status"),
        "import_summary": (snapshot.metadata_ or {}).get("import_summary"),
        "import_error": (snapshot.metadata_ or {}).get("import_error"),
        "progress": (snapshot.metadata_ or {}).get("progress"),
    }
    job = await jobs.latest_job_for_snapshot(db, snapshot_id)
    if job is not None:
        out["job"] = job.to_dict()
    if (snapshot.metadata_ or {}).get("import_status") == "completed":
        out["reconciliation"] = await ii.verify_snapshot_staged_completely(db, snapshot_id)
    # Who registered it (the maker) -- the UI states the maker/checker rule
    # against a name, and a QA lead who IS the registrant learns before
    # clicking that their approval will be refused, not after.
    out["created_by"] = snapshot.created_by
    # The append-only decision, if one exists: approval creates a NEW row
    # that supersedes this one, so a reload of the ORIGINAL id must still be
    # able to say "approved, by whom, as which successor" rather than
    # showing a stale PENDING with no way to find the APPROVED row.
    successor = (await db.execute(
        select(sm.SourceSnapshot)
        .where(sm.SourceSnapshot.supersedes_snapshot_id == snapshot.id)
        .limit(1))).scalars().first()
    out["decision"] = None if successor is None else {
        "successor_snapshot_id": str(successor.id), "status": successor.status,
        "approved_by": successor.approved_by, "approved_role": successor.approved_role,
        "approved_at": successor.approved_at.isoformat() if successor.approved_at else None,
        "approval_ref": successor.approval_ref,
    }
    # The journey's matching capability for THIS source, stated up front so
    # the UI distinguishes "supported" from "unavailable because the
    # relationship data is absent" before anyone clicks Match.
    out["matching"] = _matching_capability(snapshot.source_system)
    # Reference-snapshot preflight as recorded at import (2026-10-04): gate,
    # findings, held checks, the bound of what was checked, and whether it
    # was enforced or shadow-only. None for a snapshot imported before it
    # existed -- shown as "not evaluated", never as clear.
    out["reference_preflight"] = (snapshot.metadata_ or {}).get("reference_preflight")
    return out


def _matching_capability(source_system: str) -> Dict[str, Any]:
    if source_system == sm.SOURCE_IQVIA_HCO:
        return {"supported": True, "reason": None, "code": None}
    if source_system == sm.SOURCE_IQVIA_HCP:
        return {"supported": False, "code": "AFFILIATION_DATA_UNAVAILABLE",
                "reason": _HCP_UNAVAILABLE_REASON}
    if source_system == sm.SOURCE_IQVIA_AFFILIATION:
        return {"supported": False, "code": "AFFILIATION_DATA_UNAVAILABLE",
                "reason": _AFFIL_UNAVAILABLE_REASON}
    return {"supported": False, "code": "NOT_MATCHABLE",
            "reason": f"{source_system} is not matchable by this route"}


_HCP_UNAVAILABLE_REASON = (
    "HCP-to-organisation matching is not available: the delivered data carries no "
    "usable HCP<->HCO affiliation link (dedicated AFFIL extract absent; HCP_ADDR's own "
    "HOSP_AFFIL_* fields are unpopulated). This is a data-completeness fact, not a bug -- "
    "matching will not silently return zero results.")
_AFFIL_UNAVAILABLE_REASON = (
    "No affiliation snapshot has ever been approved with real data as of this pass; "
    "matching against it is not yet meaningful.")


@router.post("/snapshots/{snapshot_id}/approve",
            summary="QA-lead approval -- the only way a snapshot becomes usable")
async def approve(
    snapshot_id: uuid.UUID, body: ApproveRequest, db: AsyncSession = Depends(get_db),
    user=Depends(require_role("qalead")),
):
    """Thin wrapper over the existing, unmodified `approve_snapshot` --
    refuses a snapshot whose import has not completed or did not reconcile,
    so a partial/failed import can never be approved by mistake (the
    CONTROLLED PUBLICATION this journey requires: nothing short of this call
    ever makes a snapshot's rows readable, and this call itself refuses an
    incomplete one). This holds across a restart too: a snapshot whose
    import job got requeued/resumed after a worker died stays
    `import_status: "staging"` until the RESUMED run actually completes."""
    _require_licensed_access(user)
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(404, f"no snapshot {snapshot_id}")
    import_status = (snapshot.metadata_ or {}).get("import_status")
    if import_status != "completed":
        await _audit(db, user, action="IQVIA_SNAPSHOT_APPROVAL", outcome="rejected",
                     resource_type="iqvia_snapshot", resource_id=snapshot_id,
                     details={"code": "IMPORT_NOT_COMPLETE", "import_status": import_status})
        raise HTTPException(409, detail={
            "error": f"snapshot import_status is {import_status!r}, not 'completed'",
            "code": "IMPORT_NOT_COMPLETE"})
    reconciliation = await ii.verify_snapshot_staged_completely(db, snapshot_id)
    if not reconciliation["reconciles"]:
        await _audit(db, user, action="IQVIA_SNAPSHOT_APPROVAL", outcome="rejected",
                     resource_type="iqvia_snapshot", resource_id=snapshot_id,
                     details={"code": "RECONCILIATION_FAILED", "reconciliation": reconciliation})
        raise HTTPException(409, detail={
            "error": "staged row count does not reconcile with rows read minus "
                     "rows rejected -- refusing to approve a partial import",
            "code": "RECONCILIATION_FAILED", "reconciliation": reconciliation})
    try:
        approved = await sx.approve_snapshot(db, snapshot_id, user=user,
                                             approval_ref=body.approval_ref)
    except PermissionError as exc:
        # `approve_snapshot` already enforces maker/checker (the registrant
        # cannot approve their own snapshot, ADR-006 decision 4) and the role
        # floor. Surface it as the SAME structured segregation-of-duties
        # refusal the review-service QA gate uses -- a machine code the UI
        # branches on, never a stringified exception.
        is_maker = "maker/checker" in str(exc)
        code = "MAKER_CHECKER_VIOLATION" if is_maker else "APPROVAL_ROLE_REQUIRED"
        await _audit(db, user, action="IQVIA_SNAPSHOT_APPROVAL", outcome="rejected",
                     resource_type="iqvia_snapshot", resource_id=snapshot_id,
                     details={"code": code, "registrant": snapshot.created_by})
        raise HTTPException(409, detail={
            "error": (f"segregation of duties: {getattr(user, 'email', None) or 'UNKNOWN'} "
                      f"registered snapshot {snapshot_id} and may not approve it; a "
                      "different QA lead must record the approval")
                     if is_maker else str(exc),
            "code": code, "registrant": snapshot.created_by})
    except ValueError as exc:
        await _audit(db, user, action="IQVIA_SNAPSHOT_APPROVAL", outcome="rejected",
                     resource_type="iqvia_snapshot", resource_id=snapshot_id,
                     details={"code": "APPROVAL_REFUSED", "reason": str(exc)})
        raise HTTPException(409, detail={"error": str(exc), "code": "APPROVAL_REFUSED"})
    await _audit(db, user, action="IQVIA_SNAPSHOT_APPROVED", outcome="success",
                 resource_type="iqvia_snapshot", resource_id=snapshot_id,
                 details={"successor_snapshot_id": str(approved.id),
                          "approval_ref": body.approval_ref,
                          "registrant": snapshot.created_by,
                          "reconciliation": reconciliation})
    return {"snapshot_id": str(approved.id), "status": approved.status,
           "approved_by": approved.approved_by, "approved_at": approved.approved_at.isoformat(),
           "supersedes_snapshot_id": str(snapshot_id)}


@router.post("/snapshots/{snapshot_id}/match",
            summary="Run matching against the registry for an APPROVED HCO snapshot")
async def match_snapshot(
    snapshot_id: uuid.UUID, db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    """HCO only. HCP and AFFILIATION are refused outright, explicitly, with
    the reason -- never a silent no-op -- per the task's own instruction to
    keep incomplete affiliation data explicitly unavailable rather than
    quietly absent."""
    _require_licensed_access(user)
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(404, f"no snapshot {snapshot_id}")
    capability = _matching_capability(snapshot.source_system)
    if not capability["supported"]:
        # A refusal for absent relationship data is an auditable, blocked
        # act -- recorded as such, distinct from an error and from a run that
        # found nothing.
        await _audit(db, user, action="IQVIA_SNAPSHOT_MATCH", outcome="blocked",
                     resource_type="iqvia_snapshot", resource_id=snapshot_id,
                     details={"code": capability["code"],
                              "source_system": snapshot.source_system})
        if capability["code"] == "NOT_MATCHABLE":
            raise HTTPException(422, capability["reason"])
        raise HTTPException(409, detail={"error": capability["reason"],
                                         "code": capability["code"]})
    try:
        summary = await im.match_hco_snapshot(db, snapshot_id=snapshot_id,
                                              actor=getattr(user, "email", None) or "UNKNOWN")
    except ValueError as exc:
        await _audit(db, user, action="IQVIA_SNAPSHOT_MATCH", outcome="rejected",
                     resource_type="iqvia_snapshot", resource_id=snapshot_id,
                     details={"code": "MATCH_REFUSED", "reason": str(exc)})
        raise HTTPException(409, detail={"error": str(exc), "code": "MATCH_REFUSED"})
    result = summary.as_dict()
    await _audit(db, user, action="IQVIA_SNAPSHOT_MATCHED", outcome="success",
                 resource_type="iqvia_snapshot", resource_id=snapshot_id,
                 details={k: v for k, v in result.items() if k != "details"})
    return result


async def _ensure_durable_original(db, job) -> None:
    """Preserve the uploaded original in durable storage before staging, and record the locator (or an explicit
    "not preserved") on the snapshot. Idempotent: a resumed job that already holds a preserved record skips it.
    The upload runs in a worker thread with its own heartbeat so the reaper does not take a long upload for a
    dead worker."""
    import asyncio

    from starlette.concurrency import run_in_threadpool

    from app.core.database import async_session_maker
    from app.core.storage import original_blob_store as obs

    snapshot = await db.get(sm.SourceSnapshot, job.snapshot_id)
    meta = dict(snapshot.metadata_ or {})
    if (meta.get("durable_original") or {}).get("preserved"):
        return
    if not obs.configured():
        record = obs.not_preserved_record("no durable original backend is configured")
    else:
        stop = asyncio.Event()

        async def _beat():
            while not stop.is_set():
                try:
                    async with async_session_maker() as hb:
                        await jobs.heartbeat(hb, job.id, phase="preserving durable original")
                except Exception:  # noqa: BLE001 - a missed beat must not kill the upload
                    logger.warning("heartbeat during durable-original upload failed", exc_info=True)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass

        beat = asyncio.create_task(_beat())
        try:
            record = await run_in_threadpool(obs.preserve_file, Path(job.file_path), snapshot.sha256)
        finally:
            stop.set()
            await beat
    snapshot.metadata_ = {**meta, "durable_original": record}
    await db.commit()


async def run_import_job(job_id: str) -> None:
    """Runs one durable `IqviaImportJob` to completion (or failure).

    Called by `iqvia_import_scheduler.py`'s poller AFTER it has already
    claimed the job (moved it to RUNNING) -- this function does not claim,
    it executes. It opens its OWN database session, since the poller's
    claiming session has already closed by the time a multi-minute (at real
    scale, multi-hour) import runs.

    This is the durable replacement for the old `_run_import_background`:
    the importer itself (`iqvia_import._import_csv`) was already correctly
    resumable when called again with the same `snapshot_id` -- this function
    is what makes sure that call actually happens again after a restart.
    """
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        job = await jobs.get_import_job(db, uuid.UUID(job_id))
        if job is None:
            logger.error("run_import_job: no job %s", job_id)
            return
        importer = _IMPORTER[job.source]

        async def progress(summary: ii.ImportSummary) -> None:
            await jobs.heartbeat(
                db, job.id,
                phase=f"rows_read={summary.rows_read} staged={summary.rows_staged} "
                     f"rejected={summary.rows_rejected}")
            snapshot = await db.get(sm.SourceSnapshot, job.snapshot_id)
            if snapshot is not None:
                snapshot.metadata_ = {**(snapshot.metadata_ or {}), "progress": {
                    "rows_read": summary.rows_read, "rows_staged": summary.rows_staged,
                    "rows_already_staged": summary.rows_already_staged,
                    "rows_rejected": summary.rows_rejected}}
                await db.commit()

        try:
            # Durable original FIRST: when a durable backend is configured, nothing is staged from a file that
            # could not be preserved (a failure here fails/requeues the job like any other attempt failure).
            await _ensure_durable_original(db, job)
            summary = await importer(
                db, file_path=Path(job.file_path), label=job.label,
                created_by=job.created_by, snapshot_id=job.snapshot_id,
                progress_cb=progress)
            snapshot = await db.get(sm.SourceSnapshot, job.snapshot_id)
            snapshot.metadata_ = {**(snapshot.metadata_ or {}),
                                  "import_status": "completed",
                                  "import_summary": {
                                      "rows_read": summary.rows_read,
                                      "rows_staged": summary.rows_staged,
                                      "rows_already_staged": summary.rows_already_staged,
                                      "rows_rejected": summary.rows_rejected,
                                      "rejected_sample_lines": summary.rejected_sample_lines,
                                  }}
            await db.commit()
            await jobs.finish_succeeded(db, job.id)
            if job.upload_id is not None:
                await jobs.mark_upload_staged(db, job.upload_id)
        except Exception as exc:  # noqa: BLE001 -- must not crash the scheduler tick
            logger.error("IQVIA import job %s failed (snapshot=%s, attempt=%s): %s",
                        job.id, job.snapshot_id, job.attempt_count, type(exc).__name__,
                        exc_info=True)
            reason = f"{type(exc).__name__}: {str(exc)[:300]}"
            new_state = await jobs.finish_failed_or_requeue(db, job.id, reason)
            snapshot = await db.get(sm.SourceSnapshot, job.snapshot_id)
            if snapshot is not None:
                if new_state == um.IqviaImportJob.STATE_FAILED:
                    snapshot.metadata_ = {**(snapshot.metadata_ or {}),
                                          "import_status": "failed", "import_error": reason}
                    if job.upload_id is not None:
                        await jobs.mark_upload_failed(db, job.upload_id)
                else:
                    # Requeued -- still mid-import from the caller's point of
                    # view; the next poller tick resumes it (the importer's
                    # own row-level resume means this picks up where the
                    # failed attempt left off, not from byte zero).
                    snapshot.metadata_ = {**(snapshot.metadata_ or {}),
                                          "import_status": "staging",
                                          "import_error": f"attempt failed, retrying: {reason}"}
                await db.commit()
