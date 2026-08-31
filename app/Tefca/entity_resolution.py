"""
Entity resolution for the evidence layer — DB-backed, with mock as a fallback.

THE PROBLEM THIS SOLVES
───────────────────────
`routes._entity_by_reference()` scanned `ALL_MOCK_ENTITIES`. That made the whole
D1–D6 evidence pipeline structurally blind to the database: loading 2,300 RCE
records into `tefca_reg_entities` would have changed nothing about what the
evidence layer could see, because the evidence layer never looked there. Every
dimension would keep being assembled against 41 bundled fixtures.

This module is the seam. It resolves an entity reference against the canonical
registry (`tefca_reg_entities` + `tefca_entity_identifiers`) and returns it in
the FHIR-plus-`_rce` shape the dimension assemblers already consume, so nothing
downstream has to know where the entity came from.

RESOLUTION ORDER — MOST SPECIFIC IDENTIFIER FIRST
    1. TEFCAID   the RCE's own primary key; decisive
    2. HCID      TEFCA home community id
    3. NPI       present on many but not all TEFCA entities
    4. registry UUID / rce_organization_id
    5. exact name (case-insensitive)

Name is LAST and exact-only. Fuzzy name matching belongs in
`tefca_registry.entity_resolver`, which exists for adjudicating whether two
records describe one organisation and routes uncertain pairs to a human.
Guessing here would attach one organisation's federal evidence to another's
review, which is worse than resolving nothing.

THE FEATURE FLAG
────────────────
    ENTITY_RESOLVER_SOURCE = "mock" (default) | "db" | "db_then_mock"

Default "mock" so the existing suite keeps passing unchanged — the weekend's
job is to BUILD this path, not to switch production onto it. Monday, after
Area 1 → Area 2 → Registry is real and the RCE dataset is approved, the flag
flips to "db".

"db_then_mock" falls back to the fixtures when the database has no match, and
LOGS A WARNING every time it does. The warning is not decoration: a silent
fallback would let a demo look like it was reading real data while it was
reading fixtures, which is the exact failure mode the assessment flagged.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select

logger = logging.getLogger(__name__)

# ── Feature flag ─────────────────────────────────────────────────────────────

SOURCE_MOCK = "mock"
SOURCE_DB = "db"
SOURCE_DB_THEN_MOCK = "db_then_mock"

VALID_SOURCES = (SOURCE_MOCK, SOURCE_DB, SOURCE_DB_THEN_MOCK)

#: Default. Deliberately NOT "db" — see the module docstring.
DEFAULT_SOURCE = SOURCE_MOCK

ENV_VAR = "ENTITY_RESOLVER_SOURCE"


def resolver_source() -> str:
    """The configured resolution source, read fresh on every call.

    Read at call time rather than captured at import so a test can set the
    environment variable without reloading the module, and so an operator
    flipping the flag does not need a restart to take effect.

    An unrecognised value falls back to the default WITH a warning rather than
    raising. A typo in an environment variable must not take the review pipeline
    down, but it must also not pass unnoticed.
    """
    configured = (os.getenv(ENV_VAR) or DEFAULT_SOURCE).strip().lower()
    if configured not in VALID_SOURCES:
        logger.warning(
            "%s=%r is not one of %s — falling back to %r.",
            ENV_VAR, configured, list(VALID_SOURCES), DEFAULT_SOURCE,
        )
        return DEFAULT_SOURCE
    return configured


# ── Registry row -> evidence-layer entity shape ──────────────────────────────

_IDENTIFIER_SYSTEMS = {
    "npi": "http://hl7.org/fhir/sid/us-npi",
    "tefcaid": "urn:docuaction:tefca/identifier/tefcaid",
    "hcid": "urn:docuaction:tefca/identifier/hcid",
    "aaid": "urn:docuaction:tefca/identifier/aaid",
    "ccn": "urn:oid:2.16.840.1.113883.4.336",
    "naic": "urn:oid:2.16.840.1.113883.6.300",
}

_LEVEL_TO_TYPE_CODE = {
    "qhin": "QHIN",
    "participant": "PARTICIPANT",
    "sub_participant": "SUBPARTICIPANT",
    "child": "SUBPARTICIPANT",
}

_LEVEL_TO_SEQUOIA = {
    "participant": "Participant",
    "sub_participant": "Subparticipant",
    "child": "Subparticipant",
}


def registry_entity_to_evidence_shape(
    entity_row: Any,
    identifiers: List[Any],
    parent_tefcaid: Optional[str] = None,
) -> Dict[str, Any]:
    """One `tefca_reg_entities` row as the dict the dimension assemblers expect.

    The shape is FHIR-Organization-like with an `_rce` block, because that is
    what `evidence_assembly` and `applicability` already read. Building it here
    means the assemblers need no branch for "came from the database" — the
    alternative was a second code path through six dimensions, which is how two
    populations end up being judged by subtly different rules.

    Values are carried across unmodified. Nothing is normalised, corrected or
    defaulted on the way through: this is a projection, not a transformation,
    and a value that was wrong in the registry must still look wrong here.
    """
    by_type: Dict[str, str] = {}
    fhir_identifiers: List[Dict[str, str]] = []
    for ident in identifiers or []:
        itype = (getattr(ident, "identifier_type", None) or "").strip().lower()
        value = (getattr(ident, "identifier_value", None) or "").strip()
        if not itype or not value:
            continue
        by_type.setdefault(itype, value)
        system = getattr(ident, "system_uri", None) or _IDENTIFIER_SYSTEMS.get(itype)
        if system:
            fhir_identifiers.append({"system": system, "value": value})

    level = (getattr(entity_row, "entity_level", None) or "").strip().lower()
    exchange = getattr(entity_row, "exchange_purposes", None) or {}
    purposes = exchange.get("purposes") if isinstance(exchange, dict) else None
    purposes_text = ",".join(purposes) if isinstance(purposes, list) else ""

    zip_code = getattr(entity_row, "zip", None) or ""
    city = getattr(entity_row, "city", None) or ""
    state = getattr(entity_row, "state", None) or ""
    street = getattr(entity_row, "address", None) or ""
    name = getattr(entity_row, "name", None) or ""
    is_active = bool(getattr(entity_row, "is_active", True))

    rce_block: Dict[str, str] = {field: "" for field in _RCE_FIELDS}
    rce_block.update({
        # The delivery's own `id` (an OID) where we have it, else the registry
        # UUID. This is the value D5 cites as the source record identifier.
        "id": (getattr(entity_row, "rce_org_oid", None)
               or str(getattr(entity_row, "id", ""))),
        "domains": "RCE",
        "orgManagingOrg": getattr(entity_row, "org_managing_org", None) or "",
        "purposesofuse": purposes_text,
        "stateofoperation": state,
        "organizationNodeType": getattr(entity_row, "org_node_type", None) or "",
        # ENTITY COLUMNS FIRST, identifier rows as the fallback.
        #
        # An identifier row exists only where the value is unique across the
        # delivery — TEFCAID, HCID and AAID are shared by organisation families
        # and are skipped there to preserve the identifier table's uniqueness
        # guarantee. The entity columns are populated unconditionally, so
        # reading them first is what stops a family member's TEFCAID from
        # silently disappearing out of its own evidence.
        "NPI": getattr(entity_row, "npi", None) or by_type.get("npi", ""),
        "NAIC": by_type.get("naic", ""),
        "CCN": by_type.get("ccn", ""),
        "HCID": getattr(entity_row, "rce_hcid", None) or by_type.get("hcid", ""),
        "AAID": getattr(entity_row, "rce_aaid", None) or by_type.get("aaid", ""),
        "TEFCAID": (getattr(entity_row, "rce_tefcaid", None)
                    or by_type.get("tefcaid", "")),
        "active": "1" if is_active else "0",
        "sequoiaorgtype": (
            getattr(entity_row, "sequoia_org_type", None)
            or _LEVEL_TO_SEQUOIA.get(level, "")
        ),
        "hl7orgrole": getattr(entity_row, "hl7_org_role", None) or "",
        "name": name,
        "address_line": street,
        "address_text": " ".join(p for p in (street, city, state, zip_code) if p),
        "address_city": city,
        "address_state": state,
        "address_postalCode": zip_code,
        "address_country": "US",
        "partOf": parent_tefcaid or "",
    })
    rce_block.update({k: str(v) for k, v in
                      (getattr(entity_row, "rce_attributes", None) or {}).items()
                      if k in rce_block})

    entity: Dict[str, Any] = {
        "resourceType": "Organization",
        "id": str(getattr(entity_row, "id", "")),
        "identifier": fhir_identifiers,
        "active": is_active,
        "type": [{"coding": [{
            "system": "urn:docuaction:tefca/entity-type",
            "code": _LEVEL_TO_TYPE_CODE.get(level, "PARTICIPANT"),
        }]}],
        "name": name,
        "address": [{
            "use": "work",
            "line": [street] if street else [],
            "city": city,
            "state": state,
            "postalCode": zip_code,
            "country": "US",
        }],
        "_rce": rce_block,
        # Provenance. A caller reading evidence must be able to tell whether it
        # was assembled against the registry or against bundled fixtures — and
        # "MOCK" is what stops fixture-derived findings being reported as real.
        "_resolution_source": SOURCE_DB,
        "_registry_entity_id": str(getattr(entity_row, "id", "")),
        # A DocuAction ANNOTATION, deliberately outside `_rce`. The `_rce` block
        # holds exactly the 41 delivered fields and nothing else — mixing our
        # own conclusions into it would make the record indistinguishable from
        # what the RCE actually sent.
        "_rce_test_record": bool(getattr(entity_row, "is_test_record", False)),
    }
    return entity


def _rce_field_names() -> tuple:
    from app.Tefca.rce_fields import RCE_FIELDS
    return RCE_FIELDS


_RCE_FIELDS = _rce_field_names()


# ── Database resolution ──────────────────────────────────────────────────────

#: Identifier types tried, in order. TEFCAID first: it is the RCE's own primary
#: key and the only one guaranteed unique across the delivery.
_IDENTIFIER_PRIORITY = ("rce_org_oid", "tefcaid", "hcid", "npi", "aaid", "ccn")


#: Controlled outcomes of resolving ONE reference to ONE canonical entity.
#: These are states, not a boolean: "I found nothing" and "I found four" are
#: different answers and only one of them is a data-quality problem.
RESOLVED = "RESOLVED"
AMBIGUOUS = "AMBIGUOUS"
NOT_FOUND = "NOT_FOUND"
INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


def _outcome(state, entity_id=None, matched_on=None, candidates=None):
    return {"state": state, "entity_id": entity_id, "matched_on": matched_on,
            "candidates": list(candidates or [])}


async def resolve_reference_detail(db, reference: str) -> Dict[str, Any]:
    """The resolution ladder, reporting WHY it ended where it did.

    `resolve_from_db` answers "which entity is this" and returns None for every
    kind of failure. That is the right shape for the evidence pipeline, which
    can only proceed with an entity — but it collapses three different answers
    into one, and a caller that has to act on the difference cannot.

    A COR priority request is exactly such a caller. "No entity matches this
    reference" needs the COR asked for more information; "four entities match"
    needs a human to say which, and needs the candidates preserved so they can.
    Returning None for both would turn an ambiguity into a silent miss.

    Never raises: a database fault reports NOT_FOUND, the same honest answer as
    a genuine miss, and far better than a 500 on a read an analyst is waiting on.
    """
    ref = (reference or "").strip()
    if not ref:
        return _outcome(INSUFFICIENT_INFORMATION)

    from app.tefca_registry import models as reg

    try:
        # 1-3. By identifier, most specific first.
        rows = (await db.execute(
            select(reg.TefcaEntityIdentifier)
            .where(reg.TefcaEntityIdentifier.identifier_value == ref)
        )).scalars().all()
        if rows:
            ranked = sorted(
                rows,
                key=lambda r: _IDENTIFIER_PRIORITY.index(r.identifier_type)
                if (r.identifier_type or "") in _IDENTIFIER_PRIORITY
                else len(_IDENTIFIER_PRIORITY),
            )
            return _outcome(RESOLVED, ranked[0].entity_id,
                            matched_on=f"identifier:{ranked[0].identifier_type}")

        # 3b. By the RCE org OID / TEFCAID / HCID columns. These carry values
        #     that have no identifier row because they are shared across an
        #     organisation family, so a column lookup is the only way to find
        #     those entities at all.
        for column in ("rce_org_oid", "rce_tefcaid", "rce_hcid", "rce_aaid"):
            attr = getattr(reg.TefcaRegEntity, column, None)
            if attr is None:
                continue
            found = (await db.execute(
                select(reg.TefcaRegEntity.id).where(attr == ref).limit(5)
            )).scalars().all()
            if len(found) == 1:
                return _outcome(RESOLVED, found[0], matched_on=f"column:{column}")
            if len(found) > 1:
                # A family identifier matching several entities is not a
                # failure — but it does not identify ONE entity, so it
                # cannot resolve one.
                logger.info("reference %r matches %d entities on %s; "
                            "refusing to pick one.", ref, len(found), column)
                return _outcome(AMBIGUOUS, matched_on=f"column:{column}",
                                candidates=found)

        # 4. By the registry's own UUID.
        try:
            import uuid as _uuid
            candidate = _uuid.UUID(ref)
        except (ValueError, AttributeError, TypeError):
            candidate = None
        if candidate is not None:
            found = await db.get(reg.TefcaRegEntity, candidate)
            if found is not None and not getattr(found, "is_deleted", False):
                return _outcome(RESOLVED, found.id, matched_on="registry_uuid")

        # 5. Exact name, case-insensitive. Last, and only when it is
        #    UNAMBIGUOUS — two entities sharing a name resolve to neither.
        #    The registry already holds several rows called "Mayo Clinic";
        #    picking one would attach another organisation's evidence to this
        #    review.
        name_matches = (await db.execute(
            select(reg.TefcaRegEntity.id)
            .where(func.lower(reg.TefcaRegEntity.name) == ref.lower())
            .where(or_(reg.TefcaRegEntity.is_deleted.is_(False),
                       reg.TefcaRegEntity.is_deleted.is_(None)))
            .limit(5)
        )).scalars().all()
        if len(name_matches) == 1:
            return _outcome(RESOLVED, name_matches[0], matched_on="exact_name")
        if len(name_matches) > 1:
            logger.info(
                "entity reference %r matches %d registry entities by name; "
                "refusing to guess.", ref, len(name_matches))
            return _outcome(AMBIGUOUS, matched_on="exact_name",
                            candidates=name_matches)

        return _outcome(NOT_FOUND)

    except Exception as exc:  # noqa: BLE001 — resolution must never 500 a read
        logger.warning("registry entity resolution failed for %r: %s: %s",
                       ref, type(exc).__name__, exc, exc_info=True)
        return _outcome(NOT_FOUND)


async def resolve_from_db(db, reference: str) -> Optional[Dict[str, Any]]:
    """Resolve `reference` against the canonical registry, or return None.

    The evidence pipeline can only proceed with an entity, so every non-RESOLVED
    outcome is None here. The ladder itself lives in `resolve_reference_detail`
    so there is ONE implementation of what a reference means, not two that can
    drift apart.

    Never raises. A database fault during resolution returns None, which the
    caller reports as "not resolved" — the same honest answer as a genuine
    miss, and far better than a 500 on a read that an analyst is waiting on.
    """
    outcome = await resolve_reference_detail(db, reference)
    if outcome["state"] != RESOLVED:
        return None

    from app.tefca_registry import models as reg

    try:
        entity_id = outcome["entity_id"]
        entity_row = await db.get(reg.TefcaRegEntity, entity_id)
        if entity_row is None or getattr(entity_row, "is_deleted", False):
            return None

        identifiers = (await db.execute(
            select(reg.TefcaEntityIdentifier)
            .where(reg.TefcaEntityIdentifier.entity_id == entity_id)
        )).scalars().all()

        parent_tefcaid = await _parent_tefcaid(db, entity_id)
        return registry_entity_to_evidence_shape(entity_row, identifiers, parent_tefcaid)

    except Exception as exc:  # noqa: BLE001 — resolution must never 500 a read
        logger.warning("registry entity resolution failed for %r: %s: %s",
                       reference, type(exc).__name__, exc, exc_info=True)
        return None


async def _parent_tefcaid(db, entity_id) -> Optional[str]:
    """The TEFCAID of this entity's Participant parent, if one is recorded.

    Only `sub_participant_of` / `belongs_to` edges count. A `managed_by_qhin`
    edge names the QHIN, which is a different relationship and must not be
    returned as `partOf` — that conflation is what would put a QHIN where the
    hierarchy expects a Participant.
    """
    from app.tefca_registry import models as reg

    parent_id = (await db.execute(
        select(reg.TefcaEntityRelationship.parent_entity_id)
        .where(reg.TefcaEntityRelationship.child_entity_id == entity_id)
        .where(reg.TefcaEntityRelationship.relationship_type.in_(
            ("sub_participant_of", "belongs_to", "member_of")))
        .where(reg.TefcaEntityRelationship.status == "active")
        .limit(1)
    )).scalar_one_or_none()
    if parent_id is None:
        return None
    return (await db.execute(
        select(reg.TefcaEntityIdentifier.identifier_value)
        .where(reg.TefcaEntityIdentifier.entity_id == parent_id)
        .where(reg.TefcaEntityIdentifier.identifier_type == "tefcaid")
        .limit(1)
    )).scalar_one_or_none()


# ── Mock resolution ──────────────────────────────────────────────────────────

def resolve_from_mock(reference: str) -> Optional[Dict[str, Any]]:
    """Resolve against the bundled fixtures. Same order as the DB path."""
    from app.Tefca.mock_data import ALL_MOCK_ENTITIES
    from app.Tefca import rce_fields

    ref = (reference or "").strip()
    if not ref:
        return None

    for entity in ALL_MOCK_ENTITIES:
        if str(entity.get("id")) == ref:
            return entity
    for accessor in (rce_fields.tefca_id, rce_fields.hcid,
                     rce_fields.aaid, rce_fields.rce_npi):
        for entity in ALL_MOCK_ENTITIES:
            if accessor(entity) == ref:
                return entity
    for entity in ALL_MOCK_ENTITIES:
        for ident in entity.get("identifier") or []:
            if (ident.get("value") or "").strip() == ref:
                return entity
    for entity in ALL_MOCK_ENTITIES:
        if (entity.get("name") or "").strip().lower() == ref.lower():
            return entity
    return None


# ── The public entry point ───────────────────────────────────────────────────

async def resolve_entity(db, reference: str) -> Optional[Dict[str, Any]]:
    """Resolve one entity reference under the configured source.

    Returns the entity dict, or None. The returned dict carries
    `_resolution_source` so a caller — and the audit trail — can always tell
    whether the evidence that follows was assembled against the registry or
    against bundled fixtures.
    """
    source = resolver_source()

    if source == SOURCE_MOCK:
        found = resolve_from_mock(reference)
        return _tag_mock(found)

    resolved = await resolve_from_db(db, reference) if db is not None else None
    if resolved is not None:
        return resolved

    if source == SOURCE_DB:
        return None

    # db_then_mock. The warning is mandatory: a silent fallback lets a run look
    # like it read real data when it read fixtures.
    found = resolve_from_mock(reference)
    if found is not None:
        logger.warning(
            "ENTITY_RESOLVER_SOURCE=%s: %r did not resolve in the registry; "
            "FELL BACK TO BUNDLED FIXTURE %r. Evidence assembled from this "
            "entity is fixture-derived and must not be reported as a finding "
            "against a real organisation.",
            SOURCE_DB_THEN_MOCK, reference, found.get("id"),
        )
    return _tag_mock(found)


def _tag_mock(entity: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Mark a fixture-derived entity as such, without mutating the shared dict.

    ALL_MOCK_ENTITIES is a module-level constant shared across requests;
    stamping it in place would permanently alter it for every later caller.
    `test_eq003_masking_does_not_mutate_the_shared_dataset` pins exactly this
    hazard for the masking path, and it applies identically here.
    """
    if entity is None:
        return None
    tagged = dict(entity)
    tagged["_resolution_source"] = SOURCE_MOCK
    return tagged


def make_parent_resolver(db, population: Optional[List[Dict[str, Any]]] = None):
    """A synchronous parent resolver for D5/D6, over an in-memory population.

    D5 and D6 resolve a parent reference while assembling, which is synchronous.
    Rather than make the assemblers async — which would ripple through every
    dimension for one lookup — the caller passes an already-materialised
    population and this indexes it.

    With no population, returns None, and the dimensions report the parent
    reference as PRESENT-but-not-checked rather than unresolved. Claiming a
    broken hierarchy because nothing looked would be a false finding.
    """
    if not population:
        return None

    from app.Tefca import rce_fields

    index: Dict[str, Dict[str, Any]] = {}
    for entity in population:
        for key in (rce_fields.tefca_id(entity), rce_fields.hcid(entity),
                    str(entity.get("id") or "")):
            if key:
                index.setdefault(key, entity)
        reference = f"Organization/{entity.get('id')}"
        index.setdefault(reference, entity)

    def resolve(reference: str) -> Optional[Dict[str, Any]]:
        return index.get((reference or "").strip())

    return resolve
