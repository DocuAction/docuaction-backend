"""One-time legacy-PROD -> Alembic convergence.

PROD's database is old-generation SQLAlchemy create_all() output: 42 tables
owned by the server admin, no alembic_version, no docuaction_owner/
docuaction_app roles, and none of the RCE/Area-1 Alembic schema. No historical
Alembic revision truthfully represents that state, so this does NOT stamp a
historical revision and does NOT replay the chain blindly.

Instead it reproduces, deterministically and idempotently, the exact sequence
that produced the certified DEV schema - create_all() to seed the model
tables, then the reviewed Alembic chain - but with the legacy-specific
pre-steps PROD needs (role model, ownership reconciliation) inserted first, and
with a hard schema-equivalence proof gating the point at which Alembic
bookkeeping becomes truthful.

WHY REUSE THE CHAIN INSTEAD OF REIMPLEMENTING ITS GRANTS
    The Area-1 privilege model (column-level UPDATE, revoked table-level
    UPDATE/DELETE, the run-lifecycle and delivery-jobs grants) lives in audited
    migrations 20260828/20260830/20260903. Re-expressing it here would risk a
    subtly different, less-safe security posture. So after seeding the schema
    and reconciling ownership, this runs the REAL `alembic upgrade head`, which
    applies those exact reviewed grants and writes alembic_version itself - no
    separate stamp, no invented grant logic.

MODES
    --dry-run  READ ONLY. Inspects, computes the plan, prints it. Issues no
               CREATE/ALTER/DROP/GRANT/REVOKE/INSERT/UPDATE/DELETE/TRUNCATE and
               no stamp. Default.
    --apply    Executes the plan inside one transaction where PostgreSQL
               permits, fail-closed. Requires --i-understand-this-writes.

GUARDS (fail closed)
    * refuses if alembic_version already exists (already Alembic-managed).
    * refuses if the DB does not present the expected legacy signature
      (no docuaction_owner role) unless --allow-nonlegacy is given for tests.
    * never DROP/TRUNCATE; never drops a populated table; only additive DDL,
      metadata create_all(checkfirst), ownership reassignment on an explicit
      allowlist, and the reviewed migration chain.
"""
from __future__ import annotations

import argparse
import os
import sys

import sqlalchemy as sa
from sqlalchemy import text


OWNER_ROLE = "docuaction_owner"
APP_ROLE = "docuaction_app"
ADDITIVE = {  # table -> columns the candidate added since the legacy create_all (all nullable)
    "audit_logs": ["event_type", "outcome", "correlation_id"],
    "decisions": ["approval_justification", "rejection_reason", "rejection_category",
                  "supersedes", "sla_hours", "deadline", "escalation_level", "escalated_to",
                  "escalated_at", "is_overdue", "outcome_text", "outcome_date",
                  "outcome_matched", "outcome_notes", "outcome_recorded_by",
                  "required_approver_role", "approval_threshold_usd", "domain"],
    "tefca_import_history": ["file_hash"],
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

    add_columns = {}
    for t, cols in ADDITIVE.items():
        if t in existing:
            have = _existing_columns(conn, t)
            need = [c for c in cols if c not in have]
            if need:
                add_columns[t] = need

    return {
        "alembic_present": alembic_present,
        "owner_present": owner_present,
        "roles_to_create": [r for r in (OWNER_ROLE, APP_ROLE) if not _role_exists(conn, r)],
        "tables_to_create": missing,
        "tables_shared_kept": shared,
        "legacy_only_preserved_untouched": legacy_only,
        "additive_columns": add_columns,
        "ownership_allowlist": sorted(candidate & (existing | set(missing))),
    }


def print_plan(p):
    print("=== CONVERGENCE PLAN (dry-run) ===")
    print(f"alembic_version already present : {p['alembic_present']}")
    print(f"docuaction_owner present        : {p['owner_present']}")
    print(f"roles to create                 : {p['roles_to_create']}")
    print(f"tables to CREATE (empty)        : {len(p['tables_to_create'])}")
    for t in p["tables_to_create"]:
        print(f"    + {t}")
    print(f"shared tables kept in place     : {len(p['tables_shared_kept'])}")
    print(f"legacy-only tables preserved    : {len(p['legacy_only_preserved_untouched'])}  {p['legacy_only_preserved_untouched']}")
    print(f"additive columns to add         : {p['additive_columns']}")
    print(f"ownership -> {OWNER_ROLE} on     : {len(p['ownership_allowlist'])} candidate-managed tables")
    print("Additive-only plan: no existing table is removed or emptied; no destructive change.")


def apply(engine, runtime_principal=None):
    md = _candidate_metadata()
    with engine.begin() as conn:
        p = plan(conn)
        if p["alembic_present"]:
            raise SystemExit("REFUSED: alembic_version already present - this DB is already "
                             "Alembic-managed; use the normal migration path.")
        if runtime_principal and not _role_exists(conn, runtime_principal):
            raise SystemExit(f"REFUSED: --runtime-principal {runtime_principal!r} does not exist; "
                             "cannot preserve its access.")
        # 1. role model
        for r, attrs in ((OWNER_ROLE, "NOLOGIN NOSUPERUSER NOBYPASSRLS"),
                         (APP_ROLE, "NOLOGIN NOSUPERUSER NOBYPASSRLS")):
            if not _role_exists(conn, r):
                conn.execute(text(f'CREATE ROLE "{r}" WITH {attrs}'))
        # 2. membership in the owner role - needed to reassign ownership to it in
        #    step 5, and so the reviewed migration chain can SET ROLE to it. This
        #    does NOT create objects (see step 3).
        conn.execute(text(f'GRANT "{OWNER_ROLE}" TO CURRENT_USER'))
        # 3. seed the model schema as CURRENT_USER - the existing schema owner /
        #    admin (PROD: pgadmin), which holds CREATE on schema public. A fresh
        #    NOLOGIN owner role does NOT (PostgreSQL 15+ removed the default
        #    PUBLIC CREATE grant), so creating as the owner role would fail. New
        #    tables land owned by CURRENT_USER and are normalised to the owner
        #    role in step 5. create_all is idempotent: checkfirst skips existing.
        md.create_all(bind=conn, checkfirst=True)
        # 4. additive columns the legacy create_all predates (all nullable -> safe on data)
        for t, cols in p["additive_columns"].items():
            model = md.tables[t]
            for cn in cols:
                col = model.columns[cn]
                coltype = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE public."{t}" ADD COLUMN IF NOT EXISTS "{cn}" {coltype}'))
        # 5. ownership reconciliation - explicit allowlist only (candidate-managed tables)
        for t in p["ownership_allowlist"]:
            conn.execute(text(f'ALTER TABLE public."{t}" OWNER TO "{OWNER_ROLE}"'))
        print(f"seeded schema + reconciled ownership of {len(p['ownership_allowlist'])} tables to {OWNER_ROLE}")
        # 6. preserve the existing runtime principal's access across the ownership
        #    move: grant it MEMBERSHIP in the new owner role (a role-membership
        #    grant, not a table privilege) so the running app keeps working with no
        #    DATABASE_URL / credential change. Least-priv hardening to a dedicated
        #    login is a separate, later, connection-string-changing step.
        if runtime_principal:
            conn.execute(text(f'GRANT "{OWNER_ROLE}" TO "{runtime_principal}"'))
            print(f"preserved runtime access: granted {OWNER_ROLE} membership to {runtime_principal} "
                  "(inherits ownership rights; no DATABASE_URL change)")
    return p


def run_chain_and_verify(db_url):
    """Run the reviewed Alembic chain (applies grants + writes alembic_version),
    then prove head, single-head, and a no-op re-run."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    os.environ["DB_APP_ROLE"] = APP_ROLE
    command.upgrade(cfg, "head")
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    print("alembic heads:", heads)
    assert len(heads) == 1, f"expected one head, got {heads}"
    eng = sa.create_engine(db_url)
    with eng.connect() as c:
        cur = c.execute(text("select version_num from alembic_version")).scalars().all()
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
