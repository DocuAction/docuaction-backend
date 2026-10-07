"""Database enforcement: an approved (or otherwise non-PENDING) source snapshot cannot be changed.

PR #122 grants the runtime role column-level UPDATE on `record_count` and `metadata` so the IQVIA
importer can finish staging. A column grant cannot tell a PENDING staging row from an APPROVED one, so
by itself it would let the runtime role rewrite `metadata`/`record_count` of an approved snapshot -
changing the evidence an approval was given on. The trigger added to migration
20261006_snapshot_bookkeeping closes that at the database, independent of application code:

    UPDATE   allowed only when OLD.status = NEW.status = 'PENDING' and nothing except record_count
             and metadata changes (so staging bookkeeping works, and nothing else does)
    DELETE   refused for any row that is not PENDING
    INSERT   untouched: approval, rejection, supersession and rollback stay NEW rows

The trigger applies to every role, the table owner and superusers included; only an explicit
`ALTER TABLE ... DISABLE TRIGGER` (a deliberate, auditable DDL act) bypasses it.
Synthetic data only. Needs a database and the `docuaction_app` role; skips otherwise.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from rce_traceability_support import SYN, rolled_back_db  # noqa: F401

pytestmark = pytest.mark.regression

APP_ROLE = "docuaction_app"
GUARD = "source_snapshot_immutable"
NON_PENDING = ["APPROVED", "SUPERSEDED", "REJECTED", "FAILED", "ROLLED_BACK"]


async def _require_guard_and_role(db):
    if not (await db.execute(text("select 1 from pg_roles where rolname = :r"), {"r": APP_ROLE})).first():
        pytest.skip(f"role {APP_ROLE} does not exist on this database")
    if not (await db.execute(text(
            "select 1 from pg_trigger where tgrelid = 'source_snapshot'::regclass "
            "and tgname = 'trg_source_snapshot_guard' and not tgisinternal and tgenabled = 'O'"))).first():
        pytest.fail("trigger trg_source_snapshot_guard is missing or disabled: migration "
                    "20261006_snapshot_bookkeeping is not applied or no longer creates the guard")


async def _insert(db, status: str, *, label: str | None = None) -> uuid.UUID:
    sid = uuid.uuid4()
    await db.execute(text(
        "insert into source_snapshot (id, source_system, snapshot_label, sha256, record_count, received_at, "
        "status, approved_by, approved_role, approved_at, created_by, correlation_id, metadata) "
        "values (:id, 'IQVIA_HCO', :label, :sha, 5, now(), :status, :ab, :ar, :at, :by, :corr, "
        "'{\"k\": 1}'::jsonb)"),
        {"id": sid, "label": label or f"{SYN}-guard-{sid}", "sha": "a" * 64, "status": status,
         # ck_source_snapshot_approval: an APPROVED row must name who approved it, in what role, and when.
         "ab": SYN if status == "APPROVED" else None, "ar": "qalead" if status == "APPROVED" else None,
         "at": datetime.now(timezone.utc) if status == "APPROVED" else None,
         "by": SYN, "corr": f"{SYN}-corr"})
    return sid


async def _expect_refused(db, sql: str, params: dict, *, role: str | None):
    """Run `sql` (optionally as `role`) inside a savepoint and require the guard or a privilege denial."""
    await db.execute(text("SAVEPOINT s_guard"))
    if role:
        await db.execute(text(f'SET ROLE "{role}"'))
    try:
        with pytest.raises(DBAPIError) as exc:
            await db.execute(text(sql), params)
        message = str(exc.value)
    finally:
        # The refused statement aborted the transaction, so the rollback must come first.
        await db.execute(text("ROLLBACK TO SAVEPOINT s_guard"))
        await db.execute(text("RESET ROLE"))
    return message


async def _row(db, sid):
    return (await db.execute(text(
        "select status, record_count, metadata, snapshot_label, sha256 from source_snapshot where id = :i"),
        {"i": sid})).one()


class TestStagingBookkeepingStillWorks:
    async def test_runtime_role_can_update_bookkeeping_on_a_pending_row(self, rolled_back_db):
        db = rolled_back_db
        await _require_guard_and_role(db)
        sid = await _insert(db, "PENDING")
        await db.execute(text(f'SET ROLE "{APP_ROLE}"'))
        await db.execute(text(
            "update source_snapshot set record_count = 9, metadata = '{\"progress\": 1}'::jsonb where id = :i"),
            {"i": sid})
        await db.execute(text("RESET ROLE"))
        row = await _row(db, sid)
        assert row.record_count == 9 and row.metadata == {"progress": 1} and row.status == "PENDING"

    async def test_successor_rows_can_still_be_inserted_by_the_runtime_role(self, rolled_back_db):
        db = rolled_back_db
        await _require_guard_and_role(db)
        pending = await _insert(db, "PENDING")
        await db.execute(text(f'SET ROLE "{APP_ROLE}"'))
        approved = uuid.uuid4()
        await db.execute(text(
            "insert into source_snapshot (id, source_system, snapshot_label, sha256, record_count, received_at, "
            "status, approved_by, approved_role, approved_at, supersedes_snapshot_id, created_by, correlation_id) "
            "values (:id, 'IQVIA_HCO', :l, :sha, 5, now(), 'APPROVED', :by, 'qalead', now(), :sup, :by, :corr)"),
            {"id": approved, "l": f"{SYN}-approved-{approved}", "sha": "b" * 64, "sup": pending,
             "by": SYN, "corr": f"{SYN}-corr"})
        await db.execute(text("RESET ROLE"))
        assert (await _row(db, approved)).status == "APPROVED"


class TestNonPendingSnapshotsCannotChange:
    @pytest.mark.parametrize("status", NON_PENDING)
    @pytest.mark.parametrize("column,value", [("metadata", "'{\"tampered\": true}'::jsonb"),
                                              ("record_count", "999999")])
    async def test_runtime_role_cannot_change_even_the_bookkeeping_columns(self, rolled_back_db, status,
                                                                          column, value):
        db = rolled_back_db
        await _require_guard_and_role(db)
        sid = await _insert(db, status)
        before = await _row(db, sid)
        message = await _expect_refused(
            db, f"update source_snapshot set {column} = {value} where id = :i", {"i": sid}, role=APP_ROLE)
        assert GUARD in message, f"expected the immutability guard, got: {message[:200]}"
        assert tuple(await _row(db, sid)) == tuple(before), "the row must be unchanged"

    @pytest.mark.parametrize("status", NON_PENDING)
    async def test_owner_and_superuser_cannot_change_a_non_pending_row_either(self, rolled_back_db, status):
        db = rolled_back_db
        await _require_guard_and_role(db)
        sid = await _insert(db, status)
        for set_clause in ("snapshot_label = 'renamed'", "sha256 = repeat('c', 64)", "status = 'PENDING'",
                           "metadata = '{}'::jsonb"):
            message = await _expect_refused(
                db, f"update source_snapshot set {set_clause} where id = :i", {"i": sid}, role=None)
            assert GUARD in message, f"{set_clause}: expected the guard, got {message[:200]}"

    @pytest.mark.parametrize("status", NON_PENDING)
    async def test_a_non_pending_row_cannot_be_deleted(self, rolled_back_db, status):
        db = rolled_back_db
        await _require_guard_and_role(db)
        sid = await _insert(db, status)
        message = await _expect_refused(db, "delete from source_snapshot where id = :i", {"i": sid}, role=None)
        assert GUARD in message


class TestPendingRowsAreOnlyBookkeepingEditable:
    @pytest.mark.parametrize("set_clause", [
        "status = 'APPROVED'",                                   # in-place approval
        "status = 'APPROVED', metadata = '{\"x\": 1}'::jsonb",   # smuggled alongside allowed columns
        "sha256 = repeat('d', 64)",
        "snapshot_label = 'renamed'",
        "approved_by = 'someone'",
        "supersedes_snapshot_id = gen_random_uuid()",
        "record_count = 1, snapshot_label = 'renamed'",
    ])
    async def test_nothing_but_the_two_bookkeeping_columns_can_change(self, rolled_back_db, set_clause):
        db = rolled_back_db
        await _require_guard_and_role(db)
        sid = await _insert(db, "PENDING")
        message = await _expect_refused(
            db, f"update source_snapshot set {set_clause} where id = :i", {"i": sid}, role=None)
        assert GUARD in message or "foreign key" in message, message[:200]
        assert (await _row(db, sid)).status == "PENDING"

    async def test_a_pending_staging_row_can_still_be_cleaned_up(self, rolled_back_db):
        db = rolled_back_db
        await _require_guard_and_role(db)
        sid = await _insert(db, "PENDING")
        await db.execute(text("delete from source_snapshot where id = :i"), {"i": sid})
        assert (await db.execute(text("select count(*) from source_snapshot where id = :i"),
                                 {"i": sid})).scalar() == 0
