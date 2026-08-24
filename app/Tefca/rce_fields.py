"""
The RCE delivery's 41 fields — canonical names, vocabularies, and accessors.

WHY THIS MODULE EXISTS
──────────────────────
Until now every statement about "what ONC supplies" was made against the
bundled 30-entity FHIR fixture, and `evidence_assembly.ONC_FIELDS_NOT_SUPPLIED`
asserted that HCID, Exchange Purpose and entity type were simply absent. The RCE
delivery supplies all three. Rather than scatter `entity["_rce"].get("HCID")`
across four modules, every read of an RCE field goes through an accessor here,
so when the real file lands on Monday there is ONE place where a field name or a
vocabulary changes.

PROVENANCE OF THE VOCABULARIES BELOW — READ THIS BEFORE TRUSTING THEM
─────────────────────────────────────────────────────────────────────
The 41 field NAMES and their order are as supplied by the program office. The
value vocabularies split into two groups, and the split is recorded here rather
than smoothed over:

  STATED — given explicitly by the program office. Safe to rely on.
      sequoiaorgtype        Participant | Subparticipant
      organizationNodeType  initiator | passthrough | no node
      purposesofuse         T-TRTMNT, or empty
      domains               RCE
      contact_purpose       ADMIN

  PROVISIONAL — NOT yet observed in the delivered file. The field exists in the
  supplied schema; its value vocabulary is a placeholder standing in until the
  file is profiled (Monday P0/P1). Anything reading these must tolerate an
  unknown value rather than assume membership.
      hl7orgrole, delegationRole, doa, transaction, stateofoperation, NAIC

`PROVISIONAL_VOCABULARIES` names them so a test can assert that no rule treats a
provisional value as authoritative, and so the Monday profiling pass has an
explicit checklist rather than a memory.

sequoiaorgtype vs organizationNodeType
──────────────────────────────────────
These are NOT the same axis and conflating them would corrupt the hierarchy:

    sequoiaorgtype        WHAT THE ORGANISATION IS in TEFCA — its place in the
                          QHIN → Participant → Subparticipant structure.
    organizationNodeType  HOW IT BEHAVES TECHNICALLY on the exchange — whether
                          it initiates, passes through, or operates no node.

A Subparticipant may be an initiator; a Participant may operate no node. Neither
fact tells you anything about the other. `tefca_class_of()` reads
sequoiaorgtype and NEVER organizationNodeType — see
`app/Tefca/applicability.py`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ── The 41 fields, in delivered file order ───────────────────────────────────

RCE_FIELDS: tuple = (
    "id", "domains", "initiatoronly", "orgManagingOrg", "purposesofuse",
    "stateofoperation", "doa", "transaction", "delegationRole",
    "organizationNodeType", "NPI", "NAIC", "CCN", "HCID", "AAID", "TEFCAID",
    "active", "sequoiaorgtype", "hl7orgrole", "name", "alias", "phone", "email",
    "address_text", "address_line", "address_city", "address_state",
    "address_postalCode", "address_country", "partOf",
    "contact_company", "contact_purpose", "contact_name", "contact_phone",
    "contact_email", "contact_address_text", "contact_address_line",
    "contact_address_city", "contact_address_state", "contact_address_postalCode",
    "contact_address_country",
)

RCE_FIELD_COUNT = len(RCE_FIELDS)  # 41

#: The pipe character the delivery uses. Named rather than inlined so the
#: delimiter is one edit when a future delivery changes it.
RCE_DELIMITER = "|"


# ── Vocabularies ─────────────────────────────────────────────────────────────

# STATED — supplied by the program office.
SEQUOIA_ORG_TYPES = ("Participant", "Subparticipant")

#: TECHNICAL EXCHANGE BEHAVIOUR. Not the TEFCA hierarchy. See module docstring.
ORGANIZATION_NODE_TYPES = ("initiator", "passthrough", "no node")

PURPOSE_TREATMENT = "T-TRTMNT"
PURPOSES_OF_USE = (PURPOSE_TREATMENT,)

DOMAIN_RCE = "RCE"
CONTACT_PURPOSE_ADMIN = "ADMIN"

# PROVISIONAL — placeholder vocabularies pending the Monday file profile.
PROVISIONAL_VOCABULARIES: Dict[str, tuple] = {
    "hl7orgrole": ("prov", "pay", "gov", "lab", "phar", "other"),
    "delegationRole": ("delegating", "delegated", "none"),
    "doa": ("executed", "pending", "none"),
    "transaction": ("query", "response", "both", "none"),
    "stateofoperation": (),   # free-text / multi-value; no closed set assumed
    "NAIC": (),               # numeric payer code; no closed set
}

#: Fields whose vocabulary has NOT been confirmed against the delivered file.
#: No rule may treat membership in these as authoritative.
PROVISIONAL_FIELDS = frozenset(PROVISIONAL_VOCABULARIES)


# ── Identifier systems for the RCE-supplied identifiers ──────────────────────
#
# Deliberately distinct from the legacy `urn:docuaction:tefca/identifier`, which
# carries the fixture's own PART-001 / SUBPART-001 keys. Overloading that system
# would make two different identifier schemes indistinguishable in one audit
# trail — the same mistake `source_registry.py` exists to prevent for the word
# "pecos".

RCE_TEFCAID_SYSTEM = "urn:docuaction:tefca/identifier/tefcaid"
RCE_HCID_SYSTEM = "urn:docuaction:tefca/identifier/hcid"
RCE_AAID_SYSTEM = "urn:docuaction:tefca/identifier/aaid"
NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"

RCE_IDENTIFIER_SYSTEMS = {
    "TEFCAID": RCE_TEFCAID_SYSTEM,
    "HCID": RCE_HCID_SYSTEM,
    "AAID": RCE_AAID_SYSTEM,
    "NPI": NPI_SYSTEM,
}


# ── Accessors ────────────────────────────────────────────────────────────────
#
# Every one tolerates an entity with no RCE block at all, because the 30 bundled
# fixtures predate the delivery and a caller must not have to ask first.


def rce_block(entity: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The entity's RCE record, or an empty dict."""
    if not isinstance(entity, dict):
        return {}
    block = entity.get("_rce")
    return block if isinstance(block, dict) else {}


def _clean(value: Any) -> Optional[str]:
    """Trimmed string, or None for blank. Blank and absent are the same fact."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def rce_value(entity: Dict[str, Any], field: str) -> Optional[str]:
    """One RCE field by its exact delivered name."""
    return _clean(rce_block(entity).get(field))


def has_rce_data(entity: Dict[str, Any]) -> bool:
    return bool(rce_block(entity))


def tefca_id(entity: Dict[str, Any]) -> Optional[str]:
    return rce_value(entity, "TEFCAID")


def hcid(entity: Dict[str, Any]) -> Optional[str]:
    return rce_value(entity, "HCID")


def aaid(entity: Dict[str, Any]) -> Optional[str]:
    return rce_value(entity, "AAID")


def rce_npi(entity: Dict[str, Any]) -> Optional[str]:
    """The RCE-supplied NPI. Legitimately empty on many TEFCA entities —
    absence is a fact about the entity, never a defect in the record."""
    return rce_value(entity, "NPI")


def sequoia_org_type(entity: Dict[str, Any]) -> Optional[str]:
    """Participant | Subparticipant, exactly as delivered (case preserved)."""
    return rce_value(entity, "sequoiaorgtype")


def organization_node_type(entity: Dict[str, Any]) -> Optional[str]:
    """Technical exchange behaviour. NEVER the TEFCA hierarchy."""
    return rce_value(entity, "organizationNodeType")


def org_managing_org(entity: Dict[str, Any]) -> Optional[str]:
    """The QHIN OID that manages this organisation."""
    return rce_value(entity, "orgManagingOrg")


def part_of(entity: Dict[str, Any]) -> Optional[str]:
    """The RCE parent reference — a Subparticipant's Participant.

    Distinct from `orgManagingOrg`, which points at the QHIN. An entity may
    legitimately carry both, and they produce two different relationship edges.
    """
    return rce_value(entity, "partOf")


def parent_reference(entity: Dict[str, Any]) -> Optional[str]:
    """The entity's Participant-parent reference, or None.

    PRECEDENCE IS NOT A FALLBACK CHAIN. Where an RCE record exists it is the
    authority, and its EMPTY `partOf` is a positive statement — "this entity has
    no Participant parent" — which is exactly the case for every Participant,
    whose parent is a QHIN and is expressed through `orgManagingOrg`.

    Falling through to the FHIR `partOf` when the RCE field is empty would read
    the fixture's QHIN reference as a Participant parent, then fail to resolve
    it against a Participant population and report a broken hierarchy for
    entities whose hierarchy is perfectly correct. The FHIR reference is
    consulted ONLY for entities that carry no RCE record at all.
    """
    if has_rce_data(entity):
        return part_of(entity)
    reference = (entity.get("partOf") or {}).get("reference")
    return _clean(reference)


def qhin_reference(entity: Dict[str, Any]) -> Optional[str]:
    """The managing-QHIN pointer: RCE `orgManagingOrg`, else the FHIR partOf
    when it names a QHIN. Distinct from `parent_reference` — different edge."""
    managing = org_managing_org(entity)
    if managing:
        return managing
    reference = (entity.get("partOf") or {}).get("reference") or ""
    return reference if "qhin" in reference.lower() else None


def purposes_of_use(entity: Dict[str, Any]) -> List[str]:
    """Exchange purposes as a list. Empty list means none supplied.

    Never inferred, and never derived from Medicare data: PECOS has nothing to
    say about why two organisations exchange information under TEFCA.
    """
    raw = rce_value(entity, "purposesofuse")
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[,;]", raw) if p.strip()]


def domains(entity: Dict[str, Any]) -> Optional[str]:
    return rce_value(entity, "domains")


def hl7_org_role(entity: Dict[str, Any]) -> Optional[str]:
    """PROVISIONAL vocabulary — see module docstring."""
    return rce_value(entity, "hl7orgrole")


def delegation_role(entity: Dict[str, Any]) -> Optional[str]:
    """PROVISIONAL vocabulary — see module docstring."""
    return rce_value(entity, "delegationRole")


def is_initiator_only(entity: Dict[str, Any]) -> Optional[bool]:
    raw = rce_value(entity, "initiatoronly")
    if raw is None:
        return None
    return raw.strip() in ("1", "true", "True", "TRUE", "Y", "yes")


def is_active(entity: Dict[str, Any]) -> bool:
    """Active per the RCE `active` column, falling back to FHIR `active`.

    RCE delivers "0"/"1". An inactive entity is a legitimate, reportable state —
    it is not an error and is never silently dropped.
    """
    raw = rce_value(entity, "active")
    if raw is not None:
        return raw.strip() not in ("0", "false", "False", "FALSE", "N", "no")
    return bool(entity.get("active", True)) if isinstance(entity, dict) else True


def contact(entity: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """The 11 contact_* fields as one block. PII — gate before exposing."""
    block = rce_block(entity)
    return {
        field: _clean(block.get(field))
        for field in RCE_FIELDS
        if field.startswith("contact_")
    }


def rce_address(entity: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """The RCE-supplied address components, unmodified."""
    block = rce_block(entity)
    return {
        "address_text": _clean(block.get("address_text")),
        "address_line": _clean(block.get("address_line")),
        "address_city": _clean(block.get("address_city")),
        "address_state": _clean(block.get("address_state")),
        "address_postalCode": _clean(block.get("address_postalCode")),
        "address_country": _clean(block.get("address_country")),
    }


# ── Data-quality detectors ───────────────────────────────────────────────────
#
# These DETECT and REPORT. Nothing here corrects a value: the corrupted okina in
# "Kapiʻolani" stays corrupted in the record, an issue names it, and a human
# decides. Silent correction would put a value in the audit trail that the RCE
# never sent.

#: Byte sequences that appear when UTF-8 has been decoded as CP-1252/Latin-1 and
#: re-encoded. `â€˜` is UTF-8 E2 80 98 (the okina, U+02BB / left single quote)
#: read one byte at a time. The file decodes cleanly as UTF-8, so nothing raises
#: — the corruption is only visible as a pattern.
_MOJIBAKE_MARKERS = (
    "â€™", "â€˜", "â€œ", "â€\x9d", "â€“", "â€”", "â€¦", "â€",
    "Ã¡", "Ã©", "Ã­", "Ã³", "Ãº", "Ã±", "Ã¼", "Ã–", "Ã„",
    "Ê»", "Â ", "Â·", "Â»", "Â«", "ï»¿",
)


def detect_mojibake(text: Optional[str]) -> List[str]:
    """Mojibake markers present in `text`. Empty list means none found.

    Returns the markers rather than a bool so the issue record can quote what
    was actually seen — "MOJIBAKE_DETECTED" with no evidence is not reviewable.
    """
    if not text:
        return []
    return sorted({m for m in _MOJIBAKE_MARKERS if m in text})


def has_mojibake(entity: Dict[str, Any]) -> bool:
    """True when any RCE text field on this entity carries a mojibake marker."""
    return bool(mojibake_fields(entity))


def mojibake_fields(entity: Dict[str, Any]) -> Dict[str, List[str]]:
    """{field: [markers]} for every RCE field showing encoding corruption."""
    out: Dict[str, List[str]] = {}
    for field, value in rce_block(entity).items():
        markers = detect_mojibake(value if isinstance(value, str) else None)
        if markers:
            out[field] = markers
    return out


def embedded_tab_fields(entity: Dict[str, Any]) -> List[str]:
    """RCE fields containing a literal TAB.

    A TAB inside `address_text` is the documented defect in the delivery. It
    matters because a reader that ever switches to tab-delimited would split the
    record on it, and because the value round-trips into a report as whitespace
    nobody can see.
    """
    return sorted(
        field for field, value in rce_block(entity).items()
        if isinstance(value, str) and "\t" in value
    )


#: Substrings marking a record as a vendor/integration test artefact rather than
#: a real TEFCA entity. "-DOA-TEST" is the pattern observed in the delivery
#: ("ELLKAY-DOA-TEST").
#:
#: Matched against the NAME fields ONLY — never against TEFCAID/HCID/AAID. An
#: identifier is an opaque token: a hex UUID can contain "test" by coincidence,
#: and a synthetic fixture identifier contains it by construction. Screening
#: identifiers here flagged all 41 fixtures as test artefacts on the first run,
#: which is exactly the kind of over-broad heuristic that would quarantine live
#: entities from a real delivery.
_TEST_RECORD_MARKERS = ("doa-test", "doa_test", "testonly", "test-only",
                        "donotuse", "do-not-use", "dummy-org")

#: A name that IS the word "test", or ends in a "test" token — "ELLKAY-DOA-TEST",
#: "ACME TEST". Bounded so "Testa Medical Group" and "Protest Health" do not
#: match on a bare substring.
_TEST_NAME_PATTERN = re.compile(r"(^|[\s\-_])test([\s\-_]|$)", re.IGNORECASE)


def is_test_record(entity: Dict[str, Any]) -> bool:
    """True when the record looks like a test artefact.

    A heuristic, and treated as one: it raises an issue for an analyst, it never
    drops the record. A real organisation with "Test" in its legal name is a
    thing that can happen, and deleting it because of a substring would be the
    silent data loss this whole architecture exists to prevent.
    """
    if not isinstance(entity, dict):
        return False
    if entity.get("_rce_test_record") is True:
        return True
    names = [
        str(v) for v in (
            entity.get("name"),
            rce_value(entity, "name"),
            rce_value(entity, "alias"),
        ) if v
    ]
    haystack = " ".join(names).lower()
    if any(marker in haystack for marker in _TEST_RECORD_MARKERS):
        return True
    return any(_TEST_NAME_PATTERN.search(name) for name in names)


def quality_flags(entity: Dict[str, Any]) -> List[str]:
    """Every data-quality signal on this entity, as stable flag codes.

    Ordered and deduplicated so two runs over the same record produce the same
    list — a flag set that reorders is a flag set that cannot be diffed.
    """
    flags: List[str] = []
    if has_mojibake(entity):
        flags.append("MOJIBAKE_DETECTED")
    if embedded_tab_fields(entity):
        flags.append("EMBEDDED_TAB")
    if is_test_record(entity):
        flags.append("TEST_RECORD_SUSPECTED")
    if not is_active(entity):
        flags.append("INACTIVE_RECORD")
    if has_rce_data(entity) and not hcid(entity):
        flags.append("MISSING_HCID")
    if has_rce_data(entity) and not purposes_of_use(entity):
        flags.append("MISSING_PURPOSES_OF_USE")
    return sorted(set(flags))
