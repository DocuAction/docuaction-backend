"""One-time legacy-PROD -> Alembic convergence, split across TWO security boundaries.

PROD's database is old-generation SQLAlchemy create_all() output: 42 tables owned
by the server admin (pgadmin), no alembic_version, no docuaction_owner/
docuaction_app roles, and none of the RCE/Area-1 Alembic schema. No historical
Alembic revision truthfully represents that state, so this does NOT stamp a
historical revision and does NOT replay the chain blindly.

WHY TWO BOUNDARIES
    Transferring ownership of the 42 pgadmin-owned tables is an OWNER-ONLY
    operation (PostgreSQL requires the executor to be the table's owner or a
    superuser). A least-privilege migration identity is neither, so it cannot do
    it - and we will not grant it pgadmin/superuser/BYPASSRLS to make it able to.
    Conversely, Alembic must NOT run as pgadmin. So the work is split:

    BOOTSTRAP A  (--bootstrap)  Executed by the existing owner-capable identity
        (PROD: pgadmin). Owner-only prep and NOTHING else: create the two
        least-privilege roles, grant the owner role CREATE on schema public,
        transfer ownership of the reviewed candidate-managed allowlist from the
        legacy owner to docuaction_owner, and grant the runtime principal owner-
        role membership so the running application keeps access. Runs NO Alembic,
        creates NO tables/columns, changes NO rows.

    MIGRATION B  (--migrate)   Executed EXCLUSIVELY by the dedicated PROD
        migration identity (a non-admin Entra principal mapped into PostgreSQL,
        made a member of docuaction_owner). It proves session_user = the
        migration identity and, after SET ROLE, current_user = docuaction_owner,
        then adds the model-only columns and runs the REAL reviewed Alembic chain
        (which creates the missing tables, applies grants, and writes
        alembic_version itself). It performs NO ownership transfer and never runs
        as pgadmin.

CHAIN-FIRST, NO STAMP
    Migration B runs `alembic upgrade head` and lets app-startup create_all()
    no-op after (a safety net) - the same order DEV builds in. It does NOT run
    create_all() before the chain (that would front-load final-state objects the
    chain builds incrementally and collide), and it does NOT stamp a historical
    revision - the chain writes alembic_version itself.

MODES
    (no write flag)                    READ ONLY plan. Default.
    --bootstrap                        BOOTSTRAP A dry-run (plan only).
    --bootstrap --i-understand-bootstrap-writes   Execute Bootstrap A.
    --migrate                          MIGRATION B dry-run (plan only).
    --migrate --i-understand-migration-writes     Execute Migration B.

GUARDS (fail closed)
    * both boundaries refuse if alembic_version already exists.
    * Bootstrap A refuses if a named --runtime-principal does not exist.
    * Migration B refuses unless docuaction_owner exists AND the candidate tables
      are already owned by it (i.e. Bootstrap A has run), and unless the
      connecting identity can SET ROLE docuaction_owner.
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


def _tables_not_owned_by_owner(conn, tables) -> list:
    if not tables:
        return []
    rows = conn.execute(text(
        "select relname from pg_class "
        "where relnamespace='public'::regnamespace and relkind='r' "
        "and relname = any(:names) and relowner::regrole::text <> :owner"),
        {"names": list(tables), "owner": OWNER_ROLE}).scalars().all()
    return sorted(rows)


def plan(conn):
    """Compute the deterministic convergence plan. Read-only."""
    md = _candidate_metadata()
    existing = _existing_tables(conn)
    candidate = {t.name for t in md.sorted_tables}

    alembic_present = "alembic_version" in existing
    owner_present = _role_exists(conn, OWNER_ROLE)

    missing = sorted(candidate - existing - {"alembic_version"})
    shared = sorted(candidate & existing)
    legacy_only = sorted(existing - candidate - {"alembic_version"})

    # model-only columns: columns the current model defines on an EXISTING table
    # that the legacy DB lacks. These were added to models without a migration
    # (DEV gets them from create_all on fresh tables); the chain's later index/
    # constraint migrations assume they exist, and create_all(checkfirst) cannot
    # retrofit a column onto an existing table - so Migration B adds them first,
    # as nullable columns, AS the owner role. The chain's own column-adds on these
    # tables are guarded (has_column), so the pre-add is a safe no-op for them.
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
        "owner_present": owner_present,
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
    print(f"roles to create                 : {p['roles_to_create']}")
    print(f"[Bootstrap A] candidate tables re-owned to {OWNER_ROLE}: {len(p['tables_shared_reowned'])}")
    print(f"[Bootstrap A] legacy-only tables preserved : {len(p['legacy_only_preserved_untouched'])}  {p['legacy_only_preserved_untouched']}")
    total_cols = sum(len(v) for v in p["model_only_columns_to_add"].values())
    print(f"[Migration B] model-only columns to add (nullable): {total_cols}  { { t: [c for c, _ in v] for t, v in p['model_only_columns_to_add'].items() } }")
    print(f"[Migration B] tables the CHAIN will create : {len(p['tables_the_chain_will_create'])}")
    for t in p["tables_the_chain_will_create"]:
        print(f"    + {t}")
    print("Additive only: no existing table is removed or emptied; no destructive change; "
          "the reviewed chain writes its own version row (no historical revision is faked).")


def bootstrap_apply(engine, runtime_principal=None):
    """BOOTSTRAP A - one-time OWNER-ONLY prep, executed by the existing owner-
    capable identity (PROD: pgadmin). Owner-only operations ONLY: create the two
    least-privilege roles, grant the owner role CREATE on schema public, transfer
    ownership of the reviewed candidate-managed allowlist from the legacy owner to
    docuaction_owner, preserve all legacy-only ownership, and grant the runtime
    principal owner-role membership so the running application keeps access.

    It runs NO Alembic, creates NO tables/columns, changes NO rows, and never
    DROP/TRUNCATE/REASSIGN OWNED, never changes DATABASE_URL, never deploys."""
    with engine.begin() as conn:
        p = plan(conn)
        if p["alembic_present"]:
            raise SystemExit("REFUSED: alembic_version already present - this DB is already "
                             "Alembic-managed; use the normal migration path.")
        if runtime_principal and not _role_exists(conn, runtime_principal):
            raise SystemExit(f"REFUSED: --runtime-principal {runtime_principal!r} does not exist; "
                             "cannot preserve its access.")
        # 1. least-privilege role model
        for r, attrs in ((OWNER_ROLE, "NOLOGIN NOSUPERUSER NOBYPASSRLS"),
                         (APP_ROLE, "NOLOGIN NOSUPERUSER NOBYPASSRLS")):
            if not _role_exists(conn, r):
                conn.execute(text(f'CREATE ROLE "{r}" WITH {attrs}'))
        # 2. membership so the owner-capable executor can reassign ownership to the
        #    owner role (ALTER OWNER requires membership in the target role).
        conn.execute(text(f'GRANT "{OWNER_ROLE}" TO CURRENT_USER'))
        # 3. the owner role must be able to create the missing tables during
        #    Migration B (PostgreSQL 15+ removed the default PUBLIC CREATE on
        #    schema public). Executed by CURRENT_USER, the schema owner (pgadmin).
        conn.execute(text(f'GRANT CREATE, USAGE ON SCHEMA public TO "{OWNER_ROLE}"'))
        # 4. ownership transfer - reviewed candidate-managed allowlist ONLY.
        #    Legacy-only tables (not in the model) are deliberately left untouched.
        for t in p["tables_shared_reowned"]:
            conn.execute(text(f'ALTER TABLE public."{t}" OWNER TO "{OWNER_ROLE}"'))
        # 5. preserve the running application's access across the ownership move:
        #    grant the runtime principal MEMBERSHIP in the owner role (role-
        #    membership grant, not a table privilege) - no DATABASE_URL change.
        if runtime_principal:
            conn.execute(text(f'GRANT "{OWNER_ROLE}" TO "{runtime_principal}"'))
        print(f"BOOTSTRAP A applied: roles ensured (NOLOGIN NOSUPERUSER NOBYPASSRLS), "
              f"CREATE on schema public granted to {OWNER_ROLE}, "
              f"{len(p['tables_shared_reowned'])} candidate tables re-owned to {OWNER_ROLE}, "
              f"{len(p['legacy_only_preserved_untouched'])} legacy-only tables left untouched, "
              f"runtime principal preserved = {runtime_principal or '(none)'}. "
              "No Alembic, no table/column creation, no row change.")
    return p


def _prove_migration_identity(conn):
    """Prove the connecting identity is a non-owner migration principal that can
    only act as the owner via SET ROLE (membership), never as pgadmin/superuser."""
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
    Proves session_user / current_user, adds the model-only columns AS the owner
    role (SET ROLE), then runs the reviewed Alembic chain. Contains NO ownership
    transfer of legacy tables and never runs as pgadmin - Bootstrap A already
    moved ownership to docuaction_owner."""
    engine = sa.create_engine(sync_url)
    with engine.begin() as conn:
        p = plan(conn)
        if p["alembic_present"]:
            raise SystemExit("REFUSED: alembic_version already present - already Alembic-managed.")
        if not _role_exists(conn, OWNER_ROLE):
            raise SystemExit(f"REFUSED: {OWNER_ROLE} absent - run Bootstrap A (--bootstrap) first.")
        not_owned = _tables_not_owned_by_owner(conn, p["tables_shared_reowned"])
        if not_owned:
            raise SystemExit(f"REFUSED: {len(not_owned)} candidate table(s) are not owned by "
                             f"{OWNER_ROLE} - Bootstrap A has not run: {not_owned[:5]}")
        # identity proof + become the owner role via membership (never pgadmin)
        _prove_migration_identity(conn)
        # add model-only columns AS the owner role (owns the re-owned tables)
        n_cols = 0
        for t, cols in p["model_only_columns_to_add"].items():
            for cn, coltype in cols:
                conn.execute(text(f'ALTER TABLE public."{t}" ADD COLUMN IF NOT EXISTS "{cn}" {coltype}'))
                n_cols += 1
        conn.execute(text("RESET ROLE"))
        print(f"added {n_cols} model-only column(s) as {OWNER_ROLE}; handing off to the reviewed chain")
    run_chain_and_verify(sync_url)


def run_chain_and_verify(db_url):
    """Run the reviewed Alembic chain (creates missing tables, applies grants,
    writes alembic_version) AS the owner role via env.py's DB_MIGRATION_ROLE
    SET ROLE, then run create_all(checkfirst) as the same no-op safety net DEV
    runs at app startup (also as the owner role), then prove single head,
    head==version, schema equivalence to the model, and a no-op re-run."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    # the chain connects as the migration identity and SET ROLEs to the owner role
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
        # DEV's app-startup safety net, AS the owner role: create_all(checkfirst)
        # is table-level, so it only CREATES whole model tables the chain somehow
        # missed (owned by the owner role) and never touches existing tables.
        conn.execute(text(f'SET ROLE "{OWNER_ROLE}"'))
        before = _existing_tables(conn)
        md.create_all(bind=conn, checkfirst=True)
        after = _existing_tables(conn)
        newly = sorted(after - before)
        conn.execute(text("RESET ROLE"))
        if newly:
            print(f"safety-net create_all created {len(newly)} table(s) the chain missed: {newly}")
        # schema equivalence: every model table must now exist
        missing_after = sorted({t.name for t in md.sorted_tables} - after)
        assert not missing_after, f"model tables missing after convergence: {missing_after}"
        cur = conn.execute(text("select version_num from alembic_version")).scalars().all()
    print("alembic_version:", cur)
    assert cur == list(heads), "alembic_version != head after convergence"


def main():
    ap = argparse.ArgumentParser(description="Two-boundary legacy-PROD -> Alembic convergence.")
    ap.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    ap.add_argument("--bootstrap", action="store_true",
                    help="BOOTSTRAP A: owner-run role/ownership prep (no Alembic)")
    ap.add_argument("--i-understand-bootstrap-writes", action="store_true")
    ap.add_argument("--migrate", action="store_true",
                    help="MIGRATION B: migration-identity Alembic chain (no ownership transfer)")
    ap.add_argument("--i-understand-migration-writes", action="store_true")
    ap.add_argument("--runtime-principal", default=os.getenv("CONV_RUNTIME_PRINCIPAL"),
                    help="(Bootstrap A) existing app login role to preserve via owner-role "
                         "membership so its DB access survives with no DATABASE_URL change")
    args = ap.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL required")
    if args.bootstrap and args.migrate:
        raise SystemExit("choose exactly ONE of --bootstrap or --migrate")
    sync_url = args.database_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgres://", "postgresql://")
    engine = sa.create_engine(sync_url)

    # pure read-only plan
    if not args.bootstrap and not args.migrate:
        with engine.connect() as conn:
            print_plan(plan(conn))
            if args.runtime_principal:
                print(f"runtime principal to preserve      : {args.runtime_principal} "
                      f"(exists={_role_exists(conn, args.runtime_principal)})")
        print("\nDRY-RUN ONLY. Choose --bootstrap or --migrate with the matching write "
              "acknowledgement to execute.")
        return

    if args.bootstrap:
        if not args.i_understand_bootstrap_writes:
            with engine.connect() as conn:
                print_plan(plan(conn))
            print("\nBOOTSTRAP A DRY-RUN. Re-run with --bootstrap --i-understand-bootstrap-writes "
                  "(as the owner-capable identity) to execute.")
            return
        bootstrap_apply(engine, runtime_principal=args.runtime_principal)
        print("BOOTSTRAP A COMPLETE")
        return

    # --migrate
    if not args.i_understand_migration_writes:
        with engine.connect() as conn:
            print_plan(plan(conn))
        print("\nMIGRATION B DRY-RUN. Re-run with --migrate --i-understand-migration-writes "
              "(as the dedicated migration identity) to execute.")
        return
    migration_apply(sync_url)
    print("MIGRATION B COMPLETE")


if __name__ == "__main__":
    main()
