"""
Applicability engine — which evidence dimensions apply to THIS entity.

WHAT THE ONC/HHS DATA ACTUALLY CONTAINS
───────────────────────────────────────
Inspected before writing any rule (30 entities, FHIR R4 Organization, the
bundled ONC-shaped dataset). Fields present on 30/30:

  resourceType, id, identifier[], active, type[], name, telecom[], address[],
  partOf, _qhin        (alias on 4/30, meta on 1/30)

  identifier[]  system=http://hl7.org/fhir/sid/us-npi        -> NPI
                system=urn:docuaction:tefca/identifier       -> TEFCA identifier
  type[].coding[].code  -> QHIN | PARTICIPANT | SUBPARTICIPANT
  partOf.reference      -> parent organization
  _qhin                 -> QHIN attribution

FIELDS THE SPEC ASKS ABOUT THAT ONC DOES **NOT** SUPPLY: HCID, Exchange
Purpose, an NPI Type 1/Type 2 marker, and any provider/entity type beyond the
TEFCA class. Those are not invented here. Where a rule would need one, the rule
degrades to CORROBORATIVE or REVIEW instead of guessing.

THE CONSEQUENCE THAT DRIVES THIS MODULE
───────────────────────────────────────
ONC gives the TEFCA class (QHIN/Participant/Subparticipant). It does NOT give
the provider/organisation type. So Medicare relevance cannot be read off the
ONC record: it has to come from NPPES (enumeration type + taxonomy), which is
the primary identity authority anyway.

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

    def applicability_of(self, dimension: Dimension) -> str:
        return self.dimensions.get(dimension.value, Applicability.CORROBORATIVE.value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_category": self.entity_category,
            "tefca_class": self.tefca_class,
            "nppes_enumeration_type": self.nppes_enumeration_type,
            "nppes_taxonomy_code": self.nppes_taxonomy_code,
            "nppes_taxonomy_desc": self.nppes_taxonomy_desc,
            "medicare_relevance": self.medicare_relevance,
            "dimensions": dict(self.dimensions),
            "rationale": dict(self.rationale),
            "inputs_used": list(self.inputs_used),
        }


def tefca_class_of(entity: Dict[str, Any]) -> Optional[str]:
    """QHIN | PARTICIPANT | SUBPARTICIPANT from the ONC-supplied type coding."""
    for t in entity.get("type") or []:
        for c in t.get("coding") or []:
            code = (c.get("code") or "").strip().upper()
            if code in {"QHIN", "PARTICIPANT", "SUBPARTICIPANT"}:
                return code
    return None


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

    return category, {
        "tefca_class": tefca_class, "enumeration_type": enumeration_type,
        "taxonomy_code": taxonomy_code, "taxonomy_desc": taxonomy_desc,
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
        "TEFCA alignment is evaluated from ONC/HHS/RCE data only. Fields ONC did "
        "not supply (HCID, Exchange Purpose) are reported as not supplied, never inferred."
    )

    # ── D6 Provider ↔ organisation relationship.
    #
    # The RCE half is required wherever ONC expressed a relationship (partOf).
    # The PECOS reassignment half is corroborative at most, and not applicable at
    # all for a non-provider entity — a QHIN does not reassign Medicare benefits.
    has_parent = bool((entity.get("partOf") or {}).get("reference"))
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
