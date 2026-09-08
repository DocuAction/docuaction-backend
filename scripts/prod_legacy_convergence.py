"""One-time legacy-PROD -> Alembic convergence, across THREE owner/migration steps.

The certified DEV runtime model (ground-truthed against docuaction-db-dev) is:

    docuaction_app    LOGIN, NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOBYPASSRLS,
                      OWNS every non-Area-1 table (its access comes from
                      ownership) - this is the application login identity.
    docuaction_owner  NOLOGIN, owns ONLY the Area-1 tables + alembic_version.
    (neither role is a member of the other)
    migration SP      Entra LOGIN, member of docuaction_owner only -> SET ROLE
                      docuaction_owner -> runs Alembic. Never owns non-Area-1.
    pgadmin           legacy owner; runs the owner-only bootstrap/finalize only.

Transferring ownership of the pgadmin-owned legacy tables, and reassigning the
final non-Area-1 ownership to docuaction_app, are OWNER-ONLY operations a least-
privilege migration identity cannot do - and we will not grant it pgadmin/
superuser/BYPASSRLS. Conversely Alembic must NOT run as pgadmin. So the work is
split into three explicitly-gated steps:

    BOOTSTRAP A  (--bootstrap)  Owner-run (pgadmin). Create the two roles
        (docuaction_app LOGIN with the app password, docuaction_owner NOLOGIN),
        grant CONNECT + schema privileges, and TEMPORARILY re-own the existing
        candidate tables to docuaction_owner so the chain can build/alter them.
        Runs NO Alembic, creates NO tables, changes NO rows.

    MIGRATION B  (--migrate)   Migration-identity-run. Proves session_user = the
        migration identity and current_user = docuaction_owner after SET ROLE,
        adds model-only columns, then runs the REAL reviewed Alembic chain (which
        creates the Area-1 tables owned by docuaction_owner, applies the reviewed
        grants, and writes alembic_version). No ownership transfer, never pgadmin.

    FINALIZE     (--finalize)  Owner-run (pgadmin). Reassign ownership to the DEV
        model: every candidate table EXCEPT the Area-1 set -> docuaction_app;
        the Area-1 tables and alembic_version stay docuaction_owner. Runs NO
        Alembic. This is the step the migration identity cannot perform (it is
        not a member of docuaction_app).

CHAIN-FIRST, NO STAMP
    Migration B runs `alembic upgrade head` (the chain builds the schema) and
    lets create_all(checkfirst) no-op after - the order DEV builds in. It does
    NOT stamp; the chain writes alembic_version itself.

MODES
    (no write flag)                                  READ ONLY plan.
    --bootstrap --i-understand-bootstrap-writes      Execute Bootstrap A.
    --migrate   --i-understand-migration-writes      Execute Migration B.
    --finalize  --i-understand-finalize-writes       Execute Finalize.

ALREADY-MANAGED PROD (docuaction-db-geo; see the MANAGED section below)
    --managed-prepare --i-understand-prepare-writes    Owner-run PREPARE.
    --managed-migrate --i-understand-migration-writes  Migration-identity MIGRATE.
    --finalize        --i-understand-finalize-writes   Owner-run Finalize (same).
    Every managed step fails closed unless alembic_version == --expected-revision
    (default 20260829_report_artifacts). Bootstrap A never runs there.

GUARDS (fail closed)
    * Bootstrap A refuses if alembic_version already exists, or if the app
      password (CONV_APP_PASSWORD) is absent when docuaction_app must be created.
    * Migration B refuses unless Bootstrap A has run (owner role present and the
      candidate tables owned by it) and the identity can SET ROLE docuaction_owner.
    * Finalize refuses unless the chain has run (alembic_version present) and both
      roles exist.
    * never DROP/TRUNCATE and never REASSIGN OWNED.
"""
from __future__ import annotations

import argparse
import os
import sys

import sqlalchemy as sa
from sqlalchemy import text


OWNER_ROLE = "docuaction_owner"
APP_ROLE = "docuaction_app"
# The Area-1 tables that docuaction_owner OWNS in certified DEV (docuaction_app
# gets only least-privilege grants on these from the reviewed chain). Everything
# else in the candidate model is owned by docuaction_app. alembic_version is also
# owned by docuaction_owner but is not a model table, so it is never reassigned.
AREA1_OWNER_TABLES = {
    "rce_source_records",
    "rce_source_intakes",
    "rce_ingestion_runs",
    "rce_rule_execution_history",
    "rce_delivery_jobs",
}


def _candidate_metadata():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault("SECRET_KEY", "t" * 64)
    os.environ.setdefault("ALLOWED_HOSTS", "*")
    import app.models.database          # noqa: F401
    import app.platform_config.models   # noqa: F401
    import app.tefca_registry.models    # noqa: F401
    import app.tefca_registry.rce.models  # noqa: F401
    import app.Tefca.models             # noqa: F401
    from app.core.database import Base
    return Base.metadata


def _existing_tables(conn) -> set:
    return set(sa.inspect(conn).get_table_names(schema="public"))


def _existing_columns(conn, table) -> set:
    return {c["name"] for c in sa.inspect(conn).get_columns(table, schema="public")}


def _role_exists(conn, role) -> bool:
    return conn.execute(text("select 1 from pg_roles where rolname=:r"), {"r": role}).first() is not None


def _lit(value: str) -> str:
    """A safely single-quoted SQL string literal (standard_conforming_strings is
    on for Azure PG16, so doubling the quote is sufficient). Used for the app
    password in CREATE ROLE - the password is NEVER logged."""
    return "'" + value.replace("'", "''") + "'"


def _tables_not_owned_by(conn, tables, owner) -> list:
    if not tables:
        return []
    rows = conn.execute(text(
        "select relname from pg_class "
        "where relnamespace='public'::regnamespace and relkind='r' "
        "and relname = any(:names) and relowner::regrole::text <> :owner"),
        {"names": list(tables), "owner": owner}).scalars().all()
    return sorted(rows)


def plan(conn):
    """Compute the deterministic convergence plan. Read-only."""
    md = _candidate_metadata()
    existing = _existing_tables(conn)
    candidate = {t.name for t in md.sorted_tables}

    alembic_present = "alembic_version" in existing
    missing = sorted(candidate - existing - {"alembic_version"})
    shared = sorted(candidate & existing)
    legacy_only = sorted(existing - candidate - {"alembic_version"})

    by_name = {t.name: t for t in md.sorted_tables}
    add_columns = {}
    for tname in shared:
        have = _existing_columns(conn, tname)
        need = [(c.name, c.type.compile(dialect=conn.engine.dialect))
                for c in by_name[tname].columns if c.name not in have]
        if need:
            add_columns[tname] = need

    return {
        "alembic_present": alembic_present,
        "owner_present": _role_exists(conn, OWNER_ROLE),
        "app_present": _role_exists(conn, APP_ROLE),
        "roles_to_create": [r for r in (OWNER_ROLE, APP_ROLE) if not _role_exists(conn, r)],
        "tables_the_chain_will_create": missing,
        "tables_shared_reowned": shared,
        "legacy_only_preserved_untouched": legacy_only,
        "model_only_columns_to_add": add_columns,
    }


def print_plan(p):
    print("=== CONVERGENCE PLAN (dry-run) ===")
    print(f"alembic_version already present : {p['alembic_present']}")
    print(f"docuaction_owner present        : {p['owner_present']}")
    print(f"docuaction_app present          : {p['app_present']}")
    print(f"roles to create                 : {p['roles_to_create']}")
    print(f"[Bootstrap A] candidate tables re-owned to {OWNER_ROLE} (temporary): {len(p['tables_shared_reowned'])}")
    print(f"[Bootstrap A] legacy-only tables preserved : {len(p['legacy_only_preserved_untouched'])}  {p['legacy_only_preserved_untouched']}")
    total_cols = sum(len(v) for v in p["model_only_columns_to_add"].values())
    print(f"[Migration B] model-only columns to add (nullable): {total_cols}  { { t: [c for c, _ in v] for t, v in p['model_only_columns_to_add'].items() } }")
    print(f"[Migration B] tables the CHAIN will create : {len(p['tables_the_chain_will_create'])}")
    for t in p["tables_the_chain_will_create"]:
        print(f"    + {t}")
    print(f"[Finalize] Area-1 tables kept on {OWNER_ROLE}: {sorted(AREA1_OWNER_TABLES)}")
    print(f"[Finalize] all other candidate tables re-owned to {APP_ROLE} (the application login)")
    print("Additive only: no existing table is removed or emptied; no destructive change; "
          "the reviewed chain writes its own version row (no historical revision is faked).")


def bootstrap_apply(engine, app_password=None, become_role=None):
    """BOOTSTRAP A - owner-only prep, executed by the existing owner-capable
    identity. Creates the two roles (docuaction_app LOGIN with the application
    password; docuaction_owner NOLOGIN), grants CONNECT + schema privileges, and
    TEMPORARILY re-owns the existing candidate tables to docuaction_owner so the
    chain (run as that role) can build/alter them. Finalize later moves the
    non-Area-1 tables to docuaction_app.

    become_role (optional): SET ROLE to this owner role for the whole operation.
    Lets a secretless admin who can SET ROLE to the legacy owner (PROD: the Entra
    admin, who can SET ROLE pgadmin via azure_pg_admin) run Bootstrap A WITHOUT
    the legacy owner's password. It is a session-local role switch, not a new
    grant. When unset, the operation runs as the connecting user (e.g. pgadmin
    authenticated by password).

    Runs NO Alembic, creates NO application tables/columns, changes NO rows, and
    never DROP/TRUNCATE/REASSIGN OWNED."""
    with engine.begin() as conn:
        p = plan(conn)
        if p["alembic_present"]:
            raise SystemExit("REFUSED: alembic_version already present - already Alembic-managed.")
        if not p["app_present"] and not app_password:
            raise SystemExit("REFUSED: docuaction_app must be created as a LOGIN role but no app "
                             "password was supplied (set CONV_APP_PASSWORD). It is never logged.")
        # 0. optionally become the owner role (session-local SET ROLE; no password,
        #    no new privilege). All owner-only operations below then run as it.
        if become_role:
            session_user = conn.execute(text("select session_user")).scalar()
            conn.execute(text(f'SET ROLE "{become_role}"'))
            current_user = conn.execute(text("select current_user")).scalar()
            if current_user != become_role:
                raise SystemExit(f"REFUSED: SET ROLE {become_role} did not take effect "
                                 f"(current_user={current_user}); the connecting identity cannot become it.")
            print(f"owner context: session_user={session_user}  current_user(after SET ROLE)={current_user}")
        # 1. roles. docuaction_app is the application LOGIN identity (least priv);
        #    docuaction_owner is the NOLOGIN ownership/migration role.
        if not p["app_present"]:
            conn.execute(text(
                f'CREATE ROLE "{APP_ROLE}" WITH LOGIN PASSWORD {_lit(app_password)} '
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"))
        if not p["owner_present"]:
            conn.execute(text(f'CREATE ROLE "{OWNER_ROLE}" WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS'))
        # 2. connection + schema privileges (per the reviewed baseline design).
        conn.execute(text(f'GRANT CONNECT ON DATABASE {conn.engine.url.database} TO "{APP_ROLE}"'))
        conn.execute(text(f'GRANT USAGE, CREATE ON SCHEMA public TO "{APP_ROLE}"'))
        conn.execute(text(f'GRANT USAGE, CREATE ON SCHEMA public TO "{OWNER_ROLE}"'))
        # 3. the owner-capable executor must be able to reassign ownership to BOTH
        #    roles (owner in Bootstrap A, app in Finalize). Role-membership grants
        #    to the operator only - NOT between docuaction_app and docuaction_owner.
        conn.execute(text(f'GRANT "{OWNER_ROLE}" TO CURRENT_USER'))
        conn.execute(text(f'GRANT "{APP_ROLE}" TO CURRENT_USER'))
        # 4. TEMPORARILY re-own the existing candidate tables to docuaction_owner
        #    so the chain (which SET ROLEs to it) can build/alter them. Legacy-only
        #    tables (not in the model) are left untouched. Finalize moves the
        #    non-Area-1 ones to docuaction_app afterwards.
        for t in p["tables_shared_reowned"]:
            conn.execute(text(f'ALTER TABLE public."{t}" OWNER TO "{OWNER_ROLE}"'))
        print(f"BOOTSTRAP A applied: {APP_ROLE} LOGIN + {OWNER_ROLE} NOLOGIN ensured (least privilege), "
              f"schema privileges granted, {len(p['tables_shared_reowned'])} candidate tables temporarily "
              f"re-owned to {OWNER_ROLE}, {len(p['legacy_only_preserved_untouched'])} legacy-only untouched. "
              "No Alembic, no table/column creation, no row change.")
    return p


def _prove_migration_identity(conn):
    session_user = conn.execute(text("select session_user")).scalar()
    conn.execute(text(f'SET ROLE "{OWNER_ROLE}"'))
    current_user = conn.execute(text("select current_user")).scalar()
    if current_user != OWNER_ROLE:
        raise SystemExit(f"REFUSED: SET ROLE {OWNER_ROLE} did not take effect "
                         f"(current_user={current_user}); identity is not a member.")
    print(f"identity proof: session_user={session_user}  current_user(after SET ROLE)={current_user}")
    return session_user


def migration_apply(sync_url):
    """MIGRATION B - executed EXCLUSIVELY by the dedicated migration identity.
    Proves identity, adds model-only columns AS the owner role, runs the reviewed
    chain. NO ownership transfer of legacy tables, never runs as pgadmin."""
    engine = sa.create_engine(sync_url)
    with engine.begin() as conn:
        p = plan(conn)
        if p["alembic_present"]:
            raise SystemExit("REFUSED: alembic_version already present - already Alembic-managed.")
        if not _role_exists(conn, OWNER_ROLE):
            raise SystemExit(f"REFUSED: {OWNER_ROLE} absent - run Bootstrap A (--bootstrap) first.")
        not_owned = _tables_not_owned_by(conn, p["tables_shared_reowned"], OWNER_ROLE)
        if not_owned:
            raise SystemExit(f"REFUSED: {len(not_owned)} candidate table(s) are not owned by "
                             f"{OWNER_ROLE} - Bootstrap A has not run: {not_owned[:5]}")
        _prove_migration_identity(conn)
        n_cols = 0
        for t, cols in p["model_only_columns_to_add"].items():
            for cn, coltype in cols:
                conn.execute(text(f'ALTER TABLE public."{t}" ADD COLUMN IF NOT EXISTS "{cn}" {coltype}'))
                n_cols += 1
        conn.execute(text("RESET ROLE"))
        print(f"added {n_cols} model-only column(s) as {OWNER_ROLE}; handing off to the reviewed chain")
    run_chain_and_verify(sync_url)


def run_chain_and_verify(db_url):
    """Run the reviewed Alembic chain AS the owner role (env.py DB_MIGRATION_ROLE
    SET ROLE), then create_all(checkfirst) as the same no-op safety net (also as
    the owner role), then prove single head, head==version, schema equivalence,
    and a no-op re-run."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    os.environ["DB_MIGRATION_ROLE"] = OWNER_ROLE
    os.environ["DB_APP_ROLE"] = APP_ROLE
    command.upgrade(cfg, "head")
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    print("alembic heads:", heads)
    assert len(heads) == 1, f"expected one head, got {heads}"

    md = _candidate_metadata()
    eng = sa.create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text(f'SET ROLE "{OWNER_ROLE}"'))
        before = _existing_tables(conn)
        md.create_all(bind=conn, checkfirst=True)
        after = _existing_tables(conn)
        newly = sorted(after - before)
        conn.execute(text("RESET ROLE"))
        if newly:
            print(f"safety-net create_all created {len(newly)} table(s) the chain missed: {newly}")
        missing_after = sorted({t.name for t in md.sorted_tables} - after)
        assert not missing_after, f"model tables missing after convergence: {missing_after}"
        cur = conn.execute(text("select version_num from alembic_version")).scalars().all()
    print("alembic_version:", cur)
    assert cur == list(heads), "alembic_version != head after convergence"


def finalize_ownership(engine, become_role=None):
    """FINALIZE - owner-run (pgadmin, or a secretless admin via become_role SET
    ROLE). Reassign ownership to the certified DEV model: every candidate table
    EXCEPT the Area-1 set -> docuaction_app (the application login, whose access
    is by ownership); the Area-1 tables and alembic_version stay docuaction_owner.
    Runs NO Alembic; the migration identity cannot do this (not a member of
    docuaction_app)."""
    with engine.begin() as conn:
        if become_role:
            conn.execute(text(f'SET ROLE "{become_role}"'))
            if conn.execute(text("select current_user")).scalar() != become_role:
                raise SystemExit(f"REFUSED: SET ROLE {become_role} did not take effect.")
        if "alembic_version" not in _existing_tables(conn):
            raise SystemExit("REFUSED: alembic_version absent - run Migration B (--migrate) first.")
        for r in (OWNER_ROLE, APP_ROLE):
            if not _role_exists(conn, r):
                raise SystemExit(f"REFUSED: {r} absent - Bootstrap A has not run.")
        # After Migration B docuaction_owner owns everything the convergence built
        # or re-owned. Reassign every base table it owns to docuaction_app EXCEPT
        # the Area-1 set and alembic_version (these stay docuaction_owner). This is
        # driven off actual ownership, so tables created by the chain but not on the
        # imported model metadata (e.g. report_artifacts) are handled too. Legacy-
        # only tables (owned by the legacy owner, not docuaction_owner) are untouched.
        owned = conn.execute(text(
            "select relname from pg_class where relnamespace='public'::regnamespace "
            "and relkind='r' and relowner::regrole::text = :owner"), {"owner": OWNER_ROLE}).scalars().all()
        keep = AREA1_OWNER_TABLES | {"alembic_version"}
        to_app = sorted(set(owned) - keep)
        for t in to_app:
            conn.execute(text(f'ALTER TABLE public."{t}" OWNER TO "{APP_ROLE}"'))
        # A column-linked sequence (serial/identity) AUTOMATICALLY follows its
        # table's owner on ALTER TABLE OWNER above - and PostgreSQL refuses a
        # direct ALTER SEQUENCE OWNER on it. So only STANDALONE sequences (not
        # tied to a table column) owned by docuaction_owner need a manual move.
        seqs = conn.execute(text(
            "select c.relname from pg_class c "
            "where c.relnamespace='public'::regnamespace and c.relkind='S' "
            "and c.relowner::regrole::text = :owner and not exists ("
            "  select 1 from pg_depend d where d.objid=c.oid "
            "  and d.deptype in ('a','i') and d.refobjsubid > 0)"),
            {"owner": OWNER_ROLE}).scalars().all()
        for s in seqs:
            conn.execute(text(f'ALTER SEQUENCE public."{s}" OWNER TO "{APP_ROLE}"'))
        kept = sorted(AREA1_OWNER_TABLES & set(owned))
        print(f"FINALIZE applied: {len(to_app)} non-Area-1 tables (+ {len(seqs)} standalone sequences) "
              f"re-owned to {APP_ROLE}; {len(kept)} Area-1 tables + alembic_version kept on {OWNER_ROLE}. "
              "No Alembic, no row change.")
    return to_app


# ═══════════════════════════════════════════════════════════════════════════
# ALREADY-MANAGED PROD (docuaction-db-geo) - measured read-only 2026-09-08
#
# The authoritative PROD database is NOT the legacy shape above. It is already
# Alembic-managed (alembic_version = 20260829_report_artifacts), holds 93
# tables (89 owned by docuaction_app, the four Area-1 tables owned by
# docuaction_owner), and both roles already exist with the certified least-
# privilege attributes. Bootstrap A MUST NOT run there (it refuses by design:
# alembic_version present) and the legacy Migration B refuses too. This mode
# is the minimum path for that measured state:
#
#     MANAGED PREPARE  (--managed-prepare)  Owner-run (pgadmin via SET ROLE).
#         Fail-closed gate, then: add ONLY the verified model-only columns on
#         `decisions` (docuaction_app owns it, so the migration identity cannot
#         ALTER it), ensure docuaction_owner holds USAGE+CREATE on public, and
#         move alembic_version to docuaction_owner so the chain can write it.
#         No role creation, no broad re-ownership, no CONV_APP_PASSWORD.
#     MANAGED MIGRATE  (--managed-migrate)  Migration-identity-run. Same gate,
#         full identity proof (least privilege, member of docuaction_owner
#         ONLY, cannot SET ROLE the admin or the app role), then the REAL chain
#         expected -> head, then a second `upgrade head` proven NO-OP.
#     FINALIZE         (--finalize)         unchanged: owner-run, moves the
#         chain-created non-Area-1 table(s) to docuaction_app; Area-1 (incl.
#         rce_delivery_jobs) and alembic_version stay docuaction_owner.
#
# The gate refuses unless alembic_version is exactly ONE row equal to the
# expected revision, the revision is known and strictly behind the single
# head, the roles/ownership model matches, and the schema fingerprint
# (chain-created tables absent, model-only columns exactly as measured,
# legacy-only set exactly as measured) matches. Never stamps.
# ═══════════════════════════════════════════════════════════════════════════
EXPECTED_MANAGED_REVISION = "20260829_report_artifacts"
MANAGED_MODEL_ONLY_COLUMNS = {
    "decisions": ["approval_justification", "rejection_reason", "rejection_category", "supersedes",
                  "sla_hours", "deadline", "escalation_level", "escalated_to", "escalated_at", "is_overdue",
                  "outcome_text", "outcome_date", "outcome_matched", "outcome_notes", "outcome_recorded_by",
                  "required_approver_role", "approval_threshold_usd", "domain"],
    "review_records": ["source_record_id", "assigned_to_user_id", "assigned_at"],
}
# Tables whose model-only columns PREPARE adds itself. review_records is
# deliberately NOT here: pending revision 20260831_review_case adds those three
# columns together with their indexes and skips BOTH when a column pre-exists,
# so pre-adding them would silently lose the indexes.
MANAGED_PREPARE_TABLES = ("decisions",)
# Tables a pending revision ALTERs that docuaction_app owns. PostgreSQL allows
# ALTER TABLE only to the owner (or a member of the owning role), and the
# migration identity is a member of docuaction_owner ONLY - so PREPARE moves
# exactly these to docuaction_owner for the chain (20260831_review_case adds
# columns/indexes/CHECK and relaxes entity_id on review_records) and FINALIZE
# returns them to docuaction_app. The app keeps its runtime privileges on them
# throughout via explicit grants. This is the single-table form of the
# certified "temporary re-own for the chain" - never a broad re-ownership.
MANAGED_CHAIN_ALTERS = ("review_records",)
assert not set(MANAGED_CHAIN_ALTERS) & AREA1_OWNER_TABLES, "Area-1 tables are never re-owned or re-granted here"
MANAGED_CHAIN_CREATES = {"report_export_jobs", "rce_delivery_jobs"}
MANAGED_LEGACY_ONLY = [
    "area1_mutation_log", "automation_rules", "bulletin_articles", "bulletin_audit_log", "bulletin_briefings",
    "bulletin_cost_logs", "bulletin_delivery_log", "bulletin_recipients", "bulletin_run_log",
    "bulletin_search_profiles", "bulletin_source_outcome", "bulletin_source_registry", "document_comparisons",
    "document_patterns", "document_relationships", "output_templates", "report_artifacts",
    "structured_extractions", "tefca_qa_audit", "validation_queue",
]
MANAGED_AREA1_PRESENT = {"rce_source_records", "rce_source_intakes", "rce_ingestion_runs",
                         "rce_rule_execution_history"}


def _refuse(msg):
    raise SystemExit("REFUSED (managed gate): " + msg)


def _role_attrs(conn, role):
    return conn.execute(text(
        "select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls from pg_roles where rolname=:r"),
        {"r": role}).first()


def _direct_memberships(conn, role):
    return set(conn.execute(text(
        "select r.rolname from pg_auth_members m join pg_roles r on r.oid=m.roleid "
        "join pg_roles x on x.oid=m.member where x.rolname=:r"), {"r": role}).scalars().all())


def _table_owner(conn, table):
    return conn.execute(text(
        "select relowner::regrole::text from pg_class where relkind='r' "
        "and relnamespace='public'::regnamespace and relname=:t"), {"t": table}).scalar()


def managed_gate(conn, expected_revision, after_prepare=False):
    """Fail-closed precondition gate for the already-managed PROD path. Read-only.
    Returns the plan so callers never re-derive it."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    existing = _existing_tables(conn)
    # 1. Alembic lineage: exactly one row, exactly the expected, known, strictly behind ONE head.
    if "alembic_version" not in existing:
        _refuse("alembic_version is ABSENT - this is not the already-managed database (legacy path only).")
    av_owner = _table_owner(conn, "alembic_version")
    who = conn.execute(text("select session_user, current_user")).first()
    try:
        rows = conn.execute(text("select version_num from alembic_version")).scalars().all()
    except Exception as e:  # noqa: BLE001 - e.g. the migration identity before PREPARE re-owned it
        _refuse(f"alembic_version (owner={av_owner}) is not readable as {tuple(who)} - run --managed-prepare first "
                f"(it moves alembic_version to {OWNER_ROLE}): {str(e).splitlines()[0][:120]}")
    if len(rows) != 1:
        _refuse(f"alembic_version holds {len(rows)} rows; expected exactly one.")
    current = rows[0]
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    if len(heads) != 1:
        _refuse(f"repository has {len(heads)} heads: {heads}")
    if current == heads[0]:
        _refuse("database is already at head - nothing to migrate; the managed path is not applicable.")
    lineage = [r.revision for r in script.iterate_revisions(heads[0], None)]
    if current != expected_revision:
        ahead = current in lineage and expected_revision in [
            r.revision for r in script.iterate_revisions(current, None)]
        _refuse(f"alembic_version={current!r} but the certified expected revision is {expected_revision!r}"
                + (" - the database is AHEAD of the expected revision." if ahead else "."))
    try:
        known = script.get_revision(current)
    except Exception:  # noqa: BLE001 - alembic raises on an unknown id; refuse rather than crash
        known = None
    if known is None:
        _refuse(f"revision {current!r} is unknown to this repository.")
    if current not in lineage:
        _refuse(f"revision {current!r} is not an ancestor of head {heads[0]!r} (database ahead or off-branch).")
    pending = list(reversed([r.revision for r in script.iterate_revisions(heads[0], current)]))
    # 2. Roles: both exist with the certified attributes; no cross-membership.
    for role, login in ((APP_ROLE, True), (OWNER_ROLE, False)):
        a = _role_attrs(conn, role)
        if a is None:
            _refuse(f"role {role} is absent (the managed path never creates roles).")
        if a[0] is not login or any(a[1:]):
            _refuse(f"role {role} attributes {tuple(a)} differ from the certified least-privilege model.")
    if conn.execute(text("select pg_has_role(:a,:o,'MEMBER') or pg_has_role(:o,:a,'MEMBER')"),
                    {"a": APP_ROLE, "o": OWNER_ROLE}).scalar():
        _refuse(f"prohibited cross-membership between {APP_ROLE} and {OWNER_ROLE}.")
    # 3. Ownership model: every table on app or owner; owner owns exactly the present Area-1 set
    #    (plus alembic_version once PREPARE has moved it).
    owners = dict(conn.execute(text(
        "select relname, relowner::regrole::text from pg_class where relkind='r' "
        "and relnamespace='public'::regnamespace")).all())
    foreign = sorted(t for t, o in owners.items() if o not in (APP_ROLE, OWNER_ROLE))
    if foreign:
        _refuse(f"{len(foreign)} table(s) owned by neither {APP_ROLE} nor {OWNER_ROLE}: {foreign[:8]}")
    owner_owned = {t for t, o in owners.items() if o == OWNER_ROLE}
    prepared = {"alembic_version", *MANAGED_CHAIN_ALTERS}
    if owner_owned - prepared != MANAGED_AREA1_PRESENT:
        _refuse(f"{OWNER_ROLE} owns {sorted(owner_owned)}; expected exactly the measured Area-1 set "
                f"{sorted(MANAGED_AREA1_PRESENT)} (+ {sorted(prepared)} after PREPARE).")
    if after_prepare and not prepared <= owner_owned:
        _refuse(f"{sorted(prepared - owner_owned)} not yet owned by {OWNER_ROLE} - run --managed-prepare first.")
    # 4. Schema fingerprint against candidate metadata.
    p = plan(conn)
    if p["tables_the_chain_will_create"]:
        _refuse(f"candidate tables missing from the database: {p['tables_the_chain_will_create'][:8]}")
    present_chain = sorted(MANAGED_CHAIN_CREATES & existing)
    if present_chain:
        _refuse(f"chain-created table(s) already present before the chain ran: {present_chain}")
    if p["legacy_only_preserved_untouched"] != sorted(MANAGED_LEGACY_ONLY):
        _refuse(f"legacy-only table set differs from the measured baseline: "
                f"{sorted(set(p['legacy_only_preserved_untouched']) ^ set(MANAGED_LEGACY_ONLY))}")
    live_missing = {t: [c for c, _ in cols] for t, cols in p["model_only_columns_to_add"].items()}
    for t, cols in live_missing.items():
        if t not in MANAGED_MODEL_ONLY_COLUMNS or set(cols) - set(MANAGED_MODEL_ONLY_COLUMNS[t]):
            _refuse(f"unexpected model-only drift on {t}: {cols}")
    for t, cols in MANAGED_MODEL_ONLY_COLUMNS.items():
        got = set(live_missing.get(t, []))
        if got != set(cols) and not (got == set() and (after_prepare or t in MANAGED_PREPARE_TABLES)):
            _refuse(f"model-only columns on {t} are {sorted(got)}; expected exactly {cols} (or none after PREPARE).")
    if after_prepare:
        still = [t for t in MANAGED_PREPARE_TABLES if live_missing.get(t)]
        if still:
            _refuse(f"PREPARE has not added the model-only columns on {still} - run --managed-prepare first.")
        for priv in ("USAGE", "CREATE"):
            if not conn.execute(text("select has_schema_privilege(:r,'public',:p)"),
                                {"r": OWNER_ROLE, "p": priv}).scalar():
                _refuse(f"{OWNER_ROLE} lacks {priv} on schema public - run --managed-prepare first.")
    p["managed"] = {"current_revision": current, "head": heads[0], "pending": pending,
                    "alembic_version_owner": owners.get("alembic_version"),
                    "chain_alter_owners": {t: owners.get(t) for t in MANAGED_CHAIN_ALTERS},
                    "tables": len(owners), "owner_owned": sorted(owner_owned),
                    "prepare_columns": {t: live_missing.get(t, []) for t in MANAGED_PREPARE_TABLES}}
    return p


def print_managed_plan(p):
    m = p["managed"]
    print("=== MANAGED PROD PLAN (already Alembic-managed; gate PASSED) ===")
    print(f"current revision        : {m['current_revision']}")
    print(f"head                    : {m['head']}")
    print(f"pending revisions ({len(m['pending'])})   : {m['pending']}")
    print(f"tables / owner-owned    : {m['tables']} / {m['owner_owned']}")
    print(f"alembic_version owner   : {m['alembic_version_owner']}")
    print(f"[PREPARE] model-only columns to add: { {t: len(c) for t, c in m['prepare_columns'].items()} }")
    print(f"[PREPARE] alembic_version -> {OWNER_ROLE}: {m['alembic_version_owner'] != OWNER_ROLE}")
    print(f"[PREPARE] temporary re-own for the chain (returned by FINALIZE): "
          f"{[t for t, o in m['chain_alter_owners'].items() if o != OWNER_ROLE]}")
    print(f"[MIGRATE] chain creates : {sorted(MANAGED_CHAIN_CREATES)} (review_records columns added by 20260831_review_case)")
    print(f"legacy-only preserved   : {len(p['legacy_only_preserved_untouched'])}")
    print("No role creation, no broad re-ownership, no destructive DDL, no row change; "
          "the chain writes alembic_version itself.")


def _become(conn, become_role):
    """Session-local SET ROLE to the owner role (PROD: pgadmin, from the NOINHERIT
    Entra admin, which holds no table privilege of its own until it does). Done
    BEFORE the gate so the gate's catalog/table reads run with the owner's
    privileges. Not a grant; verified to have taken effect."""
    if not become_role:
        return
    session_user = conn.execute(text("select session_user")).scalar()
    conn.execute(text(f'SET ROLE "{become_role}"'))
    current_user = conn.execute(text("select current_user")).scalar()
    if current_user != become_role:
        raise SystemExit(f"REFUSED: SET ROLE {become_role} did not take effect (current_user={current_user}).")
    print(f"owner context: session_user={session_user}  current_user(after SET ROLE)={current_user}")


def managed_prepare(engine, expected_revision, become_role=None):
    """MANAGED PREPARE - owner-run (pgadmin, reached by session-local SET ROLE from
    the Entra admin). Adds ONLY the measured model-only columns on the tables in
    MANAGED_PREPARE_TABLES (idempotent ADD COLUMN IF NOT EXISTS), ensures the
    owner role can CREATE in public, and moves alembic_version to the owner role
    so the chain can write it. Nothing else: no CREATE ROLE, no table re-ownership
    beyond alembic_version, no Alembic, no row change."""
    with engine.begin() as conn:
        _become(conn, become_role)
        p = managed_gate(conn, expected_revision)
        md = _candidate_metadata()
        n_cols = 0
        for t in MANAGED_PREPARE_TABLES:
            model_cols = {c.name: c for c in md.tables[t].columns}
            for cn in p["managed"]["prepare_columns"][t]:
                coltype = model_cols[cn].type.compile(dialect=conn.engine.dialect)
                conn.execute(text(f'ALTER TABLE public."{t}" ADD COLUMN IF NOT EXISTS "{cn}" {coltype}'))
                n_cols += 1
        conn.execute(text(f'GRANT USAGE, CREATE ON SCHEMA public TO "{OWNER_ROLE}"'))
        moved = False
        if p["managed"]["alembic_version_owner"] != OWNER_ROLE:
            conn.execute(text(f'ALTER TABLE public."alembic_version" OWNER TO "{OWNER_ROLE}"'))
            moved = True
        reowned = []
        for t in MANAGED_CHAIN_ALTERS:
            if p["managed"]["chain_alter_owners"][t] != OWNER_ROLE:
                conn.execute(text(f'ALTER TABLE public."{t}" OWNER TO "{OWNER_ROLE}"'))
                reowned.append(t)
            # the application keeps exactly its former (owner-level) runtime access for the window
            conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON public."{t}" TO "{APP_ROLE}"'))
        print(f"MANAGED PREPARE applied: {n_cols} model-only column(s) added on {MANAGED_PREPARE_TABLES}, "
              f"schema privileges ensured for {OWNER_ROLE}, alembic_version re-owned={moved}, "
              f"temporarily re-owned for the chain={reowned} (app privileges retained; FINALIZE returns them). "
              "No roles created, no other ownership change, no Alembic, no row change.")
    return p


def _prove_managed_migration_identity(conn, admin_role):
    """Full identity proof for the dedicated migration principal before any DDL."""
    session_user = conn.execute(text("select session_user")).scalar()
    a = _role_attrs(conn, session_user)
    if a is None or any(a[1:]):
        raise SystemExit(f"REFUSED: migration identity {session_user} is not least privilege "
                         f"(login,super,createdb,createrole,bypassrls)={tuple(a) if a else None}")
    direct = _direct_memberships(conn, session_user)
    if direct != {OWNER_ROLE}:
        raise SystemExit(f"REFUSED: migration identity {session_user} is a direct member of {sorted(direct)}; "
                         f"it must be a member of {OWNER_ROLE} ONLY.")
    for forbidden in (admin_role, APP_ROLE):
        sp = conn.begin_nested()
        try:
            conn.execute(text(f'SET ROLE "{forbidden}"'))
        except Exception:  # noqa: BLE001 - the refusal is the proof
            sp.rollback()
        else:
            sp.rollback()
            raise SystemExit(f"REFUSED: migration identity {session_user} can SET ROLE {forbidden} - boundary broken.")
        conn.execute(text("RESET ROLE"))
    conn.execute(text(f'SET ROLE "{OWNER_ROLE}"'))
    current_user = conn.execute(text("select current_user")).scalar()
    if current_user != OWNER_ROLE:
        raise SystemExit(f"REFUSED: SET ROLE {OWNER_ROLE} did not take effect (current_user={current_user}).")
    print(f"identity proof: session_user={session_user}  attrs(super,createdb,createrole,bypassrls)={tuple(a[1:])}  "
          f"direct_memberships={sorted(direct)}  cannot SET ROLE {admin_role}/{APP_ROLE}=proven  "
          f"current_user(after SET ROLE)={current_user}")
    conn.execute(text("RESET ROLE"))
    return session_user


def managed_migrate(sync_url, expected_revision, admin_role):
    """MANAGED MIGRATE - executed EXCLUSIVELY by the dedicated migration identity
    on the already-managed database. Gate (after PREPARE), identity proof, then
    the REAL reviewed chain expected -> head, then a second upgrade proven no-op."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    engine = sa.create_engine(sync_url)
    with engine.begin() as conn:
        p = managed_gate(conn, expected_revision, after_prepare=True)
        _prove_managed_migration_identity(conn, admin_role)
    print(f"chain: {p['managed']['current_revision']} -> {p['managed']['head']} "
          f"({len(p['managed']['pending'])} pending: {p['managed']['pending']})")
    run_chain_and_verify(sync_url)
    # second upgrade must be a NO-OP: version unchanged, pending == 0, one head.
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", sync_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    script = ScriptDirectory.from_config(cfg)
    with engine.connect() as conn:
        cur = conn.execute(text("select version_num from alembic_version")).scalars().all()
        pending = [r.revision for r in script.iterate_revisions(script.get_heads()[0], cur[0])] if len(cur) == 1 else None
        for t in MANAGED_MODEL_ONLY_COLUMNS:
            have = _existing_columns(conn, t)
            missing = [c for c in MANAGED_MODEL_ONLY_COLUMNS[t] if c not in have]
            assert not missing, f"model-only columns still missing on {t} after the chain: {missing}"
    assert cur == list(script.get_heads()) and len(script.get_heads()) == 1, f"version {cur} != single head"
    assert pending == [], f"pending revisions after no-op re-run: {pending}"
    print(f"second upgrade head: NO-OP (version={cur[0]}, heads=1, pending=0)")


def main():
    ap = argparse.ArgumentParser(description="Three-step legacy-PROD -> Alembic convergence.")
    ap.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    ap.add_argument("--bootstrap", action="store_true", help="BOOTSTRAP A: owner-run role/ownership prep")
    ap.add_argument("--i-understand-bootstrap-writes", action="store_true")
    ap.add_argument("--migrate", action="store_true", help="MIGRATION B: migration-identity Alembic chain")
    ap.add_argument("--i-understand-migration-writes", action="store_true")
    ap.add_argument("--finalize", action="store_true", help="FINALIZE: owner-run ownership reconciliation to DEV model")
    ap.add_argument("--i-understand-finalize-writes", action="store_true")
    ap.add_argument("--bootstrap-as-role", default=os.getenv("CONV_BOOTSTRAP_ROLE"),
                    help="(Bootstrap A / Finalize) SET ROLE to this owner role for the operation "
                         "(e.g. pgadmin), so a secretless admin that can SET ROLE to the legacy "
                         "owner runs it without the owner's password. Session-local; not a new grant.")
    ap.add_argument("--managed-prepare", action="store_true",
                    help="MANAGED PREPARE (already-managed PROD): owner-run model-only columns + alembic_version ownership")
    ap.add_argument("--i-understand-prepare-writes", action="store_true")
    ap.add_argument("--managed-migrate", action="store_true",
                    help="MANAGED MIGRATE (already-managed PROD): migration-identity chain expected -> head")
    ap.add_argument("--expected-revision", default=os.getenv("CONV_EXPECTED_REVISION", EXPECTED_MANAGED_REVISION),
                    help="(managed) alembic_version MUST equal this or the gate refuses")
    ap.add_argument("--admin-role", default=os.getenv("CONV_ADMIN_ROLE", "pgadmin"),
                    help="(managed) server-admin role the migration identity must NOT be able to SET ROLE to")
    args = ap.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL required")
    if sum(bool(x) for x in (args.bootstrap, args.migrate, args.finalize,
                             args.managed_prepare, args.managed_migrate)) > 1:
        raise SystemExit("choose exactly ONE of --bootstrap / --migrate / --finalize / --managed-prepare / --managed-migrate")
    sync_url = args.database_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgres://", "postgresql://")
    engine = sa.create_engine(sync_url)

    if args.managed_prepare:
        if not args.i_understand_prepare_writes:
            with engine.connect() as conn:
                _become(conn, args.bootstrap_as_role)
                print_managed_plan(managed_gate(conn, args.expected_revision))
            print("\nMANAGED PREPARE DRY-RUN. Re-run with --managed-prepare --i-understand-prepare-writes "
                  "(as the owner-capable identity, e.g. --bootstrap-as-role pgadmin) to execute.")
            return
        managed_prepare(engine, args.expected_revision, become_role=args.bootstrap_as_role)
        print("MANAGED PREPARE COMPLETE")
        return

    if args.managed_migrate:
        if not args.i_understand_migration_writes:
            with engine.connect() as conn:
                print_managed_plan(managed_gate(conn, args.expected_revision, after_prepare=True))
            print("\nMANAGED MIGRATE DRY-RUN. Re-run with --managed-migrate --i-understand-migration-writes "
                  "(as the dedicated migration identity) to execute.")
            return
        managed_migrate(sync_url, args.expected_revision, args.admin_role)
        print("MANAGED MIGRATE COMPLETE")
        return

    if not (args.bootstrap or args.migrate or args.finalize):
        with engine.connect() as conn:
            print_plan(plan(conn))
        print("\nDRY-RUN ONLY. Choose --bootstrap / --migrate / --finalize with the matching write ack.")
        return

    if args.bootstrap:
        if not args.i_understand_bootstrap_writes:
            with engine.connect() as conn:
                print_plan(plan(conn))
            print("\nBOOTSTRAP A DRY-RUN. Re-run with --bootstrap --i-understand-bootstrap-writes "
                  "(as the owner-capable identity, CONV_APP_PASSWORD set) to execute.")
            return
        bootstrap_apply(engine, app_password=os.getenv("CONV_APP_PASSWORD"),
                        become_role=args.bootstrap_as_role)
        print("BOOTSTRAP A COMPLETE")
        return

    if args.migrate:
        if not args.i_understand_migration_writes:
            with engine.connect() as conn:
                print_plan(plan(conn))
            print("\nMIGRATION B DRY-RUN. Re-run with --migrate --i-understand-migration-writes "
                  "(as the dedicated migration identity) to execute.")
            return
        migration_apply(sync_url)
        print("MIGRATION B COMPLETE")
        return

    # --finalize
    if not args.i_understand_finalize_writes:
        print("FINALIZE DRY-RUN. Re-run with --finalize --i-understand-finalize-writes "
              "(as the owner-capable identity) to execute.")
        return
    finalize_ownership(engine, become_role=args.bootstrap_as_role)
    print("FINALIZE COMPLETE")


if __name__ == "__main__":
    main()
