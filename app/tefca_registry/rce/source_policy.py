"""M-1..M-4 enforcement hook — executable mandatory-source policy, INACTIVE.

With NO active `rce_source_policies` row (the shipped state; nothing seeds
one), `apply_source_policy` is a no-op and classification behavior is
byte-identical to today. Once the owner records the M-decisions and activates
policy rows (Gate C), the SAME code enforces them, so the decision takes
effect without another engineering round.

SEMANTICS (M-4, as proposed — the policy rows decide WHICH sources):
  * Only a B1/B2 outcome is ever reconsidered; B3/B4 stand.
  * For each APPLICABLE mandatory source, the record's state must be an
    ACCEPTABLE answer (verified/clear):
      - missing / not_checked / unavailable / failed  -> B3 with the
        incomplete-source reason SOURCE_NOT_COMPLETED_<SOURCE>;
      - possible_match (pending candidate)            -> B3 with the specific
        review reason EXCLUSION_POSSIBLE_MATCH_PENDING (defense in depth —
        RULE-008 normally routes it first);
      - an ANSWERED ADVERSE outcome (not_found, deactivated, excluded) is a
        FINDING, never relabelled SOURCE_NOT_COMPLETED: it is left to the
        approved rule conditions and their precedence.
  * conditional role: applies only where its applicability facts match
    (e.g. {"requires_npi": true}); a conditional check outside its
    applicability is NOT_APPLICABLE and never blocks.
  * advisory role: never independently blocks anything.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: M-1 RECORDED OWNER DECISION (2026-11-07/08, preserved reference: commit
#: 1da7efe): attributable SAM exclusion screening is REQUIRED for B1 within
#: the adopted applicability policy. This is the canonical PROPOSED policy
#: DATA — documented here, seeded NOWHERE; activating it is the Gate-C act of
#: inserting+activating the row with its governance approval reference.
#: Applicability is stated explicitly (all records; the exclusion question
#: applies to every delivered entity). Conditional sources whose
#: applicability facts are UNDEFINED are deliberately absent — an undefined
#: applicability is disclosed, never defaulted to mandatory.
#:
#: HISTORY, not rewritten: bucket_classifier._v2_rules documented "SAM is
#: added as a DISQUALIFIER, never as a requirement" — correct for the 2026-10
#: context (no SAM answer channel existed; requiring it would have
#: reclassified the registry on an outage). The owner's M-1 decision
#: SUPERSEDES that stance going forward, now that attributable extract
#: screening exists; the v2 docstring stays as written because historical
#: behavior is not rewritten.
#:
#: SAM outcome vocabulary kept distinct end-to-end:
#:   SCREENED_NO_CANDIDATE            -> classifier "clear"
#:   POSSIBLE_MATCH (pending)         -> "not_found" today; "possible_match"
#:                                       under the EX-3 component's flag —
#:                                       its OWN review reason, never a false
#:                                       SOURCE_NOT_COMPLETED label
#:   CONFIRMED_APPLICABLE_EXCLUSION   -> "excluded"/"debarred" (approved B4)
#:   NOT_SCREENED / UNAVAILABLE / FAILED -> incomplete ->
#:                                       SOURCE_NOT_COMPLETED_SAM_GOV
PROPOSED_POLICY_M1 = (
    {"source": "sam_gov", "role": "mandatory",
     "applicability": {},   # explicit: applies to every delivered record
     "policy_reference": "M-1 owner decision "
                         "(OCTOBER_DECISIONS_APPROVED_2026-11-07.md, commit "
                         "1da7efe); D-11 Part 2"},
)

#: Reserved provenance code for a policy-driven determination. NOT a
#: review_rules row: the versioned provenance it cites is the ACTIVE
#: rce_source_policies row (policy_reference + approval_reference), and the
#: rationale names the specific SOURCE_NOT_COMPLETED_* / pending reason. Kept
#: distinct from DEFAULT-UNMATCHED, which means "the rule set does not
#: describe this input".
SOURCE_POLICY_RULE_CODE = "SOURCE-POLICY-M4"

ACCEPTABLE_STATES = frozenset({"verified", "clear"})
INCOMPLETE_STATES = frozenset({"not_checked", "unavailable", "failed"})
PENDING_REVIEW_STATES = frozenset({"possible_match"})


async def load_active_policies(session) -> List[Dict[str, Any]]:
    """Active policy rows, newest version per source. Empty list = inactive."""
    from sqlalchemy import select
    from app.tefca_registry.rce import models as m

    rows = (await session.execute(
        select(m.RceSourcePolicy).where(m.RceSourcePolicy.is_active.is_(True))
    )).scalars().all()
    best: Dict[str, Any] = {}
    for r in rows:
        cur = best.get(r.source)
        if cur is None or (r.version or 0) > (cur.version or 0):
            best[r.source] = r
    return [{"id": str(r.id), "version": r.version,
             "source": r.source, "role": r.role,
             "applicability": r.applicability or {},
             "policy_reference": r.policy_reference,
             "approval_reference": str(r.approval_reference)}
            for r in best.values()]


def _applies(policy: Dict[str, Any], record_facts: Dict[str, Any]) -> bool:
    """Does this policy row apply to the record? Advisory never blocks;
    requires_npi is honored; a conditional policy with NO stated
    applicability applies nowhere (M-policy is never decided by omission)."""
    if policy["role"] == "advisory":
        return False
    applicability = policy.get("applicability") or {}
    if applicability.get("requires_npi") and not record_facts.get("has_submitted_npi"):
        return False
    if policy["role"] == "conditional" and not applicability:
        # A conditional policy with no stated applicability facts cannot be
        # defaulted to "always applies" — that would decide M-policy by
        # omission. It applies nowhere until its facts are stated.
        return False
    return True


def apply_source_policy(bucket: str, rule_code: Optional[str],
                        sources: Dict[str, Any],
                        record_facts: Dict[str, Any],
                        policies: List[Dict[str, Any]],
                        ) -> Tuple[str, Optional[str], Optional[str],
                                   Optional[Dict[str, Any]]]:
    """(bucket, rule_code, policy_reason, matched_policy). Pure; unchanged -
    with matched_policy None - when `policies` is empty or the bucket is not
    B1/B2. `matched_policy` is the ACTUAL policy row dict (id, version,
    policy_reference, approval_reference) so the stored determination cites
    its real versioned provenance, never a hardcoded placeholder."""
    if not policies or bucket not in ("B1", "B2"):
        return bucket, rule_code, None, None
    for policy in policies:
        if not _applies(policy, record_facts):
            continue
        raw = sources.get(policy["source"])
        state = raw if isinstance(raw, str) else (raw or {}).get("status") if raw else None
        if state in ACCEPTABLE_STATES:
            continue
        if state in PENDING_REVIEW_STATES:
            return "B3", None, "EXCLUSION_POSSIBLE_MATCH_PENDING", policy
        if state is None or state in INCOMPLETE_STATES:
            return ("B3", None,
                    f"SOURCE_NOT_COMPLETED_{policy['source'].upper()}", policy)
        # Answered adverse (not_found / deactivated / excluded / anything
        # else the rules know): a finding, owned by the rule conditions.
    return bucket, rule_code, None, None
