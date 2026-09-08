"""Already-managed PROD (docuaction-db-geo) migration path against a real
disposable PostgreSQL (superuser test DB, e.g. the CI postgres:16 service).

Reproduces the REAL measured PROD starting state (read-only Gate 3 discovery of
docuaction-db-geo, 2026-09-08 - metadata only, no Government data):

  * alembic_version = 20260829_report_artifacts (repo head 20260903_delivery_grants,
    exactly five revisions pending)
  * 93 public tables: the 72 candidate tables + 20 legacy-only + alembic_version
  * docuaction_app  LOGIN, least privilege, OWNS 89 tables incl. alembic_version
  * docuaction_owner NOLOGIN, least privilege, OWNS exactly the four Area-1 tables
    (rce_source_records / rce_source_intakes / rce_ingestion_runs /
    rce_rule_execution_history); docuaction_app holds SELECT+INSERT on them only
  * legacy_owner models pgadmin (non-superuser server admin: CREATEDB CREATEROLE
    BYPASSRLS, member of BOTH roles WITH ADMIN); entra_admin models the Entra
    admin (NOINHERIT, can SET ROLE legacy_owner, never escalated)
  * migration_identity models the dedicated migration SP (least privilege; made
    a member of docuaction_owner ONLY by an explicit mapping step)
  * decisions is missing its 18 model-only columns; review_records is missing
    its 3 (those three are added, with their indexes, by 20260831_review_case)
  * rce_delivery_jobs and report_export_jobs are absent (created by the chain)

Proves the certified sequence for that state - MANAGED PREPARE (owner) ->
MANAGED MIGRATE (migration identity) -> FINALIZE (owner) - plus every fail-
closed gate. Skips unless CONV_SUPERUSER_URL is set.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_prod_convergence_integration import (  # noqa: E402
    SU, CONV, APP_PW, LEGACY_OWNER, MIGRATION_ID, _ALL_ROLES, _md, _eng, _url_as, fixture_db,  # noqa: F401
    _run_conv, _expect_db_error, _run_sql_as, _attrs, _member, _owner,
)

pytestmark = pytest.mark.skipif(not SU, reason="CONV_SUPERUSER_URL not set (needs a superuser test DB)")

EXPECTED = "20260829_report_artifacts"
HEAD = "20260903_delivery_grants"
PENDING = ["20260830_run_lifecycle", "20260831_review_case", "20260831_export_jobs",
           "20260902_delivery_jobs", "20260903_delivery_grants"]
DECISIONS_COLS = ["approval_justification", "rejection_reason", "rejection_category", "supersedes", "sla_hours",
                  "deadline", "escalation_level", "escalated_to", "escalated_at", "is_overdue", "outcome_text",
                  "outcome_date", "outcome_matched", "outcome_notes", "outcome_recorded_by",
                  "required_approver_role", "approval_threshold_usd", "domain"]
REVIEW_COLS = ["source_record_id", "assigned_to_user_id", "assigned_at"]
LEGACY_ONLY = ["area1_mutation_log", "automation_rules", "bulletin_articles", "bulletin_audit_log",
               "bulletin_briefings", "bulletin_cost_logs", "bulletin_delivery_log", "bulletin_recipients",
               "bulletin_run_log", "bulletin_search_profiles", "bulletin_source_outcome",
               "bulletin_source_registry", "document_comparisons", "document_patterns",
               "document_relationships", "output_templates", "report_artifacts", "structured_extractions",
               "tefca_qa_audit", "validation_queue"]
AREA1_PRESENT = ["rce_source_records", "rce_source_intakes", "rce_ingestion_runs", "rce_rule_execution_history"]
AREA1_FINAL = AREA1_PRESENT + ["rce_delivery_jobs"]
ENTRA_ADMIN = "entra_admin"
CK = "ck_review_record_has_subject"


def _cols(conn, table):
    import sqlalchemy as sa
    return {c["name"] for c in sa.inspect(conn).get_columns(table, schema="public")}


def _tables(conn):
    from sqlalchemy import text
    return dict(conn.execute(text(
        "select relname, relowner::regrole::text from pg_class where relkind='r' "
        "and relnamespace='public'::regnamespace")).all())


def _version(conn):
    from sqlalchemy import text
    return conn.execute(text("select version_num from alembic_version")).scalars().all()


def _anchors(conn):
    """Row count + PK-set digest per populated table. Never row contents."""
    from sqlalchemy import text
    out = {}
    for t, pk in (("users", "id"), ("audit_logs", "id"), ("bulletin_articles", "id")):
        out[t] = conn.execute(text(
            f'select count(*), md5(coalesce(string_agg("{pk}"::text, \',\' order by "{pk}"::text), \'\')) '
            f'from public."{t}"')).first()
    return out


def _role_count(conn):
    from sqlalchemy import text
    return conn.execute(text("select count(*) from pg_roles")).scalar()


def _build_managed_prod_like(url):
    """The REAL docuaction-db-geo starting shape (see module docstring)."""
    from sqlalchemy import text
    md = _md()
    eng = _eng(url)
    dbname = url.rsplit("/", 1)[1]
    with eng.begin() as c:
        for r in _ALL_ROLES:
            c.execute(text(f'DROP ROLE IF EXISTS "{r}"'))
        # pgadmin-like server admin: non-superuser, CREATEDB CREATEROLE BYPASSRLS (measured)
        c.execute(text(f"CREATE ROLE {LEGACY_OWNER} LOGIN PASSWORD 'x' NOSUPERUSER CREATEDB CREATEROLE BYPASSRLS"))
        c.execute(text(f"CREATE ROLE docuaction_app LOGIN PASSWORD '{APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"))
        c.execute(text("CREATE ROLE docuaction_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"))
        c.execute(text(f"CREATE ROLE {MIGRATION_ID} LOGIN PASSWORD 'x' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"))
        c.execute(text(f"CREATE ROLE {ENTRA_ADMIN} LOGIN PASSWORD 'x' NOSUPERUSER NOINHERIT CREATEROLE NOCREATEDB NOBYPASSRLS"))
        c.execute(text(f"GRANT {LEGACY_OWNER} TO {ENTRA_ADMIN}"))
        c.execute(text(f"GRANT docuaction_app TO {LEGACY_OWNER} WITH ADMIN OPTION"))
        c.execute(text(f"GRANT docuaction_owner TO {LEGACY_OWNER} WITH ADMIN OPTION"))
        # PROD: schema public is owned by azure_pg_admin, of which pgadmin is a member;
        # the equivalent privilege relationship here is the admin model owning it.
        c.execute(text(f"ALTER SCHEMA public OWNER TO {LEGACY_OWNER}"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dbname}" TO docuaction_app'))
        c.execute(text("GRANT USAGE, CREATE ON SCHEMA public TO docuaction_app"))
        # docuaction_owner's schema privileges are deliberately NOT granted here:
        # PREPARE must ensure them (the live value was not measured).
        md.create_all(bind=c)                                    # the 72 candidate tables
        c.execute(text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY)"))
        c.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": EXPECTED})
        for t in LEGACY_ONLY:
            c.execute(text(f'CREATE TABLE "{t}" (id uuid primary key, note text)'))
        c.execute(text("INSERT INTO bulletin_articles (id, note) VALUES (:i, 'legacy-only')"), {"i": str(uuid.uuid4())})
    with eng.begin() as c:
        # the measured model-only drift (indexes on the dropped columns go with them)
        for cn in DECISIONS_COLS:
            c.execute(text(f'ALTER TABLE public.decisions DROP COLUMN "{cn}"'))
        for cn in REVIEW_COLS:
            c.execute(text(f'ALTER TABLE public.review_records DROP COLUMN "{cn}"'))
        c.execute(text(f'ALTER TABLE public.review_records DROP CONSTRAINT IF EXISTS "{CK}"'))
        c.execute(text("ALTER TABLE public.review_records ALTER COLUMN entity_id SET NOT NULL"))
        # ownership model exactly as measured
        for tn in _tables(c):
            c.execute(text(f'ALTER TABLE public."{tn}" OWNER TO docuaction_app'))
        for tn in AREA1_PRESENT:
            c.execute(text(f'ALTER TABLE public."{tn}" OWNER TO docuaction_owner'))
            c.execute(text(f'GRANT SELECT, INSERT ON public."{tn}" TO docuaction_app'))
        c.execute(text("GRANT UPDATE (promotion_status, canonical_entity_id) ON public.rce_source_records TO docuaction_app"))
        # representative existing rows (synthetic)
        for i in range(2):
            c.execute(text(
                "insert into users (id,tenant_id,email,password_hash,full_name,company,role,plan,allowed_modules,"
                "is_active,is_verified,status,created_at,updated_at,last_active_at) values "
                "(:i,'t',:e,'x','U','C','viewer','free','[]'::json,true,true,'active',now(),now(),now())"),
                {"i": str(uuid.uuid4()), "e": f"u{i}@synthetic.invalid"})
        c.execute(text("insert into audit_logs (id,tenant_id,action,created_at) values (:i,'t','seed',now())"),
                  {"i": str(uuid.uuid4())})
    with eng.connect() as c:
        tables = _tables(c)
        assert len(tables) == 93, len(tables)
        assert sum(1 for o in tables.values() if o == "docuaction_app") == 89
        assert sorted(t for t, o in tables.items() if o == "docuaction_owner") == sorted(AREA1_PRESENT)
        assert _version(c) == [EXPECTED]
        assert not (set(DECISIONS_COLS) & _cols(c, "decisions")) and not (set(REVIEW_COLS) & _cols(c, "review_records"))
    return eng


def _set_version(url, value):
    from sqlalchemy import text
    with _eng(url).begin() as c:
        c.execute(text("update alembic_version set version_num=:v"), {"v": value})


# ─────────────────────────────────────────────────────────────────────────────
def test_managed_gate_fails_closed_on_every_lineage_deviation(fixture_db):
    from sqlalchemy import text
    _build_managed_prod_like(fixture_db)
    admin = _url_as(fixture_db, ENTRA_ADMIN)

    # expected-revision gate PASS (dry-run, writes nothing)
    r = _run_conv(admin, "--managed-prepare", "--bootstrap-as-role", LEGACY_OWNER)
    assert "gate PASSED" in r.stdout and f"pending revisions (5)   : {PENDING}" in r.stdout, r.stdout[-600:]
    assert "MANAGED PREPARE DRY-RUN" in r.stdout
    with _eng(fixture_db).connect() as c:
        assert _version(c) == [EXPECTED] and set(DECISIONS_COLS).isdisjoint(_cols(c, "decisions"))
    print("MANAGED_GATE_EXPECTED_REVISION=PASS")

    def refused(*args, needle, env=None):
        rr = _run_conv(admin, *args, "--bootstrap-as-role", LEGACY_OWNER, expect_ok=False, extra_env=env)
        out = rr.stdout + rr.stderr
        assert rr.returncode != 0 and "REFUSED (managed gate)" in out and needle in out, out[-500:]

    # wrong revision
    _set_version(fixture_db, "20260828_area1_grants")
    refused("--managed-prepare", needle="certified expected revision")
    # already at head (DB ahead)
    _set_version(fixture_db, HEAD)
    refused("--managed-prepare", needle="already at head")
    # unknown revision
    _set_version(fixture_db, "deadbeefcafe")
    refused("--managed-prepare", needle="certified expected revision")
    refused("--managed-prepare", "--expected-revision", "deadbeefcafe", needle="unknown to this repository")
    # multiple rows
    _set_version(fixture_db, EXPECTED)
    with _eng(fixture_db).begin() as c:
        c.execute(text("insert into alembic_version values ('20260828_area1_grants')"))
    refused("--managed-prepare", needle="expected exactly one")
    with _eng(fixture_db).begin() as c:
        c.execute(text("delete from alembic_version where version_num<>:v"), {"v": EXPECTED})
    # schema fingerprint: an unexpected chain-created table already present
    with _eng(fixture_db).begin() as c:
        c.execute(text("create table report_export_jobs (id uuid primary key)"))
        c.execute(text("alter table report_export_jobs owner to docuaction_app"))
    refused("--managed-prepare", needle="already present before the chain")
    with _eng(fixture_db).begin() as c:
        c.execute(text("drop table report_export_jobs"))
    # ownership fingerprint: a table owned by a foreign role
    with _eng(fixture_db).begin() as c:
        c.execute(text(f"alter table public.documents owner to {LEGACY_OWNER}"))
    refused("--managed-prepare", needle="owned by neither")
    with _eng(fixture_db).begin() as c:
        c.execute(text("alter table public.documents owner to docuaction_app"))
    # MIGRATE before PREPARE (alembic_version still app-owned)
    rr = _run_conv(_url_as(fixture_db, MIGRATION_ID), "--managed-migrate", "--i-understand-migration-writes",
                   expect_ok=False, extra_env={"CONV_ADMIN_ROLE": LEGACY_OWNER})
    assert rr.returncode != 0 and "run --managed-prepare first" in (rr.stdout + rr.stderr)
    # the legacy steps refuse the managed database (guards intact); Bootstrap A never runs here
    for args in (("--bootstrap", "--i-understand-bootstrap-writes"), ("--migrate", "--i-understand-migration-writes")):
        rr = _run_conv(admin, *args, "--bootstrap-as-role", LEGACY_OWNER, expect_ok=False,
                       extra_env={"CONV_APP_PASSWORD": APP_PW})
        assert rr.returncode != 0 and "already present" in (rr.stdout + rr.stderr)
    # and a legacy-shaped database refuses the managed path
    with _eng(fixture_db).begin() as c:
        c.execute(text("drop table alembic_version"))
    refused("--managed-prepare", needle="ABSENT")
    with _eng(fixture_db).connect() as c:
        assert set(DECISIONS_COLS).isdisjoint(_cols(c, "decisions")), "no gate refusal may write"
    print("MANAGED_GATE_WRONG_REVISION=FAIL_CLOSED (wrong/ahead/unknown/multi-row/fingerprint/ownership/legacy-shape)")


def test_managed_prepare_migrate_finalize_end_to_end(fixture_db):
    import sqlalchemy as sa
    from sqlalchemy import text
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    su = _build_managed_prod_like(fixture_db)
    admin = _url_as(fixture_db, ENTRA_ADMIN)
    mig_url = _url_as(fixture_db, MIGRATION_ID)
    with su.connect() as c:
        pre = _anchors(c); pre_roles = _role_count(c); pre_tables = _tables(c)
        assert _attrs(c, MIGRATION_ID)[:4] == (False, False, False, False)
        assert _attrs(c, ENTRA_ADMIN)[0] is False and _attrs(c, LEGACY_OWNER)[0] is False

    # ── MANAGED PREPARE (entra_admin -> SET ROLE legacy_owner; no owner password) ──
    rp = _run_conv(admin, "--managed-prepare", "--i-understand-prepare-writes", "--bootstrap-as-role", LEGACY_OWNER)
    assert "MANAGED PREPARE COMPLETE" in rp.stdout, rp.stdout[-500:]
    assert f"owner context: session_user={ENTRA_ADMIN}  current_user(after SET ROLE)={LEGACY_OWNER}" in rp.stdout
    assert "18 model-only column(s) added on ('decisions',)" in rp.stdout and "alembic_version re-owned=True" in rp.stdout
    assert "temporarily re-owned for the chain=['review_records']" in rp.stdout
    with su.connect() as c:
        assert set(DECISIONS_COLS) <= _cols(c, "decisions")
        assert set(REVIEW_COLS).isdisjoint(_cols(c, "review_records")), "review_records columns belong to the chain"
        assert _owner(c, "alembic_version") == "docuaction_owner" and _version(c) == [EXPECTED]
        assert _owner(c, "review_records") == "docuaction_owner", "single-table temporary re-own for 20260831_review_case"
        assert _role_count(c) == pre_roles, "PREPARE must create no role"
        now = _tables(c)
        touched = {"alembic_version", "review_records"}
        assert {t: o for t, o in now.items() if t not in touched} == {t: o for t, o in pre_tables.items() if t not in touched}
        assert all(now[t] == "docuaction_app" for t in LEGACY_ONLY) and all(now[t] == "docuaction_owner" for t in AREA1_PRESENT)
        assert _anchors(c) == pre
        assert c.execute(text("select has_schema_privilege('docuaction_owner','public','CREATE')")).scalar()
        assert not sa.inspect(c).has_table("report_export_jobs")
    # idempotent: a second PREPARE adds nothing and re-owns nothing
    rp2 = _run_conv(admin, "--managed-prepare", "--i-understand-prepare-writes", "--bootstrap-as-role", LEGACY_OWNER)
    assert "0 model-only column(s) added" in rp2.stdout and "alembic_version re-owned=False" in rp2.stdout
    assert "temporarily re-owned for the chain=[]" in rp2.stdout
    # the application keeps working on review_records during the PREPARE -> FINALIZE window
    with _eng(_url_as(fixture_db, "docuaction_app", pw=APP_PW)).begin() as c:
        c.execute(text("select count(*) from review_records"))
        c.execute(text("select domain from decisions limit 1"))
    with su.connect() as c:
        assert len(_cols(c, "decisions")) == len(_md().tables["decisions"].columns)
    print("MANAGED_PREPARE=PASS (18 columns applied exactly once; alembic_version -> owner; no roles; no re-own)")

    # ── migration SP mapping (explicit operator step, modelled) ──
    with su.connect() as c:
        c.execution_options(isolation_level="AUTOCOMMIT").execute(text(f"GRANT docuaction_owner TO {MIGRATION_ID}"))
    _expect_db_error(lambda: _run_sql_as(mig_url, f"SET ROLE {LEGACY_OWNER}"), "permission denied", "cannot", "insufficient")
    _expect_db_error(lambda: _run_sql_as(mig_url, "SET ROLE docuaction_app"), "permission denied", "cannot", "insufficient")
    _expect_db_error(lambda: _run_sql_as(mig_url, "ALTER TABLE public.decisions ADD COLUMN zz int"),
                     "must be owner", "permission denied")
    print("MIGRATION_IDENTITY_LEAST_PRIVILEGE=PASS (cannot SET ROLE admin/app; cannot ALTER app-owned tables)")

    # ── MANAGED MIGRATE (migration_identity) ──
    rm = _run_conv(mig_url, "--managed-migrate", "--i-understand-migration-writes",
                   extra_env={"CONV_ADMIN_ROLE": LEGACY_OWNER})
    assert "MANAGED MIGRATE COMPLETE" in rm.stdout, rm.stdout[-800:]
    assert f"identity proof: session_user={MIGRATION_ID}" in rm.stdout
    assert "direct_memberships=['docuaction_owner']" in rm.stdout
    assert f"cannot SET ROLE {LEGACY_OWNER}/docuaction_app=proven" in rm.stdout
    assert "current_user(after SET ROLE)=docuaction_owner" in rm.stdout
    assert f"chain: {EXPECTED} -> {HEAD} (5 pending: {PENDING})" in rm.stdout
    assert f"second upgrade head: NO-OP (version={HEAD}, heads=1, pending=0)" in rm.stdout
    cfg = Config(os.path.join(os.path.dirname(CONV), "..", "alembic.ini"))
    assert ScriptDirectory.from_config(cfg).get_heads() == [HEAD]
    with su.connect() as c:
        assert _version(c) == [HEAD]
        assert set(REVIEW_COLS) <= _cols(c, "review_records")
        assert c.execute(text("select count(*) from pg_constraint where conname=:n"), {"n": CK}).scalar() == 1
        assert _owner(c, "rce_delivery_jobs") == "docuaction_owner" and _owner(c, "report_export_jobs") == "docuaction_owner"
        privs = {p: c.execute(text("select has_table_privilege('docuaction_app','rce_delivery_jobs',:p)"), {"p": p}).scalar()
                 for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
        assert privs == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False}, privs
        assert _anchors(c) == pre
        assert _role_count(c) == pre_roles
    # second MIGRATE refuses: database is now at head
    rm2 = _run_conv(mig_url, "--managed-migrate", "--i-understand-migration-writes", expect_ok=False,
                    extra_env={"CONV_ADMIN_ROLE": LEGACY_OWNER})
    assert rm2.returncode != 0 and "already at head" in (rm2.stdout + rm2.stderr)
    print(f"MANAGED_MIGRATE=PASS (5 revisions -> {HEAD}; heads=1; pending=0; rerun no-op)")

    # ── FINALIZE (entra_admin -> SET ROLE legacy_owner) ──
    rf = _run_conv(admin, "--finalize", "--i-understand-finalize-writes", "--bootstrap-as-role", LEGACY_OWNER)
    assert "FINALIZE COMPLETE" in rf.stdout and "2 non-Area-1 tables" in rf.stdout, rf.stdout[-400:]
    with su.connect() as c:
        final = _tables(c)
        assert len(final) == 95
        for t in AREA1_FINAL:
            assert final[t] == "docuaction_owner", f"{t} must be docuaction_owner"
        assert final["alembic_version"] == "docuaction_owner"
        assert final["report_export_jobs"] == "docuaction_app"
        assert final["review_records"] == "docuaction_app", "FINALIZE returns the temporarily re-owned table"
        assert all(final[t] == "docuaction_app" for t in LEGACY_ONLY), "legacy-only untouched"
        assert sorted(t for t, o in final.items() if o == "docuaction_owner") == sorted(AREA1_FINAL + ["alembic_version"])
        assert _anchors(c) == pre and _version(c) == [HEAD]
        assert not _member(c, "docuaction_app", "docuaction_owner") and not _member(c, "docuaction_owner", "docuaction_app")
        assert not _member(c, MIGRATION_ID, "docuaction_app") and not _member(c, MIGRATION_ID, LEGACY_OWNER)
        assert _attrs(c, MIGRATION_ID)[:4] == (False, False, False, False)
        assert _attrs(c, ENTRA_ADMIN)[0] is False and _attrs(c, "docuaction_app")[:4] == (False, False, False, False)
    print("FINAL_OWNERSHIP_DEV_MODEL=PASS")

    # ── application survives, protected DELETE denied, app cannot escalate ──
    app_url = _url_as(fixture_db, "docuaction_app", pw=APP_PW)
    with _eng(app_url).begin() as c:
        su_, cu_ = c.execute(text("select session_user, current_user")).first()
        assert (su_, cu_) == ("docuaction_app", "docuaction_app")
        assert c.execute(text("select count(*) from users")).scalar() == pre["users"][0]
        c.execute(text("select domain, sla_hours from decisions limit 1"))              # new columns readable
        c.execute(text("update users set last_active_at=now()"))
        c.execute(text("insert into audit_logs (id,tenant_id,action,created_at) values (:i,'t','app',now())"),
                  {"i": str(uuid.uuid4())})
        c.execute(text("select count(*) from rce_source_records"))
        c.execute(text("insert into report_export_jobs (id,identity,export_type,source_intake_id,classification,"
                       "generator_version,requested_by) values (:i,'x','x',:s,'x','x','x')"),
                  {"i": str(uuid.uuid4()), "s": str(uuid.uuid4())})
        c.execute(text("insert into rce_delivery_jobs (id,identity,original_filename,storage_path,sha256,"
                       "file_size_bytes,registered_by) values (:i,'x','f','p','h',1,'x')"), {"i": str(uuid.uuid4())})
    _expect_db_error(lambda: _run_sql_as(app_url, "DELETE FROM rce_delivery_jobs"), "permission denied")
    _expect_db_error(lambda: _run_sql_as(app_url, "SET ROLE docuaction_owner"), "permission denied", "cannot", "insufficient")
    _expect_db_error(lambda: _run_sql_as(app_url, "ALTER TABLE public.rce_source_records OWNER TO docuaction_app"),
                     "must be owner", "permission denied", "insufficient")
    _expect_db_error(lambda: _run_sql_as(app_url, "UPDATE rce_source_records SET raw_line='x'"), "permission denied")
    print("APP_RUNTIME=PASS  PROTECTED_DELETE_DENIED=PASS  NEG_APP_CANNOT_ESCALATE=PASS")
    print("DB_GEO_MANAGED_PATH=PASS (prepare -> migrate -> finalize; rows/PKs preserved; boundary intact)")


def test_managed_forced_chain_failure_fails_closed(fixture_db):
    import sqlalchemy as sa
    from sqlalchemy import text
    su = _build_managed_prod_like(fixture_db)
    admin = _url_as(fixture_db, ENTRA_ADMIN)
    mig_url = _url_as(fixture_db, MIGRATION_ID)
    _run_conv(admin, "--managed-prepare", "--i-understand-prepare-writes", "--bootstrap-as-role", LEGACY_OWNER)
    with su.connect() as c:
        c.execution_options(isolation_level="AUTOCOMMIT").execute(text(f"GRANT docuaction_owner TO {MIGRATION_ID}"))
        pre = _anchors(c)
    # a decoy the gate cannot see (index-name collision) makes 20260831_export_jobs fail mid-chain
    with _eng(mig_url).begin() as c:
        c.execute(text("SET ROLE docuaction_owner"))
        c.execute(text("CREATE INDEX ix_report_export_jobs_identity ON public.rce_ingestion_runs (id)"))
    r = _run_conv(mig_url, "--managed-migrate", "--i-understand-migration-writes", expect_ok=False,
                  extra_env={"CONV_ADMIN_ROLE": LEGACY_OWNER})
    assert r.returncode != 0, r.stdout[-600:]
    with su.connect() as c:
        assert _version(c) == [EXPECTED], "a failed chain must leave the version exactly where it was (no stamp, no partial)"
        assert not sa.inspect(c).has_table("report_export_jobs") and not sa.inspect(c).has_table("rce_delivery_jobs")
        assert set(REVIEW_COLS).isdisjoint(_cols(c, "review_records")), "partial revisions must roll back"
        assert _anchors(c) == pre
    print(f"MANAGED_FORCED_FAILURE=FAIL_CLOSED rev={EXPECTED} recovery=EXPLICIT_REPAIR_OR_PITR")
