"""Versioned source/check policy registry -- OFFICIAL vs. PROPOSED-SHADOW.

WHY THIS EXISTS
───────────────
Connectors version themselves (`API_VERSION`), rule sets version themselves
(`RULE_SET_VERSION`, `FIELD_MAP_VERSION`) and ingested data is versioned per
snapshot. None of that is a POLICY: nothing records who approved relying on
a given schema / mapping / identity method / freshness window for a
classification decision, and per `qa-evidence/PROJECT_CONTRACT_CONTEXT.md`
(Task 2 / Deliverable 2 not yet accepted) no such approval exists.

THE CENTRAL RULE
────────────────
No source's policy is approved. Every `official_view()` therefore reports
`POLICY_UNAPPROVED` and `freshness = UNKNOWN`, whatever the evidence's own
`as_of` date. Publication cadence is a fact about the SOURCE; it is never,
by itself, an approved freshness deadline.

THIS MODULE NEVER CHANGES AN OUTCOME
────────────────────────────────────
`POLICY_UNAPPROVED` is a statement about the POLICY, recorded BESIDE an
evidence item. It does not overwrite, downgrade or re-open any existing
official disposition, bucket or review -- `annotate_evidence` adds one
`source_policy` block and touches nothing else (asserted by test). The
existing connectors remain the only thing that decides whether a source
answered.

A separate `proposed_view()` carries concrete candidate values against the
SAME pinned evidence. It is always `PROPOSED_INACTIVE`; nothing here, and no
caller in this codebase, promotes a proposed value into the official path.

THREE TIMESTAMPS, NEVER ONE
───────────────────────────
    as_of         what date the SOURCE says its data describes / was published
    retrieved_at  when this system fetched it
    verified_at   when the verification that used it ran
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

POLICY_UNAPPROVED = "POLICY_UNAPPROVED"
POLICY_APPROVED = "POLICY_APPROVED"
PROPOSED_INACTIVE = "PROPOSED_INACTIVE"
APPROVAL_STATUSES = (POLICY_UNAPPROVED, POLICY_APPROVED, PROPOSED_INACTIVE)

FRESHNESS_CURRENT = "CURRENT"
FRESHNESS_STALE = "STALE"
FRESHNESS_UNKNOWN = "UNKNOWN"
FRESHNESS_STATES = (FRESHNESS_CURRENT, FRESHNESS_STALE, FRESHNESS_UNKNOWN)

POLICY_REGISTRY_VERSION = "2026-10-04.2"

# Source ids. The Registry API and the Data Dissemination FILE are two
# different NPPES products with different schemas, cadences and identity
# methods; one id for both would let a policy approved for one silently
# cover the other.
NPPES_REGISTRY_API = "NPPES_REGISTRY_API"
NPPES_DISSEMINATION_FILE = "NPPES_DISSEMINATION_FILE"
PECOS_PROXY = "PECOS_PROXY"
CMS_PPEF = "CMS_PPEF"
CMS_REVOCATION = "CMS_REVOCATION"
OIG_LEIE = "OIG_LEIE"
SAM_GOV = "SAM_GOV"
USPS = "USPS"
IQVIA = "IQVIA"
#: Not a verification source: the retention rule for archived snapshots,
#: supplements and raw payloads. Registered here so its absence of authority
#: is recorded in the same place and the same vocabulary.
EVIDENCE_RETENTION = "EVIDENCE_RETENTION"

ALL_SOURCE_IDS = (NPPES_REGISTRY_API, NPPES_DISSEMINATION_FILE, PECOS_PROXY, CMS_PPEF,
                  CMS_REVOCATION, OIG_LEIE, SAM_GOV, USPS, IQVIA, EVIDENCE_RETENTION)

#: Evidence-item `source` literal -> policy id.
EVIDENCE_SOURCE_TO_POLICY = {
    "NPPES": NPPES_REGISTRY_API,
    "PECOS": PECOS_PROXY,
    "CMS_PPEF_ENROLLMENT": CMS_PPEF,
    "CMS_PPEF_PRACTICE_LOCATION": CMS_PPEF,
    "CMS_PPEF_REASSIGNMENT": CMS_PPEF,
    "CMS_REVOCATION": CMS_REVOCATION,
    "OIG_LEIE": OIG_LEIE,
    "SAM_GOV": SAM_GOV,
    "USPS": USPS,
    "IQVIA_HCO": IQVIA, "IQVIA_HCP": IQVIA, "IQVIA_AFFILIATION": IQVIA,
}
#: The manual path's source keys (`review_service.probe_sources`).
MANUAL_SOURCE_TO_POLICY = {"nppes": NPPES_REGISTRY_API, "pecos": PECOS_PROXY,
                           "oig_leie": OIG_LEIE, "sam_gov": SAM_GOV,
                           "cms_revocation": CMS_REVOCATION}


@dataclass(frozen=True)
class SourcePolicy:
    """One version of one source's policy."""
    source_id: str
    schema_version: str
    mapping_version: str
    identity_method: str
    dependencies: List[str]
    applicability_authority: str
    publication_cadence: str
    freshness_window_days: Optional[int]
    blocking_scope: str
    reason_codes: List[str]
    effective_date: str
    approval_status: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    rationale: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.approval_status not in APPROVAL_STATUSES:
            raise ValueError(f"approval_status must be one of {APPROVAL_STATUSES}, "
                             f"got {self.approval_status!r}")
        if self.approval_status == POLICY_APPROVED and not (self.approved_by and self.approved_at):
            raise ValueError(f"{self.source_id}: POLICY_APPROVED requires approved_by "
                             f"and approved_at -- approval provenance is not optional")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id, "schema_version": self.schema_version,
            "mapping_version": self.mapping_version, "identity_method": self.identity_method,
            "dependencies": list(self.dependencies),
            "applicability_authority": self.applicability_authority,
            "publication_cadence": self.publication_cadence,
            "freshness_window_days": self.freshness_window_days,
            "blocking_scope": self.blocking_scope, "reason_codes": list(self.reason_codes),
            "effective_date": self.effective_date, "approval_status": self.approval_status,
            "approved_by": self.approved_by, "approved_at": self.approved_at,
            "rationale": self.rationale, "extra": dict(self.extra),
        }


def _unapproved(source_id: str, rationale: str) -> SourcePolicy:
    return SourcePolicy(
        source_id=source_id, schema_version="UNAPPROVED", mapping_version="UNAPPROVED",
        identity_method="UNAPPROVED", dependencies=[], applicability_authority="UNAPPROVED",
        publication_cadence="UNAPPROVED", freshness_window_days=None,
        blocking_scope="UNAPPROVED -- no official policy means no official blocking scope",
        reason_codes=[], effective_date="UNAPPROVED", approval_status=POLICY_UNAPPROVED,
        rationale=rationale)


_NO_COR = ("No COR-reviewed policy exists (qa-evidence/PROJECT_CONTRACT_CONTEXT.md: "
           "Task 2 / Deliverable 2 not yet accepted). ")

OFFICIAL_POLICIES: Dict[str, SourcePolicy] = {
    NPPES_REGISTRY_API: _unapproved(NPPES_REGISTRY_API, _NO_COR + "Covers the live per-NPI "
                                    "Registry API only."),
    NPPES_DISSEMINATION_FILE: _unapproved(NPPES_DISSEMINATION_FILE, _NO_COR + "Covers the "
                                          "Data Dissemination FILE only; no in-app loader "
                                          "for it exists or is introduced."),
    PECOS_PROXY: _unapproved(PECOS_PROXY, _NO_COR + "The NPPES-backed PECOS proxy is not an "
                             "approved stand-in for a PECOS feed."),
    CMS_PPEF: _unapproved(CMS_PPEF, _NO_COR + "Covers the CMS Public Provider Enrollment "
                          "extracts."),
    CMS_REVOCATION: _unapproved(CMS_REVOCATION, _NO_COR + "Covers the CMS revocation extract."),
    OIG_LEIE: _unapproved(OIG_LEIE, _NO_COR + "Covers the OIG LEIE downloadable list."),
    SAM_GOV: _unapproved(SAM_GOV, _NO_COR + "Covers SAM.gov entity registration (v3) and "
                         "exclusions (v4)."),
    USPS: _unapproved(USPS, _NO_COR + "The signed USPS agreement and API access are "
                      "retained; what a USPS result may be USED FOR is not approved."),
    IQVIA: _unapproved(IQVIA, _NO_COR + "Covers delivered IQVIA HCO/HCP/affiliation facts."),
    EVIDENCE_RETENTION: _unapproved(
        EVIDENCE_RETENTION,
        "No approved retention rule exists for archived source snapshots, supplements or "
        "raw payloads. Until one does: existing evidence is preserved, NOTHING is deleted "
        "automatically, and indefinite retention of every payload is NOT thereby approved "
        "either -- that is the decision still required (see PROPOSED_POLICIES)."),
}

PROPOSED_POLICIES: Dict[str, SourcePolicy] = {
    NPPES_REGISTRY_API: SourcePolicy(
        source_id=NPPES_REGISTRY_API, schema_version="npiregistry-api/2.1",
        mapping_version="connectors.NPPESConnector._shape",
        identity_method="Exact NPI (Luhn-valid, 10 digits). A missing NPI is a fact about "
                        "the entity, never a finding and never a reason to skip name-keyed "
                        "exclusion screening.",
        dependencies=["npi_validator.npi_rejection_reason gate"],
        applicability_authority="45 CFR 162.408 (NPI standard); entity type decides whether "
                                "an NPI is expected at all",
        publication_cadence="live API over a registry updated daily by CMS",
        freshness_window_days=1,
        blocking_scope="A stale/unavailable answer makes NPPES-keyed identity UNAVAILABLE "
                       "for that entity only; exclusion screening still runs.",
        reason_codes=["NPPES_API_UNAVAILABLE", "NPPES_API_REQUEST_ERROR_BODY",
                      "NPPES_API_STALE"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Candidate. A live lookup's evidence ages from its retrieval time; one "
                  "day matches the registry's own update cycle."),
    NPPES_DISSEMINATION_FILE: SourcePolicy(
        source_id=NPPES_DISSEMINATION_FILE,
        schema_version="NPPES Data Dissemination monthly file",
        mapping_version="V2",
        identity_method="Exact NPI against the offline index built by "
                        "scripts/phase6_population_enrichment.py. Offline analysis only; "
                        "the application has no loader for this file.",
        dependencies=["offline index build completes before any lookup"],
        applicability_authority="CMS Data Dissemination file publication notice / V2 "
                                "file layout documentation",
        publication_cadence="monthly full file (weekly incrementals exist and are not used)",
        freshness_window_days=35,
        blocking_scope="Stale past the window: this file's own evidence is UNAVAILABLE for "
                       "freshness. Never blocks another source or delivery intake.",
        reason_codes=["NPPES_FILE_STALE", "NPPES_FILE_SCHEMA_NOT_V2", "NPPES_FILE_UNAVAILABLE"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Candidate. Pins the mapping to the documented V2 layout so a layout "
                  "change is detected as NPPES_FILE_SCHEMA_NOT_V2 rather than mis-read."),
    PECOS_PROXY: SourcePolicy(
        source_id=PECOS_PROXY, schema_version="npiregistry-api/2.1 (proxy)",
        mapping_version="PROXY_NOT_PECOS",
        identity_method="Derived from the NPPES Registry answer (connectors.PECOS_BACKING = "
                        "'nppes_proxy'). It is the same observation as NPPES, not an "
                        "independent PECOS record, and carries no enrolment status.",
        dependencies=["NPPES Registry API answer"],
        applicability_authority="connectors.PECOS_BACKING_NOTE",
        publication_cadence="inherits NPPES; PECOS proper is not connected",
        freshness_window_days=None,
        blocking_scope="Never blocks on PECOS-specific grounds. Medicare enrolment is "
                       "answered by CMS_PPEF, a different dataset.",
        reason_codes=["PECOS_PROXY_IS_NOT_PECOS"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Candidate. Makes the proxy/PPEF distinction a reviewable policy fact."),
    CMS_PPEF: SourcePolicy(
        source_id=CMS_PPEF, schema_version="ppef_ingest.validate_schema (per component)",
        mapping_version="ppef_ingest expected-column registry",
        identity_method="Exact NPI -> ENRLMT_ID; relational components join on ENRLMT_ID.",
        dependencies=["a validated, ingested quarterly snapshot (as_of_label)"],
        applicability_authority="CMS Public Provider Enrollment data dictionary; applies "
                                "only to entity types that enrol in Medicare",
        publication_cadence="quarterly",
        freshness_window_days=120,
        blocking_scope="Stale/unavailable: MEDICARE_ENROLLMENT evidence is UNAVAILABLE. "
                       "Never a finding against the entity.",
        reason_codes=["PPEF_SNAPSHOT_STALE", "PPEF_SCHEMA_MISMATCH", "PPEF_UNAVAILABLE"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Candidate. One quarter plus publication slack."),
    CMS_REVOCATION: SourcePolicy(
        source_id=CMS_REVOCATION, schema_version="CMS revocation extract (AMENDMENT_1)",
        mapping_version="evidence_assembly AMENDMENT_1_REVOCATION_SEMANTICS",
        identity_method="Exact NPI / ENRLMT_ID. A match is a POTENTIAL revocation pending "
                        "identity matching and analyst evaluation.",
        dependencies=["extract reachable"],
        applicability_authority="CMS revocation publication",
        publication_cadence="periodic CMS publication",
        freshness_window_days=120,
        blocking_scope="Unavailable: the revocation control is UNAVAILABLE, never clear.",
        reason_codes=["REVOCATION_UNAVAILABLE", "REVOCATION_STALE"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Candidate, aligned with CMS_PPEF."),
    OIG_LEIE: SourcePolicy(
        source_id=OIG_LEIE,
        schema_version="oig-leie-updated-csv/required-columns-v1",
        mapping_version="connectors.LEIE_REQUIRED_COLUMNS",
        identity_method="(1) Exact NPI where the list row carries one. (2) Organisation / "
                        "individual NAME: exact, plus deterministic normalization "
                        "(connectors.normalize_org_name -- punctuation, whitespace, "
                        "corporate designators; no similarity score, no threshold). A name "
                        "match is a POTENTIAL_HIT candidate only: it never confirms, clears, "
                        "auto-closes or passes. EIN/TIN/SSN are unavailable and not used.",
        dependencies=["list download passes leie_schema_problem (HTTP 200 is not proof)"],
        applicability_authority="42 U.S.C. 1320a-7 (OIG exclusion authority); screening "
                                "attaches to the organisation, so it applies with or "
                                "without an NPI",
        publication_cadence="monthly list with monthly supplements",
        freshness_window_days=35,
        blocking_scope="Stale/unavailable: exclusion screening is UNAVAILABLE for affected "
                       "entities -- never a clearance. Independent of every unrelated hold.",
        reason_codes=["LEIE_LIST_STALE", "LEIE_LIST_REFUSED_SCHEMA", "LEIE_UNAVAILABLE",
                      "LEIE_POTENTIAL_HIT_NAME", "LEIE_NO_CANDIDATE_IN_LIST"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Candidate. A clean screen is reported as 'no candidate found in list X "
                  "using methods Y'. Reinstatement requires the list's own REINDATE or "
                  "equivalent authoritative evidence plus review; disappearance from a "
                  "later list, or elapsed time, clears nothing.",
        extra={"preserved_fields": ["exclusion_type", "exclusion_date",
                                    "reinstatement_date", "state"]}),
    SAM_GOV: SourcePolicy(
        source_id=SAM_GOV,
        schema_version="entity-information v3 + exclusions v4",
        mapping_version="connectors._sam_structural_check keys",
        identity_method="(1) UEI exact (CAGE where delivered). (2) Legal-name search when "
                        "no UEI: a single name result is a candidate, more than one is "
                        "AMBIGUOUS and never resolved by guessing. Registration (v3) and "
                        "exclusions (v4) are independent legs; one never implies the other. "
                        "EIN/TIN/SSN are unavailable and not used.",
        dependencies=["SAM_GOV_API_KEY", "per-key daily quota (HTTP 429)"],
        applicability_authority="2 CFR Part 180 / FAR 9.4. SAM exclusions differ by type "
                                "and program; one legal effect is NOT assumed for all of "
                                "them -- type, program and dates are preserved for the "
                                "adjudicator.",
        publication_cadence="live API over a continuously updated system",
        freshness_window_days=1,
        blocking_scope="Either leg unavailable: debarment status is UNKNOWN for that "
                       "entity, never clear. Independent of every unrelated hold.",
        reason_codes=["SAM_UNAVAILABLE", "SAM_RATE_LIMITED", "SAM_ERROR_BODY",
                      "SAM_IDENTITY_AMBIGUOUS", "SAM_POTENTIAL_HIT",
                      "SAM_EXCLUSION_LEG_NOT_COMPLETED"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Candidate. A live lookup's evidence ages from retrieval.",
        extra={"preserved_fields": ["exclusion name", "type", "excluding agency",
                                    "active dates"]}),
    USPS: SourcePolicy(
        source_id=USPS, schema_version="USPS address validation API",
        mapping_version="address-fields-only",
        identity_method="NONE. Validates a mailing address FORMAT. It does not establish a "
                        "provider's identity and does not establish physical occupancy.",
        dependencies=["USPS_API_USER_ID (optional; code-only normalization without it)",
                      "daily request budget"],
        applicability_authority="The existing signed USPS agreement and API access are "
                                "retained. Verified permitted scope recorded here: address "
                                "standardisation/validation. Holding credentials does not "
                                "establish any wider permitted use, and none is assumed.",
        publication_cadence="live call; no dataset",
        freshness_window_days=None,
        blocking_scope="Never blocks an identity or occupancy determination.",
        reason_codes=["USPS_FORMAT_ONLY_NOT_IDENTITY_EVIDENCE"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Candidate. States the limit of what a postal answer proves.",
        extra={"proves_identity": False, "proves_occupancy": False}),
    IQVIA: SourcePolicy(
        source_id=IQVIA, schema_version="reference_preflight.REFERENCE_SCHEMAS",
        mapping_version="delivered-facts-only",
        identity_method="Delivered HCO/HCP facts are usable (exact NPI is the only "
                        "automatic match). The organisation-to-practitioner AFFILIATION "
                        "journey is UNSUPPORTED: no affiliation data was delivered, and "
                        "none is inferred or fabricated.",
        dependencies=["ENABLE_IQVIA_SOURCES", "an approved snapshot",
                      "reference preflight gate not BLOCKED"],
        applicability_authority="Licensed extract as delivered; scope is the delivered "
                                "facts only",
        publication_cadence="per delivery",
        freshness_window_days=None,
        blocking_scope="The unsupported affiliation journey never blocks the delivered "
                       "HCO/HCP facts.",
        reason_codes=["IQVIA_AFFILIATION_JOURNEY_UNSUPPORTED", "IQVIA_SNAPSHOT_BLOCKED"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Candidate. Names the one unsupported capability precisely."),
    EVIDENCE_RETENTION: SourcePolicy(
        source_id=EVIDENCE_RETENTION, schema_version="n/a", mapping_version="n/a",
        identity_method="n/a",
        dependencies=["a retention schedule approved by the contracting authority"],
        applicability_authority="REQUIRED AND NOT YET IDENTIFIED: the contract's records "
                                "clause / applicable NARA schedule, confirmed with the COR. "
                                "This proposal cites none because none has been read.",
        publication_cadence="n/a",
        freshness_window_days=None,
        blocking_scope="None. Until authority exists nothing is deleted and nothing is "
                       "declared permanently retained.",
        reason_codes=["RETENTION_POLICY_UNAPPROVED"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Decision needed before operational activation: (1) which payloads are "
                  "records (hashes + provenance only, or full payloads); (2) retention "
                  "period per class; (3) who may dispose and how disposal is evidenced. "
                  "No automated deletion is introduced by this proposal.",
        extra={"automatic_deletion": False, "indefinite_retention_approved": False}),
}


def _pinned_evidence(as_of: Optional[str], retrieved_at: Optional[str],
                     verified_at: Optional[str]) -> Dict[str, Optional[str]]:
    return {"as_of": as_of, "retrieved_at": retrieved_at, "verified_at": verified_at}


def _compute_freshness(policy: SourcePolicy, reference_time: Optional[str]) -> str:
    if policy.freshness_window_days is None or not reference_time:
        return FRESHNESS_UNKNOWN
    try:
        ref = datetime.fromisoformat(str(reference_time).replace("Z", "+00:00"))
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
    except ValueError:
        return FRESHNESS_UNKNOWN
    age_days = (datetime.now(timezone.utc) - ref).total_seconds() / 86400
    return FRESHNESS_STALE if age_days > policy.freshness_window_days else FRESHNESS_CURRENT


def official_view(source_id: str, *, as_of: Optional[str] = None,
                  retrieved_at: Optional[str] = None,
                  verified_at: Optional[str] = None) -> Dict[str, Any]:
    """What a decision is allowed to rely on. POLICY_UNAPPROVED / UNKNOWN for
    every source today, by construction -- not derived from `as_of`."""
    if source_id not in OFFICIAL_POLICIES:
        raise KeyError(f"no official policy entry for source_id={source_id!r}")
    policy = OFFICIAL_POLICIES[source_id]
    freshness = (FRESHNESS_UNKNOWN if policy.approval_status != POLICY_APPROVED
                 else _compute_freshness(policy, as_of or retrieved_at))
    return {"source_id": source_id, "view": "official",
            "approval_status": policy.approval_status, "freshness": freshness,
            "policy": policy,
            "evidence": _pinned_evidence(as_of, retrieved_at, verified_at)}


def proposed_view(source_id: str, *, as_of: Optional[str] = None,
                  retrieved_at: Optional[str] = None,
                  verified_at: Optional[str] = None) -> Dict[str, Any]:
    """Shadow view: candidate values against the same pinned evidence.
    Freshness here is for REVIEW visibility only."""
    if source_id not in PROPOSED_POLICIES:
        raise KeyError(f"no proposed policy entry for source_id={source_id!r}")
    policy = PROPOSED_POLICIES[source_id]
    return {"source_id": source_id, "view": "proposed",
            "approval_status": policy.approval_status,
            "freshness": _compute_freshness(policy, as_of or retrieved_at),
            "policy": policy,
            "evidence": _pinned_evidence(as_of, retrieved_at, verified_at)}


def both_views(source_id: str, *, as_of: Optional[str] = None,
               retrieved_at: Optional[str] = None,
               verified_at: Optional[str] = None) -> Dict[str, Any]:
    kw = dict(as_of=as_of, retrieved_at=retrieved_at, verified_at=verified_at)
    return {"official": official_view(source_id, **kw),
            "proposed": proposed_view(source_id, **kw)}


def _summary(view: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe, compact form of a view for an evidence snapshot."""
    p: SourcePolicy = view["policy"]
    return {"approval_status": view["approval_status"], "freshness": view["freshness"],
            "schema_version": p.schema_version, "mapping_version": p.mapping_version,
            "freshness_window_days": p.freshness_window_days,
            "effective_date": p.effective_date}


def source_policy_block(observations: List[Dict[str, Any]], *,
                        verified_at: Optional[str]) -> Dict[str, Any]:
    """The `source_policy` block for one verification.

    `observations`: [{"policy_id", "evidence_source", "as_of", "retrieved_at"}].
    One entry per policy id that was actually used, each with BOTH views
    against the same three timestamps. Sources with no registered policy are
    listed, not dropped."""
    by_policy: Dict[str, Dict[str, Any]] = {}
    unregistered: List[str] = []
    for obs in observations:
        pid = obs.get("policy_id")
        if pid not in OFFICIAL_POLICIES:
            if obs.get("evidence_source"):
                unregistered.append(str(obs["evidence_source"]))
            continue
        entry = by_policy.setdefault(pid, {"evidence_sources": [], "as_of": None,
                                           "retrieved_at": None})
        src = obs.get("evidence_source")
        if src and src not in entry["evidence_sources"]:
            entry["evidence_sources"].append(src)
        entry["as_of"] = entry["as_of"] or obs.get("as_of")
        entry["retrieved_at"] = entry["retrieved_at"] or obs.get("retrieved_at")
    sources: Dict[str, Any] = {}
    for pid, entry in sorted(by_policy.items()):
        views = both_views(pid, as_of=entry["as_of"], retrieved_at=entry["retrieved_at"],
                           verified_at=verified_at)
        sources[pid] = {
            "evidence_sources": sorted(entry["evidence_sources"]),
            "evidence": views["official"]["evidence"],
            "official": _summary(views["official"]),
            "proposed_inactive": _summary(views["proposed"]),
        }
    return {
        "registry_version": POLICY_REGISTRY_VERSION,
        "sources": sources,
        "unregistered_sources": sorted(set(unregistered)),
        "note": ("Policy status is recorded beside the evidence and does not change any "
                 "disposition, bucket or review. No source policy is approved; official "
                 "freshness is UNKNOWN. Proposed values are INACTIVE and shown for review "
                 "only."),
    }


def annotate_evidence(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Add a `source_policy` block to assembled D1-D6 evidence, IN PLACE.

    Reads each evidence item's source, query timestamp and (where present)
    provenance as-of label. Writes exactly one new top-level key. No
    dimension, item or disposition is modified."""
    observations: List[Dict[str, Any]] = []
    for dimension in evidence.get("dimensions", []) or []:
        for item in dimension.get("evidence", []) or []:
            src = item.get("source")
            if not src:
                continue
            provenance = item.get("provenance") or {}
            observations.append({
                "policy_id": EVIDENCE_SOURCE_TO_POLICY.get(src), "evidence_source": src,
                "as_of": (provenance.get("as_of_label") or provenance.get("source_as_of")
                          if isinstance(provenance, dict) else None),
                "retrieved_at": item.get("query_timestamp"),
            })
    evidence["source_policy"] = source_policy_block(
        observations, verified_at=evidence.get("generated_at"))
    return evidence


def manual_sources_block(sources: Dict[str, Dict[str, Any]], *,
                         verified_at: Optional[str]) -> Dict[str, Any]:
    """The same block for the manual path's `sources` dict."""
    observations = []
    for key, info in (sources or {}).items():
        if not isinstance(info, dict):
            continue
        persisted = info.get("persisted_evidence") or {}
        observations.append({
            "policy_id": MANUAL_SOURCE_TO_POLICY.get(key), "evidence_source": key,
            "as_of": None,
            "retrieved_at": persisted.get("generation_timestamp") or info.get("verified_at"),
        })
    return source_policy_block(observations, verified_at=verified_at)


#: Names a person reads. The ids above are for code and audit rows.
SOURCE_LABELS: Dict[str, str] = {
    "NPPES_REGISTRY_API": "NPPES NPI Registry (live lookup)",
    "NPPES_DISSEMINATION_FILE": "NPPES monthly data file",
    "PECOS_PROXY": "Medicare enrollment (inferred from NPPES)",
    "CMS_PPEF": "Medicare enrollment (CMS provider enrollment file)",
    "CMS_REVOCATION": "CMS revocation list",
    "OIG_LEIE": "OIG exclusion list (LEIE)",
    "SAM_GOV": "SAM.gov exclusions",
    "USPS": "USPS address check",
    "IQVIA": "IQVIA reference data",
    "EVIDENCE_RETENTION": "Evidence retention",
}


def registry_dto() -> Dict[str, Any]:
    """Every source, both views, no evidence -- for the policy API/UI."""
    return {
        "registry_version": POLICY_REGISTRY_VERSION,
        "any_policy_approved": any(p.approval_status == POLICY_APPROVED
                                   for p in OFFICIAL_POLICIES.values()),
        "sources": [{
            "source_id": sid,
            "label": SOURCE_LABELS.get(sid, sid),
            "official": OFFICIAL_POLICIES[sid].as_dict(),
            "proposed_inactive": PROPOSED_POLICIES[sid].as_dict(),
        } for sid in ALL_SOURCE_IDS],
        "note": ("Official entries are POLICY_UNAPPROVED. Proposed entries are INACTIVE "
                 "candidates for review; nothing promotes them automatically."),
    }
