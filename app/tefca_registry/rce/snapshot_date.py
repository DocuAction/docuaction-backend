"""Authoritative population (snapshot) date of a delivery — recorded, never inferred.

A delivery carries three kinds of time that must not be confused:

* `received_at`  — the registrant-stated RECEIPT date (`received_date` on the registration form);
* `created_at`   — when DocuAction ingested the file;
* snapshot date  — the population date the publisher's extract represents (e.g. the RCE directory
                   as of 2026-09-02).

Nothing in the delivery file or its receipt proves the third, so it is recorded here by an
authorised operator **with its basis** (what document or communication establishes it) and an
audit row, into `rce_source_intakes.source_metadata["snapshot_date"]`. The quality engine reads
only that key (`quality_engine.snapshot_date_from_metadata`); when it is absent the deadline
assessment of NPI-001 1.4.0 stays `unresolved`. A file-name pattern such as
`onc-snapshot-20260902.csv` is evidence an operator may cite in the basis; it is never read
automatically.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict

from app.tefca_registry import audit as reg_audit

SNAPSHOT_DATE_KEY = "snapshot_date"
BASIS_KEY = "snapshot_date_basis"
RECORDED_BY_KEY = "snapshot_date_recorded_by"
RECORDED_AT_KEY = "snapshot_date_recorded_at"
AUDIT_ACTION = "source_snapshot_date_recorded"
MIN_BASIS_CHARS = 10


class SnapshotDateRefused(ValueError):
    """The request does not establish an auditable snapshot date."""


def record_snapshot_date(db, intake, *, snapshot_date: date, basis: str, user,
                         ip_address: str | None = None) -> Dict[str, Any]:
    """Record (or re-record, append-only in the audit log) the delivery's snapshot date.

    Refuses a future date, a date after the stated receipt date (a population cannot be
    extracted after it was received), and a basis shorter than MIN_BASIS_CHARS. Never changes
    `received_at`. The previous value, if any, is kept in the audit row.
    """
    basis = (basis or "").strip()
    if len(basis) < MIN_BASIS_CHARS:
        raise SnapshotDateRefused(f"a basis of at least {MIN_BASIS_CHARS} characters is required "
                                  "(what document or communication establishes the snapshot date)")
    today = datetime.now(timezone.utc).date()
    if snapshot_date > today:
        raise SnapshotDateRefused(f"snapshot date {snapshot_date.isoformat()} is in the future")
    received = getattr(intake, "received_at", None)
    if received is not None and snapshot_date > received.date():
        raise SnapshotDateRefused(f"snapshot date {snapshot_date.isoformat()} is after the stated receipt "
                                  f"date {received.date().isoformat()}; a population cannot be extracted after "
                                  "it was received")
    actor_id, actor_email = reg_audit.actor_of(user)
    meta = dict(intake.source_metadata or {})
    previous = meta.get(SNAPSHOT_DATE_KEY)
    now = datetime.now(timezone.utc).isoformat()
    meta.update({SNAPSHOT_DATE_KEY: snapshot_date.isoformat(), BASIS_KEY: basis,
                 RECORDED_BY_KEY: actor_email or str(actor_id or "unknown"), RECORDED_AT_KEY: now})
    intake.source_metadata = meta   # new dict: SQLAlchemy sees the JSONB change
    reg_audit.record(db, AUDIT_ACTION, None, actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address,
                     metadata={"source_intake_id": str(intake.id), "snapshot_date": snapshot_date.isoformat(),
                               "previous_snapshot_date": previous, "basis": basis,
                               "received_at": received.isoformat() if received else None,
                               "actor_role": getattr(user, "role", None)})
    return {"intake_id": str(intake.id), SNAPSHOT_DATE_KEY: snapshot_date.isoformat(), BASIS_KEY: basis,
            "previous_snapshot_date": previous, "received_at": received.isoformat() if received else None,
            "recorded_by": meta[RECORDED_BY_KEY], "recorded_at": now,
            "note": "received_at is the stated receipt date and is unchanged; the quality engine reads "
                    "snapshot_date only from this metadata; assessments already issued are not re-run."}
