"""IQVIA Release 1 — FOUNDATION ONLY (matching rules, source authority,
snapshot approval, licensed-data access). No IQVIA content is read here:
the licensed file specifications, samples and a data-use approval are not
in DocuAction's possession, so every IQVIA observation table stays a
PROPOSAL (docs/architecture/iqvia_release1_schema_proposal.md, ADR-006).

What this module fixes in code, so the licensed integration cannot drift:

  SOURCE AUTHORITY   ONC RCE is the only source of TEFCA facts (class,
                     QHIN, partOf, active, purposes). IQVIA, NPPES, CCN and
                     names/addresses are OBSERVATIONS about an entity. An
                     observation never creates, ends or re-parents a TEFCA
                     relationship. `TEFCA_AUTHORITATIVE_FIELDS` is the list.

  MATCHING           The only automatic match is an exact NPI that NPPES
                     says is Type 2 (organisation), that is Luhn-valid, and
                     that exactly ONE registry entity carries. Everything
                     else — CCN, exact name+address+phone, fuzzy discovery —
                     produces a CANDIDATE for an analyst; conflicts (the NPI
                     on more than one entity, a Type-1 NPI on an
                     organisation) are EXCEPTIONS. Never a silent merge.

  MAKER / CHECKER    An analyst determines (reviewed_by); independent QA
                     makes it reportable (qa_by ≠ reviewed_by; the table's
                     CHECK enforces it).

  SNAPSHOT APPROVAL  A licensed snapshot is usable only after a QA-lead-or-
                     above approval, recorded as a NEW `source_snapshot` row
                     (append-only) that supersedes the RECEIVED one.

  ACCESS             Licensed content is served only above the reviewer
                     floor and only when the programme flag is on.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select

from app.core import request_context
from app.core.security import role_at_least
from app.services.npi_validator import validate_npi
from app.tefca_registry.rce import snapshot_models as sm

logger = logging.getLogger(__name__)

MATCHING_MODEL_VERSION = "r1-foundation-1.0.0"

#: Facts that come from the ONC RCE delivery and from nowhere else.
TEFCA_AUTHORITATIVE_FIELDS = frozenset({
    "sequoia_org_type", "entity_level", "org_managing_org", "part_of",
    "is_active", "operational_status", "exchange_purposes", "org_node_type",
    "tefcaid", "hcid", "aaid", "rce_org_oid",
})

#: Sources that may only ever OBSERVE.
OBSERVATION_SOURCES = frozenset({
    sm.SOURCE_IQVIA_HCO, sm.SOURCE_IQVIA_HCP, sm.SOURCE_IQVIA_AFFILIATION,
    "NPPES", "CMS_PECOS", "CMS_CCN",
})

#: Programme flag; licensed sources are dark until it is on AND a snapshot
#: has been approved.
LICENSED_SOURCE_FLAG = "ENABLE_IQVIA_SOURCES"
LICENSED_ACCESS_ROLE = "reviewer"
SNAPSHOT_APPROVAL_ROLE = "qalead"


class SourceAuthorityViolation(RuntimeError):
    """An observation tried to assert a TEFCA fact."""


class MatchDecision(dict):
    """method, status, confidence, reason, evidence — a dict for JSON."""


def assert_not_tefca_fact(source_system: str, fields: Iterable[str]) -> None:
    """Refuse any write from an observation source to a TEFCA fact."""
    if source_system == sm.SOURCE_ONC_RCE:
        return
    illegal = sorted(set(fields) & TEFCA_AUTHORITATIVE_FIELDS)
    if illegal:
        raise SourceAuthorityViolation(
            f"{source_system} is an observation source; it cannot set TEFCA "
            f"fact(s) {illegal}. Only the ONC RCE delivery does.")


def nppes_entity_type(nppes_evidence: Optional[Dict[str, Any]]) -> Optional[str]:
    """'1' | '2' | None from an NPPES result (`enumeration_type` NPI-1/NPI-2)."""
    if not nppes_evidence:
        return None
    et = (nppes_evidence.get("enumeration_type") or "").strip().upper()
    return {"NPI-1": "1", "NPI-2": "2"}.get(et)


def evaluate_npi_match(*, source_npi: Optional[str],
                       nppes_evidence: Optional[Dict[str, Any]],
                       registry_entities_with_npi: List[Any],
                       source_record_key: str) -> MatchDecision:
    """The one automatic rule. AUTO_APPROVED only when every gate holds."""
    npi = (source_npi or "").strip()
    base = {"method": sm.METHOD_NPI_EXACT_TYPE2, "source_record_key": source_record_key,
            "model_version": MATCHING_MODEL_VERSION,
            "evidence": {"npi_present": bool(npi)}}
    if not npi:
        return MatchDecision(base, status=sm.MATCH_CANDIDATE, confidence=None,
                             reason="no NPI on the source record; nothing to match on")
    ok, why = validate_npi(npi)
    base["evidence"]["npi_valid"] = ok
    if not ok:
        return MatchDecision(base, status=sm.MATCH_EXCEPTION, confidence=None,
                             reason=f"source NPI fails validation: {why}")
    etype = nppes_entity_type(nppes_evidence)
    base["evidence"]["nppes_entity_type"] = etype
    if etype is None:
        return MatchDecision(base, status=sm.MATCH_CANDIDATE, confidence=None,
                             reason="NPPES entity type not evidenced; an analyst decides")
    if etype != "2":
        return MatchDecision(base, status=sm.MATCH_EXCEPTION, confidence=None,
                             reason="NPPES says Type 1 (individual); an organisation "
                                    "record cannot auto-match an individual NPI")
    n = len(registry_entities_with_npi)
    base["evidence"]["registry_entities_with_npi"] = n
    if n == 0:
        return MatchDecision(base, status=sm.MATCH_CANDIDATE, confidence=None,
                             reason="no registry entity carries this NPI")
    if n > 1:
        return MatchDecision(base, status=sm.MATCH_EXCEPTION, confidence=None,
                             reason=f"{n} registry entities carry this NPI; ambiguous")
    return MatchDecision(base, status=sm.MATCH_AUTO_APPROVED, confidence=1.0,
                         entity_id=str(registry_entities_with_npi[0]),
                         reason="exact Luhn-valid Type-2 NPI held by exactly one entity")


def evaluate_ccn_candidate(*, source_ccn: Optional[str], source_record_key: str,
                           registry_entities_with_ccn: List[Any]) -> MatchDecision:
    """CCN is CANDIDATE-only, always: a CCN identifies a certified facility,
    not necessarily the TEFCA organisation."""
    ccn = (source_ccn or "").strip()
    base = {"method": sm.METHOD_CCN_CANDIDATE, "source_record_key": source_record_key,
            "model_version": MATCHING_MODEL_VERSION,
            "evidence": {"ccn_present": bool(ccn),
                         "registry_entities_with_ccn": len(registry_entities_with_ccn)}}
    if not ccn or not registry_entities_with_ccn:
        return MatchDecision(base, status=sm.MATCH_CANDIDATE, confidence=None,
                             reason="no CCN evidence to offer")
    return MatchDecision(base, status=sm.MATCH_CANDIDATE, confidence=None,
                         candidates=[str(e) for e in registry_entities_with_ccn],
                         reason="CCN candidate(s) for analyst determination; never automatic")


def evaluate_descriptive_candidate(*, method: str, source_record_key: str,
                                   candidates: List[Any], score: Optional[float]) -> MatchDecision:
    """Exact name+address+phone and fuzzy discovery: candidates only."""
    if method not in (sm.METHOD_EXACT_NAME_ADDRESS_PHONE, sm.METHOD_FUZZY_DISCOVERY):
        raise ValueError(f"not a descriptive method: {method}")
    return MatchDecision(
        {"method": method, "source_record_key": source_record_key,
         "model_version": MATCHING_MODEL_VERSION, "evidence": {"score": score}},
        status=sm.MATCH_CANDIDATE, confidence=None,
        candidates=[str(c) for c in candidates],
        reason="descriptive similarity is discovery only; an analyst determines")


# ── persistence ──────────────────────────────────────────────────────────────

def _cid() -> str:
    return request_context.correlation_id()[:64]


async def record_match(db, *, entity_id, source_system: str, snapshot_id,
                       decision: MatchDecision, proposed_by: str,
                       reviewed_by: Optional[str] = None, qa_by: Optional[str] = None,
                       supersedes_match_id=None, commit: bool = True) -> sm.EntitySourceMatch:
    """Append one match row. The table's CHECKs refuse an automatic status on
    any method but NPI_EXACT_TYPE2 and a QA sign-off by the same person who
    reviewed; this function refuses the same things before the round-trip."""
    status = decision["status"]
    method = decision["method"]
    if status == sm.MATCH_AUTO_APPROVED and method != sm.METHOD_NPI_EXACT_TYPE2:
        raise ValueError(f"{method} can never be AUTO_APPROVED")
    if qa_by and reviewed_by and qa_by == reviewed_by:
        raise ValueError("maker/checker: QA sign-off must not be the reviewer")
    if status == sm.MATCH_QA_APPROVED and not (qa_by and reviewed_by):
        raise ValueError("QA_APPROVED requires both reviewed_by and qa_by")
    if status == sm.MATCH_ANALYST_APPROVED and not reviewed_by:
        raise ValueError("ANALYST_APPROVED requires reviewed_by")
    now = datetime.now(timezone.utc)
    row = sm.EntitySourceMatch(
        id=uuid.uuid4(), entity_id=entity_id, source_system=source_system,
        source_snapshot_id=snapshot_id, source_record_key=decision["source_record_key"],
        match_method=method, match_status=status, confidence=decision.get("confidence"),
        evidence={"reason": decision.get("reason"), **(decision.get("evidence") or {})},
        matching_model_version=decision.get("model_version") or MATCHING_MODEL_VERSION,
        proposed_by=proposed_by[:320], reviewed_by=reviewed_by, qa_by=qa_by,
        reviewed_at=now if reviewed_by else None, qa_at=now if qa_by else None,
        supersedes_match_id=supersedes_match_id, correlation_id=_cid())
    db.add(row)
    if commit:
        await db.commit()
    return row


async def register_snapshot(db, *, source_system: str, label: str, sha256: str,
                            record_count: int, received_at: datetime, created_by: str,
                            metadata: Optional[Dict[str, Any]] = None,
                            commit: bool = True) -> sm.SourceSnapshot:
    """A licensed snapshot arrives RECEIVED. Nothing reads it until approved."""
    if source_system not in OBSERVATION_SOURCES and source_system != sm.SOURCE_ONC_RCE:
        raise ValueError(f"unknown source system {source_system!r}")
    row = sm.SourceSnapshot(
        id=uuid.uuid4(), source_system=source_system, snapshot_label=label[:200],
        sha256=sha256, record_count=int(record_count), received_at=received_at,
        status=sm.SNAPSHOT_RECEIVED, metadata_=dict(metadata or {}),
        created_by=created_by[:320], correlation_id=_cid())
    db.add(row)
    if commit:
        await db.commit()
    return row


async def approve_snapshot(db, snapshot_id, *, user, approval_ref: str,
                           commit: bool = True) -> sm.SourceSnapshot:
    """QA-lead-or-above approval, APPEND-ONLY: a new APPROVED row that
    supersedes the RECEIVED one. The RECEIVED row is never edited."""
    if not role_at_least(user, SNAPSHOT_APPROVAL_ROLE):
        raise PermissionError(f"snapshot approval requires {SNAPSHOT_APPROVAL_ROLE} or above")
    received = await db.get(sm.SourceSnapshot, snapshot_id)
    if received is None:
        raise ValueError(f"no snapshot {snapshot_id}")
    if received.status != sm.SNAPSHOT_RECEIVED:
        raise ValueError(f"snapshot {snapshot_id} is {received.status}, not RECEIVED")
    successor = (await db.execute(
        select(sm.SourceSnapshot.id, sm.SourceSnapshot.status)
        .where(sm.SourceSnapshot.supersedes_snapshot_id == received.id).limit(1))).first()
    if successor is not None:
        raise ValueError(f"snapshot {snapshot_id} already has a {successor[1]} decision "
                         f"({successor[0]}); decisions are append-only and final")
    actor = getattr(user, "email", None) or "UNKNOWN"
    if actor == received.created_by:
        raise PermissionError("maker/checker: the registrant cannot approve their own snapshot")
    now = datetime.now(timezone.utc)
    row = sm.SourceSnapshot(
        id=uuid.uuid4(), source_system=received.source_system,
        snapshot_label=received.snapshot_label, sha256=received.sha256,
        record_count=received.record_count, received_at=received.received_at,
        intake_id=received.intake_id, status=sm.SNAPSHOT_APPROVED,
        approved_by=actor[:320], approved_at=now, approval_ref=approval_ref[:120],
        supersedes_snapshot_id=received.id, metadata_=dict(received.metadata_ or {}),
        created_by=actor[:320], correlation_id=_cid())
    db.add(row)
    if commit:
        await db.commit()
    return row


async def current_approved_snapshot(db, source_system: str) -> Optional[sm.SourceSnapshot]:
    """Latest APPROVED snapshot of a source that no later row supersedes."""
    superseded = select(sm.SourceSnapshot.supersedes_snapshot_id).where(
        sm.SourceSnapshot.supersedes_snapshot_id.isnot(None))
    return (await db.execute(
        select(sm.SourceSnapshot)
        .where(sm.SourceSnapshot.source_system == source_system,
               sm.SourceSnapshot.status == sm.SNAPSHOT_APPROVED,
               sm.SourceSnapshot.id.notin_(superseded))
        .order_by(sm.SourceSnapshot.received_at.desc(), sm.SourceSnapshot.created_at.desc())
        .limit(1))).scalars().first()


# ── access ───────────────────────────────────────────────────────────────────

def licensed_source_enabled() -> bool:
    import os
    from app.core.config import settings
    value = getattr(settings, LICENSED_SOURCE_FLAG, None)
    if value is None:
        value = os.environ.get(LICENSED_SOURCE_FLAG, "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def licensed_access_allowed(user) -> Dict[str, Any]:
    """Who may see IQVIA-derived content: reviewer floor AND programme flag.
    Returns a decision dict rather than raising, so routes can answer with
    `availability` the way the delivery routes do."""
    if not licensed_source_enabled():
        return {"allowed": False, "availability": "not_configured",
                "reason": f"{LICENSED_SOURCE_FLAG} is off"}
    if not role_at_least(user, LICENSED_ACCESS_ROLE):
        return {"allowed": False, "availability": f"requires_role:{LICENSED_ACCESS_ROLE}",
                "reason": "licensed content is served above the reviewer floor only"}
    return {"allowed": True, "availability": "available", "reason": None}
