"""Audit entries for controlled export generation.

WHY THIS IS FOUR LINES OF WRAPPER AND NOT A FRAMEWORK
─────────────────────────────────────────────────────
`audit_logs` already exists, already has `event_type`, `outcome`,
`resource_type`, `resource_id` and `correlation_id` as first-class indexed
columns, and is already what the Audit Trail UI reads. Producing an export is an
event of the kind it was built for. This module exists only to make sure every
export event is spelled the same way — same event type, same outcome vocabulary,
same resource — so "show me every export anyone produced" is one query rather
than a scan for a phrase somebody typed by hand.

WHAT AN EXPORT AUDIT ENTRY MUST AND MUST NOT CONTAIN
────────────────────────────────────────────────────
It answers who, what, when, which delivery, which version, what happened, and —
on success — which artifact. That is enough to reconstruct the event.

It carries NO Government row values. An audit trail that copied the data it
describes would be a second, uncontrolled copy of the population, sitting in a
table with different retention and different access rules from the one the
export itself is subject to. It also carries no secrets, no tokens, no
connection strings and no exception text: the runner puts diagnostics in the
application log, where an administrator reads them, and the audit entry records
the controlled outcome.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: The Audit Trail UI filters on this. Producing a controlled export is a
#: reporting event, not a data change: nothing in DocuAction changes because a
#: workbook was made.
EVENT_TYPE = "reporting"

RESOURCE_TYPE = "controlled_export"

ACTION_REQUESTED = "EXPORT_JOB_REQUESTED"
ACTION_REUSED = "EXPORT_JOB_REUSED"
ACTION_SUCCEEDED = "EXPORT_JOB_SUCCEEDED"
ACTION_FAILED = "EXPORT_JOB_FAILED"
ACTION_REAPED = "EXPORT_JOB_REAPED"

#: `outcome` is an indexed column precisely so "what failed" is not a scan.
_OUTCOMES = {
    ACTION_REQUESTED: "success",
    ACTION_REUSED: "success",
    ACTION_SUCCEEDED: "success",
    ACTION_FAILED: "failure",
    ACTION_REAPED: "failure",
}


async def record_export_event(db, *, action: str, actor: str,
                              job_id: Optional[str] = None,
                              detail: Optional[str] = None,
                              extra: Optional[Dict[str, Any]] = None) -> None:
    """Write one export audit entry. Never raises.

    An audit write that could fail the operation it describes would make the
    trail a liability: the safest system would be the one that recorded least.
    A failure to audit is logged loudly and the export proceeds.
    """
    from app.models.database import AuditLog

    payload: Dict[str, Any] = {"job_id": job_id}
    if detail:
        payload["detail"] = detail
    payload.update(extra or {})

    try:
        db.add(AuditLog(
            action=action,
            event_type=EVENT_TYPE,
            outcome=_OUTCOMES.get(action, "success"),
            resource_type=RESOURCE_TYPE,
            resource_id=job_id,
            # The actor as the application knows them. `user_id` is a foreign
            # key to `users` and an export may be requested by a system caller
            # that has no row there, so the identity travels in `details` where
            # it cannot break the write.
            details={**payload, "actor": actor},
            correlation_id=job_id,
        ))
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("could not write export audit entry %s for %s: %s",
                     action, job_id, exc)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
