"""
TEFCA registry — FHIR R4 Bundle import engine.

Imports a FHIR R4 ``Bundle`` (type: collection) of ``Organization`` resources
into the existing ``tefca_reg_*`` tables. No schema changes. Follows the module's
existing conventions (models in ``models.py``, JSONB snapshot/exchange_purposes as
dicts, audit via ``tefca_reg_audit_log``).

Behaviour
---------
* Two-pass: pass 1 creates entities + identifiers + endpoints; pass 2 resolves
  ``partOf`` references into relationships (handles any ordering in the Bundle).
* Idempotent: an entity whose TEFCAID **or** HCID already exists is SKIPPED
  (never overwritten).
* Fault-tolerant: a single bad resource is recorded in the batch ``errors`` and
  skipped; the batch continues.
* Batch-tracked: every run writes a ``tefca_import_batches`` row.

The shared ``persist_import`` here is also used by ``csv_import.py`` so the two
importers produce identical structures.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.services.npi_validator import validate_for_import
from app.tefca_registry import models as reg

logger = logging.getLogger(__name__)


def safe_import_error(label: str, exc: Exception, context: str = "") -> str:
    """Caller-safe text for a per-row import failure.

    The raw exception is deliberately NOT returned. A database error carries the
    ORM name, the driver, the constraint, the table, the column list and the
    whole statement; returning it hands an API client a map of the schema. The
    detail is logged server-side instead, where an operator can still find it.

    Never surface: table names, column names, SQL, constraint names, ORM or
    driver class names.
    """
    logger.error("import row failure (%s) label=%r: %s: %s",
                 context or "import", label, type(exc).__name__, exc, exc_info=True)
    if isinstance(exc, IntegrityError):
        return (f"{label}: Duplicate entity identifier. An entity with this "
                f"identifier already exists.")
    if isinstance(exc, ValueError):
        # Our own validation text ("missing required column(s): TEFCAID"), which
        # is the actionable part of a bad row. It contains no database internals.
        return f"{label}: {exc}"
    return (f"{label}: Unable to process this row. Please verify the data and "
            f"retry.")

# Canonical system URI per identifier type (matches the seed convention).
SYSTEM_URI = {
    "npi": "http://hl7.org/fhir/sid/us-npi",
    "hcid": "urn:ietf:rfc:3986",
    "ccn": "urn:oid:2.16.840.1.113883.4.336",
    "clia": "urn:oid:2.16.840.1.113883.4.7",
    "naic": "urn:oid:2.16.840.1.113883.6.300",
    "tefcaid": "https://rce.sequoiaproject.org/fhir/identifier/tefcaid",
}

# FHIR identifier system -> our identifier_type (for system-based detection).
_SYSTEM_TO_TYPE = {
    "http://hl7.org/fhir/sid/us-npi": "npi",
    "urn:oid:2.16.840.1.113883.4.336": "ccn",
    "urn:oid:2.16.840.1.113883.4.7": "clia",
    "urn:oid:2.16.840.1.113883.6.300": "naic",
}

# child relationship_type by the child's level.
REL_TYPE_BY_LEVEL = {
    "participant": "belongs_to",
    "sub_participant": "sub_participant_of",
    "child": "member_of",
}

ENTITY_LEVELS = {"qhin", "participant", "sub_participant", "child"}


@dataclass
class ParsedEntity:
    """Normalized entity produced by either the FHIR or CSV parser."""
    key: str                                   # fhir id (FHIR) or TEFCAID (CSV) — parent-resolution key
    name: str
    level: str
    entity_type: str
    operational_status: str
    state: Optional[str] = None
    city: Optional[str] = None
    zip: Optional[str] = None
    address: Optional[str] = None
    fhir_resource: Optional[dict] = None
    exchange_purposes: dict = field(default_factory=lambda: {"purposes": []})
    identifiers: list = field(default_factory=list)   # [(itype, value, system_uri, is_primary)]
    endpoints: list = field(default_factory=list)     # [dict(**endpoint columns)]
    parent_ref: Optional[str] = None                  # parent key (fhir id or ParentTEFCAID)


# ── shared persistence (used by both FHIR and CSV importers) ──────────────────

async def persist_import(
    session: AsyncSession,
    *,
    source_type: str,
    filename: Optional[str],
    file_checksum: Optional[str],
    file_size: Optional[int],
    parsed: list,
    total: int,
    pre_errors: Optional[list] = None,
    resolve_db_parent: Callable[[AsyncSession, str], Awaitable[Optional[uuid.UUID]]],
    actor_id=None,
    actor_email=None,
    ip_address=None,
) -> dict:
    """Persist a parsed import (entities/identifiers/relationships/versions/audit)
    and its batch record. Idempotent (skip existing TEFCAID/HCID); fault-tolerant
    (per-entity savepoints)."""
    t0 = time.monotonic()
    started = datetime.utcnow()
    err_list = list(pre_errors or [])

    batch = reg.TefcaImportBatch(
        id=uuid.uuid4(), source_type=source_type, filename=filename,
        file_checksum=file_checksum, file_size_bytes=file_size,
        status="processing", total_records=total,
        imported_count=0, updated_count=0, skipped_count=0,
        # A COPY. `errors` is a plain JSONB column with no MutableList wrapper,
        # so SQLAlchemy's committed-state snapshot keeps a reference to whatever
        # list it is handed. Passing the live err_list means that snapshot is
        # mutated in step with it, old and new compare equal at assignment time,
        # no UPDATE is emitted, and the batch keeps the empty list it was first
        # flushed with — leaving an auditor an error_count with no error detail.
        error_count=len(err_list), errors=list(err_list), imported_by=actor_id,
        started_at=started,
    )
    session.add(batch)
    await session.flush()
    session.add(reg.TefcaRegAuditLog(
        id=uuid.uuid4(), entity_id=None, action="import_started",
        actor_id=actor_id, actor_email=actor_email,
        metadata_={"source": source_type, "filename": filename, "records": total},
        ip_address=ip_address))

    # Pre-load existing mandatory-identifier values for idempotent skip.
    existing = set((await session.execute(
        select(reg.TefcaEntityIdentifier.identifier_value).where(
            reg.TefcaEntityIdentifier.identifier_type.in_(("tefcaid", "hcid"))))).scalars().all())

    errors = len(err_list)
    imported = 0
    skipped = 0
    key_to_uuid: dict[str, uuid.UUID] = {}
    created: list[tuple[ParsedEntity, uuid.UUID]] = []
    # NPIs that failed the check digit. Reported alongside the batch result so an
    # operator sees them without trawling the audit table; never a rejection.
    validation_warnings: list[dict] = []

    # ── PASS 1 — entities + identifiers + endpoints + audit ──
    for p in parsed:
        dup_vals = [v for (t, v, _u, _pri) in p.identifiers if t in ("tefcaid", "hcid")]
        if any(v in existing for v in dup_vals):
            skipped += 1
            err_list.append(f"Entity {p.name} already exists")
            continue
        try:
            async with session.begin_nested():
                eid = uuid.uuid4()
                session.add(reg.TefcaRegEntity(
                    id=eid, name=p.name, display_name=p.name,
                    entity_level=p.level, entity_type=p.entity_type,
                    operational_status=p.operational_status,
                    verification_status="not_verified",
                    state=p.state, city=p.city, zip=p.zip, address=p.address,
                    fhir_resource=p.fhir_resource, exchange_purposes=p.exchange_purposes,
                    current_version=1, is_active=(p.operational_status != "inactive"),
                ))
                # Flush the parent row first: these child tables have plain FK
                # columns (no ORM relationship), so the unit-of-work won't order
                # the entity insert ahead of its identifiers on its own.
                await session.flush()
                for (itype, val, uri, pri) in p.identifiers:
                    session.add(reg.TefcaEntityIdentifier(
                        id=uuid.uuid4(), entity_id=eid, identifier_type=itype,
                        identifier_value=val, system_uri=uri, is_primary=pri,
                        identifier_status="active"))
                for ep in p.endpoints:
                    session.add(reg.TefcaEntityEndpoint(id=uuid.uuid4(), entity_id=eid, **ep))

                # NPI check digit (CMS Luhn). FLAGS, never rejects: existing seed
                # and RCE data contains NPIs with bad check digits, and refusing
                # the import would break a working system to enforce a rule those
                # records predate. The entity lands, the problem is visible.
                for (itype, val, _u, _pri) in p.identifiers:
                    if itype != "npi":
                        continue
                    result = validate_for_import(val)
                    if result["npi_valid"]:
                        continue
                    warn = {"entity_name": p.name, "key": p.key,
                            "npi": result["npi"],
                            "error": result["npi_validation_error"]}
                    validation_warnings.append(warn)
                    session.add(reg.TefcaRegAuditLog(
                        id=uuid.uuid4(), entity_id=eid, action="npi_flagged",
                        actor_id=actor_id, actor_email=actor_email,
                        metadata_=warn, ip_address=ip_address))

                session.add(reg.TefcaRegAuditLog(
                    id=uuid.uuid4(), entity_id=eid, action="entity_created",
                    actor_id=actor_id, actor_email=actor_email,
                    metadata_={"source": source_type, "fhir_id": p.key}, ip_address=ip_address))
                await session.flush()
            for v in dup_vals:
                existing.add(v)     # catch in-file duplicates too
            key_to_uuid[p.key] = eid
            created.append((p, eid))
            imported += 1
        except Exception as ex:  # noqa: BLE001 — one bad entity must not fail the batch
            errors += 1
            err_list.append(safe_import_error(p.name, ex, "entity"))

    # ── PASS 2 — resolve partOf into relationships ──
    rels_by_child: dict[uuid.UUID, list] = {}
    for (p, eid) in created:
        if not p.parent_ref:
            continue
        try:
            parent = key_to_uuid.get(p.parent_ref) or await resolve_db_parent(session, p.parent_ref)
            if parent is None:
                err_list.append(f"{p.name}: parent '{p.parent_ref}' not found")
                errors += 1
                continue
            if parent == eid:
                continue
            rtype = REL_TYPE_BY_LEVEL.get(p.level, "belongs_to")
            async with session.begin_nested():
                session.add(reg.TefcaEntityRelationship(
                    id=uuid.uuid4(), parent_entity_id=parent, child_entity_id=eid,
                    relationship_type=rtype, effective_date=date.today(),
                    status="active", source="import"))
                await session.flush()
            rels_by_child.setdefault(eid, []).append(
                {"parent_id": str(parent), "relationship_type": rtype})
        except Exception as ex:  # noqa: BLE001
            errors += 1
            err_list.append(safe_import_error(p.name, ex, "relationship"))

    # ── initial version snapshot per created entity ──
    for (p, eid) in created:
        idents = [{"identifier_type": t, "identifier_value": v, "system_uri": u, "is_primary": pri}
                  for (t, v, u, pri) in p.identifiers]
        snapshot = {
            "name": p.name, "entity_level": p.level, "entity_type": p.entity_type,
            "operational_status": p.operational_status, "state": p.state,
            "city": p.city, "zip": p.zip, "address": p.address,
            "fhir_resource": p.fhir_resource,
            "identifiers": idents, "relationships": rels_by_child.get(eid, []),
        }
        session.add(reg.TefcaEntityVersion(
            id=uuid.uuid4(), entity_id=eid, version_number=1, snapshot_data=snapshot,
            change_reason="initial_import", changed_by=actor_id))

    status = "completed" if errors == 0 else ("partial" if imported > 0 else "failed")
    batch.status = status
    batch.imported_count = imported
    batch.skipped_count = skipped
    batch.error_count = errors
    # Copy again (see the constructor), then force the column dirty. flag_modified
    # is belt-and-braces for an audit-trail column: it guarantees the UPDATE is
    # emitted without depending on how history comparison treats a JSONB list.
    batch.errors = list(err_list)
    flag_modified(batch, "errors")
    batch.completed_at = datetime.utcnow()
    batch.duration_ms = int((time.monotonic() - t0) * 1000)
    session.add(reg.TefcaRegAuditLog(
        id=uuid.uuid4(), entity_id=None, action="import_completed",
        actor_id=actor_id, actor_email=actor_email,
        metadata_={"batch_id": str(batch.id), "imported": imported,
                   "skipped": skipped, "errors": errors,
                   "npi_flagged": len(validation_warnings)}, ip_address=ip_address))
    await session.commit()

    return {
        "batch_id": str(batch.id), "source_type": source_type, "status": status,
        "total": total, "imported": imported, "skipped": skipped,
        "error_count": errors, "errors": err_list, "duration_ms": batch.duration_ms,
        # Entities that imported successfully but carry an NPI failing the CMS
        # check digit. Separate from `errors`: nothing was rejected.
        "validation_warnings": validation_warnings,
        "npi_flagged_count": len(validation_warnings),
    }


async def _resolve_fhir_parent(session: AsyncSession, parent_fhir_id: str):
    """Resolve a parent by the FHIR id stored in fhir_resource->>'id'."""
    return (await session.execute(
        select(reg.TefcaRegEntity.id).where(
            reg.TefcaRegEntity.fhir_resource["id"].astext == parent_fhir_id))).scalar_one_or_none()


async def _resolve_tefcaid_parent(session: AsyncSession, parent_tefcaid: str):
    """Resolve a parent by its TEFCAID identifier value."""
    return (await session.execute(
        select(reg.TefcaEntityIdentifier.entity_id).where(
            reg.TefcaEntityIdentifier.identifier_type == "tefcaid",
            reg.TefcaEntityIdentifier.identifier_value == parent_tefcaid))).scalar_one_or_none()


# ── FHIR parsing ──────────────────────────────────────────────────────────────

def _detect_level(res: dict) -> str:
    profile = (res.get("meta") or {}).get("profile") or []
    p0 = (profile[0] if profile else "").lower()
    if p0.endswith("/qhin"):
        return "qhin"
    if "subparticipant" in p0:
        return "sub_participant"
    if p0.endswith("/participant"):
        return "participant"
    if p0.endswith("/child"):
        return "child"
    code = (((res.get("type") or [{}])[0].get("coding") or [{}])[0].get("code") or "").lower()
    return {"qhin": "qhin", "subparticipant": "sub_participant",
            "participant": "participant", "child": "child"}.get(code, "sub_participant")


def _detect_entity_type(res: dict, level: str) -> str:
    if level == "qhin":
        return "health_information_network"
    parts = [res.get("name") or ""]
    for t in (res.get("type") or []):
        for cod in (t.get("coding") or []):
            parts.append(cod.get("code") or "")
            parts.append(cod.get("display") or "")
    text = " ".join(parts).lower()
    if "hospital" in text:
        return "hospital_system"
    if "health plan" in text or "healthplan" in text or ("health" in text and "plan" in text):
        return "health_plan"
    if "exchange" in text or "hie" in text:
        return "health_information_exchange"
    if "clearinghouse" in text:
        return "clearinghouse"
    if "laborator" in text:
        return "laboratory"
    if "pharmac" in text:
        return "pharmacy"
    return "provider"


def _extract_identifiers(res: dict) -> list:
    out = []
    for idn in (res.get("identifier") or []):
        value = idn.get("value")
        if not value:
            continue
        system = idn.get("system") or ""
        code = ""
        coding = (idn.get("type") or {}).get("coding") or []
        if coding:
            code = (coding[0].get("code") or "").upper()
        if code == "HCID":
            itype = "hcid"
        elif code == "TEFCAID":
            itype = "tefcaid"
        else:
            itype = _SYSTEM_TO_TYPE.get(system)
        if not itype:
            continue  # unrecognized identifier — skip (never guess)
        out.append((itype, value, SYSTEM_URI.get(itype, system), itype == "tefcaid"))
    return out


def _extract_address(res: dict):
    addrs = res.get("address") or []
    a = addrs[0] if addrs else {}
    line = a.get("line") or []
    return a.get("state"), a.get("city"), a.get("postalCode"), (", ".join(line) if line else None)


def _extract_purposes(res: dict) -> dict:
    purposes = []
    for ext in (res.get("extension") or []):
        if "purpose" not in (ext.get("url") or "").lower():
            continue
        if ext.get("valueCode"):
            purposes.append(ext["valueCode"])
        vc = ext.get("valueCoding") or {}
        if vc.get("code"):
            purposes.append(vc["code"])
        for c in ((ext.get("valueCodeableConcept") or {}).get("coding") or []):
            if c.get("code"):
                purposes.append(c["code"])
    return {"purposes": purposes}


def _extract_endpoints(res: dict) -> list:
    out = []
    for c in (res.get("contained") or []):
        if not isinstance(c, dict) or c.get("resourceType") != "Endpoint":
            continue
        code = ((c.get("connectionType") or {}).get("code") or "").lower()
        etype = ("fhir_r4" if "fhir" in code else "direct_messaging" if "direct" in code
                 else "ihe_xcpd" if "xcpd" in code else "ihe_xca" if "xca" in code
                 else "ihe_xdr" if "xdr" in code else "soap" if "soap" in code else "rest")
        out.append({
            "endpoint_type": etype, "url": c.get("address"),
            "connection_type": (c.get("connectionType") or {}).get("code"),
            "name": c.get("name"), "status": "active", "environment": "production",
        })
    return out


def _parse_org(res: dict) -> ParsedEntity:
    name = res.get("name")
    if not name:
        raise ValueError("Organization has no name")
    level = _detect_level(res)
    state, city, zip_, address = _extract_address(res)
    partof = ((res.get("partOf") or {}).get("reference") or "")
    parent_ref = partof.split("/")[-1] if partof else None
    return ParsedEntity(
        key=res.get("id") or f"gen-{uuid.uuid4()}",
        name=name, level=level, entity_type=_detect_entity_type(res, level),
        operational_status="active" if res.get("active", True) else "inactive",
        state=state, city=city, zip=zip_, address=address,
        fhir_resource=res, exchange_purposes=_extract_purposes(res),
        identifiers=_extract_identifiers(res), endpoints=_extract_endpoints(res),
        parent_ref=parent_ref,
    )


async def import_fhir_bundle(
    session: AsyncSession, bundle: dict, *,
    filename: Optional[str] = None, file_checksum: Optional[str] = None,
    file_size: Optional[int] = None, actor_id=None, actor_email=None, ip_address=None,
) -> dict:
    """Import a FHIR R4 Bundle of Organization resources."""
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise ValueError("Payload is not a FHIR Bundle")
    entries = bundle.get("entry") or []
    parsed, pre_errors, total = [], [], 0
    for e in entries:
        res = (e or {}).get("resource") if isinstance(e, dict) else None
        if not isinstance(res, dict) or res.get("resourceType") != "Organization":
            continue
        total += 1
        try:
            parsed.append(_parse_org(res))
        except Exception as ex:  # noqa: BLE001
            pre_errors.append(
                safe_import_error(str(res.get("id") or "unknown"), ex, "parse"))
    return await persist_import(
        session, source_type="fhir_bundle", filename=filename, file_checksum=file_checksum,
        file_size=file_size, parsed=parsed, total=total, pre_errors=pre_errors,
        resolve_db_parent=_resolve_fhir_parent,
        actor_id=actor_id, actor_email=actor_email, ip_address=ip_address)
