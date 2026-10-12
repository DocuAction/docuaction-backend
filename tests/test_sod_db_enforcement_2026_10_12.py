"""Database enforcement of no-self-QA and append-only on review_decision_events.

Migration 20261012_sod_db_enforcement (DRAFT, not applied to DEV/PROD):
  * `review_event_enforce_sod()` refuses a QA event written by the actor of the determination it
    reviews, whatever `sod_exception_granted_by` / `sod_exception_reason` contain;
  * `trg_review_event_append_only_rows` refuses UPDATE for every role, and DELETE for any role that
    does not hold the table owner's privileges;
  * `trg_review_event_append_only_trunc` does the same for TRUNCATE.

Two layers, both against a REAL PostgreSQL (the isolation-postgres CI job, or any local scratch
cluster with the docuaction_owner / docuaction_app roles). Synthetic data only; never DEV or PROD.

  Layer 1 (`TestEnforcement`)  the shared test database at head. Every test runs in a transaction
      that is rolled back, switching to `docuaction_app` / `docuaction_owner` with SET ROLE. The
      app role is GRANTed the WIDEST plausible runtime privileges inside the transaction (it is
      rolled back), so the refusals below are the TRIGGERS' doing, not a missing grant.
  Layer 2 (`TestMigrationLifecycle`)  a throw-away database created for the test: migrate to the
      parent revision, seed a HISTORICAL self-QA event that carries an exception, upgrade, prove the
      row is byte-for-byte untouched (md5 and xmin) and still readable, downgrade (function body
      restored exactly, guards gone), upgrade again, and prove the preflight refuses an app-owned
      table or function without changing anything.

Skips with a named reason when no suitable database / roles exist.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid

import pytest

asyncpg = pytest.importorskip("asyncpg")
from sqlalchemy.engine import make_url  # noqa: E402

pytestmark = pytest.mark.regression

OWNER, APP = "docuaction_owner", "docuaction_app"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT = "20261009_iqvia_affil_org_indexes"
REVISION = "20261012_sod_db_enforcement"
SOD_MSG = "segregation of duties"
APPEND_MSG = "review_decision_events_append_only"
LONG = "a rationale that is long enough for the check constraint"


def run(coro):
    return asyncio.run(coro)


def _url():
    return make_url(os.environ.get("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test"))


def _connect_kwargs(database=None):
    u = _url()
    return dict(user=u.username, password=u.password, host=u.host, port=u.port or 5432,
                database=database or u.database)


async def _connect(database=None):
    return await asyncpg.connect(**_connect_kwargs(database), timeout=5)


def _db_ready():
    async def go():
        c = await _connect()
        try:
            roles = {r["rolname"] for r in await c.fetch(
                "select rolname from pg_roles where rolname = any($1)", [OWNER, APP])}
            trig = await c.fetchval(
                "select count(*) from pg_trigger where tgname = any($1) and not tgisinternal",
                ["trg_review_event_append_only_rows", "trg_review_event_append_only_trunc"])
            return roles == {OWNER, APP}, int(trig)
        finally:
            await c.close()
    try:
        return run(go())
    except Exception as exc:  # noqa: BLE001
        return False, f"no database: {exc}"


ROLES_OK, GUARDS = _db_ready()


# ── helpers ──────────────────────────────────────────────────────────────────

async def _new_review(c):
    rid = "REV-SOD-" + uuid.uuid4().hex[:8]
    await c.execute("insert into review_records (id, review_id, source_record_id) values ($1,$2,$3)",
                    uuid.uuid4(), rid, uuid.uuid4())
    return rid


async def _event(c, rid, seq, etype, actor, *, qa_action=None, grantor=None, reason=None):
    await c.execute(
        "insert into review_decision_events (id, review_id, sequence_number, event_type, actor_user_id, "
        "actor_email, actor_role, rationale, qa_action, qa_reason, determination, "
        "sod_exception_granted_by, sod_exception_reason) "
        "values ($1,$2,$3,$4,$5,$6,'reviewer',$7,$8,$9,$10,$11,$12)",
        uuid.uuid4(), rid, seq, etype, actor, f"{actor}@example.invalid", LONG, qa_action,
        LONG if qa_action else None, "CONFIRM" if etype != "QA_REVIEW" else None, grantor, reason)


async def _refused(c, role, sql, *args, needle):
    """Run `sql` as `role` inside a savepoint; require an error containing `needle`; return the text."""
    await c.execute("savepoint s_ref")
    if role:
        await c.execute(f'set local role "{role}"')
    try:
        with pytest.raises(asyncpg.PostgresError) as exc:
            await c.execute(sql, *args)
        message = str(exc.value)
    finally:
        await c.execute("rollback to savepoint s_ref")
        await c.execute("reset role")
    assert needle in message, f"expected {needle!r}, got {message[:240]}"
    return message


async def _as_role(c, role, sql, *args):
    await c.execute(f'set local role "{role}"')
    try:
        return await c.execute(sql, *args)
    finally:
        await c.execute("reset role")


async def _count(c, rid):
    return await c.fetchval("select count(*) from review_decision_events where review_id = $1", rid)


INSERT_QA = ("insert into review_decision_events (id, review_id, sequence_number, event_type, "
             "actor_user_id, actor_email, actor_role, rationale, qa_action, qa_reason, "
             "sod_exception_granted_by, sod_exception_reason) "
             "values ($1,$2,$3,'QA_REVIEW',$4,'q@example.invalid','qalead',$5,'APPROVE',$5,$6,$7)")


def _txn(coro_fn):
    """Run `coro_fn(conn)` inside a transaction that is always rolled back."""
    async def go():
        c = await _connect()
        tr = c.transaction()
        await tr.start()
        try:
            # Worst plausible runtime grants, rolled back with the transaction.
            await c.execute(f'grant select, insert, update, delete, truncate on review_decision_events to "{APP}"')
            await c.execute(f'grant select on review_records to "{APP}"')
            await coro_fn(c)
        finally:
            await tr.rollback()
            await c.close()
    run(go())


@pytest.mark.skipif(not ROLES_OK, reason=f"needs a database with {OWNER}/{APP}: {GUARDS}")
@pytest.mark.skipif(ROLES_OK and GUARDS != 2,
                    reason="database is not migrated to 20261012_sod_db_enforcement (guards absent)")
class TestEnforcement:
    def test_self_qa_is_refused_whatever_the_exception_fields_say(self):
        analyst, other_admin = uuid.uuid4(), uuid.uuid4()

        async def body(c):
            for grantor, reason in ((None, None), (other_admin, LONG), (uuid.uuid4(), "x" * 40),
                                    (analyst, LONG), (other_admin, None), (None, LONG)):
                rid = await _new_review(c)
                await _event(c, rid, 1, "ANALYST_DETERMINATION", analyst)
                await _refused(c, APP, INSERT_QA, uuid.uuid4(), rid, 2, analyst, LONG, grantor, reason,
                               needle=SOD_MSG)
                assert await _count(c, rid) == 1, "the refused self-QA event must not exist"
        _txn(body)

    def test_independent_qa_is_allowed_as_the_runtime_role(self):
        analyst, qa = uuid.uuid4(), uuid.uuid4()

        async def body(c):
            rid = await _new_review(c)
            await _event(c, rid, 1, "ANALYST_DETERMINATION", analyst)
            await _as_role(c, APP, INSERT_QA, uuid.uuid4(), rid, 2, qa, LONG, None, None)
            assert await _count(c, rid) == 2
            # an RETURN then a fresh determination by the SAME analyst is not a QA event: allowed
            rid2 = await _new_review(c)
            await _event(c, rid2, 1, "ANALYST_DETERMINATION", analyst)
            await _event(c, rid2, 2, "QA_REVIEW", qa, qa_action="RETURN")
            await _event(c, rid2, 3, "ANALYST_DETERMINATION", analyst)
            assert await _count(c, rid2) == 3
        _txn(body)

    def test_self_qa_means_the_latest_determination_actor(self):
        """A QA by the OLD analyst on a superseding determination by someone else is independent;
        a QA by the superseding analyst is self-QA. Same rule the original trigger body applied."""
        a, b = uuid.uuid4(), uuid.uuid4()

        async def body(c):
            rid = await _new_review(c)
            await _event(c, rid, 1, "ANALYST_DETERMINATION", a)
            await _event(c, rid, 2, "SUPERSEDING_DETERMINATION", b)
            await _refused(c, APP, INSERT_QA, uuid.uuid4(), rid, 3, b, LONG, a, LONG, needle=SOD_MSG)
            await _as_role(c, APP, INSERT_QA, uuid.uuid4(), rid, 3, a, LONG, None, None)
            assert await _count(c, rid) == 3
        _txn(body)

    def test_qa_without_a_preceding_determination_is_still_refused(self):
        async def body(c):
            rid = await _new_review(c)
            await _refused(c, APP, INSERT_QA, uuid.uuid4(), rid, 1, uuid.uuid4(), LONG, None, None,
                           needle="no preceding determination")
        _txn(body)

    def test_update_is_refused_for_the_runtime_role_the_owner_and_the_superuser(self):
        analyst = uuid.uuid4()

        async def body(c):
            rid = await _new_review(c)
            await _event(c, rid, 1, "ANALYST_DETERMINATION", analyst)
            before = await c.fetchval("select md5(t::text) from review_decision_events t where review_id=$1", rid)
            sql = "update review_decision_events set rationale = $2 where review_id = $1"
            for role in (APP, OWNER, None):   # None = the connecting (super)user
                await _refused(c, role, sql, rid, "tampered rationale text", needle=APPEND_MSG)
            await _refused(c, APP, "update review_decision_events set actor_user_id = $2 where review_id = $1",
                           rid, uuid.uuid4(), needle=APPEND_MSG)
            assert before == await c.fetchval(
                "select md5(t::text) from review_decision_events t where review_id=$1", rid)
        _txn(body)

    def test_delete_and_truncate_are_refused_for_the_runtime_role(self):
        analyst = uuid.uuid4()

        async def body(c):
            rid = await _new_review(c)
            await _event(c, rid, 1, "ANALYST_DETERMINATION", analyst)
            await _refused(c, APP, "delete from review_decision_events where review_id = $1", rid,
                           needle=APPEND_MSG)
            await _refused(c, APP, "truncate review_decision_events", needle=APPEND_MSG)
            assert await _count(c, rid) == 1
        _txn(body)

    def test_owner_may_delete_synthetic_rows_documented_carve_out(self):
        """DELETE (not UPDATE) is open to the table owner: governed maintenance and the repository's
        own test cleanup need it, and the owner can DISABLE TRIGGER anyway. Documented residual risk."""
        analyst = uuid.uuid4()

        async def body(c):
            rid = await _new_review(c)
            await _event(c, rid, 1, "ANALYST_DETERMINATION", analyst)
            await _as_role(c, OWNER, "delete from review_decision_events where review_id = $1", rid)
            assert await _count(c, rid) == 0
        _txn(body)

    def test_runtime_role_cannot_disable_the_guards(self):
        async def body(c):
            for trg in ("trg_review_event_sod", "trg_review_event_append_only_rows",
                        "trg_review_event_append_only_trunc"):
                await _refused(c, APP, f"alter table review_decision_events disable trigger {trg}",
                               needle="must be owner")
            await _refused(c, APP, "drop trigger trg_review_event_append_only_rows on review_decision_events",
                           needle="must be owner")
        _txn(body)


# ── layer 2: migration lifecycle on a throw-away database ────────────────────

def _alembic(dbname, *args, expect_ok=True):
    u = _url()
    url = u.set(database=dbname).render_as_string(hide_password=False)
    env = dict(os.environ, DATABASE_URL=url, DB_MIGRATION_ROLE=OWNER, DB_APP_ROLE=APP,
               SECRET_KEY=os.environ.get("SECRET_KEY", "t" * 64), ALLOWED_HOSTS="*")
    r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=REPO, env=env,
                       capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, f"alembic {' '.join(args)} failed:\n{r.stdout[-1500:]}\n{r.stderr[-2500:]}"
    return r


@pytest.fixture(scope="module")
def scratch():
    """A fresh database, owned by the owner role, with the same schema grants the CI job uses."""
    if not ROLES_OK:
        pytest.skip(f"needs {OWNER}/{APP} roles: {GUARDS}")
    name = "sod_mig_" + uuid.uuid4().hex[:8]

    async def create():
        c = await _connect()
        try:
            await c.execute(f'create database "{name}" owner "{OWNER}"')
        finally:
            await c.close()
        c = await _connect(name)
        try:
            await c.execute(f'alter schema public owner to "{OWNER}"')
            await c.execute(f'grant usage, create on schema public to "{APP}"')
        finally:
            await c.close()
    try:
        run(create())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot create a scratch database: {exc}")
    yield name

    async def drop():
        c = await _connect()
        try:
            await c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                            "where datname = $1 and pid <> pg_backend_pid()", name)
            await c.execute(f'drop database if exists "{name}"')
        finally:
            await c.close()
    run(drop())


async def _snapshot(c):
    return {r["review_id"] + str(r["sequence_number"]): (r["md5"], r["xmin"]) for r in await c.fetch(
        "select review_id, sequence_number, md5(t::text) as md5, t.xmin::text as xmin "
        "from review_decision_events t order by 1, 2")}


async def _fn_md5(c, name):
    return await c.fetchval("select md5(prosrc) from pg_proc where proname = $1", name)


async def _guard_names(c):
    return {r[0] for r in await c.fetch(
        "select tgname from pg_trigger where tgrelid = 'review_decision_events'::regclass and not tgisinternal")}


class TestMigrationLifecycle:
    def test_history_survives_upgrade_downgrade_upgrade_and_preflight_fails_closed(self, scratch):
        db = scratch
        _alembic(db, "upgrade", PARENT)

        analyst, qa, grantor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        state = {}

        async def seed():
            c = await _connect(db)
            try:
                state["old_fn"] = await _fn_md5(c, "review_event_enforce_sod")
                assert await _guard_names(c) == {"trg_review_event_sod"}
                hist = await _new_review(c)   # HISTORICAL: a self-QA carrying an exception, legal then
                await _event(c, hist, 1, "ANALYST_DETERMINATION", analyst)
                await _event(c, hist, 2, "QA_REVIEW", analyst, qa_action="APPROVE", grantor=grantor, reason=LONG)
                clean = await _new_review(c)
                await _event(c, clean, 1, "ANALYST_DETERMINATION", analyst)
                await _event(c, clean, 2, "QA_REVIEW", qa, qa_action="APPROVE")
                state["hist"], state["before"] = hist, await _snapshot(c)
                assert len(state["before"]) == 4
            finally:
                await c.close()
        run(seed())

        # ── upgrade ──
        _alembic(db, "upgrade", "head")

        async def after_upgrade():
            c = await _connect(db)
            try:
                assert await c.fetchval("select version_num from alembic_version") == REVISION
                assert await _guard_names(c) == {"trg_review_event_sod", "trg_review_event_append_only_rows",
                                                 "trg_review_event_append_only_trunc"}
                assert await _snapshot(c) == state["before"], "migration modified or rewrote a row"
                # historical self-QA with an exception is still selectable, with its exception intact
                row = await c.fetchrow(
                    "select actor_user_id, sod_exception_granted_by, sod_exception_reason from "
                    "review_decision_events where review_id = $1 and sequence_number = 2", state["hist"])
                assert row["actor_user_id"] == analyst and row["sod_exception_granted_by"] == grantor
                assert await c.fetchval(
                    "select count(*) from review_effective_determination where review_id = $1", state["hist"]) == 1
                # …and a NEW self-QA is now refused, even on that same review
                with pytest.raises(asyncpg.PostgresError) as exc:
                    await _event(c, state["hist"], 3, "QA_REVIEW", analyst, qa_action="APPROVE",
                                 grantor=grantor, reason=LONG)
                assert SOD_MSG in str(exc.value)
                assert await _snapshot(c) == state["before"]
                state["new_fn"] = await _fn_md5(c, "review_event_enforce_sod")
                assert state["new_fn"] != state["old_fn"]
            finally:
                await c.close()
        run(after_upgrade())

        # ── downgrade: exact restoration of the old function, guards gone, rows untouched ──
        _alembic(db, "downgrade", "-1")

        async def after_downgrade():
            c = await _connect(db)
            try:
                assert await c.fetchval("select version_num from alembic_version") == PARENT
                assert await _guard_names(c) == {"trg_review_event_sod"}
                assert await _fn_md5(c, "review_event_enforce_sod") == state["old_fn"]
                assert await c.fetchval("select count(*) from pg_proc where proname = 'review_event_append_only_guard'") == 0
                assert await _snapshot(c) == state["before"]
            finally:
                await c.close()
        run(after_downgrade())

        # ── upgrade again (idempotent re-application) ──
        _alembic(db, "upgrade", "head")

        async def after_reapply():
            c = await _connect(db)
            try:
                assert await _fn_md5(c, "review_event_enforce_sod") == state["new_fn"]
                assert len(await _guard_names(c)) == 3
                assert await _snapshot(c) == state["before"]
            finally:
                await c.close()
        run(after_reapply())

    @pytest.mark.parametrize("what,statement", [
        ("table", f'alter table review_decision_events owner to "{APP}"'),
        ("function", f'alter function review_event_enforce_sod() owner to "{APP}"'),
    ])
    def test_preflight_refuses_when_the_runtime_role_owns_the_object(self, what, statement):
        if not ROLES_OK:
            pytest.skip(f"needs {OWNER}/{APP} roles: {GUARDS}")
        name = "sod_pf_" + uuid.uuid4().hex[:8]

        async def mk():
            c = await _connect()
            try:
                await c.execute(f'create database "{name}" owner "{OWNER}"')
            finally:
                await c.close()
            c = await _connect(name)
            try:
                await c.execute(f'alter schema public owner to "{OWNER}"')
                await c.execute(f'grant usage, create on schema public to "{APP}"')
            finally:
                await c.close()
        try:
            run(mk())
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"cannot create a scratch database: {exc}")
        try:
            _alembic(name, "upgrade", PARENT)

            async def own():
                c = await _connect(name)
                try:
                    await c.execute(statement)
                finally:
                    await c.close()
            run(own())
            r = _alembic(name, "upgrade", "head", expect_ok=False)
            assert r.returncode != 0
            assert "SodDbEnforcementPreconditionError" in r.stderr + r.stdout
            assert "Nothing was changed" in r.stderr + r.stdout

            async def unchanged():
                c = await _connect(name)
                try:
                    assert await c.fetchval("select version_num from alembic_version") == PARENT
                    assert await _guard_names(c) == {"trg_review_event_sod"}
                finally:
                    await c.close()
            run(unchanged())
        finally:
            async def drop():
                c = await _connect()
                try:
                    await c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                                    "where datname = $1 and pid <> pg_backend_pid()", name)
                    await c.execute(f'drop database if exists "{name}"')
                finally:
                    await c.close()
            run(drop())


class TestMissingObjects:
    def test_a_database_without_the_function_and_trigger_gets_both(self):
        """A database built from the models and stamped never ran 20260825: the function and trigger
        are absent. The migration creates them (a stronger control, never a weaker one)."""
        if not ROLES_OK:
            pytest.skip(f"needs {OWNER}/{APP} roles: {GUARDS}")
        name = "sod_mo_" + uuid.uuid4().hex[:8]

        async def mk():
            c = await _connect()
            try:
                await c.execute(f'create database "{name}" owner "{OWNER}"')
            finally:
                await c.close()
            c = await _connect(name)
            try:
                await c.execute(f'alter schema public owner to "{OWNER}"')
                await c.execute(f'grant usage, create on schema public to "{APP}"')
            finally:
                await c.close()
        try:
            run(mk())
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"cannot create a scratch database: {exc}")
        try:
            _alembic(name, "upgrade", PARENT)

            async def strip():
                c = await _connect(name)
                try:
                    await c.execute("drop trigger trg_review_event_sod on review_decision_events")
                    await c.execute("drop function review_event_enforce_sod()")
                finally:
                    await c.close()
            run(strip())
            _alembic(name, "upgrade", "head")

            async def check():
                c = await _connect(name)
                try:
                    assert len(await _guard_names(c)) == 3
                    rid = await _new_review(c)
                    analyst = uuid.uuid4()
                    await _event(c, rid, 1, "ANALYST_DETERMINATION", analyst)
                    with pytest.raises(asyncpg.PostgresError) as exc:
                        await _event(c, rid, 2, "QA_REVIEW", analyst, qa_action="APPROVE",
                                     grantor=uuid.uuid4(), reason=LONG)
                    assert SOD_MSG in str(exc.value)
                finally:
                    await c.close()
            run(check())
        finally:
            async def drop():
                c = await _connect()
                try:
                    await c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                                    "where datname = $1 and pid <> pg_backend_pid()", name)
                    await c.execute(f'drop database if exists "{name}"')
                finally:
                    await c.close()
            run(drop())


def test_alembic_chain_has_exactly_one_head_and_this_revision_follows_the_parent():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(os.path.join(REPO, "alembic.ini")))
    assert len(script.get_heads()) == 1, script.get_heads()
    assert script.get_revision(REVISION).down_revision == PARENT
