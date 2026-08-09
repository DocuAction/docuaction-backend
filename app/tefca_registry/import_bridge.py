"""Bridge entity imports into the registry so verification can see them.

THE PROBLEM THIS SOLVES

The Entity Import page posts to POST /api/tefca/entities/upload, which writes
`tefca_entities`. Registry verification reads `tefca_reg_entities`. There was no
link between them, so importing five hospitals and then verifying them touched
two disjoint sets of rows. The end-to-end demo made this visible: step 3 matched
registry records by NAME, address comparison reported `not_compared` because
those records held no address, and the import→verification path was broken while
every individual step reported success.

THE APPROACH — BRIDGE, NOT MERGE

Both tables stay. On import, each accepted row is also upserted into the
registry, so one action populates both stores and verification has a name and an
address to compare. Merging the tables outright would touch every endpoint on
two routers at once; this is the same outcome reachable one table at a time.

MATCHING IS BY NPI

The registry keeps identifiers in `tefca_entity_identifiers`, not on the entity
row, so an existing registry entity is found through its NPI identifier. NPI is
the right key here: it is the identifier a provider is actually known by, it is
what the import validates, and it is what NPPES and PECOS are queried with. Name
matching — which is what the demo fell back on — collides constantly, since the
registry already holds several rows called "Mayo Clinic".

NEVER FAILS THE IMPORT

Every entry point returns a result rather than raising. A registry write failing
must not lose an import that already succeeded: the entity is in
`tefca_entities`, the caller is told the bridge did not complete, and the row can
be re-bridged later. The reverse — failing the whole upload because a secondary
write failed — would discard work the operator already did.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select

from app.tefca_registry import models as reg

logger = logging.getLogger(__name__)

# Legacy entity_type -> registry entity_level. The legacy table stores a coarse
# type; the registry stores a hierarchy level and a separate free-text type.
LEVEL_BY_LEGACY_TYPE = {
    "QHIN": "qhin",
    "PARTICIPANT": "participant",
    "SUBPARTICIPANT": "sub_participant",
}
DEFAULT_LEVEL = "participant"

CREATED, UPDATED, FAILED = "created", "updated", "failed"


def one_line_address(address: Optional[str], city: Optional[str],
                     state: Optional[str], zip_code: Optional[str]) -> str:
    """Street, city, state and ZIP as one line.

    The registry's `address` column is what review_service compares against the
    authoritative practice address, and NPPES returns that as a single line
    ("1800 ORLEANS ST BALTIMORE MD 21287"). Storing only the street here would
    compare a street against a full address and report a mismatch on entities
    that agree — so the stored value carries the same components.

    ZIP is truncated to five digits: NPPES pads to nine without a hyphen, and
    212870010 matches nothing.
    """
    parts = [address, city, state, (zip_code or "")[:5]]
    return " ".join(str(p).strip() for p in parts if p and str(p).strip())


async def find_registry_entity_by_npi(session, npi: str):
    """The registry entity carrying this NPI, or None."""
    if not (npi or "").strip():
        return None
    entity_id = (await session.execute(
        select(reg.TefcaEntityIdentifier.entity_id).where(
            reg.TefcaEntityIdentifier.identifier_type == "npi",
            reg.TefcaEntityIdentifier.identifier_value == npi.strip(),
        ).limit(1))).scalar_one_or_none()
    if entity_id is None:
        return None
    return await session.get(reg.TefcaRegEntity, entity_id)


async def bridge_entity(
    session,
    *,
    npi: str,
    name: str,
    address: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    zip_code: Optional[str] = None,
    entity_type: Optional[str] = None,
    source: str = "csv_import",
    import_entity_id: Optional[Any] = None,
) -> Dict[str, Any]:
    """Create or update the registry entity for one imported row.

    Returns {"status": created|updated|failed, "entity_id": str|None, ...}.
    Never raises — see the module docstring.
    """
    result: Dict[str, Any] = {"status": FAILED, "entity_id": None,
                              "npi": npi, "name": name}
    if not (npi or "").strip() or not (name or "").strip():
        result["reason"] = "npi and name are both required to bridge a row"
        return result

    legacy_type = str(entity_type or "").strip().upper()
    level = LEVEL_BY_LEGACY_TYPE.get(legacy_type, DEFAULT_LEVEL)
    full_address = one_line_address(address, city, state, zip_code)

    try:
        existing = await find_registry_entity_by_npi(session, npi)
        if existing is not None:
            # Update in place. Fields are only overwritten when the import
            # actually carries a value: a CSV without an address column must not
            # blank an address the registry already had from another source.
            existing.name = name or existing.name
            existing.display_name = name or existing.display_name
            if full_address:
                existing.address = full_address
            if city:
                existing.city = city
            if state:
                existing.state = state[:2].upper()
            if zip_code:
                existing.zip = str(zip_code)[:10]
            existing.updated_at = datetime.utcnow()
            await session.flush()
            result.update(status=UPDATED, entity_id=str(existing.id))
            return result

        entity_id = uuid.uuid4()
        session.add(reg.TefcaRegEntity(
            id=entity_id,
            name=name,
            display_name=name,
            entity_level=level,
            entity_type=(legacy_type.lower() or "provider"),
            operational_status="active",
            # NOT verified. The row was imported, which is a statement about
            # where it came from, not about whether anything confirmed it.
            verification_status="not_verified",
            address=full_address or None,
            city=city or None,
            state=(state[:2].upper() if state else None),
            zip=(str(zip_code)[:10] if zip_code else None),
            current_version=1,
            is_active=True,
        ))
        # Flush the parent before the identifier: these child tables carry plain
        # FK columns with no ORM relationship, so the unit of work will not order
        # the inserts on its own.
        await session.flush()
        session.add(reg.TefcaEntityIdentifier(
            id=uuid.uuid4(), entity_id=entity_id,
            identifier_type="npi", identifier_value=npi.strip(),
            system_uri="http://hl7.org/fhir/sid/us-npi",
            is_primary=True, identifier_status="active"))
        session.add(reg.TefcaRegAuditLog(
            id=uuid.uuid4(), entity_id=entity_id, action="entity_created",
            metadata_={"source": source, "npi": npi,
                       "import_entity_id": str(import_entity_id)
                       if import_entity_id else None}))
        await session.flush()
        result.update(status=CREATED, entity_id=str(entity_id))
        return result
    except Exception as exc:  # noqa: BLE001 — must never lose a successful import
        logger.warning("registry bridge failed for NPI %s: %s: %s",
                       npi, type(exc).__name__, exc, exc_info=True)
        result["reason"] = f"{type(exc).__name__}"
        return result


async def bridge_many(session, rows, *, source: str = "csv_import") -> Dict[str, Any]:
    """Bridge a batch. Returns counts plus per-row detail.

    Each row is bridged inside its own savepoint so one bad row cannot roll back
    the ones already written — the same fault-tolerance the importer itself uses.
    """
    created = updated = failed = 0
    details = []
    for row in rows:
        try:
            async with session.begin_nested():
                outcome = await bridge_entity(session, source=source, **row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("registry bridge savepoint failed: %s", exc)
            outcome = {"status": FAILED, "entity_id": None,
                       "npi": row.get("npi"), "name": row.get("name"),
                       "reason": type(exc).__name__}
        details.append(outcome)
        if outcome["status"] == CREATED:
            created += 1
        elif outcome["status"] == UPDATED:
            updated += 1
        else:
            failed += 1
    return {"registry_created": created, "registry_updated": updated,
            "registry_failed": failed, "registry_details": details}
