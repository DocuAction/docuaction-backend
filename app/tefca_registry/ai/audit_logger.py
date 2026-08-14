"""Audit trail for every TEFCA AI interaction.

USES THE EXISTING TABLE. No new table is created. Rows go to
`tefca_reg_audit_log` via app.tefca_registry.audit.record() under the action
`ai_entity_resolution` — the same table, same helper, and same action string
that entity_resolver.py already writes today. Following the existing pattern
means an auditor reading the TEFCA trail finds AI events where they already
look, rather than in a second place they have to be told about.

WHAT IS LOGGED AND WHAT IS NOT:

  input_hash    SHA-256 of the exact request payload. Not the payload.
                The payload is public directory data, but a hash proves what
                was sent, is reproducible from the source records, and cannot
                itself become a second copy of the data to govern and retain.
  output_text   The model's raw response, truncated. Kept in full text because
                it is the artifact the reviewer's decision was made against;
                a hash of it would prove nothing about what a human read.
  ai_raw_confidence      recorded, never used for a decision
  evidence_quality_score recorded, computed from objective signals

NEVER RAISES. An audit write failing must not fail the operation it describes —
a lost row is recoverable and visible in the application log; a 500 during
entity verification is not. This mirrors the contract of the existing
audit.record() helper.

DOES NOT COMMIT. The caller owns the transaction, so the audit row lands in the
same commit as the work it records. Committing here would let the two diverge
if the caller later rolled back.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("docuaction.tefca.ai.audit")

ACTION_AI_ENTITY_RESOLUTION = "ai_entity_resolution"

MAX_OUTPUT_CHARS = 4000
MAX_ERRORS_RECORDED = 25


def hash_input(payload: Any) -> str:
    """SHA-256 of a payload, serialized deterministically.

    sort_keys is load-bearing: without it, two identical payloads whose dicts
    were built in a different order hash differently, and the hash stops being
    usable as evidence that the same question was asked twice.
    """
    try:
        text = json.dumps(payload, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 — a hash must always be producible
        text = repr(payload)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TEFCAAIAuditLogger:
    """Writes one row per TEFCA AI interaction. Append-only by convention.

    The session and entity are held on the instance because the orchestrator's
    denial paths log with only a reason available — threading a session through
    every call site would make the cheap "deny and record why" path the
    awkward one, and an awkward audit call is an audit call that gets skipped.
    """

    def __init__(self, session: Any = None, entity_id: Any = None,
                 actor_id: Any = None, actor_email: Optional[str] = None):
        self.session = session
        self.entity_id = entity_id
        self.actor_id = actor_id
        self.actor_email = actor_email
        # Every row this logger wrote, in order. The orchestrator returns these
        # so a caller without a DB session (a test, a dry run) can still inspect
        # exactly what would have been recorded.
        self.records: List[Dict[str, Any]] = []

    async def log(
        self,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        prompt_version: Optional[str] = None,
        input_hash: Optional[str] = None,
        output_text: Optional[str] = None,
        ai_raw_confidence: Optional[float] = None,
        evidence_quality_score: Optional[float] = None,
        evidence_signals: Optional[Dict[str, Any]] = None,
        validation_passed: Optional[bool] = None,
        validation_errors: Optional[List[str]] = None,
        human_review_required: bool = True,
        policy_decision: str = "",
        latency_ms: Optional[float] = None,
        reason: str = "",
        **extra: Any,
    ) -> Dict[str, Any]:
        """Record one interaction. Returns the detail dict that was written.

        Keyword-only: this call has a dozen similar-looking fields, several of
        them floats, and a positional swap between ai_raw_confidence and
        evidence_quality_score would silently produce a compliance record that
        says the opposite of the truth.

        `human_review_required` defaults to True. For TEFCA it is always True;
        the default means a caller that forgets the argument records the
        accurate value rather than an absent one.
        """
        detail: Dict[str, Any] = {
            "event": ACTION_AI_ENTITY_RESOLUTION,
            "scope": "tefca_entity_resolution",
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "input_hash": input_hash,
            "output_text": (output_text or "")[:MAX_OUTPUT_CHARS] or None,
            # Named in full so the distinction survives into the stored JSON,
            # where a future reader has only the key to go on.
            "ai_raw_confidence": ai_raw_confidence,
            "ai_raw_confidence_note": "logged for observability; never used for decisions",
            "evidence_quality_score": evidence_quality_score,
            "evidence_signals": evidence_signals or {},
            "validation_passed": validation_passed,
            "validation_errors": list(validation_errors or [])[:MAX_ERRORS_RECORDED],
            "human_review_required": bool(human_review_required),
            "policy_decision": policy_decision,
            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
            "reason": reason or None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            detail.update(extra)

        self.records.append(detail)
        self._persist(detail)
        return detail

    def _persist(self, detail: Dict[str, Any]) -> None:
        """Stage the row on the caller's session, if there is one."""
        if self.session is None:
            # No session: an unwired call path or a test. The record is still
            # held in self.records and emitted to the application log, so the
            # interaction is never silently unrecorded.
            logger.info("TEFCA AI audit (no session): policy=%s provider=%s "
                        "validation_passed=%s review_required=%s",
                        detail.get("policy_decision"), detail.get("provider"),
                        detail.get("validation_passed"),
                        detail.get("human_review_required"))
            return

        try:
            from app.tefca_registry import audit as reg_audit
            reg_audit.record(
                self.session,
                ACTION_AI_ENTITY_RESOLUTION,
                self.entity_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                metadata=detail,
            )
        except Exception as exc:  # noqa: BLE001 — see module docstring
            logger.warning("TEFCA AI audit write failed (policy=%s): %s",
                           detail.get("policy_decision"), exc)
