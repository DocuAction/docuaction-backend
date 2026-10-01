"""Read-only reconciliation of the "1,298 Failed" indicator (AP-007 /
CHECKPOINT-1298-FAILED-INDICATOR-STATIC-ANALYSIS.md) for job
0930826c-970e-419d-ab8d-f05bb4f99116, intake
4417b334-7440-4c30-9afa-05f4268f17c7, on Azure DEV.

PREPARED, NOT RUN. This script is invoked only by
`.github/workflows/dev-readonly-diagnostic.yml`, which is `workflow_dispatch`
only -- nothing triggers it automatically. It is committed here so the
governed diagnostic has a versioned, reviewable artifact to run instead of
an ad-hoc manual SSH session (the static-analysis checkpoint explains why a
manual session was not attempted).

Connects the same way `.github/scripts/gov_integrity_snapshot.py` already
does: the workflow's own `az account get-access-token` token, passed in as
PGTOKEN (never written to a file, never echoed), `SET ROLE
docuaction_owner` (the only role this dedicated identity can assume without
a new grant -- same reasoning as that script), then every statement below
runs inside one explicit `READ ONLY` transaction with a 20s statement
timeout. Selects and prints AGGREGATE COUNTS ONLY -- no NPI, entity name,
source payload, or credential is ever selected, referenced, or printed.
"""
import json
import os
import sys

import psycopg2

PGHOST = os.environ["PGHOST"]
PGDATABASE = os.environ["PGDATABASE"]
PG_PRINCIPAL = os.environ["PG_PRINCIPAL"]
PGTOKEN = os.environ["PGTOKEN"]

INTAKE_ID = "4417b334-7440-4c30-9afa-05f4268f17c7"
STATEMENT_TIMEOUT_MS = 20000  # 20s bound, matches the task's required cap

_AGG_SQL = """
WITH pop AS (
  SELECT DISTINCT canonical_entity_id AS eid
  FROM rce_curated_records
  WHERE source_intake_id = %(intake)s::uuid AND canonical_entity_id IS NOT NULL
),
pop_text AS (SELECT CAST(eid AS text) AS eid FROM pop),
ev AS (
  SELECT v.entity_id::text AS eid,
         CASE WHEN lower(coalesce(v.detail, '')) LIKE '%%deactivat%%' THEN 'deactivated'
              ELSE (CASE lower(btrim(coalesce(v.verification_status, '')))
                 WHEN 'verified' THEN 'verified' WHEN 'match' THEN 'verified' WHEN 'matched' THEN 'verified'
                 WHEN 'not_found' THEN 'not_found' WHEN 'no_match' THEN 'not_found'
                 WHEN 'unavailable' THEN 'unavailable' WHEN 'source_unavailable' THEN 'unavailable'
                 WHEN 'failed' THEN 'failed' WHEN 'error' THEN 'failed'
                 WHEN 'deactivated' THEN 'deactivated' ELSE NULL END) END AS outcome
  FROM tefca_verifications v
  WHERE v.entity_id IN (SELECT eid FROM pop)
  UNION ALL
  SELECT d.entity_id AS eid,
         CASE upper(btrim(coalesce(d.disposition, '')))
              WHEN 'PASS' THEN 'verified' WHEN 'CORROBORATED' THEN 'verified'
              WHEN 'NOT_FOUND' THEN 'not_found'
              WHEN 'UNAVAILABLE' THEN 'unavailable'
              WHEN 'FAIL' THEN 'failed' WHEN 'CONFLICT' THEN 'failed' ELSE NULL END AS outcome
  FROM tefca_dimension_evidence d
  WHERE d.entity_id IN (SELECT eid FROM pop_text)
),
per_entity AS (
  SELECT eid,
    bool_or(outcome = 'verified')    AS any_verified,
    bool_or(outcome = 'not_found')   AS any_not_found,
    bool_or(outcome = 'failed')      AS any_failed,
    bool_or(outcome = 'unavailable') AS any_unavailable
  FROM ev WHERE outcome IS NOT NULL GROUP BY eid
)
SELECT
  (SELECT count(DISTINCT eid) FROM ev)                                   AS attempted_union,
  (SELECT count(*) FROM per_entity WHERE any_verified)                   AS verified_distinct,
  (SELECT count(*) FROM per_entity WHERE any_not_found)                  AS not_found_distinct,
  (SELECT count(*) FROM per_entity WHERE any_failed)                     AS failed_distinct,
  (SELECT count(*) FROM per_entity WHERE any_unavailable)                AS unavailable_distinct,
  (SELECT count(*) FROM per_entity WHERE any_verified AND any_failed)    AS verified_failed_overlap,
  (SELECT count(*) FROM per_entity WHERE any_not_found AND any_failed)   AS notfound_failed_overlap,
  (SELECT count(*) FROM per_entity WHERE any_unavailable AND any_failed) AS unavailable_failed_overlap
"""

_BY_STATUS_SQL = """
WITH pop AS (SELECT DISTINCT canonical_entity_id AS eid FROM rce_curated_records
             WHERE source_intake_id = %(intake)s::uuid AND canonical_entity_id IS NOT NULL)
SELECT lower(btrim(coalesce(v.source,'(null)'))) AS source,
       lower(btrim(coalesce(v.verification_status,'(null)'))) AS raw_status,
       count(*) AS row_count, count(DISTINCT v.entity_id) AS distinct_entities
FROM tefca_verifications v WHERE v.entity_id IN (SELECT eid FROM pop)
GROUP BY 1, 2 ORDER BY 1, 2
"""

_BY_DISPOSITION_SQL = """
WITH pop_text AS (SELECT CAST(canonical_entity_id AS text) AS eid FROM rce_curated_records
                  WHERE source_intake_id = %(intake)s::uuid AND canonical_entity_id IS NOT NULL)
SELECT lower(btrim(coalesce(d.source,'(null)'))) AS source,
       upper(btrim(coalesce(d.disposition,'(null)'))) AS raw_disposition,
       count(*) AS row_count, count(DISTINCT d.entity_id) AS distinct_entities
FROM tefca_dimension_evidence d WHERE d.entity_id IN (SELECT eid FROM pop_text)
GROUP BY 1, 2 ORDER BY 1, 2
"""


def _rows_as_dicts(cur) -> list:
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> None:
    try:
        conn = psycopg2.connect(
            host=PGHOST, dbname=PGDATABASE, user=PG_PRINCIPAL, password=PGTOKEN,
            sslmode="require", connect_timeout=20,
        )
    except psycopg2.OperationalError as exc:
        # Same network boundary migration-preflight.yml and
        # gov_integrity_snapshot.py already document: DEV Postgres's firewall
        # trusts only the docuaction-dev App Service's own outbound IPs. The
        # workflow's handshake step (wait for the operator's temporary /32)
        # runs before this script, so reaching here unreachable means the
        # handshake did not complete -- reported as data, not a crash.
        print(f"DEV Postgres is not reachable from this runner: {exc}", file=sys.stderr)
        print("readable=false")
        sys.exit(0)

    conn.autocommit = False  # this connection only ever does one explicit transaction, then closes
    cur = conn.cursor()

    role = os.environ.get("DB_INTEGRITY_ROLE", "").strip()
    if not role:
        print("FAIL: DB_INTEGRITY_ROLE is not set - refusing to read under an "
              "unspecified role.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    out = {"intake_id": INTAKE_ID}
    try:
        cur.execute("set role " + role)  # role name validated by the DB, never by this script
        cur.execute("BEGIN TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))

        cur.execute("SELECT count(*) FROM rce_source_records WHERE source_intake_id = %(intake)s::uuid",
                    {"intake": INTAKE_ID})
        out["total_records"] = cur.fetchone()[0]

        cur.execute(
            "SELECT count(DISTINCT canonical_entity_id) FROM rce_curated_records "
            "WHERE source_intake_id = %(intake)s::uuid AND canonical_entity_id IS NOT NULL",
            {"intake": INTAKE_ID})
        out["eligible"] = cur.fetchone()[0]

        cur.execute(_AGG_SQL, {"intake": INTAKE_ID})
        agg_cols = [c.name for c in cur.description]
        out.update(dict(zip(agg_cols, cur.fetchone())))

        cur.execute(_BY_STATUS_SQL, {"intake": INTAKE_ID})
        out["tefca_verifications_by_status"] = _rows_as_dicts(cur)

        cur.execute(_BY_DISPOSITION_SQL, {"intake": INTAKE_ID})
        out["tefca_dimension_evidence_by_disposition"] = _rows_as_dicts(cur)

        cur.execute("ROLLBACK")  # reader only; nothing to commit, ever
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:  # noqa: BLE001 - connection is about to be closed regardless
            pass
        conn.close()
        raise
    conn.close()

    print("readable=true")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
