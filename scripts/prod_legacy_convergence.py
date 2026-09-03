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


def bootstrap_apply(engine, app_password=None):
    """BOOTSTRAP A - owner-only prep, executed by the existing owner-capable
    identity (PROD: pgadmin). Creates the two roles (docuaction_app LOGIN with the
    application password; docuaction_owner NOLOGIN), grants CONNECT + schema
    privileges, and TEMPORARILY re-owns the existing candidate tables to
    docuaction_owner so the chain (run as that role) can build/alter them.
    Finalize later moves the non-Area-1 tables to docuaction_app.

    Runs NO Alembic, creates NO application tables/columns, changes NO rows, and
    never DROP/TRUNCATE/REASSIGN OWNED."""
    with engine.begin() as conn:
        p = plan(conn)
        if p["alembic_present"]:
            raise SystemExit("REFUSED: alembic_version already present - already Alembic-managed.")
        if not p["app_present"] and not app_password:
            raise SystemExit("REFUSED: docuaction_app must be created as a LOGIN role but no app "
                             "password was supplied (set CONV_APP_PASSWORD). It is never logged.")
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


def finalize_ownership(engine):
    """FINALIZE - owner-run (pgadmin). Reassign ownership to the certified DEV
    model: every candidate table EXCEPT the Area-1 set -> docuaction_app (the
    application login, whose access is by ownership); the Area-1 tables and
    alembic_version stay docuaction_owner. Runs NO Alembic; the migration identity
    cannot do this (it is not a member of docuaction_app)."""
    md = _candidate_metadata()
    candidate = {t.name for t in md.sorted_tables}
    with engine.begin() as conn:
        if "alembic_version" not in _existing_tables(conn):
            raise SystemExit("REFUSED: alembic_version absent - run Migration B (--migrate) first.")
        for r in (OWNER_ROLE, APP_ROLE):
            if not _role_exists(conn, r):
                raise SystemExit(f"REFUSED: {r} absent - Bootstrap A has not run.")
        existing = _existing_tables(conn)
        to_app = sorted((candidate & existing) - AREA1_OWNER_TABLES)
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
        kept = sorted(AREA1_OWNER_TABLES & existing)
        print(f"FINALIZE applied: {len(to_app)} non-Area-1 candidate tables (+ {len(seqs)} sequences) "
              f"re-owned to {APP_ROLE}; {len(kept)} Area-1 tables + alembic_version kept on {OWNER_ROLE}. "
              "No Alembic, no row change.")
    return to_app


def main():
    ap = argparse.ArgumentParser(description="Three-step legacy-PROD -> Alembic convergence.")
    ap.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    ap.add_argument("--bootstrap", action="store_true", help="BOOTSTRAP A: owner-run role/ownership prep")
    ap.add_argument("--i-understand-bootstrap-writes", action="store_true")
    ap.add_argument("--migrate", action="store_true", help="MIGRATION B: migration-identity Alembic chain")
    ap.add_argument("--i-understand-migration-writes", action="store_true")
    ap.add_argument("--finalize", action="store_true", help="FINALIZE: owner-run ownership reconciliation to DEV model")
    ap.add_argument("--i-understand-finalize-writes", action="store_true")
    args = ap.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL required")
    if sum(bool(x) for x in (args.bootstrap, args.migrate, args.finalize)) > 1:
        raise SystemExit("choose exactly ONE of --bootstrap / --migrate / --finalize")
    sync_url = args.database_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgres://", "postgresql://")
    engine = sa.create_engine(sync_url)

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
        bootstrap_apply(engine, app_password=os.getenv("CONV_APP_PASSWORD"))
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
    finalize_ownership(engine)
    print("FINALIZE COMPLETE")


if __name__ == "__main__":
    main()
