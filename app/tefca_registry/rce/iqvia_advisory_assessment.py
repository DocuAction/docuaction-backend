"""IQVIA advisory ASSESSMENT: durable, append-only evidence of actual IQVIA use. DEFAULT OFF.

WHAT THIS ADDS (and only this)
------------------------------
`iqvia_affiliation_consumer` answers a lookup and forgets it. This module lets an analyst RECORD the
outcome of one organisation-first lookup, so that "IQVIA was used" is a stored fact with:

  * the snapshot (id, status, SHA-256 prefix, record count) the lookup read,
  * the query inputs (masked + keyed-hash of the normalised identifiers, never the raw values),
  * the result summary (found / counts / conflict codes / truncation / registry outcome),
  * the analyst's decision + rationale, role, and a UTC timestamp.

The lookup is executed SERVER-SIDE inside `record_assessment`; the client cannot supply a result.

WHAT THIS IS NOT
----------------
* Not verification. IQVIA is supplemental. No decision value means "cleared", "excluded" or "B1/B4";
  there is no column that could carry one, and a CHECK constraint pins `affects_verification` to false.
  A NOT_FOUND_IN_EXTRACT decision is a statement about the extract, never about the entity.
* Not reachable unless ENABLE_IQVIA_AFFILIATION_CONSUMPTION and ENABLE_IQVIA_SOURCES are true and the
  caller is at the reviewer floor (same gate as the lookup routes). Only an APPROVED/SUPERSEDED snapshot
  can be assessed; the diagnostic pending-snapshot switch is not exposed here.
* Approval of a snapshot and consumption of it stay separate switches.

REDACTION BY ROLE (see `redact_for_role`)
  reviewer, senior_analyst : decision, snapshot provenance, MASKED identifiers, counts, conflict codes;
                             rationale only for their own rows; no HCO keys, hashes or entity ids.
  qalead and above         : everything stored, including HCO keys, rationale, hashes and entity id.
Reports and CSVs carry only the aggregate `disclosure_from_rows` (no identifiers, no names).

REVIEW LINK (optional): `review_id` attaches the assessment to the review it informs, as the written workflow
(WP6-C spec) describes. It is validated server-side (REVIEW_NOT_FOUND / REVIEW_NOT_IN_DELIVERY / ENTITY_NOT_IN_REVIEW) and
is NOT required: delivery-level evidence stays permitted, and an entity is never required. There is deliberately no
database FK to review_records (an owner-owned append-only table must not depend on a REFERENCES grant on an
app-owned table); the check is made at write time and rows are never edited, so a later correction is a NEW row.

DELIVERY CONTEXT: `delivery_intake_id` is required and validated server-side (DELIVERY_CONTEXT_REQUIRED /
DELIVERY_NOT_FOUND / ENTITY_NOT_IN_DELIVERY). The report disclosure is read per delivery and is NOT gated by the
consumption flag: disabling future lookups never erases the record of past use.

SCHEMA: table `iqvia_advisory_assessment` is created by DRAFT migration
20261014_iqvia_advisory_assessment.py -- NOT APPLIED to any database by this change.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, func,
                        select, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.core.security import role_level
from app.tefca_registry.rce import iqvia_affiliation_consumer as ac
from app.tefca_registry.rce import snapshot_models as sm

DETERMINATION = "ANALYST_ADVISORY_NOT_VERIFICATION"
QALEAD_LEVEL = 6

D_IDENTITY = "IDENTITY_CORROBORATED_ADVISORY"
D_RELATIONSHIP = "RELATIONSHIP_CORROBORATED_ADVISORY"
D_NOT_FOUND = "NOT_FOUND_IN_EXTRACT"
D_CONFLICT = "CONFLICT_REFERRED"
D_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
DECISIONS = (D_IDENTITY, D_RELATIONSHIP, D_NOT_FOUND, D_CONFLICT, D_INSUFFICIENT)
_CORROBORATING = (D_IDENTITY, D_RELATIONSHIP)


class AssessmentRefused(ValueError):
    """The requested decision is not supported by the lookup result; `.code` is stable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class IqviaAdvisoryAssessment(Base):
    __tablename__ = "iqvia_advisory_assessment"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False)
    snapshot_status = Column(String(16), nullable=False)
    snapshot_sha256_prefix = Column(String(12), nullable=False)
    snapshot_record_count = Column(Integer, nullable=True)
    query_inputs = Column(JSONB, nullable=False)
    result_summary = Column(JSONB, nullable=False)
    decision = Column(String(40), nullable=False)
    rationale = Column(Text, nullable=False)
    entity_id = Column(UUID(as_uuid=True))
    #: Optional review this evidence informs (review_records.review_id). Added by 20261015_iqvia_assess_review; no FK, see module doc.
    review_id = Column(String(20))
    #: Every assessment belongs to exactly ONE delivery; the report disclosure is scoped by this column.
    delivery_intake_id = Column(UUID(as_uuid=True), ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                                nullable=False)
    determination = Column(String(40), nullable=False, server_default=text(f"'{DETERMINATION}'"))
    affects_verification = Column(Boolean, nullable=False, server_default=text("false"))
    assessed_by = Column(String(320), nullable=False)
    assessed_role = Column(String(64), nullable=False)
    assessed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    correlation_id = Column(String(64), nullable=False)
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))

    __table_args__ = (
        CheckConstraint("affects_verification = false", name="ck_iqvia_assess_not_verification"),
        CheckConstraint("determination = 'ANALYST_ADVISORY_NOT_VERIFICATION'", name="ck_iqvia_assess_determination"),
        CheckConstraint("decision IN ('IDENTITY_CORROBORATED_ADVISORY','RELATIONSHIP_CORROBORATED_ADVISORY',"
                        "'NOT_FOUND_IN_EXTRACT','CONFLICT_REFERRED','INSUFFICIENT_EVIDENCE')",
                        name="ck_iqvia_assess_decision"),
    )


# ── pure helpers ─────────────────────────────────────────────────────────────
def keyed_hash(value: Optional[str], key: str) -> Optional[str]:
    """HMAC-SHA256 of a normalised identifier. Keyed because a bare hash of a 10-digit NPI is
    brute-forceable; used only to test two assessments used the same identifier."""
    if not value:
        return None
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


def build_query_inputs(*, org_npi: Any, org_ccn: Any, hco_record_key: Optional[str], key: str) -> Dict[str, Any]:
    n, c = ac.normalize_npi(org_npi), ac.normalize_ccn(org_ccn)
    return {
        "org_npi_masked": ac.mask(n["value"]) if n["valid"] else ("<invalid>" if n["present"] else "<empty>"),
        "org_ccn_masked": ac.mask(c["value"]) if c["valid"] else ("<invalid>" if c["present"] else "<empty>"),
        "org_npi_keyed_hash": keyed_hash(n["value"], key),
        "org_ccn_keyed_hash": keyed_hash(c["value"], key),
        "org_npi_valid": bool(n["valid"]), "org_ccn_valid": bool(c["valid"]),
        "hco_record_key_supplied": bool(hco_record_key),
    }


def summarise_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Counts and codes only: no relationship rows, no person-level keys."""
    reg = result.get("registry_candidate") or {}
    return {
        "found_in_extract": bool(result.get("found_in_extract")),
        "organisations": len(result.get("organisations") or []) + int(result.get("organisations_omitted") or 0),
        "total_relationships": int(result.get("total_relationships") or 0),
        "truncated": bool(result.get("truncated")),
        "conflict_codes": sorted({c.get("code") for c in result.get("conflicts") or [] if c.get("code")}),
        "notes": list(result.get("notes") or []),
        "registry_outcome": reg.get("outcome"),
        "hco_record_keys": [o["hco_record_key"] for o in (result.get("organisations") or [])][:25],
    }


def check_decision(decision: str, summary: Dict[str, Any]) -> None:
    """A decision may not claim more than the lookup result supports. Raises AssessmentRefused."""
    if decision not in DECISIONS:
        raise AssessmentRefused("UNKNOWN_DECISION", "decision is not one of the permitted advisory values")
    unusable = "NO_USABLE_IDENTIFIER" in summary["notes"]
    if unusable and decision != D_INSUFFICIENT:
        raise AssessmentRefused("UNAVAILABLE_NO_USABLE_IDENTIFIER",
                                "no usable identifier was supplied; only INSUFFICIENT_EVIDENCE can be recorded")
    if decision in _CORROBORATING:
        if not summary["found_in_extract"]:
            raise AssessmentRefused("NOT_FOUND_CANNOT_CORROBORATE", "nothing was found in the extract")
        if decision == D_RELATIONSHIP and not summary["total_relationships"]:
            raise AssessmentRefused("NO_RELATIONSHIP_TO_CORROBORATE",
                                    "the lookup returned no relationship rows; record IDENTITY_CORROBORATED_ADVISORY "
                                    "or INSUFFICIENT_EVIDENCE instead")
        if summary["conflict_codes"]:
            raise AssessmentRefused("CONFLICT_CANNOT_CORROBORATE",
                                    "identifier conflicts exist; record CONFLICT_REFERRED instead")
    if decision == D_NOT_FOUND and summary["found_in_extract"]:
        raise AssessmentRefused("FOUND_CANNOT_BE_NOT_FOUND", "the extract contains matching rows")
    if decision == D_CONFLICT and not summary["conflict_codes"]:
        raise AssessmentRefused("NO_CONFLICT_TO_REFER", "the lookup reported no identifier conflict")


def redact_for_role(row: Dict[str, Any], user) -> Dict[str, Any]:
    """Field-level redaction of one stored assessment (dict form) for `user`."""
    full = role_level(getattr(user, "role", None)) >= QALEAD_LEVEL
    own = (getattr(user, "email", None) or "") == row.get("assessed_by")
    qi = dict(row.get("query_inputs") or {})
    rs = dict(row.get("result_summary") or {})
    out = {k: row.get(k) for k in (
        "id", "snapshot_id", "snapshot_status", "snapshot_sha256_prefix", "snapshot_record_count", "decision",
        "determination", "affects_verification", "assessed_by", "assessed_role", "assessed_at",
        "delivery_intake_id", "review_id")}
    out["query_inputs"] = {k: v for k, v in qi.items() if full or not k.endswith("keyed_hash")}
    out["result_summary"] = {k: v for k, v in rs.items() if full or k != "hco_record_keys"}
    out["rationale"] = row.get("rationale") if (full or own) else None
    out["rationale_redacted"] = not (full or own)
    out["entity_id"] = row.get("entity_id") if full else None
    out["redaction_level"] = "FULL" if full else "REVIEWER"
    return out


STATE_NOT_USED = "IQVIA_NOT_USED"
STATE_USED = "IQVIA_ADVISORY_USED"
STATE_STORE_NOT_APPLIED = "IQVIA_ASSESSMENT_STORE_NOT_APPLIED"
STATE_STORE_UNREADABLE = "IQVIA_ASSESSMENT_STORE_UNREADABLE"


def disclosure_from_rows(*, flag_enabled: bool, rows: Sequence[Dict[str, Any]],
                         store: str = "READABLE") -> Dict[str, Any]:
    """The truthful, identifier-free statement of ACTUAL IQVIA use for a report or CSV.
    `rows`: dicts with decision, snapshot_id, snapshot_sha256_prefix, assessed_at, assessed_by.
    Staged or approved snapshots are not use; only recorded assessments are.

    `flag_enabled` says whether FUTURE lookups are allowed. It never hides a recorded assessment: switching
    consumption off stops new use, it does not erase the fact of past use. `store` is READABLE, NOT_APPLIED (the
    table does not exist in this database) or UNREADABLE (it exists but the runtime role cannot read it); in the
    last two cases the report says it cannot tell, rather than claiming "not used"."""
    base = {"assessments": 0, "reviews_linked": 0, "by_decision": {}, "snapshots": [], "first_assessed_at": None,
            "last_assessed_at": None, "distinct_assessors": 0, "verification_source": False,
            "consumption_currently_enabled": bool(flag_enabled), "store": store}
    if store == "NOT_APPLIED":
        return {**base, "state": STATE_STORE_NOT_APPLIED,
                "statement": ("The IQVIA advisory-assessment store is not present in this environment, so no IQVIA "
                              "assessment can have been recorded here. IQVIA is not a verification source.")}
    if store == "UNREADABLE":
        return {**base, "state": STATE_STORE_UNREADABLE,
                "statement": ("The IQVIA advisory-assessment store exists but could not be read by the reporting "
                              "role, so whether IQVIA was used for this delivery cannot be stated from this report.")}
    if not rows:
        return {**base, "state": STATE_NOT_USED,
                "statement": ("No IQVIA data was used for this delivery. A staged or approved IQVIA snapshot "
                              "is not use, and IQVIA is not a verification source.")}
    snaps = sorted({(str(r["snapshot_id"]), r.get("snapshot_sha256_prefix") or "") for r in rows})
    times = sorted(str(r["assessed_at"]) for r in rows if r.get("assessed_at"))
    n = len(rows)
    tail = ("" if flag_enabled else
            " IQVIA lookup is currently disabled; these are historical records of past use and remain part of "
            "the evidence.")
    linked = len({r.get("review_id") for r in rows if r.get("review_id")})
    review_tail = (f" {linked} distinct review(s) are linked to these assessments; the rest are delivery-level evidence."
                   if linked else " None is linked to a review (delivery-level evidence).")
    return {**base, "state": STATE_USED, "assessments": n, "reviews_linked": linked,
            "by_decision": dict(sorted(Counter(r["decision"] for r in rows).items())),
            "snapshots": [{"snapshot_id": s, "sha256_prefix": p} for s, p in snaps],
            "first_assessed_at": times[0] if times else None, "last_assessed_at": times[-1] if times else None,
            "distinct_assessors": len({r.get("assessed_by") for r in rows}),
            "statement": (f"IQVIA affiliation data was consulted in {n} analyst advisory assessment(s) for this "
                          "delivery. It is supplemental: it did not verify any entity, is not exclusion "
                          "clearance, and did not set or change any B1-B4 determination." + review_tail + tail)}


# ── database access ──────────────────────────────────────────────────────────
def _row_dict(r: IqviaAdvisoryAssessment) -> Dict[str, Any]:
    return {c: getattr(r, c) for c in (
        "id", "snapshot_id", "snapshot_status", "snapshot_sha256_prefix", "snapshot_record_count",
        "query_inputs", "result_summary", "decision", "rationale", "entity_id", "delivery_intake_id", "review_id",
        "determination", "affects_verification", "assessed_by", "assessed_role", "assessed_at")}


async def require_delivery_context(db, *, delivery_intake_id, entity_id=None, review_id=None) -> None:
    """The assessment must name a delivery that exists; an entity, if given, must be present in that delivery.
    Server-side, because the delivery chosen decides which report discloses the use."""
    if delivery_intake_id is None:
        raise AssessmentRefused("DELIVERY_CONTEXT_REQUIRED",
                                "delivery_intake_id is required: an assessment is recorded against one delivery")
    from app.tefca_registry.rce import models as rm

    found = (await db.execute(select(rm.RceSourceIntake.id).where(rm.RceSourceIntake.id == delivery_intake_id))
             ).first()
    if found is None:
        raise AssessmentRefused("DELIVERY_NOT_FOUND", "no delivery with that id exists")
    if entity_id is not None:
        # `present` matters: record_presence also writes present=false rows for entities the PREVIOUS delivery carried
        # and this one does not, so a row existing is not the same as the entity being in this delivery.
        present = (await db.execute(select(sm.RceEntityPresence.id).where(
            sm.RceEntityPresence.intake_id == delivery_intake_id,
            sm.RceEntityPresence.entity_id == entity_id,
            sm.RceEntityPresence.present.is_(True)).limit(1))).first()
        if present is None:
            raise AssessmentRefused("ENTITY_NOT_IN_DELIVERY", "the entity is not present in that delivery")
    if review_id is not None:
        await _require_review_context(db, delivery_intake_id=delivery_intake_id, entity_id=entity_id,
                                      review_id=review_id)


async def _require_review_context(db, *, delivery_intake_id, entity_id, review_id) -> None:
    """The review must exist and belong to THIS delivery, by the same linkage the delivery's own record list uses
    (a review matches a delivery record by source_record_id, or by the record's entity). A selected entity must be
    one of the review's own entities. Reads only; nothing is written on refusal."""
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as rm

    review = (await db.execute(select(reg.ReviewRecord.review_id, reg.ReviewRecord.entity_id,
                                      reg.ReviewRecord.source_record_id)
                               .where(reg.ReviewRecord.review_id == review_id))).first()
    if review is None:
        raise AssessmentRefused("REVIEW_NOT_FOUND", "no review with that id exists")
    _, review_entity, review_record = review

    # the delivery's own source records and the entities present in it
    in_delivery = False
    review_entities = set()
    if review_entity is not None:
        review_entities.add(review_entity)
    if review_record is not None:
        rec = (await db.execute(select(rm.RceSourceRecord.source_intake_id, rm.RceSourceRecord.canonical_entity_id)
                                .where(rm.RceSourceRecord.id == review_record))).first()
        if rec is not None and rec[0] == delivery_intake_id:
            in_delivery = True
            if rec[1] is not None:
                review_entities.add(rec[1])
    if not in_delivery and review_entity is not None:
        in_delivery = (await db.execute(select(sm.RceEntityPresence.id).where(
            sm.RceEntityPresence.intake_id == delivery_intake_id,
            sm.RceEntityPresence.entity_id == review_entity,
            sm.RceEntityPresence.present.is_(True)).limit(1))).first() is not None
    if not in_delivery:
        raise AssessmentRefused("REVIEW_NOT_IN_DELIVERY", "that review does not belong to the selected delivery")
    if entity_id is not None and entity_id not in review_entities:
        raise AssessmentRefused("ENTITY_NOT_IN_REVIEW", "the selected entity is not an entity of that review")


async def record_assessment(db, *, user, secret: str, correlation_id: str, build_sha: str,
                            snapshot_id, org_npi: Any = None, org_ccn: Any = None,
                            hco_record_key: Optional[str] = None, decision: str, rationale: str,
                            entity_id=None, delivery_intake_id=None, review_id=None) -> IqviaAdvisoryAssessment:
    """Run the lookup server-side, validate the decision against it, add ONE evidence row (no commit)."""
    if not (rationale or "").strip():
        raise AssessmentRefused("RATIONALE_REQUIRED", "an analyst rationale is required")
    await require_delivery_context(db, delivery_intake_id=delivery_intake_id, entity_id=entity_id,
                                   review_id=review_id)
    result = await ac.organisation_relationships(  # raises SnapshotNotEligible for a non-approved snapshot
        db, snapshot_id=snapshot_id, org_npi=org_npi, org_ccn=org_ccn, hco_record_key=hco_record_key, limit=1)
    summary = summarise_result(result)
    check_decision(decision, summary)
    prov = result["provenance"]
    row = IqviaAdvisoryAssessment(
        snapshot_id=uuid.UUID(prov["snapshot_id"]), snapshot_status=prov["status"],
        snapshot_sha256_prefix=prov["sha256_prefix"], snapshot_record_count=prov["record_count"],
        query_inputs=build_query_inputs(org_npi=org_npi, org_ccn=org_ccn, hco_record_key=hco_record_key, key=secret),
        result_summary=summary, decision=decision, rationale=rationale.strip(),
        entity_id=entity_id, delivery_intake_id=delivery_intake_id, review_id=review_id,
        determination=DETERMINATION, affects_verification=False,
        assessed_by=getattr(user, "email", None) or "UNKNOWN", assessed_role=str(getattr(user, "role", "") or ""),
        correlation_id=correlation_id, build_sha=build_sha)
    db.add(row)
    return row


async def list_assessments(db, *, snapshot_id=None, delivery_intake_id=None, review_id=None, limit: int = 50,
                           offset: int = 0) -> List[Dict[str, Any]]:
    q = select(IqviaAdvisoryAssessment)
    if snapshot_id is not None:
        q = q.where(IqviaAdvisoryAssessment.snapshot_id == snapshot_id)
    if delivery_intake_id is not None:
        q = q.where(IqviaAdvisoryAssessment.delivery_intake_id == delivery_intake_id)
    if review_id is not None:
        q = q.where(IqviaAdvisoryAssessment.review_id == review_id)
    q = q.order_by(IqviaAdvisoryAssessment.assessed_at.desc(), IqviaAdvisoryAssessment.id).limit(
        max(1, min(limit, 200))).offset(max(0, offset))
    return [_row_dict(r) for r in (await db.execute(q)).scalars().all()]


async def _store_state(db) -> str:
    """READABLE / NOT_APPLIED / UNREADABLE, decided by catalogue functions. No statement can fail, so the
    caller's transaction is never poisoned and nothing is retried."""
    row = (await db.execute(text(
        "SELECT to_regclass('public.iqvia_advisory_assessment') IS NOT NULL AS present, "
        "CASE WHEN to_regclass('public.iqvia_advisory_assessment') IS NULL THEN false "
        "ELSE has_table_privilege('public.iqvia_advisory_assessment', 'SELECT') END AS readable"))).one()
    if not row[0]:
        return "NOT_APPLIED"
    return "READABLE" if row[1] else "UNREADABLE"


async def disclosure_for_intake(db, *, flag_enabled: bool, intake_id) -> Dict[str, Any]:
    """Report/CSV disclosure for ONE delivery. Reads the assessment store whatever the consumption flag says:
    the flag gates new lookups, not the record of past use. An absent or unreadable store is stated, never
    reported as 'not used'. Rows are matched on the delivery's own id only."""
    if intake_id is None:
        return disclosure_from_rows(flag_enabled=flag_enabled, rows=[])
    store = await _store_state(db)
    if store != "READABLE":
        return disclosure_from_rows(flag_enabled=flag_enabled, rows=[], store=store)
    a = IqviaAdvisoryAssessment  # unbounded: a disclosure must not understate use. Columns only, no rationale.
    got = (await db.execute(select(a.decision, a.snapshot_id, a.snapshot_sha256_prefix, a.assessed_at,
                                   a.assessed_by, a.review_id).where(a.delivery_intake_id == intake_id))).all()
    return disclosure_from_rows(flag_enabled=flag_enabled, rows=[
        {"decision": d, "snapshot_id": s, "snapshot_sha256_prefix": p, "assessed_at": t, "assessed_by": u,
         "review_id": r} for d, s, p, t, u, r in got])
