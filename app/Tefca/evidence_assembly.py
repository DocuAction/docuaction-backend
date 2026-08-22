"""
Assemble the six evidence dimensions from source results.

This module is where the spec's rules become code. The rules that matter most,
and where each one lives:

  * NPPES is the primary NPI identity authority. PECOS corroborates D1 and can
    never overrule it — `_dimension_identity`.
  * A PECOS non-match is never a TEFCA failure — `_dimension_medicare`.
  * OIG / SAM / CMS-Revocation stay three separately identifiable controls
    inside one dimension — `_dimension_exclusion`.
  * A revocation hit is REVIEW pending identity matching, never automatic
    rejection — `_dimension_exclusion`.
  * MULTIPLE_NPI_FLAG=Y means a differing NPI is NOT a conflict until
    ADDITIONAL_NPIS has been consulted — `_npi_alignment`.
  * RCE relationship and PECOS reassignment are different questions and are
    never treated as equivalent — `_dimension_relationship`.
  * Website evidence is supplemental and an unreachable site is never held
    against an entity — `_dimension_address` / `website_corroboration`.

No function in this file computes a score, a percentage, or a count of passing
sources, and no CMS component is counted as an independent vote.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from app.Tefca import rce_fields
from app.Tefca.address_evidence import (
    AddressComparison,
    SOURCE_NPPES,
    SOURCE_PECOS_PRACTICE_LOCATION,
    SOURCE_WEBSITE,
    build_address_rows,
    reconcile,
)
from app.Tefca.applicability import (
    ApplicabilityProfile,
    EntityCategory,
    pecos_reassignment_applicability,
    tefca_class_of,
)
from app.Tefca.cms_ppef import (
    COMPONENT_UNPUBLISHED_REASON,
    NO_ACTIVE_REVOCATION_RECORD_FOUND,
    PPEFComponent,
)
from app.Tefca.connectors import SourceResult
from app.Tefca.evidence_dimensions import (
    Applicability,
    Dimension,
    Disposition,
    DimensionResult,
    EvidenceItem,
)

NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"
TEFCA_ID_SYSTEM = "urn:docuaction:tefca/identifier"

#: Fields the spec asks about that may be absent from a given RCE record.
#:
#: CORRECTED, AND THE CORRECTION IS THE POINT. This tuple previously asserted
#: that hcid and exchange_purpose are never supplied by ONC. That was true of the
#: pre-delivery FHIR fixture and is false of the RCE delivery, which carries HCID
#: and purposesofuse on most records. D5 now reports "not supplied" only for the
#: fields THIS record actually lacks, checked per entity, rather than declaring a
#: whole category of data missing from the feed.
#:
#: What genuinely still has no RCE source: an NPI Type 1/Type 2 marker and a
#: NUCC-grade provider taxonomy. Both come from NPPES, which remains the identity
#: authority. They stay in this list because no RCE field supplies them.
ONC_FIELDS_NOT_SUPPLIED = (
    "hcid", "exchange_purpose", "npi_type_marker", "provider_entity_type",
)


def _identifier(entity: Dict[str, Any], system: str) -> Optional[str]:
    for ident in entity.get("identifier") or []:
        if (ident.get("system") or "") == system:
            return (ident.get("value") or "").strip() or None
    return None


def _submitted_address(entity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    addresses = entity.get("address") or []
    return addresses[0] if addresses else None


def _nppes_location(nppes_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for a in nppes_data.get("addresses") or []:
        if (a.get("address_purpose") or "").upper() == "LOCATION":
            return {
                "line": [a.get("address_1"), a.get("address_2")],
                "city": a.get("city"),
                "state": a.get("state"),
                "postalCode": a.get("postal_code"),
            }
    return None


def _ok(result: Optional[SourceResult]) -> bool:
    return bool(result and result.success)


# ── D1 IDENTITY ──────────────────────────────────────────────────────────────

def _npi_alignment(
    rce_npi: Optional[str],
    nppes: Optional[SourceResult],
    pecos: Optional[SourceResult],
) -> Dict[str, Any]:
    """RCE NPI ↔ NPPES ↔ PECOS, including Type 1 / Type 2 reasoning.

    Amendment 2 is enforced here. When PECOS carries MULTIPLE_NPI_FLAG=Y, the
    provider is known to hold more than one NPI, and the file that lists the
    others (ADDITIONAL_NPIS) is not published by CMS. A differing NPI in that
    state is therefore UNRESOLVED, not a conflict — reporting a conflict would
    be asserting something the available data cannot support, against an entity
    that may be entirely correct.
    """
    nppes_data = (nppes.data or {}) if _ok(nppes) else {}
    pecos_data = (pecos.data or {}) if _ok(pecos) else {}
    pecos_records: List[Dict[str, Any]] = pecos_data.get("records") or []

    nppes_npi = nppes_data.get("npi")
    nppes_type = nppes_data.get("enumeration_type")  # NPI-1 individual / NPI-2 org
    pecos_npis = sorted({r.get("npi") for r in pecos_records if r.get("npi")})
    multiple_npi = pecos_data.get("multiple_npi_flag")

    matches: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []

    if rce_npi and nppes_npi:
        if str(rce_npi) == str(nppes_npi):
            matches.append({"field": "npi", "rce": rce_npi, "nppes": nppes_npi, "result": "MATCH"})
        else:
            conflicts.append({
                "field": "npi", "rce": rce_npi, "nppes": nppes_npi, "result": "CONFLICT",
                "note": "RCE-supplied NPI differs from the NPI NPPES returned.",
            })

    if rce_npi and pecos_npis:
        if str(rce_npi) in {str(n) for n in pecos_npis}:
            matches.append({"field": "npi", "rce": rce_npi, "pecos": pecos_npis, "result": "MATCH",
                            "note": "Corroboration only — NPPES remains the identity authority."})
        elif multiple_npi == "Y":
            unresolved.append({
                "field": "npi", "rce": rce_npi, "pecos_primary": pecos_npis,
                "result": "UNRESOLVED_MULTIPLE_NPI",
                "rule_applied": "AMENDMENT_2_MULTIPLE_NPI_FLAG",
                "note": ("MULTIPLE_NPI_FLAG=Y: the provider holds additional NPIs. The "
                         "PPEF ADDITIONAL_NPIS component is required to resolve this and "
                         f"is not published — {COMPONENT_UNPUBLISHED_REASON}. A differing "
                         "NPI is NOT a conflict in this state."),
            })
        else:
            unresolved.append({
                "field": "npi", "rce": rce_npi, "pecos_primary": pecos_npis,
                "result": "PECOS_NPI_DIFFERS",
                "note": ("PECOS enrolment NPI differs. PECOS is corroborative for identity; "
                         "NPPES governs. Presented for analyst review."),
            })

    # Type 1 / Type 2 reasoning. The ONC record carries no NPI type marker, so
    # the comparison is NPPES enumeration type against the enrolment class PECOS
    # reports and against the TEFCA class ONC assigned.
    type_alignment: Dict[str, Any] = {
        "nppes_enumeration_type": nppes_type,
        "nppes_type_meaning": (
            "Type 1 — individual practitioner" if nppes_type == "NPI-1"
            else "Type 2 — organization" if nppes_type == "NPI-2" else None
        ),
        "pecos_enrollment_classes": sorted({
            r.get("enrollment_class") for r in pecos_records if r.get("enrollment_class")
        }),
        "onc_supplied_npi_type_marker": None,  # ONC does not supply one — verified.
        "result": "NOT_EVALUATED",
    }
    pecos_classes = set(type_alignment["pecos_enrollment_classes"])
    if nppes_type and pecos_classes:
        expected = "INDIVIDUAL" if nppes_type == "NPI-1" else "ORGANIZATION"
        if expected in pecos_classes:
            type_alignment["result"] = "ALIGNED"
        else:
            type_alignment["result"] = "DIVERGENT"
            unresolved.append({
                "field": "npi_type", "nppes": nppes_type,
                "pecos_enrollment_classes": sorted(pecos_classes),
                "result": "TYPE_DIVERGENCE",
                "note": ("NPPES entity type and the PECOS enrolment class disagree. An "
                         "individual practitioner and the organisation they bill under are "
                         "different subjects; presented for analyst review, not scored."),
            })
    elif nppes_type:
        type_alignment["result"] = "NPPES_ONLY"

    return {
        "matches": matches,
        "conflicts": conflicts,
        "unresolved": unresolved,
        "type_alignment": type_alignment,
        "rce_npi": rce_npi,
        "nppes_npi": nppes_npi,
        "pecos_npis": pecos_npis,
        "multiple_npi_flag": multiple_npi,
    }


def _dimension_identity(
    entity: Dict[str, Any],
    profile: ApplicabilityProfile,
    sources: Dict[str, SourceResult],
) -> DimensionResult:
    dim = Dimension.D1_IDENTITY.value
    applicability = profile.applicability_of(Dimension.D1_IDENTITY)
    nppes = sources.get("nppes")
    pecos = sources.get("cms_ppef_enrollment")
    rce_npi = _identifier(entity, NPI_SYSTEM)
    items: List[EvidenceItem] = []

    # Primary authority: NPPES.
    if not _ok(nppes):
        items.append(EvidenceItem(
            dimension=dim, source="NPPES", disposition=Disposition.UNAVAILABLE.value,
            fields_evaluated=["npi", "legal_name", "enumeration_type", "taxonomy"],
            note=(nppes.error if nppes else "NPPES was not queried."),
            rule_applied="PRIMARY_IDENTITY_AUTHORITY_UNAVAILABLE",
        ))
        nppes_disposition = Disposition.UNAVAILABLE
    else:
        nppes_data = nppes.data or {}
        found = bool(nppes_data.get("found"))
        submitted_name = entity.get("name") or ""
        name_match = (
            bool(submitted_name) and bool(nppes_data.get("legal_name"))
            and submitted_name.strip().upper() == str(nppes_data["legal_name"]).strip().upper()
        )
        items.append(EvidenceItem(
            dimension=dim, source="NPPES",
            disposition=Disposition.PASS.value if found else Disposition.NOT_FOUND.value,
            source_record_identifier=nppes_data.get("npi"),
            query_timestamp=nppes.query_timestamp,
            dataset_version_anchor=nppes.api_version,
            record_count=1 if found else 0,
            fields_evaluated=["npi", "legal_name", "enumeration_type", "taxonomy", "status"],
            field_matches=([{"field": "legal_name", "submitted": submitted_name,
                             "nppes": nppes_data.get("legal_name"), "result": "MATCH"}]
                           if name_match else []),
            field_conflicts=([{"field": "legal_name", "submitted": submitted_name,
                               "nppes": nppes_data.get("legal_name"), "result": "DIFFERS"}]
                             if found and submitted_name and not name_match else []),
            original_values={k: nppes_data.get(k) for k in
                             ("npi", "legal_name", "enumeration_type", "taxonomy", "taxonomy_code", "status")},
            rule_applied="NPPES_PRIMARY_IDENTITY_AUTHORITY",
            note=None if found else "NPI not present in NPPES.",
        ))
        nppes_disposition = Disposition.PASS if found else Disposition.REVIEW

    # Corroboration: PECOS. Explicitly labelled so it can never read as primary.
    alignment = _npi_alignment(rce_npi, nppes, pecos)
    if pecos is not None:
        if _ok(pecos):
            pecos_data = pecos.data or {}
            items.append(EvidenceItem.from_provenance(
                dimension=dim, source="CMS_PPEF_ENROLLMENT",
                disposition=(Disposition.CORROBORATED.value
                             if alignment["matches"] else
                             Disposition.INSUFFICIENT_EVIDENCE.value
                             if alignment["unresolved"] else Disposition.NOT_FOUND.value),
                provenance=pecos_data.get("provenance"),
                ppef_component=PPEFComponent.ENROLLMENT.value,
                source_record_identifier=",".join(pecos_data.get("enrollment_ids") or []) or None,
                fields_evaluated=["NPI", "ENRLMT_ID", "PECOS_ASCT_CNTL_ID", "MULTIPLE_NPI_FLAG",
                                  "PROVIDER_TYPE_DESC", "ORG_NAME", "FIRST_NAME", "LAST_NAME"],
                field_matches=alignment["matches"],
                field_conflicts=[],  # PECOS never contributes a conflict to identity.
                original_values={
                    "pecos_npis": alignment["pecos_npis"],
                    "enrollment_ids": pecos_data.get("enrollment_ids"),
                    "pac_ids": pecos_data.get("pac_ids"),
                    "multiple_npi_flag": alignment["multiple_npi_flag"],
                },
                normalized_values={"type_alignment": alignment["type_alignment"],
                                   "unresolved": alignment["unresolved"]},
                rule_applied="PECOS_CORROBORATES_IDENTITY_NEVER_REPLACES_NPPES",
                note="Corroborative only. NPPES remains the primary NPI identity source.",
            ))
        else:
            items.append(EvidenceItem(
                dimension=dim, source="CMS_PPEF_ENROLLMENT",
                disposition=Disposition.UNAVAILABLE.value,
                ppef_component=PPEFComponent.ENROLLMENT.value,
                note=pecos.error, rule_applied="CORROBORATION_UNAVAILABLE_NOT_A_FINDING",
            ))

    # Dimension roll-up. Identity conflicts against the PRIMARY authority are the
    # only thing that can move this to REVIEW; corroboration gaps cannot.
    if nppes_disposition == Disposition.UNAVAILABLE:
        disposition = Disposition.UNAVAILABLE
        rationale = "The primary identity authority (NPPES) was unavailable."
        requires_analyst = True
    elif alignment["conflicts"]:
        disposition = Disposition.REVIEW
        rationale = ("RCE-supplied identity differs from NPPES. Presented for analyst "
                     "determination; not auto-failed.")
        requires_analyst = True
    elif nppes_disposition == Disposition.REVIEW:
        disposition = Disposition.REVIEW
        rationale = "NPI was not found in NPPES."
        requires_analyst = True
    elif alignment["unresolved"]:
        disposition = Disposition.REVIEW
        rationale = ("Identity established from NPPES; PECOS corroboration is unresolved "
                     "(see MULTIPLE_NPI_FLAG / type alignment notes).")
        requires_analyst = True
    else:
        disposition = Disposition.PASS
        rationale = "Identity established from NPPES, with PECOS corroboration where available."
        requires_analyst = False

    return DimensionResult(
        dimension=dim, disposition=disposition.value, applicability=applicability,
        rationale=rationale, items=items, requires_analyst=requires_analyst,
    )


# ── D2 MEDICARE ENROLLMENT ───────────────────────────────────────────────────

def _dimension_medicare(profile: ApplicabilityProfile,
                        sources: Dict[str, SourceResult]) -> DimensionResult:
    dim = Dimension.D2_MEDICARE_ENROLLMENT.value
    applicability = profile.applicability_of(Dimension.D2_MEDICARE_ENROLLMENT)
    pecos = sources.get("cms_ppef_enrollment")
    items: List[EvidenceItem] = []

    if applicability == Applicability.NOT_APPLICABLE.value:
        return DimensionResult(
            dimension=dim, disposition=Disposition.NOT_APPLICABLE.value,
            applicability=applicability,
            rationale=profile.rationale.get(dim, "Medicare enrolment does not apply to this entity."),
            items=items,
        )

    if not _ok(pecos):
        items.append(EvidenceItem(
            dimension=dim, source="CMS_PPEF_ENROLLMENT", disposition=Disposition.UNAVAILABLE.value,
            ppef_component=PPEFComponent.ENROLLMENT.value,
            note=(pecos.error if pecos else "PECOS enrolment was not queried."),
            rule_applied="CMS_OUTAGE_IS_NOT_A_VERIFICATION_FAILURE",
        ))
        return DimensionResult(
            dimension=dim, disposition=Disposition.UNAVAILABLE.value, applicability=applicability,
            rationale=("CMS PPEF did not answer. Recorded as unavailable evidence; it is "
                       "not a failure and does not count against the entity."),
            items=items, requires_analyst=True,
        )

    data = pecos.data or {}
    records = data.get("records") or []
    found = bool(data.get("found"))
    items.append(EvidenceItem.from_provenance(
        dimension=dim, source="CMS_PPEF_ENROLLMENT",
        disposition=Disposition.PASS.value if found else Disposition.NOT_FOUND.value,
        provenance=data.get("provenance"),
        ppef_component=PPEFComponent.ENROLLMENT.value,
        source_record_identifier=",".join(data.get("enrollment_ids") or []) or None,
        fields_evaluated=["NPI", "ENRLMT_ID", "PECOS_ASCT_CNTL_ID", "PROVIDER_TYPE_CD",
                          "PROVIDER_TYPE_DESC", "STATE_CD", "MULTIPLE_NPI_FLAG"],
        original_values={
            "records": records,
            "enrollment_ids": data.get("enrollment_ids"),
            "pac_ids": data.get("pac_ids"),
            "record_count": data.get("record_count"),
        },
        rule_applied="PECOS_ENROLLMENT_EVIDENCE",
        note=("An approved Medicare enrolment is represented in the public PPEF."
              if found else
              "No enrolment for this NPI in the public PPEF extract. This is not a "
              "TEFCA failure — public PECOS data is quarterly and not exhaustive of "
              "every enrolment scenario."),
    ))

    if found:
        disposition, rationale, needs = (
            Disposition.PASS,
            f"Medicare enrolment evidence present ({len(records)} PPEF record(s)).",
            False,
        )
    elif applicability == Applicability.REQUIRED.value:
        disposition, rationale, needs = (
            Disposition.REVIEW,
            ("Medicare relevance is established for this entity but no PPEF enrolment "
             "was found. Routed to analyst review; a PECOS non-match is never an "
             "automatic TEFCA failure."),
            True,
        )
    else:
        disposition, rationale, needs = (
            Disposition.NOT_APPLICABLE,
            ("No PPEF enrolment found and Medicare relevance was not established for "
             "this entity type. Nothing here counts against the entity."),
            False,
        )
    return DimensionResult(dimension=dim, disposition=disposition.value,
                           applicability=applicability, rationale=rationale,
                           items=items, requires_analyst=needs)


# ── D3 EXCLUSION / DEBARMENT / REVOCATION ────────────────────────────────────

def _dimension_exclusion(entity: Dict[str, Any], profile: ApplicabilityProfile,
                         sources: Dict[str, SourceResult]) -> DimensionResult:
    """Three controls, three separately identifiable results, one dimension.

    They are never collapsed into "federal checks passed": OIG exclusion, SAM
    debarment and CMS revocation answer different questions and a reviewer has
    to be able to see which one spoke.

    APPLICABILITY IS DECIDED PER SOURCE, NOT ONCE FOR THE DIMENSION
    ──────────────────────────────────────────────────────────────
    A missing NPI does NOT make D3 not-applicable. Exclusion and debarment
    screening are obligations that attach to the ORGANISATION, and both OIG LEIE
    and SAM.gov support organisation-name search — so where no NPI exists, the
    org-level check is attempted rather than skipped.

    What must never happen is the previous behaviour: `lookup_by_npi("")`
    returned a clean "no exclusion found" for an entity nothing was searched
    for, which is a manufactured negative. Each source now resolves to one of:

        PASS                   searched, and nothing found
        REVIEW                 searched, potential match, analyst decides
        NOT_FOUND              org-level search ran and matched nothing
        INSUFFICIENT_EVIDENCE  no usable identifier for this source; the check
                               could not be performed and is NOT reported clean
        UNAVAILABLE            the source itself could not be reached
        NOT_APPLICABLE         the control does not attach to this entity type
    """
    dim = Dimension.D3_EXCLUSION_REVOCATION.value
    applicability = profile.applicability_of(Dimension.D3_EXCLUSION_REVOCATION)
    items: List[EvidenceItem] = []
    any_hit = False
    any_unavailable = False
    any_insufficient = False

    npi = profile.npi_available
    org_name = (rce_fields.rce_value(entity, "name") or entity.get("name") or "").strip()
    uei = _identifier(entity, "urn:docuaction:tefca/identifier/uei")

    # ── OIG LEIE — NPI first, organisation name as the documented fallback ──
    leie = sources.get("leie_npi") if npi else None
    leie_org = sources.get("leie_org")
    if npi and _ok(leie):
        d = leie.data or {}
        excluded = bool(d.get("excluded") or d.get("match") or d.get("matches"))
        any_hit = any_hit or excluded
        items.append(EvidenceItem(
            dimension=dim, source="OIG_LEIE",
            disposition=Disposition.REVIEW.value if excluded else Disposition.PASS.value,
            query_timestamp=leie.query_timestamp, dataset_version_anchor=leie.api_version,
            query_identifier=f"npi={npi}",
            fields_evaluated=["npi", "exclusion_type", "exclusion_date"],
            original_values=dict(d),
            rule_applied="OIG_LEIE_EXCLUSION_CHECK_BY_NPI",
            note="Potential exclusion match — analyst determination required."
                 if excluded else "No exclusion record found for this NPI.",
        ))
    elif _ok(leie_org):
        d = leie_org.data or {}
        excluded = bool(d.get("excluded") or d.get("exclusion_found"))
        any_hit = any_hit or excluded
        items.append(EvidenceItem(
            dimension=dim, source="OIG_LEIE",
            disposition=Disposition.REVIEW.value if excluded else Disposition.NOT_FOUND.value,
            query_timestamp=leie_org.query_timestamp,
            dataset_version_anchor=leie_org.api_version,
            query_identifier=f"org={org_name}",
            fields_evaluated=["business_name", "exclusion_type", "exclusion_date"],
            original_values=dict(d),
            rule_applied="OIG_LEIE_ORG_LEVEL_CHECK_NO_NPI",
            note=("Potential exclusion match on the organisation name — analyst "
                  "determination required."
                  if excluded else
                  "No NPI available, so LEIE was screened by organisation name. No "
                  "match found. NOT_FOUND rather than PASS: a name search is weaker "
                  "evidence than an NPI match and must not read as an equivalent "
                  "clearance."),
        ))
    elif leie_org is not None and not _ok(leie_org):
        any_unavailable = True
        items.append(EvidenceItem(
            dimension=dim, source="OIG_LEIE", disposition=Disposition.UNAVAILABLE.value,
            rule_applied="OIG_LEIE_ORG_LEVEL_CHECK_NO_NPI",
            note=(leie_org.error or "OIG LEIE could not be reached."),
        ))
    elif not npi and not org_name:
        any_insufficient = True
        items.append(EvidenceItem(
            dimension=dim, source="OIG_LEIE",
            disposition=Disposition.INSUFFICIENT_EVIDENCE.value,
            rule_applied="NO_USABLE_IDENTIFIER_FOR_LEIE",
            note=("Neither an NPI nor an organisation name is available, so no LEIE "
                  "screening could be performed. Reported as insufficient evidence — "
                  "NOT as an absence of exclusions."),
        ))
    else:
        any_unavailable = True
        items.append(EvidenceItem(
            dimension=dim, source="OIG_LEIE", disposition=Disposition.UNAVAILABLE.value,
            note=(leie.error if leie else "OIG LEIE was not queried."),
        ))

    # ── SAM.gov — keyed on UEI; organisation name is the fallback ──
    #
    # SAM is NOT keyed on NPI at all, so a missing NPI is irrelevant to it. What
    # matters is whether a UEI or a legal name was available.
    sam = sources.get("sam_exclusion")
    sam_name = sources.get("sam_name")
    if _ok(sam) and uei:
        d = sam.data or {}
        debarred = bool(d.get("debarred") or d.get("exclusions"))
        any_hit = any_hit or debarred
        items.append(EvidenceItem(
            dimension=dim, source="SAM_GOV",
            disposition=Disposition.REVIEW.value if debarred else Disposition.PASS.value,
            query_timestamp=sam.query_timestamp, dataset_version_anchor=sam.api_version,
            query_identifier=f"uei={uei}",
            fields_evaluated=["uei", "registration_status", "exclusions"],
            original_values=dict(d),
            rule_applied="SAM_DEBARMENT_CHECK_BY_UEI",
            note="Potential debarment match — analyst determination required."
                 if debarred else "No debarment record found.",
        ))
    elif _ok(sam_name):
        d = sam_name.data or {}
        debarred = bool(d.get("debarred") or d.get("exclusions"))
        any_hit = any_hit or debarred
        items.append(EvidenceItem(
            dimension=dim, source="SAM_GOV",
            disposition=Disposition.REVIEW.value if debarred else Disposition.NOT_FOUND.value,
            query_timestamp=sam_name.query_timestamp,
            dataset_version_anchor=sam_name.api_version,
            query_identifier=f"legal_name={org_name}",
            fields_evaluated=["legal_name", "registration_status", "exclusions"],
            original_values=dict(d),
            rule_applied="SAM_ORG_LEVEL_CHECK_NO_UEI",
            note=("Potential debarment match on the organisation name — analyst "
                  "determination required."
                  if debarred else
                  "No UEI available, so SAM.gov was screened by legal name. No match "
                  "found. NOT_FOUND rather than PASS — a name search does not carry "
                  "the weight of a UEI match."),
        ))
    elif _ok(sam):
        d = sam.data or {}
        debarred = bool(d.get("debarred") or d.get("exclusions"))
        any_hit = any_hit or debarred
        items.append(EvidenceItem(
            dimension=dim, source="SAM_GOV",
            disposition=Disposition.REVIEW.value if debarred else Disposition.PASS.value,
            query_timestamp=sam.query_timestamp, dataset_version_anchor=sam.api_version,
            fields_evaluated=["uei", "registration_status", "exclusions"],
            original_values=dict(d),
            rule_applied="SAM_DEBARMENT_CHECK",
            note="Potential debarment match — analyst determination required."
                 if debarred else "No debarment record found.",
        ))
    elif not uei and not org_name:
        any_insufficient = True
        items.append(EvidenceItem(
            dimension=dim, source="SAM_GOV",
            disposition=Disposition.INSUFFICIENT_EVIDENCE.value,
            rule_applied="NO_USABLE_IDENTIFIER_FOR_SAM",
            note=("Neither a UEI nor an organisation name is available, so no SAM.gov "
                  "screening could be performed. Reported as insufficient evidence — "
                  "NOT as an absence of debarments."),
        ))
    else:
        any_unavailable = True
        items.append(EvidenceItem(
            dimension=dim, source="SAM_GOV", disposition=Disposition.UNAVAILABLE.value,
            note=(sam.error if sam else "SAM.gov was not queried."),
        ))

    revocation = sources.get("cms_revocation")
    if _ok(revocation):
        d = revocation.data or {}
        matches = d.get("matches") or []
        any_hit = any_hit or bool(matches)
        items.append(EvidenceItem.from_provenance(
            dimension=dim, source="CMS_REVOCATION",
            disposition=Disposition.REVIEW.value if matches else Disposition.PASS.value,
            provenance=d.get("provenance"),
            source_record_identifier=",".join(
                m["enrollment_id"] for m in matches if m.get("enrollment_id")) or None,
            fields_evaluated=["ENRLMT_ID", "NPI", "ORG_NAME", "STATE_CD", "PROVIDER_TYPE_DESC",
                              "REVOCATION_RSN", "REVOCATION_EFCTV_DT",
                              "REENROLLMENT_BAR_EXPRTN_DT"],
            original_values={"matches": matches, "result": d.get("result")},
            rule_applied="AMENDMENT_1_REVOCATION_SEMANTICS",
            note=(
                "Potential revocation match. REVIEW pending identity matching and analyst "
                "evaluation — never an automatic rejection."
                if matches else
                f"{NO_ACTIVE_REVOCATION_RECORD_FOUND}. This satisfies the CMS Revocation "
                "check ONLY: it is not evidence of enrolment, eligibility to enrol, or "
                "overall good standing."
            ),
        ))
    else:
        any_unavailable = True
        items.append(EvidenceItem(
            dimension=dim, source="CMS_REVOCATION", disposition=Disposition.UNAVAILABLE.value,
            note=(revocation.error if revocation else "CMS revocation was not queried."),
            rule_applied="CMS_OUTAGE_IS_NOT_A_VERIFICATION_FAILURE",
        ))

    # Roll-up. Order matters: a potential match outranks an outage, and an
    # outage outranks a check that could not be attempted — but NONE of the
    # three ever produces a clean PASS, because each means something different
    # was left unestablished.
    if any_hit:
        disposition, rationale, needs = (
            Disposition.REVIEW,
            ("At least one exclusion/debarment/revocation control returned a potential "
             "match. Each control is reported separately above. Analyst determination "
             "required — no entity is rejected on a data match alone."),
            True,
        )
    elif any_unavailable:
        disposition, rationale, needs = (
            Disposition.UNAVAILABLE,
            "One or more of the three controls could not be reached. Not a finding.",
            True,
        )
    elif any_insufficient:
        disposition, rationale, needs = (
            Disposition.INSUFFICIENT_EVIDENCE,
            ("One or more controls had no usable identifier to search on, so the check "
             "could not be performed. This is NOT a clearance and NOT a finding against "
             "the entity — it is a statement that the screening did not happen."),
            True,
        )
    else:
        settled = [i.source for i in items
                   if i.disposition == Disposition.PASS.value]
        name_only = [i.source for i in items
                     if i.disposition == Disposition.NOT_FOUND.value]
        rationale = (
            "All three controls answered with no match: OIG LEIE no exclusion, SAM no "
            f"debarment, CMS {NO_ACTIVE_REVOCATION_RECORD_FOUND}."
        )
        if name_only:
            # Screened, nothing found, but on a weaker key. The dimension does
            # not claim a full PASS on the strength of a name search alone.
            rationale = (
                f"No exclusion, debarment or revocation match found. {', '.join(sorted(set(name_only)))} "
                "was screened at organisation level rather than by identifier, which is "
                "weaker evidence; the dimension is reported as REVIEW so an analyst "
                "confirms the identity behind the name search."
            )
            disposition, needs = Disposition.REVIEW, True
        else:
            disposition, needs = Disposition.PASS, False
        return DimensionResult(dimension=dim, disposition=disposition.value,
                               applicability=applicability, rationale=rationale,
                               items=items, requires_analyst=needs)
    return DimensionResult(dimension=dim, disposition=disposition.value,
                           applicability=applicability, rationale=rationale,
                           items=items, requires_analyst=needs)


# ── D4 ADDRESS ───────────────────────────────────────────────────────────────

_ADDRESS_RESULT_TO_DISPOSITION = {
    AddressComparison.MATCH: Disposition.PASS,
    AddressComparison.PARTIAL_MATCH: Disposition.REVIEW,
    AddressComparison.CONFLICT: Disposition.REVIEW,
    AddressComparison.NOT_FOUND: Disposition.NOT_FOUND,
    AddressComparison.UNAVAILABLE: Disposition.UNAVAILABLE,
}


def _dimension_address(entity: Dict[str, Any], profile: ApplicabilityProfile,
                       sources: Dict[str, SourceResult],
                       website_evidence: Optional[Dict[str, Any]] = None) -> DimensionResult:
    dim = Dimension.D4_ADDRESS.value
    applicability = profile.applicability_of(Dimension.D4_ADDRESS)
    submitted = _submitted_address(entity)
    candidates: List[Dict[str, Any]] = []

    nppes = sources.get("nppes")
    if _ok(nppes):
        candidates.append({
            "source": SOURCE_NPPES,
            "address": _nppes_location(nppes.data or {}),
            "query_timestamp": nppes.query_timestamp,
            "dataset_anchor": nppes.api_version,
        })
    else:
        candidates.append({"source": SOURCE_NPPES, "unavailable": True,
                           "note": (nppes.error if nppes else "NPPES not queried.")})

    location = sources.get("cms_ppef_practice_location")
    if _ok(location):
        rows = (location.data or {}).get("records") or []
        # One-to-many: every location row becomes its own comparison row, so
        # "the provider also bills from a second site" is visible rather than
        # collapsed into whichever row happened to be first.
        if not rows:
            # Distinguish "CMS has no location row for this enrolment" from
            # "our snapshot is partial and cannot answer".
            if (location.data or {}).get("inconclusive"):
                candidates.append({
                    "source": SOURCE_PECOS_PRACTICE_LOCATION, "unavailable": True,
                    "note": (location.data or {}).get("inconclusive_reason"),
                })
            else:
                candidates.append({
                    "source": SOURCE_PECOS_PRACTICE_LOCATION, "address": None,
                    "note": ("NO_PRACTICE_LOCATION — CMS documents that some individual "
                             "enrolments legitimately have no practice location row. Not a failure."),
                })
        for row in rows:
            candidates.append({
                "source": SOURCE_PECOS_PRACTICE_LOCATION,
                "address": {"line": [row.get("ADR_LN_1"), row.get("ADR_LN_2")],
                            "city": row.get("CITY_NAME"), "state": row.get("STATE_CD"),
                            "postalCode": row.get("ZIP_CD")},
                "dataset_anchor": location.api_version,
            })
    else:
        candidates.append({
            "source": SOURCE_PECOS_PRACTICE_LOCATION, "unavailable": True,
            "note": (location.error if location else COMPONENT_UNPUBLISHED_REASON),
        })

    if website_evidence:
        candidates.append({
            "source": SOURCE_WEBSITE,
            "address": website_evidence.get("address"),
            "unavailable": website_evidence.get("unavailable", False),
            "note": website_evidence.get("note"),
        })

    rows = build_address_rows(submitted, candidates)
    result = reconcile(rows)
    disposition = _ADDRESS_RESULT_TO_DISPOSITION.get(result["result"], Disposition.REVIEW)

    items = [EvidenceItem(
        dimension=dim, source=row["source"], disposition=row["comparison"],
        source_dataset=row.get("dataset_anchor"),
        query_timestamp=row.get("query_timestamp"),
        fields_evaluated=["line", "city", "state", "postal_code"],
        original_values={"address": row["original_value"], "components": row["components"]},
        normalized_values={"normalized": row["normalized_value"]},
        field_conflicts=[{"field": "address", "differences": row["differences"]}]
                        if row["differences"] else [],
        rule_applied="ADDRESS_COMPARE_NEVER_OVERWRITE",
        note=row.get("note"),
        retrieved_at=row["retrieved_at"],
    ) for row in result["rows"]]

    return DimensionResult(
        dimension=dim, disposition=disposition.value, applicability=applicability,
        rationale=result["rationale"], items=items,
        requires_analyst=disposition in (Disposition.REVIEW, Disposition.UNAVAILABLE),
    )


# ── D5 TEFCA ALIGNMENT ───────────────────────────────────────────────────────

def _dimension_tefca(
    entity: Dict[str, Any],
    profile: ApplicabilityProfile,
    parent_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> DimensionResult:
    """Evaluated from ONC/HHS/RCE data ONLY.

    Six facets, each with its own disposition, rolled up into one dimension
    result. The facets are reported individually because "D5 REVIEW" without
    saying WHICH part of the TEFCA record is in question is not reviewable.

        TEFCAID          present            -> PASS      / NOT_FOUND
        HCID             present            -> PASS      / NOT_FOUND
        sequoiaorgtype   present + in vocab -> PASS      / REVIEW
        purposesofuse    present            -> PASS      / NOT_APPLICABLE
        partOf           resolves           -> PASS      / REVIEW
        active           active=1           -> PASS      / REVIEW

    Fields the RCE record does not carry are reported as not supplied. They are
    never inferred from PECOS, and Exchange Purpose in particular is never
    derived from Medicare data — PECOS has nothing to say about why two
    organisations exchange information under TEFCA.
    """
    dim = Dimension.D5_TEFCA_ALIGNMENT.value
    applicability = profile.applicability_of(Dimension.D5_TEFCA_ALIGNMENT)

    tefca_class = tefca_class_of(entity)
    tefcaid = rce_fields.tefca_id(entity)
    hcid = rce_fields.hcid(entity)
    aaid = rce_fields.aaid(entity)
    sequoia = rce_fields.sequoia_org_type(entity)
    node_type = rce_fields.organization_node_type(entity)
    purposes = rce_fields.purposes_of_use(entity)
    rce_parent = rce_fields.part_of(entity)
    managing_org = rce_fields.org_managing_org(entity)
    active = rce_fields.is_active(entity)
    npi = rce_fields.rce_npi(entity) or _identifier(entity, NPI_SYSTEM)
    legacy_identifier = _identifier(entity, TEFCA_ID_SYSTEM)
    fhir_parent = (entity.get("partOf") or {}).get("reference")
    qhin = entity.get("_qhin")

    facets: Dict[str, Dict[str, Any]] = {}
    matches: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []

    def facet(name: str, disposition: Disposition, value: Any, note: str) -> None:
        facets[name] = {"disposition": disposition.value, "value": value, "note": note}
        if disposition is Disposition.PASS:
            matches.append({"field": name, "value": value, "result": "PASS"})
        elif disposition in (Disposition.REVIEW, Disposition.CONFLICT):
            conflicts.append({"field": name, "value": value,
                              "result": disposition.value, "note": note})

    # ── TEFCAID ──
    facet("tefca_identifier", Disposition.PASS if tefcaid else Disposition.NOT_FOUND,
          tefcaid,
          "TEFCA identifier supplied by the RCE." if tefcaid else
          "No TEFCAID in the RCE record. Reported as not found; nothing is inferred.")

    # ── HCID ──
    #
    # NOT_FOUND rather than REVIEW. The delivery is documented to omit HCID on
    # some records, so its absence is a known property of the data rather than a
    # question about this entity.
    facet("hcid", Disposition.PASS if hcid else Disposition.NOT_FOUND, hcid,
          "Home Community ID supplied by the RCE." if hcid else
          "No HCID in the RCE record. The delivery omits HCID on some records; "
          "reported as not found, never inferred.")

    # ── sequoiaorgtype ──
    if sequoia and sequoia in rce_fields.SEQUOIA_ORG_TYPES:
        facet("sequoia_org_type", Disposition.PASS, sequoia,
              f"RCE organisational classification: {sequoia}.")
    elif sequoia:
        facet("sequoia_org_type", Disposition.REVIEW, sequoia,
              f"sequoiaorgtype '{sequoia}' is outside the delivered vocabulary "
              f"{list(rce_fields.SEQUOIA_ORG_TYPES)}. Analyst determination required.")
    else:
        facet("sequoia_org_type", Disposition.REVIEW, None,
              "No sequoiaorgtype in the RCE record. The entity's TEFCA "
              "classification cannot be established from the delivered data.")

    # ── purposesofuse ──
    #
    # NOT_APPLICABLE, not REVIEW: an entity that exchanges for no declared
    # purpose has not failed anything, and the delivery leaves the field empty
    # on records where no purpose applies.
    facet("exchange_purpose",
          Disposition.PASS if purposes else Disposition.NOT_APPLICABLE,
          purposes,
          f"Exchange Purpose supplied: {', '.join(purposes)}." if purposes else
          "No Exchange Purpose supplied in the RCE record. NOT_APPLICABLE — never "
          "inferred, and never derived from PECOS.")

    # ── partOf ──
    parent_reference = rce_fields.parent_reference(entity)
    if not parent_reference:
        if tefca_class == "SUBPARTICIPANT":
            facet("parent_relationship", Disposition.REVIEW, None,
                  "A Subparticipant with no partOf in the RCE record. The TEFCA "
                  "hierarchy requires a Participant parent; analyst determination "
                  "required.")
        else:
            facet("parent_relationship", Disposition.NOT_APPLICABLE, None,
                  "No partOf supplied. A Participant's parent is its QHIN, expressed "
                  "through orgManagingOrg rather than partOf.")
    elif parent_resolver is None:
        # PRESENT but not checked. Deliberately distinct from "unresolved":
        # claiming a broken hierarchy because nothing looked is a false finding.
        facet("parent_relationship", Disposition.PASS, parent_reference,
              "Parent reference present in the RCE record. Resolution against the "
              "entity population was not attempted in this evaluation.")
    elif parent_resolver(parent_reference) is not None:
        facet("parent_relationship", Disposition.PASS, parent_reference,
              "Parent reference resolves to an entity in the TEFCA population.")
    else:
        facet("parent_relationship", Disposition.REVIEW, parent_reference,
              f"partOf '{parent_reference}' does not resolve to any entity in the "
              "population. Analyst determination required — the parent may simply "
              "not be in the delivered scope.")

    # ── active ──
    facet("active_status", Disposition.PASS if active else Disposition.REVIEW,
          "1" if active else "0",
          "Entity is active in the RCE record." if active else
          "Entity is INACTIVE in the RCE record (active=0). Routed to analyst "
          "review; an inactive entity is a legitimate reportable state and the "
          "record is preserved, never dropped.")

    supplied = {
        "tefca_class": tefca_class,
        "sequoia_org_type": sequoia,
        "organization_node_type": node_type,
        "tefca_identifier": tefcaid,
        "hcid": hcid,
        "aaid": aaid,
        "npi": npi,
        "exchange_purpose": purposes,
        "parent_reference": parent_reference,
        "org_managing_org": managing_org,
        "qhin_attribution": qhin,
        "organization_name": entity.get("name"),
        "active": "1" if active else "0",
        "legacy_tefca_identifier": legacy_identifier,
    }
    not_supplied = {
        field: "NOT_SUPPLIED_BY_RCE"
        for field in ONC_FIELDS_NOT_SUPPLIED
        if not supplied.get(field)
    }

    quality = rce_fields.quality_flags(entity)
    if quality:
        # Data-quality signals are reported alongside the TEFCA facts, not folded
        # into them. Mojibake in a name does not make the entity non-aligned; it
        # makes the record defective, which is a different statement.
        conflicts.append({
            "field": "data_quality", "result": "FLAGGED", "value": quality,
            "note": "Record-level data-quality flags. Reported for the analyst; "
                    "no value is auto-corrected and nothing here alters the "
                    "TEFCA alignment facets above.",
        })

    needs_analyst = any(
        f["disposition"] == Disposition.REVIEW.value for f in facets.values()
    )
    disposition = Disposition.REVIEW if needs_analyst else Disposition.PASS

    item = EvidenceItem(
        dimension=dim, source="ONC_RCE_DIRECTORY", disposition=disposition.value,
        source_record_identifier=tefcaid or rce_fields.rce_value(entity, "id"),
        fields_evaluated=sorted(supplied.keys()),
        field_matches=matches,
        field_conflicts=conflicts,
        original_values=supplied,
        normalized_values={
            "facets": facets,
            "fields_not_supplied_by_rce": not_supplied,
            "data_quality_flags": quality,
            "organization_node_type_note": (
                "organizationNodeType describes technical exchange behaviour. It is "
                "NOT the TEFCA hierarchy and is never used to derive the entity's "
                "TEFCA class."
            ),
        },
        rule_applied="TEFCA_ALIGNMENT_FROM_RCE_DATA_ONLY",
        note=("Evaluated exclusively from the RCE-supplied record. Nothing here is "
              "inferred from Medicare data, and Exchange Purpose is never derived "
              "from PECOS."),
    )
    unresolved = sorted(k for k, v in facets.items()
                        if v["disposition"] == Disposition.REVIEW.value)
    return DimensionResult(
        dimension=dim, disposition=disposition.value, applicability=applicability,
        rationale=("TEFCA alignment evaluated from the RCE-supplied record."
                   + (f" Facets requiring analyst determination: {', '.join(unresolved)}."
                      if unresolved else "")),
        items=[item], requires_analyst=needs_analyst,
    )


# ── D6 PROVIDER ↔ ORGANIZATION RELATIONSHIP ─────────────────────────────────

def _dimension_relationship(
    entity: Dict[str, Any],
    profile: ApplicabilityProfile,
    sources: Dict[str, SourceResult],
    parent_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> DimensionResult:
    """RCE relationship is primary; PECOS reassignment corroborates at most.

    They answer different questions and are never treated as equivalent:
      RCE   — what is this organisation's relationship within TEFCA?
      PECOS — has this practitioner reassigned Medicare benefits to this entity?

    TWO RCE POINTERS, TWO DIFFERENT EDGES
    `partOf` names a Subparticipant's Participant. `orgManagingOrg` names the
    managing QHIN. They are recorded separately because they are different
    relationships: collapsing them would put a QHIN where the hierarchy expects
    a Participant, and would make "has a parent" true for every entity in the
    delivery.
    """
    dim = Dimension.D6_PROVIDER_ORG_RELATIONSHIP.value
    applicability = profile.applicability_of(Dimension.D6_PROVIDER_ORG_RELATIONSHIP)
    rce_parent = rce_fields.part_of(entity)
    fhir_parent = (entity.get("partOf") or {}).get("reference")
    parent = rce_fields.parent_reference(entity)
    managing_org = rce_fields.qhin_reference(entity)
    items: List[EvidenceItem] = []

    resolved_parent = parent_resolver(parent) if (parent and parent_resolver) else None
    parent_resolution = (
        "NOT_ATTEMPTED" if (parent and parent_resolver is None)
        else "RESOLVED" if resolved_parent is not None
        else "UNRESOLVED" if parent
        else "NO_PARENT_REFERENCE"
    )

    items.append(EvidenceItem(
        dimension=dim, source="ONC_RCE_DIRECTORY",
        disposition=Disposition.PASS.value if parent else Disposition.NOT_FOUND.value,
        source_record_identifier=rce_fields.tefca_id(entity),
        fields_evaluated=["partOf", "orgManagingOrg", "sequoiaorgtype", "_qhin"],
        original_values={
            "parent_reference": parent,
            "rce_part_of": rce_parent,
            "fhir_part_of": fhir_parent,
            "org_managing_org": managing_org,
            "qhin_attribution": entity.get("_qhin"),
            "tefca_class": tefca_class_of(entity),
            "sequoia_org_type": rce_fields.sequoia_org_type(entity),
        },
        normalized_values={
            "parent_resolution": parent_resolution,
            "resolved_parent_name": (resolved_parent or {}).get("name"),
            "edge_semantics": {
                "partOf": "Subparticipant -> its Participant",
                "orgManagingOrg": "entity -> its managing QHIN",
            },
        },
        rule_applied="RCE_RELATIONSHIP_IS_PRIMARY_TEFCA_EVIDENCE",
        note=("RCE-supplied TEFCA relationship. This is the authoritative statement of the "
              "entity's TEFCA relationship; Medicare reassignment is not a substitute for it."),
    ))

    reassignment_applicability = pecos_reassignment_applicability(profile)
    reassignment = sources.get("cms_ppef_reassignment")

    if reassignment_applicability == Applicability.NOT_APPLICABLE.value:
        items.append(EvidenceItem(
            dimension=dim, source="CMS_PPEF_REASSIGNMENT",
            disposition=Disposition.NOT_APPLICABLE.value,
            ppef_component=PPEFComponent.REASSIGNMENT.value,
            rule_applied="NON_PROVIDER_ENTITY_REASSIGNMENT_NOT_APPLICABLE",
            note=(f"{profile.entity_category} does not reassign Medicare benefits. "
                  "NOT_APPLICABLE unless specific circumstances establish applicability."),
        ))
        corroboration = Disposition.NOT_APPLICABLE
    elif not _ok(reassignment):
        items.append(EvidenceItem(
            dimension=dim, source="CMS_PPEF_REASSIGNMENT",
            disposition=Disposition.UNAVAILABLE.value,
            ppef_component=PPEFComponent.REASSIGNMENT.value,
            rule_applied="CORROBORATION_UNAVAILABLE_NOT_A_FINDING",
            note=(reassignment.error if reassignment else COMPONENT_UNPUBLISHED_REASON),
        ))
        corroboration = Disposition.UNAVAILABLE
    else:
        data = reassignment.data or {}
        records = data.get("records") or []
        receiving = sorted({r.get("RCV_BNFT_ENRLMT_ID") for r in records if r.get("RCV_BNFT_ENRLMT_ID")})
        if not records and data.get("inconclusive"):
            # A partial snapshot cannot support "no reassignment exists".
            corroboration = Disposition.INSUFFICIENT_EVIDENCE
            note = data.get("inconclusive_reason")
        elif not records:
            # "RCE relationship exists + no PECOS reassignment" — never a FAIL.
            corroboration = (Disposition.REVIEW if profile.medicare_relevance == "LIKELY"
                             else Disposition.NOT_APPLICABLE)
            note = ("No Medicare reassignment found. Under the reassignment rules this is "
                    "NOT_APPLICABLE or REVIEW depending on Medicare applicability — never a failure.")
        else:
            corroboration = Disposition.CORROBORATED
            note = ("Medicare reassignment present. Corroborates the RCE relationship; it "
                    "does not replace it and is not turned into a score.")
        items.append(EvidenceItem.from_provenance(
            dimension=dim, source="CMS_PPEF_REASSIGNMENT", disposition=corroboration.value,
            provenance=data.get("provenance"),
            ppef_component=PPEFComponent.REASSIGNMENT.value,
            fields_evaluated=["REASGN_BNFT_ENRLMT_ID", "RCV_BNFT_ENRLMT_ID"],
            original_values={"records": records, "receiving_enrollment_ids": receiving},
            rule_applied="PECOS_REASSIGNMENT_CORROBORATIVE_ONLY",
            note=note,
        ))

    if not parent and applicability == Applicability.NOT_APPLICABLE.value:
        disposition, rationale, needs = (
            Disposition.NOT_APPLICABLE,
            "No ONC-expressed relationship and no Medicare relationship applicability.",
            False,
        )
    elif not parent:
        disposition, rationale, needs = (
            Disposition.NOT_FOUND,
            "ONC supplied no parent relationship for this entity.",
            False,
        )
    elif corroboration == Disposition.CORROBORATED:
        disposition, rationale, needs = (
            Disposition.PASS,
            "RCE relationship present and corroborated by Medicare reassignment.",
            False,
        )
    elif corroboration == Disposition.REVIEW:
        disposition, rationale, needs = (
            Disposition.REVIEW,
            ("RCE relationship present; Medicare reassignment absent for a Medicare-relevant "
             "provider. Presented to the analyst — never an automatic failure."),
            True,
        )
    else:
        disposition, rationale, needs = (
            Disposition.PASS,
            ("RCE relationship present. Medicare corroboration is "
             f"{corroboration.value.lower()} and cannot affect the determination."),
            corroboration == Disposition.UNAVAILABLE,
        )

    return DimensionResult(dimension=dim, disposition=disposition.value,
                           applicability=applicability, rationale=rationale,
                           items=items, requires_analyst=needs)


def relationship_conflict_review(rce_parent_name: Optional[str],
                                 pecos_receiving_names: List[str]) -> Dict[str, Any]:
    """RCE and PECOS naming different organisations → REVIEW, with all of them shown.

    A practitioner may legitimately hold several relationships at once. The
    reviewer gets the full set; the system does not pick one and call the others
    wrong.
    """
    if not rce_parent_name or not pecos_receiving_names:
        return {"result": "NOT_EVALUATED", "organizations": pecos_receiving_names or []}
    normalized = {n.strip().upper() for n in pecos_receiving_names if n}
    if rce_parent_name.strip().upper() in normalized:
        return {"result": "CORROBORATED", "organizations": sorted(normalized)}
    return {
        "result": "REVIEW",
        "organizations": sorted(normalized),
        "rce_organization": rce_parent_name,
        "note": ("RCE and PECOS name different organisations. A practitioner may hold "
                 "multiple legitimate relationships — all are presented to the analyst, "
                 "and none is treated as a conflict or a failure by the system."),
    }


# ── Public entry point ───────────────────────────────────────────────────────

def assemble_dimensions(
    entity: Dict[str, Any],
    profile: ApplicabilityProfile,
    sources: Dict[str, SourceResult],
    website_evidence: Optional[Dict[str, Any]] = None,
    parent_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> List[DimensionResult]:
    """Build all six dimensions, in review reading order.

    `parent_resolver` maps an RCE parent reference (a TEFCAID, HCID or record id)
    to the parent entity, or None when it does not resolve. Optional: with no
    resolver, D5 and D6 report that a parent reference is PRESENT but that
    resolution was not attempted — which is a different and more honest state
    than reporting it unresolved.
    """
    return [
        _dimension_identity(entity, profile, sources),
        _dimension_medicare(profile, sources),
        _dimension_exclusion(entity, profile, sources),
        _dimension_address(entity, profile, sources, website_evidence),
        _dimension_tefca(entity, profile, parent_resolver),
        _dimension_relationship(entity, profile, sources, parent_resolver),
    ]
