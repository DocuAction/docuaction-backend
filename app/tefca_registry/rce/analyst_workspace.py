"""Consolidated analyst workspace — one read model per entity, built from
what is already persisted. Nothing here decides anything.

WHAT ONE WORKSPACE ENTRY HOLDS
──────────────────────────────
For one promoted entity of one delivery:
    findings       the quality issues on its delivered line (current run),
                   the verification-stage issues (NPI-005/006/009), and its
                   latest review record (bucket / rule / version / state)
    evidence       the persisted D1-D6 dimension evidence, latest generation,
                   each item stamped with its age and marked REUSED_FRESH or
                   STALE against a configurable window — so an analyst reuses
                   fresh evidence instead of asking for it again, and sees
                   plainly when it is too old to reuse
    requirement    for every finding, the GOVERNING requirement: the quality
                   rule id + version + description, the field map's
                   `documented` text (which says "no spec text in hand" when
                   that is the truth), and for the classification the
                   review-rule row that produced it
    questions      the precise, unanswered question(s) a human must resolve,
                   templated from the finding type — never a verdict
    context        patterns that deserve CONTEXT, never automatic rejection:
                   related organisations (a shared TEFCAID family), a mixed
                   business model (several exchange-purpose classes), a
                   possibly virtual-care organisation
    assessments    Epic's-lessons slots — traffic volume, exchange balance,
                   geographic plausibility — that read ONLY an AUTHORIZED
                   traffic-evidence input. No such persisted record type
                   exists today, so every slot reports
                   `assessment: unavailable, reason: authorized traffic
                   evidence not provided`. These slots do not, and must not,
                   compute anything from registry or delivery data: that
                   would assert a traffic-monitoring obligation the contract
                   does not contain. Contract scope is not expanded here.

RECURRING CAUSES ARE GROUPED, DISPOSITIONS ARE NOT
──────────────────────────────────────────────────
Findings with the same (rule, issue type, field) across a delivery are
grouped so the CAUSE is investigated once (`cause_groups`, with a
representative entity). Every entity still keeps its OWN disposition and its
OWN independent QA — the workspace links to the existing disposition and
determination routes and never bypasses them.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import func, select

from app.Tefca.models import TEFCADimensionEvidence
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import run_selection
from app.tefca_registry.rce.exception_ledger import stage_for
from app.tefca_registry.rce.field_map import FIELD_BY_NAME
from app.tefca_registry.rce.quality_rules import RULE_BY_ID

logger = logging.getLogger(__name__)

WORKSPACE_VERSION = "1.0.0"
FRESHNESS_ENV = "WORKSPACE_EVIDENCE_FRESHNESS_DAYS"
DEFAULT_FRESHNESS_DAYS = 30

REUSED_FRESH = "REUSED_FRESH"
STALE = "STALE"
UNKNOWN_AGE = "UNKNOWN_AGE"

ASSESSMENT_UNAVAILABLE = "unavailable"
NO_TRAFFIC_EVIDENCE_REASON = "authorized traffic evidence not provided"

#: Exchange-purpose token prefixes grouped into business classes. Used only
#: to SURFACE a mixed model as context; never to reject.
_PURPOSE_CLASSES = {
    "T-TRTMNT": "treatment", "T-TREAT": "treatment",
    "T-PYMNT": "payment", "T-HCO": "health_care_operations",
    "T-PH": "public_health", "T-PH-ECR": "public_health", "T-PH-ELR": "public_health",
    "T-GOVDTRM": "government_benefits", "T-GOVDTRM-ACP": "government_benefits",
    "T-GOVDTRM-SSD": "government_benefits", "T-IAS": "individual_access",
}
_VIRTUAL_TERMS = ("telehealth", "tele-health", "telemedicine", "virtual", "online", "digital health")

#: (rule_id or issue_type) -> the question an analyst must answer.
_QUESTION_BY_ISSUE_TYPE = {
    "NPI_CHECKSUM_INVALID": "Confirm the intended NPI for this organisation: the delivered value {masked} fails the CMS check digit and was not promoted.",
    "NPI_LENGTH_INVALID": "Confirm the intended NPI: the delivered value {masked} is not 10 characters.",
    "NPI_FORMAT_INVALID": "Confirm the intended NPI: the delivered value {masked} is not 10 digits.",
    "MULTIPLE_NPI_IN_ONE_FIELD": "State which of the delivered NPIs belongs to this organisation; splitting them is an identity decision.",
    "NPI_REQUIRED": "Confirm whether this organisation is a covered health care provider required to carry its own NPI, and supply it if so.",
    "NPI_NOT_FOUND": "Confirm whether NPI {masked} belongs to this organisation or to a representative provider: NPPES has no record of it.",
    "NPI_DEACTIVATED": "Confirm whether NPI {masked} was deactivated for this organisation, and which identifier now applies.",
    "NPI_EXISTING_VALUE_CONFLICT": "Decide which NPI is this organisation's: the delivered value conflicts with the registered one.",
    "IDENTIFIER_EXISTING_VALUE_CONFLICT": "Decide which identifier is this organisation's: the delivered value conflicts with the registered one.",
    "MATERIAL_IDENTIFIER_CONFLICT": "Decide which identifier is this organisation's (QA required): the conflict is material.",
    "INVALID_ACTIVE_IDENTIFIER": "Confirm the replacement for the active identifier that failed validation (QA required).",
    "PART_OF_UNRESOLVED": "Identify the parent organisation named by partOf {value}: it resolves to no delivered record, registry entity or QHIN.",
    "MISSING_PART_OF": "State this organisation's parent: no partOf was delivered.",
    "DUPLICATE_SOURCE_ID": "State which delivered line is this organisation: the source id is repeated.",
    "DUPLICATE_HCID": "Confirm whether the shared HCID is one home community or a delivery error.",
    "ZIP_STATE_MISMATCH": "Confirm the address of record: the ZIP prefix and the state disagree and neither can be inferred from the other.",
    "STATE_NOT_USPS_CODE": "Confirm the state of the address of record.",
    "UNKNOWN_SEQUOIA_ORG_TYPE": "Confirm the TEFCA class: sequoiaorgtype carries a value outside the observed vocabulary.",
    "MISSING_ACTIVE_VALUE": "Confirm whether this organisation is active: no flag was delivered.",
    "UNSUPPORTED_ACTIVE_VALUE": "Confirm whether this organisation is active: the delivered flag is not a supported form.",
    "SUSPECTED_TEST_RECORD": "Confirm whether this is a test record that must not be reported as an organisation.",
}
_QUESTION_BY_BUCKET = {
    "B2": "Confirm that the administrative variance (name/address/taxonomy differs in form) does not change the organisation's identity.",
    "B3": "Explain the source disagreement or the missing primary-source record; the rules could not resolve it.",
    "B4": "Confirm the identity match behind the disqualifying finding (exclusion, debarment or invalid identifier) before any determination.",
}
_QUESTION_BY_DIMENSION_DISPOSITION = {
    ("EXCLUSION_REVOCATION", "REVIEW"): "Confirm whether the exclusion/debarment record is this organisation: identity is unconfirmed, and an unconfirmed match is neither a pass nor a finding.",
    ("IDENTITY", "CONFLICT"): "Decide which identity evidence is authoritative: NPPES and the delivery disagree on the organisation's identity.",
    ("ADDRESS", "CONFLICT"): "Confirm the practice address of record: the delivered address and the source address differ materially (practice vs mailing vs corporate meanings respected).",
}

# ── authorized traffic evidence (Epic's lessons) ─────────────────────────────

#: Injectable provider. `None` means no authorized traffic-evidence record
#: type exists — the default, and the truth today. A test may inject one to
#: prove the slots consume it; production code never synthesises one.
TrafficEvidenceProvider = Callable[[Any, uuid.UUID], Awaitable[Optional[Dict[str, Any]]]]
_traffic_evidence_provider: Optional[TrafficEvidenceProvider] = None


def set_traffic_evidence_provider(provider: Optional[TrafficEvidenceProvider]) -> None:
    global _traffic_evidence_provider
    _traffic_evidence_provider = provider


async def authorized_traffic_evidence(db, entity_id) -> Optional[Dict[str, Any]]:
    """The ONLY input the contextual assessments may read. Returns None when
    no authorized evidence has been provided — it never derives traffic from
    registry or delivery data."""
    if _traffic_evidence_provider is None:
        return None
    try:
        return await _traffic_evidence_provider(db, entity_id)
    except Exception as exc:  # noqa: BLE001 — an evidence lookup must not fail the workspace
        logger.warning("traffic evidence provider failed for %s: %s", entity_id, exc)
        return None


def contextual_assessments(evidence: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Traffic volume, exchange balance, geographic plausibility — each from
    authorized evidence only. Without it, every slot is UNAVAILABLE."""
    slots = ("traffic_volume", "exchange_balance", "geographic_plausibility")
    if not evidence:
        return {s: {"assessment": ASSESSMENT_UNAVAILABLE, "reason": NO_TRAFFIC_EVIDENCE_REASON,
                    "basis": None} for s in slots}
    out = {}
    for s in slots:
        block = evidence.get(s)
        if not isinstance(block, dict) or "assessment" not in block:
            out[s] = {"assessment": ASSESSMENT_UNAVAILABLE,
                      "reason": NO_TRAFFIC_EVIDENCE_REASON, "basis": None}
        else:
            out[s] = {"assessment": block["assessment"], "reason": block.get("reason"),
                      "basis": {"evidence_id": evidence.get("evidence_id"),
                                "authorized_by": evidence.get("authorized_by"),
                                "period": evidence.get("period")}}
    return out


# ── helpers ──────────────────────────────────────────────────────────────────

def mask_identifier(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    v = str(value)
    return ("*" * max(len(v) - 4, 0)) + v[-4:]


def freshness_days() -> int:
    try:
        return int(os.getenv(FRESHNESS_ENV, DEFAULT_FRESHNESS_DAYS))
    except ValueError:
        return DEFAULT_FRESHNESS_DAYS


def _parse_ts(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def freshness_of(stamp, *, now: datetime, window_days: int) -> Dict[str, Any]:
    dt = _parse_ts(stamp)
    if dt is None:
        return {"status": UNKNOWN_AGE, "age_days": None, "window_days": window_days}
    age = (now - dt).total_seconds() / 86400.0
    return {"status": REUSED_FRESH if age <= window_days else STALE,
            "age_days": round(age, 2), "window_days": window_days}


def _governing_for_issue(issue) -> Dict[str, Any]:
    rule = RULE_BY_ID.get(issue.rule_id)
    spec = FIELD_BY_NAME.get(issue.field_name or "")
    return {
        "rule_id": issue.rule_id, "rule_version": issue.rule_version,
        "rule_description": rule.description if rule else None,
        "rule_stage": rule.stage if rule else stage_for(issue.rule_id, issue.issue_type),
        "field": issue.field_name,
        "field_necessity": spec.necessity if spec else None,
        "field_documented": spec.documented if spec else None,
        "field_docuaction": spec.docuaction if spec else None,
        "correction_authority": issue.correction_authority,
    }


def _question_for_issue(issue) -> str:
    template = _QUESTION_BY_ISSUE_TYPE.get(issue.issue_type)
    if template is None:
        return (f"Resolve {issue.issue_type} on {issue.field_name or 'the record'}: "
                f"{issue.description[:160]}")
    return template.format(masked=mask_identifier(issue.original_value),
                           value=issue.original_value)


def _context_patterns(entity, family_size: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if family_size > 1:
        out.append({"pattern": "RELATED_ORGANIZATIONS", "auto_reject": False,
                    "evidence": {"shared_tefcaid_family_size": family_size},
                    "note": "A TEFCAID shared by several records is an organisation family "
                            "(a system and its facilities), not a duplicate; related "
                            "organisations are context for the analyst, never a rejection."})
    purposes = (entity.exchange_purposes or {})
    tokens = purposes.get("purposes") if isinstance(purposes, dict) else purposes
    classes = sorted({_PURPOSE_CLASSES.get(str(t).strip(), "other")
                      for t in (tokens or []) if t})
    if len(classes) > 1:
        out.append({"pattern": "MIXED_BUSINESS_MODEL", "auto_reject": False,
                    "evidence": {"exchange_purpose_classes": classes},
                    "note": "Several exchange-purpose classes are legitimate for one "
                            "organisation; a mixed model is context, never a rejection."})
    name = (entity.name or "").lower()
    if any(t in name for t in _VIRTUAL_TERMS):
        out.append({"pattern": "VIRTUAL_CARE_POSSIBLE", "auto_reject": False,
                    "evidence": {"name_term_matched": True},
                    "note": "A virtual-care organisation may legitimately have no practice "
                            "address matching a physical location; context only, never "
                            "an automatic rejection."})
    return out


# ── the read model ───────────────────────────────────────────────────────────

async def delivery_workspace(db, intake_id, *, entity_id=None, limit: int = 50,
                             offset: int = 0) -> Dict[str, Any]:
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise ValueError(f"No intake {intake_id}")
    now = datetime.now(timezone.utc)
    window = freshness_days()

    curated_stmt = (select(m.RceCuratedRecord)
                    .where(m.RceCuratedRecord.source_intake_id == intake.id,
                           m.RceCuratedRecord.canonical_entity_id.isnot(None))
                    .order_by(m.RceCuratedRecord.rce_org_oid))
    if entity_id is not None:
        curated_stmt = curated_stmt.where(m.RceCuratedRecord.canonical_entity_id == entity_id)
    total = int((await db.execute(
        select(func.count()).select_from(curated_stmt.subquery()))).scalar() or 0)
    curated = (await db.execute(curated_stmt.limit(limit).offset(offset))).scalars().all()

    # ── cause groups over the WHOLE delivery (current run), not just the page ──
    scope = run_selection.issues_filter(intake.id)
    group_rows = (await db.execute(
        select(m.RceIssue.rule_id, m.RceIssue.issue_type, m.RceIssue.field_name,
               func.count(), func.count(func.distinct(m.RceIssue.source_record_id)),
               func.min(m.RceIssue.issue_code))
        .where(scope).group_by(m.RceIssue.rule_id, m.RceIssue.issue_type, m.RceIssue.field_name)
        .order_by(func.count().desc()))).all()
    cause_groups = {}
    for rule_id, issue_type, field_name, n, records, first_code in group_rows:
        key = f"{rule_id}|{issue_type}|{field_name or ''}"
        rule = RULE_BY_ID.get(rule_id)
        cause_groups[key] = {
            "group_key": key, "rule_id": rule_id, "issue_type": issue_type,
            "field_name": field_name, "findings": int(n), "records": int(records),
            "representative_issue_code": first_code,
            "rule_description": rule.description if rule else None,
            "investigate_once": int(records) > 1,
            "note": ("Investigate the cause once for the group; record a disposition and "
                     "independent QA per record — the workspace never applies one "
                     "decision to many records."),
        }

    entities: List[Dict[str, Any]] = []
    for c in curated:
        entity = await db.get(reg.TefcaRegEntity, c.canonical_entity_id)
        if entity is None:
            continue
        review = (await db.execute(
            select(reg.ReviewRecord)
            .where(reg.ReviewRecord.entity_id == entity.id,
                   reg.ReviewRecord.classification_bucket.isnot(None))
            .order_by(reg.ReviewRecord.created_at.desc()).limit(1))).scalars().first()
        rule_row = None
        if review is not None and review.classification_rule:
            rule_row = (await db.execute(
                select(reg.ReviewRule)
                .where(reg.ReviewRule.rule_code == review.classification_rule,
                       reg.ReviewRule.version == (review.classification_rule_version or 1))
                .limit(1))).scalars().first()
        issues = (await db.execute(
            select(m.RceIssue).where(scope, m.RceIssue.source_record_id == c.source_record_id)
            .order_by(m.RceIssue.issue_code))).scalars().all()
        evidence_rows = (await db.execute(
            select(TEFCADimensionEvidence)
            .where(TEFCADimensionEvidence.entity_id == str(entity.id))
            .order_by(TEFCADimensionEvidence.generation_timestamp.desc()))).scalars().all()
        latest_gen = evidence_rows[0].generation_timestamp if evidence_rows else None
        evidence_items = []
        reused = stale = 0
        for e in evidence_rows:
            if e.generation_timestamp != latest_gen:
                break
            fresh = freshness_of(e.retrieved_at or e.query_timestamp or e.generation_timestamp,
                                 now=now, window_days=window)
            reused += fresh["status"] == REUSED_FRESH
            stale += fresh["status"] == STALE
            evidence_items.append({
                "dimension": e.evidence_dimension, "source": e.source,
                "disposition": e.disposition, "dimension_disposition": e.dimension_disposition,
                "applicability": e.dimension_applicability,
                "query_identifier": mask_identifier(e.query_identifier),
                "as_of": str(e.retrieved_at or e.query_timestamp or e.generation_timestamp),
                "dataset_version_anchor": e.dataset_version_anchor,
                "rule_applied": e.rule_applied, "note": e.note,
                "freshness": fresh,
            })
        family_size = 0
        if entity.rce_tefcaid:
            family_size = int((await db.execute(
                select(func.count()).select_from(reg.TefcaRegEntity)
                .where(reg.TefcaRegEntity.rce_tefcaid == entity.rce_tefcaid,
                       reg.TefcaRegEntity.is_deleted.is_(False)))).scalar() or 0)

        findings = []
        questions: List[Dict[str, Any]] = []
        for i in issues:
            findings.append({
                "issue_id": str(i.id), "issue_code": i.issue_code,
                "stage": stage_for(i.rule_id, i.issue_type),
                "rule_id": i.rule_id, "rule_version": i.rule_version,
                "issue_type": i.issue_type, "severity": i.severity,
                "field_name": i.field_name,
                "original_value": (mask_identifier(i.original_value)
                                   if (i.field_name or "").upper() == "NPI" else i.original_value),
                "status": i.resolution, "correction_authority": i.correction_authority,
                "cause_group": f"{i.rule_id}|{i.issue_type}|{i.field_name or ''}",
                "governing_requirement": _governing_for_issue(i),
                "actions": {"disposition": f"/api/tefca/rce/issues/{i.id}/dispositions"},
            })
            if i.resolution in ("OPEN", "PROPOSED", "UNDER_REVIEW"):
                questions.append({"about": i.issue_code, "question": _question_for_issue(i)})
        if review is not None:
            q = _QUESTION_BY_BUCKET.get(review.classification_bucket)
            if q and review.reviewer_resolution is None:
                questions.append({"about": review.review_id, "question": q})
        for item in evidence_items:
            q = _QUESTION_BY_DIMENSION_DISPOSITION.get((item["dimension"], item["disposition"]))
            if q:
                questions.append({"about": f"{item['dimension']}/{item['source']}", "question": q})

        entities.append({
            "entity": {"entity_id": str(entity.id), "rce_org_oid": entity.rce_org_oid,
                       "name": entity.name, "sequoia_org_type": entity.sequoia_org_type,
                       "hl7_org_role": entity.hl7_org_role,
                       "verification_status": entity.verification_status,
                       "curated_record_id": str(c.id),
                       "source_record_id": str(c.source_record_id),
                       "record_status": c.record_status},
            "review": None if review is None else {
                "review_id": review.review_id,
                "bucket": review.classification_bucket,
                "rule": review.classification_rule,
                "rule_version": review.classification_rule_version,
                "rationale": review.classification_rationale,
                "reviewer_resolution": review.reviewer_resolution,
                "reclassified_to": review.reclassified_to,
                "reportable_at": review.reportable_at.isoformat() if review.reportable_at else None,
                "assigned_to_user_id": (str(review.assigned_to_user_id)
                                        if review.assigned_to_user_id else None),
                "governing_requirement": None if rule_row is None else {
                    "rule_code": rule_row.rule_code, "name": rule_row.name,
                    "version": rule_row.version, "bucket": rule_row.bucket,
                    "description": rule_row.description,
                    "effective_date": (rule_row.effective_date.isoformat()
                                       if rule_row.effective_date else None),
                    "retired_date": (rule_row.retired_date.isoformat()
                                     if rule_row.retired_date else None),
                    "conditions": rule_row.conditions},
                "actions": {
                    "claim": f"/api/tefca/arc/reviews/{review.review_id}/claim",
                    "determination": f"/api/tefca/arc/reviews/{review.review_id}/determination",
                    "independent_qa": f"/api/tefca/arc/reviews/{review.review_id}/qa",
                    "history": f"/api/tefca/arc/reviews/{review.review_id}/history"},
            },
            "findings": findings,
            "evidence": {"generation_timestamp": latest_gen, "items": evidence_items,
                         "reused_fresh": reused, "stale": stale,
                         "freshness_window_days": window},
            "unanswered_questions": questions,
            "context_patterns": _context_patterns(entity, family_size),
            "contextual_assessments": contextual_assessments(
                await authorized_traffic_evidence(db, entity.id)),
        })

    return {
        "workspace_version": WORKSPACE_VERSION,
        "intake_id": str(intake.id), "delivery_label": intake.delivery_label,
        "total_entities": total, "count": len(entities), "limit": limit, "offset": offset,
        "freshness_window_days": window,
        "cause_groups": list(cause_groups.values()),
        "entities": entities,
        "scope_note": ("Contextual assessments read only authorized traffic evidence; "
                       "none is derived from registry or delivery data and no "
                       "traffic-monitoring obligation is asserted. Related organisations, "
                       "mixed business models and virtual care are surfaced as context "
                       "and are never automatically rejected. Dispositions and "
                       "independent QA remain per record through the existing routes."),
    }
