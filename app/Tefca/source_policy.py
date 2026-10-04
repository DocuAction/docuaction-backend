"""Versioned source/check policy registry — OFFICIAL vs. PROPOSED-SHADOW.

WHY THIS EXISTS (Round 23, Part B §2)
───────────────────────────────────────
Individual connectors (connectors.py) already version THEMSELVES
(`API_VERSION` per class) and individual pipeline runs already version
RULES (`quality_rules.RULE_SET_VERSION`, `field_map.FIELD_MAP_VERSION`) and
even individual DATA snapshots (`scripts/phase6_population_enrichment.py`'s
`source_version_snapshots` table: version_label/source_as_of/
source_file_hash). None of that is a POLICY: nothing records who approved
treating a given schema/mapping/identity-method/freshness-window as the
one this registry is allowed to rely on for a live classification
decision — and per `qa-evidence/PROJECT_CONTRACT_CONTEXT.md`, no COR
acceptance of any such policy has happened. This module is that missing
layer, and it is honest about not having one yet.

THE CENTRAL RULE
─────────────────
No source's policy is APPROVED today. Every `official_view()` therefore
reports `approval_status=POLICY_UNAPPROVED` and `freshness=FRESHNESS_UNKNOWN`
— ALWAYS, regardless of how recent the evidence's own `as_of` date is.
Source publication cadence is a fact about the SOURCE, never by itself an
approved freshness deadline; computing "CURRENT" from cadence alone here
would be exactly the thing this module exists to prevent.

A SEPARATE `proposed_view()` carries concrete, reviewable proposed values
(freshness window, blocking scope, reason codes, rationale) against the
SAME pinned evidence (`as_of`, `retrieved_at`, `verified_at` all pass
through unchanged) -- but its `approval_status` is always
`PROPOSED_INACTIVE`, and nothing in this module, or any caller found in
this codebase, ever promotes a proposed value into the official path.
Promoting one is a human, COR-facing decision this module does not make.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Approval status (the thing this module adds; distinct from any
#    source's own API_VERSION or any rule-set's own version string) ────────
POLICY_UNAPPROVED = "POLICY_UNAPPROVED"
POLICY_APPROVED = "POLICY_APPROVED"
PROPOSED_INACTIVE = "PROPOSED_INACTIVE"
APPROVAL_STATUSES = (POLICY_UNAPPROVED, POLICY_APPROVED, PROPOSED_INACTIVE)

# ── Freshness (reused shape, not a new vocabulary invented for this module:
#    CURRENT/STALE/UNKNOWN is this task's own conceptual target; this is the
#    one dimension Part A's delta (docs/review/DELTA-2026-10-04.md §5)
#    named as a genuine gap with no existing equivalent) ────────────────────
FRESHNESS_CURRENT = "CURRENT"
FRESHNESS_STALE = "STALE"
FRESHNESS_UNKNOWN = "UNKNOWN"
FRESHNESS_STATES = (FRESHNESS_CURRENT, FRESHNESS_STALE, FRESHNESS_UNKNOWN)


@dataclass(frozen=True)
class SourcePolicy:
    """One version of one source's policy — schema, mapping, identity
    method, dependencies, applicability authority, publication cadence,
    freshness window, blocking scope, reason codes, effective date,
    approval provenance. A single, auditable record, not scattered
    constants."""
    source_id: str
    schema_version: str
    mapping_version: str
    identity_method: str
    dependencies: List[str]
    applicability_authority: str
    publication_cadence: str
    freshness_window_days: Optional[int]  # None: this source has no cadence-derived window at all
    blocking_scope: str                   # what a failure of THIS source may ever block, in plain words
    reason_codes: List[str]
    effective_date: str                   # ISO date this policy VERSION would take effect, if approved
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


# ── OFFICIAL registry: every entry here is POLICY_UNAPPROVED today. ────────
# If a COR ever approves one, that entry moves here with approval_status=
# POLICY_APPROVED, approved_by/approved_at filled in, and the matching entry
# below is removed from PROPOSED_POLICIES (approval REPLACES the proposal,
# never adds a third, ambiguous state).

OFFICIAL_POLICIES: Dict[str, SourcePolicy] = {
    "NPPES_BULK": SourcePolicy(
        source_id="NPPES_BULK", schema_version="UNAPPROVED", mapping_version="UNAPPROVED",
        identity_method="UNAPPROVED", dependencies=[], applicability_authority="UNAPPROVED",
        publication_cadence="UNAPPROVED", freshness_window_days=None,
        blocking_scope="UNAPPROVED -- no official policy means no official blocking scope either",
        reason_codes=[], effective_date="UNAPPROVED", approval_status=POLICY_UNAPPROVED,
        rationale="No COR-reviewed policy exists for the NPPES Data Dissemination bulk "
                 "index (qa-evidence/PROJECT_CONTRACT_CONTEXT.md, Task 2/Deliverable 2 is "
                 "not yet accepted). See PROPOSED_POLICIES for the candidate version."),
    "PECOS_PROXY": SourcePolicy(
        source_id="PECOS_PROXY", schema_version="UNAPPROVED", mapping_version="UNAPPROVED",
        identity_method="UNAPPROVED", dependencies=[], applicability_authority="UNAPPROVED",
        publication_cadence="UNAPPROVED", freshness_window_days=None,
        blocking_scope="UNAPPROVED",
        reason_codes=[], effective_date="UNAPPROVED", approval_status=POLICY_UNAPPROVED,
        rationale="No COR-reviewed policy exists for treating the NPPES-backed PECOS "
                 "proxy (connectors.PECOS_BACKING='nppes_proxy') as an approved stand-in "
                 "for a real PECOS feed."),
    "USPS": SourcePolicy(
        source_id="USPS", schema_version="UNAPPROVED", mapping_version="UNAPPROVED",
        identity_method="UNAPPROVED", dependencies=[], applicability_authority="UNAPPROVED",
        publication_cadence="UNAPPROVED", freshness_window_days=None,
        blocking_scope="UNAPPROVED",
        reason_codes=[], effective_date="UNAPPROVED", approval_status=POLICY_UNAPPROVED,
        rationale="The signed USPS agreement and existing API access (usps_connector.py) "
                 "are retained and usable; no COR-reviewed policy exists for what a USPS "
                 "result is allowed to be USED FOR in a verification decision."),
    "IQVIA": SourcePolicy(
        source_id="IQVIA", schema_version="UNAPPROVED", mapping_version="UNAPPROVED",
        identity_method="UNAPPROVED", dependencies=[], applicability_authority="UNAPPROVED",
        publication_cadence="UNAPPROVED", freshness_window_days=None,
        blocking_scope="UNAPPROVED",
        reason_codes=[], effective_date="UNAPPROVED", approval_status=POLICY_UNAPPROVED,
        rationale="No COR-reviewed policy exists for IQVIA's delivered HCO/HCP/"
                 "affiliation facts as a verification source."),
}


# ── PROPOSED (shadow) registry: concrete candidate values, explicit
#    rationale, NEVER installed by this module or any caller. ──────────────

PROPOSED_POLICIES: Dict[str, SourcePolicy] = {
    "NPPES_BULK": SourcePolicy(
        source_id="NPPES_BULK", schema_version="NPPES-Data-Dissemination-2026",
        # "Pin NPPES bulk mappings to documented V2" -- this task's own
        # instruction, recorded here as a PROPOSED value, not installed.
        mapping_version="V2",
        identity_method="NPI exact match against the bulk index "
                        "(scripts/phase6_population_enrichment.py's nppes_index.json)",
        dependencies=["nppes_index.json build must complete before any lookup"],
        applicability_authority="CMS NPPES Data Dissemination File publication notice",
        publication_cadence="monthly (CMS's documented publication cycle)",
        freshness_window_days=35,  # one publication cycle + slack -- a PROPOSED number
        blocking_scope="If stale past the window: downgrade this source's own evidence "
                       "to UNAVAILABLE for freshness reasons -- never blocks an unrelated "
                       "source's result, and never blocks delivery intake.",
        reason_codes=["NPPES_BULK_STALE", "NPPES_BULK_SCHEMA_MISMATCH", "NPPES_BULK_UNAVAILABLE"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Candidate policy for COR review. Pins the bulk mapping version "
                 "explicitly (V2) so a future silent CMS schema change is detected as "
                 "NPPES_BULK_SCHEMA_MISMATCH rather than silently mis-read."),
    "PECOS_PROXY": SourcePolicy(
        source_id="PECOS_PROXY", schema_version="NPPES-proxy-v2.1",
        mapping_version="PROXY_NOT_PECOS",
        identity_method="Proxies the NPPES live API (connectors.NPPESConnector, "
                        "API_VERSION='2.1') under the label connectors.PECOS_BACKING="
                        "'nppes_proxy' -- the SAME physical connection as NPPES, not an "
                        "independent PECOS feed.",
        dependencies=["NPPESConnector reachability"],
        applicability_authority="connectors.PECOS_BACKING_NOTE (already in code, cited "
                                "here rather than restated)",
        publication_cadence="follows NPPES's own cadence; PECOS proper has no cadence "
                           "here because it is not connected",
        freshness_window_days=None,  # deliberately unset: proxy data inherits NPPES's state, not its own
        blocking_scope="Never blocks on PECOS-specific grounds -- there is no PECOS "
                       "connection to be stale or unavailable independent of NPPES.",
        reason_codes=["PECOS_PROXY_IS_NOT_PECOS"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Makes the existing NPPES-proxy distinction (already enforced in "
                 "connectors.py and its UI label) a reviewable POLICY fact, not just a "
                 "code comment -- 'distinguish PECOS datasets' per this task."),
    "USPS": SourcePolicy(
        source_id="USPS", schema_version="USPS-ShippingAPI-AddressValidate",
        mapping_version="address-fields-only",
        identity_method="NONE -- USPS address validation establishes a MAILING ADDRESS "
                        "FORMAT, never a provider's identity and never physical occupancy "
                        "of that address. A caller that reads a USPS PASS as identity or "
                        "occupancy evidence is misusing this source.",
        dependencies=["USPS_API_USER_ID env var (optional; falls back to code-only "
                     "normalization without it, per usps_connector.py's own docstring)"],
        applicability_authority="The signed USPS agreement and existing API access "
                                "(retained, per this task's own instruction) -- scope is "
                                "address format validation ONLY, not identity or "
                                "occupancy verification; that broader scope was never "
                                "granted and is not assumed here.",
        publication_cadence="N/A -- a live validation call, not a periodic dataset",
        freshness_window_days=None,
        blocking_scope="Never blocks identity or occupancy determinations -- it has no "
                       "evidentiary standing for either.",
        reason_codes=["USPS_FORMAT_ONLY_NOT_IDENTITY_EVIDENCE"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Explicit permitted-scope statement per this task's instruction: "
                 "'do not assume credentials establish all uses... postal validation "
                 "does not prove provider identity or occupancy.'",
        extra={"proves_identity": False, "proves_occupancy": False}),
    "IQVIA": SourcePolicy(
        source_id="IQVIA", schema_version="IQVIA-HCO-HCP-Affiliation-CSV",
        mapping_version="delivered-facts-only",
        identity_method="Delivered HCO/HCP facts are usable as-is (staged, matched per "
                        "source_matching.py). The affiliation-relationship JOURNEY "
                        "(organization-to-practitioner hierarchy inference) is a "
                        "SEPARATE, UNSUPPORTED capability -- relationship data, not the "
                        "delivered facts themselves, is what is absent.",
        dependencies=["ENABLE_IQVIA_SOURCES flag", "a staged, approved snapshot"],
        applicability_authority="This task's own instruction: 'IQVIA remains usable for "
                                "supported delivered facts. Only the absent affiliation "
                                "journey is UNSUPPORTED.'",
        publication_cadence="per-delivery (operator-staged extracts, not a periodic feed)",
        freshness_window_days=None,
        blocking_scope="The affiliation-journey gap never blocks the delivered HCO/HCP "
                       "facts themselves from being usable.",
        reason_codes=["IQVIA_AFFILIATION_JOURNEY_UNSUPPORTED"],
        effective_date="2026-10-04", approval_status=PROPOSED_INACTIVE,
        rationale="Names the specific unsupported capability so a caller cannot read "
                 "'IQVIA is usable' as 'the affiliation journey works' -- the exact "
                 "fabrication this task's instruction forbids."),
}


def _pinned_evidence(as_of: Optional[str], retrieved_at: Optional[str],
                     verified_at: Optional[str]) -> Dict[str, Optional[str]]:
    """The three timestamps this module tracks separately, per this task's
    own instruction, never collapsed into one 'checked_at' value."""
    return {"as_of": as_of, "retrieved_at": retrieved_at, "verified_at": verified_at}


def official_view(source_id: str, *, as_of: Optional[str] = None,
                  retrieved_at: Optional[str] = None,
                  verified_at: Optional[str] = None) -> Dict[str, Any]:
    """The view a live verification decision is allowed to read.

    ALWAYS POLICY_UNAPPROVED / FRESHNESS_UNKNOWN for every source in
    OFFICIAL_POLICIES today -- this is not computed from `as_of`, on
    purpose: a recent `as_of` date is a fact about the SOURCE, and this
    function never promotes that fact into an approved freshness
    determination no human has signed off on.
    """
    if source_id not in OFFICIAL_POLICIES:
        raise KeyError(f"no official policy entry for source_id={source_id!r}")
    policy = OFFICIAL_POLICIES[source_id]
    freshness = (FRESHNESS_UNKNOWN if policy.approval_status != POLICY_APPROVED
                else _compute_freshness(policy, as_of))
    return {
        "source_id": source_id, "view": "official",
        "approval_status": policy.approval_status,
        "freshness": freshness,
        "policy": policy,
        "evidence": _pinned_evidence(as_of, retrieved_at, verified_at),
    }


def proposed_view(source_id: str, *, as_of: Optional[str] = None,
                  retrieved_at: Optional[str] = None,
                  verified_at: Optional[str] = None) -> Dict[str, Any]:
    """The shadow view: concrete proposed values against the SAME pinned
    evidence as `official_view` for the same call -- never read by a live
    decision path, only for side-by-side reporting/review."""
    if source_id not in PROPOSED_POLICIES:
        raise KeyError(f"no proposed policy entry for source_id={source_id!r}")
    policy = PROPOSED_POLICIES[source_id]
    freshness = _compute_freshness(policy, as_of)  # computed for REVIEW visibility only
    return {
        "source_id": source_id, "view": "proposed",
        "approval_status": policy.approval_status,  # always PROPOSED_INACTIVE
        "freshness": freshness,
        "policy": policy,
        "evidence": _pinned_evidence(as_of, retrieved_at, verified_at),
    }


def _compute_freshness(policy: SourcePolicy, as_of: Optional[str]) -> str:
    """Only ever called for an APPROVED policy or for a PROPOSED policy's
    own shadow display -- never contributes to an official decision for an
    unapproved source (see official_view's early return above)."""
    if policy.freshness_window_days is None or not as_of:
        return FRESHNESS_UNKNOWN
    try:
        as_of_dt = datetime.fromisoformat(as_of)
        if as_of_dt.tzinfo is None:
            as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return FRESHNESS_UNKNOWN
    age_days = (datetime.now(timezone.utc) - as_of_dt).days
    return FRESHNESS_STALE if age_days > policy.freshness_window_days else FRESHNESS_CURRENT


def both_views(source_id: str, *, as_of: Optional[str] = None,
              retrieved_at: Optional[str] = None,
              verified_at: Optional[str] = None) -> Dict[str, Any]:
    """Official and proposed views against IDENTICAL pinned evidence, for a
    reviewer to compare side by side -- this task's own instruction:
    'Produce both views against identical pinned evidence.'"""
    return {
        "official": official_view(source_id, as_of=as_of, retrieved_at=retrieved_at,
                                  verified_at=verified_at),
        "proposed": proposed_view(source_id, as_of=as_of, retrieved_at=retrieved_at,
                                  verified_at=verified_at),
    }
