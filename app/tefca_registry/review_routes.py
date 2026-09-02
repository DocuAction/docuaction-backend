"""TEFCA ARC Tasks 3-5: rules, sampling, reviews, reports, priority review.

Mounted at /api/tefca (alongside the existing registry router). Kept in its own
module because it is contract-scoped work with its own lifecycle — the review
engine changes when ONC guidance changes, which is a different cadence from the
registry CRUD it sits on top of.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import get_client_ip
from app.core.database import get_db
from app.core.security import require_role
from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg
from app.tefca_registry.bucket_classifier import (
    BucketClassifier, ensure_seed_rules, ensure_rules_v2)
from app.tefca_registry.report_generator import (
    build_report_data, render_html, report_id_for)
from app.tefca_registry.sampling_engine import CochranSampler

logger = logging.getLogger(__name__)

# Prefixed /api/tefca/arc, NOT /api/tefca. The legacy review module already
# owns /api/tefca/reviews and /api/tefca/reports; mounting there silently
# shadowed these endpoints behind the older ones, which returned a completely
# different payload shape. /arc keeps the contract-scoped Tasks 3-5 surface
# separable from the legacy module rather than competing with it.
router = APIRouter(prefix="/api/tefca/arc", tags=["TEFCA ARC Review"])

_classifier = BucketClassifier()


# ── review id ────────────────────────────────────────────────────────────────

async def generate_review_id(db: AsyncSession) -> str:
    """REV-YYYY-NNNNNN, sequential within the year.

    Derived from the current maximum rather than a counter table, then retried
    on collision. Two reviews created in the same instant would otherwise race
    to the same id, and review ids appear in delivered reports — a duplicate is
    not something that can be quietly corrected later.
    """
    year = datetime.utcnow().year
    prefix = f"REV-{year}-"
    for _attempt in range(6):
        top = (await db.execute(
            select(func.max(reg.ReviewRecord.review_id))
            .where(reg.ReviewRecord.review_id.like(f"{prefix}%")))).scalar()
        nxt = (int(top.rsplit("-", 1)[1]) + 1) if top else 1
        candidate = f"{prefix}{nxt:06d}"
        exists = (await db.execute(
            select(reg.ReviewRecord.id)
            .where(reg.ReviewRecord.review_id == candidate))).scalar_one_or_none()
        if not exists:
            return candidate
    return f"{prefix}{uuid.uuid4().hex[:6].upper()}"


# ── schemas ──────────────────────────────────────────────────────────────────

class RuleCreate(BaseModel):
    rule_code: str
    name: str
    bucket: str = Field(description="B1 | B2 | B3 | B4")
    priority: int
    conditions: dict
    description: Optional[str] = None
    effective_date: Optional[date] = None


class RuleUpdate(BaseModel):
    """Creates a NEW version. The existing row is retired, never edited."""
    name: Optional[str] = None
    bucket: Optional[str] = None
    priority: Optional[int] = None
    conditions: Optional[dict] = None
    description: Optional[str] = None
    effective_date: Optional[date] = None


class SampleCreate(BaseModel):
    sample_name: Optional[str] = None
    review_type: str = "weekly"
    confidence_level: float = Field(0.95, gt=0, lt=1)
    margin_of_error: float = Field(0.05, gt=0, lt=1)
    proportion: float = Field(0.5, ge=0, le=1)
    use_fpc: bool = True
    random_seed: Optional[int] = None
    stratify_by: Optional[str] = Field(
        None, description="entity_level | state — proportional allocation")
    entity_level: Optional[str] = None


class ResolveReview(BaseModel):
    resolution: str = Field(description="confirm | reclassify")
    reclassified_to: Optional[str] = Field(None, description="B1 | B2 | B3 | B4")
    rationale: str = Field(min_length=1, max_length=2000)


class GenerateReport(BaseModel):
    report_type: str = "weekly"
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    sample_id: Optional[uuid.UUID] = None


# ── rules API (Task 1.9) ─────────────────────────────────────────────────────

@router.get("/review-rules", dependencies=[Depends(require_role("viewer"))])
async def list_rules(include_retired: bool = Query(False),
                     db: AsyncSession = Depends(get_db)):
    await ensure_seed_rules(db)
    # v2 wires SAM.gov into classification. Every SAM condition fires only
    # on a positive finding, so with no SAM key this is a no-op on bucketing
    # (test_v2_is_identical_to_v1_when_sam_is_silent). Idempotent.
    await ensure_rules_v2(db)
    stmt = select(reg.ReviewRule)
    if not include_retired:
        stmt = stmt.where(reg.ReviewRule.is_active.is_(True),
                          reg.ReviewRule.retired_date.is_(None))
    rows = (await db.execute(stmt.order_by(reg.ReviewRule.priority))).scalars().all()
    return {"total": len(rows), "rules": [_rule_dict(r) for r in rows]}


@router.get("/review-rules/history", dependencies=[Depends(require_role("viewer"))])
async def rules_history(db: AsyncSession = Depends(get_db)):
    """Every version ever, current and retired. There is no DELETE — a rule that
    produced a past classification has to remain readable."""
    rows = (await db.execute(
        select(reg.ReviewRule)
        .order_by(reg.ReviewRule.rule_code, reg.ReviewRule.version))).scalars().all()
    return {"total": len(rows), "versions": [_rule_dict(r) for r in rows]}


@router.get("/review-rules/{rule_id}", dependencies=[Depends(require_role("viewer"))])
async def get_rule(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    r = await db.get(reg.ReviewRule, rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    return _rule_dict(r)


@router.post("/review-rules", dependencies=[Depends(require_role("admin"))])
async def create_rule(req: RuleCreate, request: Request,
                      db: AsyncSession = Depends(get_db),
                      user=Depends(require_role("admin"))):
    if req.bucket not in ("B1", "B2", "B3", "B4"):
        raise HTTPException(400, "bucket must be one of B1, B2, B3, B4")
    dup = (await db.execute(
        select(reg.ReviewRule).where(reg.ReviewRule.rule_code == req.rule_code,
                                     reg.ReviewRule.retired_date.is_(None)))
           ).scalars().first()
    if dup:
        raise HTTPException(
            409, f"{req.rule_code} already has a current version (v{dup.version}). "
                 f"Use PUT to supersede it — rules are versioned, not edited.")
    row = reg.ReviewRule(
        rule_code=req.rule_code, name=req.name, bucket=req.bucket,
        priority=req.priority, conditions=req.conditions,
        description=req.description, version=1,
        effective_date=req.effective_date or date.today(), is_active=True)
    db.add(row)
    actor_id, actor_email = reg_audit.actor_of(user)
    reg_audit.record(db, "review_rule_created", None, actor_id=actor_id,
                     actor_email=actor_email, ip_address=get_client_ip(request),
                     metadata={"rule_code": req.rule_code, "version": 1})
    await db.commit()
    _classifier._rules = None            # force reload on next classify
    return _rule_dict(row)


@router.put("/review-rules/{rule_id}", dependencies=[Depends(require_role("admin"))])
async def supersede_rule(rule_id: uuid.UUID, req: RuleUpdate, request: Request,
                         db: AsyncSession = Depends(get_db),
                         user=Depends(require_role("admin"))):
    """Retire the current version and insert version+1.

    The old row is never mutated. Classifications already made reference a
    specific rule_code+version and must keep resolving to the text that actually
    produced them.
    """
    cur = await db.get(reg.ReviewRule, rule_id)
    if not cur:
        raise HTTPException(404, "Rule not found")
    if cur.retired_date:
        raise HTTPException(400, f"{cur.rule_code} v{cur.version} is already retired")

    cur.retired_date = date.today()
    cur.is_active = False
    new = reg.ReviewRule(
        rule_code=cur.rule_code,
        name=req.name or cur.name,
        bucket=req.bucket or cur.bucket,
        priority=req.priority if req.priority is not None else cur.priority,
        conditions=req.conditions if req.conditions is not None else cur.conditions,
        description=req.description or cur.description,
        version=(cur.version or 1) + 1,
        effective_date=req.effective_date or date.today(),
        is_active=True)
    db.add(new)
    actor_id, actor_email = reg_audit.actor_of(user)
    reg_audit.record(db, "review_rule_superseded", None, actor_id=actor_id,
                     actor_email=actor_email, ip_address=get_client_ip(request),
                     metadata={"rule_code": cur.rule_code,
                               "from_version": cur.version, "to_version": new.version})
    await db.commit()
    _classifier._rules = None
    return {"retired": _rule_dict(cur), "current": _rule_dict(new)}


def _rule_dict(r) -> dict:
    return {"id": str(r.id), "rule_code": r.rule_code, "name": r.name,
            "bucket": r.bucket, "priority": r.priority, "conditions": r.conditions,
            "description": r.description, "version": r.version,
            "effective_date": r.effective_date, "retired_date": r.retired_date,
            "is_active": r.is_active}


# ── sampling API (Task 2.3) ──────────────────────────────────────────────────

@router.post("/samples", dependencies=[Depends(require_role("contributor"))])
async def draw_sample(req: SampleCreate, db: AsyncSession = Depends(get_db),
                      user=Depends(require_role("contributor"))):
    stmt = select(reg.TefcaRegEntity)
    if req.entity_level:
        stmt = stmt.where(reg.TefcaRegEntity.entity_level == req.entity_level)
    population = (await db.execute(stmt)).scalars().all()
    if not population:
        raise HTTPException(400, "Population is empty; nothing to sample.")

    strata_fn = None
    if req.stratify_by in ("entity_level", "state"):
        strata_fn = lambda e: getattr(e, req.stratify_by, None) or "unknown"  # noqa: E731

    result = CochranSampler().draw_sample(
        population, strata=strata_fn, seed=req.random_seed,
        confidence=req.confidence_level, margin=req.margin_of_error,
        proportion=req.proportion, use_fpc=req.use_fpc,
        strata_config={"stratify_by": req.stratify_by} if strata_fn else None)

    rules = await _classifier.load_rules(db)
    rule_set_version = max((r.get("version", 1) for r in rules), default=None)

    sample = reg.ReviewSample(
        sample_name=req.sample_name or f"{req.review_type} {date.today().isoformat()}",
        review_type=req.review_type, population_size=result.population_size,
        sample_size=result.sample_size, confidence_level=result.confidence_level,
        margin_of_error=result.margin_of_error, proportion=result.proportion,
        use_fpc=result.use_fpc, random_seed=result.random_seed,
        rule_set_version=rule_set_version, strata_config=result.strata_config,
        strata_distribution=result.strata_distribution, status="drawn",
        created_by=getattr(user, "id", None))
    db.add(sample)
    await db.flush()
    for e in result.selected:
        db.add(reg.SampleEntity(
            sample_id=sample.id, entity_id=e.id, review_status="pending",
            stratum=(strata_fn(e) if strata_fn else None)))
    await db.commit()
    return {"sample_id": str(sample.id), **result.config(),
            "review_type": req.review_type, "rule_set_version": rule_set_version}


@router.get("/samples", dependencies=[Depends(require_role("viewer"))])
async def list_samples(limit: int = Query(50, ge=1, le=500),
                       db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(reg.ReviewSample).order_by(reg.ReviewSample.drawn_at.desc())
        .limit(limit))).scalars().all()
    return {"total": len(rows), "samples": [_sample_dict(s) for s in rows]}


@router.get("/samples/{sample_id}", dependencies=[Depends(require_role("viewer"))])
async def get_sample(sample_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    s = await db.get(reg.ReviewSample, sample_id)
    if not s:
        raise HTTPException(404, "Sample not found")
    members = (await db.execute(
        select(reg.SampleEntity).where(reg.SampleEntity.sample_id == sample_id))
    ).scalars().all()
    return {**_sample_dict(s), "entities": [
        {"entity_id": str(m.entity_id), "review_status": m.review_status,
         "review_id": m.review_id, "discrepancy_bucket": m.discrepancy_bucket,
         "stratum": m.stratum} for m in members]}


@router.get("/samples/{sample_id}/stats", dependencies=[Depends(require_role("viewer"))])
async def sample_stats(sample_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    s = await db.get(reg.ReviewSample, sample_id)
    if not s:
        raise HTTPException(404, "Sample not found")
    members = (await db.execute(
        select(reg.SampleEntity).where(reg.SampleEntity.sample_id == sample_id))
    ).scalars().all()
    buckets: Dict[str, int] = {}
    for m in members:
        if m.discrepancy_bucket:
            buckets[m.discrepancy_bucket] = buckets.get(m.discrepancy_bucket, 0) + 1
    reviewed = sum(1 for m in members if m.review_status == "reviewed")
    return {
        "sample_id": str(sample_id),
        "configuration": _sample_dict(s),      # every parameter used
        "members": len(members), "reviewed": reviewed,
        "pending": len(members) - reviewed,
        "buckets": buckets,
    }


def _sample_dict(s) -> dict:
    """JSON-safe: drawn_at is stringified because this dict is embedded in a
    report's JSONB column. A raw datetime there fails to serialise on commit,
    which surfaced as a 500 only when a report was generated WITH a sample —
    the no-sample path worked and hid it."""
    return {"id": str(s.id), "sample_name": s.sample_name,
            "review_type": s.review_type, "population_size": s.population_size,
            "sample_size": s.sample_size, "confidence_level": s.confidence_level,
            "margin_of_error": s.margin_of_error, "proportion": s.proportion,
            "use_fpc": s.use_fpc, "random_seed": s.random_seed,
            "rule_set_version": s.rule_set_version,
            "strata_config": s.strata_config,
            "strata_distribution": s.strata_distribution,
            "status": s.status,
            "drawn_at": s.drawn_at.isoformat() if s.drawn_at else None}


# ── reviews + B3 resolution (Task 1.8) ───────────────────────────────────────

@router.get("/reviews", dependencies=[Depends(require_role("viewer"))])
async def list_reviews(bucket: Optional[str] = None,
                       pending_only: bool = Query(False),
                       limit: int = Query(100, ge=1, le=1000),
                       db: AsyncSession = Depends(get_db)):
    stmt = select(reg.ReviewRecord)
    if bucket:
        stmt = stmt.where(reg.ReviewRecord.classification_bucket == bucket)
    if pending_only:
        stmt = stmt.where(reg.ReviewRecord.reviewer_resolution.is_(None))
    rows = (await db.execute(
        stmt.order_by(reg.ReviewRecord.created_at.desc()).limit(limit))).scalars().all()
    return {"total": len(rows), "reviews": [_review_dict(r) for r in rows]}


@router.get("/reviews/{review_id}", dependencies=[Depends(require_role("viewer"))])
async def get_review(review_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(reg.ReviewRecord)
                          .where(reg.ReviewRecord.review_id == review_id))
         ).scalars().first()
    if not r:
        raise HTTPException(404, "Review not found")
    return _review_dict(r, include_results=True)


@router.patch("/reviews/{review_id}/resolve",
              dependencies=[Depends(require_role("reviewer"))])
async def resolve_review(review_id: str, req: ResolveReview, request: Request,
                         db: AsyncSession = Depends(get_db),
                         user=Depends(require_role("reviewer"))):
    """Close out a B3 with an explicit human decision.

    B3 means the rules could not explain the evidence, so it cannot be closed by
    the engine. Both outcomes are recorded: confirming B3 is a finding in its
    own right, not an absence of one, and the rationale is mandatory so a later
    reader can see why.
    """
    r = (await db.execute(select(reg.ReviewRecord)
                          .where(reg.ReviewRecord.review_id == review_id))
         ).scalars().first()
    if not r:
        raise HTTPException(404, "Review not found")
    if req.resolution not in ("confirm", "reclassify"):
        raise HTTPException(400, "resolution must be 'confirm' or 'reclassify'")
    if req.resolution == "reclassify":
        if req.reclassified_to not in ("B1", "B2", "B3", "B4"):
            raise HTTPException(400, "reclassified_to must be B1, B2, B3 or B4")
        r.reviewer_resolution = "reclassified"
        r.reclassified_to = req.reclassified_to
    else:
        r.reviewer_resolution = "confirmed"

    actor_id, actor_email = reg_audit.actor_of(user)
    r.reclassified_by = actor_id
    r.reclassified_at = datetime.utcnow()
    r.resolution_rationale = req.rationale
    r.reviewed_at = datetime.utcnow()

    await db.execute(
        reg.SampleEntity.__table__.update()
        .where(reg.SampleEntity.review_id == review_id)
        .values(review_status="reviewed",
                discrepancy_bucket=(r.reclassified_to or r.classification_bucket),
                reviewed_at=datetime.utcnow()))

    reg_audit.record(db, "review_resolved", r.entity_id, actor_id=actor_id,
                     actor_email=actor_email, ip_address=get_client_ip(request),
                     metadata={"review_id": review_id,
                               "resolution": r.reviewer_resolution,
                               "reclassified_to": r.reclassified_to,
                               "rationale": req.rationale})
    await db.commit()
    return _review_dict(r)


def _review_dict(r, include_results: bool = False) -> dict:
    # entity_id is NULL on a pre-promotion case (a HELD source record has no
    # entity yet), so this must not str(None) into the string "None".
    d = {"review_id": r.review_id,
         "entity_id": str(r.entity_id) if r.entity_id else None,
         "source_record_id": (str(r.source_record_id)
                              if getattr(r, "source_record_id", None) else None),
         "assigned_to_user_id": (str(r.assigned_to_user_id)
                                 if getattr(r, "assigned_to_user_id", None)
                                 else None),
         "sample_id": str(r.sample_id) if r.sample_id else None,
         "classification": {
             "bucket": r.classification_bucket, "rule_code": r.classification_rule,
             "rule_version": r.classification_rule_version,
             "rationale": r.classification_rationale},
         "reviewer_resolution": r.reviewer_resolution,
         "reclassified_to": r.reclassified_to,
         "resolution_rationale": r.resolution_rationale,
         "reviewed_at": r.reviewed_at, "created_at": r.created_at,
         "effective_bucket": r.reclassified_to or r.classification_bucket}
    if include_results:
        d["verification_results"] = r.verification_results
    return d


# ── reports (Task 3.5) ───────────────────────────────────────────────────────

@router.post("/reports/generate", deprecated=True,
             summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Generate a report",
             dependencies=[Depends(require_role("admin"))])
async def generate_report(req: GenerateReport, db: AsyncSession = Depends(get_db),
                          user=Depends(require_role("admin"))):
    """Build and ARCHIVE a report. Immutable once stored."""
    end = req.period_end or date.today()
    start = req.period_start or (end - timedelta(days=7 if req.report_type == "weekly" else 90))

    reviews = (await db.execute(
        select(reg.ReviewRecord).where(reg.ReviewRecord.created_at >= datetime(
            start.year, start.month, start.day)))).scalars().all()
    review_dicts = [{
        "review_id": r.review_id, "classification_bucket": r.classification_bucket,
        "reviewer_resolution": r.reviewer_resolution,
        "reclassified_to": r.reclassified_to} for r in reviews]

    verifs = (await db.execute(
        select(reg.TefcaVerification).where(reg.TefcaVerification.verified_at >= datetime(
            start.year, start.month, start.day)))).scalars().all()
    verif_dicts = [{"source": v.source, "verification_status": v.verification_status}
                   for v in verifs]

    sample_cfg = None
    if req.sample_id:
        s = await db.get(reg.ReviewSample, req.sample_id)
        if s:
            sample_cfg = _sample_dict(s)

    rules = await _classifier.load_rules(db)
    rsv = max((r.get("version", 1) for r in rules), default=None)

    data = build_report_data(
        report_type=req.report_type, period_start=start, period_end=end,
        reviews=review_dicts, verifications=verif_dicts,
        sample=sample_cfg, rule_set_version=rsv)

    rid = report_id_for(req.report_type, end)
    # Reports are immutable. A second generation for the same period gets a
    # suffixed id rather than overwriting what was already delivered.
    if (await db.execute(select(reg.ReviewReport.id)
                         .where(reg.ReviewReport.report_id == rid))).scalar_one_or_none():
        rid = f"{rid}-R{uuid.uuid4().hex[:4].upper()}"

    html = render_html(data, rid)
    row = reg.ReviewReport(
        report_id=rid, report_type=req.report_type, period_start=start,
        period_end=end, sample_id=req.sample_id, rule_set_version=rsv,
        report_data=data, report_html=html,
        generated_by=getattr(user, "id", None))
    db.add(row)
    await db.commit()
    return {"report_id": rid, "report_type": req.report_type,
            "period": {"start": start, "end": end}, "data": data}


@router.get("/reports", deprecated=True,
            summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. List reports",
            dependencies=[Depends(require_role("viewer"))])
async def list_reports(report_type: Optional[str] = None,
                       limit: int = Query(50, ge=1, le=200),
                       db: AsyncSession = Depends(get_db)):
    stmt = select(reg.ReviewReport)
    if report_type:
        stmt = stmt.where(reg.ReviewReport.report_type == report_type)
    rows = (await db.execute(
        stmt.order_by(reg.ReviewReport.generated_at.desc()).limit(limit))).scalars().all()
    return {"total": len(rows), "reports": [
        {"report_id": r.report_id, "report_type": r.report_type,
         "period_start": r.period_start, "period_end": r.period_end,
         "rule_set_version": r.rule_set_version,
         "generated_at": r.generated_at} for r in rows]}


@router.get("/reports/{report_id}", deprecated=True,
            summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Report detail",
            dependencies=[Depends(require_role("viewer"))])
async def get_report(report_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(reg.ReviewReport)
                          .where(reg.ReviewReport.report_id == report_id))
         ).scalars().first()
    if not r:
        raise HTTPException(404, "Report not found")
    return {"report_id": r.report_id, "report_type": r.report_type,
            "period_start": r.period_start, "period_end": r.period_end,
            "rule_set_version": r.rule_set_version,
            "generated_at": r.generated_at, "data": r.report_data}


@router.get("/reports/{report_id}/excel", deprecated=True,
            summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Report as Excel",
            dependencies=[Depends(require_role("viewer"))])
async def get_report_excel(report_id: str, db: AsyncSession = Depends(get_db)):
    """Excel form of an archived report.

    Built from the STORED report_data, never recomputed — a report that renders
    one set of numbers as HTML and another as Excel would be worse than having
    no export. Entity rows are rehydrated from the review records the report
    covers so the sheet carries review IDs and per-source outcomes.
    """
    from fastapi.responses import Response
    from app.tefca_registry.report_excel import build_weekly_excel

    r = (await db.execute(select(reg.ReviewReport)
                          .where(reg.ReviewReport.report_id == report_id))
         ).scalars().first()
    if not r:
        raise HTTPException(404, "Report not found")

    ids = []
    for bucket_ids in ((r.report_data or {}).get("classification_distribution", {})
                       .get("review_ids", {}) or {}).values():
        ids.extend([i for i in (bucket_ids or []) if i])

    rows = []
    if ids:
        records = (await db.execute(
            select(reg.ReviewRecord)
            .where(reg.ReviewRecord.review_id.in_(ids)))).scalars().all()
        by_entity = {}
        if records:
            ents = (await db.execute(
                select(reg.TefcaRegEntity).where(
                    reg.TefcaRegEntity.id.in_([x.entity_id for x in records])))
            ).scalars().all()
            by_entity = {e.id: e for e in ents}
        for rec in records:
            ent = by_entity.get(rec.entity_id)
            vr = rec.verification_results or {}
            rows.append({
                "review_id": rec.review_id,
                "entity_name": getattr(ent, "name", ""),
                "npi": ((vr.get("sources") or {}).get("nppes") or {}).get(
                    "lookup_identifier", ""),
                "entity_type": getattr(ent, "entity_level", ""),
                "verification": vr.get("sources") or {},
                "bucket": rec.reclassified_to or rec.classification_bucket,
                "rule_code": rec.classification_rule,
                "rationale": rec.classification_rationale,
            })

    data = build_weekly_excel(r.report_data or {}, report_id, rows)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.xlsx"'})


@router.get("/reports/{report_id}/html", deprecated=True,
            summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Report as HTML",
            dependencies=[Depends(require_role("viewer"))])
async def get_report_html(report_id: str, db: AsyncSession = Depends(get_db)):
    from fastapi.responses import HTMLResponse
    r = (await db.execute(select(reg.ReviewReport)
                          .where(reg.ReviewReport.report_id == report_id))
         ).scalars().first()
    if not r:
        raise HTTPException(404, "Report not found")
    # The stored snapshot, not a re-render. What the client received is what
    # this returns, whatever the data looks like now.
    return HTMLResponse(content=r.report_html or "<p>No HTML snapshot stored.</p>")


# ── priority review (Task 5) ─────────────────────────────────────────────────

@router.post("/priority-review", dependencies=[Depends(require_role("admin"))])
async def priority_review(entity_id: uuid.UUID, request: Request,
                          db: AsyncSession = Depends(get_db),
                          user=Depends(require_role("admin"))):
    """COR-directed review of one entity: verify, classify, archive a report."""
    entity = await db.get(reg.TefcaRegEntity, entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")

    from app.tefca_registry.review_service import run_review
    result = await run_review(db, entity, user=user,
                              ip_address=get_client_ip(request),
                              trigger="priority")

    rules = await _classifier.load_rules(db)
    rsv = max((r.get("version", 1) for r in rules), default=None)
    today = date.today()
    data = build_report_data(
        report_type="priority", period_start=today, period_end=today,
        reviews=[{"review_id": result["review_id"],
                  "classification_bucket": result["classification"]["bucket"],
                  "reviewer_resolution": None, "reclassified_to": None}],
        verifications=[{"source": k, "verification_status": v.get("status")}
                       for k, v in result["verification"].items()],
        rule_set_version=rsv,
        extra_limitations=[
            "Priority review covers a single entity; no statistical sample was "
            "drawn and no population inference should be made from it."])
    data["priority_review"] = {
        "entity_id": str(entity_id), "entity_name": entity.name,
        "root_cause": result["classification"]["rationale"],
        "severity": {"B1": "none", "B2": "low", "B3": "medium",
                     "B4": "high"}.get(result["classification"]["bucket"], "unknown"),
        "recommendations": _recommendations(result["classification"]["bucket"]),
        "status": "complete",
    }

    rid = report_id_for("priority", today)
    if (await db.execute(select(reg.ReviewReport.id)
                         .where(reg.ReviewReport.report_id == rid))).scalar_one_or_none():
        rid = f"{rid}-{uuid.uuid4().hex[:4].upper()}"
    db.add(reg.ReviewReport(
        report_id=rid, report_type="priority", period_start=today, period_end=today,
        entity_id=entity_id, rule_set_version=rsv, report_data=data,
        report_html=render_html(data, rid), generated_by=getattr(user, "id", None)))
    await db.commit()
    return {**result, "report_id": rid, "priority_review": data["priority_review"]}


def _recommendations(bucket: str) -> List[str]:
    return {
        "B1": ["No corrective action required. Re-verify on the normal cycle."],
        "B2": ["Confirm the administrative variance with the participant.",
               "Update the registry record if the authoritative source is correct."],
        "B3": ["Assign to an analyst for manual examination.",
               "Obtain the participant's source documentation.",
               "Resolve via PATCH /api/tefca/arc/reviews/{review_id}/resolve with a "
               "recorded rationale."],
        "B4": ["Escalate immediately — exclusion, debarment or an invalid identifier.",
               "Suspend the entity pending resolution.",
               "Notify the COR; document the disposition."],
    }.get(bucket, ["Manual examination required."])


# ═══ Priority-review dashboard (QA-2.1, QA-2.2, QA-2.3) ═══════════════════════
# There was no dashboard and no due-date model before this — "shows no overdue
# metrics" was a missing feature, not a broken query. The SLA policy itself lives
# in app/tefca_registry/sla.py so it is testable without a database and editable
# in one place; this endpoint only joins it to the sample tables.


@router.get("/priority-reviews/dashboard",
            dependencies=[Depends(require_role("viewer"))])
async def priority_review_dashboard(
    include_completed: bool = Query(False,
                                    description="Include reviews already done"),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """Outstanding sampled reviews with SLA state, newest sample first.

    A review's clock starts when its sample was DRAWN, not when the entity was
    created — the entity may have sat in the registry for months before being
    selected, and dating the SLA from creation would report every first review
    as years overdue.
    """
    from app.tefca_registry import sla

    now = datetime.utcnow()
    rows = (await db.execute(
        select(reg.SampleEntity, reg.ReviewSample, reg.TefcaRegEntity)
        .join(reg.ReviewSample, reg.SampleEntity.sample_id == reg.ReviewSample.id)
        .join(reg.TefcaRegEntity, reg.SampleEntity.entity_id == reg.TefcaRegEntity.id)
        .order_by(reg.ReviewSample.drawn_at.desc())
        .limit(limit))).all()

    items: List[Dict[str, Any]] = []
    for sample_entity, sample, entity in rows:
        completed = (sample_entity.review_status or "").lower() == "reviewed"
        if completed and not include_completed:
            continue
        block = sla.describe(sample.drawn_at, sample.review_type,
                             now=now, completed=completed)
        items.append({
            "sample_id": str(sample.id),
            "sample_name": sample.sample_name,
            "review_type": sample.review_type,
            "entity_id": str(entity.id),
            "entity_name": entity.name,
            "review_id": sample_entity.review_id,
            "review_status": sample_entity.review_status,
            "discrepancy_bucket": sample_entity.discrepancy_bucket,
            # ISO 8601 throughout (QA-2.2). Formatting for a human is the display
            # layer's job; a mixed-format payload cannot be sorted or parsed.
            "drawn_at": sample.drawn_at.isoformat() if sample.drawn_at else None,
            "reviewed_at": (sample_entity.reviewed_at.isoformat()
                            if sample_entity.reviewed_at else None),
            **block,
        })

    overdue = [i for i in items if i["sla_status"] == sla.OVERDUE]
    at_risk = [i for i in items if i["sla_status"] == sla.AT_RISK]
    return {
        "generated_at": now.isoformat(),
        "total": len(items),
        # QA-2.1 — the count AND the list. A bare number tells a reviewer that
        # something is late without telling them what to open.
        "overdue_count": len(overdue),
        "overdue_reviews": overdue,
        "at_risk_count": len(at_risk),
        "on_track_count": len(items) - len(overdue) - len(at_risk),
        "sla_windows_days": sla.REVIEW_SLA_DAYS,
        "reviews": items,
    }


# ═══ Review cycles (QA-3.1, QA-3.2, QA-3.3) ═══════════════════════════════════
# Cycle creation already worked, at POST /api/v1/tefca/cycles on the legacy
# router, gated at program_manager (admin clears it; reviewer does not). The
# reported defect is a PATH mismatch, not a permission or mounting fault.
#
# These routes put the documented ARC-namespaced surface on top of the SAME
# tefca_review_cycles table rather than introducing a second cycle store, which
# would let two endpoints disagree about how many cycles exist.


class ARCCycleCreate(BaseModel):
    name: Optional[str] = Field(None, description="Operator label for the cycle")
    cycle_type: str = Field(
        description="TASK3_RETROSPECTIVE | TASK4_ONGOING | TASK5_PRIORITY")
    start_date: str = Field(description="ISO 8601 date or datetime")
    end_date: Optional[str] = Field(None, description="ISO 8601 date or datetime")


def _parse_iso(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        raise HTTPException(422, f"{field} must be ISO 8601 (got {value!r})")


@router.post("/cycles", status_code=201,
             dependencies=[Depends(require_role("admin"))])
async def create_arc_cycle(req: ARCCycleCreate, http: Request,
                           db: AsyncSession = Depends(get_db),
                           user=Depends(require_role("admin"))):
    """Create a review cycle. 201 with the cycle_id."""
    from app.Tefca.models import CycleStatus, CycleType, TEFCAReviewCycle

    try:
        ctype = CycleType(req.cycle_type)
    except ValueError:
        raise HTTPException(
            422, f"Invalid cycle_type. Use one of: {[c.value for c in CycleType]}")

    start = _parse_iso(req.start_date, "start_date")
    end = _parse_iso(req.end_date, "end_date") if req.end_date else None
    if end and end < start:
        # Caught here rather than left to the reader: an inverted range makes
        # every completion percentage and overdue count downstream meaningless.
        raise HTTPException(422, "end_date must not be earlier than start_date")

    row = TEFCAReviewCycle(
        cycle_type=ctype, cycle_start_date=start, cycle_end_date=end,
        cycle_status=CycleStatus.PLANNED,
        created_by=str(getattr(user, "email", "") or ""),
    )
    db.add(row)
    await db.flush()
    reg_audit.record(db, "cycle_created", None,
                     actor_id=getattr(user, "id", None),
                     actor_email=getattr(user, "email", None),
                     ip_address=get_client_ip(http),
                     metadata={"cycle_id": str(row.cycle_id),
                               "cycle_type": ctype.value, "name": req.name})
    await db.commit()
    return {
        "cycle_id": str(row.cycle_id),
        "name": req.name,
        "cycle_type": ctype.value,
        "cycle_status": row.cycle_status.value,
        # QA-3.2 — echo the dates back so a caller can confirm what was stored
        # rather than trusting that its input was parsed the way it intended.
        "start_date": start.isoformat(),
        "end_date": end.isoformat() if end else None,
    }


@router.get("/cycles/{cycle_id}/stats",
            dependencies=[Depends(require_role("viewer"))])
async def arc_cycle_stats(cycle_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Completion and bucket distribution for one cycle (QA-3.3)."""
    from app.Tefca.models import TEFCAReviewCycle
    from app.tefca_registry import sla

    cycle = (await db.execute(
        select(TEFCAReviewCycle)
        .where(TEFCAReviewCycle.cycle_id == cycle_id))).scalar_one_or_none()
    if not cycle:
        raise HTTPException(404, f"Review cycle {cycle_id} not found")

    total = int(cycle.total_entities_sampled or 0)
    reviewed = int(cycle.total_entities_completed or 0)
    pending = max(0, total - reviewed)
    buckets = {"B1": int(cycle.bucket_1_count or 0),
               "B2": int(cycle.bucket_2_count or 0),
               "B3": int(cycle.bucket_3_count or 0),
               "B4": int(cycle.bucket_4_count or 0)}

    # A cycle with nothing sampled is 0% complete, not 100%. Dividing by a zero
    # population and calling the result "done" is how an empty cycle reports as
    # a finished one.
    completion_rate = round(reviewed / total, 4) if total else 0.0

    status = (cycle.cycle_status.value if cycle.cycle_status else None)
    finished = status in ("COMPLETE", "REPORT_GENERATED")
    overdue = bool(cycle.cycle_end_date) and not finished and \
        sla.is_overdue(cycle.cycle_end_date, datetime.utcnow())

    return {
        "cycle_id": str(cycle.cycle_id),
        "cycle_type": cycle.cycle_type.value if cycle.cycle_type else None,
        "cycle_status": status,
        "start_date": (cycle.cycle_start_date.isoformat()
                       if cycle.cycle_start_date else None),
        "end_date": (cycle.cycle_end_date.isoformat()
                     if cycle.cycle_end_date else None),
        "total": total,
        "reviewed": reviewed,
        "pending": pending,
        "completion_rate": completion_rate,
        "completion_percent": round(completion_rate * 100, 2),
        "bucket_counts": buckets,
        "overdue": overdue,
    }


# ── B2 QA GATE — immutable analyst / QA decision events ──────────────────────
#
# These endpoints record DECISIONS. They assign no work and imply no tier:
# which tier receives which bucket is Decision D3 and is unresolved, so no queue
# routing is built here. What QA governs is what happens AFTER a determination
# exists, which is independent of who was assigned it.

class AnalystDetermination(BaseModel):
    determination: str = Field(description="CONFIRM | RECLASSIFY")
    determined_bucket: Optional[str] = Field(None, description="B1 | B2 | B3 | B4")
    rationale: str = Field(min_length=10, max_length=4000)


class QaReview(BaseModel):
    qa_action: str = Field(description="APPROVE | RETURN | ESCALATE")
    qa_reason: str = Field(min_length=10, max_length=4000)
    escalated_to_user_id: Optional[uuid.UUID] = None
    escalation_reason: Optional[str] = Field(None, max_length=4000)
    #: Segregation-of-duties exception. Requires an admin grant from a DIFFERENT
    #: person. Recorded, counted, and expected to be disabled in production.
    sod_exception_granted_by: Optional[uuid.UUID] = None
    sod_exception_reason: Optional[str] = Field(None, max_length=4000)


class SupersedeDetermination(BaseModel):
    supersedes_decision_id: uuid.UUID
    determination: str = Field(description="CONFIRM | RECLASSIFY")
    determined_bucket: Optional[str] = Field(None, description="B1 | B2 | B3 | B4")
    supersession_reason: str = Field(min_length=10, max_length=4000)
    rationale: Optional[str] = Field(None, max_length=4000)


@router.post("/reviews/{review_id}/determination",
             dependencies=[Depends(require_role("reviewer"))])
async def record_determination(review_id: str, req: AnalystDetermination,
                               request: Request,
                               db: AsyncSession = Depends(get_db),
                               user=Depends(require_role("reviewer"))):
    """Record an analyst determination as a new immutable event.

    OWNERSHIP-GATED, ADDED HERE AT THE ROUTE — NOT INSIDE THE SHARED
    FUNCTION. Found missing during DEV certification, 2026-09-02, and proven
    live: an authenticated `reviewer` who had never claimed a case could
    still record a determination on it, purely by knowing its id — the only
    check was the caller's ROLE, never who actually held the case.

    This mirrors `priority_review.py`'s own call site exactly: it fetches
    the record and calls `case_assignment.require_owner(record, user)`
    itself, immediately before calling this SAME shared
    `record_analyst_determination`, rather than relying on that function to
    enforce it. That is the established convention for a reason — a first
    attempt at fixing this put the check INSIDE
    `record_analyst_determination` and broke roughly fifty pre-existing
    tests (workbook export, QA gate, sampling, supervisor dashboards — none
    of them about ownership) whose fixtures legitimately call the shared
    function directly to seed a determination without going through
    claim/release, exactly as `priority_review.py`'s own usage shows is a
    supported way to call it. Gating at the interactive HTTP route, where
    the actual vulnerability was proven, fixes the real exposure without
    disturbing that.
    """
    from app.tefca_registry.case_assignment import AssignmentRefused, require_owner
    from app.tefca_registry.qa_gate import QaGateRefused, record_analyst_determination

    record = (await db.execute(
        select(reg.ReviewRecord).where(reg.ReviewRecord.review_id == review_id)
    )).scalars().first()
    if record is None:
        raise HTTPException(404, f"no review exists with id {review_id}")
    try:
        require_owner(record, user)
    except AssignmentRefused as exc:
        raise HTTPException(409, str(exc))

    try:
        result = await record_analyst_determination(
            db, review_id, user=user, determination=req.determination,
            determined_bucket=req.determined_bucket, rationale=req.rationale,
            ip_address=get_client_ip(request))
    except QaGateRefused as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return result


@router.post("/reviews/{review_id}/qa", dependencies=[Depends(require_role("qalead"))])
async def submit_qa(review_id: str, req: QaReview, request: Request,
                    db: AsyncSession = Depends(get_db),
                    user=Depends(require_role("qalead"))):
    """APPROVE, RETURN or ESCALATE. Never an edit of the analyst's decision."""
    from app.tefca_registry.qa_gate import QaGateRefused, submit_qa_review

    try:
        result = await submit_qa_review(
            db, review_id, user=user, qa_action=req.qa_action,
            qa_reason=req.qa_reason,
            escalated_to_user_id=req.escalated_to_user_id,
            escalation_reason=req.escalation_reason,
            sod_exception_granted_by=req.sod_exception_granted_by,
            sod_exception_reason=req.sod_exception_reason,
            ip_address=get_client_ip(request))
    except QaGateRefused as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return result


@router.post("/reviews/{review_id}/supersede",
             dependencies=[Depends(require_role("program_manager"))])
async def supersede(review_id: str, req: SupersedeDetermination, request: Request,
                    db: AsyncSession = Depends(get_db),
                    user=Depends(require_role("program_manager"))):
    """Issue a NEW determination that supersedes an earlier one after escalation."""
    from app.tefca_registry.qa_gate import QaGateRefused, supersede_determination

    try:
        result = await supersede_determination(
            db, review_id, user=user,
            supersedes_decision_id=req.supersedes_decision_id,
            determination=req.determination,
            determined_bucket=req.determined_bucket,
            supersession_reason=req.supersession_reason,
            rationale=req.rationale, ip_address=get_client_ip(request))
    except QaGateRefused as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return result


# NOT /reviews/qa-queue: `@router.get("/reviews/{review_id}")` is registered
# earlier in this module, and FastAPI matches in registration order — the
# literal path would be swallowed as review_id="qa-queue" and return 404 for
# a review that does not exist. A distinct path avoids depending on ordering.
@router.get("/qa-queue", dependencies=[Depends(require_role("qalead"))])
async def get_qa_queue(limit: int = Query(100, ge=1, le=500),
                       db: AsyncSession = Depends(get_db)):
    """Determinations awaiting QA. Not an analyst work queue — see D3."""
    from app.tefca_registry.qa_gate import qa_queue

    items = await qa_queue(db, limit=limit)
    return {"count": len(items), "awaiting_qa": items}


@router.get("/reviews/{review_id}/history",
            dependencies=[Depends(require_role("viewer"))])
async def get_review_history(review_id: str, db: AsyncSession = Depends(get_db)):
    """The full ordered decision chain, superseded events included and marked."""
    from app.tefca_registry.qa_gate import review_state

    return await review_state(db, review_id)


# ── CASE ASSIGNMENT — the Step #10 service, now reachable ────────────────────
#
# `case_assignment` has existed since the human-workflow gate but had no HTTP
# surface, so a case could be created and determined but never CLAIMED. These
# endpoints are deliberately queue-agnostic: a DQ exception, a sampled record
# and a COR priority request are all `review_records`, and all three need the
# same ownership semantics. A priority-only claim endpoint would have been a
# second implementation of a solved problem.


class CaseAssign(BaseModel):
    to_user_id: uuid.UUID
    reason: Optional[str] = Field(None, max_length=4000)
    #: Required only when the case is already held by someone. Assigning
    #: unheld work needs nothing; taking work off a live holder must say why.
    override_reason: Optional[str] = Field(None, max_length=4000)


class CaseRelease(BaseModel):
    reason: Optional[str] = Field(None, max_length=4000)


# The three case READS below sit at the viewer floor, like every other TEFCA
# read. None of them names an analyst — `available_cases` is unowned by
# definition, `my_work` is scoped to the caller, and the most sensitive thing in
# the analyst package is the decision chain, which is already viewer-readable at
# `/reviews/{review_id}/history`. The AUTHORITY is on the writes: claiming and
# releasing need `reviewer`, assigning needs `senior_analyst`.

@router.get("/available-cases", dependencies=[Depends(require_role("viewer"))])
async def available_cases(queue_source: Optional[str] = Query(None),
                          limit: int = Query(100, ge=1, le=500),
                          db: AsyncSession = Depends(get_db)):
    """Unowned cases anyone may take."""
    from app.tefca_registry.case_assignment import available_cases as _available

    items = await _available(db, queue_source=queue_source, limit=limit)
    return {"count": len(items), "cases": items}


@router.get("/my-work", dependencies=[Depends(require_role("viewer"))])
async def my_work(queue_source: Optional[str] = Query(None),
                  limit: int = Query(100, ge=1, le=500),
                  db: AsyncSession = Depends(get_db),
                  user=Depends(require_role("viewer"))):
    """Cases this user holds. Never another user's — the owner is the caller."""
    from app.tefca_registry.case_assignment import my_work as _mine

    items = await _mine(db, user=user, queue_source=queue_source, limit=limit)
    return {"count": len(items), "cases": items}


@router.post("/reviews/{review_id}/claim",
             dependencies=[Depends(require_role("reviewer"))])
async def claim_case(review_id: str, request: Request,
                     db: AsyncSession = Depends(get_db),
                     user=Depends(require_role("reviewer"))):
    """Take an unowned case. Atomic: at most one claimer wins."""
    from app.tefca_registry.case_assignment import AssignmentRefused, claim

    try:
        result = await claim(db, review_id, user=user,
                             ip_address=get_client_ip(request))
    except AssignmentRefused as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return result


@router.post("/reviews/{review_id}/release",
             dependencies=[Depends(require_role("reviewer"))])
async def release_case(review_id: str, req: CaseRelease, request: Request,
                       db: AsyncSession = Depends(get_db),
                       user=Depends(require_role("reviewer"))):
    """Give a case back. Only its holder, or a supervisor, may."""
    from app.tefca_registry.case_assignment import AssignmentRefused, release

    try:
        result = await release(db, review_id, user=user, reason=req.reason,
                               ip_address=get_client_ip(request))
    except AssignmentRefused as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return result


@router.post("/reviews/{review_id}/assign",
             dependencies=[Depends(require_role("senior_analyst"))])
async def assign_case(review_id: str, req: CaseAssign, request: Request,
                      db: AsyncSession = Depends(get_db),
                      user=Depends(require_role("senior_analyst"))):
    """Supervisor assignment. The MANNER of acquisition lives in the audit log."""
    from app.tefca_registry.case_assignment import AssignmentRefused, assign

    try:
        result = await assign(db, review_id, user=user, to_user_id=req.to_user_id,
                              reason=req.reason,
                              override_reason=req.override_reason,
                              ip_address=get_client_ip(request))
    except AssignmentRefused as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return result


# ── TASK 5 PRIORITY REVIEWS (D5.1) ───────────────────────────────────────────
#
# The COR names the entities and states the deadline (para 146). Every field a
# client may set is on the request model below and nothing else: reportability,
# the reviewer, the audit actor and the determination are all set by the
# workflow, and there is no request shape that can reach them.
#
# There is no deadline default here, and none anywhere else in this surface.
# The contract sets the deadline per request; a default would be a service
# level AGT invented and then reported against.


class PriorityRequestCreate(BaseModel):
    cor_reference: str = Field(min_length=1, max_length=100,
                               description="The COR's own reference for the request")
    target_reference: str = Field(min_length=1, max_length=500,
                                  description="The organisation the COR named: "
                                              "TEFCAID, HCID, NPI, org OID, "
                                              "registry id or exact name")
    issue_description: str = Field(min_length=1, max_length=8000)
    requested_by: str = Field(min_length=1, max_length=255)
    received_at: Optional[datetime] = Field(
        None, description="When the request arrived. Defaults to now.")
    deadline: Optional[datetime] = Field(
        None, description="The deadline the COR stated. Omitted means the COR "
                          "did not state one — it is NOT computed.")
    instructions: Optional[str] = Field(None, max_length=8000)
    qhin: Optional[str] = Field(None, max_length=100)


class PriorityTargetResolve(BaseModel):
    entity_id: uuid.UUID
    rationale: str = Field(min_length=10, max_length=4000)


class PriorityDeadlineAmend(BaseModel):
    deadline: Optional[datetime] = None
    reason: str = Field(min_length=10, max_length=4000)


class PriorityWithdraw(BaseModel):
    reason: str = Field(min_length=10, max_length=4000)


class PriorityFinding(BaseModel):
    severity: str = Field(description="CRITICAL | HIGH | MEDIUM | LOW")
    rationale: str = Field(min_length=10, max_length=4000)
    root_cause_determination: Optional[str] = Field(
        None, max_length=50,
        description="Omit when the root cause was NOT determined. The contract "
                    "asks for it if determined; it is never manufactured.")
    root_cause_description: Optional[str] = Field(None, max_length=8000)
    recommendations: List[Dict[str, Any]] = Field(default_factory=list)
    prevention_recommendation: Optional[str] = Field(None, max_length=8000)
    resolution_notes: Optional[str] = Field(None, max_length=8000)


def _priority_refused(exc) -> HTTPException:
    return HTTPException(409, str(exc))


@router.post("/priority-requests",
             dependencies=[Depends(require_role("program_manager"))])
async def create_priority_request(req: PriorityRequestCreate, request: Request,
                                  db: AsyncSession = Depends(get_db),
                                  user=Depends(require_role("program_manager"))):
    """Log an authorized COR request. Idempotent on (reference, target)."""
    from app.tefca_registry import priority_review as pr

    try:
        result = await pr.receive_request(
            db, cor_reference=req.cor_reference,
            target_reference=req.target_reference,
            issue_description=req.issue_description,
            requested_by=req.requested_by, received_at=req.received_at,
            deadline=req.deadline, instructions=req.instructions, qhin=req.qhin,
            actor=str(getattr(user, "email", "") or ""),
            actor_id=getattr(user, "id", None))
    except pr.PriorityRefused as exc:
        raise _priority_refused(exc)
    await db.commit()
    return result


@router.get("/priority-requests", dependencies=[Depends(require_role("viewer"))])
async def list_priority_requests(include_withdrawn: bool = Query(False),
                                 limit: int = Query(100, ge=1, le=500),
                                 db: AsyncSession = Depends(get_db)):
    from app.tefca_registry import priority_review as pr

    items = await pr.open_requests(db, limit=limit,
                                   include_withdrawn=include_withdrawn)
    return {"count": len(items), "requests": items}


# Registered BEFORE /priority-requests/{case_id}: FastAPI matches in
# registration order, and the literal would otherwise be read as a case id.
@router.get("/priority-requests/workload",
            dependencies=[Depends(require_role("viewer"))])
async def priority_workload(due_soon_within_hours: Optional[float] = Query(
                                None, gt=0,
                                description="Caller-supplied warning band. There "
                                            "is no default: the contract sets no "
                                            "standing turnaround."),
                            db: AsyncSession = Depends(get_db)):
    from app.tefca_registry import priority_review as pr

    return await pr.workload_summary(db, due_soon_within_hours=due_soon_within_hours)


@router.get("/priority-requests/{case_id}",
            dependencies=[Depends(require_role("viewer"))])
async def get_priority_request(case_id: uuid.UUID,
                               db: AsyncSession = Depends(get_db)):
    from app.tefca_registry import priority_review as pr

    try:
        return await pr.get_request(db, case_id)
    except pr.PriorityRefused as exc:
        raise HTTPException(404, str(exc))


@router.get("/priority-requests/{case_id}/package",
            dependencies=[Depends(require_role("viewer"))])
async def get_priority_package(case_id: uuid.UUID,
                               db: AsyncSession = Depends(get_db)):
    """The analyst workspace for one request."""
    from app.tefca_registry import priority_review as pr

    try:
        return await pr.analyst_package(db, case_id)
    except pr.PriorityRefused as exc:
        raise HTTPException(404, str(exc))


@router.get("/priority-requests/{case_id}/result",
            dependencies=[Depends(require_role("viewer"))])
async def get_priority_result(case_id: uuid.UUID,
                              db: AsyncSession = Depends(get_db)):
    """The D5.1 content, withheld until a QA approval stands."""
    from app.tefca_registry import priority_review as pr

    try:
        return await pr.reportable_result(db, case_id)
    except pr.PriorityRefused as exc:
        raise HTTPException(404, str(exc))


@router.get("/priority-requests/{case_id}/deadline-history",
            dependencies=[Depends(require_role("viewer"))])
async def get_priority_deadline_history(case_id: uuid.UUID,
                                        db: AsyncSession = Depends(get_db)):
    from app.tefca_registry import priority_review as pr

    try:
        return await pr.deadline_history(db, case_id)
    except pr.PriorityRefused as exc:
        raise HTTPException(404, str(exc))


@router.post("/priority-requests/{case_id}/resolve-target",
             dependencies=[Depends(require_role("senior_analyst"))])
async def resolve_priority_target(case_id: uuid.UUID, req: PriorityTargetResolve,
                                  db: AsyncSession = Depends(get_db),
                                  user=Depends(require_role("senior_analyst"))):
    """Name the canonical entity an ambiguous or unmatched request meant."""
    from app.tefca_registry import priority_review as pr

    try:
        result = await pr.resolve_target_manually(
            db, case_id, entity_id=req.entity_id, rationale=req.rationale,
            actor=str(getattr(user, "email", "") or ""),
            actor_id=getattr(user, "id", None))
    except pr.PriorityRefused as exc:
        raise _priority_refused(exc)
    await db.commit()
    return result


@router.post("/priority-requests/{case_id}/deadline",
             dependencies=[Depends(require_role("program_manager"))])
async def amend_priority_deadline(case_id: uuid.UUID, req: PriorityDeadlineAmend,
                                  db: AsyncSession = Depends(get_db),
                                  user=Depends(require_role("program_manager"))):
    """Record a deadline the COR changed. The original is kept in the audit trail."""
    from app.tefca_registry import priority_review as pr

    try:
        result = await pr.amend_deadline(
            db, case_id, new_deadline=req.deadline, reason=req.reason,
            actor=str(getattr(user, "email", "") or ""),
            actor_id=getattr(user, "id", None))
    except pr.PriorityRefused as exc:
        raise _priority_refused(exc)
    await db.commit()
    return result


@router.post("/priority-requests/{case_id}/withdraw",
             dependencies=[Depends(require_role("program_manager"))])
async def withdraw_priority_request(case_id: uuid.UUID, req: PriorityWithdraw,
                                    db: AsyncSession = Depends(get_db),
                                    user=Depends(require_role("program_manager"))):
    """Record a COR withdrawal. Nothing is deleted."""
    from app.tefca_registry import priority_review as pr

    try:
        result = await pr.withdraw_request(
            db, case_id, reason=req.reason,
            actor=str(getattr(user, "email", "") or ""),
            actor_id=getattr(user, "id", None))
    except pr.PriorityRefused as exc:
        raise _priority_refused(exc)
    await db.commit()
    return result


@router.post("/priority-requests/{case_id}/finding",
             dependencies=[Depends(require_role("reviewer"))])
async def record_priority_finding(case_id: uuid.UUID, req: PriorityFinding,
                                  request: Request,
                                  db: AsyncSession = Depends(get_db),
                                  user=Depends(require_role("reviewer"))):
    """The analyst's D5.1 content, recorded with its determination event.

    Reportability is NOT set here and cannot be: it is set only by a QA APPROVE
    at `POST /reviews/{review_id}/qa`, by a different person.
    """
    from app.tefca_registry import priority_review as pr
    from app.tefca_registry.case_assignment import AssignmentRefused
    from app.tefca_registry.qa_gate import QaGateRefused

    try:
        result = await pr.record_finding(
            db, case_id, user=user,
            root_cause_determination=req.root_cause_determination,
            root_cause_description=req.root_cause_description,
            severity=req.severity, recommendations=req.recommendations,
            prevention_recommendation=req.prevention_recommendation,
            resolution_notes=req.resolution_notes, rationale=req.rationale,
            ip_address=get_client_ip(request))
    except (pr.PriorityRefused, AssignmentRefused, QaGateRefused) as exc:
        raise _priority_refused(exc)
    await db.commit()
    return result


# ── SUPERVISOR OPERATIONS (Step #15) ─────────────────────────────────────────
#
# A CONTROL PLANE, not another engine. Every endpoint here is a READ derived
# from review_records, review_decision_events, sample_entities and the priority
# requests. The one write a supervisor legitimately owns — assignment — is the
# existing `/reviews/{review_id}/assign` above, where the audit trail and the
# override rule already live. There is deliberately no operations endpoint that
# can record a determination, approve a QA review or set reportability.
#
# Reads sit at the viewer floor like every other TEFCA read
# (`test_rbac_roles::test_no_tefca_read_endpoint_sits_above_the_viewer_floor`).
# The authority is on the writes.


@router.get("/operations/dashboard", dependencies=[Depends(require_role("viewer"))])
async def operations_dashboard(
        queue_source: Optional[str] = Query(
            None, description="Scope the summary to one work source so it "
                              "agrees with the queue being looked at."),
        due_soon_within_hours: Optional[float] = Query(
            None, gt=0, description="Caller-supplied warning band for COR "
                                    "deadlines. No default: the contract sets "
                                    "no standing turnaround."),
        stale_after_days: Optional[float] = Query(
            None, gt=0, description="Caller-supplied internal staleness band. "
                                    "No default: no approved threshold exists."),
        db: AsyncSession = Depends(get_db)):
    """Supervisor summary. Every card counts something that actually exists."""
    from app.tefca_registry.supervisor_ops import dashboard

    return await dashboard(db, queue_source=queue_source,
                           due_soon_within_hours=due_soon_within_hours,
                           stale_after_days=stale_after_days)


@router.get("/operations/work-queue", dependencies=[Depends(require_role("viewer"))])
async def operations_work_queue(
        queue_source: Optional[str] = Query(None),
        work_reason: Optional[str] = Query(
            None, description="HUMAN_REQUIRED | STATISTICAL_SAMPLE | "
                              "PRIORITY_REQUEST | QA_RETURN | QA_ESCALATION"),
        state: Optional[str] = Query(None),
        assignee: Optional[uuid.UUID] = Query(None),
        unassigned_only: bool = Query(False),
        qhin_entity_id: Optional[uuid.UUID] = Query(None),
        limited_only: bool = Query(False),
        reportable: Optional[bool] = Query(None),
        deadline_state: Optional[str] = Query(None),
        search: Optional[str] = Query(None, max_length=200),
        sort: str = Query("age"),
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        due_soon_within_hours: Optional[float] = Query(None, gt=0),
        stale_after_days: Optional[float] = Query(None, gt=0),
        db: AsyncSession = Depends(get_db)):
    """The operational queue: filtered, sorted, paginated, deterministically."""
    from app.tefca_registry.supervisor_ops import SupervisorRefused, work_queue

    try:
        return await work_queue(
            db, queue_source=queue_source, work_reason=work_reason, state=state,
            assignee=assignee, unassigned_only=unassigned_only,
            qhin_entity_id=qhin_entity_id, limited_only=limited_only,
            reportable=reportable, deadline_state=deadline_state, search=search,
            sort=sort, offset=offset, limit=limit,
            due_soon_within_hours=due_soon_within_hours,
            stale_after_days=stale_after_days)
    except SupervisorRefused as exc:
        raise HTTPException(400, str(exc))


@router.get("/operations/analyst-workload",
            dependencies=[Depends(require_role("viewer"))])
async def operations_analyst_workload(
        queue_source: Optional[str] = Query(None),
        stale_after_days: Optional[float] = Query(None, gt=0),
        db: AsyncSession = Depends(get_db)):
    """Open work in hand, per holder. Not a performance measure."""
    from app.tefca_registry.supervisor_ops import analyst_workload

    return await analyst_workload(db, queue_source=queue_source,
                                  stale_after_days=stale_after_days)


@router.get("/operations/qa-workload", dependencies=[Depends(require_role("viewer"))])
async def operations_qa_workload(queue_source: Optional[str] = Query(None),
                                 db: AsyncSession = Depends(get_db)):
    """The independent QA view, kept separate from the analyst queue."""
    from app.tefca_registry.supervisor_ops import qa_workload

    return await qa_workload(db, queue_source=queue_source)


@router.get("/operations/sampling", dependencies=[Depends(require_role("viewer"))])
async def operations_sampling(db: AsyncSession = Depends(get_db)):
    """Official sampling progress, or an honest statement that none exists."""
    from app.tefca_registry.supervisor_ops import sampling_overview

    return await sampling_overview(db)


@router.get("/operations/priority", dependencies=[Depends(require_role("viewer"))])
async def operations_priority(
        due_soon_within_hours: Optional[float] = Query(None, gt=0),
        db: AsyncSession = Depends(get_db)):
    """Task 5 workload against the deadlines the COR actually set."""
    from app.tefca_registry.supervisor_ops import priority_overview

    return await priority_overview(db, due_soon_within_hours=due_soon_within_hours)


@router.get("/operations/readiness", dependencies=[Depends(require_role("viewer"))])
async def operations_readiness(db: AsyncSession = Depends(get_db)):
    """Aggregate readiness. Reports DQ findings and cases SEPARATELY."""
    from app.tefca_registry.supervisor_ops import government_readiness

    return await government_readiness(db)


# Registered AFTER the literal /operations/* paths above, so none of them is
# swallowed as a review_id. FastAPI matches in registration order.
@router.get("/operations/cases/{review_id}",
            dependencies=[Depends(require_role("viewer"))])
async def operations_case_detail(
        review_id: str,
        due_soon_within_hours: Optional[float] = Query(None, gt=0),
        stale_after_days: Optional[float] = Query(None, gt=0),
        db: AsyncSession = Depends(get_db)):
    """One case, as a supervisor needs to see it. No evidence values."""
    from app.tefca_registry.supervisor_ops import SupervisorRefused, case_detail

    try:
        return await case_detail(db, review_id,
                                 due_soon_within_hours=due_soon_within_hours,
                                 stale_after_days=stale_after_days)
    except SupervisorRefused as exc:
        raise HTTPException(404, str(exc))


@router.get("/operations/cases/{review_id}/timeline",
            dependencies=[Depends(require_role("viewer"))])
async def operations_case_timeline(review_id: str,
                                   db: AsyncSession = Depends(get_db)):
    """What actually happened to this case. Nothing is inferred or filled in."""
    from app.tefca_registry.supervisor_ops import (SupervisorRefused,
                                                   audit_timeline)

    try:
        return {"review_id": review_id,
                "timeline": await audit_timeline(db, review_id)}
    except SupervisorRefused as exc:
        raise HTTPException(404, str(exc))


@router.get("/entities/{entity_id}/verification-coverage",
            dependencies=[Depends(require_role("viewer"))],
            summary="Control-based verification coverage for one entity")
async def entity_verification_coverage(
    entity_id: str,
    db: AsyncSession = Depends(get_db),
):
    """What the evidence methodology says about one entity. READS ONLY.

    Replaces "N of M sources agree" as the headline. Source agreement is still
    returned, but inside each control, where it is supporting evidence rather
    than a determination — three databases answering three different questions
    were never three votes on one.

    The response separates two things the UI must never merge:

        entity_verification    did we establish who this organisation is?
        contractual_compliance does the requirement appear satisfied?

    `contractual_compliance` can never be NON_COMPLIANT here. That value is
    reachable only through an analyst determination an independent QA reviewer
    has approved.
    """
    from app.tefca_registry.verification_coverage_service import (
        coverage_for_entity)

    try:
        assessment = await coverage_for_entity(db, entity_id)
    except Exception as exc:  # noqa: BLE001 - a malformed id is a 404, not a 500
        logger.info("verification coverage lookup failed for %r: %s",
                    entity_id, exc)
        assessment = None

    if assessment is None:
        raise HTTPException(404, "No such entity.")
    return assessment.to_dict()
