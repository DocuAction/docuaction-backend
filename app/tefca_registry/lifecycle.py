"""Entity lifecycle: guarded status transitions and verification confidence.

This is the wiring layer the registry was missing. `state_machine.py` and
`app/services/npi_validator.py` were both implemented, unit-tested, and imported
by nothing except their own tests — the rules existed on paper while the API let
any status follow any other. Nothing here re-implements those rules; it calls
them from the request path.

Two behaviours are deliberate and worth stating:

* A refused transition is audited, not just rejected. An attempt to move an
  entity straight from draft to active is the event a reviewer most wants to
  see, and recording only successes hides exactly the wrong thing.
* Confidence is scored over the sources that ANSWERED. A source that is down
  contributes neither credit nor penalty, and the divisor shrinks to match —
  otherwise an outage at NPPES would look identical to an entity NPPES has
  never heard of, which is a false accusation rather than a low score.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg
from app.tefca_registry import state_machine as sm

logger = logging.getLogger(__name__)

# Weight per authoritative source. NPPES dominates because it is the identity
# source of record for a provider; the rest corroborate.
SOURCE_WEIGHTS: Dict[str, float] = {
    "nppes": 0.40,
    "pecos": 0.20,
    "sam_gov": 0.10,
    "oig_leie": 0.10,
    "state": 0.10,
    "irs": 0.10,
}


class TransitionRefused(Exception):
    """A status change the state machine does not allow."""

    def __init__(self, current: Optional[str], target: str, message: str):
        self.current, self.target, self.message = current, target, message
        super().__init__(message)


def explain_refusal(current: Optional[str], target: str, reason: str) -> str:
    """A message the caller can act on, naming the route they should take.

    'Invalid transition' tells an operator nothing. Naming the missing step —
    and the states that ARE reachable — turns a 400 into instructions.
    """
    base = f"Cannot transition from {current} to {target}."
    if reason:
        base += f" {reason[0].upper()}{reason[1:]}."
    allowed = sm.allowed_targets(current)
    if allowed:
        base += f" Allowed from {current}: {', '.join(sorted(allowed))}."
    else:
        base += f" {current} is terminal; no transition is permitted."
    return base


def check_transition(current: Optional[str], target: str) -> Tuple[bool, str]:
    """(allowed, human-readable reason). Never raises."""
    ok, reason = sm.validate_transition(current, target)
    return ok, ("" if ok else explain_refusal(current, target, reason))


def apply_transition(session, entity, target: str, *, user=None,
                     ip_address: Optional[str] = None,
                     extra: Optional[dict] = None) -> dict:
    """Validate and apply a status change, auditing either outcome.

    Raises TransitionRefused (audited first) when the state machine says no.
    Does not commit — the caller owns the transaction so the audit row and the
    status change land together.
    """
    current = entity.operational_status
    actor_id, actor_email = reg_audit.actor_of(user)
    allowed, message = check_transition(current, target)

    payload = sm.audit_payload(entity.id, current, target, allowed,
                               "" if allowed else message)
    if extra:
        payload.update(extra)

    if not allowed:
        reg_audit.record(session, reg_audit.STATUS_CHANGE_REFUSED, entity.id,
                         actor_id=actor_id, actor_email=actor_email,
                         ip_address=ip_address, metadata=payload)
        raise TransitionRefused(current, target, message)

    entity.operational_status = target
    # is_active mirrors the lifecycle so existing "active entities" queries keep
    # working without every caller learning the state machine.
    entity.is_active = target != sm.INACTIVE
    reg_audit.record(session, reg_audit.STATUS_CHANGED, entity.id,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address, metadata=payload)
    return {"entity_id": str(entity.id), "from_state": current,
            "to_state": target, "allowed": True}


def compute_confidence(source_results: Dict[str, Any]) -> dict:
    """Weighted confidence over the sources that actually answered.

    `source_results` maps a source key from SOURCE_WEIGHTS to one of:
        True/False            - matched / did not match
        None                  - unavailable (skipped, not counted)
        {"matched": bool, "available": bool}

    Returns the score plus the arithmetic behind it, because a bare 0.62 is not
    reviewable. `coverage` is the share of total weight that was reachable — a
    high score over 40% coverage is a weaker claim than the same score over 90%,
    and the caller should be able to tell those apart.
    """
    considered, earned, per_source = 0.0, 0.0, {}
    for key, weight in SOURCE_WEIGHTS.items():
        raw = source_results.get(key)
        if raw is None:
            per_source[key] = {"weight": weight, "status": "unavailable",
                               "counted": False}
            continue
        if isinstance(raw, dict):
            available = raw.get("available", True)
            matched = bool(raw.get("matched"))
        else:
            available, matched = True, bool(raw)
        if not available:
            per_source[key] = {"weight": weight, "status": "unavailable",
                               "counted": False}
            continue
        considered += weight
        if matched:
            earned += weight
        per_source[key] = {"weight": weight,
                           "status": "match" if matched else "no_match",
                           "counted": True}

    score = round(earned / considered, 4) if considered > 0 else None
    return {
        "confidence_score": score,
        "weight_considered": round(considered, 4),
        "weight_earned": round(earned, 4),
        "coverage": round(considered / sum(SOURCE_WEIGHTS.values()), 4),
        "sources": per_source,
        # Stated explicitly so a null score is never read as "scored zero".
        "note": ("no authoritative source was reachable; score is null rather "
                 "than 0.0" if score is None else
                 "score is over reachable sources only; see coverage"),
    }
