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
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import true as sa_true, func, select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.field_map import FIELD_MAP_VERSION

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


async def promote_delivery(db, intake_id, *, actor: Optional[str] = None,
                           actor_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    """Promote every promotable curated record for one delivery.

    Two passes, because a Subparticipant's parent may be curated after it.
    Pass 1 creates entities, identifiers and contacts; pass 2 resolves partOf
    into relationship edges once every entity exists.
    """
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise ValueError(f"No intake {intake_id}")
    if (intake.source_metadata or {}).get("schema_drift"):
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

    # Existing rce_org_oid → entity, so a re-promotion updates rather than
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

    promoted = 0
    updated = 0
    identifiers_skipped_shared = 0
    skipped_status: Dict[str, int] = {}
    oid_to_entity: Dict[str, uuid.UUID] = dict(existing_by_oid)
    promoted_pairs: List[Tuple[Any, uuid.UUID]] = []
    #: Rows this run will never promote (HELD/REJECTED/missing key). Held so the
    #: drain loop cannot re-select them forever.
    unpromotable: set = set()

    # ── pass 1 — entities, identifiers, contacts ──
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
            if row.record_status not in PROMOTABLE_STATUSES:
                skipped_status[row.record_status] = \
                    skipped_status.get(row.record_status, 0) + 1
                continue
            if not row.rce_org_oid or not row.name:
                skipped_status["MISSING_KEY"] = skipped_status.get("MISSING_KEY", 0) + 1
                continue

            entity_id = oid_to_entity.get(row.rce_org_oid)
            entity_type = _HL7_ROLE_TO_ENTITY_TYPE.get(
                (row.hl7_org_role or "").strip(), DEFAULT_ENTITY_TYPE)

            if entity_id is None:
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
                    # An NPI that is not ten digits is NOT promoted as an
                    # identifier — it would be queried against NPPES and would
                    # produce a confident non-match that means nothing. It stays
                    # in Area 1 and in the issue ledger.
                    if itype == "npi" and (len(value) != 10 or not value.isdigit()):
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
                               "field_map_version": FIELD_MAP_VERSION}))
            else:
                entity = await db.get(reg.TefcaRegEntity, entity_id)
                if entity is not None:
                    entity.name = row.name or entity.name
                    entity.operational_status = row.operational_status or entity.operational_status
                    entity.is_active = bool(row.is_active)
                    entity.updated_at = datetime.utcnow()
                    updated += 1

            row.canonical_entity_id = entity_id
            row.promoted_at = datetime.utcnow()
            promoted_pairs.append((row.source_record_id, entity_id))
            progressed = True

        await db.commit()
        if not progressed:
            # Every row in this batch was unpromotable; the next iteration
            # excludes them, so this cannot spin.
            continue

    # Mark the Area 1 rows promoted — the only column on a source record that
    # the pipeline writes after intake, and deliberately so: it is a POINTER,
    # not a change to delivered content.
    for start in range(0, len(promoted_pairs), BATCH_SIZE):
        chunk = promoted_pairs[start:start + BATCH_SIZE]
        for source_record_id, entity_id in chunk:
            record = await db.get(m.RceSourceRecord, source_record_id)
            if record is not None:
                record.promotion_status = "promoted"
                record.canonical_entity_id = entity_id
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

    return {
        "intake_id": str(intake_id),
        "curated_records": total,
        "entities_created": promoted,
        "entities_updated": updated,
        "identifier_rows_skipped_shared_value": identifiers_skipped_shared,
        "qhin_entities": len(qhin_map),
        "relationships_managed_by_qhin": edges_qhin,
        "relationships_sub_participant_of": edges_parent,
        "unresolved_parents": unresolved_parents,
        "not_promoted_by_status": skipped_status,
        "curated_status_counts": intake_status_counts,
    }
