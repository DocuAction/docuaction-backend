"""DEV, 2026-10-06: POST /api/tefca/rce/deliveries/{intake}/preflight returned 500 on every call and no run was
persisted. Server log: `permission denied for table rce_preflight_run` raised at the final commit of
`preflight.run_preflight`, then masked by `PendingRollbackError` from the FAILED-status write.

Cause: `run_preflight` inserts the run as RUNNING and later UPDATEs it to COMPLETE (or FAILED), but migration
20261003_preflight_shadow_workspace grants the runtime role only SELECT, INSERT on every table it creates.
Every database-backed preflight test connects as a superuser, so none could see it - the same class as D-05.

Fix (migration 20261007_preflight_run_finalize + preflight.py): column-level UPDATE on the eight completion columns
of rce_preflight_run only, a row guard making a finished run immutable, the six sibling tables left append-only,
and a failure path that records a FAILED run as an INSERT and re-raises the real error.

These tests run the real functions as the RUNTIME role (`SET ROLE docuaction_app`).
Synthetic data only. Skips without a database or without the role.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.tefca_registry.rce import preflight as pf
from rce_traceability_support import SYN, make_rows, rolled_back_db, seed_intake  # noqa: F401

pytestmark = pytest.mark.regression

APP_ROLE = "docuaction_app"
GUARD = "rce_preflight_run_immutable"
FINAL = {"status", "classification_gate", "records_evaluated", "findings_count", "normalizations_count",
         "summary", "error", "completed_at"}
SIBLINGS = ("rce_preflight_finding", "rce_preflight_normalization", "rce_shadow_comparison",
            "rce_shadow_finding_delta", "rce_shadow_approval", "rce_successor_publication_event")


async def _require_role(db):
    if not (await db.execute(text("select 1 from pg_roles where rolname = :r"), {"r": APP_ROLE})).first():
        pytest.skip(f"role {APP_ROLE} does not exist on this database")


async def _as_runtime(db):
    await _require_role(db)
    # DEV's runtime role can already READ the legacy tables (the failing statement on DEV was the final commit of
    # rce_preflight_run, after those reads succeeded). A freshly migrated test database has no such baseline, so
    # model it - inside this test's own transaction, rolled back with it - to isolate what is under test: the
    # privileges the preflight migration itself grants on its own tables.
    await db.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{APP_ROLE}"'))
    await db.execute(text(f'SET ROLE "{APP_ROLE}"'))


async def _refused(db, sql: str, params: dict, *, role: str | None) -> str:
    """Run `sql` (optionally as `role`) in a savepoint and require it to be refused; return the message."""
    await db.execute(text("SAVEPOINT s_pf"))
    if role:
        await db.execute(text(f'SET ROLE "{role}"'))
    try:
        with pytest.raises(DBAPIError) as exc:
            await db.execute(text(sql), params)
        return str(exc.value)
    finally:
        # The refused statement aborted the transaction, so the rollback must come first.
        await db.execute(text("ROLLBACK TO SAVEPOINT s_pf"))
        await db.execute(text("RESET ROLE"))


async def _make_run(db, status: str = "RUNNING"):
    intake_id, _ = await seed_intake(db, make_rows(1), with_job=False)
    return (await db.execute(text(
        "insert into rce_preflight_run (id, source_intake_id, field_map_version, rule_set_version, "
        "preflight_version, status, actor, correlation_id) values (gen_random_uuid(), :i, 'v', 'v', 'v', :s, :a, 'c') returning id"),
        {"i": intake_id, "s": status, "a": SYN})).scalar()


class TestPreflightRunsAsTheRuntimeRole:
    async def test_run_completes_and_is_persisted(self, rolled_back_db):
        db = rolled_back_db
        intake_id, _ = await seed_intake(db, make_rows(3), with_job=False)
        await _as_runtime(db)
        run = await pf.run_preflight(db, intake_id, actor=f"{SYN}-runtime")
        await db.execute(text("RESET ROLE"))

        assert run["status"] == "COMPLETE", run
        row = (await db.execute(text(
            "select status, records_evaluated, completed_at is not null as done from rce_preflight_run "
            "where id = :i"), {"i": run["run_id"]})).one()
        assert row.status == "COMPLETE" and row.records_evaluated == 3 and row.done

    async def test_findings_are_written_and_linked_to_the_run(self, rolled_back_db):
        db = rolled_back_db
        intake_id, _ = await seed_intake(db, make_rows(3), with_job=False)
        await _as_runtime(db)
        run = await pf.run_preflight(db, intake_id, actor=f"{SYN}-runtime")
        await db.execute(text("RESET ROLE"))
        n = (await db.execute(text("select count(*) from rce_preflight_finding where run_id = :i"),
                              {"i": run["run_id"]})).scalar()
        assert n == run["findings_count"] and n > 0

        # ...and the runtime role still cannot edit what it wrote.
        msg = await _refused(db, "update rce_preflight_finding set description = 'edited' where run_id = :i",
                             {"i": run["run_id"]}, role=APP_ROLE)
        assert "permission denied" in msg


class TestAFailedRunIsRecordedAndTheRealErrorSurfaces:
    async def test_failure_is_persisted_as_a_failed_run_and_the_original_error_is_raised(
            self, rolled_back_db, monkeypatch):
        db = rolled_back_db
        intake_id, _ = await seed_intake(db, make_rows(2), with_job=False)

        def boom(*_a, **_k):
            raise RuntimeError("synthetic rule failure")
        monkeypatch.setattr(pf, "_schema_findings", boom)

        await _as_runtime(db)
        with pytest.raises(RuntimeError, match="synthetic rule failure"):
            await pf.run_preflight(db, intake_id, actor=f"{SYN}-runtime")
        await db.execute(text("RESET ROLE"))

        rows = (await db.execute(text(
            "select status, error, completed_at is not null as done from rce_preflight_run "
            "where source_intake_id = :i"), {"i": intake_id})).all()
        assert len(rows) == 1, "exactly one run row: the partial RUNNING row is rolled back, not left behind"
        assert rows[0].status == "FAILED" and rows[0].done
        assert "synthetic rule failure" in rows[0].error, rows[0].error


class TestTheGrantIsNarrowAndAFinishedRunIsImmutable:
    async def test_runtime_role_can_update_only_the_completion_columns(self, rolled_back_db):
        db = rolled_back_db
        await _require_role(db)
        cols = [r[0] for r in (await db.execute(text(
            "select attname from pg_attribute where attrelid = 'rce_preflight_run'::regclass "
            "and attnum > 0 and not attisdropped"))).fetchall()]
        can = {c for c in cols if (await db.execute(
            text("select has_column_privilege(:r, 'rce_preflight_run', :c, 'UPDATE')"),
            {"r": APP_ROLE, "c": c})).scalar()}
        assert can == FINAL, f"runtime role can UPDATE {sorted(can)}"
        assert not (await db.execute(
            text("select has_table_privilege(:r, 'rce_preflight_run', 'UPDATE')"), {"r": APP_ROLE})).scalar()
        for t in SIBLINGS:
            assert not (await db.execute(
                text("select has_any_column_privilege(:r, :t, 'UPDATE')"), {"r": APP_ROLE, "t": t})).scalar(), t
            for priv in ("DELETE", "TRUNCATE"):
                assert not (await db.execute(
                    text("select has_table_privilege(:r, :t, :p)"), {"r": APP_ROLE, "t": t, "p": priv})).scalar()

    @pytest.mark.parametrize("final_status", ["COMPLETE", "FAILED"])
    async def test_a_running_run_can_be_finalized_by_the_runtime_role(self, rolled_back_db, final_status):
        db = rolled_back_db
        await _require_role(db)
        rid = await _make_run(db)
        await db.execute(text(f'SET ROLE "{APP_ROLE}"'))
        await db.execute(text(
            "update rce_preflight_run set status = :s, records_evaluated = 4, completed_at = now() where id = :i"),
            {"s": final_status, "i": rid})
        await db.execute(text("RESET ROLE"))
        assert (await db.execute(text("select status from rce_preflight_run where id = :i"),
                                 {"i": rid})).scalar() == final_status

    @pytest.mark.parametrize("status", ["COMPLETE", "FAILED"])
    @pytest.mark.parametrize("role", [APP_ROLE, None])
    async def test_a_finished_run_cannot_be_changed_by_anyone(self, rolled_back_db, status, role):
        db = rolled_back_db
        await _require_role(db)
        rid = await _make_run(db, status)
        msg = await _refused(db, "update rce_preflight_run set findings_count = 99 where id = :i",
                             {"i": rid}, role=role)
        assert GUARD in msg, msg[:200]

    @pytest.mark.parametrize("set_clause", [
        "actor = 'someone else'", "build_sha = 'forged'", "rule_set_version = 'x'", "started_at = now()",
        "status = 'RUNNING'",                       # not a finalization
        "status = 'COMPLETE', actor = 'smuggled'",  # an allowed column plus a provenance column
    ])
    async def test_finalizing_cannot_touch_provenance_or_stay_running(self, rolled_back_db, set_clause):
        db = rolled_back_db
        await _require_role(db)
        rid = await _make_run(db)
        msg = await _refused(db, f"update rce_preflight_run set {set_clause} where id = :i", {"i": rid}, role=None)
        assert GUARD in msg, msg[:200]

    async def test_a_finished_run_cannot_be_deleted(self, rolled_back_db):
        db = rolled_back_db
        await _require_role(db)
        rid = await _make_run(db, "COMPLETE")
        msg = await _refused(db, "delete from rce_preflight_run where id = :i", {"i": rid}, role=None)
        assert GUARD in msg
