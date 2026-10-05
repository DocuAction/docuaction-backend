"""Read-only ownership diagnostic for DEV (report_export_jobs, rce_delivery_stage_events).

Run by .github/workflows/dev-ownership-readonly-diagnostic.yml. It reads
catalog metadata and the single alembic_version row, inside one
`BEGIN TRANSACTION READ ONLY` that always ends in ROLLBACK. It executes no
DDL, no GRANT/REVOKE, no ownership change and reads no business row.

Why: revision 20261001_report_generation_jobs failed on DEV (run
37272045742) with "must be owner of table report_export_jobs". Before any
repair is chosen, the actual owner, ACL, runtime privileges and the
migration identity's authority have to be read rather than assumed.

Connection: the same dedicated identity and token pattern as
migration-preflight.yml (PGHOST / PGDATABASE / PG_PRINCIPAL / PGTOKEN). The
token is read from the environment and never printed.
"""
import os
import sys

import psycopg2

TABLES = ("report_export_jobs", "rce_delivery_stage_events")
OWNER_ROLE = "docuaction_owner"
RUNTIME_ROLE = "docuaction_app"
MIGRATION_IDENTITY = os.environ["PG_PRINCIPAL"]
STATEMENT_TIMEOUT_MS = 20000

ACL_SQL = """
    select k.relname, '' as column_name, coalesce(gee.rolname, 'PUBLIC'), a.privilege_type,
           a.is_grantable, gor.rolname
    from pg_catalog.pg_class k
    join pg_catalog.pg_namespace n on n.oid = k.relnamespace
    cross join lateral aclexplode(coalesce(k.relacl, acldefault('r', k.relowner))) a
    join pg_catalog.pg_roles gor on gor.oid = a.grantor
    left join pg_catalog.pg_roles gee on gee.oid = a.grantee
    where n.nspname = 'public' and k.relname = any(%(tables)s)
    union all
    select k.relname, att.attname, coalesce(gee.rolname, 'PUBLIC'), a.privilege_type,
           a.is_grantable, gor.rolname
    from pg_catalog.pg_class k
    join pg_catalog.pg_namespace n on n.oid = k.relnamespace
    join pg_catalog.pg_attribute att on att.attrelid = k.oid and att.attnum > 0 and not att.attisdropped
    cross join lateral aclexplode(att.attacl) a
    join pg_catalog.pg_roles gor on gor.oid = a.grantor
    left join pg_catalog.pg_roles gee on gee.oid = a.grantee
    where n.nspname = 'public' and k.relname = any(%(tables)s) and att.attacl is not null
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
    p = {"tables": list(TABLES), "owner": OWNER_ROLE, "runtime": RUNTIME_ROLE, "mig": MIGRATION_IDENTITY}
    try:
        cur.execute("BEGIN TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))

        section(cur, "1. Session and server",
                "select current_user, session_user, current_database(), "
                "current_setting('server_version') as server_version, "
                "current_setting('transaction_read_only') as transaction_read_only")

        # alembic_version is read the way migration-preflight.yml reads it: as the owner role.
        print("\n=== 2. Current Alembic revision (expected 20260930_alembic_version_read) ===")
        cur.execute("SAVEPOINT rev")
        try:
            cur.execute(f"SET LOCAL ROLE {OWNER_ROLE}")
            cur.execute("select version_num from public.alembic_version")
            print("alembic_version rows:", [r[0] for r in cur.fetchall()])
        except psycopg2.Error as exc:
            cur.execute("ROLLBACK TO SAVEPOINT rev")
            print(f"NOT READABLE: {type(exc).__name__}: {str(exc).strip()}")
        cur.execute("RESET ROLE")

        section(cur, "3. Owners of the affected tables",
                "select k.relname as table_name, r.rolname as owner, k.relkind "
                "from pg_catalog.pg_class k join pg_catalog.pg_namespace n on n.oid = k.relnamespace "
                "join pg_catalog.pg_roles r on r.oid = k.relowner "
                "where n.nspname = 'public' and k.relname = any(%(tables)s) order by 1", p)

        section(cur, "4. Left behind by the failed migration? (expect f / f)",
                "select (select string_agg(att.attname, ', ' order by att.attnum) "
                "        from pg_catalog.pg_attribute att "
                "        where att.attrelid = to_regclass('public.report_export_jobs') "
                "          and att.attnum > 0 and not att.attisdropped) as report_export_jobs_columns, "
                "       exists (select 1 from pg_catalog.pg_attribute att "
                "               where att.attrelid = to_regclass('public.report_export_jobs') "
                "                 and att.attname in ('report_type', 'request_parameters') "
                "                 and not att.attisdropped) as new_columns_present, "
                "       to_regclass('public.ix_report_export_jobs_report_type') is not null as new_index_present")
        section(cur, "4b. Indexes on report_export_jobs",
                "select indexname from pg_catalog.pg_indexes "
                "where schemaname = 'public' and tablename = 'report_export_jobs' order by 1")

        section(cur, "5. Table-level and column-level ACLs (pg_catalog)", ACL_SQL, p,
                header=["table_name", "column_name", "grantee", "privilege", "grant_option", "grantor"])

        section(cur, "6. Effective privileges of the runtime and owner roles (includes inherited)",
                "select r.rolname as role_name, k.relname as table_name, "
                "       string_agg(pr, ', ' order by pr) filter (where has_table_privilege(r.oid, k.oid, pr)) "
                "         as effective_privileges "
                "from pg_catalog.pg_roles r cross join pg_catalog.pg_class k "
                "cross join unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) as pr "
                "where r.rolname in (%(runtime)s, %(owner)s) and k.relnamespace = 'public'::regnamespace "
                "  and k.relname = any(%(tables)s) group by 1, 2 order by 1, 2", p)

        section(cur, "7. Roles involved (a role missing here does not exist under that name)",
                "select rolname, rolsuper, rolinherit, rolcreaterole, rolcanlogin from pg_catalog.pg_roles "
                "where rolname in (%(owner)s, %(runtime)s, %(mig)s) "
                "   or oid in (select relowner from pg_catalog.pg_class "
                "              where relnamespace = 'public'::regnamespace and relname = any(%(tables)s)) "
                "order by 1", p)

        section(cur, "8. Role memberships (inherit/set options shown on PostgreSQL 16+)",
                "select m.roleid::regrole::text as granted_role, m.member::regrole::text as member, "
                "       (to_jsonb(m) - 'roleid' - 'member' - 'oid' - 'grantor')::text as options "
                "from pg_catalog.pg_auth_members m "
                "where m.roleid in (select oid from pg_catalog.pg_roles where rolname in (%(owner)s, %(runtime)s)) "
                "   or m.member in (select oid from pg_catalog.pg_roles "
                "                   where rolname in (%(owner)s, %(runtime)s, %(mig)s)) "
                "   or m.roleid in (select relowner from pg_catalog.pg_class "
                "                   where relnamespace = 'public'::regnamespace and relname = 'report_export_jobs') "
                "order by 1, 2", p)

        section(cur, "9. Can the migration identity transfer report_export_jobs? (empty = a role does not exist)",
                "with ids as ( "
                "  select (select oid from pg_catalog.pg_roles where rolname = %(mig)s) as mig, "
                "         (select oid from pg_catalog.pg_roles where rolname = %(owner)s) as new_owner, "
                "         (select oid from pg_catalog.pg_roles where rolname = %(runtime)s) as runtime, "
                "         (select relowner from pg_catalog.pg_class where relnamespace = 'public'::regnamespace "
                "            and relname = 'report_export_jobs') as cur_owner) "
                "select cur_owner::regrole::text as current_owner, "
                "       cur_owner = runtime as current_owner_is_the_runtime_role, "
                "       pg_has_role(mig, new_owner, 'MEMBER') as migration_identity_member_of_docuaction_owner, "
                "       pg_has_role(mig, cur_owner, 'MEMBER') as migration_identity_member_of_current_owner, "
                "       pg_has_role(new_owner, cur_owner, 'MEMBER') as docuaction_owner_member_of_current_owner, "
                "       pg_has_role(cur_owner, new_owner, 'MEMBER') as current_owner_member_of_docuaction_owner "
                "from ids", p)
        print("\n=== DIAGNOSTIC COMPLETE ===")
        return 0
    finally:
        conn.rollback()
        conn.close()
        print("Read-only transaction rolled back. Nothing was changed.")


if __name__ == "__main__":
    sys.exit(main())
