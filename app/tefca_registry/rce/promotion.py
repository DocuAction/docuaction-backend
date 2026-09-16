"""
P8 — promote approved Area 2 records into the canonical TEFCA registry.

THE CHAIN IS NOT OPTIONAL
─────────────────────────
    Area 1 source record → Issue → Correction → Area 2 curated → Registry entity

There is no Area 1 → Registry path. `promote_delivery` reads
`rce_curated_records` and nothing else, so a source record can only reach the
registry by having been curated, and a held record cannot reach it at all.

WHAT GETS PROMOTED
Only CLEAN and CORRECTED. HELD records have an unresolved substantive problem;
REJECTED records could not be parsed. Both remain in Area 1 and Area 2, are
counted in reconciliation, and are excluded from verification — which is the
point of holding them.

EVERY CURATED RECORD GETS A DISPOSITION (2026-09-17)
────────────────────────────────────────────────────
    Received = Created + Updated + Matched/Unchanged + Held + Rejected
             + Missing Key + Excluded

Pass 1 writes exactly one SYSTEM disposition event per curated record into
`rce_disposition_events` (see `dispositions.py`), so the accounting identity
above can be proven from rows rather than asserted from counts. A re-run does
not duplicate: a record whose current disposition already says what this run
would say is left alone; a record whose disposition CHANGES (a HELD record
released by an analyst and now promoted) gets the next sequence.

MATCHED RECORDS: UPDATED, UNCHANGED, OR IN CONFLICT
A curated record whose `rce_org_oid` names an entity the registry already holds
is MATCHED. Its material fields (`dispositions.MATERIAL_FIELDS`) are compared:
if any differ the entity is UPDATED — with a `tefca_entity_versions` row
carrying the before/after snapshot and an `entity_updated` audit row — and if
none differ the entity is MATCHED_UNCHANGED and nothing is touched. Identifiers
(npi, tefcaid, hcid, aaid) are compared SEPARATELY and never silently
overwritten: a delivered value that differs from the registered one raises an
NPI-008 finding in the issue ledger and a CONFLICT_RAISED identifier decision
event, HOLDS the record, and leaves both values side by side until an analyst
decides. This comparison runs for EVERY matched row, including rows already
held by a quality finding and regardless of whether the submitted value is
itself valid: an invalid submitted value against a registered one is still a
conflict a human must see.

IDENTITY: `id`, NOT TEFCAID
The delivery's `id` is the only field observed to be unique (23,566/23,566).
TEFCAID repeats across organisation families — 43 values covering 241 extra
rows, one of them 69 times. Keying identity on TEFCAID would have merged 241
distinct organisations into 43. `rce_org_oid` is therefore the identity key and
TEFCAID is promoted as a non-unique identifier alongside it.

TWO EDGES, TWO MEANINGS
    orgManagingOrg → managed_by_qhin      entity → its QHIN
    partOf         → sub_participant_of   Subparticipant → its Participant

A Participant's partOf repeats its orgManagingOrg. Emitting both edges there
would assert the same fact twice and make "has a Participant parent" true for
every entity in the delivery, so only the QHIN edge is created for a
Participant.

QHINs ARE SYNTHESISED
The 11 orgManagingOrg OIDs resolve to no record in the delivery — the QHINs are
external referents. A QHIN entity is created for each so the hierarchy has a
root to attach to, marked with source 'rce_qhin_synthesised' so nobody mistakes
it for a delivered record.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, true as sa_true, func, select, update

from app.core import request_context
from app.services.npi_validator import validate_npi
from app.tefca_registry import models as reg
from app.tefca_registry.rce import dispositions as disp
from app.tefca_registry.rce import identifier_decisions
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import run_selection
from app.tefca_registry.rce.field_map import FIELD_MAP_VERSION
from app.tefca_registry.rce.curation import UNDECIDED_RESOLUTIONS
from app.tefca_registry.rce.quality_rules import HIGH, HUMAN_REQUIRED, RULE_BY_ID

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000

PROMOTABLE_STATUSES = ("CLEAN", "CORRECTED")

SYSTEM_URI = {
    "rce_org_oid": "urn:docuaction:tefca/identifier/rce-org-oid",
    "tefcaid": "urn:docuaction:tefca/identifier/tefcaid",
    "hcid": "urn:docuaction:tefca/identifier/hcid",
    "aaid": "urn:docuaction:tefca/identifier/aaid",
    "npi": "http://hl7.org/fhir/sid/us-npi",
}

REL_MANAGED_BY_QHIN = "managed_by_qhin"
REL_SUB_PARTICIPANT_OF = "sub_participant_of"

#: Entity type assigned when the delivery gives no better signal. `hl7orgrole`
#: is populated on 0.25% of records, so most entities land here — which is
#: honest: the delivery does not carry a taxonomy.
DEFAULT_ENTITY_TYPE = "provider"

_HL7_ROLE_TO_ENTITY_TYPE = {
    "provider": "provider",
    "diagnostics": "laboratory",
    "agency": "government_agency",
    "payer": "health_plan",
    "HIE/HIO": "health_information_exchange",
}

#: Fields compared on a match to decide UPDATED versus MATCHED_UNCHANGED.
#: Identifiers are deliberately NOT here: they are compared by
#: `_identifier_conflicts` and never overwritten.
MATERIAL_FIELDS = (
    "name", "entity_level", "operational_status", "is_active", "address_line",
    "address_city", "address_state", "address_postal_code", "exchange_purposes",
    "sequoia_org_type", "org_managing_org",
)

#: Identifier types compared on a match, the delivered field each came from,
#: and the entity column that carries the family value when no identifier row
#: does. NPI has no entity column: it lives only in identifier rows.
_COMPARED_IDENTIFIERS: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("npi", "NPI", None),
    ("tefcaid", "TEFCAID", "rce_tefcaid"),
    ("hcid", "HCID", "rce_hcid"),
    ("aaid", "AAID", "rce_aaid"),
)

#: Issue types for identifier conflicts, by identifier type.
CONFLICT_ISSUE_TYPE = {
    "npi": "NPI_EXISTING_VALUE_CONFLICT",
    "tefcaid": "IDENTIFIER_EXISTING_VALUE_CONFLICT",
    "hcid": "IDENTIFIER_EXISTING_VALUE_CONFLICT",
    "aaid": "IDENTIFIER_EXISTING_VALUE_CONFLICT",
}
CONFLICT_RULE_ID = "NPI-008"

#: Promotion-time issue codes live in their own namespace so they can never
#: collide with the quality engine's DQ-<date>-<run>-<seq> codes.
PROMOTION_ISSUE_PREFIX = "PR"


def promotion_issue_code(when: Optional[datetime] = None) -> str:
    """PR-YYYYMMDD-<16 hex>. Unique by construction; 28 characters."""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return f"{PROMOTION_ISSUE_PREFIX}-{stamp}-{uuid.uuid4().hex[:16]}"


def _exclude_test_records() -> bool:
    """The profile switch. Default false; see `Settings.RCE_EXCLUDE_TEST_RECORDS`."""
    try:
        from app.core.config import settings
        return bool(getattr(settings, "RCE_EXCLUDE_TEST_RECORDS", False))
    except Exception:  # noqa: BLE001 — settings unavailable means default
        return False


async def _ensure_qhin_entities(db, qhin_oids: List[str],
                                actor: Optional[str]) -> Dict[str, uuid.UUID]:
    """Create or find a registry entity for each QHIN OID.

    Synthesised, and labelled as such. The delivery references these OIDs but
    contains no row for them, so a QHIN entity here is DocuAction's construct —
    marking it prevents it being read later as delivered evidence.
    """
    mapping: Dict[str, uuid.UUID] = {}
    for oid in sorted(set(o for o in qhin_oids if o)):
        existing = (await db.execute(
            select(reg.TefcaEntityIdentifier.entity_id).where(
                reg.TefcaEntityIdentifier.identifier_type == "rce_org_oid",
                reg.TefcaEntityIdentifier.identifier_value == oid).limit(1)
        )).scalar_one_or_none()
        if existing:
            mapping[oid] = existing
            continue

        entity_id = uuid.uuid4()
        db.add(reg.TefcaRegEntity(
            id=entity_id,
            name=f"QHIN {oid}",
            display_name=f"QHIN {oid}",
            entity_level="qhin",
            entity_type="health_information_network",
            operational_status="active",
            verification_status="not_verified",
            current_version=1,
            is_active=True,
        ))
        await db.flush()
        db.add(reg.TefcaEntityIdentifier(
            id=uuid.uuid4(), entity_id=entity_id,
            identifier_type="rce_org_oid", identifier_value=oid,
            system_uri=SYSTEM_URI["rce_org_oid"], is_primary=True,
            identifier_status="active"))
        db.add(reg.TefcaRegAuditLog(
            id=uuid.uuid4(), entity_id=entity_id, action="entity_created",
            actor_email=actor,
            metadata_={"source": "rce_qhin_synthesised", "oid": oid,
                       "note": ("QHIN referenced by orgManagingOrg but not "
                                "present as a record in the delivery. "
                                "Synthesised so the hierarchy has a root.")}))
        mapping[oid] = entity_id
        await db.flush()
    return mapping


# ── identifier comparison on a match ─────────────────────────────────────────

async def _active_identifier(db, entity_id, identifier_type: str) -> Optional[str]:
    """The entity's current registered value for one identifier type, or None.

    The active identifier row wins; when no row exists the family value on the
    entity column (tefcaid/hcid/aaid) is the registered value. An NPI has no
    entity column, so "no active row" means "no registered NPI".
    """
    value = (await db.execute(
        select(reg.TefcaEntityIdentifier.identifier_value).where(
            reg.TefcaEntityIdentifier.entity_id == entity_id,
            reg.TefcaEntityIdentifier.identifier_type == identifier_type,
            reg.TefcaEntityIdentifier.identifier_status == "active")
        .order_by(reg.TefcaEntityIdentifier.is_primary.desc(),
                  reg.TefcaEntityIdentifier.created_at)
        .limit(1))).scalar_one_or_none()
    if value:
        return value.strip() or None
    column = next((c for t, _f, c in _COMPARED_IDENTIFIERS
                   if t == identifier_type), None)
    if column is None:
        return None
    entity = await db.get(reg.TefcaRegEntity, entity_id)
    raw = getattr(entity, column, None) if entity is not None else None
    return (raw or "").strip() or None


async def _add_missing_identifiers(db, row, entity_id, shared, *, intake_id) -> Dict[str, str]:
    """Register identifiers the delivery supplies that the matched entity lacks.

    A matched entity with NO registered NPI (or TEFCAID/HCID/AAID) that now
    receives a valid one is a material change, not a conflict and not something
    to drop on the floor: the value is written as an active identifier row and
    reported to the caller so the match is accounted as UPDATED with a version
    row. Values shared within the delivery or already registered elsewhere are
    withheld exactly as on the create path; an NPI that fails validation is
    never written (the quality finding describes it).
    Returns {identifier_type: value} for every row added.
    """
    added: Dict[str, str] = {}
    # NPI only. TEFCAID/HCID/AAID are family identifiers that live on the
    # entity COLUMNS (written at create time) and get an identifier ROW only
    # when unique within the delivery; a matched entity's family values are
    # compared for conflict above, not re-registered here.
    for itype, _field_name, _column in _COMPARED_IDENTIFIERS:
        if itype != "npi":
            continue
        submitted = (getattr(row, itype, None) or "").strip() or None
        if not submitted:
            continue
        if not validate_npi(submitted)[0]:
            continue
        if itype in shared and submitted in shared[itype]:
            continue
        existing = await _active_identifier(db, entity_id, itype)
        if existing is not None:
            continue
        db.add(reg.TefcaEntityIdentifier(
            id=uuid.uuid4(), entity_id=entity_id, identifier_type=itype,
            identifier_value=submitted, system_uri=SYSTEM_URI.get(itype),
            is_primary=False, identifier_status="active"))
        added[itype] = submitted
    if added:
        await db.flush()
    return added


async def _open_conflict_issue(db, row, identifier_type: str, field_name: str,
                               submitted: str):
    """An undecided NPI-008 issue for this record/field/value, if one exists."""
    return (await db.execute(
        select(m.RceIssue).where(
            m.RceIssue.source_record_id == row.source_record_id,
            m.RceIssue.rule_id == CONFLICT_RULE_ID,
            m.RceIssue.field_name == field_name,
            m.RceIssue.original_value == submitted,
            m.RceIssue.resolution.in_(sorted(UNDECIDED_RESOLUTIONS)))
        .limit(1))).scalar_one_or_none()


async def _identifier_conflicts(db, row, entity_id, *, intake_id, run_id,
                                stamp: datetime) -> List[Dict[str, Any]]:
    """Compare the delivered identifiers with the registered ones.

    Runs for EVERY matched row, whatever its record_status, and does not require
    the submitted value to be valid: a 9-digit or lettered or checksum-failing
    value delivered against a registered NPI is a conflict a human must see,
    beside the quality finding that already describes the value itself.

    Skips a type only when the delivered value is empty or equals the registered
    one. For each real difference it writes (once) an NPI-008 issue and a
    CONFLICT_RAISED decision event, and returns the still-unresolved conflicts.
    A conflict an analyst has already decided for exactly this submitted value
    is not re-raised.
    """
    conflicts: List[Dict[str, Any]] = []
    for itype, field_name, _column in _COMPARED_IDENTIFIERS:
        submitted = (getattr(row, itype, None) or "").strip() or None
        if not submitted:
            continue
        existing = await _active_identifier(db, entity_id, itype)
        if existing is None or existing == submitted:
            continue

        latest = await identifier_decisions.latest_event(db, entity_id, itype)
        if latest is not None and latest.decision != identifier_decisions.CONFLICT_RAISED \
                and submitted in {latest.submitted_value or None,
                                  latest.selected_value or None}:
            # A human has already decided this exact submitted value. Whatever
            # they chose, the registry reflects it; re-raising would re-hold a
            # record on a question that has been answered.
            continue
        issue = await _open_conflict_issue(db, row, itype, field_name, submitted)
        # Raised already for THIS issue (a re-run of the same delivery)? A new
        # delivery that repeats the conflict gets its own event tied to its own
        # issue, so the ledger row for that delivery carries both values.
        already_raised = (
            latest is not None
            and latest.decision == identifier_decisions.CONFLICT_RAISED
            and issue is not None and latest.issue_id == issue.id)
        newly_written = False
        if issue is None:
            valid_note = ""
            if itype == "npi":
                ok, message = validate_npi(submitted)
                if not ok:
                    valid_note = (f" The submitted value itself fails validation "
                                  f"({message}); that finding is recorded "
                                  f"separately by the quality rules.")
            issue = m.RceIssue(
                id=uuid.uuid4(),
                issue_code=promotion_issue_code(stamp),
                source_intake_id=intake_id,
                source_record_id=row.source_record_id,
                run_id=run_id,
                rule_id=CONFLICT_RULE_ID,
                rule_version=RULE_BY_ID[CONFLICT_RULE_ID].version,
                issue_type=CONFLICT_ISSUE_TYPE[itype],
                severity=HIGH,
                field_name=field_name,
                original_value=submitted,
                suggested_value=existing,
                suggested_source="tefca_entity_identifiers",
                suggested_confidence=None,
                correction_authority=HUMAN_REQUIRED,
                description=(
                    f"The delivered {field_name} {submitted!r} differs from the "
                    f"value registered for the matched entity ({existing!r}). The "
                    f"registered value was retained and the entity was not "
                    f"updated; the submitted value is preserved in Area 1 and "
                    f"Area 2. The record is held until an analyst confirms the "
                    f"existing value, confirms the submitted value, corrects it, "
                    f"or rejects the finding.{valid_note}"),
                resolution="OPEN",
                created_at=datetime.utcnow(),
            )
            db.add(issue)
            await db.flush()
            newly_written = True

        if not already_raised:
            await identifier_decisions.raise_conflict(
                db, entity_id=entity_id, identifier_type=itype,
                submitted_value=submitted, existing_value=existing,
                source_record_id=row.source_record_id, intake_id=intake_id,
                issue_id=issue.id)
        conflicts.append({
            "identifier_type": itype, "field_name": field_name,
            "submitted": submitted, "existing": existing,
            "issue_id": issue.id, "issue_code": issue.issue_code,
            "newly_raised": newly_written or not already_raised,
        })
    return conflicts


# ── material comparison on a match ───────────────────────────────────────────

def _entity_material(entity) -> Dict[str, Any]:
    purposes = entity.exchange_purposes
    if isinstance(purposes, dict):
        purposes = purposes.get("purposes")
    return {
        "name": entity.name,
        "entity_level": entity.entity_level,
        "operational_status": entity.operational_status,
        "is_active": bool(entity.is_active),
        "address_line": entity.address,
        "address_city": entity.city,
        "address_state": entity.state,
        "address_postal_code": entity.zip,
        "exchange_purposes": list(purposes or []),
        "sequoia_org_type": entity.sequoia_org_type,
        "org_managing_org": entity.org_managing_org,
    }


def _row_material(row) -> Dict[str, Any]:
    return {
        "name": row.name,
        "entity_level": row.entity_level or "participant",
        "operational_status": row.operational_status or "active",
        "is_active": bool(row.is_active),
        "address_line": row.address_line,
        "address_city": row.address_city,
        "address_state": row.address_state,
        "address_postal_code": row.address_postal_code,
        "exchange_purposes": list(row.exchange_purposes or []),
        "sequoia_org_type": row.sequoia_org_type,
        "org_managing_org": row.org_managing_org,
    }


def _apply_material(entity, incoming: Dict[str, Any], changed: List[str]) -> None:
    mapping = {
        "name": "name", "entity_level": "entity_level",
        "operational_status": "operational_status", "is_active": "is_active",
        "address_line": "address", "address_city": "city",
        "address_state": "state", "address_postal_code": "zip",
        "sequoia_org_type": "sequoia_org_type",
        "org_managing_org": "org_managing_org",
    }
    for field in changed:
        if field == "exchange_purposes":
            entity.exchange_purposes = {"purposes": list(incoming["exchange_purposes"])}
        elif field == "name":
            entity.name = incoming["name"]
            entity.display_name = incoming["name"]
        else:
            setattr(entity, mapping[field], incoming[field])
    entity.updated_at = datetime.utcnow()


# ── the promotion ────────────────────────────────────────────────────────────

async def promote_delivery(db, intake_id, *, actor: Optional[str] = None,
                           actor_id: Optional[uuid.UUID] = None,
                           disposition_actor_type: str = "SYSTEM",
                           disposition_reason: Optional[str] = None,
                           job_id=None) -> Dict[str, Any]:
    """Promote every promotable curated record for one delivery.

    Two passes, because a Subparticipant's parent may be curated after it.
    Pass 1 creates or updates entities, identifiers and contacts and writes one
    disposition per curated record; pass 2 resolves partOf into relationship
    edges once every entity exists.

    `disposition_actor_type` / `disposition_reason` are used by the analyst
    disposition flow (`curation.apply_disposition`): when a human decision
    releases a record and this drain re-promotes it, the resulting disposition
    is a HUMAN event carrying ANALYST_DISPOSITION and the decision text.
    """
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise ValueError(f"No intake {intake_id}")

    if job_id is None:
        from app.tefca_registry.rce.delivery_jobs import job_for_intake
        job = await job_for_intake(db, intake_id)
        job_id = job.id if job is not None else None
    current_run = await run_selection.current_run(db, intake_id)
    run_id = current_run.id if current_run is not None else None
    stamp = datetime.now(timezone.utc)
    actor_name = actor or "SYSTEM"
    if disposition_actor_type not in ("SYSTEM", "HUMAN"):
        raise ValueError(f"unknown disposition actor type {disposition_actor_type!r}")

    # Current disposition per source record, so a re-run writes nothing it has
    # already said and a changed outcome appends the next sequence.
    current_disposition: Dict[Any, str] = {
        r["source_record_id"]: r["disposition"]
        for r in await disp.current_for_intake(db, intake_id, limit=10_000_000)}
    written: Dict[str, int] = {d: 0 for d in disp.tm.DISPOSITIONS}

    async def dispose(row, disposition: str, reason_code: str, reason: str, *,
                      entity_id=None, changed_fields=None) -> None:
        if current_disposition.get(row.source_record_id) == disposition:
            return
        if disposition_actor_type == "HUMAN":
            reason = (f"{disposition_reason or 'analyst decision'} — "
                      f"{reason_code}: {reason}")
            reason_code_out = disp.REASON_ANALYST
        else:
            reason_code_out = reason_code
        await disp.record(
            db, intake_id=intake_id, source_record_id=row.source_record_id,
            disposition=disposition, reason_code=reason_code_out, reason=reason,
            curated_record_id=row.id, job_id=job_id, entity_id=entity_id,
            changed_fields=changed_fields, actor=actor_name,
            actor_type=disposition_actor_type)
        current_disposition[row.source_record_id] = disposition
        written[disposition] += 1

    if (intake.source_metadata or {}).get("schema_drift"):
        # The refusal is correct, and every record still needs its accounting.
        drifted = (await db.execute(
            select(m.RceCuratedRecord).where(
                m.RceCuratedRecord.source_intake_id == intake_id))).scalars().all()
        for row in drifted:
            await dispose(row, disp.HELD, disp.REASON_HELD_SCHEMA,
                          "The delivery's header does not match the locked field "
                          "map; promotion is held until the map is reconciled.")
        await db.commit()
        raise ValueError(
            f"Intake {intake_id} was delivered with a schema that does not match "
            f"the locked field map. Promotion is held until the map is "
            f"reconciled — promoting an unknown schema would mis-assign values.")

    total = int((await db.execute(
        select(func.count()).select_from(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.source_intake_id == intake_id))).scalar() or 0)

    qhin_oids = [row[0] for row in (await db.execute(
        select(m.RceCuratedRecord.org_managing_org).distinct().where(
            m.RceCuratedRecord.source_intake_id == intake_id,
            m.RceCuratedRecord.org_managing_org.isnot(None)))).all()]
    qhin_map = await _ensure_qhin_entities(db, qhin_oids, actor)
    await db.commit()

    # Existing rce_org_oid → entity, so a re-promotion matches rather than
    # duplicates. The delivery's `id` is unique, so this is a safe key.
    existing_by_oid: Dict[str, uuid.UUID] = dict(
        (value, entity_id) for value, entity_id in (await db.execute(
            select(reg.TefcaEntityIdentifier.identifier_value,
                   reg.TefcaEntityIdentifier.entity_id).where(
                reg.TefcaEntityIdentifier.identifier_type == "rce_org_oid"))).all())

    # WHICH IDENTIFIER VALUES ARE SAFE TO WRITE AS IDENTIFIER ROWS
    #
    # `tefca_entity_identifiers` carries a UNIQUE index on
    # (identifier_type, identifier_value, system_uri). The delivery does not
    # honour that for every identifier: TEFCAID repeats across 241 rows, HCID
    # across 4, AAID across 3. Those repeats are real organisations sharing a
    # family identifier, not duplicates to be merged.
    #
    # So the shared values are written to the ENTITY as columns — where a
    # lookup correctly returns the whole family — and an identifier ROW is
    # written only where the value is unique within the delivery. That keeps the
    # uniqueness guarantee intact for NPI and the rest rather than relaxing it
    # for everyone to accommodate one field.
    shared: Dict[str, set] = {}
    for column, key in (("tefcaid", "tefcaid"), ("hcid", "hcid"),
                        ("aaid", "aaid"), ("npi", "npi")):
        rows = (await db.execute(
            select(getattr(m.RceCuratedRecord, column), func.count())
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   getattr(m.RceCuratedRecord, column).isnot(None))
            .group_by(getattr(m.RceCuratedRecord, column))
            .having(func.count() > 1))).all()
        shared[key] = {value for value, _count in rows}
    logger.info("shared identifier values in delivery: %s",
                {k: len(v) for k, v in shared.items()})
    # ACROSS DELIVERIES, THE SAME RULE. An identifier value that already has a
    # row in `tefca_entity_identifiers` — because an EARLIER delivery promoted a
    # different organisation carrying the same NPI, TEFCAID, HCID or AAID —
    # would violate the same unique index at flush time. Before this check the
    # collision surfaced as an IntegrityError inside the promotion batch and the
    # whole delivery job FAILED at stage PROMOTION (reproduced 2026-09-14 with a
    # second synthetic delivery). The value is still written to the entity
    # columns; only the identifier ROW is withheld, exactly as for a value shared
    # within one delivery. Nothing is merged and nothing is inferred: two
    # organisations that both declare an NPI remain two entities, and the
    # collision is visible in the promotion summary.
    for column, key in (("tefcaid", "tefcaid"), ("hcid", "hcid"),
                        ("aaid", "aaid"), ("npi", "npi")):
        delivered = {value for (value,) in (await db.execute(
            select(getattr(m.RceCuratedRecord, column)).distinct().where(
                m.RceCuratedRecord.source_intake_id == intake_id,
                getattr(m.RceCuratedRecord, column).isnot(None)))).all()
            if value}
        if not delivered:
            continue
        already = {value for (value,) in (await db.execute(
            select(reg.TefcaEntityIdentifier.identifier_value).where(
                reg.TefcaEntityIdentifier.identifier_type == key,
                reg.TefcaEntityIdentifier.identifier_value.in_(delivered)))).all()}
        if already:
            shared[key] |= already
    logger.info("identifier values already registered by an earlier delivery: %s",
                {k: len(v) for k, v in shared.items()})

    exclude_test = _exclude_test_records()

    promoted = 0
    updated = 0
    unchanged = 0
    conflicts_raised = 0
    records_in_conflict = 0
    identifiers_skipped_shared = 0
    identifiers_skipped_invalid = 0
    skipped_status: Dict[str, int] = {}
    oid_to_entity: Dict[str, uuid.UUID] = dict(existing_by_oid)
    promoted_pairs: List[Tuple[Any, uuid.UUID]] = []
    #: Rows this run will never promote (HELD/REJECTED/missing key/excluded/
    #: in conflict). Held so the drain loop cannot re-select them forever.
    unpromotable: set = set()
    #: Area 1 promotion_status mirror for rows that are NOT promoted; the
    #: promoted ones are marked from committed state below.
    mirror: Dict[str, List[Any]] = {"held": [], "excluded": []}

    def _park(row, status_key: str, mirror_as: str) -> None:
        """Record a row this run cannot promote, so the drain terminates.

        Every non-promotable branch of pass 1 calls this before `continue`.
        A row that cannot be promoted keeps `canonical_entity_id` NULL and would
        otherwise be re-selected on every pass; without this the final
        iteration — where only such rows remain — never ends, and the Area 1
        marking and pass 2 are never reached.
        """
        skipped_status[status_key] = skipped_status.get(status_key, 0) + 1
        unpromotable.add(row.id)
        mirror[mirror_as].append(row.source_record_id)

    # ── pass 1 — entities, identifiers, contacts, dispositions ──
    #
    # DRAIN, DO NOT PAGINATE. This loop commits every batch and sets
    # canonical_entity_id as it goes, so it is walking a set it is itself
    # shrinking. LIMIT/OFFSET over that re-presented a row that had already been
    # promoted earlier in the same run: the second visit found no entry in
    # oid_to_entity, created a SECOND entity for the same rce_org_oid, and died
    # on the unique index over (identifier_type, identifier_value, system_uri) --
    # partway through, with earlier batches already committed.
    #
    # Selecting only rows that still need promotion makes the loop idempotent:
    # it terminates when nothing is left, a re-run after a failure resumes
    # instead of restarting, and no row can be visited twice.
    # Independent of record_status: a record whose current quality run still
    # carries an undecided HIGH/CRITICAL finding is held, whatever a legacy path
    # wrote into record_status (review finding M-1, 2026-09-16).
    from app.tefca_registry.rce.curation import _blocking_by_record
    undecided_holding = {
        record_id for record_id, entry in (await _blocking_by_record(db, intake_id)).items()
        if entry["issues"]}

    while True:
        rows = (await db.execute(
            select(m.RceCuratedRecord)
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.canonical_entity_id.is_(None),
                   m.RceCuratedRecord.id.notin_(unpromotable) if unpromotable
                   else sa_true())
            .order_by(m.RceCuratedRecord.id)
            .limit(BATCH_SIZE))).scalars().all()
        if not rows:
            break
        progressed = False

        for row in rows:
            # REJECTED: the line could not be parsed; nothing positional is
            # trustworthy, so it is neither matched nor compared.
            if row.record_status == "REJECTED":
                _park(row, "REJECTED", "excluded")
                await dispose(row, disp.REJECTED, disp.REASON_REJECTED_PARSE,
                              row.status_reason or "Source line could not be parsed.")
                continue

            missing_key = not row.rce_org_oid or not row.name
            entity_id = oid_to_entity.get(row.rce_org_oid) if row.rce_org_oid else None
            if entity_id is None and row.rce_org_oid:
                # Safety net, independent of the in-memory map. If an identifier
                # row for this oid already exists, adopt its entity rather than
                # creating a rival one -- creating the rival is what violated the
                # unique index and aborted promotion mid-delivery.
                found = (await db.execute(
                    select(reg.TefcaEntityIdentifier.entity_id).where(
                        reg.TefcaEntityIdentifier.identifier_type == "rce_org_oid",
                        reg.TefcaEntityIdentifier.identifier_value == row.rce_org_oid)
                    .limit(1))).scalar_one_or_none()
                if found is not None:
                    entity_id = found
                    oid_to_entity[row.rce_org_oid] = entity_id

            # IDENTIFIER COMPARISON — for every matched row, whatever its status.
            conflicts: List[Dict[str, Any]] = []
            if entity_id is not None:
                conflicts = await _identifier_conflicts(
                    db, row, entity_id, intake_id=intake_id, run_id=run_id,
                    stamp=stamp)
                conflicts_raised += sum(1 for c in conflicts if c["newly_raised"])

            quality_hold = (row.record_status not in PROMOTABLE_STATUSES
                            or row.source_record_id in undecided_holding)
            if quality_hold and row.record_status in PROMOTABLE_STATUSES:
                row.record_status = "HELD"
                row.status_reason = ("Held: an undecided holding-severity finding "
                                     "remains in the current quality run.")

            if conflicts:
                records_in_conflict += 1
                codes = ", ".join(f"{c['field_name']} {c['submitted']!r} vs "
                                  f"registered {c['existing']!r} ({c['issue_code']})"
                                  for c in conflicts)
                if row.record_status != "HELD":
                    row.record_status = "HELD"
                    row.status_reason = (
                        f"{len(conflicts)} identifier conflict(s) with the matched "
                        f"registry entity: {codes}. Held from promotion until an "
                        f"analyst decides; the registered value was retained and "
                        f"the submitted value is preserved.")
                elif "identifier conflict" not in (row.status_reason or ""):
                    row.status_reason = (
                        f"{row.status_reason or 'Held.'} Also {len(conflicts)} "
                        f"identifier conflict(s) with the matched registry "
                        f"entity: {codes}.")
                _park(row, "HELD", "held")
                if quality_hold:
                    reason_code = disp.REASON_HELD_QUALITY
                    reason = (f"{disp.REASON_HELD_QUALITY}; "
                              f"{disp.REASON_HELD_CONFLICT}. Held by a quality "
                              f"finding AND by an identifier conflict: {codes}.")
                else:
                    reason_code = disp.REASON_HELD_CONFLICT
                    reason = (f"{disp.REASON_HELD_CONFLICT}. Delivered identifier "
                              f"differs from the registered value: {codes}.")
                await dispose(row, disp.HELD, reason_code, reason, entity_id=entity_id)
                continue

            if quality_hold:
                _park(row, row.record_status, "held")
                await dispose(row, disp.HELD, disp.REASON_HELD_QUALITY,
                              row.status_reason or "Held by an unresolved quality "
                              "finding at holding severity.")
                continue
            if missing_key:
                _park(row, "MISSING_KEY", "excluded")
                await dispose(row, disp.MISSING_KEY, disp.REASON_MISSING_KEY,
                              "The record carries no rce_org_oid or no name; it "
                              "cannot be keyed or named in the registry.")
                continue
            if row.is_test_record and exclude_test:
                _park(row, "EXCLUDED", "excluded")
                await dispose(row, disp.EXCLUDED, disp.REASON_EXCLUDED_TEST,
                              "The organisation name matches a test-artefact "
                              "pattern and the profile excludes test records. "
                              "Preserved in Area 1 and Area 2; not promoted.")
                continue

            entity_type = _HL7_ROLE_TO_ENTITY_TYPE.get(
                (row.hl7_org_role or "").strip(), DEFAULT_ENTITY_TYPE)

            if entity_id is None:
                entity_id = uuid.uuid4()
                db.add(reg.TefcaRegEntity(
                    id=entity_id,
                    name=row.name,
                    display_name=row.name,
                    entity_level=row.entity_level or "participant",
                    entity_type=entity_type,
                    operational_status=row.operational_status or "active",
                    verification_status="not_verified",
                    state=(row.address_state or None),
                    city=(row.address_city or None),
                    zip=(row.address_postal_code or None),
                    address=(row.address_line or None),
                    exchange_purposes={"purposes": list(row.exchange_purposes or [])},
                    current_version=1,
                    is_active=bool(row.is_active),
                    # RCE attributes as columns. Every entity keeps its family
                    # TEFCAID here whether or not it also gets an identifier row.
                    rce_org_oid=row.rce_org_oid,
                    rce_tefcaid=row.tefcaid,
                    rce_hcid=row.hcid,
                    rce_aaid=row.aaid,
                    sequoia_org_type=row.sequoia_org_type,
                    org_node_type=row.org_node_type,
                    hl7_org_role=row.hl7_org_role,
                    org_managing_org=row.org_managing_org,
                    is_test_record=bool(row.is_test_record),
                    rce_attributes=dict(row.rce_attributes or {}),
                    source_record_id=row.source_record_id,
                ))
                await db.flush()
                oid_to_entity[row.rce_org_oid] = entity_id
                promoted += 1
                for itype, value in (
                    ("rce_org_oid", row.rce_org_oid), ("tefcaid", row.tefcaid),
                    ("hcid", row.hcid), ("aaid", row.aaid), ("npi", row.npi),
                ):
                    if not value:
                        continue
                    # An NPI that does not pass the CMS validator — length,
                    # format AND Luhn check digit — is NOT promoted as an
                    # identifier. It would be queried against NPPES and would
                    # produce a confident non-match that means nothing. It stays
                    # in Area 1, in Area 2 and in the issue ledger.
                    if itype == "npi" and not validate_npi(value)[0]:
                        identifiers_skipped_invalid += 1
                        continue
                    # Shared across the delivery — the value lives on the
                    # entity columns instead. Writing it here would violate the
                    # identifier table's uniqueness guarantee.
                    if itype in shared and value in shared[itype]:
                        identifiers_skipped_shared += 1
                        continue
                    db.add(reg.TefcaEntityIdentifier(
                        id=uuid.uuid4(), entity_id=entity_id,
                        identifier_type=itype, identifier_value=value,
                        system_uri=SYSTEM_URI.get(itype),
                        is_primary=(itype == "rce_org_oid"),
                        identifier_status="active"))
                if row.contact:
                    db.add(m.TefcaEntityContact(
                        id=uuid.uuid4(), entity_id=entity_id,
                        source_record_id=row.source_record_id,
                        contact_purpose=row.contact.get("contact_purpose"),
                        company=row.contact.get("contact_company"),
                        name=row.contact.get("contact_name"),
                        phone=row.contact.get("contact_phone"),
                        email=row.contact.get("contact_email"),
                        address_text=row.contact.get("contact_address_text"),
                        address_line=row.contact.get("contact_address_line"),
                        address_city=row.contact.get("contact_address_city"),
                        address_state=row.contact.get("contact_address_state"),
                        address_postal_code=row.contact.get("contact_address_postalCode"),
                        address_country=row.contact.get("contact_address_country"),
                    ))
                db.add(reg.TefcaEntityVersion(
                    id=uuid.uuid4(), entity_id=entity_id, version_number=1,
                    snapshot_data={
                        "name": row.name, "entity_level": row.entity_level,
                        "rce_org_oid": row.rce_org_oid, "tefcaid": row.tefcaid,
                        "hcid": row.hcid, "npi": row.npi,
                        "operational_status": row.operational_status,
                        "is_test_record": bool(row.is_test_record),
                        "transformation_version": row.transformation_version,
                    },
                    change_reason="initial_import", changed_by=actor_id))
                db.add(reg.TefcaRegAuditLog(
                    id=uuid.uuid4(), entity_id=entity_id, action="entity_created",
                    actor_email=actor,
                    metadata_={"source": "rce_promotion",
                               "intake_id": str(intake_id),
                               "source_record_id": str(row.source_record_id),
                               "curated_record_id": str(row.id),
                               "field_map_version": FIELD_MAP_VERSION,
                               "correlation_id": request_context.correlation_id()}))
                await dispose(row, disp.CREATED, disp.REASON_CREATED,
                              "No registry entity carried this rce_org_oid; a new "
                              "entity was created from the curated record.",
                              entity_id=entity_id)
            else:
                entity = await db.get(reg.TefcaRegEntity, entity_id)
                if entity is None:
                    # The identifier row points at an entity that does not
                    # exist. That is a registry integrity defect, not something
                    # to paper over by creating a rival; hold the row.
                    row.record_status = "HELD"
                    row.status_reason = (
                        f"rce_org_oid {row.rce_org_oid} is registered to entity "
                        f"{entity_id}, which does not exist. Registry integrity "
                        f"must be repaired before this record can be matched.")
                    _park(row, "HELD", "held")
                    await dispose(row, disp.HELD, disp.REASON_HELD_QUALITY,
                                  row.status_reason)
                    continue
                before = _entity_material(entity)
                incoming = _row_material(row)
                material_changed = disp.material_changes(before, incoming, MATERIAL_FIELDS)
                added_identifiers = await _add_missing_identifiers(
                    db, row, entity_id, shared, intake_id=intake_id)
                changed = material_changed + [f"identifier:{t}" for t in added_identifiers]
                if changed:
                    if material_changed:
                        _apply_material(entity, incoming, material_changed)
                    version_number = int(entity.current_version or 1) + 1
                    entity.current_version = version_number
                    db.add(reg.TefcaEntityVersion(
                        id=uuid.uuid4(), entity_id=entity_id,
                        version_number=version_number,
                        snapshot_data={
                            "change": "rce_promotion_update",
                            "changed_fields": changed,
                            "before": {f: before[f] for f in material_changed},
                            "after": {f: incoming[f] for f in material_changed},
                            "identifiers_added": added_identifiers,
                            "intake_id": str(intake_id),
                            "source_record_id": str(row.source_record_id),
                            "curated_record_id": str(row.id),
                            "transformation_version": row.transformation_version,
                        },
                        change_reason="rce_promotion_update",
                        change_summary=(f"Delivery updated {len(changed)} material "
                                        f"field(s): {', '.join(changed)}"),
                        changed_by=actor_id))
                    db.add(reg.TefcaRegAuditLog(
                        id=uuid.uuid4(), entity_id=entity_id, action="entity_updated",
                        actor_id=actor_id, actor_email=actor,
                        metadata_={"source": "rce_promotion",
                                   "intake_id": str(intake_id),
                                   "source_record_id": str(row.source_record_id),
                                   "curated_record_id": str(row.id),
                                   "changed_fields": changed,
                                   "version_number": version_number,
                                   "correlation_id": request_context.correlation_id()}))
                    updated += 1
                    await dispose(row, disp.UPDATED, disp.REASON_UPDATED,
                                  f"Matched entity {entity_id} by rce_org_oid; "
                                  f"material fields differed and were updated: "
                                  f"{', '.join(changed)}. Version {version_number} "
                                  f"records the before/after values.",
                                  entity_id=entity_id, changed_fields=changed)
                else:
                    unchanged += 1
                    await dispose(row, disp.MATCHED_UNCHANGED, disp.REASON_UNCHANGED,
                                  f"Matched entity {entity_id} by rce_org_oid; no "
                                  f"material field differs. Nothing was written to "
                                  f"the entity.",
                                  entity_id=entity_id)

            row.canonical_entity_id = entity_id
            row.promoted_at = datetime.utcnow()
            promoted_pairs.append((row.source_record_id, entity_id))
            progressed = True

        await db.commit()
        if not progressed:
            # Every row in this batch was unpromotable. They are now all in
            # `unpromotable`, so the next iteration's query excludes them and the
            # candidate set strictly shrinks — this cannot spin.
            continue

    # Mark the Area 1 rows promoted — the only column on a source record that
    # the pipeline writes after intake, and deliberately so: it is a POINTER,
    # not a change to delivered content.
    # DERIVED FROM THE DATABASE, NOT FROM THIS RUN. Marking only the pairs this
    # invocation happened to promote left Area 1 permanently behind Area 2 when
    # an earlier run aborted partway: those records were promoted, but no later
    # run knew to mark them, and reconciliation's "Area 1 promotion markers agree
    # with Area 2" check failed with no way to recover. Re-deriving the work from
    # rce_curated_records makes this loop self-healing and idempotent - it marks
    # whatever is still unmarked, whichever run promoted it.
    while True:
        pending = (await db.execute(
            select(m.RceCuratedRecord.source_record_id,
                   m.RceCuratedRecord.canonical_entity_id)
            .join(m.RceSourceRecord,
                  m.RceSourceRecord.id == m.RceCuratedRecord.source_record_id)
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.canonical_entity_id.isnot(None),
                   or_(m.RceSourceRecord.promotion_status.is_(None),
                       m.RceSourceRecord.promotion_status != "promoted"))
            .limit(BATCH_SIZE))).all()
        if not pending:
            break
        for source_record_id, entity_id in pending:
            record = await db.get(m.RceSourceRecord, source_record_id)
            if record is not None:
                record.promotion_status = "promoted"
                record.canonical_entity_id = entity_id
        await db.commit()

    # The mirror for rows NOT promoted: HELD → held; REJECTED / MISSING_KEY /
    # EXCLUDED → excluded. Still only `promotion_status`; delivered content is
    # never touched.
    for status_value, ids in mirror.items():
        for offset in range(0, len(ids), BATCH_SIZE):
            chunk = ids[offset:offset + BATCH_SIZE]
            await db.execute(
                update(m.RceSourceRecord)
                .where(m.RceSourceRecord.id.in_(chunk),
                       m.RceSourceRecord.canonical_entity_id.is_(None),
                       m.RceSourceRecord.promotion_status != status_value)
                .values(promotion_status=status_value))
    await db.commit()

    # ── pass 2 — relationships ──
    edges_qhin = 0
    edges_parent = 0
    unresolved_parents = 0
    today = date.today()

    existing_edges = set((str(p), str(c), t) for p, c, t in (await db.execute(
        select(reg.TefcaEntityRelationship.parent_entity_id,
               reg.TefcaEntityRelationship.child_entity_id,
               reg.TefcaEntityRelationship.relationship_type))).all())

    for offset in range(0, total, BATCH_SIZE):
        rows = (await db.execute(
            select(m.RceCuratedRecord)
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.canonical_entity_id.isnot(None))
            .order_by(m.RceCuratedRecord.id)
            .limit(BATCH_SIZE).offset(offset))).scalars().all()

        for row in rows:
            child_id = row.canonical_entity_id
            if not child_id:
                continue

            qhin_id = qhin_map.get(row.org_managing_org or "")
            if qhin_id and str(qhin_id) != str(child_id):
                key = (str(qhin_id), str(child_id), REL_MANAGED_BY_QHIN)
                if key not in existing_edges:
                    db.add(reg.TefcaEntityRelationship(
                        id=uuid.uuid4(), parent_entity_id=qhin_id,
                        child_entity_id=child_id,
                        relationship_type=REL_MANAGED_BY_QHIN,
                        effective_date=today, status="active", source="import",
                        notes="orgManagingOrg — entity to its managing QHIN."))
                    existing_edges.add(key)
                    edges_qhin += 1

            # A Participant's partOf repeats its QHIN. No second edge: the
            # managed_by_qhin edge above already states that fact.
            if row.sequoia_org_type == "Participant":
                continue
            if not row.part_of or row.part_of == row.org_managing_org:
                continue

            parent_entity = oid_to_entity.get(row.part_of)
            if parent_entity is None:
                unresolved_parents += 1
                continue
            if str(parent_entity) == str(child_id):
                continue
            key = (str(parent_entity), str(child_id), REL_SUB_PARTICIPANT_OF)
            if key not in existing_edges:
                db.add(reg.TefcaEntityRelationship(
                    id=uuid.uuid4(), parent_entity_id=parent_entity,
                    child_entity_id=child_id,
                    relationship_type=REL_SUB_PARTICIPANT_OF,
                    effective_date=today, status="active", source="import",
                    notes="partOf — Subparticipant to its Participant."))
                existing_edges.add(key)
                edges_parent += 1
        await db.commit()

    intake_status_counts = dict((status, int(count)) for status, count in (
        await db.execute(
            select(m.RceCuratedRecord.record_status, func.count())
            .where(m.RceCuratedRecord.source_intake_id == intake_id)
            .group_by(m.RceCuratedRecord.record_status))).all())
    disposition_counts = await disp.counts_for_intake(db, intake_id)

    return {
        "intake_id": str(intake_id),
        "job_id": str(job_id) if job_id else None,
        "run_id": str(run_id) if run_id else None,
        "curated_records": total,
        "entities_created": promoted,
        "entities_updated": updated,
        "entities_unchanged": unchanged,
        "entities_matched": updated + unchanged + records_in_conflict,
        "conflicts_raised": conflicts_raised,
        "records_in_identifier_conflict": records_in_conflict,
        "identifier_rows_skipped_shared_value": identifiers_skipped_shared,
        "identifier_rows_skipped_invalid_npi": identifiers_skipped_invalid,
        "qhin_entities": len(qhin_map),
        "relationships_managed_by_qhin": edges_qhin,
        "relationships_sub_participant_of": edges_parent,
        "unresolved_parents": unresolved_parents,
        "not_promoted_by_status": skipped_status,
        "curated_status_counts": intake_status_counts,
        "dispositions": disposition_counts,
        "dispositions_written_this_run": {k: v for k, v in written.items() if v},
    }
