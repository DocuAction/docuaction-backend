"""Controlled, bounded, durable rechecks (2026-10-04, Part B).

THE GAP
───────
Automated coverage (`automated_verification.py`) is first-attempt-only: an
entity counts as covered the moment it has ANY evidence row, so a source
that was UNAVAILABLE when the entity was looked up is never looked up again.
A source outage therefore became a permanent hole in that delivery's
evidence, and nothing re-evaluated records when a newer reference snapshot
landed.

WHAT A RECHECK IS -- AND IS NOT
───────────────────────────────
A recheck re-gathers evidence for a BOUNDED set of entities of ONE delivery,
for ONE source, because of ONE recorded trigger, and appends a new evidence
generation. Prior generations are never edited or deleted.

    Technical resolution triggers RE-EVALUATION, not compliance approval.

A recheck never allocates a review id, never writes a ReviewRecord, never
runs the B1-B4 classifier and never marks an entity verified. The only
entity-state change it can make is toward scrutiny: an answer that carries a
risk signal puts the entity `in_review`. Independent defects (other open
issues, other sources) are not touched.

CONTROLS
────────
    idempotent      one job per (delivery, trigger, source, trigger ref) --
                    a UNIQUE key; repeating the trigger returns the SAME job.
                    One item per (job, entity), processed at most once.
    bounded         MAX_ENTITIES_PER_JOB targets, MAX_BATCH_SIZE per call,
                    MAX_ATTEMPTS claims. What was not targeted is counted.
    maker/checker   a requested job runs only after a DIFFERENT person
                    approves it.
    stale baseline  the targets' latest evidence is hashed at request time;
                    if it has changed by the time the job starts, the job is
                    REFUSED_STALE and nothing is looked up.
    rate limits     every lookup goes through the connectors' own per-source
                    limiter; if a whole batch is still unavailable the job
                    STOPS rather than spend the rest of the quota.
    crash recovery  item state commits with the evidence it produced; a
                    heartbeat plus `reap_stale_jobs` requeue a dead worker's
                    job, and only PENDING items are ever processed.
    pinned          rule-set, field-map and policy-registry versions and the
                    trigger reference are recorded on the job.

No scheduler tick is registered for this module: a batch runs only when it
is explicitly asked for. `ENABLE_CONTROLLED_RECHECKS` (default off) gates the
routes, because a recheck spends real source quota.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select, text

from app.Tefca import source_policy as sp
from app.core import request_context
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import recheck_models as rm
from app.tefca_registry.rce.recheck_models import RceRecheckItem, RceRecheckJob

logger = logging.getLogger(__name__)

MAX_ENTITIES_PER_JOB = 2000
DEFAULT_BATCH_SIZE = 50
MAX_BATCH_SIZE = 200
STALE_HEARTBEAT_SECONDS = 900

#: Policy ids whose evidence the D1-D6 gather actually produces. IQVIA is
#: deliberately absent: its matching runs through the IQVIA match route, not
#: this evidence path.
SUPPORTED_SOURCES = (sp.NPPES_REGISTRY_API, sp.OIG_LEIE, sp.SAM_GOV, sp.CMS_PPEF,
                     sp.CMS_REVOCATION)

_RISK_DISPOSITIONS = ("REVIEW", "CONFLICT", "FAIL")
_UNANSWERED = ("UNAVAILABLE", "INSUFFICIENT_EVIDENCE")


class RecheckRefused(RuntimeError):
    """A refusal with a precise reason. Routes turn it into a 409."""


def evidence_sources_for(source_id: str) -> List[str]:
    return sorted(k for k, v in sp.EVIDENCE_SOURCE_TO_POLICY.items() if v == source_id)


def idempotency_key(intake_id, trigger_kind: str, source_id: str, trigger_ref: str) -> str:
    raw = f"{intake_id}|{trigger_kind}|{source_id}|{trigger_ref.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_TARGETS_SQL = """
    SELECT r.rce_org_oid, r.canonical_entity_id, e.id AS evidence_id, e.disposition
    FROM rce_curated_records r
    JOIN LATERAL (
        SELECT d.id, d.disposition
        FROM tefca_dimension_evidence d
        WHERE d.entity_id = CAST(r.canonical_entity_id AS TEXT)
          AND d.source = ANY(CAST(:sources AS text[]))
        ORDER BY d.created_at DESC, d.generation_timestamp DESC, d.id DESC
        LIMIT 1
    ) e ON TRUE
    WHERE r.source_intake_id = CAST(:intake_id AS uuid)
      AND r.canonical_entity_id IS NOT NULL
      AND r.rce_org_oid IS NOT NULL
    ORDER BY r.rce_org_oid
"""


async def _candidates(db, intake_id, source_id: str) -> List[Tuple[str, Any, Any, str]]:
    """(ref, entity_id, latest evidence id, latest disposition) per entity of
    the delivery that has ANY evidence from this source, de-duplicated."""
    rows = (await db.execute(text(_TARGETS_SQL), {
        "intake_id": str(intake_id), "sources": evidence_sources_for(source_id)})).all()
    seen, out = set(), []
    for ref, entity_id, evidence_id, disposition in rows:
        if entity_id in seen:
            continue
        seen.add(entity_id)
        out.append((ref, entity_id, evidence_id, disposition))
    return out


def _baseline_hash(targets: List[Tuple[str, Any, Any, str]]) -> str:
    return hashlib.sha256("|".join(
        f"{entity_id}:{evidence_id}:{disposition}"
        for _, entity_id, evidence_id, disposition in sorted(targets, key=lambda t: str(t[1]))
    ).encode("utf-8")).hexdigest()


async def _validate_trigger(db, trigger_kind: str, source_id: str, trigger_ref: str
                            ) -> Dict[str, Any]:
    """What authorises this trigger. Raises RecheckRefused when nothing does."""
    if trigger_kind == rm.TRIGGER_SOURCE_RECOVERY:
        return {"authority": "operator-recorded source recovery; the job itself stops "
                             "if the source is still unavailable",
                "trigger_ref": trigger_ref}
    if trigger_kind == rm.TRIGGER_APPROVED_MAPPING_CHANGE:
        official = sp.OFFICIAL_POLICIES[source_id]
        if official.approval_status != sp.POLICY_APPROVED:
            raise RecheckRefused(
                f"no APPROVED mapping/policy exists for {source_id} (official status "
                f"{official.approval_status}); a PROPOSED policy is inactive and cannot "
                f"trigger a recheck")
        if official.mapping_version != trigger_ref:
            raise RecheckRefused(
                f"trigger_ref {trigger_ref!r} is not the approved mapping version "
                f"{official.mapping_version!r} for {source_id}")
        return {"authority": f"approved policy by {official.approved_by} at "
                             f"{official.approved_at}", "mapping_version": trigger_ref}
    if trigger_kind == rm.TRIGGER_NEW_APPROVED_SNAPSHOT:
        if source_id != sp.CMS_PPEF:
            raise RecheckRefused(
                f"{source_id} is not answered from an ingested reference snapshot on this "
                f"evidence path (only CMS_PPEF is); no snapshot can trigger its recheck")
        from app.Tefca.models import TEFCAPPEFSnapshot
        try:
            snap = await db.get(TEFCAPPEFSnapshot, uuid.UUID(str(trigger_ref)))
        except ValueError:
            snap = None
        if snap is None:
            raise RecheckRefused(f"no CMS PPEF snapshot {trigger_ref!r}")
        if snap.ingest_status != "complete":
            raise RecheckRefused(
                f"CMS PPEF snapshot {trigger_ref} is {snap.ingest_status!r}, not a "
                f"completed, schema-validated ingest; a partial snapshot triggers nothing")
        return {"authority": "completed, schema-validated CMS PPEF ingest "
                             "(ppef_ingest.validate_schema); PPEF has no separate human "
                             "approval step",
                "snapshot_id": str(snap.id), "component": snap.component,
                "as_of_label": snap.as_of_label, "sha256": snap.sha256}
    raise RecheckRefused(f"trigger_kind must be one of {rm.TRIGGER_KINDS}")


def _actor(user) -> Tuple[Optional[str], str]:
    uid = getattr(user, "id", None)
    return (str(uid) if uid else None,
            getattr(user, "email", None) or (str(uid) if uid else "unknown"))


async def request_recheck(db, intake_id, *, trigger_kind: str, source_id: str,
                          trigger_ref: str, rationale: str, user,
                          max_entities: int = MAX_ENTITIES_PER_JOB,
                          batch_size: int = DEFAULT_BATCH_SIZE) -> Dict[str, Any]:
    """Create a PENDING_APPROVAL recheck job, or return the existing one for
    the same trigger. Looks nothing up."""
    if source_id not in SUPPORTED_SOURCES:
        raise RecheckRefused(
            f"rechecks are supported for {SUPPORTED_SOURCES}; {source_id!r} is not "
            f"re-evaluated on this path")
    if not (trigger_ref or "").strip():
        raise RecheckRefused("trigger_ref is required: what recovered, which snapshot, or "
                             "which approved mapping version")
    if not (rationale or "").strip():
        raise RecheckRefused("rationale is required")
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise RecheckRefused(f"no intake {intake_id}")

    key = idempotency_key(intake.id, trigger_kind, source_id, trigger_ref)
    existing = (await db.execute(select(RceRecheckJob).where(
        RceRecheckJob.idempotency_key == key))).scalars().first()
    if existing is not None:
        return {**job_dto(existing), "already_exists": True}

    authority = await _validate_trigger(db, trigger_kind, source_id, trigger_ref.strip())

    candidates = await _candidates(db, intake.id, source_id)
    if trigger_kind == rm.TRIGGER_SOURCE_RECOVERY:
        eligible = [c for c in candidates if (c[3] or "").upper() == "UNAVAILABLE"]
    else:
        eligible = candidates
    if not eligible:
        raise RecheckRefused(
            f"no entity of this delivery needs a {trigger_kind} recheck for {source_id} "
            f"({len(candidates)} entity(ies) have evidence from it; none qualifies)")
    bound = max(1, min(int(max_entities or MAX_ENTITIES_PER_JOB), MAX_ENTITIES_PER_JOB))
    targets = eligible[:bound]

    from app.tefca_registry.rce.field_map import FIELD_MAP_VERSION
    from app.tefca_registry.rce.quality_rules import RULE_SET_VERSION
    actor_id, actor = _actor(user)
    job = RceRecheckJob(
        id=uuid.uuid4(), intake_id=intake.id, trigger_kind=trigger_kind,
        source_id=source_id, trigger_ref=trigger_ref.strip()[:255], idempotency_key=key,
        state=rm.STATE_PENDING_APPROVAL, requested_by=actor[:320], requested_by_id=actor_id,
        rationale=rationale.strip(), baseline_hash=_baseline_hash(targets),
        target_count=len(targets), untargeted_remaining=len(eligible) - len(targets),
        batch_size=max(1, min(int(batch_size or DEFAULT_BATCH_SIZE), MAX_BATCH_SIZE)),
        pinned={"trigger_authority": authority,
                "policy_registry_version": sp.POLICY_REGISTRY_VERSION,
                "official_policy_status": sp.OFFICIAL_POLICIES[source_id].approval_status,
                "rule_set_version": RULE_SET_VERSION, "field_map_version": FIELD_MAP_VERSION,
                "evidence_sources": evidence_sources_for(source_id),
                "max_entities": bound},
        summary={}, correlation_id=request_context.correlation_id()[:64])
    db.add(job)
    await db.flush()
    await db.execute(RceRecheckItem.__table__.insert(), [
        {"id": uuid.uuid4(), "job_id": job.id, "entity_id": entity_id, "entity_ref": ref,
         "state": rm.ITEM_PENDING, "prior_evidence_id": evidence_id,
         "prior_disposition": disposition, "detail": {}}
        for ref, entity_id, evidence_id, disposition in targets])
    await db.commit()
    return {**job_dto(job), "already_exists": False}


async def _job_or_refuse(db, job_id) -> RceRecheckJob:
    job = await db.get(RceRecheckJob, job_id)
    if job is None:
        raise RecheckRefused(f"no recheck job {job_id}")
    return job


async def _current_baseline(db, job: RceRecheckJob) -> str:
    """The same hash, recomputed now, over the job's own targeted entities."""
    targeted = {row for (row,) in (await db.execute(
        select(RceRecheckItem.entity_id).where(RceRecheckItem.job_id == job.id))).all()}
    candidates = [c for c in await _candidates(db, job.intake_id, job.source_id)
                  if c[1] in targeted]
    return _baseline_hash(candidates)


async def approve_recheck(db, job_id, *, user) -> Dict[str, Any]:
    """Independent approval. The requester may not approve their own request."""
    job = await _job_or_refuse(db, job_id)
    if job.state != rm.STATE_PENDING_APPROVAL:
        raise RecheckRefused(f"job is {job.state}; only a PENDING_APPROVAL job is approved")
    actor_id, actor = _actor(user)
    same = ((actor_id and job.requested_by_id and actor_id == job.requested_by_id)
            or actor == job.requested_by)
    if same:
        raise RecheckRefused(
            f"segregation of duties: {actor} requested recheck {job.id} and may not "
            f"approve it. A different person must approve a recheck.")
    if await _current_baseline(db, job) != job.baseline_hash:
        job.state = rm.STATE_REFUSED_STALE
        job.error_reason = ("stale baseline at approval: the targeted entities' evidence "
                            "changed after the request; request a new recheck")
        job.completed_at = datetime.utcnow()
        await db.commit()
        raise RecheckRefused(job.error_reason)
    job.approved_by, job.approved_by_id = actor[:320], actor_id
    job.approved_at = datetime.utcnow()
    job.state = rm.STATE_QUEUED
    await db.commit()
    return job_dto(job)


async def claim(db, job_id) -> Optional[RceRecheckJob]:
    """Move a QUEUED job to RUNNING under a row lock. None if another worker
    holds it, it is not QUEUED, or its attempts are exhausted (then FAILED)."""
    job = (await db.execute(
        select(RceRecheckJob).where(RceRecheckJob.id == job_id,
                                    RceRecheckJob.state == rm.STATE_QUEUED)
        .with_for_update(skip_locked=True))).scalar_one_or_none()
    if job is None:
        return None
    if (job.attempt_count or 0) >= RceRecheckJob.MAX_ATTEMPTS:
        job.state = rm.STATE_FAILED
        job.error_reason = f"attempts exhausted ({job.attempt_count})"
        job.completed_at = datetime.utcnow()
        await db.commit()
        return None
    job.state = rm.STATE_RUNNING
    job.attempt_count = (job.attempt_count or 0) + 1
    job.started_at = job.started_at or datetime.utcnow()
    job.heartbeat_at = datetime.utcnow()
    await db.commit()
    return job


def _source_disposition(evidence: Dict[str, Any], sources: List[str]) -> Optional[str]:
    """The rechecked source's own disposition in freshly assembled evidence:
    a risk signal outranks an unanswered check, which outranks an answer."""
    found = [(item.get("disposition") or "").upper()
             for dim in evidence.get("dimensions", []) or []
             for item in dim.get("evidence", []) or []
             if item.get("source") in sources]
    if not found:
        return None
    for group in (_RISK_DISPOSITIONS, _UNANSWERED):
        for d in group:
            if d in found:
                return d
    return found[0]


async def _recheck_one(db, job: RceRecheckJob, item: RceRecheckItem, *, local_store) -> None:
    from app.Tefca.entity_resolution import resolve_entity
    from app.Tefca.evidence_service import EvidenceService, evidence_rows_for_persistence
    from app.Tefca.models import TEFCADimensionEvidence
    from app.tefca_registry.rce import verification_findings as vf

    now = datetime.utcnow()
    entity = await resolve_entity(db, item.entity_ref)
    if entity is None:
        item.state, item.outcome, item.processed_at = rm.ITEM_DONE, rm.OUTCOME_NOT_RESOLVED, now
        item.detail = {"reason": "entity reference did not resolve"}
        return
    evidence = await EvidenceService(local_store=local_store).build_evidence(entity)
    sources = evidence_sources_for(job.source_id)
    disposition = _source_disposition(evidence, sources)

    # A NEW generation is appended. Nothing prior is edited or deleted.
    rows = evidence_rows_for_persistence(str(item.entity_id), None, evidence)
    for row in rows:
        row["review_cycle_id"] = None
        db.add(TEFCADimensionEvidence(**row))
    try:
        from app.Tefca import rce_fields
        await vf.record_from_evidence(db, entity_id=item.entity_id,
                                      npi=rce_fields.rce_npi(entity), evidence=evidence)
    except Exception as exc:  # noqa: BLE001 -- the ledger must not fail a recheck
        logger.error("recheck: NPI outcome not recorded for %s: %s", item.entity_id,
                     type(exc).__name__, exc_info=True)

    if disposition is None or disposition in _UNANSWERED:
        outcome = rm.OUTCOME_STILL_UNAVAILABLE
    elif disposition in _RISK_DISPOSITIONS:
        outcome = rm.OUTCOME_RISK_SIGNAL
        row = await db.get(reg.TefcaRegEntity, item.entity_id)
        if row is not None and row.verification_status != "in_review":
            row.verification_status = "in_review"       # toward scrutiny only
    else:
        outcome = rm.OUTCOME_ANSWERED_NO_SIGNAL         # NOT a verification pass
    item.state, item.outcome, item.new_disposition = rm.ITEM_DONE, outcome, disposition
    item.processed_at = now
    item.detail = {"evidence_generation": evidence.get("generated_at"),
                   "rows_appended": len(rows),
                   "entity_status_changed_to_verified": False}


async def run_batch(db, job_id, *, batch_size: Optional[int] = None) -> Dict[str, Any]:
    """Process up to one batch of PENDING items of a RUNNING job, commit once.
    Call again until `complete`."""
    from app.Tefca.ppef_store import make_local_store

    job = await _job_or_refuse(db, job_id)
    if job.state != rm.STATE_RUNNING:
        raise RecheckRefused(f"job is {job.state}; claim it first (only RUNNING jobs run)")

    if (job.processed_count or 0) == 0 and await _current_baseline(db, job) != job.baseline_hash:
        job.state = rm.STATE_REFUSED_STALE
        job.error_reason = ("stale baseline: the targeted entities' evidence changed "
                            "after approval; nothing was looked up")
        job.completed_at = datetime.utcnow()
        await db.commit()
        return {**job_dto(job), "processed_this_call": 0, "complete": True}

    size = max(1, min(int(batch_size or job.batch_size or DEFAULT_BATCH_SIZE), MAX_BATCH_SIZE))
    items = (await db.execute(
        select(RceRecheckItem)
        .where(RceRecheckItem.job_id == job.id, RceRecheckItem.state == rm.ITEM_PENDING)
        .order_by(RceRecheckItem.entity_ref).limit(size))).scalars().all()

    local_store = make_local_store(db)
    for item in items:
        try:
            await _recheck_one(db, job, item, local_store=local_store)
        except Exception as exc:  # noqa: BLE001 -- one entity must not lose the batch
            logger.error("recheck %s: unhandled error for %s: %s", job.id, item.entity_ref,
                         type(exc).__name__, exc_info=True)
            item.state, item.processed_at = rm.ITEM_ERROR, datetime.utcnow()
            item.detail = {"error": f"{type(exc).__name__}: {exc}"[:500]}

    job.processed_count = (job.processed_count or 0) + len(items)
    job.heartbeat_at = datetime.utcnow()
    await db.flush()

    counts = dict((await db.execute(
        select(func.coalesce(RceRecheckItem.outcome, RceRecheckItem.state), func.count())
        .where(RceRecheckItem.job_id == job.id)
        .group_by(func.coalesce(RceRecheckItem.outcome, RceRecheckItem.state)))).all())
    pending = int(counts.get(rm.ITEM_PENDING, 0))
    job.summary = {"by_outcome": {k: int(v) for k, v in counts.items()},
                   "pending": pending,
                   "note": "ANSWERED_NO_SIGNAL is a re-evaluation result, not a "
                           "verification pass; no entity was marked verified."}

    # Circuit breaker: a whole batch still unavailable means the source has
    # not recovered. Stop rather than spend the rest of the quota.
    all_down = bool(items) and all(i.outcome == rm.OUTCOME_STILL_UNAVAILABLE for i in items)
    if all_down and pending:
        job.state = rm.STATE_STOPPED_SOURCE_UNAVAILABLE
        job.error_reason = (f"every entity in a batch of {len(items)} was still "
                            f"unavailable from {job.source_id}; stopped with {pending} "
                            f"item(s) untouched")
    elif pending == 0:
        # 2026-10-04 (found in the browser journey): a job in which NOT ONE
        # entity was actually asked -- every reference failed to resolve, or
        # every lookup raised -- reported SUCCEEDED / "Finished". Nothing was
        # re-evaluated, so that is a failure with a reason, not a success.
        not_asked = int(counts.get(rm.OUTCOME_NOT_RESOLVED, 0)) + int(counts.get(rm.ITEM_ERROR, 0))
        total_items = sum(int(v) for v in counts.values())
        job.completed_at = datetime.utcnow()
        if total_items and not_asked == total_items:
            job.state = rm.STATE_FAILED
            job.error_reason = (
                f"no entity could be re-evaluated: {int(counts.get(rm.OUTCOME_NOT_RESOLVED, 0))} "
                f"reference(s) did not resolve in the registry and "
                f"{int(counts.get(rm.ITEM_ERROR, 0))} lookup(s) raised an error. The source "
                f"was not asked. Check the entity resolver configuration "
                f"(ENTITY_RESOLVER_SOURCE) before requesting another recheck.")
        else:
            job.state = rm.STATE_SUCCEEDED
    await db.commit()
    return {**job_dto(job), "processed_this_call": len(items),
            "complete": job.state != rm.STATE_RUNNING}


async def resume_stopped(db, job_id, *, user) -> Dict[str, Any]:
    """Requeue a job the circuit breaker stopped. Bounded by MAX_ATTEMPTS."""
    job = await _job_or_refuse(db, job_id)
    if job.state != rm.STATE_STOPPED_SOURCE_UNAVAILABLE:
        raise RecheckRefused(f"job is {job.state}; only a STOPPED_SOURCE_UNAVAILABLE job "
                             f"is resumed")
    if (job.attempt_count or 0) >= RceRecheckJob.MAX_ATTEMPTS:
        job.state = rm.STATE_FAILED
        job.error_reason = f"attempts exhausted ({job.attempt_count}); source still unavailable"
        job.completed_at = datetime.utcnow()
        await db.commit()
        raise RecheckRefused(job.error_reason)
    job.state = rm.STATE_QUEUED
    job.summary = {**(job.summary or {}), "resumed_by": _actor(user)[1]}
    await db.commit()
    return job_dto(job)


async def reap_stale_jobs(db, threshold_seconds: int = STALE_HEARTBEAT_SECONDS
                          ) -> List[Dict[str, Any]]:
    """A RUNNING job whose worker stopped heartbeating goes back to QUEUED
    (its DONE items stay done) or, with attempts exhausted, to FAILED."""
    cutoff = datetime.utcnow() - timedelta(seconds=threshold_seconds)
    stale = (await db.execute(select(RceRecheckJob).where(
        RceRecheckJob.state == rm.STATE_RUNNING,
        RceRecheckJob.heartbeat_at < cutoff))).scalars().all()
    out = []
    for job in stale:
        if (job.attempt_count or 0) < RceRecheckJob.MAX_ATTEMPTS:
            job.state = rm.STATE_QUEUED
        else:
            job.state = rm.STATE_FAILED
            job.error_reason = "worker_stopped_without_reporting"
            job.completed_at = datetime.utcnow()
        out.append({"job_id": str(job.id), "state": job.state,
                    "attempt_count": job.attempt_count})
    if out:
        await db.commit()
    return out


def job_dto(job: RceRecheckJob) -> Dict[str, Any]:
    return {
        "job_id": str(job.id), "intake_id": str(job.intake_id),
        "trigger_kind": job.trigger_kind, "source_id": job.source_id,
        "trigger_ref": job.trigger_ref, "state": job.state,
        "requested_by": job.requested_by, "approved_by": job.approved_by,
        "approved_at": job.approved_at.isoformat() if job.approved_at else None,
        "rationale": job.rationale, "pinned": job.pinned or {},
        "target_count": job.target_count,
        "untargeted_remaining": job.untargeted_remaining,
        "processed_count": job.processed_count, "batch_size": job.batch_size,
        "attempt_count": job.attempt_count, "max_attempts": RceRecheckJob.MAX_ATTEMPTS,
        "summary": job.summary or {}, "error_reason": job.error_reason,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "is_compliance_approval": False,
    }


async def list_jobs(db, intake_id, *, limit: int = 50) -> Dict[str, Any]:
    """Recheck jobs for one delivery, newest first. Read-only; available
    whether or not the feature is on, so a screen can say which it is."""
    from app.core.config import settings

    rows = (await db.execute(
        select(RceRecheckJob).where(RceRecheckJob.intake_id == intake_id)
        .order_by(RceRecheckJob.created_at.desc()).limit(limit))).scalars().all()
    return {
        "enabled": bool(getattr(settings, "ENABLE_CONTROLLED_RECHECKS", False)),
        "supported_sources": [
            {"source_id": s, "label": sp.SOURCE_LABELS.get(s, s)}
            for s in SUPPORTED_SOURCES],
        "trigger_kinds": list(rm.TRIGGER_KINDS),
        "limits": {"max_entities": MAX_ENTITIES_PER_JOB, "max_batch_size": MAX_BATCH_SIZE},
        "roles": {"request": "reviewer", "approve_run_resume": "qalead",
                  "rule": "the person who requested a recheck cannot approve it"},
        "items": [job_dto(j) for j in rows],
    }


async def list_items(db, job_id, *, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """Drill-down: every targeted entity, its prior and new disposition."""
    job = await _job_or_refuse(db, job_id)
    total = int((await db.execute(select(func.count()).select_from(RceRecheckItem).where(
        RceRecheckItem.job_id == job.id))).scalar() or 0)
    rows = (await db.execute(
        select(RceRecheckItem).where(RceRecheckItem.job_id == job.id)
        .order_by(RceRecheckItem.entity_ref).limit(limit).offset(offset))).scalars().all()
    return {"job_id": str(job.id), "total": total, "count": len(rows),
            "limit": limit, "offset": offset,
            "items": [{"entity_id": str(r.entity_id), "entity_ref": r.entity_ref,
                       "state": r.state, "prior_disposition": r.prior_disposition,
                       "new_disposition": r.new_disposition, "outcome": r.outcome,
                       "prior_evidence_id": str(r.prior_evidence_id)
                       if r.prior_evidence_id else None,
                       "processed_at": r.processed_at.isoformat() if r.processed_at else None,
                       "detail": r.detail or {}} for r in rows]}


async def items_csv(db, job_id) -> str:
    data = await list_items(db, job_id, limit=MAX_ENTITIES_PER_JOB, offset=0)
    from app.reports.engine.csv_engine import neutralise_row

    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(["job_id", "entity_id", "entity_ref", "state", "prior_disposition",
                "new_disposition", "outcome", "processed_at"])
    for i in data["items"]:
        # entity_ref is a delivered identifier: neutralised like every other
        # exported cell, so it cannot be read as a spreadsheet formula.
        w.writerow(neutralise_row([
            data["job_id"], i["entity_id"], i["entity_ref"], i["state"],
            i["prior_disposition"] or "", i["new_disposition"] or "",
            i["outcome"] or "", i["processed_at"] or ""]))
    return out.getvalue()
