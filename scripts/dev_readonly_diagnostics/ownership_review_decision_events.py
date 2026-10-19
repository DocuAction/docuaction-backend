"""Read-only ownership diagnostic for DEV (review_decision_events).

Run by .github/workflows/dev-ownership-readonly-diagnostic-sod.yml. It reads
catalog metadata and the single alembic_version row, inside one
`BEGIN TRANSACTION READ ONLY` that always ends in ROLLBACK. It executes no
DDL, no GRANT/REVOKE, no ownership change and reads no business row.

Why: PR #151 (20261012_sod_db_enforcement) adds append-only guard triggers
to review_decision_events, but the migration's own preflight requires the
table to already be owner-owned (docs/SOD_DB_OWNERSHIP_PREREQUISITE.sql is a
separate, manual DBA step) and scripts/prod_legacy_convergence.py's FINALIZE
now refuses to hand the table back to docuaction_app whenever either the
grant shape is unexplained or the guard triggers already exist with no
grants. Before requesting approval for that DBA step (or for applying the
migration), the table's actual current owner, ACL, runtime privileges, guard
trigger presence and the current Alembic revision must be read -- not
assumed.

Connection: the same dedicated identity and token pattern as
migration-preflight.yml (PGHOST / PGDATABASE / PG_PRINCIPAL / PGTOKEN). The
token is read from the environment and never printed.
"""
import os
import sys

import psycopg2

TABLE = "review_decision_events"
OWNER_ROLE = "docuaction_owner"
RUNTIME_ROLE = "docuaction_app"
MIGRATION_IDENTITY = os.environ["PG_PRINCIPAL"]
STATEMENT_TIMEOUT_MS = 20000
GUARD_TRIGGERS = ("trg_review_event_sod", "trg_review_event_append_only_rows",
                  "trg_review_event_append_only_trunc")

ACL_SQL = """
    select k.relname, '' as column_name, coalesce(gee.rolname, 'PUBLIC'), a.privilege_type,
           a.is_grantable, gor.rolname
    from pg_catalog.pg_class k
    join pg_catalog.pg_namespace n on n.oid = k.relnamespace
    cross join lateral aclexplode(coalesce(k.relacl, acldefault('r', k.relowner))) a
    join pg_catalog.pg_roles gor on gor.oid = a.grantor
    left join pg_catalog.pg_roles gee on gee.oid = a.grantee
    where n.nspname = 'public' and k.relname = %(table)s
    order by 1, 2, 3, 4"""


def section(cur, title, sql, params=None, header=None):
    print(f"\n=== {title} ===")
    cur.execute(sql, params)
    rows = cur.fetchall()
    cols = header or [d[0] for d in cur.description]
    print(" | ".join(cols))
    for row in rows:
        print(" | ".join("" if v is None else str(v) for v in row))
    print(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return rows


def main() -> int:
    conn = psycopg2.connect(
        host=os.environ["PGHOST"], dbname=os.environ["PGDATABASE"],
        user=MIGRATION_IDENTITY, password=os.environ["PGTOKEN"],
        sslmode="require", connect_timeout=20)
    cur = conn.cursor()
    p = {"table": TABLE, "owner": OWNER_ROLE, "runtime": RUNTIME_ROLE, "mig": MIGRATION_IDENTITY,
        "guards": list(GUARD_TRIGGERS)}
    try:
        cur.execute("BEGIN TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))

        section(cur, "1. Session and server",
                "select current_user, session_user, current_database(), "
                "current_setting('server_version') as server_version, "
                "current_setting('transaction_read_only') as transaction_read_only")

        print("\n=== 2. Current Alembic revision ===")
        cur.execute("SAVEPOINT rev")
        try:
            cur.execute(f"SET LOCAL ROLE {OWNER_ROLE}")
            cur.execute("select version_num from public.alembic_version")
            print("alembic_version rows:", [r[0] for r in cur.fetchall()])
        except psycopg2.Error as exc:
            cur.execute("ROLLBACK TO SAVEPOINT rev")
            print(f"NOT READABLE: {type(exc).__name__}: {str(exc).strip()}")
        cur.execute("RESET ROLE")

        section(cur, "3. Owner of review_decision_events",
                "select k.relname as table_name, r.rolname as owner, k.relkind "
                "from pg_catalog.pg_class k join pg_catalog.pg_namespace n on n.oid = k.relnamespace "
                "join pg_catalog.pg_roles r on r.oid = k.relowner "
                "where n.nspname = 'public' and k.relname = %(table)s", p)

        section(cur, "4. Guard triggers present (trg_review_event_sod is the pre-existing "
                     "self-QA guard; the append_only_* pair is 20261012_sod_db_enforcement)",
                "select t.tgname, t.tgenabled::text, t.tgtype "
                "from pg_catalog.pg_trigger t "
                "where t.tgrelid = to_regclass('public.review_decision_events') "
                "  and not t.tgisinternal and t.tgname = any(%(guards)s) order by 1", p)

        section(cur, "5. Table-level ACL (pg_catalog)", ACL_SQL, p,
                header=["table_name", "column_name", "grantee", "privilege", "grant_option", "grantor"])

        section(cur, "6. Effective privileges of the runtime and owner roles (includes inherited)",
                "select r.rolname as role_name, "
                "       string_agg(pr, ', ' order by pr) filter "
                "         (where has_table_privilege(r.oid, to_regclass('public.review_decision_events'), pr)) "
                "         as effective_privileges "
                "from pg_catalog.pg_roles r "
                "cross join unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) as pr "
                "where r.rolname in (%(runtime)s, %(owner)s) group by 1 order by 1", p)

        section(cur, "7. Classification against prod_legacy_convergence.py's FINALIZE branches "
                     "(informational only -- computed here, not written anywhere)",
                "select "
                "  (select r.rolname from pg_catalog.pg_class k join pg_catalog.pg_roles r "
                "     on r.oid = k.relowner where k.oid = to_regclass('public.review_decision_events')) as owner, "
                "  has_table_privilege(%(runtime)s, to_regclass('public.review_decision_events'), 'SELECT') as sel, "
                "  has_table_privilege(%(runtime)s, to_regclass('public.review_decision_events'), 'INSERT') as ins, "
                "  has_table_privilege(%(runtime)s, to_regclass('public.review_decision_events'), 'UPDATE') as upd, "
                "  has_table_privilege(%(runtime)s, to_regclass('public.review_decision_events'), 'DELETE') as del, "
                "  exists (select 1 from pg_catalog.pg_trigger t "
                "          where t.tgrelid = to_regclass('public.review_decision_events') "
                "            and not t.tgisinternal "
                "            and t.tgname in ('trg_review_event_append_only_rows', "
                "                             'trg_review_event_append_only_trunc')) as guards_present")

        section(cur, "8. Roles involved (a role missing here does not exist under that name)",
                "select rolname, rolsuper, rolinherit, rolcreaterole, rolcanlogin from pg_catalog.pg_roles "
                "where rolname in (%(owner)s, %(runtime)s, %(mig)s) "
                "   or oid in (select relowner from pg_catalog.pg_class "
                "              where relnamespace = 'public'::regnamespace and relname = %(table)s) "
                "order by 1", p)

        section(cur, "9. Role memberships (inherit/set options shown on PostgreSQL 16+)",
                "select m.roleid::regrole::text as granted_role, m.member::regrole::text as member, "
                "       (to_jsonb(m) - 'roleid' - 'member' - 'oid' - 'grantor')::text as options "
                "from pg_catalog.pg_auth_members m "
                "where m.roleid in (select oid from pg_catalog.pg_roles where rolname in (%(owner)s, %(runtime)s)) "
                "   or m.member in (select oid from pg_catalog.pg_roles "
                "                   where rolname in (%(owner)s, %(runtime)s, %(mig)s)) "
                "order by 1, 2", p)

        print("\n=== DIAGNOSTIC COMPLETE ===")
        return 0
    finally:
        conn.rollback()
        conn.close()
        print("Read-only transaction rolled back. Nothing was changed.")


if __name__ == "__main__":
    sys.exit(main())
