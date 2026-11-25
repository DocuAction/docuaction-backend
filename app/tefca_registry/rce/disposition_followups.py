"""Delivery-level processing after an analyst decision, out of the save request.

WHAT MOVED, AND WHAT DID NOT (2026-11-18)
─────────────────────────────────────────
The DECISION stays synchronous and unchanged: permission, the per-finding
guard, the terminal refusal, the identifier decision, the issue state walk,
`apply_correction` (staleness hash, NPI validation, correction detail), the
hold recomputation and the audit row all still run inside the request, and the
request still answers with what was recorded.

The CONSEQUENCE moved: whole-delivery re-promotion, full reconciliation (every
existing check, the Area 1 re-hash included) and the persisted snapshot. With
`ENABLE_DEFERRED_DISPOSITION_PROCESSING` set, the request records a row in
`rce_disposition_followups` BEFORE its first commit and answers 202; a worker
on the existing delivery scheduler does the rest.

    RECEIVED ──► QUEUED ──► PROCESSING ──► COMPLETED
        │           ▲            │
        │           └── retry ───┤ (bounded; then FAILED with a safe code)
        ├──► NOT_REQUIRED  (no record status changed: nothing to process)
        ├──► REFUSED       (the decision was refused after a partial commit)
        └──► FAILED        (the request died or errored: never replayed)

ONE RUN PER DELIVERY, COALESCED
───────────────────────────────
A worker takes a delivery-level Postgres advisory lock (session-level, on a
dedicated connection, as `_issue_disposition_guard` does per finding), claims
EVERY queued row for that delivery as one batch, and runs ONE hold
recomputation + promotion + reconciliation + snapshot for the batch. A
strictly one-run-per-decision queue cannot keep up: DEV took 51 s per run and
peaked at 76 releasing saves in one hour.

THE CONSEQUENCE IS STATE-DERIVED, WHICH IS WHAT MAKES RECOVERY SAFE
──────────────────────────────────────────────────────────────────
Promotion drains only records that still need promoting, and writes no
disposition event when the disposition is unchanged. Nothing here re-applies a
decision or a correction: a retry after a crash re-derives the work from the
database. An interrupted DECISION is never replayed either - see
`sweep_received`.

EVERYTHING THE WORKER WRITES GOES THROUGH THE LOCK CONNECTION (2026-11-19)
────────────────────────────────────────────────────────────────────────
The advisory lock is session-level, so it lives exactly as long as its
connection. Every session a run uses is bound to that connection
(`DeliveryLock.session`): if the connection dies, the lock and the uncommitted
work die together, and whatever was committed was committed while the lock was
held. A run cannot keep writing a delivery it no longer holds.

A synchronous save (flag off) takes the same lock in SHARED mode for its whole
request once the table exists (`synchronous_slot`). Shared holders coexist, so
synchronous saves relate to each other exactly as before; the worker, which
takes it exclusively, cannot start while one is in flight, and a synchronous
save is refused while the worker holds it.

Flag off: nothing in this module runs from a request, and the worker finds no
rows. The worker and the reaper are deliberately NOT conditional on the flag,
so turning the flag off never strands work that was already accepted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, text, update

from app.core import request_context
from app.tefca_registry.rce import disposition_followup_models as fm
from app.tefca_registry.rce.disposition_followup_models import RceDispositionFollowup as F

logger = logging.getLogger(__name__)

FLAG = "ENABLE_DEFERRED_DISPOSITION_PROCESSING"

#: A batch is retried this many times before its rows are FAILED.
MAX_ATTEMPTS = 3
#: Bounded re-drain: decisions accepted while a batch runs join it, up to this.
MAX_DRAIN_ROUNDS = 5
#: Bounded wait for a decision that is still being applied in its own request
#: before a reconciliation that does not pass is believed.
RECONCILE_SETTLE_ROUNDS = 3
RECONCILE_SETTLE_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 15
#: A PROCESSING row silent for this long has lost its worker.
STALE_PROCESSING_SECONDS = 180
#: A RECEIVED row this old, whose finding guard is free, has lost its request.
STALE_RECEIVED_SECONDS = 300

CODE_PROCESSING_ERROR = "PROCESSING_ERROR"
CODE_RETRIES_EXHAUSTED = "RETRIES_EXHAUSTED"
CODE_WORKER_LOST = "WORKER_LOST"
CODE_DECISION_INTERRUPTED = "DECISION_INTERRUPTED"
CODE_DECISION_ERROR = "DECISION_ERROR"
CODE_DECISION_REFUSED = "DECISION_REFUSED"
#: Codes that describe the delivery-level step. A later completed run for the
#: same delivery covers them, because that run re-derives the whole delivery.
PROCESSING_CODES = (CODE_PROCESSING_ERROR, CODE_RETRIES_EXHAUSTED, CODE_WORKER_LOST)

#: The ONLY failure text a reviewer is shown. No exception text, no values.
SAFE_EXPLANATIONS = {
    CODE_PROCESSING_ERROR: (
        "Delivery processing stopped before it finished. Your decision is "
        "recorded. Processing will be retried automatically."
    ),
    CODE_RETRIES_EXHAUSTED: (
        "Delivery processing did not finish after several attempts. Your "
        "decision is recorded, but the delivery's promotion and reconciliation "
        "are not up to date. An authorised reviewer can retry processing."
    ),
    CODE_WORKER_LOST: (
        "The worker processing this delivery stopped responding. Your decision "
        "is recorded. Processing will be retried automatically."
    ),
    CODE_DECISION_INTERRUPTED: (
        "The request was interrupted before the decision finished recording. "
        "It was not repeated automatically. Open the finding to see what was "
        "recorded, and decide again if it is still open."
    ),
    CODE_DECISION_ERROR: (
        "The decision could not be completed because of a server error. It was "
        "not repeated automatically. Open the finding to see what was recorded, "
        "and decide again if it is still open."
    ),
    CODE_DECISION_REFUSED: ("The decision was refused. The reason was returned with the request."),
}

RETRY_REFUSED_NOT_RECORDED = (
    "The decision for this finding was not recorded, so there is no delivery "
    "processing to retry. Open the finding and record the decision again."
)
RETRY_REFUSED_APPROVED = (
    "This finding has an approved decision that was never applied. Processing "
    "is not retried for it: open the finding and complete the decision first."
)


class FollowupRefused(RuntimeError):
    """An operation on a follow-up row was refused, with a reason safe to show."""


class IdempotencyConflict(RuntimeError):
    """The same Idempotency-Key was reused with a different decision."""


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


async def active(db) -> bool:
    """Deferral is used only when the flag is on AND the table exists. With
    the flag on and the migration not applied, saves stay synchronous rather
    than fail."""
    if not enabled():
        return False
    try:
        return await store_exists(db)
    except Exception:  # noqa: BLE001
        return False


def _now() -> datetime:
    return datetime.now(UTC)


def fingerprint(decision: str, reason: str, value: str | None) -> str:
    # A JSON array, not a joined string: no value of one field can be read as
    # the boundary of another, and `null` stays distinct from "".
    raw = json.dumps(
        [
            (decision or "").strip().upper(),
            (reason or "").strip(),
            # NOT trimmed: the correction is written exactly as supplied, so
            # two values that differ only in whitespace are different decisions.
            None if value is None else str(value),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clean_key(raw: str | None) -> str | None:
    key = (raw or "").strip()
    return key[:128] if key else None


# ── the row, as the request sees it ──────────────────────────────────────────


async def find_by_key(db, issue_id, key: str | None) -> F | None:
    if not key or issue_id is None:
        return None
    return (
        await db.execute(select(F).where(F.issue_id == issue_id, F.idempotency_key == key).limit(1))
    ).scalar_one_or_none()


def new_row(
    *,
    kind: str,
    intake_id,
    issue_id,
    source_record_id,
    decision: str,
    reason: str,
    actor: str,
    actor_id,
    attribution: str,
    idempotency_key: str | None,
    payload_fingerprint: str | None,
    delivery_job_id=None,
) -> F:
    """A RECEIVED row, NOT yet added to a session. The caller adds it to the
    request session before the decision's first commit, so the first commit
    that changes anything is also the one that persists the accepted work."""
    return F(
        id=uuid.uuid4(),
        kind=kind,
        intake_id=intake_id,
        delivery_job_id=delivery_job_id,
        issue_id=issue_id,
        source_record_id=source_record_id,
        decision=decision[:32],
        decision_reason=reason,
        attribution=attribution,
        actor=(actor or "SYSTEM")[:320],
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        payload_fingerprint=payload_fingerprint,
        state=fm.STATE_RECEIVED,
        attempt_count=0,
        recovered=False,
        correlation_id=request_context.correlation_id()[:64],
        build_sha=request_context.build_sha(),
    )


async def settle(
    row_id,
    *,
    state: str,
    error_code: str | None = None,
    error_detail: str | None = None,
    record_status_before: str | None = None,
    record_status_after: str | None = None,
    decision_result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Move a RECEIVED row to its next state, on a FRESH session.

    Fresh, because the callers include every failure exit of the request,
    whose own session has just been rolled back. A row that is not RECEIVED any
    more is left alone (the sweeper or a worker already spoke). Returns the
    row's DTO, or None when the row does not exist (the decision's first commit
    never happened, so nothing was accepted).
    """
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        row = await db.get(F, row_id)
        if row is None:
            return None
        if row.state == fm.STATE_RECEIVED:
            now = _now()
            row.state = state
            row.error_code = error_code
            row.error_detail = (error_detail or None) and error_detail[:500]
            if record_status_before is not None:
                row.record_status_before = record_status_before
            if record_status_after is not None:
                row.record_status_after = record_status_after
            if decision_result is not None:
                row.decision_result = decision_result
            if state == fm.STATE_QUEUED:
                row.queued_at = now
            elif state in (fm.STATE_NOT_REQUIRED,):
                row.completed_at = now
            elif state in (fm.STATE_FAILED, fm.STATE_REFUSED):
                row.failed_at = now
            await db.commit()
            await db.refresh(row)
        return dto(row)


def explanation(row: F) -> str | None:
    if row.state not in (fm.STATE_FAILED, fm.STATE_REFUSED):
        return None
    return SAFE_EXPLANATIONS.get(row.error_code or "", SAFE_EXPLANATIONS[CODE_PROCESSING_ERROR])


_STATE_MESSAGES = {
    fm.STATE_RECEIVED: "Decision received; it is being recorded.",
    fm.STATE_QUEUED: (
        "Decision recorded. Delivery processing (promotion and "
        "reconciliation) is queued and has not run yet."
    ),
    fm.STATE_PROCESSING: (
        "Decision recorded. Delivery processing (promotion and reconciliation) is running."
    ),
    fm.STATE_COMPLETED: "Decision recorded and delivery processing completed.",
    fm.STATE_NOT_REQUIRED: (
        "Decision recorded. It changed no record status, so no delivery processing was needed."
    ),
}


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def dto(row: F) -> dict[str, Any]:
    """What a client may see. References, states and safe text only."""
    return {
        "id": str(row.id),
        "kind": row.kind,
        "state": row.state,
        "pending": row.state in fm.PENDING_STATES,
        "message": _STATE_MESSAGES.get(row.state) or explanation(row),
        "safe_explanation": explanation(row),
        "error_code": row.error_code if row.state in (fm.STATE_FAILED, fm.STATE_REFUSED) else None,
        # Processing failures only. A row that failed because its DECISION
        # was interrupted has no recorded decision to process (a recorded one
        # is queued by recovery, not left FAILED).
        "can_retry": row.state == fm.STATE_FAILED and row.error_code in PROCESSING_CODES,
        "intake_id": str(row.intake_id),
        "issue_id": str(row.issue_id) if row.issue_id else None,
        "source_record_id": str(row.source_record_id) if row.source_record_id else None,
        "decision": row.decision,
        "actor": row.actor,
        "attempt_count": row.attempt_count,
        "recovered": bool(row.recovered),
        "record_status_before": row.record_status_before,
        "record_status_after": row.record_status_after,
        "snapshot_id": str(row.snapshot_id) if row.snapshot_id else None,
        "snapshot_passed": row.snapshot_passed,
        "disposition_event_id": (
            str(row.disposition_event_id) if row.disposition_event_id else None
        ),
        "received_at": _iso(row.received_at),
        "queued_at": _iso(row.queued_at),
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "failed_at": _iso(row.failed_at),
        "status_url": f"/api/tefca/rce/disposition-processing/{row.id}",
    }


async def get(db, row_id) -> F | None:
    return await db.get(F, row_id)


async def store_exists(db) -> bool:
    return (await db.execute(text(f"SELECT to_regclass('public.{fm.TABLE}')"))).scalar() is not None


async def store_present(db) -> bool:
    """`store_exists` that never fails its caller (absent on any error)."""
    try:
        return await store_exists(db)
    except Exception:  # noqa: BLE001
        return False


# ── admission while earlier work is still outstanding (flag OFF) ─────────────
#
# The worker finishes accepted work whether or not the flag is on. So after the
# flag is turned off there can be a window in which rows are still QUEUED or
# PROCESSING. A synchronous save in that window would run its own
# whole-delivery promotion and reconciliation at the same time as the worker
# (it does not take the delivery lock) and would stamp its own reviewer on
# records other reviewers released. It is therefore refused, having written
# nothing, until the delivery has drained. Deferred saves are not affected:
# they queue behind the work already there.
#
# The count alone is a check-then-act: a FAILED row is not pending, and a
# reviewer may retry it a moment after the count was read, while the save is
# still promoting. So the synchronous paths also hold `synchronous_slot` (the
# worker's lock, shared) for the whole request and count AFTER taking it: the
# worker cannot start until the save has finished.

PROCESSING_PENDING_CODE = "DELIVERY_PROCESSING_PENDING"
PROCESSING_PENDING_MESSAGE = (
    "Earlier decisions for this delivery are still being processed. Nothing was "
    "recorded for this request. Wait until delivery processing completes, then "
    "save again."
)


PROCESSING_FAILED_CODE = "DELIVERY_PROCESSING_FAILED"
PROCESSING_FAILED_MESSAGE = (
    "Earlier decisions for this delivery have delivery processing that did not "
    "complete. Nothing was recorded for this request. Open the finding that shows "
    "the failed processing, retry it, wait until it completes, then try again."
)


async def failed_processing_count(db, intake_id) -> int:
    """Rows whose DELIVERY PROCESSING failed and has not been retried. Their
    decisions are recorded and their records are released but not promoted;
    the next worker run takes them with their own reviewer's attribution
    (`_claim(include_failed=True)`). 0 when the table is absent."""
    if intake_id is None or not await store_present(db):
        return 0
    try:
        iid = intake_id if isinstance(intake_id, uuid.UUID) else uuid.UUID(str(intake_id))
    except (ValueError, TypeError):
        return 0
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(F)
                .where(
                    F.intake_id == iid,
                    F.state == fm.STATE_FAILED,
                    F.error_code.in_(PROCESSING_CODES),
                )
            )
        ).scalar()
        or 0
    )


async def admission_refusal(db, intake_id) -> tuple[str, str] | None:
    """Why a promotion OUTSIDE the worker (a synchronous save, the identifier
    route with the flag off, the manual promote route) must not start now, as
    `(code, message)`; None when it may.

    Pending work: the worker is about to promote, or is promoting. Failed
    processing: a reviewer's released record is waiting for a retry, and
    promoting it here would write it under SOMEONE ELSE'S name (or SYSTEM)."""
    if await pending_count(db, intake_id):
        return PROCESSING_PENDING_CODE, PROCESSING_PENDING_MESSAGE
    if await failed_processing_count(db, intake_id):
        return PROCESSING_FAILED_CODE, PROCESSING_FAILED_MESSAGE
    return None


async def pending_count(db, intake_id) -> int:
    """Accepted-but-unfinished rows for a delivery. 0 when the table is absent."""
    if intake_id is None:
        return 0
    try:
        if not await store_exists(db):
            return 0
    except Exception:  # noqa: BLE001 - never fail a save on this read
        return 0
    try:
        iid = intake_id if isinstance(intake_id, uuid.UUID) else uuid.UUID(str(intake_id))
    except (ValueError, TypeError):
        return 0
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(F)
                .where(F.intake_id == iid, F.state.in_(fm.PENDING_STATES))
            )
        ).scalar()
        or 0
    )


# ── disclosure ───────────────────────────────────────────────────────────────

_EMPTY_SUMMARY = {
    "store_present": False,
    "received": 0,
    "queued": 0,
    "processing": 0,
    "pending": 0,
    "failed": 0,
    "oldest_pending_at": None,
    "reconciliation_current": True,
    "notice": None,
}

PENDING_NOTICE = (
    "{n} analyst decision(s) are recorded but their delivery "
    "processing (promotion and reconciliation) has not completed. "
    "The reconciliation shown predates them and is NOT current."
)
FAILED_NOTICE = (
    "{n} analyst decision(s) have delivery processing that did not "
    "complete. The reconciliation shown may predate them and is NOT "
    "current until processing is retried and completes."
)


DELIVERY_BUSY_MESSAGE = (
    "Promotion and reconciliation for this delivery are already running "
    "(processing of recorded analyst decisions). Wait for it to complete, "
    "then refresh before trying again."
)


async def summary(db, intake_id) -> dict[str, Any]:
    """Accepted-but-unfinished work for one delivery, for every place that
    presents a reconciliation snapshot. Fails OPEN to "nothing pending" only
    when the table does not exist (flag never enabled on this database)."""
    if intake_id is None:
        return dict(_EMPTY_SUMMARY)
    try:
        if not await store_exists(db):
            return dict(_EMPTY_SUMMARY)
    except Exception:  # noqa: BLE001 - a status read must not fail its caller
        return dict(_EMPTY_SUMMARY)
    iid = intake_id if isinstance(intake_id, uuid.UUID) else uuid.UUID(str(intake_id))
    # ONE statement, so every number comes from one database snapshot. Read
    # separately, a retry that moved the only row FAILED -> QUEUED between two
    # reads was seen by neither, and the delivery was reported current.
    #
    # A FAILED row is still outstanding unless a later row for the same finding
    # (or, for delivery-level codes, a later completed run) superseded it.
    row = (
        await db.execute(
            text(f"""
        SELECT
          count(*) FILTER (WHERE f.state = 'RECEIVED')   AS received,
          count(*) FILTER (WHERE f.state = 'QUEUED')     AS queued,
          count(*) FILTER (WHERE f.state = 'PROCESSING') AS processing,
          min(f.received_at) FILTER (
            WHERE f.state IN ('RECEIVED','QUEUED','PROCESSING'))  AS oldest,
          count(*) FILTER (WHERE f.state = 'FAILED' AND NOT EXISTS (
            SELECT 1 FROM {fm.TABLE} n
            WHERE n.intake_id = f.intake_id AND n.received_at > f.received_at
              AND n.state <> 'FAILED'
              AND (n.issue_id = f.issue_id
                   OR (f.error_code IN ('PROCESSING_ERROR','RETRIES_EXHAUSTED','WORKER_LOST')
                       AND n.state = 'COMPLETED'))))               AS failed
        FROM {fm.TABLE} f
        WHERE f.intake_id = CAST(:i AS uuid)"""),  # noqa: S608 - constant table name
            {"i": str(iid)},
        )
    ).one()
    counts = {
        fm.STATE_RECEIVED: row.received,
        fm.STATE_QUEUED: row.queued,
        fm.STATE_PROCESSING: row.processing,
    }
    oldest = row.oldest
    failed = int(row.failed or 0)
    received = int(counts.get(fm.STATE_RECEIVED, 0))
    queued = int(counts.get(fm.STATE_QUEUED, 0))
    processing = int(counts.get(fm.STATE_PROCESSING, 0))
    pending = received + queued + processing
    notice = None
    if pending:
        notice = PENDING_NOTICE.format(n=pending)
    elif failed:
        notice = FAILED_NOTICE.format(n=failed)
    return {
        "store_present": True,
        "received": received,
        "queued": queued,
        "processing": processing,
        "pending": pending,
        "failed": failed,
        "oldest_pending_at": _iso(oldest),
        "reconciliation_current": not (pending or failed),
        "notice": notice,
    }


async def latest_by_issue(db, issue_ids) -> dict[str, dict[str, Any]]:
    """Newest follow-up per finding, for the exception ledger."""
    ids = [i for i in issue_ids if i is not None]
    if not ids:
        return {}
    try:
        if not await store_exists(db):
            return {}
    except Exception:  # noqa: BLE001
        return {}
    rows = (
        (
            await db.execute(
                select(F).where(F.issue_id.in_(ids)).order_by(F.issue_id, F.received_at.desc())
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out.setdefault(str(row.issue_id), dto(row))
    return out


async def list_for_intake(db, intake_id, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(F)
                .where(F.intake_id == intake_id)
                .order_by(F.received_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [dto(r) for r in rows]


# ── attribution, per record ──────────────────────────────────────────────────


def attribution_lookup() -> Callable[[Any, Any], Awaitable[dict[str, Any] | None]]:
    """`promote_delivery(human_attribution=...)`: who released THIS record.

    Queried per record at the moment it is promoted, never preloaded: a
    decision accepted while the drain is already running has its RECEIVED row
    committed no later than its release, so the lookup still finds it. A record
    nobody decided on gets None and keeps its natural SYSTEM reason code.
    """

    async def lookup(db, source_record_id) -> dict[str, Any] | None:
        if source_record_id is None:
            return None
        # The decision that RELEASED the record (it found the record HELD), not
        # merely the newest one: a later decision on another, non-holding
        # finding of the same record must not take the credit for the release.
        # Among several releases the newest wins; with none, the newest
        # decision about the record, as before.
        released_it = case((F.record_status_before == "HELD", 0), else_=1)
        row = (
            await db.execute(
                select(F)
                .where(F.source_record_id == source_record_id, F.state.in_(fm.PENDING_STATES))
                .order_by(released_it, F.received_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None or row.attribution != fm.ATTRIBUTION_HUMAN:
            return None
        return {
            "actor": row.actor,
            "actor_id": row.actor_id,
            "reason": f"Analyst decision {row.decision}: {row.decision_reason or ''}",
            "followup_id": str(row.id),
        }

    return lookup


# ── holds, per record, with values the database reads at write time ─────────
#
# `curation.recompute_hold_status` re-derives every record of a delivery from
# rows it read earlier and writes them back later. Two of those running at
# once - two reviewers, or a reviewer and the worker - can each overwrite what
# the other just committed (local synthetic burst, 2026-11-18: corrected
# records left CLEAN instead of CORRECTED). Under the flag neither a decision
# request nor the worker calls it. A decision concerns ONE record, so only that
# record is re-derived, by a single guarded UPDATE: the blocking set comes from
# the same `curation._blocking_by_record` (one definition of "held"), and the
# status and correction count are the row's values at the moment of the write.
#
# A release is additionally refused while the record carries a finding that is
# APPROVED but not yet applied: an approved correction that never landed must
# not let its record through.

HOLD_RELEASED_REASON = "All holding-severity issues and identifier conflicts resolved."
HOLD_APPROVED_UNAPPLIED_REASON = (
    "Held: another finding on this record has an approved correction that was "
    "never applied. Open that finding and complete the decision."
)


async def _guarded_release(db, intake_id, record_ids) -> list[Any]:
    """HELD -> CORRECTED/CLEAN for the given records, only where the row is
    still HELD, still unpromoted and has no approved-but-unapplied finding.
    The caller has already established that nothing blocks them. No commit."""
    from sqlalchemy import exists

    from app.tefca_registry.rce import models as m

    ids = [r for r in record_ids if r is not None]
    if not ids:
        return []
    C = m.RceCuratedRecord
    approved_unapplied = exists().where(
        m.RceIssue.source_record_id == C.source_record_id, m.RceIssue.resolution == "APPROVED"
    )
    result = await db.execute(
        update(C)
        .where(
            C.source_intake_id == intake_id,
            C.source_record_id.in_(ids),
            C.record_status == "HELD",
            C.canonical_entity_id.is_(None),
            ~approved_unapplied,
        )
        .values(
            record_status=case(
                (func.coalesce(C.correction_count, 0) > 0, "CORRECTED"), else_="CLEAN"
            ),
            status_reason=HOLD_RELEASED_REASON,
        )
        .returning(C.source_record_id)
        .execution_options(synchronize_session=False)
    )
    return [rid for (rid,) in result.all()]


async def has_approved_unapplied(db, source_record_id) -> bool:
    """Does this record carry a finding that is APPROVED but not yet applied?
    Such a record is never released (see `_guarded_release`)."""
    from app.tefca_registry.rce import models as m

    if source_record_id is None:
        return False
    return bool(
        (
            await db.execute(
                select(func.count())
                .select_from(m.RceIssue)
                .where(
                    m.RceIssue.source_record_id == source_record_id,
                    m.RceIssue.resolution == "APPROVED",
                )
            )
        ).scalar()
    )


#: Written on the RECEIVED row by `curation.apply_disposition` in the commit
#: that follows the decision's last write. Recovery reads it: a finding left
#: open by a decision that WAS recorded (ESCALATE, DEFER, REQUEST_EVIDENCE, a
#: non-conflict REJECT) cannot be told from an interrupted one by its
#: resolution alone.
DECISION_RECORDED_MARK = "decision_recorded"


def decision_marked_recorded(row) -> bool:
    return bool(row is not None and (row.decision_result or {}).get(DECISION_RECORDED_MARK))


async def close_after_request_error(
    row_id, issue_id, *, error_detail: str
) -> dict[str, Any] | None:
    """A request failed AFTER it may have committed. Same rule as the sweeper,
    applied at once: if the finding reached a terminal resolution the decision
    IS recorded, so only its delivery work is missing -> QUEUED, `recovered`
    (the decision is never replayed). Anything else is FAILED
    `DECISION_ERROR` and waits for a person. Fresh session; RECEIVED rows only.
    """
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import curation
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        recorded = False
        if issue_id is not None:
            issue = await db.get(m.RceIssue, issue_id)
            recorded = issue is not None and curation.is_terminal_resolution(issue.resolution)
        marked = decision_marked_recorded(await db.get(F, row_id))
        now = _now()
        values: dict[str, Any]
        if recorded:
            values = {"state": fm.STATE_QUEUED, "queued_at": now, "recovered": True}
        elif marked:
            # Recorded, and it left the finding open (ESCALATE, DEFER, ...):
            # such a decision releases nothing, so there is no delivery work.
            values = {"state": fm.STATE_NOT_REQUIRED, "completed_at": now, "recovered": True}
        else:
            values = {"state": fm.STATE_FAILED, "failed_at": now, "error_code": CODE_DECISION_ERROR}
        await db.execute(
            update(F)
            .where(F.id == row_id, F.state == fm.STATE_RECEIVED)
            .values(error_detail=(error_detail or "")[:500] or None, **values)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        row = await db.get(F, row_id)
        return dto(row) if row is not None else None


async def rederive_record_hold(db, intake_id, source_record_id) -> dict[str, Any]:
    """Re-derive the hold of ONE record after a decision about it. Commits.

    Same outcome `recompute_hold_status` would reach for this record, without
    touching any other: held if something still blocks it, released if nothing
    does (and no approved-but-unapplied finding remains), untouched if it is
    already promoted or rejected.
    """
    from app.tefca_registry.rce import curation
    from app.tefca_registry.rce import models as m

    C = m.RceCuratedRecord
    entry = (await curation._blocking_by_record(db, intake_id)).get(source_record_id)
    released: list[Any] = []
    held = 0
    if entry:
        result = await db.execute(
            update(C)
            .where(
                C.source_intake_id == intake_id,
                C.source_record_id == source_record_id,
                C.record_status.in_(("CLEAN", "CORRECTED")),
                C.canonical_entity_id.is_(None),
            )
            .values(
                record_status="HELD",
                status_reason=(
                    f"{entry['issues']} unresolved holding-severity issue(s) and "
                    f"{entry['conflicts']} unresolved identifier conflict(s). Held "
                    f"from promotion until resolved."
                ),
            )
            .execution_options(synchronize_session=False)
        )
        held = int(result.rowcount or 0)
    elif await has_approved_unapplied(db, source_record_id):
        # Nothing undecided blocks it, but another finding on this record is
        # APPROVED and its correction never landed. `apply_correction` has just
        # marked the record CORRECTED; left so, the worker would promote it
        # with that approved correction missing. Held until a person completes
        # the approved decision (the same rule `_guarded_release` applies).
        result = await db.execute(
            update(C)
            .where(
                C.source_intake_id == intake_id,
                C.source_record_id == source_record_id,
                C.record_status.in_(("CLEAN", "CORRECTED")),
                C.canonical_entity_id.is_(None),
            )
            .values(record_status="HELD", status_reason=HOLD_APPROVED_UNAPPLIED_REASON)
            .execution_options(synchronize_session=False)
        )
        held = int(result.rowcount or 0)
    else:
        released = await _guarded_release(db, intake_id, [source_record_id])
    await db.commit()
    return {
        "scope": "RECORD",
        "source_record_id": str(source_record_id),
        "status_changed": held + len(released),
        "released": [str(r) for r in released],
        "blocking": dict(entry) if entry else None,
    }


#: How long a decision waits for another decision on the same delivery before
#: it is refused. Decision chains are seconds long (they no longer rewrite the
#: whole delivery), and the Save's own transport timeout is 180 s.
DECISION_SLOT_WAIT_SECONDS = 25.0
DECISION_SLOT_POLL_SECONDS = 0.1
DELIVERY_DECISION_BUSY_CODE = "DELIVERY_DECISION_BUSY"
DELIVERY_DECISION_BUSY_MESSAGE = (
    "Another decision for this delivery is being recorded. Nothing was recorded "
    "for this request. Wait a few seconds, then save again."
)


def _decision_key(intake_id) -> str:
    try:
        intake_id = uuid.UUID(str(intake_id))
    except (ValueError, TypeError):
        pass
    return f"delivery-decision:{intake_id}"


async def acquire_decision_slot(conn, intake_id) -> bool:
    """Take the delivery's one decision slot on `conn`, waiting a bounded time.

    Polls `pg_try_advisory_lock` rather than issuing a blocking lock statement,
    so a waiting request never sits inside the database and gives up cleanly.
    It is a DIFFERENT lock from the worker's: a decision waits for other
    decisions (seconds), never for promotion and reconciliation.
    """
    key = _decision_key(intake_id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + float(DECISION_SLOT_WAIT_SECONDS)
    while True:
        got = bool(
            (
                await conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": key}
                )
            ).scalar()
        )
        if got:
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(DECISION_SLOT_POLL_SECONDS)


async def release_decision_slot(conn, intake_id) -> None:
    await conn.execute(
        text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": _decision_key(intake_id)}
    )


@asynccontextmanager
async def decision_slot(intake_id):
    """The decision slot on its own connection, for callers that hold no
    per-finding guard connection. Yields whether it was acquired."""
    from app.core.database import engine

    conn = await engine.connect()
    acquired = False
    try:
        acquired = await acquire_decision_slot(conn, intake_id)
        yield acquired
    finally:
        if acquired:
            try:
                await release_decision_slot(conn, intake_id)
            except Exception:  # noqa: BLE001 - never return a still-locked conn
                await conn.invalidate()
        await conn.close()


# ── the delivery-level lock ──────────────────────────────────────────────────


def _consequence_key(intake_id) -> str:
    """ONE key for both modes: the worker and the manual routes take it
    exclusively, a synchronous save takes it shared."""
    try:
        intake_id = uuid.UUID(str(intake_id))
    except (ValueError, TypeError):
        pass
    return f"delivery-consequence:{intake_id}"


class DeliveryLock:
    """A session-level advisory lock on a dedicated connection.

    The holder does its work THROUGH that connection (`session`), so the lock
    and the work cannot be separated: see the module docstring.
    """

    def __init__(self, conn, key: str, acquired: bool):
        self._conn = conn
        self._key = key
        self.acquired = acquired
        self._mutex = asyncio.Lock()

    async def held(self) -> bool:
        """Is the lock connection still alive? If it died, Postgres released
        the lock and another worker may be running: do not persist.

        Called BETWEEN sessions only. The probe's own transaction is ended at
        once: a session bound to a connection that is already inside a
        transaction would not own it, and its commit would make nothing
        durable."""
        if not self.acquired:
            return False
        try:
            async with self._mutex:
                await self._conn.execute(text("SELECT 1"))
                await self._conn.rollback()
            return True
        except Exception:  # noqa: BLE001
            return False

    def session(self):
        """A fresh ORM session whose every statement runs on the LOCK
        connection. Refuses if the connection is inside a transaction: the
        session must begin, and therefore commit, its own."""
        from sqlalchemy.ext.asyncio import AsyncSession

        if not self.acquired:
            raise RuntimeError("the delivery lock is not held")
        if self._conn.in_transaction():
            raise RuntimeError("the delivery lock connection is inside a transaction")
        return AsyncSession(bind=self._conn, expire_on_commit=False)


@asynccontextmanager
async def delivery_lock(intake_id):
    """Try to take the one promotion/reconciliation slot for a delivery.

    NEVER blocks: a caller that cannot take it is told so and decides what to
    do (the worker tries again next tick; a route answers 409). Blocking inside
    a request would recreate the wait this module exists to remove.
    """
    from app.core.database import engine

    key = _consequence_key(intake_id)
    conn = await engine.connect()
    acquired = False
    try:
        acquired = bool(
            (
                await conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": key}
                )
            ).scalar()
        )
        # End the transaction that statement began. The lock is session-level
        # and survives it; the sessions bound to this connection must each
        # begin a transaction of their own.
        await conn.commit()
        yield DeliveryLock(conn, key, acquired)
    finally:
        if acquired:
            try:
                await conn.rollback()
                released = (
                    await conn.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": key}
                    )
                ).scalar()
                await conn.commit()
                if not released:
                    await conn.invalidate()
            except Exception:  # noqa: BLE001 - never return a still-locked conn
                await conn.invalidate()
        await conn.close()


async def acquire_synchronous_slot(conn, intake_id) -> bool:
    """The delivery lock in SHARED mode on `conn`, for a synchronous save.
    Never blocks: False means the worker (or a manual promotion or snapshot)
    holds the delivery."""
    return bool(
        (
            await conn.execute(
                text("SELECT pg_try_advisory_lock_shared(hashtextextended(:k, 0))"),
                {"k": _consequence_key(intake_id)},
            )
        ).scalar()
    )


async def release_synchronous_slot(conn, intake_id) -> None:
    """Raises when Postgres did not release it, so the caller invalidates the
    connection instead of returning it to the pool still holding the lock (a
    plain, non-shared unlock would do exactly that, silently)."""
    released = (
        await conn.execute(
            text("SELECT pg_advisory_unlock_shared(hashtextextended(:k, 0))"),
            {"k": _consequence_key(intake_id)},
        )
    ).scalar()
    if not released:
        raise RuntimeError("the shared delivery lock was not held on this connection")


@asynccontextmanager
async def synchronous_slot(intake_id):
    """`acquire_synchronous_slot` on its own connection, for a synchronous
    path that holds no per-finding guard connection. Yields whether it was
    acquired."""
    from app.core.database import engine

    conn = await engine.connect()
    acquired = False
    try:
        acquired = await acquire_synchronous_slot(conn, intake_id)
        yield acquired
    finally:
        if acquired:
            try:
                await release_synchronous_slot(conn, intake_id)
            except Exception:  # noqa: BLE001 - never return a still-locked conn
                await conn.invalidate()
        await conn.close()


async def _finding_guard_free(issue_id) -> bool:
    """Is no request currently deciding this finding? (The route holds
    `issue-disposition:{id}` for its whole duration.)"""
    from app.core.database import engine

    key = f"issue-disposition:{issue_id}"
    conn = await engine.connect()
    try:
        got = bool(
            (
                await conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": key}
                )
            ).scalar()
        )
        if got:
            await conn.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": key}
            )
        return got
    finally:
        await conn.close()


# ── the worker ───────────────────────────────────────────────────────────────


class _Heartbeat:
    """Keeps a batch's rows fresh while a long stage runs. On its OWN pooled
    session: the lock connection is busy with the run's work and must not be
    used from a second task."""

    def __init__(self, batch_id, lock: DeliveryLock):
        self.batch_id = batch_id
        self.lock = lock
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self):
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except Exception as exc:  # noqa: BLE001 - a dead pulse must not fail the batch
                logger.warning("disposition follow-up heartbeat ended with %s", type(exc).__name__)

    async def _run(self):
        from app.core.database import async_session_maker

        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
                return
            except TimeoutError:
                pass
            try:
                async with async_session_maker() as db:
                    await db.execute(
                        update(F)
                        .where(F.batch_id == self.batch_id, F.state == fm.STATE_PROCESSING)
                        .values(heartbeat_at=_now())
                    )
                    await db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("disposition follow-up heartbeat failed: %s", type(exc).__name__)


async def _next_intake(db) -> uuid.UUID | None:
    """The delivery with the oldest queued row whose registration job is not
    itself running (its pipeline owns promotion until it finishes)."""
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    candidates = (
        await db.execute(
            select(F.intake_id, func.min(F.queued_at).label("oldest"))
            .where(F.state == fm.STATE_QUEUED)
            .group_by(F.intake_id)
            .order_by(text("oldest ASC NULLS FIRST"))
            .limit(20)
        )
    ).all()
    for intake_id, _oldest in candidates:
        busy = (
            await db.execute(
                select(func.count())
                .select_from(RceDeliveryJob)
                .where(
                    RceDeliveryJob.source_intake_id == intake_id,
                    RceDeliveryJob.state.in_(RceDeliveryJob.ACTIVE_STATES),
                )
            )
        ).scalar() or 0
        if not busy:
            return intake_id
    return None


async def _claim(db, intake_id, batch_id, *, include_failed: bool = False) -> int:
    """Move every QUEUED row of the delivery into this batch.

    `include_failed` (the FIRST claim of a batch only) also takes rows whose
    earlier processing FAILED. Their decisions are recorded and their records
    are released, so this run's promotion would promote those records anyway;
    taking the rows into the batch means each record is released, promoted
    under its own reviewer's attribution and covered by the snapshot like any
    other, instead of being promoted as SYSTEM behind a row left FAILED. If
    this run fails too they return to FAILED (their attempts are exhausted).
    """
    from sqlalchemy import and_, or_

    now = _now()
    claimable = F.state == fm.STATE_QUEUED
    if include_failed:
        claimable = or_(
            claimable, and_(F.state == fm.STATE_FAILED, F.error_code.in_(PROCESSING_CODES))
        )
    result = await db.execute(
        update(F)
        .where(F.intake_id == intake_id, claimable)
        .values(
            state=fm.STATE_PROCESSING,
            batch_id=batch_id,
            started_at=func.coalesce(F.started_at, now),
            heartbeat_at=now,
            attempt_count=F.attempt_count + 1,
            failed_at=None,
            error_code=None,
            error_detail=None,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return int(result.rowcount or 0)


async def _others_pending(db, intake_id, batch_id) -> int:
    """Decisions for this delivery that are accepted but not in this batch."""
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(F)
                .where(
                    F.intake_id == intake_id,
                    F.state.in_((fm.STATE_RECEIVED, fm.STATE_QUEUED)),
                )
            )
        ).scalar()
        or 0
    )


def _owned_update(row_id, batch_id):
    """UPDATE of one row that applies only while the row is still PROCESSING
    in THIS batch. Completion, release and the reaper all write through a
    condition like this, so none of them can overwrite what another committed
    after the row was read."""
    return (
        update(F)
        .where(F.id == row_id, F.batch_id == batch_id, F.state == fm.STATE_PROCESSING)
        .execution_options(synchronize_session=False)
    )


async def _release_batch(
    batch_id, *, exc: BaseException | None, stage: str, requeue_without_penalty: bool = False
) -> None:
    """A batch that did not complete: back to QUEUED while attempts remain,
    FAILED with a safe code after that. Never COMPLETED."""
    from app.core.database import async_session_maker

    detail = f"{stage}:{type(exc).__name__}" if exc is not None else stage
    async with async_session_maker() as db:
        rows = (
            (
                await db.execute(
                    select(F).where(F.batch_id == batch_id, F.state == fm.STATE_PROCESSING)
                )
            )
            .scalars()
            .all()
        )
        now = _now()
        for row in rows:
            if requeue_without_penalty:
                values: dict[str, Any] = {
                    "state": fm.STATE_QUEUED,
                    "attempt_count": max(0, (row.attempt_count or 1) - 1),
                    "batch_id": None,
                }
            elif (row.attempt_count or 0) < MAX_ATTEMPTS:
                values = {
                    "state": fm.STATE_QUEUED,
                    "batch_id": None,
                    "error_code": CODE_PROCESSING_ERROR,
                    "error_detail": detail[:500],
                }
            else:
                values = {
                    "state": fm.STATE_FAILED,
                    "failed_at": now,
                    "error_code": CODE_RETRIES_EXHAUSTED,
                    "error_detail": detail[:500],
                }
            # Only while the row is still this batch's: a reaper that already
            # took it back is not overwritten.
            await db.execute(_owned_update(row.id, batch_id).values(**values))
        await db.commit()


async def _complete_batch(db, intake_id, batch_id, snapshot, *, commit: bool = True) -> int:
    """Mark the batch COMPLETED against the snapshot that covers it.

    `commit=False` leaves the transaction to the caller, which commits the
    snapshot and this completion together."""
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import traceability_models as tm

    rows = (
        (await db.execute(select(F).where(F.batch_id == batch_id, F.state == fm.STATE_PROCESSING)))
        .scalars()
        .all()
    )
    now = _now()
    completed = 0
    for row in rows:
        values: dict[str, Any] = {
            "state": fm.STATE_COMPLETED,
            "completed_at": now,
            "heartbeat_at": now,
            "error_code": None,
            "error_detail": None,
        }
        if row.source_record_id is not None:
            values["record_status_after"] = (
                await db.execute(
                    select(m.RceCuratedRecord.record_status)
                    .where(m.RceCuratedRecord.source_record_id == row.source_record_id)
                    .limit(1)
                )
            ).scalar_one_or_none() or row.record_status_after
            values["disposition_event_id"] = (
                await db.execute(
                    select(tm.RceDispositionEvent.id)
                    .where(tm.RceDispositionEvent.source_record_id == row.source_record_id)
                    .order_by(tm.RceDispositionEvent.sequence.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if snapshot is not None:
            values["snapshot_id"] = snapshot.id
            values["snapshot_passed"] = bool(snapshot.passed)
        result = await db.execute(_owned_update(row.id, batch_id).values(**values))
        completed += int(result.rowcount or 0)
    # Rows of OTHER records that are FAILED stay FAILED and retryable: this
    # run released and promoted only its own batch's records, so it does not
    # vouch for theirs.
    if commit:
        await db.commit()
    return completed


#: Test seam: called with the stage name just before each stage runs. A test
#: makes it raise to prove a partial failure is retried and not reapplied.
_stage_hook: Callable[[str], Awaitable[None]] | None = None


async def _stage(name: str) -> None:
    if _stage_hook is not None:
        await _stage_hook(name)


async def _run_batch(intake_id, batch_id, lock: DeliveryLock) -> dict[str, Any]:
    """One coalesced run. EVERY round and every reconciliation uses a FRESH
    session: sessions do not expire objects on commit, so a session kept
    across rounds would hand back the previous round's copy of a record that a
    request has since corrected.

    Every one of those sessions is bound to the LOCK connection
    (`lock.session()`), so nothing here can be written by a run whose lock is
    gone. The snapshot and the completion of the rows it covers are ONE
    transaction: a crash leaves both or neither, never a snapshot that a
    re-run would repeat."""
    from app.tefca_registry.rce import curation, promotion, reconciliation
    from app.tefca_registry.rce.delivery_jobs import job_for_intake

    lookup = attribution_lookup()
    stage = "HOLD_RECOMPUTE"
    out: dict[str, Any] = {
        "intake_id": str(intake_id),
        "batch_id": str(batch_id),
        "drain_rounds": 0,
        "reconcile_rounds": 0,
    }
    try:

        async def drain() -> None:
            nonlocal stage
            for round_no in range(MAX_DRAIN_ROUNDS):
                out["drain_rounds"] += 1
                # A dead lock connection means Postgres released the lock and
                # another holder may be writing this delivery: stop before the
                # next write stage rather than at the snapshot only.
                if not await lock.held():
                    raise RuntimeError("delivery lock lost during the run")
                async with lock.session() as db:
                    # Holds first, for THIS BATCH'S records only. A promotion
                    # reads what blocks each record once, at its start; one
                    # that was already running when a decision landed may have
                    # put that record back to HELD. Its decision is finished
                    # now (its row is in this batch), so release it again -
                    # guarded, and never past an approved-but-unapplied finding.
                    stage = "HOLD_RELEASE"
                    await _stage(stage)
                    records = [
                        rid
                        for (rid,) in (
                            await db.execute(
                                select(F.source_record_id).where(
                                    F.batch_id == batch_id,
                                    F.state == fm.STATE_PROCESSING,
                                    F.source_record_id.isnot(None),
                                )
                            )
                        ).all()
                    ]
                    if records:
                        blocking = await curation._blocking_by_record(db, intake_id)
                        await _guarded_release(
                            db, intake_id, [r for r in records if r not in blocking]
                        )
                        await db.commit()
                    stage = "PROMOTION"
                    await _stage(stage)
                    await promotion.promote_delivery(
                        db,
                        intake_id,
                        actor="SYSTEM",
                        human_attribution=lookup,
                        lock_candidates=True,
                    )
                    # Rows claimed here are processed by the NEXT round. The
                    # last round therefore claims nothing: later arrivals stay
                    # QUEUED for the next batch instead of being marked
                    # completed by a run that never released or promoted them.
                    if round_no == MAX_DRAIN_ROUNDS - 1:
                        return
                    if not await _claim(db, intake_id, batch_id):
                        return

        await drain()

        async with lock.session() as db:
            job = await job_for_intake(db, intake_id)
        full = None
        if job is not None:
            others = 0
            for _ in range(RECONCILE_SETTLE_ROUNDS + 1):
                out["reconcile_rounds"] += 1
                stage = "RECONCILIATION"
                await _stage(stage)
                async with lock.session() as db:
                    full = await reconciliation.reconcile_delivery(db, intake_id)
                    others = await _others_pending(db, intake_id, batch_id)
                if full.get("passed"):
                    break
                # A decision still being applied in its own request leaves its
                # record released-not-promoted for a moment, which fails the
                # equation honestly but not finally. Let it land, take it into
                # the batch, and reconcile again. No check is altered.
                if not others:
                    break
                await asyncio.sleep(RECONCILE_SETTLE_SECONDS)
                await drain()
            if not full.get("passed") and others:
                out["outcome"] = "DEFERRED_OTHERS_PENDING"
                await _release_batch(
                    batch_id, exc=None, stage="RECONCILIATION_WAIT", requeue_without_penalty=True
                )
                return out
            stage = "SNAPSHOT"
            await _stage(stage)
        if not await lock.held():
            raise RuntimeError("delivery lock lost before the snapshot was persisted")
        snapshot = None
        async with lock.session() as db:
            covered = (
                await db.execute(
                    select(F.id, F.actor).where(
                        F.batch_id == batch_id, F.state == fm.STATE_PROCESSING
                    )
                )
            ).all()
            ids = [str(i) for (i, _actor) in covered]
            actors = {actor for (_i, actor) in covered}
            if not ids:
                # The reaper took every row back. Nothing is this run's to
                # complete, so it must not leave a snapshot behind either.
                raise RuntimeError("the batch is no longer owned by this run")
            if job is not None:
                snapshot = await reconciliation.persist_snapshot(
                    db,
                    intake_id,
                    full,
                    job_id=job.id,
                    actor=(next(iter(actors)) if len(ids) == 1 and actors else "SYSTEM"),
                    trigger="DISPOSITION",
                    extra_evidence={
                        "disposition_followups": {
                            "batch_id": str(batch_id),
                            "followup_ids": ids,
                            "decisions_covered": len(ids),
                        }
                    },
                    commit=False,
                )
            # Same transaction as the snapshot. A failure from here to the
            # commit rolls the snapshot back with it; the re-run writes ONE.
            stage = "COMPLETE"
            await _stage(stage)
            completed = await _complete_batch(db, intake_id, batch_id, snapshot, commit=False)
            if completed != len(ids):
                # A row left the batch after the snapshot named it. The
                # snapshot would claim a decision this run did not complete.
                await db.rollback()
                raise RuntimeError("batch ownership changed before completion was recorded")
            await db.commit()
        if snapshot is not None:
            out["snapshot_id"] = str(snapshot.id)
            out["snapshot_passed"] = bool(snapshot.passed)
            logger.info(
                "reconciliation snapshot persisted",
                extra={
                    "job_id": str(job.id),
                    "passed": bool(snapshot.passed),
                    "trigger": "DISPOSITION",
                },
            )
        out["completed"] = completed
        out["outcome"] = "COMPLETED"
        return out
    except Exception as exc:  # noqa: BLE001 - recorded on the rows, retried
        logger.warning(
            "disposition follow-up batch failed",
            extra={"stage": stage, "error_class": type(exc).__name__},
        )
        await _release_batch(batch_id, exc=exc, stage=stage)
        out["outcome"] = "RETRY_OR_FAILED"
        out["failed_stage"] = stage
        out["error_class"] = type(exc).__name__
        return out


async def process_next() -> dict[str, Any] | None:
    """Claim and process one delivery's queued follow-ups. None when idle."""
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        intake_id = await _next_intake(db)
    if intake_id is None:
        return None
    async with delivery_lock(intake_id) as lock:
        if not lock.acquired:
            return {"intake_id": str(intake_id), "outcome": "DELIVERY_BUSY"}
        batch_id = uuid.uuid4()
        async with lock.session() as db:
            claimed = await _claim(db, intake_id, batch_id, include_failed=True)
        if not claimed:
            return None
        beat = _Heartbeat(batch_id, lock)
        await beat.start()
        started = _now()
        try:
            result = await _run_batch(intake_id, batch_id, lock)
        finally:
            await beat.stop()
        result["seconds"] = round((_now() - started).total_seconds(), 3)
        logger.info(
            "disposition follow-up batch finished",
            extra={
                "outcome": result.get("outcome"),
                "duration_ms": int(result["seconds"] * 1000),
            },
        )
        return result


async def drain_all(max_batches: int = 50) -> list[dict[str, Any]]:
    """Process until nothing is queued. For tests and operator scripts."""
    results: list[dict[str, Any]] = []
    for _ in range(max_batches):
        result = await process_next()
        if result is None:
            break
        results.append(result)
        if result.get("outcome") == "DELIVERY_BUSY":
            break
    return results


# ── recovery ─────────────────────────────────────────────────────────────────


async def reap_stale_processing(
    threshold_seconds: int = STALE_PROCESSING_SECONDS,
) -> list[dict[str, Any]]:
    """PROCESSING rows whose worker went silent: retried while attempts remain.

    Safe because the delivery-level step is state-derived: the next run
    promotes only what still needs promoting and re-reconciles. `batch_id` is
    cleared, so a worker that was merely slow cannot mark the rows COMPLETED
    after the reaper has spoken.
    """
    from app.core.database import async_session_maker

    cutoff = _now() - timedelta(seconds=threshold_seconds)
    reaped: list[dict[str, Any]] = []
    async with async_session_maker() as db:
        rows = (
            (
                await db.execute(
                    select(F).where(F.state == fm.STATE_PROCESSING, F.heartbeat_at < cutoff)
                )
            )
            .scalars()
            .all()
        )
        now = _now()
        for row in rows:
            if (row.attempt_count or 0) < MAX_ATTEMPTS:
                values: dict[str, Any] = {"state": fm.STATE_QUEUED, "error_code": CODE_WORKER_LOST}
            else:
                values = {
                    "state": fm.STATE_FAILED,
                    "failed_at": now,
                    "error_code": CODE_RETRIES_EXHAUSTED,
                }
            # Only if the row is STILL processing, still in the batch that was
            # observed and still silent: a worker that completed it, or beat
            # again, between the read above and this write is left alone.
            result = await db.execute(
                update(F)
                .where(
                    F.id == row.id,
                    F.state == fm.STATE_PROCESSING,
                    F.batch_id.is_not_distinct_from(row.batch_id),
                    F.heartbeat_at < cutoff,
                )
                .values(error_detail="reaped: no heartbeat", batch_id=None, **values)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount:
                reaped.append({"id": str(row.id), "state": values["state"]})
        if reaped:
            await db.commit()
            logger.warning("reaped %d stale disposition follow-up row(s)", len(reaped))
    return reaped


async def sweep_received(threshold_seconds: int = STALE_RECEIVED_SECONDS) -> list[dict[str, Any]]:
    """RECEIVED rows whose request is gone. The decision is NEVER replayed.

    The request died between its first commit and its last. What it committed
    stands; what it did not commit is not invented:

      * the finding reached a terminal resolution (or the row is an identifier
        decision, which commits atomically with its row): the decision is
        complete, only the state-derived delivery step is missing -> QUEUED,
        `recovered`;
      * anything else - including a finding left APPROVED with its correction
        never applied - is FAILED `DECISION_INTERRUPTED` and waits for a
        person. In particular nothing here releases a record whose approved
        correction was not applied.
    """
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import curation
    from app.tefca_registry.rce import models as m

    cutoff = _now() - timedelta(seconds=threshold_seconds)
    swept: list[dict[str, Any]] = []
    async with async_session_maker() as db:
        rows = (
            (
                await db.execute(
                    select(F).where(F.state == fm.STATE_RECEIVED, F.received_at < cutoff)
                )
            )
            .scalars()
            .all()
        )
        now = _now()
        for row in rows:
            if row.issue_id is not None and not await _finding_guard_free(row.issue_id):
                continue  # its request is still running
            complete = row.kind == fm.KIND_IDENTIFIER_DECISION
            if not complete and row.issue_id is not None:
                issue = await db.get(m.RceIssue, row.issue_id)
                complete = issue is not None and curation.is_terminal_resolution(issue.resolution)
            if complete:
                row.state = fm.STATE_QUEUED
                row.queued_at = now
                row.recovered = True
            elif decision_marked_recorded(row):
                # Recorded, finding left open by design: nothing to process.
                row.state = fm.STATE_NOT_REQUIRED
                row.completed_at = now
                row.recovered = True
            else:
                row.state = fm.STATE_FAILED
                row.failed_at = now
                row.error_code = CODE_DECISION_INTERRUPTED
                row.error_detail = "swept: request ended without settling the row"
            swept.append({"id": str(row.id), "state": row.state})
        if swept:
            await db.commit()
            logger.warning("swept %d interrupted disposition request row(s)", len(swept))
    return swept


async def retry(db, row_id, *, actor: str) -> dict[str, Any]:
    """Re-queue the delivery-level step of a FAILED row. Explicit, by a person
    allowed to decide findings. It never re-applies the decision."""
    from app.tefca_registry.rce import models as m

    row = await db.get(F, row_id)
    if row is None:
        raise FollowupRefused("No such processing record.")
    if row.state != fm.STATE_FAILED:
        raise FollowupRefused(f"Processing is {row.state}; only FAILED can be retried.")
    if row.issue_id is not None:
        issue = await db.get(m.RceIssue, row.issue_id)
        if issue is not None and issue.resolution == "APPROVED":
            raise FollowupRefused(RETRY_REFUSED_APPROVED)
    # Retry processes the delivery work of a RECORDED decision, and the only
    # rows that are FAILED with a recorded decision are those whose PROCESSING
    # failed (a recorded decision whose request died is queued by recovery, not
    # left FAILED). The finding's CURRENT resolution proves nothing about this
    # row: a later decision may have settled a finding this row's own request
    # never did, and completing this row would report work it never applied.
    # The same rule as `dto()["can_retry"]`.
    if row.error_code not in PROCESSING_CODES:
        raise FollowupRefused(RETRY_REFUSED_NOT_RECORDED)
    # One atomic FAILED -> QUEUED: a second retry, or a worker that already
    # claimed the row, finds nothing to change and is told so.
    result = await db.execute(
        update(F)
        .where(F.id == row_id, F.state == fm.STATE_FAILED)
        .values(
            state=fm.STATE_QUEUED,
            queued_at=_now(),
            attempt_count=0,
            batch_id=None,
            failed_at=None,
            error_code=None,
            error_detail=f"retry requested by {actor}"[:500],
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if not result.rowcount:
        raise FollowupRefused("Processing was already retried or is no longer FAILED.")
    await db.refresh(row)
    return dto(row)


# ── scheduler ────────────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS = 3
REAP_INTERVAL_SECONDS = 60


async def _poll_tick() -> None:
    try:
        if not await _store_ready():
            return
        await process_next()
    except Exception as exc:  # noqa: BLE001 - a tick must not stop the scheduler
        logger.error("disposition follow-up poll tick error: %s", type(exc).__name__)


async def _reap_tick() -> None:
    try:
        if not await _store_ready():
            return
        await reap_stale_processing()
        await sweep_received()
    except Exception as exc:  # noqa: BLE001
        logger.error("disposition follow-up reap tick error: %s", type(exc).__name__)


_store_checked: bool | None = None


async def _store_ready() -> bool:
    """The table exists (the migration was applied). Cached once true; while
    it is absent the ticks are no-ops, so the code can ship before the
    migration without a scheduler error every few seconds."""
    global _store_checked
    if _store_checked:
        return True
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        _store_checked = await store_exists(db)
    return bool(_store_checked)


def register_with_scheduler(scheduler) -> None:
    """Two interval jobs on the existing delivery scheduler. No new service."""
    from apscheduler.triggers.interval import IntervalTrigger

    defaults = dict(coalesce=True, misfire_grace_time=60, replace_existing=True, max_instances=1)
    scheduler.add_job(
        _poll_tick,
        IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
        id="rce_disposition_followup_poller",
        name="Analyst-decision delivery processing poller",
        **defaults,
    )
    scheduler.add_job(
        _reap_tick,
        IntervalTrigger(seconds=REAP_INTERVAL_SECONDS),
        id="rce_disposition_followup_reaper",
        name="Analyst-decision delivery processing reaper",
        **defaults,
    )
