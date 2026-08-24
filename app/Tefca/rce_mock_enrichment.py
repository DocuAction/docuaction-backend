"""
RCE-shaped enrichment for the bundled development fixtures.

WHAT THIS DOES
──────────────
Attaches an `_rce` block carrying all 41 delivered RCE fields to each of the 30
bundled entities, and defines an additional cohort of 11 entities covering the
record shapes the existing 30 cannot express — no NPI, inactive, encoding
corruption, test artefact. Together they let D5, D6 and the applicability engine
be exercised against RCE-shaped data before the real delivery is imported.

EVERY IDENTIFIER AND CONTACT VALUE HERE IS SYNTHETIC
────────────────────────────────────────────────────
The field STRUCTURE, formats, enumerations and hierarchy patterns follow the RCE
delivery. The VALUES do not come from it. No production organisation's TEFCAID,
HCID, AAID, phone or email appears in this file, and the synthetic values are
constructed so they cannot collide with a real one even by accident:

  TEFCAID   urn:uuid:00000000-test-NNNN-mock-0000000000NN
            Deliberately NOT valid RFC-4122 hex — "test" and "mock" are not
            hex digits, so no real TEFCAID can ever equal one of these. The
            impossibility is the point, not a formatting slip.
  HCID      urn:oid:2.16.840.1.113883.3.9999.1.N
  AAID      urn:oid:2.16.840.1.113883.3.9999.2.N
            The .9999 arc is reserved here as the synthetic marker; real HL7
            OID assignments do not use it.
  email     ...@example.com   (RFC 2606 reserved — can never route)
  phone     555-0NN-NNNN      (reserved fictional exchange)

WHY ENRICHMENT RATHER THAN REWRITING THE FIXTURES
─────────────────────────────────────────────────
The 30 entities are load-bearing for ~77 evidence tests plus the validation and
QA suites, which pin their NPIs, buckets and identifier arrays. Rewriting the
literals by hand would risk all of that for no gain. Enrichment adds `_rce`
without touching `identifier[]`, `name`, `active` or `_expected_bucket` on any
existing entity — `test_eq003` asserts that entity 0's non-NPI identifier list
is exactly ["PART-001"], and it still is.

The 11 new entities are APPENDED. Nothing is removed or renumbered.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from app.Tefca.rce_fields import (
    CONTACT_PURPOSE_ADMIN,
    DOMAIN_RCE,
    NPI_SYSTEM,
    PURPOSE_TREATMENT,
    RCE_FIELDS,
)

# ── Synthetic QHIN OIDs ──────────────────────────────────────────────────────
#
# orgManagingOrg in the delivery is a bare OID naming the managing QHIN. These
# are synthetic stand-ins on the reserved .9999 arc, one per QHIN the fixture
# already names.

_QHIN_BASE = "2.16.840.1.113883.3.9999"

QHIN_OIDS: Dict[str, str] = {
    "eHealth Exchange":            f"{_QHIN_BASE}.10",
    "CommonWell Health Alliance":  f"{_QHIN_BASE}.11",
    "MedAllies":                   f"{_QHIN_BASE}.12",
    "Health Gorilla":              f"{_QHIN_BASE}.13",
    "Surescripts":                 f"{_QHIN_BASE}.14",
    "KONZA National Network":      f"{_QHIN_BASE}.15",
    "Kno2":                        f"{_QHIN_BASE}.16",
}

#: QHIN for the appended RCE-profile cohort.
RCE_PROFILE_QHIN = "eHealth Exchange"


def _synthetic_tefcaid(index: int) -> str:
    """urn:uuid:00000000-test-NNNN-mock-0000000000NN — see module docstring."""
    return f"urn:uuid:00000000-test-{index:04d}-mock-{index:012d}"


def _synthetic_hcid(index: int) -> str:
    return f"urn:oid:{_QHIN_BASE}.1.{index}"


def _synthetic_aaid(index: int) -> str:
    return f"urn:oid:{_QHIN_BASE}.2.{index}"


def _synthetic_contact(index: int, org_name: str) -> Dict[str, str]:
    """Contact block. Reserved domain and exchange — cannot reach anyone."""
    return {
        "contact_company": org_name,
        "contact_purpose": CONTACT_PURPOSE_ADMIN,
        "contact_name": f"Test Administrator {index:03d}",
        "contact_phone": f"555-0{index // 100:02d}-{index % 100:04d}",
        "contact_email": f"test-admin-{index:03d}@example.com",
        "contact_address_text": "1 Test Plaza, Suite 100",
        "contact_address_line": "1 Test Plaza",
        "contact_address_city": "Testville",
        "contact_address_state": "MD",
        "contact_address_postalCode": "20850",
        "contact_address_country": "US",
    }


# ── Deriving an RCE block from an existing FHIR fixture ──────────────────────

_TYPE_TO_SEQUOIA = {
    "PARTICIPANT": "Participant",
    "SUBPARTICIPANT": "Subparticipant",
    "QHIN": None,  # a QHIN is not itself a sequoiaorgtype value
}

#: Cycled across the fixtures so every node type is represented. Technical
#: exchange behaviour only — deliberately uncorrelated with sequoiaorgtype, so a
#: test can prove the two are read independently.
_NODE_TYPE_CYCLE = ("initiator", "passthrough", "no node")


def _fhir_type_code(entity: Dict[str, Any]) -> Optional[str]:
    for t in entity.get("type") or []:
        for c in t.get("coding") or []:
            code = (c.get("code") or "").strip().upper()
            if code:
                return code
    return None


def _fhir_npi(entity: Dict[str, Any]) -> str:
    for ident in entity.get("identifier") or []:
        if (ident.get("system") or "") == NPI_SYSTEM:
            return (ident.get("value") or "").strip()
    return ""


def _fhir_telecom(entity: Dict[str, Any], system: str) -> str:
    for t in entity.get("telecom") or []:
        if (t.get("system") or "").lower() == system:
            return (t.get("value") or "").strip()
    return ""


def _fhir_address(entity: Dict[str, Any]) -> Dict[str, str]:
    addrs = entity.get("address") or []
    a = addrs[0] if addrs else {}
    line = [x for x in (a.get("line") or []) if x]
    return {
        "address_line": line[0] if line else "",
        "address_text": ", ".join(line + [
            x for x in (a.get("city"), a.get("state"), a.get("postalCode")) if x
        ]),
        "address_city": a.get("city") or "",
        "address_state": a.get("state") or "",
        "address_postalCode": a.get("postalCode") or "",
        "address_country": a.get("country") or "US",
    }


def _blank_rce_record() -> Dict[str, str]:
    """All 41 fields present and empty. A delivered record always has 41
    columns; a missing VALUE and a missing COLUMN are different facts and the
    parser must be able to tell them apart."""
    return {field: "" for field in RCE_FIELDS}


def build_rce_block(
    entity: Dict[str, Any],
    index: int,
    *,
    parent_tefcaid: Optional[str] = None,
    include_hcid: bool = True,
    include_purposes: bool = True,
) -> Dict[str, str]:
    """The 41-field RCE record for one existing FHIR fixture."""
    type_code = _fhir_type_code(entity)
    sequoia = _TYPE_TO_SEQUOIA.get(type_code or "", None)
    qhin = entity.get("_qhin") or ""
    address = _fhir_address(entity)
    name = entity.get("name") or ""

    record = _blank_rce_record()
    record.update({
        "id": entity.get("id") or "",
        "domains": DOMAIN_RCE,
        "initiatoronly": "0",
        "orgManagingOrg": QHIN_OIDS.get(qhin, ""),
        "purposesofuse": PURPOSE_TREATMENT if include_purposes else "",
        "stateofoperation": address["address_state"],
        "doa": "executed",
        "transaction": "both",
        "delegationRole": "none",
        "organizationNodeType": _NODE_TYPE_CYCLE[index % len(_NODE_TYPE_CYCLE)],
        "NPI": _fhir_npi(entity),
        "NAIC": "",
        "CCN": "",
        "HCID": _synthetic_hcid(index) if include_hcid else "",
        "AAID": _synthetic_aaid(index),
        "TEFCAID": _synthetic_tefcaid(index),
        "active": "1",
        "sequoiaorgtype": sequoia or "",
        "hl7orgrole": "prov",
        "name": name,
        "alias": (entity.get("alias") or [""])[0] if entity.get("alias") else "",
        "phone": _fhir_telecom(entity, "phone"),
        "email": _fhir_telecom(entity, "email"),
        # A Subparticipant's partOf names its Participant. A Participant's is
        # empty — its parent is the QHIN, and that edge is orgManagingOrg.
        "partOf": parent_tefcaid or "",
        **_synthetic_contact(index, name),
    })
    return record


# ── Applying enrichment to the bundled 30 ────────────────────────────────────

#: Fixtures deliberately left without an HCID, so D5 can be tested against the
#: delivery's documented "missing HCID on some records" condition.
_NO_HCID_IDS = {"rce-org-b2-006", "rce-org-b3-004"}

#: Fixtures deliberately left with no exchange purpose, so D5's
#: PASS / NOT_APPLICABLE split for purposesofuse is exercised.
_NO_PURPOSES_IDS = {"rce-org-b2-003", "rce-org-b3-006", "rce-org-b4-003"}


def enrich_entities(entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach an `_rce` block to each entity, IN PLACE, and return the list.

    Two passes, because a Subparticipant's `partOf` must carry its Participant's
    TEFCAID and the parent may appear later in the list. Same reason
    `fhir_import.persist_import` resolves parents in a second pass: import order
    is not a hierarchy.

    Nothing except `_rce` is added. `identifier[]`, `name`, `active` and
    `_expected_bucket` are untouched on every existing fixture.
    """
    # Pass 1 — assign each entity its own TEFCAID.
    tefcaid_by_fhir_id: Dict[str, str] = {}
    for index, entity in enumerate(entities, start=1):
        tefcaid_by_fhir_id[entity.get("id") or ""] = _synthetic_tefcaid(index)

    # Pass 2 — build the block, resolving partOf through the pass-1 map.
    for index, entity in enumerate(entities, start=1):
        entity_id = entity.get("id") or ""
        parent_ref = (entity.get("partOf") or {}).get("reference") or ""
        parent_fhir_id = parent_ref.split("/")[-1] if parent_ref else ""
        # Only a Participant parent becomes partOf. A QHIN parent is expressed
        # through orgManagingOrg — conflating the two would put a QHIN where the
        # hierarchy expects a Participant.
        parent_tefcaid = (
            tefcaid_by_fhir_id.get(parent_fhir_id)
            if parent_fhir_id and not parent_fhir_id.startswith("rce-qhin-")
            else None
        )
        entity["_rce"] = build_rce_block(
            entity, index,
            parent_tefcaid=parent_tefcaid,
            include_hcid=entity_id not in _NO_HCID_IDS,
            include_purposes=entity_id not in _NO_PURPOSES_IDS,
        )
    return entities


# ── The appended RCE-profile cohort ──────────────────────────────────────────
#
# Eleven entities covering the record shapes the bundled 30 cannot express. The
# indices continue from 30 so no TEFCAID collides with an enriched fixture.

_COHORT_BASE = 100  # TEFCAID index base — visibly separate from the first 30


def _cohort_entity(
    seq: int,
    *,
    fhir_id: str,
    name: str,
    sequoia: str,
    state: str,
    city: str,
    postal: str,
    npi: str = "",
    active: bool = True,
    parent_fhir_id: Optional[str] = None,
    node_type: str = "initiator",
    include_hcid: bool = True,
    include_purposes: bool = True,
    test_record: bool = False,
    note: str = "",
) -> Dict[str, Any]:
    """One cohort entity as a FHIR Organization carrying an `_rce` block."""
    index = _COHORT_BASE + seq
    identifiers = [{"system": "urn:docuaction:tefca/identifier",
                    "value": f"RCEP-{seq:03d}"}]
    if npi:
        identifiers.insert(0, {"system": NPI_SYSTEM, "value": npi})

    address = {
        "use": "work",
        "line": [f"{100 + seq} Exchange Way"],
        "city": city,
        "state": state,
        "postalCode": postal,
        "country": "US",
    }

    entity: Dict[str, Any] = {
        "resourceType": "Organization",
        "id": fhir_id,
        "identifier": identifiers,
        "active": active,
        "type": [{"coding": [{
            "system": "urn:docuaction:tefca/entity-type",
            "code": "PARTICIPANT" if sequoia == "Participant" else "SUBPARTICIPANT",
        }]}],
        "name": name,
        "telecom": [{"system": "phone", "value": f"555-0{seq:02d}-0100"}],
        "address": [address],
        "_qhin": RCE_PROFILE_QHIN,
        "_expected_bucket": None,
        "_rce_profile": True,
        "_test_note": note,
    }
    if parent_fhir_id:
        entity["partOf"] = {"reference": f"Organization/{parent_fhir_id}"}
    if test_record:
        entity["_rce_test_record"] = True

    record = _blank_rce_record()
    record.update({
        "id": fhir_id,
        "domains": DOMAIN_RCE,
        "initiatoronly": "1" if node_type == "initiator" else "0",
        "orgManagingOrg": QHIN_OIDS[RCE_PROFILE_QHIN],
        "purposesofuse": PURPOSE_TREATMENT if include_purposes else "",
        "stateofoperation": state,
        "doa": "executed",
        "transaction": "both",
        "delegationRole": "none",
        "organizationNodeType": node_type,
        "NPI": npi,
        "NAIC": "",
        "CCN": "",
        "HCID": _synthetic_hcid(index) if include_hcid else "",
        "AAID": _synthetic_aaid(index),
        "TEFCAID": _synthetic_tefcaid(index),
        "active": "1" if active else "0",
        "sequoiaorgtype": sequoia,
        "hl7orgrole": "prov",
        "name": name,
        "alias": "",
        "phone": f"555-0{seq:02d}-0100",
        "email": f"tefca-{seq:03d}@example.com",
        "address_line": address["line"][0],
        "address_text": f"{address['line'][0]}, {city}, {state} {postal}",
        "address_city": city,
        "address_state": state,
        "address_postalCode": postal,
        "address_country": "US",
        "partOf": "",  # resolved below, once every cohort TEFCAID exists
        **_synthetic_contact(index, name),
    })
    entity["_rce"] = record
    entity["_rce_parent_fhir_id"] = parent_fhir_id or ""
    return entity


def build_rce_profile_cohort() -> List[Dict[str, Any]]:
    """The 11 appended entities.

    Composition, and what each shape is for:

      3  Participants with NPI            HI / TX / NY  — hierarchy roots
      2  Subparticipants with NPI         encoding corruption (Kapiʻolani okina)
      5  Subparticipants WITHOUT NPI      NPI is legitimately absent on many
                                          TEFCA entities; two of these are also
                                          inactive
      1  test artefact                    ELLKAY-DOA-TEST pattern, inactive

    Which yields 5 no-NPI Subparticipants, 3 inactive records, 2 encoding
    defects and 1 test artefact — the requested mix, with the overlaps stated
    rather than hidden.
    """
    cohort: List[Dict[str, Any]] = []

    # ── Participants with NPI — the roots of the cohort hierarchy ──
    cohort.append(_cohort_entity(
        1, fhir_id="rce-org-rp-001", name="Pacific Islands Health Partners",
        sequoia="Participant", state="HI", city="Honolulu", postal="96813",
        npi="1306849449", node_type="initiator",
        note="Hawaii Participant — hierarchy root for the HI sub-tree."))
    cohort.append(_cohort_entity(
        2, fhir_id="rce-org-rp-002", name="Lone Star Regional Health Exchange",
        sequoia="Participant", state="TX", city="Austin", postal="78701",
        npi="1417950156", node_type="passthrough",
        note="Texas Participant — passthrough node."))
    cohort.append(_cohort_entity(
        3, fhir_id="rce-org-rp-003", name="Empire State Care Collaborative",
        sequoia="Participant", state="NY", city="Albany", postal="12207",
        npi="1528061863", node_type="no node",
        note="New York Participant operating NO exchange node — proves "
             "organizationNodeType is independent of TEFCA class."))

    # ── Subparticipants with NPI, carrying encoding corruption ──
    #
    # "Kapiʻolani" written with the okina (U+02BB) and then round-tripped
    # through CP-1252 becomes "Kapiâ€˜olani". The file still decodes cleanly as
    # UTF-8, which is exactly why this has to be detected as a pattern rather
    # than caught as a decode error.
    cohort.append(_cohort_entity(
        4, fhir_id="rce-org-rp-004", name="Kapiâ€˜olani Community Health Center",
        sequoia="Subparticipant", state="HI", city="Honolulu", postal="96826",
        npi="1639172570", parent_fhir_id="rce-org-rp-001", node_type="initiator",
        note="ENCODING DEFECT — okina corrupted to mojibake. Detected, never "
             "auto-corrected."))
    cohort.append(_cohort_entity(
        5, fhir_id="rce-org-rp-005", name="Cliniâ€™ca de Salud del Valle",
        sequoia="Subparticipant", state="TX", city="El Paso", postal="79901",
        npi="1740283287", parent_fhir_id="rce-org-rp-002", node_type="passthrough",
        note="ENCODING DEFECT — apostrophe corrupted to mojibake."))

    # ── Subparticipants WITHOUT NPI ──
    #
    # Not a defect. A health information network, clearinghouse or public health
    # agency has no reason to hold an NPI, and demanding one would reject
    # legitimate TEFCA entities.
    cohort.append(_cohort_entity(
        6, fhir_id="rce-org-rp-006", name="Hawaii Statewide Health Information Network",
        sequoia="Subparticipant", state="HI", city="Hilo", postal="96720",
        parent_fhir_id="rce-org-rp-001", node_type="passthrough",
        note="NO NPI — health information network. Legitimate absence."))
    cohort.append(_cohort_entity(
        7, fhir_id="rce-org-rp-007", name="Texas Public Health Data Consortium",
        sequoia="Subparticipant", state="TX", city="Houston", postal="77002",
        parent_fhir_id="rce-org-rp-002", node_type="initiator",
        include_hcid=False,
        note="NO NPI and NO HCID — public health agency; exercises two "
             "independent 'legitimately absent' conditions at once."))
    cohort.append(_cohort_entity(
        8, fhir_id="rce-org-rp-008", name="Hudson Valley Claims Clearinghouse",
        sequoia="Subparticipant", state="NY", city="Poughkeepsie", postal="12601",
        parent_fhir_id="rce-org-rp-003", node_type="passthrough",
        include_purposes=False,
        note="NO NPI, no exchange purpose supplied — clearinghouse."))
    cohort.append(_cohort_entity(
        9, fhir_id="rce-org-rp-009", name="Adirondack Rural Care Alliance",
        sequoia="Subparticipant", state="NY", city="Plattsburgh", postal="12901",
        parent_fhir_id="rce-org-rp-003", node_type="no node", active=False,
        note="NO NPI and INACTIVE (active=0). Reported as inactive, never "
             "dropped."))
    cohort.append(_cohort_entity(
        10, fhir_id="rce-org-rp-010", name="Rio Grande Health Cooperative",
        sequoia="Subparticipant", state="TX", city="Laredo", postal="78040",
        parent_fhir_id="rce-org-rp-002", node_type="no node", active=False,
        note="NO NPI and INACTIVE (active=0)."))

    # ── Test artefact ──
    cohort.append(_cohort_entity(
        11, fhir_id="rce-org-rp-011", name="ELLKAY-DOA-TEST",
        sequoia="Subparticipant", state="NY", city="New York", postal="10001",
        parent_fhir_id="rce-org-rp-003", node_type="initiator", active=False,
        include_purposes=False, test_record=True,
        note="TEST ARTEFACT in the production feed (ELLKAY-DOA-TEST pattern). "
             "Flagged for an analyst; the record is preserved, not deleted."))

    # Resolve cohort partOf now that every TEFCAID exists — same two-pass reason
    # as enrich_entities().
    tefcaid_by_fhir_id = {e["id"]: e["_rce"]["TEFCAID"] for e in cohort}
    for entity in cohort:
        parent_fhir_id = entity.pop("_rce_parent_fhir_id", "")
        if parent_fhir_id:
            entity["_rce"]["partOf"] = tefcaid_by_fhir_id.get(parent_fhir_id, "")
    return cohort


def cohort_copy() -> List[Dict[str, Any]]:
    """A deep copy of the cohort — for tests that want to mutate freely."""
    return copy.deepcopy(build_rce_profile_cohort())
