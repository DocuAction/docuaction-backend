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
    delivery_intake_id = Column(UUID(as_uuid=True))
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
        "delivery_intake_id")}
    out["query_inputs"] = {k: v for k, v in qi.items() if full or not k.endswith("keyed_hash")}
    out["result_summary"] = {k: v for k, v in rs.items() if full or k != "hco_record_keys"}
    out["rationale"] = row.get("rationale") if (full or own) else None
    out["rationale_redacted"] = not (full or own)
    out["entity_id"] = row.get("entity_id") if full else None
    out["redaction_level"] = "FULL" if full else "REVIEWER"
    return out


def disclosure_from_rows(*, flag_enabled: bool, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The truthful, identifier-free statement of ACTUAL IQVIA use for a report or CSV.
    `rows`: dicts with decision, snapshot_id, snapshot_sha256_prefix, assessed_at, assessed_by.
    Staged or approved snapshots are not use; only recorded assessments are."""
    if not flag_enabled or not rows:
        return {"state": "IQVIA_NOT_USED", "assessments": 0, "by_decision": {}, "snapshots": [],
                "first_assessed_at": None, "last_assessed_at": None, "distinct_assessors": 0,
                "verification_source": False,
                "statement": ("No IQVIA data was used for this delivery. A staged or approved IQVIA snapshot "
                              "is not use, and IQVIA is not a verification source.")}
    snaps = sorted({(str(r["snapshot_id"]), r.get("snapshot_sha256_prefix") or "") for r in rows})
    times = sorted(str(r["assessed_at"]) for r in rows if r.get("assessed_at"))
    n = len(rows)
    return {"state": "IQVIA_ADVISORY_USED", "assessments": n,
            "by_decision": dict(sorted(Counter(r["decision"] for r in rows).items())),
            "snapshots": [{"snapshot_id": s, "sha256_prefix": p} for s, p in snaps],
            "first_assessed_at": times[0] if times else None, "last_assessed_at": times[-1] if times else None,
            "distinct_assessors": len({r.get("assessed_by") for r in rows}),
            "verification_source": False,
            "statement": (f"IQVIA affiliation data was consulted in {n} analyst advisory assessment(s) for this "
                          "delivery. It is supplemental: it did not verify any entity, is not exclusion "
                          "clearance, and did not set or change any B1-B4 determination.")}


# ── database access ──────────────────────────────────────────────────────────
def _row_dict(r: IqviaAdvisoryAssessment) -> Dict[str, Any]:
    return {c: getattr(r, c) for c in (
        "id", "snapshot_id", "snapshot_status", "snapshot_sha256_prefix", "snapshot_record_count",
        "query_inputs", "result_summary", "decision", "rationale", "entity_id", "delivery_intake_id",
        "determination", "affects_verification", "assessed_by", "assessed_role", "assessed_at")}


async def record_assessment(db, *, user, secret: str, correlation_id: str, build_sha: str,
                            snapshot_id, org_npi: Any = None, org_ccn: Any = None,
                            hco_record_key: Optional[str] = None, decision: str, rationale: str,
                            entity_id=None, delivery_intake_id=None) -> IqviaAdvisoryAssessment:
    """Run the lookup server-side, validate the decision against it, add ONE evidence row (no commit)."""
    if not (rationale or "").strip():
        raise AssessmentRefused("RATIONALE_REQUIRED", "an analyst rationale is required")
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
        entity_id=entity_id, delivery_intake_id=delivery_intake_id,
        determination=DETERMINATION, affects_verification=False,
        assessed_by=getattr(user, "email", None) or "UNKNOWN", assessed_role=str(getattr(user, "role", "") or ""),
        correlation_id=correlation_id, build_sha=build_sha)
    db.add(row)
    return row


async def list_assessments(db, *, snapshot_id=None, delivery_intake_id=None, limit: int = 50,
                           offset: int = 0) -> List[Dict[str, Any]]:
    q = select(IqviaAdvisoryAssessment)
    if snapshot_id is not None:
        q = q.where(IqviaAdvisoryAssessment.snapshot_id == snapshot_id)
    if delivery_intake_id is not None:
        q = q.where(IqviaAdvisoryAssessment.delivery_intake_id == delivery_intake_id)
    q = q.order_by(IqviaAdvisoryAssessment.assessed_at.desc(), IqviaAdvisoryAssessment.id).limit(
        max(1, min(limit, 200))).offset(max(0, offset))
    return [_row_dict(r) for r in (await db.execute(q)).scalars().all()]


async def disclosure_for_intake(db, *, flag_enabled: bool, intake_id) -> Dict[str, Any]:
    """Report/CSV disclosure for one delivery. The table is read ONLY when the flag is on."""
    if not flag_enabled or intake_id is None:
        return disclosure_from_rows(flag_enabled=False, rows=[])
    a = IqviaAdvisoryAssessment  # unbounded: a disclosure must not understate use. Columns only, no rationale.
    got = (await db.execute(select(a.decision, a.snapshot_id, a.snapshot_sha256_prefix, a.assessed_at,
                                   a.assessed_by).where(a.delivery_intake_id == intake_id))).all()
    return disclosure_from_rows(flag_enabled=True, rows=[
        {"decision": d, "snapshot_id": s, "snapshot_sha256_prefix": p, "assessed_at": t, "assessed_by": u}
        for d, s, p, t, u in got])
