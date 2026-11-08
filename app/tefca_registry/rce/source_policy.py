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
    return [{"source": r.source, "role": r.role,
             "applicability": r.applicability or {},
             "policy_reference": r.policy_reference,
             "approval_reference": str(r.approval_reference)}
            for r in best.values()]


def _applies(policy: Dict[str, Any], record_facts: Dict[str, Any]) -> bool:
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
                        ) -> Tuple[str, Optional[str], Optional[str]]:
    """(bucket, rule_code, policy_reason). Pure; unchanged when `policies` is
    empty or the bucket is not B1/B2."""
    if not policies or bucket not in ("B1", "B2"):
        return bucket, rule_code, None
    for policy in policies:
        if not _applies(policy, record_facts):
            continue
        raw = sources.get(policy["source"])
        state = raw if isinstance(raw, str) else (raw or {}).get("status") if raw else None
        if state in ACCEPTABLE_STATES:
            continue
        if state in PENDING_REVIEW_STATES:
            return "B3", None, "EXCLUSION_POSSIBLE_MATCH_PENDING"
        if state is None or state in INCOMPLETE_STATES:
            return "B3", None, f"SOURCE_NOT_COMPLETED_{policy['source'].upper()}"
        # Answered adverse (not_found / deactivated / excluded / anything
        # else the rules know): a finding, owned by the rule conditions.
    return bucket, rule_code, None
