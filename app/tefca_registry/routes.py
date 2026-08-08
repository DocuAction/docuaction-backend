"""
TEFCA registry API (Phase 2A) — read/query + verification.

Mounted at ``/api/tefca/registry/*``, separate from the legacy ``/api/tefca/*``
and ``/api/v1/tefca/*`` routers. Router floor is ``require_role("viewer")``; each
endpoint declares its own requirement above that (see the note by the router).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.input_sanitize import reject_null_bytes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.tefca_registry import models as reg
from app.tefca_registry import queries as q
from app.tefca_registry import verification as v
from app.tefca_registry import lifecycle
from app.tefca_registry import audit as reg_audit
from app.tefca_registry.schemas import (BulkVerifyRequest, StatusChangeRequest,
                                        VerifyOptions)
from app.core.client_ip import get_client_ip

logger = logging.getLogger(__name__)

# ── Access control ────────────────────────────────────────────────────────────
# The router gate is the FLOOR (authenticated + at least viewer); each endpoint
# declares its own requirement, and the stricter of the two binds.
#
# This reverses an earlier decision. The gate was require_role("reviewer"), which
# sat above every handler's own declaration and made those declarations dead
# code — a contributor could not reach a handler marked contributor, and a viewer
# could not read. The Block 4 RBAC matrix (2026-08-02) recorded that as intended.
# The August 2026 QA matrix records it as a defect (QA-1.8) and requires reads at
# viewer and import/verification at contributor, so the floor moves to viewer and
# the per-endpoint declarations become load-bearing.
#
# WHAT THIS OPENS: every GET on this router is now reachable by a viewer, where
# it previously required reviewer. Writes are unchanged or stricter — imports and
# single-entity verification move reviewer -> contributor by design, and status
# change moves contributor -> reviewer so it matches the matrix rather than
# silently relying on the old floor.
#
# Adding an endpoint here without an explicit require_role gives it VIEWER
# access. Declare the role on every new write endpoint.
router = APIRouter(
    prefix="/api/tefca/registry",
    tags=["TEFCA Registry"],
    dependencies=[Depends(require_role("viewer"))],
)


# ── reads ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    return await q.stats(db)


@router.get("/entities")
async def list_entities(
    entity_level: Optional[str] = None,
    entity_type: Optional[str] = None,
    state: Optional[str] = None,
    verification_status: Optional[str] = None,
    operational_status: Optional[str] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = Query(None, alias="q"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await q.list_entities(
        db, entity_level=entity_level, entity_type=entity_type, state=state,
        verification_status=verification_status, operational_status=operational_status,
        is_active=is_active, q=search, limit=limit, offset=offset)


@router.get("/qhins")
async def list_qhins(db: AsyncSession = Depends(get_db)):
    return {"items": await q.list_qhins(db)}


@router.get("/participants")
async def list_participants(
    qhin_id: Optional[uuid.UUID] = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await q.list_participants(db, qhin_id=qhin_id, limit=limit, offset=offset)


@router.get("/hierarchy")
async def hierarchy_roots(db: AsyncSession = Depends(get_db)):
    """Lazy tree root: QHINs with participant counts."""
    return {"roots": await q.hierarchy_roots(db)}


@router.get("/search")
async def search(q_: str = Query(..., alias="q", min_length=1),
                 limit: int = Query(25, ge=1, le=100),
                 db: AsyncSession = Depends(get_db)):
    # Postgres cannot compare a NUL byte inside text, so an unescaped \x00 here
    # raises at the driver and surfaces as a 500. Reject it as the validation
    # failure it is (422) rather than reporting bad input as a server fault.
    reject_null_bytes(q_, "search query")
    return await q.search(db, q_, limit=limit)


@router.get("/findings")
async def list_findings(
    finding_type: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await q.list_findings(
        db, finding_type=finding_type, severity=severity, status=status,
        entity_id=entity_id, limit=limit, offset=offset)


@router.get("/verification-jobs")
async def list_jobs(limit: int = Query(100, ge=1, le=500),
                    db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from app.tefca_registry import models as reg
    rows = (await db.execute(
        select(reg.TefcaVerificationJob)
        .order_by(reg.TefcaVerificationJob.completed_at.desc().nullslast())
        .limit(limit))).scalars().all()
    return {"items": [{
        "id": j.id, "entity_id": j.entity_id, "status": j.status,
        "trigger_type": j.trigger_type, "started_at": j.started_at,
        "completed_at": j.completed_at, "duration_ms": j.duration_ms,
        "summary": j.summary,
    } for j in rows]}


@router.get("/verification-jobs/{job_id}")
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from app.tefca_registry import models as reg
    job = await db.get(reg.TefcaVerificationJob, job_id)
    if not job:
        raise HTTPException(404, "Verification job not found")
    checks = (await db.execute(
        select(reg.TefcaVerificationCheck)
        .where(reg.TefcaVerificationCheck.job_id == job_id))).scalars().all()
    return {
        "id": job.id, "entity_id": job.entity_id, "status": job.status,
        "trigger_type": job.trigger_type, "started_at": job.started_at,
        "completed_at": job.completed_at, "duration_ms": job.duration_ms,
        "summary": job.summary,
        "checks": [{
            "id": c.id, "source": c.source, "result": c.result,
            "identifier_type": c.identifier_type, "discrepancies": c.discrepancies,
            "checked_at": c.checked_at,
        } for c in checks],
    }


# NOTE: keep the parameterized /entities/{id} routes AFTER the static ones above
# so /entities/... literals aren't shadowed.

@router.get("/entities/{entity_id}")
async def get_entity(entity_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    detail = await q.get_entity_detail(db, entity_id)
    if not detail:
        raise HTTPException(404, "Entity not found")
    return detail


@router.get("/entities/{entity_id}/children")
async def get_children(entity_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return {"children": await q.get_children(db, entity_id)}


@router.get("/entities/{entity_id}/hierarchy")
async def get_subtree(entity_id: uuid.UUID,
                      max_depth: int = Query(3, ge=1, le=6),
                      db: AsyncSession = Depends(get_db)):
    tree = await q.get_subtree(db, entity_id, max_depth=max_depth)
    if not tree:
        raise HTTPException(404, "Entity not found")
    return tree


@router.get("/entities/{entity_id}/findings")
async def entity_findings(entity_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await q.list_findings(db, entity_id=entity_id)


# ── verification (writes) ─────────────────────────────────────────────────────

@router.post("/entities/{entity_id}/verify")
async def verify_entity(entity_id: uuid.UUID,
                        request: Request,
                        opts: VerifyOptions = VerifyOptions(),
                        db: AsyncSession = Depends(get_db),
                        user=Depends(require_role("contributor"))):
    """Run verification, score confidence, and advance the lifecycle.

    Every step past the internal checks is best-effort. A connector being down
    must not fail the request or lose the work already done, so external checks
    are scored over the sources that ANSWERED and an unreachable source is
    recorded as unavailable rather than as a mismatch — otherwise an NPPES
    outage would be indistinguishable from an entity NPPES has never heard of.
    """
    entity = await db.get(reg.TefcaRegEntity, entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")

    ip = get_client_ip(request)
    actor_id, actor_email = reg_audit.actor_of(user)

    # Internal checks (identity, hierarchy) — commits internally.
    internal = await v.verify_one(
        db, entity_id, include_external=opts.include_external,
        trigger_type=opts.trigger_type,
        actor_id=actor_id, actor_email=actor_email)

    # The reviewable half: five-state source probes, B1-B4 classification, a
    # stable review id and a per-source audit row. Shared with the priority
    # review so both produce identical records — three call sites building
    # review records three slightly different ways is how audit trails develop
    # holes.
    from app.tefca_registry.review_service import run_review
    review = await run_review(db, entity, user=user, ip_address=ip,
                              trigger=opts.trigger_type)

    # draft -> pending_verification is the only automatic move. Promotion to
    # active stays a human decision; evidence informs that call, it is not the
    # call itself.
    entity = await db.get(reg.TefcaRegEntity, entity_id)
    transition = None
    if entity.operational_status == lifecycle.sm.DRAFT:
        try:
            transition = lifecycle.apply_transition(
                db, entity, lifecycle.sm.PENDING_VERIFICATION, user=user,
                ip_address=ip, extra={"trigger": "verification",
                                      "review_id": review["review_id"]})
            await db.commit()
        except lifecycle.TransitionRefused as exc:
            await db.commit()
            transition = {"allowed": False, "reason": exc.message}

    return {**review, "findings": internal, "transition": transition,
            "operational_status": entity.operational_status}


async def _probe_sources(db, entity_id) -> dict:
    """Ask each authoritative source about this entity's NPI.

    Returns {source_key: True|False|None} for lifecycle.compute_confidence:
    None means "did not answer" and is excluded from the score. Never raises —
    a verification that returns partial results is far more useful than one that
    500s because a third-party API had a bad minute.
    """
    from sqlalchemy import select as _select
    npi = (await db.execute(
        _select(reg.TefcaEntityIdentifier.identifier_value).where(
            reg.TefcaEntityIdentifier.entity_id == entity_id,
            reg.TefcaEntityIdentifier.identifier_type == "npi",
        ).limit(1))).scalar_one_or_none()

    # No NPI is a fact about the record, not a source outage: nothing can
    # corroborate an identity that was never supplied.
    if not npi:
        return {k: None for k in lifecycle.SOURCE_WEIGHTS}

    results: dict = {k: None for k in lifecycle.SOURCE_WEIGHTS}
    try:
        from app.Tefca.connectors import SourceConnectorManager
        mgr = SourceConnectorManager()
    except Exception as exc:  # pragma: no cover - connectors optional
        logger.warning("TEFCA connectors unavailable for %s: %s", entity_id, exc)
        return results

    # Each connector exposes lookup_by_npi and returns a SourceResult. SAM.gov is
    # keyed on UEI rather than NPI, so it is not probed here — the registry has
    # no UEI for these entities, and asking with the wrong identifier would
    # produce a confident "no match" that means nothing. It stays unavailable,
    # which shrinks the divisor honestly.
    probes = {
        "nppes": getattr(mgr, "nppes", None),
        "pecos": getattr(mgr, "pecos", None),
        "oig_leie": getattr(mgr, "leie", None),
    }
    for key, connector in probes.items():
        fn = getattr(connector, "lookup_by_npi", None) if connector else None
        if fn is None:
            continue
        try:
            r = await fn(npi)
            # Distinguish the two failure modes deliberately. A source that
            # answered "no record" is a real no-match and must count against the
            # score. A source that errored has told us nothing, and scoring that
            # as a mismatch would turn an outage into an accusation.
            if getattr(r, "error", None):
                results[key] = None
            else:
                results[key] = bool(getattr(r, "success", False))
        except Exception as exc:  # noqa: BLE001 - one source must not sink the run
            logger.info("TEFCA source %s unavailable for %s: %s", key, entity_id, exc)
            results[key] = None

    # OIG LEIE is an EXCLUSION list: a hit is bad news, absence is the good
    # outcome. Inverted here so "matched" means the same thing (corroborates the
    # entity) across every source feeding the weighted score.
    if results.get("oig_leie") is not None:
        results["oig_leie"] = not results["oig_leie"]
    return results


@router.patch("/entities/{entity_id}/status")
async def change_entity_status(entity_id: uuid.UUID,
                               req: StatusChangeRequest,
                               request: Request,
                               db: AsyncSession = Depends(get_db),
                               # reviewer, not contributor: a lifecycle/status
                               # change is the "resolve" action the QA matrix
                               # puts at reviewer. Declared contributor before,
                               # but the old router floor made it reviewer in
                               # practice — this keeps the behaviour it actually
                               # had once the floor moved to viewer.
                               user=Depends(require_role("reviewer"))):
    """Move an entity through its lifecycle, subject to the state machine.

    The registry previously stored operational_status as a free string, so
    draft -> active (skipping verification) and inactive -> active (resurrecting
    a deregistered entity) both succeeded silently. Both are refused here, and
    the refusal is audited — an attempt to skip verification is exactly what a
    reviewer wants to see.
    """
    entity = await db.get(reg.TefcaRegEntity, entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")

    target = (req.status or "").strip().lower()
    if not lifecycle.sm.is_valid_state(target):
        raise HTTPException(
            400,
            f"Unknown status '{req.status}'. Valid: "
            f"{', '.join(sorted(lifecycle.sm.VALID_STATES))}.")

    try:
        result = lifecycle.apply_transition(
            db, entity, target, user=user,
            ip_address=get_client_ip(request),
            extra={"reason": req.reason} if req.reason else None)
    except lifecycle.TransitionRefused as exc:
        # Commit so the refusal is recorded even though the change is rejected.
        await db.commit()
        raise HTTPException(400, exc.message)

    await db.commit()
    return {**result, "reason": req.reason,
            "allowed_next": sorted(lifecycle.sm.allowed_targets(target))}


@router.delete("/entities/{entity_id}")
async def soft_delete_entity(entity_id: uuid.UUID,
                             request: Request,
                             reason: str = Query(
                                 "", description="Why this entity is being removed. "
                                                 "Recorded in the audit log."),
                             db: AsyncSession = Depends(get_db),
                             user=Depends(require_role("admin"))):
    """Soft-delete an entity. Admin only.

    SOFT, not hard. review_records, tefca_verifications and sample_entities all
    reference an entity; removing the row would orphan the evidence behind a
    classification that may already have been reported to ONC. The row stays,
    flagged, and drops out of listings, stats and the sample frame.

    This exists because there was previously no way to remove anything. That
    made a large-volume import benchmark impossible to run honestly: 1,000 test
    entities would permanently contaminate every subsequent sample draw and
    weekly report, so the benchmark was reported as Not Executed instead.

    Deliberately NOT idempotent-silent: deleting an already-deleted entity
    returns 409 rather than 200. A cleanup script that reports success for rows
    it did not touch hides a targeting bug.
    """
    entity = await db.get(reg.TefcaRegEntity, entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")
    if entity.is_deleted:
        raise HTTPException(409, "Entity is already deleted")

    entity.is_deleted = True
    entity.is_active = False
    entity.deleted_at = datetime.utcnow()

    reg_audit.record(db, entity_id=entity_id, action="entity_deleted",
                     actor_id=getattr(user, "id", None),
                     actor_email=getattr(user, "email", None),
                     metadata={"reason": reason or None,
                               "name": entity.name,
                               "soft_delete": True},
                     ip_address=get_client_ip(request))
    await db.commit()
    return {"id": str(entity_id), "name": entity.name, "is_deleted": True,
            "deleted_at": entity.deleted_at.isoformat(),
            "reason": reason or None,
            "note": "Soft delete — the row is retained so prior classifications "
                    "and samples keep their referent."}


@router.post("/dev/seed", dependencies=[Depends(require_role("admin"))])
async def seed_dev_registry(request: Request,
                            force: bool = Query(False,
                                                description="Import even if the registry already has entities. "
                                                            "Duplicate TEFCAID/HCID are skipped, so this is idempotent."),
                            include_real: bool = Query(
                                False,
                                description="Also load 5 real, publicly-listed hospital NPIs so the "
                                            "verification and B1 classification paths can be demonstrated. "
                                            "DEV ONLY — refused when ENVIRONMENT=production."),
                            db: AsyncSession = Depends(get_db),
                            user=Depends(require_role("admin"))):
    """Load synthetic demo entities through the real CSV import path.

    Admin-only and refuses a populated registry unless forced. Runs the ordinary
    importer rather than inserting rows, so seeding exercises the same parser,
    NPI validation and audit writes a real import does — a seed that bypassed
    that path could pass while the path itself was broken.

    Two of the seeded NPIs fail the CMS check digit deliberately, so the
    flag-don't-reject behaviour has something to flag.
    """
    import os
    from app.tefca_registry import dev_seed

    # Hard stop on production. Demo entities in the production registry would
    # contaminate the population every sample and report is drawn from, and a
    # contaminated denominator is not correctable after the fact. Production
    # imports ONC-provided data only.
    if (os.getenv("ENVIRONMENT", "") or "").strip().lower() == "production":
        raise HTTPException(
            403,
            "Seeding is disabled on production. This registry is the population "
            "that samples and reports are drawn from; demo entities would corrupt "
            "every downstream figure. Import ONC-provided data instead.")

    actor_id, actor_email = reg_audit.actor_of(user)
    return await dev_seed.seed(db, force=force, include_real=include_real,
                               actor_id=actor_id, actor_email=actor_email,
                               ip_address=get_client_ip(request))


@router.post("/verify")
async def verify_bulk(req: BulkVerifyRequest = BulkVerifyRequest(),
                      db: AsyncSession = Depends(get_db),
                      user=Depends(require_role("senior_analyst"))):
    from sqlalchemy import select
    from app.tefca_registry import models as reg
    stmt = select(reg.TefcaRegEntity.id)
    if req.entity_level:
        stmt = stmt.where(reg.TefcaRegEntity.entity_level == req.entity_level)
    ids = (await db.execute(stmt.limit(req.limit))).scalars().all()
    return await v.verify_entities(
        db, ids, include_external=req.include_external, trigger_type=req.trigger_type,
        actor_id=getattr(user, "id", None), actor_email=getattr(user, "email", None))


# ── import (uploads) ──────────────────────────────────────────────────────────
# New in the import-engine phase. Uploads are security-scanned by the existing
# platform scanner (_scan_upload_or_reject) before parsing. All four routes
# inherit the router's require_role("reviewer") gate (>= viewer for the reads).

def _ext_of(filename: str, default: str) -> str:
    name = filename or ""
    return name.rsplit(".", 1)[-1].lower() if "." in name else default


def _client_ip(request: Request):
    return get_client_ip(request)


@router.post("/import/fhir-bundle")
async def import_fhir_bundle_route(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    # contributor ("analyst"), per the QA matrix: importing a roster is data
    # entry, not adjudication. Was reviewer via the old router floor (QA-1.8).
    user=Depends(require_role("contributor")),
):
    content = await file.read()
    # Existing platform upload scanner (lazy import — avoids import-order coupling).
    from app.api.routes import _scan_upload_or_reject
    checksum = await _scan_upload_or_reject(
        db, user, request, content, file.filename, _ext_of(file.filename, "json"),
        "tefca_registry_import")
    try:
        bundle = json.loads(content.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")
    from app.tefca_registry.fhir_import import import_fhir_bundle
    try:
        return await import_fhir_bundle(
            db, bundle, filename=file.filename, file_checksum=checksum,
            file_size=len(content), actor_id=getattr(user, "id", None),
            actor_email=getattr(user, "email", None), ip_address=_client_ip(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/import/csv")
async def import_csv_route(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    # contributor ("analyst"), per the QA matrix: importing a roster is data
    # entry, not adjudication. Was reviewer via the old router floor (QA-1.8).
    user=Depends(require_role("contributor")),
):
    content = await file.read()
    from app.api.routes import _scan_upload_or_reject
    checksum = await _scan_upload_or_reject(
        db, user, request, content, file.filename, _ext_of(file.filename, "csv"),
        "tefca_registry_import")
    try:
        text = content.decode("utf-8-sig")
    except Exception:
        raise HTTPException(400, "Invalid CSV encoding (expected UTF-8)")
    from app.tefca_registry.csv_import import EmptyCSVError, import_csv
    try:
        result = await import_csv(
            db, text, filename=file.filename, file_checksum=checksum,
            file_size=len(content), actor_id=getattr(user, "id", None),
            actor_email=getattr(user, "email", None), ip_address=_client_ip(request))
    except EmptyCSVError as e:
        # QA-1.4. 422 rather than 400: the request is well-formed, the content is
        # not processable. Distinct from a parse failure so the operator is told
        # the file was empty rather than that it was malformed.
        raise HTTPException(422, str(e))

    # A batch import can legitimately succeed in part, so the body carries the
    # per-row detail either way. But a flat 200 on a batch where rows failed is
    # invisible to status-code monitoring — the failure only shows up to a caller
    # who parses the body (AGT-SA-001 F-001).
    #
    # The distinction matters and is not cosmetic:
    #   422 — nothing imported. The submission was unprocessable as a whole, which
    #         is the case the finding reproduced: HTTP 200 with status "failed".
    #   207 — some rows landed, some did not. A 4xx here would be wrong; the
    #         successful rows really were created and the caller must not retry
    #         the whole file blindly. "Mixed outcome, read the body."
    #   200 — everything imported.
    summary = result or {}
    if summary.get("error_count"):
        status = 422 if not summary.get("imported_count") else 207
        return JSONResponse(status_code=status, content=jsonable_encoder(summary))
    return result


def _batch_summary(b: reg.TefcaImportBatch) -> dict:
    # QA-1.6 — file_checksum and imported_by were stored but omitted here, so the
    # history LIST could not answer "which file, and who". They were reachable
    # only by opening each batch individually, which is not how an auditor reads
    # a history page. Both are on the row already; this was a projection gap.
    return {
        "id": b.id, "source_type": b.source_type, "filename": b.filename,
        "file_checksum": b.file_checksum, "file_size_bytes": b.file_size_bytes,
        "imported_by": b.imported_by,
        "status": b.status, "total_records": b.total_records,
        "imported_count": b.imported_count, "skipped_count": b.skipped_count,
        "error_count": b.error_count, "duration_ms": b.duration_ms,
        "started_at": b.started_at, "completed_at": b.completed_at,
        "created_at": b.created_at,
    }


@router.get("/import/history")
async def import_history(limit: int = Query(50, ge=1, le=500),
                         db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(reg.TefcaImportBatch)
        .order_by(reg.TefcaImportBatch.created_at.desc()).limit(limit))).scalars().all()
    return {"items": [_batch_summary(b) for b in rows]}


@router.get("/import/{batch_id}")
async def import_detail(batch_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    b = await db.get(reg.TefcaImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "Import batch not found")
    d = _batch_summary(b)
    d.update({"file_checksum": b.file_checksum, "file_size_bytes": b.file_size_bytes,
              "updated_count": b.updated_count, "errors": b.errors})
    return d
