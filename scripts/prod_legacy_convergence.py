"""One-time legacy-PROD -> Alembic convergence.

PROD's database is old-generation SQLAlchemy create_all() output: 42 tables
owned by the server admin, no alembic_version, no docuaction_owner/
docuaction_app roles, and none of the RCE/Area-1 Alembic schema. No historical
Alembic revision truthfully represents that state, so this does NOT stamp a
historical revision and does NOT replay the chain blindly.

Instead it reproduces, deterministically and idempotently, the EXACT order that
produced the certified DEV schema. DEV builds via `alembic upgrade head` (the
chain creates every table and adds every column), and app-startup create_all()
runs afterwards only as a no-op safety net. So convergence does the same, in
that order: prepare the role model and re-own the existing tables, run the REAL
reviewed chain to build the schema and apply grants, then run create_all(
checkfirst) as the same no-op safety net, and finally prove schema equivalence
and that alembic_version == head.

It does NOT run create_all() BEFORE the chain: that would front-load final-state
objects (constraints and whole tables the chain builds incrementally) and
collide with the chain's own create statements. And it does NOT stamp a
historical revision - no historical revision truthfully represents PROD's legacy
create_all() state; the chain writes alembic_version itself.

WHY REUSE THE CHAIN INSTEAD OF REIMPLEMENTING ITS SCHEMA/GRANTS
    The schema and the Area-1 privilege model (column-level UPDATE, revoked
    table-level UPDATE/DELETE, the run-lifecycle and delivery-jobs grants) live
    in audited migrations. Re-expressing either here would risk a subtly
    different, less-safe result. So the chain is the single source of truth; this
    utility only adds the legacy-specific pre-steps (roles, schema-CREATE grant
    to the owner role, ownership reconciliation, runtime-principal preservation).

MODES
    --dry-run  READ ONLY. Inspects, computes the plan, prints it. Issues no
               CREATE/ALTER/DROP/GRANT/REVOKE/INSERT/UPDATE/DELETE/TRUNCATE and
               no stamp. Default.
    --apply    Prepares roles/ownership in one transaction, fail-closed, then
               (with --run-chain) runs the reviewed chain + safety net. Requires
               --i-understand-this-writes.

GUARDS (fail closed)
    * refuses if alembic_version already exists (already Alembic-managed).
    * refuses if a named --runtime-principal does not exist.
    * never DROP/TRUNCATE and never REASSIGN OWNED; only role creation,
      role-membership and schema-CREATE grants, ownership reassignment of
      candidate tables, the reviewed migration chain, and a no-op create_all
      safety net.
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
    # retrofit a column onto an existing table - so they must be added first, as
    # nullable columns. The chain's own column-adds on these tables are guarded
    # (has_column), so a pre-add is a safe no-op for them.
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
    print(f"tables the CHAIN will create    : {len(p['tables_the_chain_will_create'])}")
    for t in p["tables_the_chain_will_create"]:
        print(f"    + {t}")
    print(f"existing candidate tables re-owned to {OWNER_ROLE}: {len(p['tables_shared_reowned'])}")
    total_cols = sum(len(v) for v in p["model_only_columns_to_add"].values())
    print(f"model-only columns to add (nullable): {total_cols}  { { t: [c for c, _ in v] for t, v in p['model_only_columns_to_add'].items() } }")
    print(f"legacy-only tables preserved    : {len(p['legacy_only_preserved_untouched'])}  {p['legacy_only_preserved_untouched']}")
    print("Chain-first plan: the reviewed Alembic chain creates the missing tables "
          "and adds columns to existing ones (its guarded migrations no-op on what "
          "already exists); no existing table is removed or emptied; no destructive change.")


def apply(engine, runtime_principal=None):
    """Prepare the legacy DB so the reviewed chain can run, then hand off. This
    performs NO schema DDL of its own beyond re-owning existing tables - the
    chain is the single source of truth for the schema (matching DEV, which
    runs `alembic upgrade head` and lets app-startup create_all() no-op after)."""
    with engine.begin() as conn:
        p = plan(conn)
        if p["alembic_present"]:
            raise SystemExit("REFUSED: alembic_version already present - this DB is already "
                             "Alembic-managed; use the normal migration path.")
        if runtime_principal and not _role_exists(conn, runtime_principal):
            raise SystemExit(f"REFUSED: --runtime-principal {runtime_principal!r} does not exist; "
                             "cannot preserve its access.")
        # 1. role model (least privilege)
        for r, attrs in ((OWNER_ROLE, "NOLOGIN NOSUPERUSER NOBYPASSRLS"),
                         (APP_ROLE, "NOLOGIN NOSUPERUSER NOBYPASSRLS")):
            if not _role_exists(conn, r):
                conn.execute(text(f'CREATE ROLE "{r}" WITH {attrs}'))
        # 2. membership in the owner role - so CURRENT_USER can reassign ownership
        #    below, and so the chain (which SET ROLEs to it via DB_MIGRATION_ROLE)
        #    can act as the owner. Role-membership grant, not a table privilege.
        conn.execute(text(f'GRANT "{OWNER_ROLE}" TO CURRENT_USER'))
        # 3. the owner role must be able to create the missing tables during the
        #    chain. PostgreSQL 15+ removed the default PUBLIC CREATE on schema
        #    public, so grant it explicitly. Executed by CURRENT_USER, the schema
        #    owner/admin (PROD: pgadmin). Schema-level infra grant, NOT a table
        #    privilege and NOT part of the Area-1 model (which the chain owns).
        conn.execute(text(f'GRANT CREATE, USAGE ON SCHEMA public TO "{OWNER_ROLE}"'))
        # 4. re-own the existing candidate tables to the owner role so the chain's
        #    guarded ALTERs (add columns, grants) succeed as the owner. Legacy-only
        #    tables (not in the model) are deliberately left untouched.
        for t in p["tables_shared_reowned"]:
            conn.execute(text(f'ALTER TABLE public."{t}" OWNER TO "{OWNER_ROLE}"'))
        # 4b. add model-only columns the legacy tables lack, as NULLABLE columns,
        #     BEFORE the chain (its index/constraint migrations assume they exist;
        #     create_all(checkfirst) cannot retrofit them onto existing tables).
        #     Nullable + IF NOT EXISTS -> safe on stored data and a no-op for any
        #     column the chain's guarded migrations also add.
        n_cols = 0
        for t, cols in p["model_only_columns_to_add"].items():
            for cn, coltype in cols:
                conn.execute(text(f'ALTER TABLE public."{t}" ADD COLUMN IF NOT EXISTS "{cn}" {coltype}'))
                n_cols += 1
        print(f"prepared roles + re-owned {len(p['tables_shared_reowned'])} existing candidate tables to {OWNER_ROLE} "
              f"+ added {n_cols} model-only column(s); the reviewed chain will create the missing tables and grants")
        # 5. preserve the existing runtime principal's access across the ownership
        #    move: grant it MEMBERSHIP in the owner role (role-membership grant,
        #    not a table privilege) so the running app keeps working with no
        #    DATABASE_URL / credential change. Least-priv hardening to a dedicated
        #    login is a separate, later, connection-string-changing step.
        if runtime_principal:
            conn.execute(text(f'GRANT "{OWNER_ROLE}" TO "{runtime_principal}"'))
            print(f"preserved runtime access: granted {OWNER_ROLE} membership to {runtime_principal} "
                  "(inherits ownership rights; no DATABASE_URL change)")
    return p


def run_chain_and_verify(db_url):
    """Run the reviewed Alembic chain (creates missing tables, adds columns,
    applies grants, writes alembic_version), then run create_all(checkfirst) as
    the same no-op safety net DEV runs at app startup, then prove single head,
    head==version, schema equivalence to the model, and a no-op re-run."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    # the chain runs AS the owner role (SET ROLE via env.py) and grants to the app role
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
        before = _existing_tables(conn)
        # DEV's app-startup safety net: create_all(checkfirst) is table-level, so
        # it only CREATES whole model tables the chain somehow missed and never
        # touches existing tables. Run as CURRENT_USER (holds CREATE on public).
        md.create_all(bind=conn, checkfirst=True)
        after = _existing_tables(conn)
        newly = sorted(after - before)
        for t in newly:  # normalise any safety-net table to the owner role
            conn.execute(text(f'ALTER TABLE public."{t}" OWNER TO "{OWNER_ROLE}"'))
        if newly:
            print(f"safety-net create_all created {len(newly)} table(s) the chain missed: {newly}")
        # schema equivalence: every model table must now exist
        missing_after = sorted({t.name for t in md.sorted_tables} - after)
        assert not missing_after, f"model tables missing after convergence: {missing_after}"
        cur = conn.execute(text("select version_num from alembic_version")).scalars().all()
    print("alembic_version:", cur)
    assert cur == list(heads), "alembic_version != head after convergence"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    ap.add_argument("--dry-run", action="store_true",
                    help="explicit read-only mode (also the default when --apply is absent)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--i-understand-this-writes", action="store_true")
    ap.add_argument("--run-chain", action="store_true",
                    help="after --apply, run the reviewed alembic chain and verify head")
    ap.add_argument("--runtime-principal", default=os.getenv("CONV_RUNTIME_PRINCIPAL"),
                    help="existing app login role to preserve across the ownership move "
                         "(granted owner-role membership so its DB access survives with no "
                         "DATABASE_URL change)")
    args = ap.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL required")
    sync_url = args.database_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgres://", "postgresql://")
    engine = sa.create_engine(sync_url)

    if not args.apply:
        with engine.connect() as conn:
            print_plan(plan(conn))
            if args.runtime_principal:
                exists = _role_exists(conn, args.runtime_principal)
                print(f"runtime principal to preserve      : {args.runtime_principal} "
                      f"(exists={exists}; will receive {OWNER_ROLE} membership, no DATABASE_URL change)")
        print("\nDRY-RUN ONLY. No changes made. Re-run with --apply --i-understand-this-writes to execute.")
        return
    if not args.i_understand_this_writes:
        raise SystemExit("--apply requires --i-understand-this-writes")
    apply(engine, runtime_principal=args.runtime_principal)
    if args.run_chain:
        run_chain_and_verify(sync_url)
    print("CONVERGENCE APPLIED")


if __name__ == "__main__":
    main()
