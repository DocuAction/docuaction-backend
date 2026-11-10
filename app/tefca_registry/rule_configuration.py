"""ONE governed, composable activation mechanism for proposed rule behavior.

WHY THIS EXISTS (Step 2C, 2026-11-08)
    The proposals accumulated as monolithic rule-set versions: #200's v4
    (RULE-006/007 together) and this branch's v5 (v4 + every guard). The
    owner's decisions do not arrive monolithically — D-01 may approve the B3
    review routing without the B4 mapping; D-03 may land without D-04. A
    mechanism that can only activate everything together forces decisions the
    methodology owner has not taken. This module supersedes `ensure_rules_v5`
    (removed) with named COMPONENTS, each tied to its own decision and
    approval reference; `ensure_rules_v4` and its audited admin route remain
    as the reviewed treatment-pair special case (activating exactly the
    components `treatment_review_b3` + `treatment_b4`), and this mechanism
    always supersedes whatever versions are active, so there is one version
    lineage and no competing mechanisms.

INACTIVE BY CONSTRUCTION
    Nothing calls `activate_rule_configuration`. It refuses any component
    without a VALIDATED approval reference: the reference must name an
    existing `tefca_audit_log` row of event type
    `methodology_approval_recorded` whose metadata names that component — an
    arbitrary nonempty string is not authorization, and recording that audit
    row is itself an explicit, admin-side act. Flag-backed components
    (producer behavior) are approved through the same mechanism; their env
    flags remain the execution switch and must only be set once the
    component's approval is recorded.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

APPROVAL_EVENT_TYPE = "methodology_approval_recorded"


class ActivationRefused(PermissionError):
    """A selection, dependency or approval reference failed validation."""


# ── the rule deltas ──────────────────────────────────────────────────────────
#
# Every delta is a pure List[dict] -> List[dict] over DEEP-COPIED rules; a
# delta may only ADD rules or ADD none_of guards / change the bucket of a rule
# it owns — positive clauses of baseline rules are never touched (asserted by
# tests).

_MATERIAL_CONFLICTS = [
    {"field": "name_mismatch", "severity": "major"},
    {"field": "address_mismatch", "severity": "major"},
]
_STRUCTURAL_INVALID = [{"field": "npi_validation", "status": "invalid"}]
_MANDATORY_FAILED = [{"source": "pecos", "status": "failed"},
                     {"source": "sam_gov", "status": "failed"}]
_PENDING_NOT_FOUND = [{"source": "sam_gov", "status": "not_found"},
                      {"source": "oig_leie", "status": "not_found"}]
_POSSIBLE_MATCH = [{"source": "sam_gov", "status": "possible_match"},
                   {"source": "oig_leie", "status": "possible_match"}]


def _add_none_of(rule: dict, items: List[dict]) -> None:
    """Append `items` to the rule's none_of guards, skipping clauses already
    present (order-insensitive comparison)."""
    existing = [tuple(sorted(c.items())) for c in rule["conditions"].get("none_of", [])
                if isinstance(c, dict)]
    for c in items:
        if tuple(sorted(c.items())) not in existing:
            rule["conditions"].setdefault("none_of", []).append(c)


def _delta_treatment_review_b3(rules: List[dict]) -> List[dict]:
    """D-01 interim per the working matrix: RULE-007 (B3 applicability review)
    plus RULE-006 mapped to B3 with the B4-candidate reason — Treatment B4 is
    NOT available from this component."""
    from app.tefca_registry.bucket_classifier import SEED_RULES_V4
    by_code = {r["rule_code"]: copy.deepcopy(r) for r in SEED_RULES_V4
               if r["rule_code"] in ("RULE-006", "RULE-007")}
    r6 = by_code["RULE-006"]
    r6["bucket"] = "B3"
    r6["name"] = ("B3 Treatment Missing Required NPI (B4 candidate; B4 mapping "
                  "pending its own approval)")
    return rules + [r6, by_code["RULE-007"]]


def _delta_treatment_b4(rules: List[dict]) -> List[dict]:
    """D-01 full: upgrades RULE-006 to B4. Depends on treatment_review_b3."""
    out = copy.deepcopy(rules)
    for r in out:
        if r["rule_code"] == "RULE-006":
            r["bucket"] = "B4"
            r["name"] = "B4 Active Treatment Missing Required NPI"
    return out


def _delta_treatment_b4_owner_adopted(rules: List[dict]) -> List[dict]:
    """OWNER-ADOPTED classification policy (superseding D-01 record,
    2026-11-08; the CONFIG A approval is preserved as history at commit
    1da7efe): active + exact T-TRTMNT token in any combination + blank
    SUBMITTED NPI -> B4, with NO node-type gate. Labelled the owner's adopted
    policy, not proof of an independently established SOP breach per record.
    Higher-priority approved conditions (RULE-005, priority 5) still win;
    HELD records are excluded upstream (D-15) and are never force-counted.
    Conflicts with the CONFIG-A components: one treatment regime at a time.

    THE RULE IS A NEW VERSION OF RULE-006 ITSELF (owner addendum, 2026-11-08:
    "RULE-006 must cover every active record whose purposesofuse contains
    exact T-TRTMNT and whose submitted NPI is blank"): rule_code stays
    RULE-006 so the versioned-rules design does what it exists for — the
    historical initiating-gated definition survives as the prior version
    (code: SEED_RULES_V4/_delta_treatment_review_b3; any activated rows are
    retired, never deleted). RULE-007 is NOT selected by this component: the
    owner rule at priority 6 covers every case-1 active record, leaving
    RULE-007's unresolved-applicability condition no independent coverage;
    its historical definition is retained, unselected."""
    out = copy.deepcopy(rules)
    out.append({
        "rule_code": "RULE-006",
        "name": "B4 Treatment Missing Required NPI (owner-adopted policy)",
        "bucket": "B4",
        "priority": 6,
        "description": (
            "Owner-adopted classification policy (superseding D-01, "
            "2026-11-08): an active record claiming the exact T-TRTMNT "
            "exchange purpose in any purpose combination with no SUBMITTED "
            "NPI classifies B4. organizationNodeType is preserved as "
            "information and does not gate this rule. A supplemental NPI "
            "(NPPES, IQVIA, vetting) never fills the submitted blank and "
            "never suppresses the condition. This records the owner's "
            "adopted policy, not an independently established SOP breach "
            "per record."),
        "conditions": {"all_of": [
            {"field": "treatment_owner_adopted_missing_npi", "status": True},
        ]},
    })
    return out


def _delta_treatment_b4_onc_scope(rules: List[dict]) -> List[dict]:
    """ONC UPDATED TREATMENT SCOPE (relayed 2026-11-09): a NEW VERSION of
    RULE-006 itself, superseding the owner-adopted any-combination definition
    (which survives as the prior version; activated rows are retired, never
    deleted). All conditions must hold IN THE PRODUCER (the rule consumes the
    single producer-emitted signal, so the rule can never widen the scope):

      - Purpose_of_Use contains the exact token T-TRTMNT as its SOLE purpose
        (combined-purpose values such as "T-PH-ECR,T-TRTMNT" are out of scope;
        T-TREAT is never a substitute);
      - participation level (sequoiaorgtype) is Participant or Sub-Participant;
      - the record is Active;
      - the ORIGINAL SUBMITTED NPI is blank.

    No initiating-node requirement. A record outside this scope is NOT routed
    anywhere by this component: it is evaluated against the remaining
    applicable rules exactly as any other record (never auto-B3). Higher-
    priority approved conditions (RULE-005, priority 5) still win; HELD
    records are excluded upstream (D-15). Supplemental NPIs (manual QA,
    NPPES, IQVIA) remain separate evidence and never fill the submitted
    blank. One treatment regime at a time (conflicts enforced)."""
    out = copy.deepcopy(rules)
    out.append({
        "rule_code": "RULE-006",
        "name": "B4 Treatment Missing Required NPI (ONC updated scope)",
        "bucket": "B4",
        "priority": 6,
        "description": (
            "ONC updated Treatment scope (relayed 2026-11-09): an Active "
            "Participant/Sub-Participant record asserting the exact T-TRTMNT "
            "exchange purpose as its SOLE purpose with no SUBMITTED NPI "
            "classifies B4. Combined-purpose records are out of this rule's "
            "scope and are evaluated by the remaining applicable rules. "
            "organizationNodeType does not gate this rule. A supplemental "
            "NPI (manual QA, NPPES, IQVIA) never fills the submitted blank "
            "and never suppresses the condition."),
        "conditions": {"all_of": [
            {"field": "treatment_onc_scope_missing_npi", "status": True},
        ]},
    })
    return out


def _delta_structural_npi_handling(rules: List[dict]) -> List[dict]:
    """D-02 rule side: defense-in-depth guards so a confirmed-invalid NPI can
    never pass B1/B2 by rule order alone. The producer half is the
    PROPOSED_NPI_INVALID_SPLIT_ENV flag (approved by this same component)."""
    out = copy.deepcopy(rules)
    for r in out:
        if r["rule_code"] in ("RULE-001", "RULE-002", "RULE-003"):
            _add_none_of(r, _STRUCTURAL_INVALID)
    return out


def _delta_mandatory_source_guards(rules: List[dict]) -> List[dict]:
    """D-03/M-4: a FAILED mandatory source blocks the B1 partial pass."""
    out = copy.deepcopy(rules)
    for r in out:
        if r["rule_code"] == "RULE-002":
            _add_none_of(r, _MANDATORY_FAILED)
    return out


def _delta_material_conflict_guards(rules: List[dict]) -> List[dict]:
    """D-04: material name/address conflicts demote B1/B2 to review. Distinct
    from D-05 (the grading that PRODUCES name_mismatch=major); an evidenced
    conflict needs no numeric threshold to be guarded once graded."""
    out = copy.deepcopy(rules)
    for r in out:
        if r["rule_code"] in ("RULE-001", "RULE-002", "RULE-003"):
            _add_none_of(r, _MATERIAL_CONFLICTS)
    return out


def _delta_exclusion_candidate_b3(rules: List[dict]) -> List[dict]:
    """EX-3/D-16: a pending exclusion candidate is B3, never B4.

    Removes the v3 PENDING `not_found` disqualifiers from RULE-005 (traced
    2026-11-08: on the exclusion dimension a candidate is producer REVIEW ->
    translator `not_found`; a clean screen is already `clear`; a plain
    exclusion-dimension not_found therefore only ever meant a candidate) and
    adds RULE-008 consuming the proposed `possible_match` state, plus
    defense-in-depth B1/B2 guards. Requires the producer flag
    PROPOSED_EXCLUSION_STATES_ENV (approved by this same component), without
    which candidates keep collapsing to not_found and — with the clauses
    removed — would fall to RULE-004/B3-default rather than B4: still review,
    never a silent pass. The manual live-probe literals ("excluded" confirmed
    -> B4 via RULE-005's retained clauses; "clear") are unchanged."""
    out = copy.deepcopy(rules)
    for r in out:
        if r["rule_code"] == "RULE-005":
            r["conditions"]["any_of"] = [
                c for c in r["conditions"].get("any_of", [])
                if not (isinstance(c, dict)
                        and (c.get("source"), c.get("status")) in
                        (("sam_gov", "not_found"), ("oig_leie", "not_found")))]
        if r["rule_code"] in ("RULE-001", "RULE-002", "RULE-003"):
            _add_none_of(r, _POSSIBLE_MATCH)
    out.append({
        "rule_code": "RULE-008",
        "name": "B3 Exclusion Possible Match Pending Adjudication",
        "bucket": "B3",
        "priority": 8,
        "description": (
            "An exclusion screen returned a possible match (ambiguous identity "
            "or a name-level hit) that no analyst has adjudicated. A candidate "
            "is never confirmed non-compliance (B4) and never a clean pass: "
            "explicit review with reason EXCLUSION_POSSIBLE_MATCH_PENDING. "
            "A CONFIRMED applicable exclusion (analyst-adjudicated identity "
            "and scope) still classifies B4 through RULE-005's excluded/"
            "debarred conditions."),
        "conditions": {"any_of": copy.deepcopy(_POSSIBLE_MATCH)},
    })
    return out


@dataclass(frozen=True)
class Component:
    name: str
    decision_refs: Tuple[str, ...]
    description: str
    apply: Optional[Callable[[List[dict]], List[dict]]] = None
    depends_on: Tuple[str, ...] = ()
    #: Components that cannot be active together (e.g. two treatment regimes).
    conflicts_with: Tuple[str, ...] = ()
    #: Env flags this component's approval covers (execution switches; set
    #: only after the component is activated with its recorded approval).
    flag_envs: Tuple[str, ...] = ()


COMPONENTS: Dict[str, Component] = {c.name: c for c in (
    Component("treatment_review_b3", ("D-01 CONFIG A (superseded 2026-11-08; preserved as history)",),
              "RULE-007 B3 applicability review + RULE-006 as B3/B4-candidate",
              apply=_delta_treatment_review_b3,
              conflicts_with=("treatment_b4_owner_adopted",
                              "treatment_b4_onc_scope")),
    Component("treatment_b4", ("D-01 CONFIG A (superseded 2026-11-08; preserved as history)",),
              "RULE-006 upgraded to B4 (confirmed Treatment non-compliance)",
              apply=_delta_treatment_b4, depends_on=("treatment_review_b3",),
              conflicts_with=("treatment_b4_owner_adopted",
                              "treatment_b4_onc_scope")),
    Component("treatment_b4_owner_adopted",
              ("D-01 SUPERSEDING 2026-11-08 (owner-adopted policy; "
               "superseded in turn by the ONC updated scope, 2026-11-09 — "
               "preserved as history)",),
              "active + exact T-TRTMNT (any combination) + blank submitted "
              "NPI -> B4; no node-type gate; RULE-009",
              apply=_delta_treatment_b4_owner_adopted,
              conflicts_with=("treatment_review_b3", "treatment_b4",
                              "treatment_b4_onc_scope")),
    Component("treatment_b4_onc_scope",
              ("ONC updated Treatment scope (relayed 2026-11-09); "
               "D-01 lineage",),
              "active + SOLE-purpose exact T-TRTMNT + Participant/"
              "Sub-Participant + blank submitted NPI -> B4; no node-type "
              "gate; out-of-scope records evaluated by the remaining rules",
              apply=_delta_treatment_b4_onc_scope,
              conflicts_with=("treatment_review_b3", "treatment_b4",
                              "treatment_b4_owner_adopted")),
    Component("structural_npi_handling", ("D-02", "D6"),
              "invalid/flagged split: producer flag + B1/B2 invalid guards",
              apply=_delta_structural_npi_handling,
              flag_envs=("RCE_PROPOSED_NPI_INVALID_SPLIT",)),
    Component("mandatory_source_guards", ("D-03", "M-4", "D4"),
              "FAILED mandatory source blocks the B1 partial pass",
              apply=_delta_mandatory_source_guards),
    Component("material_conflict_guards", ("D-04",),
              "material name/address conflict demotes B1/B2 to review",
              apply=_delta_material_conflict_guards),
    Component("name_severity_configuration", ("D-05", "D5"),
              "similarity-graded name severity; the band value comes from the "
              "recorded decision (no built-in number)",
              flag_envs=("RCE_PROPOSED_NAME_MAJOR_BELOW",)),
    Component("exclusion_candidate_b3", ("EX-3", "D-16"),
              "pending exclusion candidate -> B3, never B4; possible_match "
              "state + RULE-008",
              apply=_delta_exclusion_candidate_b3,
              flag_envs=("RCE_PROPOSED_EXCLUSION_STATES",)),
)}

#: Canonical application order (deterministic composition).
_ORDER = ("treatment_review_b3", "treatment_b4", "treatment_b4_owner_adopted",
          "treatment_b4_onc_scope",
          "structural_npi_handling", "mandatory_source_guards",
          "material_conflict_guards", "name_severity_configuration",
          "exclusion_candidate_b3")
assert set(_ORDER) == set(COMPONENTS)


def compose(selection) -> List[dict]:
    """The rule set for a validated selection of component names, composed
    over the deployed v3 baseline in canonical order. Pure; no I/O."""
    from app.tefca_registry.bucket_classifier import SEED_RULES_V3

    unknown = sorted(set(selection) - set(COMPONENTS))
    if unknown:
        raise ActivationRefused(f"unknown component(s): {unknown}")
    missing = sorted({dep for n in selection
                      for dep in COMPONENTS[n].depends_on} - set(selection))
    if missing:
        raise ActivationRefused(
            f"selection is missing required dependencies: {missing}")
    conflicts = sorted({tuple(sorted((n, c))) for n in selection
                        for c in COMPONENTS[n].conflicts_with
                        if c in selection})
    if conflicts:
        raise ActivationRefused(
            f"selection contains incompatible components: {conflicts} — one "
            f"treatment regime at a time; the superseding owner-adopted "
            f"policy replaces CONFIG A, it does not stack on it.")
    rules = copy.deepcopy(SEED_RULES_V3)
    for name in _ORDER:
        if name in selection and COMPONENTS[name].apply is not None:
            rules = COMPONENTS[name].apply(rules)
    return rules


async def _validate_approval(session, component: str, approval_ref: str) -> None:
    """The reference must be an EXISTING governance audit row of type
    `methodology_approval_recorded` whose metadata names this component.
    Recording that row is a separate explicit admin act; an arbitrary string
    here is refused."""
    import uuid as _uuid

    from sqlalchemy import select
    from app.tefca_registry import models as reg

    try:
        ref_id = _uuid.UUID(str(approval_ref).strip())
    except (ValueError, AttributeError):
        raise ActivationRefused(
            f"component {component!r}: approval reference {approval_ref!r} is "
            f"not a governance-record id. Record the methodology approval "
            f"first; elapsed time, CI success or review silence are not "
            f"approvals.")
    row = (await session.execute(
        select(reg.TefcaRegAuditLog).where(reg.TefcaRegAuditLog.id == ref_id)
    )).scalar_one_or_none()
    if row is None or (row.action or "") != APPROVAL_EVENT_TYPE:
        raise ActivationRefused(
            f"component {component!r}: no {APPROVAL_EVENT_TYPE} governance "
            f"record exists with id {ref_id}.")
    meta = row.metadata_ or {}
    if component not in (meta.get("components") or []):
        raise ActivationRefused(
            f"component {component!r}: governance record {ref_id} does not "
            f"name this component (it names {meta.get('components')}).")


async def activate_rule_configuration(session, approvals: Dict[str, str],
                                      effective: Optional[date] = None,
                                      *, actor: str = "SYSTEM") -> dict:
    """Activate the composed configuration for `approvals`
    (component name -> governance-record id). CALLED BY NOTHING; running it is
    the governed Gate-C step. Validates every component, dependency and
    approval reference BEFORE any write; the rule rows and the activation
    audit event become durable in one commit; prior active rule versions are
    retired, never deleted."""
    from sqlalchemy import func as sqlfunc, select
    from app.tefca_registry import audit as reg_audit
    from app.tefca_registry import models as reg

    if not approvals:
        raise ActivationRefused(
            "empty selection: activating zero components would retire every "
            "active rule row and re-issue the baseline as a new version with "
            "no recorded approval — refuse rather than churn the lineage.")
    rules = compose(set(approvals))          # validates selection + deps
    for name, ref in approvals.items():
        await _validate_approval(session, name, ref)

    from datetime import datetime as _dt, timezone as _tz
    today_utc = _dt.now(_tz.utc).date()
    eff = effective or today_utc
    if eff > today_utc:
        raise ActivationRefused(
            f"effective={eff.isoformat()} is in the future (UTC): prior rows "
            f"would be retired NOW while the new rows only take effect later, "
            f"leaving the loader with no active applicable rule set in "
            f"between.")
    current_max = int((await session.execute(
        select(sqlfunc.coalesce(sqlfunc.max(reg.ReviewRule.version), 0))
    )).scalar() or 0)
    new_version = current_max + 1

    active = (await session.execute(
        select(reg.ReviewRule).where(reg.ReviewRule.is_active.is_(True))
    )).scalars().all()
    for row in active:
        row.is_active = False
        row.retired_date = eff

    manifest = {n: approvals[n] for n in sorted(approvals)}
    for spec in rules:
        session.add(reg.ReviewRule(
            rule_code=spec["rule_code"], name=spec["name"],
            bucket=spec["bucket"], priority=spec["priority"],
            conditions=spec["conditions"],
            description=(f"{spec['description']} [components: "
                         f"{', '.join(sorted(approvals))}]"),
            version=new_version, effective_date=eff, is_active=True))
    reg_audit.record(
        session, "rule_configuration_activated", None,
        actor_email=actor,
        metadata={"version": new_version, "components": manifest,
                  "flag_envs_covered": sorted(
                      e for n in approvals for e in COMPONENTS[n].flag_envs)})
    await session.commit()
    logger.info("Activated rule configuration v%d (components: %s)",
                new_version, ", ".join(sorted(approvals)))
    return {"version": new_version, "rules": len(rules),
            "components": sorted(approvals)}
