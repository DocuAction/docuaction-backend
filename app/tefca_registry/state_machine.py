"""TEFCA entity lifecycle state machine (WF-002).

WHY
    The registry stored `operational_status` as a free string with no rules about
    which value could follow which. That permits draft -> active, skipping
    verification entirely, and inactive -> active, resurrecting a deregistered
    entity without re-registration. Both are silent data-integrity failures: the
    record looks correct afterwards and nothing in the audit trail says a step was
    missed.

SCHEMA
    Uses the existing `operational_status` column. No migration, no new column,
    nothing to deploy beyond code.

POSTURE
    This validates transitions; it does not rewrite history. Entities already
    holding a value outside the model are reported as such rather than coerced -
    an import that silently normalised bad data would destroy the evidence that it
    was bad.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Tuple

DRAFT = "draft"
PENDING_VERIFICATION = "pending_verification"
ACTIVE = "active"
SUSPENDED = "suspended"
INACTIVE = "inactive"

VALID_STATES: FrozenSet[str] = frozenset(
    {DRAFT, PENDING_VERIFICATION, ACTIVE, SUSPENDED, INACTIVE}
)

# Allowed transitions. Anything not listed is refused.
TRANSITIONS: Dict[str, FrozenSet[str]] = {
    DRAFT: frozenset({PENDING_VERIFICATION}),
    PENDING_VERIFICATION: frozenset({ACTIVE, DRAFT}),
    ACTIVE: frozenset({SUSPENDED, INACTIVE}),
    SUSPENDED: frozenset({ACTIVE, INACTIVE}),
    INACTIVE: frozenset(),
}

# Why each refusal matters, in terms a caller can act on.
REFUSAL_REASONS: Dict[Tuple[str, str], str] = {
    (DRAFT, ACTIVE):
        "an entity must pass verification before becoming active; "
        "submit it for verification first",
    (INACTIVE, ACTIVE):
        "a deregistered entity cannot be reactivated; it must be re-registered",
    (INACTIVE, PENDING_VERIFICATION):
        "a deregistered entity cannot re-enter verification; it must be re-registered",
    (ACTIVE, DRAFT):
        "only an entity in verification may return to draft",
    (SUSPENDED, DRAFT):
        "only an entity in verification may return to draft",
    (ACTIVE, PENDING_VERIFICATION):
        "an active entity does not re-enter verification; suspend it instead",
}


class InvalidTransition(ValueError):
    """Raised for a refused transition. Carries both states for the caller."""

    def __init__(self, current: str, target: str, message: str):
        self.current, self.target = current, target
        super().__init__(message)


def is_valid_state(state: Optional[str]) -> bool:
    return (state or "").strip().lower() in VALID_STATES


def allowed_targets(current: Optional[str]) -> List[str]:
    """States reachable from `current`, sorted. Empty for a terminal or unknown
    state - callers use this to build a UI without hardcoding the model."""
    return sorted(TRANSITIONS.get((current or "").strip().lower(), frozenset()))


def validate_transition(current: Optional[str],
                        target: Optional[str]) -> Tuple[bool, str]:
    """Check a transition. Returns (allowed, reason). Never raises.

    Reason is empty when allowed, and is written to be returned to an API caller
    verbatim.
    """
    cur = (current or "").strip().lower()
    tgt = (target or "").strip().lower()

    if not tgt:
        return False, "target state is required"
    if tgt not in VALID_STATES:
        return False, (f"'{target}' is not a valid state; expected one of "
                       f"{', '.join(sorted(VALID_STATES))}")
    if not cur:
        # A record with no state is a data problem, not a transition problem.
        return False, "entity has no current state; cannot evaluate a transition"
    if cur not in VALID_STATES:
        return False, (f"entity is in unrecognised state '{current}'; "
                       f"it must be reconciled before transitioning")
    if cur == tgt:
        return False, f"entity is already '{tgt}'"

    if tgt in TRANSITIONS.get(cur, frozenset()):
        return True, ""

    reason = REFUSAL_REASONS.get((cur, tgt))
    if reason:
        return False, f"cannot move from '{cur}' to '{tgt}': {reason}"
    permitted = allowed_targets(cur)
    return False, (f"cannot move from '{cur}' to '{tgt}'; "
                   + (f"permitted: {', '.join(permitted)}" if permitted
                      else f"'{cur}' is terminal"))


def assert_transition(current: Optional[str], target: Optional[str]) -> None:
    """Raise InvalidTransition when refused. For call sites that prefer an
    exception to a tuple; map it to HTTP 400 at the boundary."""
    ok, reason = validate_transition(current, target)
    if not ok:
        raise InvalidTransition(current or "", target or "", reason)


def audit_payload(entity_id, current: Optional[str], target: Optional[str],
                  allowed: bool, reason: str = "") -> dict:
    """Structured record for the audit trail.

    Refused transitions are recorded too. An attempt to move an entity straight
    from draft to active is exactly the event a reviewer wants to see, and
    logging only successes hides it.
    """
    return {
        "event": "tefca_entity_state_transition",
        "entity_id": str(entity_id) if entity_id is not None else None,
        "from_state": current,
        "to_state": target,
        "allowed": bool(allowed),
        "reason": reason or None,
        "control": "WF-002",
    }
