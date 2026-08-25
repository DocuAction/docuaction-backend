# PROD Database Baseline + Least-Privilege Design — DESIGN ONLY

Nothing here has been executed. PROD was not modified.

## 1. Alembic reconciliation matrix

Sixteen migrations, one linear chain, base → `20260829_report_artifacts` (head).
PROD has never run Alembic: `alembic_version` does not exist.

| # | Revision | Tables | In PROD | Status |
|---|---|---|---|---|
| 1 | `20260627_tefca_initial` | — | — | columns/indexes only |
| 2 | `20260627_tefca_dashboard` | — | — | columns/indexes only |
| 3 | `20260725_platform_config` | — | — | columns/indexes only |
| 4 | `20260725_tefca_registry` | — | — | columns/indexes only |
| 5 | `20260817_audit_fields` | — | — | columns/indexes only |
| 6 | `20260819_dim_evidence` | 1 | 1 | **effects present** |
| 7 | `20260819_ppef_snapshots` | 2 | 2 | **effects present** |
| 8 | `20260820_ppef_jobs` | 1 | 0 | absent |
| 9 | `20260822_rce_pipeline` | 8 | 0 | absent |
| 10 | `20260823_vocab_version` | — | — | columns/indexes only |
| 11 | `20260824_evidence_prov` | 2 | 0 | absent |
| 12 | `20260825_qa_events` | 1 | 0 | absent |
| 13 | `20260826_area1_audit` | 1 | 0 | absent |
| 14 | `20260827_startup_coverage` | 3 | 3 | **guarded no-op** |
| 15 | `20260828_area1_grants` | — | — | grants only |
| 16 | `20260829_report_artifacts` | 1 | 0 | absent |

### The apparent non-monotonicity, resolved

Row 14 looks out of order — its tables exist while rows 8–13's do not. It is not
a contradiction. `20260827_startup_coverage` creates `users`, `audit_logs` and
`tefca_import_history`, each behind `if not _has_table(...)`. Those tables
predate the entire chain; the migration exists to bring tables that `create_all()`
had been making under Alembic's control. On PROD all three checks are false and
the migration is a no-op.

With row 14 understood as coverage rather than creation, **PROD's schema is a
clean prefix of the chain through revision 7**.

### Data-safety findings

- **No migration in the 8→16 upgrade path drops or alters an existing PROD table
  destructively.** Every `drop_table` / `drop_column` occurrence is inside a
  `downgrade()` function and cannot run on upgrade.
- **No migration in the upgrade path contains DML.** An automated scan flagged
  `INSERT INTO` / `DELETE FROM` in revision 15; inspection shows every match is
  lowercase prose inside docstrings explaining the privilege model. There is no
  data mutation.
- Revision 9 touches the existing `tefca_reg_entities` **additively only**
  (`_add_column`, guarded), plus a new FK from a new table.
- Revisions 9 and 15 apply GRANT/REVOKE and resolve the target through
  `DB_APP_ROLE`. Revision 15 fails closed with `Area1PrivilegeTargetError` if the
  target role owns the Area 1 tables. **Roles must exist before the upgrade
  reaches revision 9.**

## 2. Baseline strategy

**Safe baseline revision: `20260819_ppef_snapshots` (revision 7).**

Why it is safe: every object created by revisions 1–7 is verified present in
PROD, and no object created by revisions 8–16 is present except the guarded
no-ops in 14. Stamping 7 asserts only what has been measured.

    alembic stamp 20260819_ppef_snapshots     # writes alembic_version ONLY
    alembic upgrade head                      # runs 8 -> 16

`stamp` creates `alembic_version` and inserts one row. It executes no DDL and
touches no data, so all 27,013 existing rows, all 14 users, all 22,242 FCC rows,
all audit/provenance rows and all TEFCA operational data are untouched by it.

The subsequent upgrade creates the 15 missing tables, adds guarded columns to
`tefca_reg_entities`, applies Area 1 grants, and no-ops through revision 14. It
drops nothing and mutates no data.

**Before executing, generate and review the offline SQL:**

    alembic upgrade 20260819_ppef_snapshots:head --sql > prod_upgrade.sql

Every statement is reviewed before any of it runs. This is not optional — it is
the only point at which a mistake in this plan is still cheap.

The five PROD-only tables (`automation_rules`, `document_comparisons`,
`document_patterns`, `document_relationships`, `structured_extractions`, all
empty) are not represented in any migration. They stay. Dropping tables to tidy
Alembic's view is not a reason to drop tables.

## 3. create_all() startup defect — corrected in code

Implemented, tested, **not deployed**: `STARTUP_SCHEMA_MUTATION_ENABLED`
(`app/core/schema_guard.py`), unset meaning denied when `ENVIRONMENT=production`.
See `tests/test_startup_schema_guard.py`. The single `create_all()` execution and
all 27 `ALTER TABLE` statements sit inside the gate, as does `ensure_qa_table()`.
The rest of boot continues unchanged on both paths, because a gate that also
disabled the application would be switched off by whoever needed it to start.

## 4. Least-privilege role transition — SQL for review, NOT executed

Run as the server administrator, once, before the Alembic upgrade:

    CREATE ROLE docuaction_owner NOLOGIN;
    CREATE ROLE docuaction_app LOGIN PASSWORD <generated in session>;

    GRANT CONNECT ON DATABASE postgres TO docuaction_app;
    GRANT USAGE, CREATE ON SCHEMA public TO docuaction_app;
    GRANT USAGE ON SCHEMA public TO docuaction_owner;

There is deliberately **no** `GRANT docuaction_owner TO docuaction_app`. That
single omission is what makes `SET ROLE` impossible, and therefore what makes
immutability real rather than decorative.

Ownership, after the Alembic upgrade has created the Area 1 tables:

    ALTER TABLE rce_source_records         OWNER TO docuaction_owner;
    ALTER TABLE rce_source_intakes         OWNER TO docuaction_owner;
    ALTER TABLE rce_ingestion_runs         OWNER TO docuaction_owner;
    ALTER TABLE rce_rule_execution_history OWNER TO docuaction_owner;

    GRANT SELECT, INSERT ON <the four> TO docuaction_app;
    REVOKE UPDATE, DELETE, TRUNCATE ON <the four> FROM docuaction_app;

    GRANT UPDATE (promotion_status, canonical_entity_id)
       ON rce_source_records TO docuaction_app;

The column-level UPDATE is not an exception being carved out — it is the design
proven on dev. `promotion.promote_delivery` writes those two columns after the
Area 2 entities commit; a table-wide UPDATE revoke refuses that write
mid-transaction and leaves Area 1's promotion markers out of step with Area 2.
Revision `20260828_area1_grants` exists for precisely this.

Every non-Area-1 table is owned by `docuaction_app`. It must **own** them:
startup and migrations issue `ALTER TABLE`, and ALTER is not a grantable
privilege. Ownership is assigned per table rather than with `REASSIGN OWNED`,
which would sweep the Area 1 tables up with everything else.

`docuaction_app` is created without SUPERUSER, CREATEROLE, CREATEDB or
BYPASSRLS. PROD's current runtime role holds the middle three.

**Verification queries, to run before declaring success.** The dev lesson was
that the ACL can read correctly while the control is inert, so ownership is the
thing to assert:

    SELECT tablename, tableowner FROM pg_tables
     WHERE schemaname='public' AND tablename LIKE 'rce\_%';
    SELECT has_table_privilege('docuaction_app','rce_source_records','UPDATE');  -- false
    SELECT pg_has_role('docuaction_app','docuaction_owner','MEMBER');            -- false
    SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls
      FROM pg_roles WHERE rolname='docuaction_app';                              -- all false

## 5. DATABASE_URL transition

1. Create `docuaction_app` with a password generated **in the maintenance
   session**, never echoed, never written to the repository, never on a command
   line.
2. Write the new URL into the PROD app setting. Scheme `postgresql://` is fine —
   both consumers normalize to `postgresql+asyncpg://`.
3. Restart; confirm `SELECT current_user` returns `docuaction_app`.
4. Separately, later, move the value behind the existing PROD Key Vault and
   managed identity (`docuaction-kv-prod`; the PROD MI already holds Key Vault
   Secrets User). Not part of this work.

**Do not derive the new password from any existing setting.** A self-referential
derivation broke DEV authentication once in this engagement: the seed was read
from the same setting being overwritten, so the stored value was never the value
applied to the role.

## 6. Rehearsal — NOT POSSIBLE as specified, and why

A faithful rehearsal must replay `stamp 7` + `upgrade head` against a schema
structurally identical to PROD. The read-only inventory captured table names, row
counts, ownership, FK count and orphan counts — but **not column-level DDL**.
Revisions 1–7 are recorded "present" on the basis of table existence, which is
enough to choose the baseline but not enough to prove every column, index and
constraint those revisions create is present.

Rehearsing against DEV would prove nothing: DEV is already at head, so every
migration under test would no-op.

**What is required:** a schema-only capture during the next authorized PROD
window — `pg_dump --schema-only --no-owner --no-privileges` — replayed into a
scratch database, then `stamp 7` + `upgrade head` against that. A schema-only
dump contains no rows, so it carries no PII and no Government data.

## 7. PROD cutover order — sequence only, not executed

1. Verify PITR: earliest restore timestamp, geo-redundant backup Enabled; record
   the exact UTC recovery point.
2. Capture full PROD configuration (site config, app settings, container config,
   ARM export) to a secured location. This is the rollback source of truth.
3. Capture `pg_dump --schema-only`; replay into scratch; rehearse §2 there.
4. Review the generated `prod_upgrade.sql` statement by statement.
5. Open a time-boxed single-IP firewall rule.
6. Create the two roles (§4), with no membership between them.
7. `alembic stamp 20260819_ppef_snapshots`.
8. `alembic upgrade head` with `DB_APP_ROLE=docuaction_app`.
9. Apply ownership and grants (§4); run the four verification queries.
10. Rotate `DATABASE_URL` to `docuaction_app` (§5).
11. Set `STARTUP_SCHEMA_MUTATION_ENABLED=false` and
    `PPEF_BULK_INGEST_ENABLED=false` explicitly, so intent is visible in the
    configuration rather than implied by an absence.
12. Remove the firewall rule; verify TCP 5432 is blocked again.
13. Grant `AcrPull` to the PROD managed identity, scoped to `acrdocuactionprod`.
14. Container configuration: clear `appCommandLine`, set `WEBSITES_PORT=8080`,
    set `linuxFxVersion` to the imported digest.
15. Health: `/api/config` 200; `SELECT current_user` = `docuaction_app`;
    `/api/reports/health/engine` reports WeasyPrint available; Area 1 privilege
    checks pass; PROD row counts unchanged from step 2.
16. Rollback triggers: any health check failing; any Area 1 privilege check
    returning true for UPDATE/DELETE/TRUNCATE; `current_user` not
    `docuaction_app`; any PROD row count differing from the step-2 capture.
17. Rollback: restore `linuxFxVersion` and `appCommandLine`, remove
    `WEBSITES_PORT`, restart, re-verify. Configuration rollback does not restore
    data — if any row count moved, stop and use the step-1 PITR point instead.
