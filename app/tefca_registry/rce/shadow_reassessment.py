"""Shadow reassessment — re-classify PERSISTED evidence under a candidate
rule set, record every delta, bind approvals to the exact package, and
design successor publication without ever applying it officially.

WHAT IS PINNED
──────────────
A comparison pins, at build time:
    * the BASELINE rule-set version and the CANDIDATE (a stored version, or
      a local what-if rule list hashed into the package)
    * the EVALUATION DATE
    * the DELIVERY (intake)
    * the EVIDENCE: every official review record compared, its evidence
      generation stamp, and the source snapshots in force for the delivery
      — references only, never values
    * the OFFICIAL BASELINE HASH: the classification state of those records
      at build time, so any later change makes the package STALE

WHAT IS RE-RUN, AND WHAT IS NOT
───────────────────────────────
ONLY the classifier (`BucketClassifier.classify`) runs, over the
`classifier_input` snapshot each official review record already carries.
No connector is called, no evidence is re-assembled, no row in
`review_records` / `rce_issues` / `tefca_dimension_evidence` is written.
The baseline rules are re-run too, as a REPRODUCIBILITY check: if the
baseline rule set does not reproduce the official bucket from the persisted
input, that entity is recorded as NOT_REPRODUCIBLE and held for a human —
it is never silently compared.

DELTA VOCABULARY
────────────────
    NEW         baseline found no discrepancy (B1), candidate finds one
                — no predecessor FINDING exists; the predecessor review
                record is linked when there is one, and may be NULL
    REMOVED     baseline found a discrepancy, candidate finds none
    CHANGED     both find a discrepancy, but a different bucket or rule
    UNCHANGED   same bucket and rule
    NOT_REPRODUCIBLE  the baseline could not be reproduced; see above

Every delta carries a DIRECTION — STRICTER / MORE_PERMISSIVE / NEUTRAL —
by bucket severity (B1 < B2 < B3 < B4), so both kinds of change are
reviewed for accuracy, not only the lenient ones. A delta that touches an
EIN / FEIN / IRS signal is flagged `manual_review_required` and is never
auto-superseded.

APPROVALS AND PUBLICATION
─────────────────────────
Analyst and independent-QA approvals bind to `package_hash`. A different
person must give each one (409, segregation of duties — same wording
family as `qa_gate.submit_qa_review`). A stale baseline refuses both
approval and publication.

Publication writes SUCCESSOR review records that name their predecessor,
and an event per attempt (refused or not), keyed on the package hash so a
retry is idempotent. It runs ONLY when SHADOW_PUBLICATION_MODE=local_test;
in every other mode it writes a REFUSED event and raises. Official
application of successor findings remains outside this code's
authorization by construction, not by convention.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select

from app.core import request_context
from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg
from app.tefca_registry.bucket_classifier import BucketClassifier
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce import snapshot_models as sm

logger = logging.getLogger(__name__)

PUBLICATION_MODE_ENV = "SHADOW_PUBLICATION_MODE"
LOCAL_TEST_MODE = "local_test"
SHADOW_VERSION = "1.0.0"

#: Severity order for the direction of a change.
BUCKET_SEVERITY = {"B1": 0, "B2": 1, "B3": 2, "B4": 3}

#: Any signal name that mentions one of these is an EIN / FEIN / IRS signal.
_EIN_MARKERS = ("ein", "fein", "irs", "tax_id", "taxid")


class ShadowRefused(RuntimeError):
    """A refusal with a precise reason. Routes turn it into a 409."""


# ── helpers ──────────────────────────────────────────────────────────────────

def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _rule_dicts(rows) -> List[Dict[str, Any]]:
    return [{
        "rule_code": r.rule_code, "name": r.name, "bucket": r.bucket,
        "priority": r.priority, "conditions": r.conditions or {},
        "description": r.description, "version": r.version,
    } for r in rows]


def rules_hash(rules: List[Dict[str, Any]]) -> str:
    return _sha([{k: r.get(k) for k in ("rule_code", "bucket", "priority",
                                        "conditions", "version")}
                 for r in sorted(rules, key=lambda r: r.get("rule_code", ""))])


async def rules_for_version(db, version: int) -> List[Dict[str, Any]]:
    """Every rule row at exactly this version, retired or not. A retired
    version is precisely what a historical baseline is."""
    rows = (await db.execute(
        select(reg.ReviewRule).where(reg.ReviewRule.version == version)
        .order_by(reg.ReviewRule.rule_code))).scalars().all()
    if not rows:
        raise ShadowRefused(f"no review rules exist at version {version}")
    return _rule_dicts(rows)


def classifier_input(verification_results: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The persisted classifier input of a review record.

    The RCE/delivery path stores it under `classifier_input`; the manual
    single-entity path stores `sources` / `fields` / `confidence_score` at
    the top level. Either shape is accepted; anything else is None (not
    reproducible — nothing to re-classify)."""
    vr = verification_results or {}
    if isinstance(vr.get("classifier_input"), dict):
        return vr["classifier_input"]
    if isinstance(vr.get("sources"), dict):
        return {"sources": vr.get("sources"), "fields": vr.get("fields") or {},
                "confidence_score": vr.get("confidence_score")}
    return None


def _touches_ein(inp: Dict[str, Any], *rule_sets: List[Dict[str, Any]]) -> bool:
    names = set((inp.get("sources") or {}).keys()) | set((inp.get("fields") or {}).keys())
    for rules in rule_sets:
        for r in rules:
            for clause in (r.get("conditions") or {}).values():
                for c in (clause if isinstance(clause, list) else []):
                    if isinstance(c, dict):
                        names.add(str(c.get("source") or c.get("field") or ""))
                    else:
                        names.add(str(c))
    return any(any(marker in n.lower() for marker in _EIN_MARKERS) for n in names)


def source_states(inp: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per-source classifier state AND the persisted disposition behind it.

    WHY THE DISPOSITION IS CARRIED, NOT JUST THE STATE: `arc_pipeline.
    _DISPOSITION_TO_STATE` maps BOTH `NOT_FOUND` (searched, nothing listed --
    a clean screen) and `REVIEW` (a pending hit awaiting confirmation) to the
    single classifier state `not_found`. A rule written against `not_found`
    therefore fires on both. A reviewer reading a delta must be able to see
    WHICH of the two produced the state; the state alone cannot say. Where
    the persisted input carries no disposition (the manual path, or a
    hand-built input), the origin is reported as unknown rather than guessed.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name, raw in (inp.get("sources") or {}).items():
        if isinstance(raw, dict):
            status = raw.get("status")
            disposition = raw.get("disposition")
            out[name] = {"status": status, "disposition": disposition,
                         "dimension": raw.get("dimension"),
                         "not_found_origin": (
                             None if status != "not_found" else
                             "clean_screen_NOT_FOUND" if disposition == "NOT_FOUND" else
                             "pending_hit_REVIEW" if disposition == "REVIEW" else
                             "CONFLICT" if disposition == "CONFLICT" else
                             "unknown_disposition_not_persisted")}
        else:
            out[name] = {"status": raw, "disposition": None, "dimension": None,
                         "not_found_origin": ("unknown_disposition_not_persisted"
                                              if raw == "not_found" else None)}
    return out


def _direction(baseline_bucket: Optional[str], candidate_bucket: Optional[str]) -> str:
    b = BUCKET_SEVERITY.get(baseline_bucket or "", -1)
    c = BUCKET_SEVERITY.get(candidate_bucket or "", -1)
    if c > b:
        return pm.DIR_STRICTER
    if c < b:
        return pm.DIR_MORE_PERMISSIVE
    return pm.DIR_NEUTRAL


async def official_records(db, intake_id) -> List[Tuple[reg.ReviewRecord, uuid.UUID]]:
    """The latest classified review record per promoted entity of a delivery."""
    entity_ids = [e for (e,) in (await db.execute(
        select(m.RceCuratedRecord.canonical_entity_id)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.canonical_entity_id.isnot(None))
        .distinct())).all()]
    out: List[Tuple[reg.ReviewRecord, uuid.UUID]] = []
    if not entity_ids:
        return out
    rows = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.entity_id.in_(entity_ids),
               reg.ReviewRecord.classification_bucket.isnot(None))
        .order_by(reg.ReviewRecord.entity_id, reg.ReviewRecord.created_at.desc(),
                  reg.ReviewRecord.review_id.desc()))).scalars().all()
    seen = set()
    for r in rows:
        if r.entity_id in seen:
            continue
        seen.add(r.entity_id)
        out.append((r, r.entity_id))
    return out


def official_state_hash(records: List[Tuple[reg.ReviewRecord, uuid.UUID]]) -> str:
    return _sha(sorted([
        str(r.id), r.review_id, r.classification_bucket, r.classification_rule,
        r.classification_rule_version, r.reviewer_resolution, r.reclassified_to,
        str(r.reportable_at),
    ] for r, _ in records))


async def _source_snapshot_refs(db, intake_id) -> List[Dict[str, Any]]:
    rows = (await db.execute(
        select(sm.SourceSnapshot).where(sm.SourceSnapshot.intake_id == intake_id)
        .order_by(sm.SourceSnapshot.created_at))).scalars().all()
    return [{"snapshot_id": str(s.id), "source_system": s.source_system,
             "status": s.status, "sha256": s.sha256,
             "received_at": s.received_at.isoformat() if s.received_at else None}
            for s in rows]


# ── build ────────────────────────────────────────────────────────────────────

async def build_comparison(db, intake_id, *, built_by: str,
                           baseline_rule_version: Optional[int] = None,
                           candidate_rule_version: Optional[int] = None,
                           candidate_rules: Optional[List[Dict[str, Any]]] = None,
                           evaluation_date: Optional[date] = None) -> Dict[str, Any]:
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise ShadowRefused(f"no intake {intake_id}")
    if (candidate_rule_version is None) == (candidate_rules is None):
        raise ShadowRefused("exactly one of candidate_rule_version or candidate_rules is required")

    records = await official_records(db, intake.id)
    if not records:
        raise ShadowRefused("the delivery has no classified official review records to compare")

    versions = sorted({r.classification_rule_version for r, _ in records
                       if r.classification_rule_version})
    if baseline_rule_version is None:
        if len(versions) != 1:
            raise ShadowRefused(
                f"official records span rule versions {versions}; name "
                f"baseline_rule_version explicitly")
        baseline_rule_version = versions[0]

    baseline_rules = await rules_for_version(db, baseline_rule_version)
    if candidate_rules is None:
        cand_rules = await rules_for_version(db, candidate_rule_version)
        cand_version: Optional[int] = candidate_rule_version
    else:
        cand_rules = [dict(r) for r in candidate_rules]
        for r in cand_rules:
            r.setdefault("version", 0)
            if not {"rule_code", "bucket", "priority", "conditions"} <= set(r):
                raise ShadowRefused("each candidate rule needs rule_code, bucket, priority, conditions")
        cand_version = None
    cand_hash = rules_hash(cand_rules)
    eval_date = evaluation_date or date.today()
    classifier = BucketClassifier()

    deltas: List[Dict[str, Any]] = []
    pins: List[Dict[str, Any]] = []
    counts = {k: 0 for k in pm.DELTA_KINDS}
    directions = {k: 0 for k in pm.DIRECTIONS}
    manual = 0
    for record, entity_id in records:
        vr = record.verification_results or {}
        pins.append({"review_record_id": str(record.id), "review_id": record.review_id,
                     "entity_id": str(entity_id),
                     "official_rule_version": record.classification_rule_version,
                     "evidence_generation_timestamp": vr.get("generation_timestamp"),
                     "resolution_source": vr.get("resolution_source")})
        inp = classifier_input(vr)
        base_bucket, base_rule, base_ver = (record.classification_bucket,
                                            record.classification_rule,
                                            record.classification_rule_version)
        detail: Dict[str, Any] = {"predecessor_finding_id": None}
        if inp is None:
            kind, direction = pm.DELTA_NOT_REPRODUCIBLE, pm.DIR_NEUTRAL
            cand_bucket = cand_rule = None
            cand_rv = None
            reason = ("no persisted classifier input on the official review record; "
                      "nothing can be re-classified without re-assembling evidence, "
                      "which this comparison refuses to do")
            needs_manual = True
        else:
            base = classifier.classify(inp, rules=baseline_rules)
            reproduced = base.bucket == base_bucket
            cand = classifier.classify(inp, rules=cand_rules)
            cand_bucket, cand_rule, cand_rv = cand.bucket, cand.rule_code, cand.rule_version
            detail["source_states"] = source_states(inp)
            detail["field_signals"] = sorted((inp.get("fields") or {}).keys())
            detail.update({"baseline_reclassified_bucket": base.bucket,
                           "baseline_reclassified_rule": base.rule_code,
                           "baseline_reproduced": reproduced,
                           "candidate_matched_conditions": cand.matched_conditions,
                           "candidate_evaluated_rules": cand.evaluated_rules})
            needs_manual = _touches_ein(inp, baseline_rules, cand_rules)
            if not reproduced:
                kind, direction = pm.DELTA_NOT_REPRODUCIBLE, pm.DIR_NEUTRAL
                reason = (f"baseline rules v{baseline_rule_version} re-classify the persisted "
                          f"input as {base.bucket}, but the official record says "
                          f"{base_bucket} (v{base_ver}); the comparison is not like-for-like "
                          f"and is held for a human")
                needs_manual = True
            else:
                base_finding = base_bucket != "B1"
                cand_finding = cand_bucket != "B1"
                if not base_finding and cand_finding:
                    kind = pm.DELTA_NEW
                    detail["predecessor_finding_id"] = None   # no predecessor finding exists
                elif base_finding and not cand_finding:
                    kind = pm.DELTA_REMOVED
                elif base_finding and cand_finding and (
                        cand_bucket != base_bucket or cand_rule != base_rule):
                    kind = pm.DELTA_CHANGED
                else:
                    kind = pm.DELTA_UNCHANGED
                    if cand_rule != base_rule:
                        detail["rule_changed_within_bucket"] = True
                direction = _direction(base_bucket, cand_bucket)
                if kind == pm.DELTA_UNCHANGED:
                    needs_manual = False
                reason = (f"baseline {base_bucket}/{base_rule} v{base_ver} -> candidate "
                          f"{cand_bucket}/{cand_rule or 'DEFAULT-UNMATCHED'}; "
                          + ("; ".join(cand.matched_conditions) or cand.rationale))
                if needs_manual:
                    reason += " [EIN/FEIN/IRS signal involved: manual review required, never auto-superseded]"
        counts[kind] += 1
        directions[direction] += 1
        manual += int(needs_manual)
        deltas.append({
            "entity_id": entity_id, "predecessor_review_record_id": record.id,
            "predecessor_review_id": record.review_id,
            "delta_kind": kind, "direction": direction,
            "baseline_bucket": base_bucket, "baseline_rule": base_rule,
            "baseline_rule_version": base_ver,
            "candidate_bucket": cand_bucket, "candidate_rule": cand_rule,
            "candidate_rule_version": cand_rv, "reason": reason,
            "manual_review_required": needs_manual, "detail": detail,
        })

    official_hash = official_state_hash(records)
    package = {
        "shadow_version": SHADOW_VERSION, "intake_id": str(intake.id),
        "baseline_rule_version": baseline_rule_version,
        "baseline_rules_hash": rules_hash(baseline_rules),
        "candidate_rule_version": cand_version, "candidate_rules_hash": cand_hash,
        "evaluation_date": eval_date.isoformat(), "official_baseline_hash": official_hash,
        "pins": sorted(pins, key=lambda p: p["review_record_id"]),
        "deltas": sorted([{
            "entity_id": str(d["entity_id"]), "predecessor_review_id": d["predecessor_review_id"],
            "delta_kind": d["delta_kind"], "direction": d["direction"],
            "baseline": [d["baseline_bucket"], d["baseline_rule"], d["baseline_rule_version"]],
            "candidate": [d["candidate_bucket"], d["candidate_rule"], d["candidate_rule_version"]],
            "manual_review_required": d["manual_review_required"],
        } for d in deltas], key=lambda d: d["entity_id"]),
    }
    package_hash = _sha(package)
    summary = {"records_compared": len(records), "by_kind": counts,
               "by_direction": directions, "manual_review_required": manual,
               "baseline_rules_hash": package["baseline_rules_hash"],
               "candidate_rules_hash": cand_hash,
               "connector_calls_made": 0,
               "official_rows_written": 0}
    now = datetime.now(timezone.utc)
    cmp_row = pm.RceShadowComparison(
        intake_id=intake.id, baseline_rule_version=baseline_rule_version,
        candidate_rule_version=cand_version,
        candidate_rules=cand_rules if candidate_rules is not None else None,
        candidate_rules_hash=cand_hash, evaluation_date=eval_date,
        evidence_pinned={"review_records": pins,
                         "source_snapshots": await _source_snapshot_refs(db, intake.id),
                         "evidence_generation_stamps": sorted({
                             p["evidence_generation_timestamp"] for p in pins
                             if p["evidence_generation_timestamp"]})},
        official_baseline_hash=official_hash, package_hash=package_hash,
        summary=summary, built_by=built_by[:320], built_at=now,
        correlation_id=request_context.correlation_id()[:64],
        build_sha=request_context.build_sha())
    db.add(cmp_row)
    await db.flush()
    await db.execute(pm.RceShadowFindingDelta.__table__.insert(), [
        {**d, "comparison_id": cmp_row.id, "created_at": now} for d in deltas])
    await db.commit()
    return await comparison_dto(db, cmp_row)


# ── read ─────────────────────────────────────────────────────────────────────

async def _comparison_or_refuse(db, comparison_id) -> pm.RceShadowComparison:
    row = await db.get(pm.RceShadowComparison, comparison_id)
    if row is None:
        raise ShadowRefused(f"no shadow comparison {comparison_id}")
    return row


async def is_stale(db, cmp_row: pm.RceShadowComparison) -> bool:
    records = await official_records(db, cmp_row.intake_id)
    return official_state_hash(records) != cmp_row.official_baseline_hash


async def _approvals(db, cmp_row) -> List[pm.RceShadowApproval]:
    return (await db.execute(
        select(pm.RceShadowApproval)
        .where(pm.RceShadowApproval.comparison_id == cmp_row.id,
               pm.RceShadowApproval.package_hash == cmp_row.package_hash)
        .order_by(pm.RceShadowApproval.approved_at))).scalars().all()


async def _events(db, cmp_row) -> List[pm.RceSuccessorPublicationEvent]:
    return (await db.execute(
        select(pm.RceSuccessorPublicationEvent)
        .where(pm.RceSuccessorPublicationEvent.comparison_id == cmp_row.id)
        .order_by(pm.RceSuccessorPublicationEvent.created_at))).scalars().all()


async def comparison_dto(db, cmp_row) -> Dict[str, Any]:
    approvals = await _approvals(db, cmp_row)
    events = await _events(db, cmp_row)
    return {
        "comparison_id": str(cmp_row.id), "intake_id": str(cmp_row.intake_id),
        "baseline_rule_version": cmp_row.baseline_rule_version,
        "candidate_rule_version": cmp_row.candidate_rule_version,
        "candidate_rules_hash": cmp_row.candidate_rules_hash,
        "candidate_is_what_if": cmp_row.candidate_rules is not None,
        "evaluation_date": cmp_row.evaluation_date.isoformat(),
        "evidence_pinned": cmp_row.evidence_pinned,
        "official_baseline_hash": cmp_row.official_baseline_hash,
        "package_hash": cmp_row.package_hash,
        "stale": await is_stale(db, cmp_row),
        "summary": cmp_row.summary,
        "approvals": [{"approval_role": a.approval_role, "actor_email": a.actor_email,
                       "actor_role": a.actor_role, "package_hash": a.package_hash,
                       "approved_at": a.approved_at.isoformat()} for a in approvals],
        "publication_events": [{"event_type": e.event_type, "mode": e.mode,
                                "reason": e.reason, "actor": e.actor,
                                "successor_review_ids": e.successor_review_ids,
                                "created_at": e.created_at.isoformat()} for e in events],
        "publication_mode": os.getenv(PUBLICATION_MODE_ENV, "") or "unset",
        "built_by": cmp_row.built_by, "built_at": cmp_row.built_at.isoformat(),
        "correlation_id": cmp_row.correlation_id, "build_sha": cmp_row.build_sha,
        "official_findings_modified": False,
    }


async def list_deltas(db, comparison_id, *, kind: Optional[str] = None,
                      direction: Optional[str] = None, limit: int = 100,
                      offset: int = 0) -> Dict[str, Any]:
    cmp_row = await _comparison_or_refuse(db, comparison_id)
    stmt = select(pm.RceShadowFindingDelta).where(
        pm.RceShadowFindingDelta.comparison_id == cmp_row.id)
    if kind:
        if kind not in pm.DELTA_KINDS:
            raise ValueError(f"kind must be one of {pm.DELTA_KINDS}")
        stmt = stmt.where(pm.RceShadowFindingDelta.delta_kind == kind)
    if direction:
        if direction not in pm.DIRECTIONS:
            raise ValueError(f"direction must be one of {pm.DIRECTIONS}")
        stmt = stmt.where(pm.RceShadowFindingDelta.direction == direction)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (await db.execute(stmt.order_by(pm.RceShadowFindingDelta.delta_kind,
                                           pm.RceShadowFindingDelta.entity_id)
                             .limit(limit).offset(offset))).scalars().all()
    return {"comparison_id": str(cmp_row.id), "total": total, "count": len(rows),
            "limit": limit, "offset": offset,
            "items": [{
                "id": str(r.id), "entity_id": str(r.entity_id) if r.entity_id else None,
                "predecessor_review_record_id": (str(r.predecessor_review_record_id)
                                                 if r.predecessor_review_record_id else None),
                "predecessor_review_id": r.predecessor_review_id,
                "delta_kind": r.delta_kind, "direction": r.direction,
                "baseline": {"bucket": r.baseline_bucket, "rule": r.baseline_rule,
                             "rule_version": r.baseline_rule_version},
                "candidate": {"bucket": r.candidate_bucket, "rule": r.candidate_rule,
                              "rule_version": r.candidate_rule_version},
                "reason": r.reason, "manual_review_required": r.manual_review_required,
                "detail": r.detail,
            } for r in rows]}


# ── approvals ────────────────────────────────────────────────────────────────

async def record_approval(db, comparison_id, *, approval_role: str, user,
                          package_hash: str, rationale: str,
                          ip_address: Optional[str] = None) -> Dict[str, Any]:
    if approval_role not in pm.APPROVAL_ROLES:
        raise ShadowRefused(f"approval_role must be one of {pm.APPROVAL_ROLES}")
    if not (rationale or "").strip():
        raise ShadowRefused("rationale is required")
    cmp_row = await _comparison_or_refuse(db, comparison_id)
    if package_hash != cmp_row.package_hash:
        raise ShadowRefused(
            f"package hash mismatch: the approval names {package_hash[:12]}... but the "
            f"comparison's package is {cmp_row.package_hash[:12]}...; approvals bind to "
            f"the exact package reviewed")
    if await is_stale(db, cmp_row):
        raise ShadowRefused(
            "stale baseline: an official review record pinned by this comparison has "
            "changed since the package was built; rebuild the comparison")
    actor_id, actor_email = reg_audit.actor_of(user)
    actor_role = str(getattr(user, "role", "") or "")
    existing = await _approvals(db, cmp_row)
    for a in existing:
        if a.approval_role == approval_role:
            raise ShadowRefused(
                f"{approval_role} approval already stands for package "
                f"{cmp_row.package_hash[:12]}... (by {a.actor_email})")
        same_person = ((actor_id is not None and a.actor_id == actor_id)
                       or (actor_email and a.actor_email == actor_email))
        if same_person:
            raise ShadowRefused(
                f"segregation of duties: {actor_email} made the {a.approval_role} approval on "
                f"comparison {cmp_row.id} and may not give the {approval_role} approval. "
                f"Analyst and independent QA must be different people.")
    row = pm.RceShadowApproval(
        comparison_id=cmp_row.id, package_hash=cmp_row.package_hash,
        approval_role=approval_role, actor_id=actor_id,
        actor_email=(actor_email or "unknown")[:320], actor_role=actor_role[:64],
        rationale=rationale.strip(), correlation_id=request_context.correlation_id()[:64])
    db.add(row)
    reg_audit.record(db, "shadow_comparison_approved", None, actor_id=actor_id,
                     actor_email=actor_email, ip_address=ip_address,
                     metadata={"comparison_id": str(cmp_row.id),
                               "package_hash": cmp_row.package_hash,
                               "approval_role": approval_role})
    await db.commit()
    return await comparison_dto(db, cmp_row)


# ── successor publication (local test mode only) ─────────────────────────────

def publication_mode() -> str:
    return os.getenv(PUBLICATION_MODE_ENV, "").strip()


async def _event(db, cmp_row, *, event_type: str, mode: str, reason: str, actor: str,
                 predecessors=None, successors=None) -> None:
    db.add(pm.RceSuccessorPublicationEvent(
        comparison_id=cmp_row.id, package_hash=cmp_row.package_hash,
        event_type=event_type, mode=(mode or "unset")[:24], reason=reason,
        predecessor_review_ids=predecessors or [], successor_review_ids=successors or [],
        actor=actor[:320], correlation_id=request_context.correlation_id()[:64]))


async def publish_successors(db, comparison_id, *, user,
                             ip_address: Optional[str] = None) -> Dict[str, Any]:
    """Write successor review records for every actionable delta — ONLY in
    local test mode. Every other outcome is a REFUSED event plus a raise."""
    from app.tefca_registry.rce.arc_pipeline import (
        _allocate_review_id, _lock_review_id_allocation)

    cmp_row = await _comparison_or_refuse(db, comparison_id)
    actor_id, actor_email = reg_audit.actor_of(user)
    actor = actor_email or "unknown"
    mode = publication_mode()

    async def refuse(reason: str):
        await _event(db, cmp_row, event_type=pm.PUB_REFUSED, mode=mode, reason=reason,
                     actor=actor)
        await db.commit()
        raise ShadowRefused(reason)

    if mode != LOCAL_TEST_MODE:
        await refuse(
            f"official application of successor findings is outside this code's "
            f"authorization: {PUBLICATION_MODE_ENV} is {mode or 'unset'!r}, not "
            f"{LOCAL_TEST_MODE!r}. Nothing was written to review_records.")
    # Idempotent retry, checked BEFORE approvals and staleness: the successors
    # written by a completed publication are themselves newer official rows,
    # so the package is stale by construction afterwards -- a retry must
    # return the same result, never a refusal.
    for e in await _events(db, cmp_row):
        if e.event_type == pm.PUB_PUBLISHED and e.package_hash == cmp_row.package_hash:
            await _event(db, cmp_row, event_type=pm.PUB_ALREADY_PUBLISHED, mode=mode,
                         reason="retry of an already-published package; no new rows written",
                         actor=actor, predecessors=e.predecessor_review_ids,
                         successors=e.successor_review_ids)
            await db.commit()
            return {"comparison_id": str(cmp_row.id), "package_hash": cmp_row.package_hash,
                    "already_published": True, "mode": mode,
                    "successor_review_ids": e.successor_review_ids,
                    "predecessor_review_ids": e.predecessor_review_ids, "withheld": [],
                    "predecessors_modified": False}

    approvals = {a.approval_role for a in await _approvals(db, cmp_row)}
    missing = [r for r in pm.APPROVAL_ROLES if r not in approvals]
    if missing:
        await refuse(f"publication requires both approvals bound to package "
                     f"{cmp_row.package_hash[:12]}...; missing: {', '.join(missing)}")
    if await is_stale(db, cmp_row):
        await refuse("stale baseline: an official review record pinned by this comparison "
                     "has changed since the approvals were given; rebuild and re-approve")

    deltas = (await db.execute(
        select(pm.RceShadowFindingDelta)
        .where(pm.RceShadowFindingDelta.comparison_id == cmp_row.id)
        .order_by(pm.RceShadowFindingDelta.entity_id))).scalars().all()

    await _lock_review_id_allocation(db)
    predecessors: List[str] = []
    successors: List[str] = []
    withheld: List[Dict[str, Any]] = []
    for d in deltas:
        if d.delta_kind == pm.DELTA_UNCHANGED:
            continue
        if d.delta_kind == pm.DELTA_NOT_REPRODUCIBLE or d.manual_review_required:
            withheld.append({"entity_id": str(d.entity_id), "delta_kind": d.delta_kind,
                             "reason": ("manual review required; never auto-superseded"
                                        if d.manual_review_required else d.reason)})
            continue
        pred = (await db.get(reg.ReviewRecord, d.predecessor_review_record_id)
                if d.predecessor_review_record_id else None)
        review_id = await _allocate_review_id(db)
        vr = dict((pred.verification_results or {}) if pred else {})
        vr["shadow_successor"] = {
            "comparison_id": str(cmp_row.id), "package_hash": cmp_row.package_hash,
            "candidate_rules_hash": cmp_row.candidate_rules_hash,
            "predecessor_review_id": d.predecessor_review_id,
            "delta_kind": d.delta_kind, "direction": d.direction,
            "publication_mode": mode,
        }
        db.add(reg.ReviewRecord(
            id=uuid.uuid4(), review_id=review_id, entity_id=d.entity_id,
            source_record_id=pred.source_record_id if pred else None,
            verification_results=vr,
            classification_bucket=d.candidate_bucket,
            classification_rule=d.candidate_rule or "DEFAULT-UNMATCHED",
            classification_rule_version=d.candidate_rule_version or 0,
            classification_rationale=(
                f"[SHADOW-SUCCESSOR of {d.predecessor_review_id or 'no predecessor'} "
                f"via comparison {cmp_row.id}, {mode}] {d.reason}")))
        if d.predecessor_review_id:
            predecessors.append(d.predecessor_review_id)
        successors.append(review_id)

    await _event(db, cmp_row, event_type=pm.PUB_PUBLISHED, mode=mode,
                 reason=f"{len(successors)} successor review record(s) written in "
                        f"{mode}; {len(withheld)} withheld for manual review",
                 actor=actor, predecessors=predecessors, successors=successors)
    reg_audit.record(db, "shadow_successors_published", None, actor_id=actor_id,
                     actor_email=actor_email, ip_address=ip_address,
                     metadata={"comparison_id": str(cmp_row.id), "mode": mode,
                               "package_hash": cmp_row.package_hash,
                               "successors": successors, "withheld": len(withheld)})
    await db.commit()
    return {"comparison_id": str(cmp_row.id), "package_hash": cmp_row.package_hash,
            "already_published": False, "mode": mode,
            "successor_review_ids": successors, "predecessor_review_ids": predecessors,
            "withheld": withheld,
            "predecessors_modified": False}
