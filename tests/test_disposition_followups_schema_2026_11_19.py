"""Schema, ownership, grants and startup protections of rce_disposition_followups.

Readiness closeout 2026-11-19 (owner authorization, item 2). Pinned here:

  * Alembic 20261118_disposition_followups is the ONLY creator: the table is in
    the startup exclusion, so `create_all` at application start can never
    create it (a start-up-created table would make the migration fail on an
    existing table and would have none of its indexes, CHECKs or grants);
  * the table the migration created matches the model: columns, the three
    CHECK constraints and the indexes, including the partial unique index that
    makes a repeated submission one row;
  * the runtime role's privileges are exactly SELECT, INSERT, UPDATE where it
    is not the owner (no DELETE, no TRUNCATE): a row is the record of accepted
    work and what became of it;
  * the CHECK constraints refuse an unknown state, kind or attribution;
  * the PROD-convergence inventory keeps the table OWNER-owned at FINALIZE, so
    the no-DELETE grant still binds afterwards;
  * with the flag on but the migration NOT applied, saves stay synchronous and
    the worker's ticks do nothing - they do not fail and create nothing.

Synthetic rows only. Uses the isolated database (`db_required`).
"""

from __future__ import annotations

import os
import uuid

import pytest
from support_delivery_api import run

TABLE = "rce_disposition_followups"


def test_only_alembic_may_create_the_table():
    from app.core.database import Base
    from app.core.schema_guard import create_all_except_migration_owned
    from app.tefca_registry.rce import traceability_models as tm

    assert TABLE in tm.MIGRATION_OWNED_TABLES
    assert TABLE in Base.metadata.tables, "the model must be on Base so autogenerate sees it"
    captured = {}

    class _Meta:
        sorted_tables = list(Base.metadata.sorted_tables)

        def create_all(self, conn, tables=None):
            captured["names"] = {t.name for t in tables}

    excluded = create_all_except_migration_owned(object(), _Meta())
    assert TABLE in excluded
    assert TABLE not in captured["names"]
    assert "rce_curated_records" in captured["names"]  # ordinary tables are still created


def test_the_convergence_inventory_keeps_the_table_owner_owned_at_finalize():
    import importlib.util

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts",
        "prod_legacy_convergence.py",
    )
    spec = importlib.util.spec_from_file_location("prod_legacy_convergence_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert TABLE in module.MANAGED_CHAIN_CREATES
    # FINALIZE hands every chain-created table to the runtime role EXCEPT the
    # owner set. An owner holds every privilege, so the no-DELETE grant binds
    # only while the table stays owner-owned (as rce_delivery_jobs does).
    assert TABLE in module.AREA1_OWNER_TABLES
    assert "rce_delivery_jobs" in module.AREA1_OWNER_TABLES  # the shape it follows


def _script_directory():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config("alembic.ini"))


def test_the_migration_chains_on_the_previous_head_and_the_chain_has_one_head():
    # 20261118 (on DEV) and 20261124_iqvia_reconciliation (merged to main first) keep
    # their places. The record-comment and NPI-research migrations follow 20261124
    # (re-pointed 2026-11-24), which makes 20261115_rce_npi_research the single head.
    scripts = _script_directory()
    assert (
        scripts.get_revision("20261118_disposition_followups").down_revision
        == "20261108_iqvia_advisory_outcome"
    )
    assert (
        scripts.get_revision("20261124_iqvia_reconciliation").down_revision
        == "20261118_disposition_followups"
    )
    assert (
        scripts.get_revision("20261112_rce_record_comments").down_revision
        == "20261124_iqvia_reconciliation"
    )
    assert scripts.get_heads() == ["20261115_rce_npi_research"]


async def _catalog():
    from sqlalchemy import text

    from app.core.database import async_session_maker

    async with async_session_maker() as db:

        async def rows(sql, **params):
            return (await db.execute(text(sql), params)).all()

        columns = {
            name: (data_type, nullable == "NO")
            for name, data_type, nullable in await rows(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t",
                t=TABLE,
            )
        }
        checks = {
            name
            for (name,) in await rows(
                "SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) "
                "AND contype = 'c'",
                t=f"public.{TABLE}",
            )
        }
        indexes = {
            name: definition
            for name, definition in await rows(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' "
                "AND tablename = :t",
                t=TABLE,
            )
        }
        foreign = {
            (column, target)
            for column, target in await rows(
                "SELECT a.attname, c.confrelid::regclass::text FROM pg_constraint c "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
                "WHERE c.conrelid = CAST(:t AS regclass) AND c.contype = 'f'",
                t=f"public.{TABLE}",
            )
        }
        owner = (
            await rows(
                "SELECT tableowner FROM pg_tables WHERE schemaname = 'public' AND tablename = :t",
                t=TABLE,
            )
        )[0][0]
        version = (await rows("SELECT version_num FROM alembic_version"))[0][0]
        app_role = os.environ.get("DB_APP_ROLE", "docuaction_app")
        role_exists = bool(await rows("SELECT 1 FROM pg_roles WHERE rolname = :r", r=app_role))
        privileges = {}
        if role_exists:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                privileges[privilege] = (
                    await rows(
                        "SELECT has_table_privilege(:r, :t, :p)",
                        r=app_role,
                        t=f"public.{TABLE}",
                        p=privilege,
                    )
                )[0][0]
        return {
            "columns": columns,
            "checks": checks,
            "indexes": indexes,
            "foreign": foreign,
            "owner": owner,
            "version": version,
            "app_role": app_role,
            "role_exists": role_exists,
            "privileges": privileges,
        }


@pytest.fixture(scope="module")
def catalog(db_available):
    if not db_available:
        pytest.skip("No database reachable at DATABASE_URL (see tests/conftest.py)")
    return run(_catalog())


def test_the_migrated_table_matches_the_model(catalog):
    from app.core.database import Base

    model = Base.metadata.tables[TABLE]
    assert catalog["version"] == _script_directory().get_current_head()
    assert set(catalog["columns"]) == {c.name for c in model.columns}
    for column in model.columns:
        assert catalog["columns"][column.name][1] is (not column.nullable), column.name
    assert catalog["checks"] == {
        "ck_rce_disp_followup_state",
        "ck_rce_disp_followup_kind",
        "ck_rce_disp_followup_attribution",
    }
    assert ("intake_id", "rce_source_intakes") in catalog["foreign"]
    assert ("issue_id", "rce_issues") in catalog["foreign"]


def test_the_indexes_exist_including_the_partial_unique_idempotency_index(catalog):
    indexes = catalog["indexes"]
    assert {
        "uq_rce_disp_followup_issue_key",
        "ix_rce_disp_followup_pending",
        "ix_rce_disp_followup_intake",
        "ix_rce_disp_followup_issue",
        "ix_rce_disp_followup_record",
    } <= set(indexes)
    unique = indexes["uq_rce_disp_followup_issue_key"]
    assert "UNIQUE" in unique and "idempotency_key IS NOT NULL" in unique
    assert "WHERE" in indexes["ix_rce_disp_followup_pending"]


def test_the_runtime_role_may_read_insert_and_update_but_never_delete(catalog):
    if not catalog["role_exists"]:
        pytest.skip(f"role {catalog['app_role']} does not exist in this database")
    privileges = catalog["privileges"]
    assert privileges["SELECT"] and privileges["INSERT"] and privileges["UPDATE"]
    if catalog["owner"] == catalog["app_role"]:
        pytest.skip(
            "the runtime role owns the table in this database: an owner holds every "
            "privilege, so the no-DELETE grant cannot be observed here"
        )
    assert privileges["DELETE"] is False
    assert privileges["TRUNCATE"] is False


@pytest.mark.usefixtures("db_required")
@pytest.mark.parametrize(
    "column, value",
    [("state", "DONE"), ("kind", "OTHER"), ("attribution", "ROBOT")],
)
def test_the_check_constraints_refuse_unknown_values(column, value):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.core.database import async_session_maker

    async def _insert():
        async with async_session_maker() as db:
            intake = (await db.execute(text("SELECT id FROM rce_source_intakes LIMIT 1"))).scalar()
            if intake is None:
                return "no-intake"
            values = {"state": "RECEIVED", "kind": "ISSUE_DISPOSITION", "attribution": "HUMAN"}
            values[column] = value
            try:
                await db.execute(
                    text(
                        f"INSERT INTO {TABLE} (id, kind, intake_id, decision, attribution, actor, "  # noqa: S608
                        "state) VALUES (:id, :kind, :intake, 'CORRECT', :attribution, "
                        "'synthetic@test.invalid', :state)"
                    ),
                    {"id": uuid.uuid4(), "intake": intake, **values},
                )
                await db.commit()
                return "inserted"
            except IntegrityError:
                await db.rollback()
                return "refused"

    outcome = run(_insert())
    if outcome == "no-intake":
        pytest.skip("no intake row exists in this database to reference")
    assert outcome == "refused"


def test_flag_on_without_the_migration_is_inert(monkeypatch):
    """The code can ship before the migration: with the table absent the flag
    does not switch deferral on, and the worker's ticks neither fail nor run."""
    from app.tefca_registry.rce import disposition_followups as followups

    monkeypatch.setenv("ENABLE_DEFERRED_DISPOSITION_PROCESSING", "true")
    ran = {"process": 0, "reap": 0, "sweep": 0}

    async def absent(_db):
        return False

    async def must_not_run(*_a, **_k):
        ran["process"] += 1

    async def must_not_reap(*_a, **_k):
        ran["reap"] += 1

    async def must_not_sweep(*_a, **_k):
        ran["sweep"] += 1

    monkeypatch.setattr(followups, "store_exists", absent)
    monkeypatch.setattr(followups, "_store_checked", None)
    monkeypatch.setattr(followups, "process_next", must_not_run)
    monkeypatch.setattr(followups, "reap_stale_processing", must_not_reap)
    monkeypatch.setattr(followups, "sweep_received", must_not_sweep)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("app.core.database.async_session_maker", lambda: _Session())

    async def _exercise():
        assert followups.enabled() is True
        assert await followups.active(object()) is False
        assert await followups.pending_count(object(), uuid.uuid4()) == 0
        assert (await followups.summary(object(), uuid.uuid4()))["store_present"] is False
        await followups._poll_tick()
        await followups._reap_tick()

    import asyncio

    asyncio.run(_exercise())
    assert ran == {"process": 0, "reap": 0, "sweep": 0}


def test_an_offline_downgrade_is_refused_and_the_online_one_locks_before_it_counts(monkeypatch):
    """`alembic downgrade --sql` cannot look at the table, so it must not emit a
    DROP at all. Online, the table is locked before the row count that decides
    whether the drop may happen."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20261118_disposition_followups.py"
    )
    spec = importlib.util.spec_from_file_location("disposition_followups_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    emitted = []
    monkeypatch.setattr(module, "_offline", lambda: True)
    monkeypatch.setattr(module.op, "execute", lambda sql, *a, **k: emitted.append(str(sql)))
    with pytest.raises(module.DispositionFollowupsPreconditionError):
        module.downgrade()
    assert emitted == []  # no DROP was generated

    # Online: what is EXECUTED, in order. (The docstring also says "ACCESS
    # EXCLUSIVE", so reading the source proves nothing about the statements.)
    def run_online(rows):
        executed = []

        class _Result:
            def scalar(self):
                return rows

        class _Bind:
            def execute(self, statement, *a, **k):
                executed.append(str(statement))
                return _Result()

        class _Inspector:
            def get_table_names(self):
                return [TABLE]

        monkeypatch.setattr(module, "_offline", lambda: False)
        monkeypatch.setattr(module.op, "get_bind", lambda: _Bind())
        monkeypatch.setattr(module.sa, "inspect", lambda bind: _Inspector())
        monkeypatch.setattr(module.op, "execute", lambda sql, *a, **k: executed.append(str(sql)))
        return executed

    executed = run_online(0)
    module.downgrade()
    assert len(executed) == 3
    assert "LOCK TABLE" in executed[0] and "ACCESS EXCLUSIVE" in executed[0]
    assert "count(*)" in executed[1].lower()
    assert executed[2].startswith("DROP TABLE")

    executed = run_online(3)
    with pytest.raises(module.DispositionFollowupsPreconditionError, match="must stay"):
        module.downgrade()
    assert len(executed) == 2 and not any("DROP" in sql for sql in executed)  # locked, counted
