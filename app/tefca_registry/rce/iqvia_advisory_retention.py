"""IQVIA advisory COMPARISON outcome retention (bulk, append-only). DEFAULT OFF.

WHAT THIS ADDS (and only this, per decision D-10 / DEF-14 retention half)
-------------------------------------------------------------------------
`iqvia_advisory_comparison.advisory_comparison_batch` answers a page and
forgets it: a 258-batch population run exists only as client-side response
captures (37/258 preserved on 2026-11-06), which the owner has ruled are not
database persistence. This module lets the advisory-comparison route RETAIN
one attributable row per compared entity -- presence outcome, address outcome,
masked identifier basis, snapshot provenance, run correlation, who asked --
when (and only when) the caller explicitly asks for persistence.

WHAT THIS IS NOT
----------------
* Not a verification source and not a classification input: CHECK constraints
  pin determination to 'AUTOMATED_CANDIDATE_NOT_CONFIRMED',
  affects_verification to false and requires_analyst_review to true. No
  column can carry a verdict, exactly like iqvia_advisory_assessment.
* Not a change to the comparison itself: with persist off (the default) the
  route's behaviour is byte-identical to before. Persisted mode writes inside
  the same bounded MAX_BATCH (200) page the route already enforces.
* Not editable: a database trigger rejects UPDATE and DELETE; the runtime
  role is granted SELECT, INSERT only. A correction is a new run_id.
* Idempotent: UNIQUE (source_snapshot_id, entity_id, run_id) with
  ON CONFLICT DO NOTHING, so a re-sent page after a client restart never
  duplicates and never errors.

Licensed values never land here: identifiers are stored MASKED (the same
`mask` the comparison output already carries) and the detail JSON holds
outcome notes and aggregate address agreement counts, never IQVIA rows.

SCHEMA: table `iqvia_advisory_comparison_outcome`, migration
20261108_iqvia_advisory_outcome (additive only; grants like 20261014).
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, ForeignKey,
                        String, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import Base

DETERMINATION = "AUTOMATED_CANDIDATE_NOT_CONFIRMED"

TABLE = "iqvia_advisory_comparison_outcome"
UQ_RUN_ENTITY = "uq_iqvia_adv_outcome_snapshot_entity_run"

PRESENCE_OUTCOMES = ("SUPPORTS", "DIFFERS", "NO_IQVIA_DATA")
ADDRESS_OUTCOMES = ("ADDRESS_CONSISTENT", "ADDRESS_DIFFERS",
                    "NO_ADDRESS_DATA", "NOT_COMPARED")


class IqviaAdvisoryComparisonOutcome(Base):
    __tablename__ = TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: the snapshot the route was called on (APPROVED or SUPERSEDED successor)
    source_snapshot_id = Column(UUID(as_uuid=True),
                                ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False)
    #: the registration-time root the staged rows live under (provenance, not an FK target change)
    root_snapshot_id = Column(UUID(as_uuid=True), nullable=False)
    snapshot_sha256_prefix = Column(String(12), nullable=False)
    source_system = Column(String(32), nullable=False)
    entity_id = Column(UUID(as_uuid=True),
                       ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"), nullable=False)
    #: caller-supplied correlation for one population run; idempotence key with snapshot+entity
    run_id = Column(String(64), nullable=False)
    presence_outcome = Column(String(32), nullable=False)
    address_outcome = Column(String(32), nullable=False)
    #: masked identifiers the comparison used (never raw values)
    identifier_basis = Column(JSONB, nullable=False)
    #: presence notes + address agreement counts (aggregates only, no IQVIA rows)
    outcome_detail = Column(JSONB, nullable=False)
    determination = Column(String(40), nullable=False,
                           server_default=text(f"'{DETERMINATION}'"))
    requires_analyst_review = Column(Boolean, nullable=False, server_default=text("true"))
    affects_verification = Column(Boolean, nullable=False, server_default=text("false"))
    created_by = Column(String(320), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source_snapshot_id", "entity_id", "run_id", name=UQ_RUN_ENTITY),
        CheckConstraint("affects_verification = false", name="ck_iqvia_adv_outcome_not_verification"),
        CheckConstraint("requires_analyst_review = true", name="ck_iqvia_adv_outcome_needs_review"),
        CheckConstraint(f"determination = '{DETERMINATION}'", name="ck_iqvia_adv_outcome_determination"),
        CheckConstraint("presence_outcome IN ('SUPPORTS','DIFFERS','NO_IQVIA_DATA')",
                        name="ck_iqvia_adv_outcome_presence"),
        CheckConstraint("address_outcome IN ('ADDRESS_CONSISTENT','ADDRESS_DIFFERS',"
                        "'NO_ADDRESS_DATA','NOT_COMPARED')", name="ck_iqvia_adv_outcome_address"),
    )


async def store_state(db) -> str:
    """READABLE / NOT_APPLIED / UNREADABLE, decided by catalogue functions
    (same discipline as iqvia_advisory_assessment._store_state): no statement
    can fail, so the caller's transaction is never poisoned."""
    row = (await db.execute(text(
        f"SELECT to_regclass('public.{TABLE}') IS NOT NULL AS present, "
        f"CASE WHEN to_regclass('public.{TABLE}') IS NULL THEN false "
        f"ELSE has_table_privilege('public.{TABLE}', 'SELECT') END AS readable"))).one()
    if not row[0]:
        return "NOT_APPLIED"
    return "READABLE" if row[1] else "UNREADABLE"


def _rows_from_comparison(*, run_id: str, comparison: Dict[str, Any],
                          created_by: str) -> list[Dict[str, Any]]:
    """One insertable row per per-entity result. NO_SUCH_ENTITY error rows are
    skipped (there is no entity to attribute them to; the response still
    reports them). Pure, so it is testable without a database."""
    prov = comparison["provenance"]
    rows: list[Dict[str, Any]] = []
    for r in comparison["results"]:
        if "error" in r:
            continue
        rows.append({
            "id": uuid.uuid4(),
            "source_snapshot_id": uuid.UUID(prov["snapshot_id"]),
            "root_snapshot_id": uuid.UUID(comparison["root_snapshot_id"]),
            "snapshot_sha256_prefix": prov["sha256_prefix"],
            "source_system": comparison["source_system"],
            "entity_id": uuid.UUID(r["entity_id"]),
            "run_id": run_id,
            "presence_outcome": r["presence"]["outcome"],
            "address_outcome": r["address"]["outcome"],
            "identifier_basis": r["identifiers"],
            "outcome_detail": {"presence_notes": r["presence"]["notes"],
                               "hco_keys": r["presence"].get("hco_keys", []),
                               "address": {k: r["address"].get(k)
                                           for k in ("reason", "iqvia_addresses", "agreeing")}},
            "determination": DETERMINATION,
            "requires_analyst_review": True,
            "affects_verification": False,
            "created_by": created_by,
        })
    return rows


async def persist_comparison_outcomes(db, *, run_id: str, comparison: Dict[str, Any],
                                      created_by: str) -> Dict[str, Any]:
    """Write one attributable outcome row per compared entity (no commit; the
    caller owns the transaction). Idempotent per (snapshot, entity, run_id):
    a re-sent page inserts nothing and reports it honestly."""
    rows = _rows_from_comparison(run_id=run_id, comparison=comparison,
                                 created_by=created_by)
    skipped_errors = len(comparison["results"]) - len(rows)
    if not rows:
        return {"run_id": run_id, "persisted": 0, "already_persisted": 0,
                "skipped_errors": skipped_errors}
    stmt = pg_insert(IqviaAdvisoryComparisonOutcome.__table__).values(rows)
    stmt = stmt.on_conflict_do_nothing(constraint=UQ_RUN_ENTITY)
    result = await db.execute(stmt)
    persisted = int(result.rowcount or 0)
    return {"run_id": run_id, "persisted": persisted,
            "already_persisted": len(rows) - persisted,
            "skipped_errors": skipped_errors}
