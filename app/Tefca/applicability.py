"""
Applicability engine — which evidence dimensions apply to THIS entity.

WHAT THE RCE DATA ACTUALLY CONTAINS
───────────────────────────────────
SUPERSEDED NOTE, KEPT DELIBERATELY. An earlier version of this docstring
recorded — as inspected fact — that ONC supplies no HCID, no Exchange Purpose
and no entity type. That was true of the 30-entity FHIR fixture it was written
against. It is NOT true of the RCE delivery, which supplies all three. The claim
is corrected here rather than quietly deleted, because a rule written on the old
premise ("degrade to CORROBORATIVE, the data cannot tell us") is only safe to
revisit if the premise it rested on is visible.

The RCE record carries 41 fields (see `app/Tefca/rce_fields.py`). The ones that
drive applicability:

  sequoiaorgtype        Participant | Subparticipant   -> TEFCA class
  organizationNodeType  initiator | passthrough | no node
                        TECHNICAL EXCHANGE BEHAVIOUR — never the hierarchy
  NPI                   frequently and legitimately EMPTY
  HCID / AAID / TEFCAID TEFCA identifiers
  purposesofuse         Exchange Purpose, e.g. T-TRTMNT
  partOf                Subparticipant -> its Participant
  orgManagingOrg        the managing QHIN
  active                0 | 1

STILL NOT SUPPLIED: an NPI Type 1/Type 2 marker, and any NUCC-grade
provider/organisation taxonomy. Those still come from NPPES, which remains the
primary identity authority. Where a rule would need one and NPPES is silent, the
rule degrades to CORROBORATIVE or NOT_APPLICABLE rather than guessing.

THE CONSEQUENCE THAT DRIVES THIS MODULE
───────────────────────────────────────
The RCE gives the TEFCA class directly, so that no longer has to be inferred.
It still does NOT give a provider/organisation taxonomy, so Medicare relevance
is established from NPPES (enumeration type + taxonomy) wherever an NPI exists.

Where no NPI exists at all, NPPES has nothing to say — and PECOS, being
NPI-keyed, has nothing to be asked. That case resolves to NOT_APPLICABLE for D2,
which is a statement about the available identifiers and not a finding against
the entity.

That produces three honest states, not two:

  REQUIRED       the methodology requires this dimension for this entity
  CORROBORATIVE  evidence is useful if present and CANNOT fail the entity
  NOT_APPLICABLE the dimension does not apply

"We could not establish Medicare relevance" resolves to CORROBORATIVE, never to
REQUIRED. A dimension that is only corroborative can produce CORROBORATED,
REVIEW, NOT_FOUND or NOT_APPLICABLE — it can never produce a determination
against the entity. That is the whole reason the third state exists: the
alternative is either inventing a provider type or holding entities to a
Medicare standard nobody established applied to them.

There is deliberately no rule of the form "Hospital => all sources mandatory".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.Tefca import rce_fields
from app.Tefca.evidence_dimensions import Applicability, Dimension

# ── NPPES taxonomy → coarse organisational category ──────────────────────────
#
# Only mappings that are unambiguous in the NUCC taxonomy are listed. Anything
# not matched here stays UNKNOWN, which is a legitimate outcome and is handled
# as such downstream — an unrecognised taxonomy is not evidence of anything.

_TAXONOMY_PREFIX_CATEGORIES = [
    # Managed care / payer organisations (NUCC section 3 "Managed Care").
    (re.compile(r"^30[125][A-Z0-9]"), "PAYER"),
    # Hospitals (28x) and hospital units.
    (re.compile(r"^28[123]"), "PROVIDER_ORGANIZATION"),
    # Nursing & custodial care, residential treatment, ambulatory (31x/32x/33x/26x).
    (re.compile(r"^(31|32|33|26)[0-9A-Z]"), "PROVIDER_ORGANIZATION"),
    # Laboratories, suppliers, agencies (29x/33x/35x).
    (re.compile(r"^(29|35)[0-9A-Z]"), "PROVIDER_ORGANIZATION"),
]

#: Public Health or Welfare Agency and Community/Behavioural health agencies.
_PUBLIC_HEALTH_TAXONOMIES = {"251K00000X", "251B00000X", "261QP0905X", "261QC1500X"}

_HIE_TAXONOMIES: set = set()  # NUCC publishes no HIE/HIN taxonomy. Left empty on purpose.


class EntityCategory:
    INDIVIDUAL_PROVIDER = "INDIVIDUAL_PROVIDER"
    PROVIDER_ORGANIZATION = "PROVIDER_ORGANIZATION"
    PUBLIC_HEALTH_AGENCY = "PUBLIC_HEALTH_AGENCY"
    PAYER = "PAYER"
    HIE_HIN_QHIN = "HIE_HIN_QHIN"
    UNKNOWN = "UNKNOWN"


@dataclass
class ApplicabilityProfile:
    """The applicability decision, plus every input that produced it."""

    entity_category: str
    tefca_class: Optional[str]
    nppes_enumeration_type: Optional[str]
    nppes_taxonomy_code: Optional[str]
    nppes_taxonomy_desc: Optional[str]
    medicare_relevance: str            # LIKELY | UNLIKELY | UNDETERMINED
    dimensions: Dict[str, str]         # Dimension.value -> Applicability.value
    rationale: Dict[str, str]          # Dimension.value -> why
    inputs_used: List[str]
    #: RCE `sequoiaorgtype` as delivered. The authority for `tefca_class`.
    sequoia_org_type: Optional[str] = None
    #: RCE `organizationNodeType` — technical exchange behaviour. Carried for
    #: display and audit ONLY. No applicability rule branches on it, and
    #: `tefca_class` is never derived from it.
    organization_node_type: Optional[str] = None
    #: Any NPI available for this entity, or None. Drives D2 applicability.
    npi_available: Optional[str] = None

    def applicability_of(self, dimension: Dimension) -> str:
        return self.dimensions.get(dimension.value, Applicability.CORROBORATIVE.value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_category": self.entity_category,
            "tefca_class": self.tefca_class,
            "sequoia_org_type": self.sequoia_org_type,
            "organization_node_type": self.organization_node_type,
            "organization_node_type_note": (
                "Technical exchange behaviour. NOT the TEFCA hierarchy and never "
                "used to derive tefca_class."
            ),
            "npi_available": bool(self.npi_available),
            "nppes_enumeration_type": self.nppes_enumeration_type,
            "nppes_taxonomy_code": self.nppes_taxonomy_code,
            "nppes_taxonomy_desc": self.nppes_taxonomy_desc,
            "medicare_relevance": self.medicare_relevance,
            "dimensions": dict(self.dimensions),
            "rationale": dict(self.rationale),
            "inputs_used": list(self.inputs_used),
        }


def tefca_class_of(entity: Dict[str, Any]) -> Optional[str]:
    """QHIN | PARTICIPANT | SUBPARTICIPANT — the entity's place in TEFCA.

    `sequoiaorgtype` is the RCE-supplied authority and is consulted FIRST; the
    FHIR type coding is the fallback for fixtures that predate the delivery.

    `organizationNodeType` is NEVER read here. It describes technical exchange
    behaviour — whether the organisation initiates, passes through, or operates
    no node — and carries no information about the TEFCA hierarchy. A
    Subparticipant may be an initiator and a Participant may operate no node.
    Reading it as a class would silently reorganise the hierarchy.
    """
    sequoia = (rce_fields.sequoia_org_type(entity) or "").strip().upper()
    if sequoia in {"PARTICIPANT", "SUBPARTICIPANT"}:
        return sequoia
    for t in entity.get("type") or []:
        for c in t.get("coding") or []:
            code = (c.get("code") or "").strip().upper()
            if code in {"QHIN", "PARTICIPANT", "SUBPARTICIPANT"}:
                return code
    return None


def node_type_of(entity: Dict[str, Any]) -> Optional[str]:
    """The RCE `organizationNodeType` — technical exchange behaviour ONLY.

    Provided as a named accessor so that anything wanting this value has an
    obvious, documented way to get it and no reason to reach for
    `tefca_class_of()` instead. Nothing in the applicability rules branches on
    it: how an organisation exchanges data does not change which verification
    dimensions apply to it.
    """
    return rce_fields.organization_node_type(entity)


def _taxonomy_category(code: Optional[str], desc: Optional[str]) -> str:
    if not code:
        return EntityCategory.UNKNOWN
    code = code.strip().upper()
    if code in _PUBLIC_HEALTH_TAXONOMIES:
        return EntityCategory.PUBLIC_HEALTH_AGENCY
    if code in _HIE_TAXONOMIES:
        return EntityCategory.HIE_HIN_QHIN
    for pattern, category in _TAXONOMY_PREFIX_CATEGORIES:
        if pattern.match(code):
            return category
    # A taxonomy we do not recognise tells us nothing. Say so rather than
    # defaulting into a category that carries obligations.
    return EntityCategory.UNKNOWN


# ── RCE organisational signal ────────────────────────────────────────────────
#
# Used ONLY when NPPES is silent — which is the normal state for a TEFCA entity
# that legitimately holds no NPI (a health information network, a clearinghouse,
# a public health agency). Without this the category stays UNKNOWN, Medicare
# relevance stays UNDETERMINED, and D2 sits at CORROBORATIVE forever for
# entities that plainly have no Medicare dimension at all.
#
# DIRECTIONALLY SAFE BY CONSTRUCTION. Every pattern here resolves to a category
# whose Medicare relevance is UNLIKELY. Nothing in this table can produce
# PROVIDER_ORGANIZATION or INDIVIDUAL_PROVIDER, so it can only ever RELAX a
# Medicare obligation — never impose one on an entity nothing established is
# Medicare-relevant. A name heuristic must not be able to create a requirement.

_RCE_ORG_SIGNALS = [
    (re.compile(r"\b(health information (network|exchange)|hie|hin|interoperability)\b", re.I),
     "HIE_HIN_QHIN"),
    (re.compile(r"\b(clearinghouse|clearing house)\b", re.I), "HIE_HIN_QHIN"),
    (re.compile(r"\b(public health|department of health|health department|"
                r"state registry|immunization registry)\b", re.I), "PUBLIC_HEALTH_AGENCY"),
    (re.compile(r"\b(health plan|payer|payor|insurance|assurance|managed care)\b", re.I),
     "PAYER"),
]


def _rce_org_signal(entity: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """(category, matched_text) from the RCE-supplied organisation name, or
    (None, None). See the table above for why this can only relax."""
    name = (rce_fields.rce_value(entity, "name") or entity.get("name") or "").strip()
    if not name:
        return None, None
    for pattern, category in _RCE_ORG_SIGNALS:
        match = pattern.search(name)
        if match:
            return category, match.group(0)
    return None, None


def available_npi(entity: Dict[str, Any],
                  nppes_data: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Any NPI available for this entity — RCE-supplied, FHIR, or NPPES-returned.

    An empty result is a fact about the record, not a defect: many TEFCA
    entities legitimately hold no NPI. It matters for applicability because
    PECOS enrolment is keyed on NPI — with no NPI there is no identifier with
    which an enrolment could be either established or refuted.
    """
    supplied = rce_fields.rce_npi(entity)
    if supplied:
        return supplied
    for ident in entity.get("identifier") or []:
        if (ident.get("system") or "") == rce_fields.NPI_SYSTEM:
            value = (ident.get("value") or "").strip()
            if value:
                return value
    returned = (nppes_data or {}).get("npi")
    return str(returned).strip() or None if returned else None


def _fhir_part_of(entity: Dict[str, Any]) -> Optional[str]:
    """The parent pointer carried OUTSIDE the `_rce` block, whatever its shape.

    Two real shapes reach this module and they are not the same type:

        FHIR fixture   partOf = {"reference": "Organization/123"}
        RCE delivery   partOf = "2.16.840.1.113883.4.391.1000"   (a bare OID)

    The original code did `(entity.get("partOf") or {}).get("reference")`, which
    raises AttributeError on the string. It never fired in production because
    every caller goes through `entity_resolution.registry_entity_to_evidence_shape`,
    which nests the RCE fields under `_rce` and leaves the top-level `partOf`
    absent — but a caller handing over a raw parsed record crashed the
    applicability engine, which is how this was found.

    Returns the reference either way, and None for anything else. A blank string
    stays falsy, so nothing here invents a relationship that was not delivered.
    """
    part_of = entity.get("partOf")
    if isinstance(part_of, dict):
        reference = part_of.get("reference")
        return reference.strip() or None if isinstance(reference, str) else None
    if isinstance(part_of, str):
        return part_of.strip() or None
    return None


def classify_entity(
    entity: Dict[str, Any],
    nppes_data: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    """Best-supported category for the entity, with the evidence that supports it."""
    tefca_class = tefca_class_of(entity)
    nppes = nppes_data or {}
    enumeration_type = (nppes.get("enumeration_type") or "").strip().upper() or None
    taxonomy_code = (nppes.get("taxonomy_code") or "").strip().upper() or None
    taxonomy_desc = nppes.get("taxonomy") or None
    inputs: List[str] = []

    if tefca_class:
        inputs.append("onc_tefca_class")
    if enumeration_type:
        inputs.append("nppes_enumeration_type")
    if taxonomy_code:
        inputs.append("nppes_taxonomy")

    # A QHIN is a network operator by definition of its TEFCA role. That is the
    # one category the ONC data alone can establish.
    if tefca_class == "QHIN":
        return EntityCategory.HIE_HIN_QHIN, {
            "tefca_class": tefca_class, "enumeration_type": enumeration_type,
            "taxonomy_code": taxonomy_code, "taxonomy_desc": taxonomy_desc,
            "inputs_used": inputs,
        }

    # NPPES enumeration type is the authority on individual vs organisation.
    # NPI-1 = individual practitioner (Type 1), NPI-2 = organisation (Type 2).
    if enumeration_type == "NPI-1":
        category = EntityCategory.INDIVIDUAL_PROVIDER
    else:
        category = _taxonomy_category(taxonomy_code, taxonomy_desc)
        if category == EntityCategory.UNKNOWN and enumeration_type == "NPI-2":
            # An organisational NPI with an unmapped taxonomy is an organisation,
            # but WHICH kind is unestablished — which is exactly why the Medicare
            # rules below stay corroborative for it.
            category = EntityCategory.UNKNOWN

    # RCE organisational signal — consulted ONLY where NPPES established nothing.
    # NPPES stays the authority whenever it spoke; this fills the gap it leaves
    # for entities that hold no NPI for it to speak about.
    rce_signal_text = None
    if category == EntityCategory.UNKNOWN:
        signal_category, rce_signal_text = _rce_org_signal(entity)
        if signal_category:
            category = getattr(EntityCategory, signal_category)
            inputs.append("rce_organization_name_signal")

    return category, {
        "tefca_class": tefca_class, "enumeration_type": enumeration_type,
        "taxonomy_code": taxonomy_code, "taxonomy_desc": taxonomy_desc,
        "rce_signal_text": rce_signal_text,
        "sequoia_org_type": rce_fields.sequoia_org_type(entity),
        "organization_node_type": rce_fields.organization_node_type(entity),
        "inputs_used": inputs,
    }


def _medicare_relevance(category: str, enumeration_type: Optional[str],
                        pecos_found: Optional[bool]) -> str:
    """LIKELY / UNLIKELY / UNDETERMINED — never a yes/no we cannot support.

    `pecos_found` is evidence, not an assumption: an entity actually present in
    the PPEF is Medicare-relevant whatever we guessed from its taxonomy. That
    ordering matters — evidence available for the review is the last and
    strongest input the spec lists.
    """
    if pecos_found:
        return "LIKELY"
    if category in (EntityCategory.INDIVIDUAL_PROVIDER, EntityCategory.PROVIDER_ORGANIZATION):
        return "LIKELY"
    if category in (EntityCategory.PAYER, EntityCategory.HIE_HIN_QHIN,
                    EntityCategory.PUBLIC_HEALTH_AGENCY):
        return "UNLIKELY"
    return "UNDETERMINED"


def build_profile(
    entity: Dict[str, Any],
    nppes_data: Optional[Dict[str, Any]] = None,
    pecos_found: Optional[bool] = None,
    methodology_requires: Optional[Dict[str, str]] = None,
) -> ApplicabilityProfile:
    """Decide applicability per dimension for one entity.

    `methodology_requires` lets the approved ARC methodology override any
    dimension explicitly; nothing here modifies B1–B4 or invents a requirement
    the methodology does not state.
    """
    category, ev = classify_entity(entity, nppes_data)
    relevance = _medicare_relevance(category, ev["enumeration_type"], pecos_found)

    REQ = Applicability.REQUIRED.value
    COR = Applicability.CORROBORATIVE.value
    NA = Applicability.NOT_APPLICABLE.value

    dims: Dict[str, str] = {}
    why: Dict[str, str] = {}

    # ── D1 Identity — always required. Every TEFCA entity has an identity to
    # establish, and NPPES/RCE are always the authorities for it.
    dims[Dimension.D1_IDENTITY.value] = REQ
    why[Dimension.D1_IDENTITY.value] = (
        "Identity is required for every TEFCA entity. NPPES is the primary NPI "
        "identity authority; PECOS may corroborate but never replaces it."
    )

    # ── D2 Medicare enrolment — driven by Medicare relevance, not by entity size.
    npi = available_npi(entity, nppes_data)
    if relevance == "LIKELY":
        dims[Dimension.D2_MEDICARE_ENROLLMENT.value] = REQ
        why[Dimension.D2_MEDICARE_ENROLLMENT.value] = (
            f"Medicare relevance LIKELY for category {category}. A PECOS non-match "
            "is still not a TEFCA failure — it routes to analyst review."
        )
    elif relevance == "UNLIKELY":
        dims[Dimension.D2_MEDICARE_ENROLLMENT.value] = NA
        why[Dimension.D2_MEDICARE_ENROLLMENT.value] = (
            f"{category} is not normally a Medicare-enrolled provider. NOT_APPLICABLE "
            "unless specific evidence establishes Medicare relevance."
        )
    elif not npi:
        # No NPI anywhere — RCE supplied none and NPPES returned none.
        #
        # PECOS enrolment records are keyed on NPI. With no NPI there is no
        # identifier with which an enrolment could be established OR refuted, so
        # leaving D2 corroborative would keep an open question that no available
        # data can ever close. NOT_APPLICABLE states the actual position: this
        # dimension has nothing to operate on.
        #
        # This is a statement about the identifiers on the record, NOT a finding
        # against the entity, and NOT an assertion that the entity has no
        # Medicare relationship. Many TEFCA entities legitimately hold no NPI.
        dims[Dimension.D2_MEDICARE_ENROLLMENT.value] = NA
        why[Dimension.D2_MEDICARE_ENROLLMENT.value] = (
            "No NPI was supplied by the RCE and none was established through NPPES. "
            "PECOS enrolment is keyed on NPI, so no enrolment can be established or "
            "refuted for this entity. NOT_APPLICABLE — a statement about the "
            "available identifiers, never a finding against the entity."
        )
    else:
        dims[Dimension.D2_MEDICARE_ENROLLMENT.value] = COR
        why[Dimension.D2_MEDICARE_ENROLLMENT.value] = (
            "Medicare relevance could not be established from the ONC data or NPPES "
            "taxonomy. Evidence is collected as corroboration and cannot fail the entity."
        )

    # ── D3 Exclusion / debarment / revocation — always required, and the three
    # controls stay separately identifiable inside the dimension.
    dims[Dimension.D3_EXCLUSION_REVOCATION.value] = REQ
    why[Dimension.D3_EXCLUSION_REVOCATION.value] = (
        "OIG LEIE, SAM.gov and CMS Revocation are each required and each reported "
        "separately; they are three different controls, not one federal check."
    )

    # ── D4 Address — required whenever ONC supplied one to compare against.
    has_address = bool((entity.get("address") or [{}])[0])
    dims[Dimension.D4_ADDRESS.value] = REQ if has_address else COR
    why[Dimension.D4_ADDRESS.value] = (
        "ONC supplied an address to reconcile against the source hierarchy."
        if has_address else
        "ONC supplied no address; any address evidence found is corroborative only."
    )

    # ── D5 TEFCA alignment — always required; it is the subject of the review.
    dims[Dimension.D5_TEFCA_ALIGNMENT.value] = REQ
    why[Dimension.D5_TEFCA_ALIGNMENT.value] = (
        "TEFCA alignment is evaluated from ONC/HHS/RCE data only. Fields the RCE "
        "record does not carry are reported as not supplied, never inferred, and "
        "Exchange Purpose is never derived from Medicare data."
    )

    # ── D6 Provider ↔ organisation relationship.
    #
    # The RCE half is required wherever the RCE expressed a relationship. Either
    # pointer counts: `partOf` names a Subparticipant's Participant, and the FHIR
    # fixture's partOf reference is the pre-delivery equivalent. `orgManagingOrg`
    # is deliberately NOT counted here — it names the QHIN, which every entity
    # has, so treating it as "a relationship was expressed" would make the
    # condition always true and the distinction meaningless.
    has_parent = bool(rce_fields.part_of(entity) or _fhir_part_of(entity))
    if category in (EntityCategory.INDIVIDUAL_PROVIDER, EntityCategory.PROVIDER_ORGANIZATION):
        dims[Dimension.D6_PROVIDER_ORG_RELATIONSHIP.value] = REQ if has_parent else COR
        why[Dimension.D6_PROVIDER_ORG_RELATIONSHIP.value] = (
            "ONC expressed a parent relationship; RCE data is the primary evidence and "
            "PECOS reassignment may corroborate it."
            if has_parent else
            "No ONC-expressed relationship; PECOS reassignment is corroborative only."
        )
    else:
        dims[Dimension.D6_PROVIDER_ORG_RELATIONSHIP.value] = REQ if has_parent else NA
        why[Dimension.D6_PROVIDER_ORG_RELATIONSHIP.value] = (
            "RCE-supplied TEFCA relationship applies; PECOS reassignment is "
            "NOT_APPLICABLE for a non-provider entity unless specific circumstances "
            "establish applicability."
            if has_parent else
            "Non-provider entity with no ONC-expressed relationship."
        )

    for dim, override in (methodology_requires or {}).items():
        if dim in dims and override in {REQ, COR, NA}:
            dims[dim] = override
            why[dim] = f"Overridden by approved ARC methodology: {override}."

    return ApplicabilityProfile(
        entity_category=category,
        tefca_class=ev["tefca_class"],
        nppes_enumeration_type=ev["enumeration_type"],
        nppes_taxonomy_code=ev["taxonomy_code"],
        nppes_taxonomy_desc=ev["taxonomy_desc"],
        medicare_relevance=relevance,
        dimensions=dims,
        rationale=why,
        inputs_used=ev["inputs_used"] + (["pecos_presence"] if pecos_found is not None else []),
        sequoia_org_type=ev.get("sequoia_org_type"),
        organization_node_type=ev.get("organization_node_type"),
        npi_available=npi,
    )


def pecos_reassignment_applicability(profile: ApplicabilityProfile) -> str:
    """Applicability of the PECOS reassignment corroboration specifically.

    Separate from D6 as a whole because D6's primary evidence (the RCE
    relationship) can be required while its Medicare corroboration is not
    applicable at all — a Participant that is a health plan has a real TEFCA
    relationship and no reason to have reassigned Medicare benefits to anyone.
    """
    if profile.entity_category in (EntityCategory.PAYER, EntityCategory.HIE_HIN_QHIN,
                                   EntityCategory.PUBLIC_HEALTH_AGENCY):
        return Applicability.NOT_APPLICABLE.value
    if profile.medicare_relevance == "UNLIKELY":
        return Applicability.NOT_APPLICABLE.value
    return Applicability.CORROBORATIVE.value
