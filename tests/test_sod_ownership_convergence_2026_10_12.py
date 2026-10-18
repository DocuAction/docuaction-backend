"""Convergence tests for the ownership PREREQUISITE of 20261012_sod_db_enforcement (PR #151).

The managed PROD-like fixture in test_prod_managed_migration_integration.py makes
`docuaction_app` the owner of every table and (as PROD is believed to be today, UNVERIFIED) of the
SoD function, trigger and view and the `review_decision_events` table. The migration's preflight
(SodDbEnforcementPreconditionError) correctly refuses to install guards on objects the runtime role
owns. The DBA ownership prerequisite (docs/SOD_DB_OWNERSHIP_PREREQUISITE.sql) is therefore part of
the certified sequence BEFORE prepare -> migrate -> finalize. The end-to-end test in
test_prod_managed_migration_integration.py applies it (as a SIMULATED DBA identity, not a real one)
and expects success; this file holds the negative controls and the transition test:

  1. test_app_owned_fixture_is_refused_by_the_preflight
        the app-owned state is refused with the documented error, "Nothing was changed" for the SoD
        objects, and event rows are identical by count/md5/xmin.
  2. test_app_owned_table_with_owner_owned_function_is_still_refused
        the table-owner leg of the preflight.
  3. test_transition_app_owned_to_owner_owned_via_runbook_then_managed_path_succeeds
        the real transition: app-owned objects -> runbook (PRE-CHECKS, APPLY twice, POST-CHECKS) ->
        prepare -> migrate -> finalize; historical rows unchanged, independent QA works, self-QA is
        refused, and the runtime role cannot UPDATE/DELETE/TRUNCATE or disable/drop/alter the guards.
  4. test_runbook_refuses_wrong_identity_and_changes_nothing
        the runbook's own identity guard: run as the runtime role it aborts and changes nothing.
  5. test_finalize_refuses_a_drifted_grant_shape_instead_of_handing_the_table_back
        FINALIZE refuses (does not hand back, does not keep owner-owned) when the runtime role's
        privileges match neither the append-only shape nor the pre-prerequisite legacy nothing-granted
        shape -- an unexplained/drifted grant state is never silently converted into full ownership.
  6. test_finalize_hands_back_an_owner_owned_table_with_no_grants_at_all
        FINALIZE's other safe branch: the runtime role holding no privileges at all on an owner-owned
        table is handed the table back, since there is no guard in that state for a handback to strip.

Skips unless CONV_SUPERUSER_URL is set (same as the sibling fixture tests). Synthetic data only.
"""
import os
import re
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_prod_managed_migration_integration import (  # noqa: E402
    _SCRIPTS, SU, REPO, APP_PW, LEGACY_OWNER, MIGRATION_ID, ENTRA_ADMIN, EXPECTED, HEAD, PENDING, CHAIN_ALTERS,
    AREA1_FINAL, _build_managed_prod_like, _anchors, _tables, _version, _run_conv, _expect_db_error,
    _run_sql_as, _eng, _url_as, _owner, fixture_db,  # noqa: F401
    _seed_sod_history, _events_checksum, _apply_sod_runbook_as_simulated_dba,
)

pytestmark = pytest.mark.skipif(not SU, reason="CONV_SUPERUSER_URL not set (needs a superuser test DB)")

TABLE = "review_decision_events"
RUNBOOK = os.path.join(REPO, "docs", "SOD_DB_OWNERSHIP_PREREQUISITE.sql")
LONG = "a rationale that is long enough for the check constraint"


def _runbook_sections():
    text = open(RUNBOOK, encoding="utf-8").read()
    parts = re.split(r"^-- @@SECTION: (\w+).*$", text, flags=re.M)
    sections = {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}
    assert set(sections) == {"PRECHECKS", "APPLY", "POSTCHECKS", "ROLLBACK"}, sorted(sections)
    return sections


def _exec_script(url, sql):
    """Run a multi-statement script exactly as psql would (no parameter processing)."""
    conn = _eng(url).raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def _sod_fingerprint(su):
    from sqlalchemy import text
    with su.connect() as c:
        return {
            "version": _version(c),
            "table_owner": _owner(c, TABLE),
            "triggers": sorted(r[0] for r in c.execute(text(
                "select tgname from pg_trigger where tgrelid='public.review_decision_events'::regclass "
                "and not tgisinternal"))),
            "fn": c.execute(text(
                "select p.proowner::regrole::text, md5(p.prosrc) from pg_proc p "
                "where p.proname='review_event_enforce_sod'")).first(),
            "guard_fn": c.execute(text(
                "select count(*) from pg_proc where proname='review_event_append_only_guard'")).scalar(),
            "acl": c.execute(text("select relacl::text from pg_class where oid='public.review_decision_events'::regclass")).scalar(),
        }


def _assert_sod_objects_untouched(before, after):
    """Everything about review_decision_events/the SoD function except the alembic version."""
    assert {k: v for k, v in after.items() if k != "version"} == {k: v for k, v in before.items() if k != "version"}


def _prepare(admin):
    rp = _run_conv(admin, "--managed-prepare", "--i-understand-prepare-writes", "--bootstrap-as-role", LEGACY_OWNER)
    assert "MANAGED PREPARE COMPLETE" in rp.stdout, rp.stdout[-500:]


def _map_migration_identity(su):
    from sqlalchemy import text
    with su.connect() as c:
        c.execution_options(isolation_level="AUTOCOMMIT").execute(text(f"GRANT docuaction_owner TO {MIGRATION_ID}"))


def _migrate_args(fixture_db):
    return (_url_as(fixture_db, MIGRATION_ID), "--managed-migrate", "--i-understand-migration-writes")


# ─────────────────────────────────────────────────────────────────────────────
def test_app_owned_fixture_is_refused_by_the_preflight(fixture_db):
    """The guard is correct: with the runtime role owning the table/function the managed migrate
    stops BEFORE any DDL with the documented error, and leaves the database exactly as it was."""
    su = _build_managed_prod_like(fixture_db)
    _seed_sod_history(su)
    events_before = _events_checksum(su)
    assert events_before[0] == 4
    admin = _url_as(fixture_db, ENTRA_ADMIN)
    _prepare(admin)
    _map_migration_identity(su)
    before = _sod_fingerprint(su)
    assert before["table_owner"] == "docuaction_app" and before["fn"][0] == "docuaction_app"
    assert before["triggers"] == ["trg_review_event_sod"]

    r = _run_conv(*_migrate_args(fixture_db), expect_ok=False, extra_env={"CONV_ADMIN_ROLE": LEGACY_OWNER})
    out = r.stdout + r.stderr
    assert r.returncode != 0, out[-600:]
    assert "SodDbEnforcementPreconditionError" in out, out[-900:]
    # the first failing precondition in the preflight is the function owner check
    assert "review_event_enforce_sod() is owned by docuaction_app" in out, out[-900:]
    assert "Nothing was changed" in out, out[-900:]
    after = _sod_fingerprint(su)
    _assert_sod_objects_untouched(before, after)
    assert _events_checksum(su) == events_before, "refusal must leave review_decision_events rows byte-identical (count, md5, xmin)"
    # IMPORTANT (documented in the notes): 20261009_iqvia_affil_org_indexes runs CREATE INDEX CONCURRENTLY inside
    # alembic's autocommit_block, which COMMITS everything applied before it. So the refusal at 20261012 leaves
    # the database at the revision before 20261009 (earlier pending revisions committed), not at EXPECTED.
    # Nothing of 20261009/20261012 is stamped; a re-run after the ownership fix resumes from there.
    assert after["version"] == [_SCRIPTS.get_revision("20261009_iqvia_affil_org_indexes").down_revision]
    assert after["version"] != [HEAD]
    print("SOD_PREFLIGHT_APP_OWNED_FIXTURE=REFUSED_AS_DOCUMENTED (SoD objects untouched)")


def test_app_owned_table_with_owner_owned_function_is_still_refused(fixture_db):
    """Half-moved state (function moved, table not): the table-owner leg of the preflight refuses."""
    su = _build_managed_prod_like(fixture_db)
    _seed_sod_history(su)
    events_before = _events_checksum(su)
    from sqlalchemy import text
    with su.begin() as c:
        c.execute(text("ALTER FUNCTION review_event_enforce_sod() OWNER TO docuaction_owner"))
    admin = _url_as(fixture_db, ENTRA_ADMIN)
    _prepare(admin)
    _map_migration_identity(su)
    before = _sod_fingerprint(su)
    r = _run_conv(*_migrate_args(fixture_db), expect_ok=False, extra_env={"CONV_ADMIN_ROLE": LEGACY_OWNER})
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "SodDbEnforcementPreconditionError" in out, out[-900:]
    assert f"{TABLE} is owned by docuaction_app" in out and "Nothing was changed" in out, out[-900:]
    _assert_sod_objects_untouched(before, _sod_fingerprint(su))
    assert _events_checksum(su) == events_before


def _runtime_role_behaviour(fixture_db):
    """What the migration + ownership model must guarantee for the runtime role, on the finished DB."""
    from sqlalchemy import text
    app_url = _url_as(fixture_db, "docuaction_app", pw=APP_PW)
    rid = "REV-OWN-" + uuid.uuid4().hex[:8]
    analyst, qa = str(uuid.uuid4()), str(uuid.uuid4())

    def insert(c, seq, etype, actor, *, grantor=None, reason=None, qa_action=None):
        c.execute(text(
            "insert into review_decision_events (id, review_id, sequence_number, event_type, actor_user_id, "
            "actor_email, actor_role, rationale, qa_action, qa_reason, determination, "
            "sod_exception_granted_by, sod_exception_reason) values "
            "(:i,:r,:s,:t,:a,'x@example.invalid','reviewer',:l,:qa,:ql,:d,:g,:gr)"),
            {"i": str(uuid.uuid4()), "r": rid, "s": seq, "t": etype, "a": actor, "l": LONG, "qa": qa_action,
             "ql": LONG if qa_action else None, "d": "CONFIRM" if etype != "QA_REVIEW" else None,
             "g": grantor, "gr": reason})

    with _eng(app_url).begin() as c:        # the app creates its review and records the determination
        c.execute(text("insert into review_records (id, review_id, source_record_id) values (:i,:r,:s)"),
                  {"i": str(uuid.uuid4()), "r": rid, "s": str(uuid.uuid4())})
        insert(c, 1, "ANALYST_DETERMINATION", analyst)

    # (a) the SoD trigger refuses self-QA - plain, and with a self-attested exception
    for kwargs in ({}, {"grantor": str(uuid.uuid4()), "reason": LONG}):
        msg = _expect_db_error(lambda kw=kwargs: _insert_in_txn(app_url, insert, 2, "QA_REVIEW", analyst,
                                                                  qa_action="APPROVE", **kw),
                               "segregation of duties")
        assert "self-attested exception fields are not accepted" in msg      # the NEW body is the active one
    # (b) an independent QA is accepted
    with _eng(app_url).begin() as c:
        insert(c, 2, "QA_REVIEW", qa, qa_action="APPROVE")
        assert c.execute(text("select count(*) from review_decision_events where review_id=:r"), {"r": rid}).scalar() == 2
    # (c) append-only: the privilege layer refuses first, for each statement
    for sql in ("UPDATE review_decision_events SET rationale = rationale",
                "DELETE FROM review_decision_events",
                "TRUNCATE review_decision_events"):
        _expect_db_error(lambda s=sql: _run_sql_as(app_url, s), "permission denied")
    # (d) the runtime role cannot remove or weaken the guards (it is not the owner)
    for sql in ("ALTER TABLE review_decision_events DISABLE TRIGGER trg_review_event_sod",
                "ALTER TABLE review_decision_events DISABLE TRIGGER trg_review_event_append_only_rows",
                "ALTER TABLE review_decision_events DISABLE TRIGGER ALL",
                "DROP TRIGGER trg_review_event_sod ON review_decision_events",
                "DROP TRIGGER trg_review_event_append_only_trunc ON review_decision_events",
                "CREATE OR REPLACE FUNCTION review_event_enforce_sod() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RETURN NEW; END $$",
                "ALTER TABLE review_decision_events OWNER TO docuaction_app",
                "SET ROLE docuaction_owner"):
        _expect_db_error(lambda s=sql: _run_sql_as(app_url, s), "must be owner", "permission denied", "cannot", "insufficient")
    # (e) the dependent view is still readable by the runtime role
    with _eng(app_url).begin() as c:
        assert c.execute(text("select count(*) from review_effective_determination where review_id=:r"),
                         {"r": rid}).scalar() == 1


def _insert_in_txn(url, insert, seq, etype, actor, **kw):
    with _eng(url).begin() as c:
        insert(c, seq, etype, actor, **kw)


def _managed_path_and_assertions(fixture_db, su, *, via):
    """prepare -> migrate -> finalize on a DB whose table/function are owner-owned, then verify."""
    from sqlalchemy import text
    admin = _url_as(fixture_db, ENTRA_ADMIN)
    with su.connect() as c:
        pre = _anchors(c)
    events_before = _events_checksum(su)
    _prepare(admin)
    # review_decision_events is owner-owned and is NOT in the temporarily-re-owned list
    assert TABLE not in CHAIN_ALTERS
    _map_migration_identity(su)
    rm = _run_conv(*_migrate_args(fixture_db), extra_env={"CONV_ADMIN_ROLE": LEGACY_OWNER})
    assert "MANAGED MIGRATE COMPLETE" in rm.stdout, rm.stdout[-800:]
    assert f"chain: {EXPECTED} -> {HEAD} ({len(PENDING)} pending: {PENDING})" in rm.stdout
    rf = _run_conv(admin, "--finalize", "--i-understand-finalize-writes", "--bootstrap-as-role", LEGACY_OWNER)
    assert "FINALIZE COMPLETE" in rf.stdout, rf.stdout[-400:]
    with su.connect() as c:
        assert _version(c) == [HEAD]
        final = _tables(c)
        assert final[TABLE] == "docuaction_owner", "FINALIZE must not hand the table back to the runtime role"
        assert sorted(t for t, o in final.items() if o == "docuaction_owner") == \
            sorted(AREA1_FINAL + ["alembic_version", TABLE])
        assert _anchors(c) == pre
        assert _events_checksum(su) == events_before, "historical events must be byte-identical (count, md5, xmin)"
        assert c.execute(text("select count(*) from review_decision_events where actor_user_id = "
                              "sod_exception_granted_by or sod_exception_granted_by is not null")).scalar() == 1,             "the historical self-QA-with-exception row is still present and readable"
        assert sorted(r[0] for r in c.execute(text(
            "select tgname from pg_trigger where tgrelid='public.review_decision_events'::regclass and not tgisinternal"))) == \
            ["trg_review_event_append_only_rows", "trg_review_event_append_only_trunc", "trg_review_event_sod"]
        assert c.execute(text("select p.proowner::regrole::text from pg_proc p where proname in "
                              "('review_event_enforce_sod','review_event_append_only_guard') group by 1")).scalars().all() \
            == ["docuaction_owner"]
        privs = {p: c.execute(text("select has_table_privilege('docuaction_app','public.review_decision_events',:p)"),
                              {"p": p}).scalar()
                 for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER", "REFERENCES")}
        assert privs == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False, "TRUNCATE": False,
                         "TRIGGER": False, "REFERENCES": False}, privs
    _runtime_role_behaviour(fixture_db)
    print(f"SOD_OWNER_OWNED_END_STATE=PASS via={via} (prepare -> migrate -> finalize; self-QA refused; "
          f"UPDATE/DELETE/TRUNCATE/DISABLE/DROP refused for docuaction_app)")


def test_transition_app_owned_to_owner_owned_via_runbook_then_managed_path_succeeds(fixture_db):
    """THE POSITIVE TRANSITION TEST. Starts in the state PROD is modelled in TODAY (table, SoD function and view
    app-owned, historical events present, including a self-QA that carries a self-attested exception), applies
    docs/SOD_DB_OWNERSHIP_PREREQUISITE.sql as the SIMULATED DBA identity (scratch role legacy_owner standing in
    for the server admin; no real DBA/Azure identity), then runs prepare -> migrate -> finalize."""
    from sqlalchemy import text
    su = _build_managed_prod_like(fixture_db)
    _seed_sod_history(su)
    hist = _events_checksum(su)
    assert hist[0] == 4
    sections = _runbook_sections()
    dba = _url_as(fixture_db, LEGACY_OWNER)          # models the server admin (member of both roles)

    # read-only PRE-CHECKS run cleanly and change nothing
    before = _sod_fingerprint(su)
    _exec_script(dba, sections["PRECHECKS"])
    assert _sod_fingerprint(su) == before
    with su.connect() as c:
        assert c.execute(text("select pg_has_role('docuaction_app', c.relowner, 'USAGE') from pg_class c "
                              "where c.oid='public.review_decision_events'::regclass")).scalar() is True

    # APPLY (+ in-transaction post-checks), then again: idempotent
    _exec_script(dba, sections["APPLY"])
    first = _sod_fingerprint(su)
    assert first["table_owner"] == "docuaction_owner" and first["fn"][0] == "docuaction_owner"
    assert first["acl"] is not None and "docuaction_app=ar/docuaction_owner" in first["acl"], first["acl"]
    _exec_script(dba, sections["APPLY"])
    assert _sod_fingerprint(su) == first, "the runbook must be idempotent"
    _exec_script(dba, sections["POSTCHECKS"])
    assert _sod_fingerprint(su) == first, "POST-CHECKS are read-only"
    assert _events_checksum(su) == hist, "the ownership move must not touch a single event row"

    # the state the migration requires is now met: the managed path succeeds end to end
    _managed_path_and_assertions(fixture_db, su, via="simulated-dba-runbook-transition")
    print("SOD_TRANSITION_TEST=PASS (applied twice; POSTCHECKS ok; managed path then succeeds)")


def test_runbook_refuses_wrong_identity_and_changes_nothing(fixture_db):
    su = _build_managed_prod_like(fixture_db)
    before = _sod_fingerprint(su)
    app = _url_as(fixture_db, "docuaction_app", pw=APP_PW)
    msg = _expect_db_error(lambda: _exec_script(app, _runbook_sections()["APPLY"]), "prerequisite")
    assert "cannot act as docuaction_owner" in msg
    assert _sod_fingerprint(su) == before
    # a broken membership (owner role is a member of the runtime role) is also refused
    from sqlalchemy import text
    with su.connect() as c:
        c.execution_options(isolation_level="AUTOCOMMIT").execute(text("GRANT docuaction_app TO docuaction_owner"))
    msg = _expect_db_error(lambda: _exec_script(_url_as(fixture_db, LEGACY_OWNER), _runbook_sections()["APPLY"]),
                           "prerequisite")
    assert "docuaction_owner is a member of docuaction_app" in msg
    assert _sod_fingerprint(su) == before


def test_finalize_refuses_a_drifted_grant_shape_instead_of_handing_the_table_back(fixture_db):
    """FINALIZE must NOT transfer review_decision_events to docuaction_app on an unexplained grant shape
    -- one that matches neither the append-only prerequisite shape (SELECT+INSERT only) nor the
    pre-prerequisite legacy shape (nothing granted). It refuses outright, and ownership, grants and
    historical rows are left exactly as they were (CodeRabbit finding on PR #151 head 64af1587: the
    prior code silently handed the table to docuaction_app on ANY shape other than the exact append-only
    one, which would have converted an unknown/drifted grant state into full ownership -- a widening of
    access that defeats the whole point of the SoD guard)."""
    from sqlalchemy import text
    su = _build_managed_prod_like(fixture_db)
    _apply_sod_runbook_as_simulated_dba(fixture_db)
    events_before = _events_checksum(su)
    with su.begin() as c:
        c.execute(text(f"GRANT UPDATE ON {TABLE} TO docuaction_app"))      # drift: not the append-only shape
    before = _sod_fingerprint(su)
    admin = _url_as(fixture_db, ENTRA_ADMIN)
    _prepare(admin)
    rf = _run_conv(admin, "--finalize", "--i-understand-finalize-writes", "--bootstrap-as-role", LEGACY_OWNER,
                   expect_ok=False)
    assert rf.returncode != 0 and "REFUSED (finalize ownership drift)" in (rf.stdout + rf.stderr), \
        (rf.stdout + rf.stderr)[-800:]
    with su.connect() as c:
        assert _tables(c)[TABLE] == "docuaction_owner", "a refused FINALIZE must not hand the table back"
    assert _sod_fingerprint(su) == before, "a refused FINALIZE must leave ownership and grants unchanged"
    assert _events_checksum(su) == events_before, "a refused FINALIZE must not touch a single event row"
    print("SOD_FINALIZE_DRIFT_REFUSAL=PASS (drifted grant shape refused; ownership/grants/rows unchanged)")


def test_finalize_hands_back_an_owner_owned_table_with_no_grants_at_all(fixture_db):
    """FINALIZE's other safe branch: an owner-owned review_decision_events on which the runtime role
    holds NOTHING (ownership moved to docuaction_owner without the DBA prerequisite's SELECT/INSERT grant
    ever running -- not the documented runbook path, but not drift either, since there is no partial state
    to misinterpret: nothing was ever granted, so there is nothing a handback could strip) is handed to
    docuaction_app, exactly as if the prerequisite had never run and the table had stayed app-owned."""
    from sqlalchemy import text
    su = _build_managed_prod_like(fixture_db)
    events_before = _events_checksum(su)
    with su.begin() as c:
        c.execute(text(f"ALTER TABLE {TABLE} OWNER TO docuaction_owner"))
        c.execute(text(f"REVOKE ALL ON TABLE {TABLE} FROM docuaction_app"))
    admin = _url_as(fixture_db, ENTRA_ADMIN)
    _prepare(admin)
    rf = _run_conv(admin, "--finalize", "--i-understand-finalize-writes", "--bootstrap-as-role", LEGACY_OWNER)
    assert "FINALIZE COMPLETE" in rf.stdout, rf.stdout[-400:]
    with su.connect() as c:
        assert _tables(c)[TABLE] == "docuaction_app"
    assert _events_checksum(su) == events_before
    print("SOD_FINALIZE_LEGACY_NONE_HANDBACK=PASS (zero prior grants; table handed back, rows untouched)")
