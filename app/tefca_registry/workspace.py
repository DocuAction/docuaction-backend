"""The analyst's verification workspace: one case, assembled once.

WHY THIS EXISTS
───────────────
An analyst reviewing one delivered organisation needs the delivered values, what
curation changed and why, what the Government sources said, what USPS observed
about the address, what the organisation publishes about itself, whatever
evidence has been attached, and the place to record a recommendation. Today that
is seven screens and seven endpoints, and the analyst is the one holding them
together in their head.

This assembles them into one response, in the seven sections the workspace
displays. It is READ-ONLY and it computes nothing new — every section is a
faithful presentation of a record that already exists.

THE FOUR LAYERS ARE KEPT VISIBLY APART
──────────────────────────────────────
    SOURCE            what ONC/RCE delivered           — never modified
    CURATED           what DocuAction derived          — always labelled
    SYSTEM EVIDENCE   what an external source observed — never a verdict
    ANALYST EVIDENCE  what a human attached

A curated value is NEVER returned in the SOURCE section, and the source value
travels beside every curated one. The point is not tidiness: an analyst who
cannot tell a delivered value from a derived one cannot review either, and a
report citing a "source" address that DocuAction actually normalised is a report
that misrepresents the Government's own file.

SOURCE_UNAVAILABLE IS NEVER CONVERTED
─────────────────────────────────────
`_source_state` passes canonical Layer 1 states through unchanged. "The source
did not answer" and "the source answered and found nothing" are different facts
about different subjects, and a workspace that showed an outage as NO_MATCH
would invite a finding against an entity nobody actually checked.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m

logger = logging.getLogger(__name__)

#: The Government/approved verification sources the workspace displays, in the
#: order an analyst reads them. Names as the evidence layer already writes them.
GOVERNMENT_SOURCES = ("NPPES", "OIG_LEIE", "PECOS", "PPEF", "SAM_GOV",
                      "CMS_REVOKED")


class WorkspaceRefused(RuntimeError):
    """The workspace could not be assembled, and the reason is stated."""


async def workspace(db, review_id: str, *, include_raw: bool = False
                    ) -> Dict[str, Any]:
    """Everything one analyst needs for one assigned case."""
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    if record is None:
        raise WorkspaceRefused(f"No review {review_id}")

    entity = (await db.get(reg.TefcaRegEntity, record.entity_id)
              if record.entity_id else None)
    curated = await _curated_for(db, record)
    source = await _source_for(db, record, curated)

    return {
        "review_id": record.review_id,
        "entity_id": str(record.entity_id) if record.entity_id else None,
        "case": await _case(db, record),

        # A — exactly as delivered.
        "source": await _section_source(db, source, curated, entity,
                                        include_raw=include_raw),
        # B — what DocuAction derived, always beside its source value.
        "curated": await _section_curated(db, curated),
        # C — the approved Government sources.
        "verification": await _section_verification(db, record),
        # D — USPS, current API platform.
        "address_verification": await _section_usps(db, source, curated),
        # E — corroborating only.
        "website": await _section_website(db, record, entity),
        # F — what humans attached.
        "evidence": await _section_evidence(db, record),
        # G — the recommendation, and what may be recorded.
        "recommendation": await _section_recommendation(db, record),

        "layer_note": (
            "SOURCE is the ONC/RCE delivery and is never modified. CURATED is "
            "DocuAction's derived value and is always shown against the source "
            "value it derives from. Verification results are OBSERVATIONS by "
            "external sources, not determinations. The determination is the "
            "analyst's, and it is not reportable until QA approves it."),
    }


# ── record lookup ────────────────────────────────────────────────────────────

async def _curated_for(db, record):
    """The Area 2 record behind this case, by source record or by entity."""
    stmt = select(m.RceCuratedRecord)
    if record.source_record_id is not None:
        stmt = stmt.where(
            m.RceCuratedRecord.source_record_id == record.source_record_id)
    elif record.entity_id is not None:
        stmt = stmt.where(
            m.RceCuratedRecord.canonical_entity_id == record.entity_id)
    else:
        return None
    return (await db.execute(stmt)).scalars().first()


async def _source_for(db, record, curated):
    """The Area 1 record behind this case. Immutable, read-only, never edited."""
    source_record_id = record.source_record_id or getattr(
        curated, "source_record_id", None)
    if source_record_id is None:
        return None
    return await db.get(m.RceSourceRecord, source_record_id)


# ── A — SOURCE ───────────────────────────────────────────────────────────────

async def _section_source(db, source, curated, entity, *, include_raw: bool):
    """The delivered values, exactly as received. READ ONLY.

    `parsed` is the delivered line split by the locked 41-field map — the values
    ONC sent, not values anything has touched. `raw_line` is available on
    request for an analyst who needs to see the literal delivered text.

    QHIN and the participant relationship come from the CANONICAL edges written
    at promotion, and are labelled as resolved rather than delivered, because
    that is what they are: the delivery carries an OID, and the edge is what
    DocuAction resolved it to.
    """
    if source is None:
        return {"available": False,
                "note": ("This case has no Area 1 record — it was created "
                         "outside the RCE delivery pipeline.")}

    parsed = dict(source.parsed or {})
    relationship = await _relationship(db, curated, entity)

    return {
        "available": True,
        "read_only": True,
        "source_record_id": str(source.id),
        "delivery": await _delivery_ref(db, source.source_intake_id),
        "line_number": source.line_number,
        "record_sha256": source.record_sha256,
        "parse_status": source.parse_status,
        "parse_note": source.parse_note,
        "identifiers": {
            "source_rce_id": source.source_rce_id,
            "tefcaid": source.tefcaid,
            "hcid": source.hcid,
            "npi": source.npi,
        },
        "delivered_values": parsed,
        "relationship": relationship,
        **({"raw_line": source.raw_line} if include_raw else {}),
        "note": ("Delivered by ONC/RCE and preserved unmodified. Nothing on "
                 "this screen edits it; corrections are recorded as curated "
                 "values against it."),
    }


async def _relationship(db, curated, entity):
    """QHIN and parent, from the canonical edges. Never inferred."""
    entity_id = getattr(entity, "id", None) or getattr(
        curated, "canonical_entity_id", None)
    if entity_id is None:
        return {"resolved": False,
                "note": ("Not promoted, so no canonical relationship exists "
                         "yet. The delivered relationship values are in "
                         "delivered_values.")}

    edges = (await db.execute(
        select(reg.TefcaEntityRelationship.relationship_type,
               reg.TefcaEntityRelationship.parent_entity_id)
        .where(reg.TefcaEntityRelationship.child_entity_id == entity_id,
               reg.TefcaEntityRelationship.status == "active"))).all()
    parents = {kind: parent for kind, parent in edges}
    ids = [p for p in parents.values() if p is not None]
    names = {}
    if ids:
        names = {i: (display or name) for i, name, display in (await db.execute(
            select(reg.TefcaRegEntity.id, reg.TefcaRegEntity.name,
                   reg.TefcaRegEntity.display_name)
            .where(reg.TefcaRegEntity.id.in_(ids)))).all()}

    def ref(kind):
        parent = parents.get(kind)
        if parent is None:
            return None
        return {"entity_id": str(parent), "name": names.get(parent)}

    return {
        "resolved": True,
        "entity_level": getattr(entity, "entity_level", None),
        "qhin": ref("managed_by_qhin"),
        "parent_organization": ref("sub_participant_of"),
        "basis": ("Canonical relationship edges written at promotion. Not "
                  "inferred from name, OID or address."),
    }


async def _delivery_ref(db, intake_id):
    if intake_id is None:
        return None
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        return {"intake_id": str(intake_id)}
    return {
        "intake_id": str(intake.id),
        "delivery_label": intake.delivery_label,
        "original_filename": intake.original_filename,
        "sha256": intake.sha256,
        "received_at": intake.received_at,
    }


# ── B — CURATED ──────────────────────────────────────────────────────────────

async def _section_curated(db, curated):
    """Derived values, each shown against the source value it came from.

    A curated value is NEVER presented as an ONC value. Every correction carries
    the original, the corrected value, the authority under which it was made,
    the stated reason, who made it and who approved it — which is what makes it
    a correction rather than an edit.
    """
    if curated is None:
        return {"available": False,
                "note": "No curated record exists for this case."}

    corrections = (await db.execute(
        select(m.RceCorrectionDetail)
        .where(m.RceCorrectionDetail.curated_record_id == curated.id)
        .order_by(m.RceCorrectionDetail.column_name))).scalars().all()

    return {
        "available": True,
        "curated_record_id": str(curated.id),
        "record_status": curated.record_status,
        "status_reason": curated.status_reason,
        "issue_count": curated.issue_count,
        "correction_count": curated.correction_count,
        "transformation_version": curated.transformation_version,
        "curated_values": {
            "name": curated.name,
            "npi": curated.npi,
            "tefcaid": curated.tefcaid,
            "hcid": curated.hcid,
            "rce_org_oid": curated.rce_org_oid,
            "entity_level": curated.entity_level,
            "sequoia_org_type": curated.sequoia_org_type,
            "org_node_type": curated.org_node_type,
            "operational_status": curated.operational_status,
            "exchange_purposes": curated.exchange_purposes,
            "is_test_record": curated.is_test_record,
        },
        "corrections": [{
            "column": c.column_name,
            "source_value": c.original_value,
            "curated_value": c.corrected_value,
            "basis": c.correction_reason,
            "authority": c.correction_authority,
            "corrected_by": c.corrected_by,
            "approval_actor": c.approval_actor,
            "confidence": c.confidence,
            "qa_status": c.qa_status,
            "timestamp": c.created_at,
            "source_modified": False,
        } for c in corrections],
        "note": ("Curated values are DocuAction's, not ONC's. The source value "
                 "is preserved and shown beside each one. Source Modified: NO."),
    }


# ── C — GOVERNMENT / APPROVED VERIFICATION ───────────────────────────────────

async def _section_verification(db, record):
    """The latest generation of dimension evidence for this case.

    Only the newest generation is returned, because dimension evidence is
    append-only and a re-verification writes a NEW generation beside the old
    one. Returning all of them would show an analyst the same source three times
    with three timestamps and leave them to work out which is current.

    Canonical statuses are passed through EXACTLY. Nothing here maps
    SOURCE_UNAVAILABLE onto NO_MATCH, or a disposition onto a bucket.
    """
    from app.Tefca.models import TEFCADimensionEvidence as DE

    rows = (await db.execute(
        select(DE).where(DE.review_id == record.review_id)
        .order_by(DE.generation_timestamp.desc(), DE.evidence_dimension,
                  DE.source))).scalars().all()
    if not rows and record.entity_id is not None:
        rows = (await db.execute(
            select(DE).where(DE.entity_id == str(record.entity_id))
            .order_by(DE.generation_timestamp.desc(), DE.evidence_dimension,
                      DE.source))).scalars().all()

    if not rows:
        return {
            "available": False,
            "generation": None,
            "dimensions": [],
            "snapshot": record.verification_results or {},
            "note": ("No dimension evidence has been generated for this case. "
                     "The stored verification snapshot, if any, is shown as "
                     "recorded at review time."),
        }

    newest = rows[0].generation_timestamp
    current = [r for r in rows if r.generation_timestamp == newest]

    by_dimension: Dict[str, Dict[str, Any]] = {}
    for row in current:
        entry = by_dimension.setdefault(row.evidence_dimension, {
            "dimension": row.evidence_dimension,
            "disposition": row.dimension_disposition,
            "applicability": row.dimension_applicability,
            "items": [],
        })
        entry["items"].append({
            "source": row.source,
            "source_dataset": row.source_dataset,
            "ppef_component": row.ppef_component,
            "observation": row.disposition,
            "fields_evaluated": row.fields_evaluated,
            "field_matches": row.field_matches,
            "field_conflicts": row.field_conflicts,
            "original_values": row.original_values,
            "normalized_values": row.normalized_values,
            "rule_applied": row.rule_applied,
            "note": row.note,
            "retrieved_at": row.retrieved_at,
            "dataset_version_anchor": row.dataset_version_anchor,
            "query_identifier": row.query_identifier,
            "analyst_notes": getattr(row, "analyst_notes", None),
        })

    return {
        "available": True,
        "generation": newest,
        "dimensions": [by_dimension[k] for k in sorted(by_dimension)],
        "sources_expected": list(GOVERNMENT_SOURCES),
        "snapshot": record.verification_results or {},
        "note": ("Observations, not determinations. A source that did not "
                 "answer reports SOURCE_UNAVAILABLE and is never shown as "
                 "NO_MATCH — an outage is a fact about the source, not about "
                 "this organisation."),
    }


# ── D — USPS ─────────────────────────────────────────────────────────────────

async def _section_usps(db, source, curated):
    """USPS address observation. Evidence — it never rewrites the ONC address.

    Reads the CURRENT USPS platform client (`apis.usps.com`, OAuth 2.0). The
    legacy Web Tools XML endpoint retired on 25 January 2026 and is not used
    here.

    Unconfigured is reported as NOT CONFIGURED with the ONC address still shown.
    A missing credential must never look like an address problem, and it must
    never block the rest of the review.
    """
    from app.tefca_registry.usps_client import get_usps_client

    onc_address = _address_of(source, curated)
    client = get_usps_client()

    if not getattr(client, "configured", False):
        return {
            "available": False,
            "status": "NOT_CONFIGURED",
            "onc_address": onc_address,
            "usps_observed_address": None,
            "source_modified": False,
            "note": ("USPS credentials are not configured in this deployment, "
                     "so no address observation was made. This is a "
                     "deployment fact, not a finding about the address."),
        }

    if not onc_address or not onc_address.get("street"):
        return {
            "available": False,
            "status": "INSUFFICIENT_IDENTIFIER",
            "onc_address": onc_address,
            "usps_observed_address": None,
            "source_modified": False,
            "note": "The delivery carries no street address to verify.",
        }

    from datetime import datetime

    try:
        result = await client.standardize(
            onc_address.get("street") or "",
            city=onc_address.get("city") or "",
            state=onc_address.get("state") or "",
            zip5=onc_address.get("zip") or "",
            secondary=onc_address.get("street2") or "")
    except Exception as exc:  # noqa: BLE001 — USPS must never break a review
        logger.info("USPS observation unavailable: %s", type(exc).__name__)
        return {
            "available": False, "status": "SOURCE_UNAVAILABLE",
            "onc_address": onc_address, "usps_observed_address": None,
            "source_modified": False,
            "note": (f"USPS did not answer ({type(exc).__name__}). Not a "
                     f"finding about the address."),
        }

    # `available` answers "did USPS give us an answer", which is a different
    # question from "is this address good" — the client's own docstring makes
    # that distinction and it is preserved here rather than flattened into one
    # pass/fail. An unavailable result is SOURCE_UNAVAILABLE, never a finding.
    observed = None
    if result.available:
        observed = {
            "street": result.standardized_street,
            "city": result.standardized_city,
            "state": result.standardized_state,
            "zip5": result.zip5,
            "zip4": result.zip4,
        }
    return {
        "available": bool(result.available),
        "status": ("OBSERVED" if result.available else "SOURCE_UNAVAILABLE"),
        "onc_address": onc_address,
        "usps_observed_address": observed,
        "dpv_confirmed": result.dpv_confirmed,
        "is_deliverable": result.is_deliverable,
        "differences": list(result.corrections or []),
        "method": result.method,
        "error": result.error,
        "retrieved_at": datetime.utcnow().isoformat(),
        "provenance": {"source": "USPS_ADDRESSES_API_V3",
                       "platform": "apis.usps.com (OAuth 2.0 client credentials)",
                       "endpoint": "/addresses/v3/address",
                       "legacy_web_tools_used": False,
                       "note": ("Legacy USPS Web Tools retired 25 January 2026 "
                                "and is not called by this path.")},
        "source_modified": False,
        "note": ("USPS is EVIDENCE. The ONC source address is unchanged and "
                 "remains the delivered value; the USPS observation sits "
                 "beside it. `differences` lists what USPS standardised, and "
                 "standardising is not correcting the source."),
    }


def _address_of(source, curated) -> Optional[Dict[str, Any]]:
    """The delivered address, from Area 1. Not a curated or normalised one."""
    parsed = dict(getattr(source, "parsed", None) or {})
    if not parsed:
        return None

    def pick(*names):
        for name in names:
            for key in parsed:
                if key.lower().replace("_", "") == name:
                    value = (parsed.get(key) or "").strip()
                    if value:
                        return value
        return None

    return {
        "street": pick("address", "addressline1", "street", "addr1"),
        "street2": pick("addressline2", "addr2"),
        "city": pick("city", "addresscity"),
        "state": pick("state", "addressstate"),
        "zip": pick("zip", "zipcode", "postalcode", "addresspostalcode"),
        "basis": "As delivered by ONC/RCE. Never a curated value.",
    }


# ── E — WEBSITE / DOMAIN ─────────────────────────────────────────────────────

async def _section_website(db, record, entity):
    """The stored website observation, if one has been made.

    This does NOT fetch. A workspace that dialled an external host every time an
    analyst opened a case would issue one outbound request per page view, and
    the analyst would be waiting on it. Website observation happens in the
    evidence run; this shows what it recorded.
    """
    from app.Tefca.models import TEFCADimensionEvidence as DE
    from app.tefca_registry import website_evidence as web

    rows = (await db.execute(
        select(DE).where(DE.review_id == record.review_id,
                         DE.source == "ENTRANT_WEBSITE")
        .order_by(DE.generation_timestamp.desc()))).scalars().all()
    if not rows:
        return {
            "available": False,
            "observed": None,
            "authoritative": False,
            "authoritative_for": list(web.AUTHORITATIVE_FOR),
            "note": ("No website observation has been recorded for this case. "
                     "Website evidence is corroborating and optional."),
        }

    row = rows[0]
    observed = dict(row.original_values or {})
    return {
        "available": True,
        "website": observed.get("website") or observed.get("url"),
        "domain": observed.get("domain"),
        "reachable": observed.get("reachable"),
        "https": observed.get("https"),
        "organization_name_observed": observed.get("organization_name_observed"),
        "address_observed": observed.get("address_observed"),
        "phone_observed": observed.get("phone_observed"),
        "contact_observed": observed.get("contact_observed"),
        "observation": row.disposition,
        "retrieved_at": row.retrieved_at,
        "generation": row.generation_timestamp,
        "authoritative": False,
        "authoritative_for": list(web.AUTHORITATIVE_FOR),
        "note": ("Self-published corroborating identity and contact evidence. "
                 "Never authoritative for NPI, enrolment, exclusion, "
                 "registration or TEFCA status."),
    }


# ── F — ANALYST EVIDENCE ─────────────────────────────────────────────────────

async def _section_evidence(db, record):
    """Evidence humans attached to this case, with its provenance."""
    from app.Tefca.models import TEFCADimensionEvidence as DE

    rows = (await db.execute(
        select(DE).where(DE.review_id == record.review_id,
                         DE.reviewed_by.isnot(None))
        .order_by(DE.created_at.desc()))).scalars().all()
    return {
        "items": [{
            "evidence_id": str(row.id),
            "dimension": row.evidence_dimension,
            "source": row.source,
            "note": row.analyst_notes,
            "added_by": row.reviewed_by,
            "added_at": row.reviewed_at,
            "provenance": {
                "source_record_identifier": row.source_record_identifier,
                "retrieved_at": row.retrieved_at,
                "dataset_version_anchor": row.dataset_version_anchor,
            },
        } for row in rows],
        "count": len(rows),
        "note": ("Analyst-supplied evidence is recorded separately from system "
                 "evidence and carries its own provenance."),
    }


# ── G — RECOMMENDATION ───────────────────────────────────────────────────────

async def _section_recommendation(db, record):
    """The system classification, the determination in force, and QA history.

    The vocabulary is the EXISTING approved one — CONFIRM / RECLASSIFY against
    the B1-B4 buckets, recorded through `POST /reviews/{id}/determination`. This
    module introduces no decision codes of its own; inventing a parallel
    vocabulary is exactly what would make two reports of the same case
    disagree.
    """
    from app.tefca_registry.qa_gate import (_events, _latest_determination,
                                            _qa_after, effective_determination)

    events = await _events(db, record.review_id)
    determination = _latest_determination(events)
    qa = _qa_after(events, determination) if determination else []

    return {
        "system_classification": {
            "bucket": record.classification_bucket,
            "rule": record.classification_rule,
            "rule_version": record.classification_rule_version,
            "rationale": record.classification_rationale,
            "note": ("A system recommendation. It is not a determination and "
                     "is not reportable."),
        },
        "determination": effective_determination(events),
        "qa_history": [{
            "action": event.qa_action,
            "actor": getattr(event, "actor_email", None),
            "actor_role": event.actor_role,
            "reason": getattr(event, "qa_reason", None)
            or getattr(event, "rationale", None),
            "at": getattr(event, "created_at", None),
        } for event in qa],
        "reportable": record.reportable_at is not None,
        "reportable_at": record.reportable_at,
        "vocabulary": {
            "determination": ["CONFIRM", "RECLASSIFY"],
            "buckets": ["B1", "B2", "B3", "B4"],
            "note": ("The existing approved determination vocabulary. Buckets "
                     "are the ARC classification; D1-D6 are the verification "
                     "dimensions shown under verification, and the two are "
                     "deliberately not collapsed into one code."),
        },
        "submit_to_qa": {
            "route": f"/api/tefca/reviews/{record.review_id}/determination",
            "then": ("QA approves, returns or escalates. Only a QA APPROVE "
                     "makes the finding reportable, and the approving QA actor "
                     "must not be the analyst."),
        },
    }


# ── case state ───────────────────────────────────────────────────────────────

async def _case(db, record):
    from app.tefca_registry.case_assignment import case_state

    try:
        state = await case_state(db, record.review_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("case state unavailable for %s: %s", record.review_id, exc)
        state = None
    return {
        "state": state,
        "assigned_to_user_id": (str(record.assigned_to_user_id)
                                if record.assigned_to_user_id else None),
        "assigned_at": record.assigned_at,
        "sample_id": str(record.sample_id) if record.sample_id else None,
        "created_at": record.created_at,
    }
