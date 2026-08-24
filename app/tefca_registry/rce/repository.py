"""
Area 1 repository — create and read. There is no update, and no delete.

IMMUTABILITY IS ENFORCED, NOT ASSERTED
──────────────────────────────────────
Calling a table immutable in a docstring stops nobody. Area 1 is protected at
four layers, each of which catches what the previous one misses:

  1. THIS MODULE exposes create and read only. There is no function here that
     issues an UPDATE or a DELETE against an intake or a source record, so the
     ordinary path cannot mutate one even by mistake.
  2. THE API has no PUT, PATCH or DELETE route for Area 1 — see routes.py.
  3. THE DATABASE ROLE should lack UPDATE/DELETE on both tables.
     `immutability_grants_sql()` emits the exact statements; `verify_immutable()`
     probes the live database and reports whether they are actually in force.
     This is the layer that survives a future developer adding a write path.
  4. AUDIT — `record_mutation_attempt()` writes any attempt to the TEFCA audit
     trail before refusing it, so an attempt is visible even when it fails.

Layer 3 is deliberately reported rather than assumed. A deployment that has not
applied the grants is not secretly safe because layers 1 and 2 hold; it is one
code change away from silent mutation, and the reconciliation report says so.

REVALIDATION
`verify_record_hashes()` re-computes the SHA-256 of stored raw lines and compares
against what was recorded at intake. That turns "we believe Area 1 is unchanged"
into something checkable at any time, which is the whole point of storing the
hash in the first place.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select, text

from app.tefca_registry.rce import models as m

logger = logging.getLogger(__name__)

#: Tables that must never accept UPDATE or DELETE from the application role.
IMMUTABLE_TABLES = ("rce_source_intakes", "rce_source_records")

#: The role that should OWN Area 1. Must not be a role the application can
#: authenticate as — an owner can re-grant to itself, so leaving ownership with
#: the application role makes any revoke self-reversible.
OWNER_ROLE = "docuaction_owner"

#: The ONLY columns on rce_source_records the application may update.
#:
#: Fourteen of the sixteen columns are immutable evidence; these two are
#: workflow state, written once by `promotion.promote_delivery`. They are the
#: entire reason a blanket REVOKE UPDATE would break the pipeline, and the
#: reason a column-level grant is the right instrument.
MUTABLE_WORKFLOW_COLUMNS = ("promotion_status", "canonical_entity_id")

#: Columns on rce_source_records that carry delivered evidence. The application
#: must hold no UPDATE privilege on any of them.
IMMUTABLE_EVIDENCE_COLUMNS = (
    "id", "source_intake_id", "line_number", "raw_line", "parsed",
    "record_sha256", "source_rce_id", "tefcaid", "hcid", "npi", "field_count",
    "parse_status", "parse_note", "created_at",
)


class Area1ImmutabilityViolation(RuntimeError):
    """An attempt was made to modify Area 1. Refused and audited."""


def immutability_grants_sql(role: str = "docuaction") -> List[str]:
    """The exact grants that make Area 1 immutable at the database layer.

    Emitted rather than executed here: revoking privileges is a DBA action with
    deployment consequences, and a migration that silently rewrote the
    application role's rights would be a surprise nobody signed off on. The
    migration applies these; this function is what it applies and what the
    verifier checks for.
    """
    statements: List[str] = []

    # OWNERSHIP MUST LEAVE THE APPLICATION ROLE FIRST, OR THE REVOKE IS A
    # SUGGESTION. `docuaction` currently OWNS both Area 1 tables, and a
    # PostgreSQL owner can re-GRANT to itself at any time; ALTER and DROP are
    # inherent to ownership and cannot be revoked at all. Without this step the
    # revoke guards against an accidental code path but not against intent.
    statements.append(
        f"-- Prerequisite: {OWNER_ROLE} must exist and must NOT be a role the "
        f"application can authenticate as.")
    for table in IMMUTABLE_TABLES:
        statements.append(f"ALTER TABLE {table} OWNER TO {OWNER_ROLE};")

    for table in IMMUTABLE_TABLES:
        statements.append(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {role};")
        statements.append(f"GRANT SELECT, INSERT ON {table} TO {role};")

    # COLUMN-LEVEL GRANT — the part that makes this safe to apply today.
    #
    # A blanket REVOKE UPDATE on rce_source_records BREAKS PROMOTION:
    # `promotion.promote_delivery` legitimately writes `promotion_status` and
    # `canonical_entity_id` after the entities are already committed, so the
    # revoke would fail mid-transaction and leave Area 1 markers out of step
    # with Area 2. PostgreSQL evaluates privileges on the COLUMNS an UPDATE
    # names, so granting exactly those two keeps promotion working while every
    # evidence column becomes unwritable by the application.
    columns = ", ".join(MUTABLE_WORKFLOW_COLUMNS)
    statements.append(
        f"GRANT UPDATE ({columns}) ON rce_source_records TO {role};")
    return statements


async def verify_immutable(db, role: Optional[str] = None) -> Dict[str, Any]:
    """Probe whether the database actually withholds UPDATE/DELETE on Area 1.

    Reports the truth rather than the intention. `enforced` is False when the
    current role still holds the privilege — which is a finding, not an error,
    and appears in the reconciliation report as one.
    """
    result: Dict[str, Any] = {"role": role, "tables": {}, "enforced": True,
                              "checked": True}
    try:
        current = role or (await db.execute(select(func.current_user()))).scalar()
        result["role"] = current
        for table in IMMUTABLE_TABLES:
            row = (await db.execute(text(
                "SELECT "
                "  has_table_privilege(:role, :tbl, 'UPDATE') AS can_update, "
                "  has_table_privilege(:role, :tbl, 'DELETE') AS can_delete, "
                "  has_table_privilege(:role, :tbl, 'INSERT') AS can_insert"
            ), {"role": current, "tbl": table})).mappings().first()
            entry = dict(row) if row else {}

            # COLUMN-LEVEL PROBE — without this the table-level answer is
            # misleading. `has_table_privilege(..., 'UPDATE')` returns TRUE
            # whenever the role can update ANY column, so the correct hardened
            # configuration (UPDATE granted on promotion_status and
            # canonical_entity_id only) would be reported as unenforced and be
            # indistinguishable from no enforcement at all.
            if table == "rce_source_records":
                writable = []
                for column in IMMUTABLE_EVIDENCE_COLUMNS + MUTABLE_WORKFLOW_COLUMNS:
                    can = (await db.execute(text(
                        "SELECT has_column_privilege(:role, :tbl, :col, 'UPDATE') AS w"
                    ), {"role": current, "tbl": table, "col": column})).scalar()
                    if can:
                        writable.append(column)
                entry["updatable_columns"] = writable
                evidence_writable = [c for c in writable
                                     if c in IMMUTABLE_EVIDENCE_COLUMNS]
                entry["evidence_columns_writable"] = evidence_writable
                entry["workflow_columns_writable"] = [
                    c for c in writable if c in MUTABLE_WORKFLOW_COLUMNS]
                # Immutable means: no EVIDENCE column is writable and the table
                # cannot be deleted from. Workflow columns being writable is the
                # intended state, not a violation.
                entry["immutable"] = not (evidence_writable or entry.get("can_delete"))
                entry["workflow_writable_as_designed"] = (
                    set(entry["workflow_columns_writable"]) == set(MUTABLE_WORKFLOW_COLUMNS))
            else:
                entry["immutable"] = not (entry.get("can_update")
                                          or entry.get("can_delete"))

            result["tables"][table] = entry
            if not entry["immutable"]:
                result["enforced"] = False
    except Exception as exc:  # noqa: BLE001 — a probe must not break a report
        logger.info("Area 1 immutability probe unavailable: %s", exc)
        result["checked"] = False
        result["enforced"] = None
        result["note"] = (
            f"Could not probe database privileges ({type(exc).__name__}). "
            f"Application-layer immutability (no update/delete code path, no "
            f"mutating API route) still holds, but database-layer enforcement "
            f"is UNVERIFIED on this deployment.")
    if result.get("enforced") is False:
        result["note"] = (
            "The application role still holds UPDATE and/or DELETE on Area 1 "
            "tables. Application-layer immutability holds today, but the "
            "database would permit a future code path to mutate delivered "
            "evidence. Apply immutability_grants_sql().")
    return result


async def record_mutation_attempt(db, *, table: str, row_id: Any, actor: str,
                                  operation: str, reason: str) -> None:
    """Audit an attempt to mutate Area 1, then let the caller refuse it.

    Written BEFORE the refusal so the attempt survives even if the refusal path
    changes. An attempt that leaves no trace is the one worth worrying about.
    """
    try:
        from app.tefca_registry import models as reg

        db.add(reg.TefcaRegAuditLog(
            entity_id=None,
            action="area1_mutation_refused",
            actor_email=actor,
            metadata_={"table": table, "row_id": str(row_id),
                       "operation": operation, "reason": reason},
        ))
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.error("could not audit Area 1 mutation attempt on %s/%s by %s: %s",
                     table, row_id, actor, exc)


# ── create ───────────────────────────────────────────────────────────────────

async def create_intake(db, **fields) -> m.RceSourceIntake:
    """Insert one delivery event. The only write path for an intake."""
    intake = m.RceSourceIntake(**fields)
    db.add(intake)
    await db.flush()
    return intake


async def create_source_records(db, rows: Sequence[Dict[str, Any]]) -> int:
    """Bulk-insert source records.

    Uses the ORM's bulk insert rather than COPY: 23,566 rows is well within
    reach, and keeping one insert path means the unique constraint on
    (intake, line_number) is enforced identically however the rows arrive.
    """
    if not rows:
        return 0
    await db.execute(m.RceSourceRecord.__table__.insert(), list(rows))
    return len(rows)


# ── read ─────────────────────────────────────────────────────────────────────

async def get_intake(db, intake_id) -> Optional[m.RceSourceIntake]:
    return await db.get(m.RceSourceIntake, intake_id)


async def list_intakes(db, limit: int = 50) -> List[m.RceSourceIntake]:
    return list((await db.execute(
        select(m.RceSourceIntake)
        .order_by(m.RceSourceIntake.received_at.desc())
        .limit(limit))).scalars().all())


async def find_intakes_by_sha(db, sha256: str) -> List[m.RceSourceIntake]:
    """Earlier deliveries with identical bytes, oldest first.

    Used to LINK a duplicate, never to reject one: a re-delivery is its own
    historical event and is recorded in full.
    """
    return list((await db.execute(
        select(m.RceSourceIntake)
        .where(m.RceSourceIntake.sha256 == sha256)
        .order_by(m.RceSourceIntake.received_at.asc()))).scalars().all())


async def list_source_records(db, intake_id, *, limit: int = 100, offset: int = 0,
                              parse_status: Optional[str] = None,
                              promotion_status: Optional[str] = None
                              ) -> List[m.RceSourceRecord]:
    stmt = select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id)
    if parse_status:
        stmt = stmt.where(m.RceSourceRecord.parse_status == parse_status)
    if promotion_status:
        stmt = stmt.where(m.RceSourceRecord.promotion_status == promotion_status)
    stmt = stmt.order_by(m.RceSourceRecord.line_number).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().all())


async def count_source_records(db, intake_id, **filters) -> int:
    stmt = select(func.count()).select_from(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id)
    for column, value in filters.items():
        if value is not None:
            stmt = stmt.where(getattr(m.RceSourceRecord, column) == value)
    return int((await db.execute(stmt)).scalar() or 0)


# ── revalidation ─────────────────────────────────────────────────────────────

async def verify_record_hashes(db, intake_id, *, sample: Optional[int] = None
                               ) -> Dict[str, Any]:
    """Re-compute stored raw lines' hashes and compare against intake values.

    Turns the immutability claim into a measurement. A mismatch means a stored
    raw line no longer hashes to what it did on arrival — which would mean Area 1
    was modified, and is reported as a hard failure rather than a warning.
    """
    stmt = select(m.RceSourceRecord.id, m.RceSourceRecord.line_number,
                  m.RceSourceRecord.raw_line, m.RceSourceRecord.record_sha256
                  ).where(m.RceSourceRecord.source_intake_id == intake_id)
    if sample:
        stmt = stmt.limit(sample)
    rows = (await db.execute(stmt)).all()

    mismatches = []
    for row_id, line_number, raw_line, stored in rows:
        recomputed = hashlib.sha256((raw_line or "").encode("utf-8")).hexdigest()
        if recomputed != stored:
            mismatches.append({"id": str(row_id), "line_number": line_number,
                               "stored": stored, "recomputed": recomputed})
    return {
        "records_checked": len(rows),
        "mismatches": len(mismatches),
        "detail": mismatches[:20],
        "intact": not mismatches,
    }


async def verify_stored_file(db, intake_id) -> Dict[str, Any]:
    """Re-hash the preserved original file and compare against the intake record."""
    import os

    intake = await get_intake(db, intake_id)
    if intake is None:
        return {"checked": False, "reason": "intake not found"}
    path = intake.storage_path
    if not path or not os.path.exists(path):
        return {"checked": False, "reason": f"stored file not found at {path}",
                "intact": None}
    with open(path, "rb") as handle:
        recomputed = hashlib.sha256(handle.read()).hexdigest()
    return {
        "checked": True,
        "storage_path": path,
        "recorded_sha256": intake.sha256,
        "recomputed_sha256": recomputed,
        "intact": recomputed == intake.sha256,
    }
