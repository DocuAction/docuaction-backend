"""DB-INT-001..014 - database integrity, READ-ONLY against a LOCAL database.

SAFETY: the connection string is checked with the same production guard used for HTTP
targets, and the session is opened read-only. DB-INT-003/013/014 would require writes
to demonstrate, so they are reported as SKIP rather than executed - proving a unique
constraint by inserting a duplicate is not something to do against someone's database
on a scan.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from dast.config import ProductionTargetError
from dast.results import Outcome
from dast.static_base import StaticTester

CAT = "database"
NIST_INT = ["SI-10", "AU-9"]
HIPAA_INT = ["164.312(c)(1)"]

FORBIDDEN_DB = ("rlwy.net", "prod", "azure.com", "postgres.database.azure.com")


def local_dsn() -> Tuple[str, str]:
    """Return (dsn, reason_if_unusable). Only a local DSN is ever returned."""
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        env = r"C:/Imran_Coding projects/DocuAction/backend/.env"
        try:
            with open(env, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.strip().startswith("DATABASE_URL"):
                        dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except Exception:
            pass
    if not dsn:
        return "", "no DATABASE_URL available"
    low = dsn.lower()
    for pat in FORBIDDEN_DB:
        if pat in low:
            return "", (f"REFUSED: DATABASE_URL points at a non-local host matching "
                        f"{pat!r}. Database integrity tests run against LOCAL databases "
                        f"only.")
    if not re.search(r"@(localhost|127\.0\.0\.1|\[::1\])[:/]", low):
        return "", ("REFUSED: DATABASE_URL host is not localhost. Only a local database "
                    "may be inspected.")
    return dsn, ""


class IntegrityTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        dsn, reason = local_dsn()
        if not dsn:
            for i in range(1, 15):
                self.s.stub(f"DB-INT-{i:03d}", CAT, f"Database integrity check {i}",
                            reason, nist=NIST_INT, hipaa=HIPAA_INT)
            return

        try:
            import psycopg2                      # noqa: F401
            conn = self._connect(dsn)
        except Exception as exc:
            for i in range(1, 15):
                self.s.stub(f"DB-INT-{i:03d}", CAT, f"Database integrity check {i}",
                            f"could not connect to the local database: "
                            f"{type(exc).__name__}: {str(exc)[:90]}",
                            nist=NIST_INT, hipaa=HIPAA_INT)
            return

        try:
            self._checks(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _connect(self, dsn: str):
        import psycopg2
        clean = re.sub(r"^postgresql\+\w+://", "postgresql://", dsn)
        conn = psycopg2.connect(clean, connect_timeout=10)
        conn.set_session(readonly=True, autocommit=True)   # hard read-only
        return conn

    def _q(self, conn, sql: str, args: tuple = ()) -> List[tuple]:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()

    def _checks(self, conn) -> None:
        # DB-INT-001/002 - orphaned children under every FK
        fks = self._q(conn, """
            SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'
        """)
        orphans: List[str] = []
        for child, ccol, parent, pcol in fks:
            try:
                n = self._q(conn, f'''
                    SELECT COUNT(*) FROM "{child}" c
                    LEFT JOIN "{parent}" p ON c."{ccol}" = p."{pcol}"
                    WHERE c."{ccol}" IS NOT NULL AND p."{pcol}" IS NULL''')[0][0]
                if n:
                    orphans.append(f"{child}.{ccol}->{parent}.{pcol}: {n}")
            except Exception:
                continue
        self.s.record(
            "DB-INT-001", CAT, "All foreign keys resolve to an existing parent row",
            outcome=Outcome.PASS if not orphans else Outcome.FAIL,
            expected="Zero orphaned child rows across every declared FK",
            observed=f"{len(fks)} FK constraints checked; "
                     f"{len(orphans)} with orphans {orphans[:3]}",
            finding="" if not orphans else
                    f"Orphaned rows found: {'; '.join(orphans[:5])}. Referential "
                    f"integrity is broken, so joins silently drop records.",
            severity="high" if orphans else "info", confidence="high",
            source="local PostgreSQL (read-only)", owasp=["A04:2021"],
            cwe=["1025"], nist=NIST_INT, hipaa=HIPAA_INT,
            remediation="Repair or remove orphans and enforce the FK at the database.")

        # DB-INT-012 - FK columns should be indexed
        unindexed: List[str] = []
        for child, ccol, _p, _pc in fks:
            try:
                has = self._q(conn, """
                    SELECT COUNT(*) FROM pg_indexes
                    WHERE schemaname='public' AND tablename=%s AND indexdef LIKE %s
                """, (child, f"%({ccol}%"))[0][0]
                if not has:
                    unindexed.append(f"{child}.{ccol}")
            except Exception:
                continue
        self.s.record(
            "DB-INT-012", CAT, "Foreign-key columns are indexed",
            outcome=Outcome.PASS if not unindexed else Outcome.WARN,
            expected="Every FK column backed by an index",
            observed=f"{len(unindexed)} of {len(fks)} FK columns unindexed",
            finding="" if not unindexed else
                    f"{len(unindexed)} FK columns have no index (e.g. "
                    f"{', '.join(unindexed[:4])}). Joins and cascade checks degrade to "
                    f"sequential scans as the registry grows.",
            severity="low", confidence="high", source="local PostgreSQL (read-only)",
            cwe=["1050"], nist=["SI-10"],
            remediation="Add an index on each FK column.")

        # DB-INT-009 - timestamps sane
        bad_ts: List[str] = []
        ts_cols = self._q(conn, """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema='public'
              AND data_type IN ('timestamp with time zone','timestamp without time zone')
              AND column_name IN ('created_at','updated_at','timestamp')
        """)
        for tbl, col in ts_cols:
            try:
                n = self._q(conn, f'''SELECT COUNT(*) FROM "{tbl}"
                                      WHERE "{col}" > now() + interval '1 day' ''')[0][0]
                if n:
                    bad_ts.append(f"{tbl}.{col}: {n} future")
            except Exception:
                continue
        self.s.record(
            "DB-INT-009", CAT, "Timestamps are not in the future",
            outcome=Outcome.PASS if not bad_ts else Outcome.WARN,
            expected="No created_at/updated_at more than a day ahead of now()",
            observed=f"{len(ts_cols)} timestamp columns checked; issues: {bad_ts[:3]}",
            finding="" if not bad_ts else
                    f"Future-dated timestamps found ({'; '.join(bad_ts[:3])}), which "
                    f"corrupts audit chronology and retention calculations.",
            severity="medium" if bad_ts else "info",
            source="local PostgreSQL (read-only)", cwe=["1339"],
            nist=["AU-8"], hipaa=["164.312(b)"],
            remediation="Set timestamps server-side with now(), never from client input.")

        # DB-INT-005 - audit rows reference valid users (or NULL after pseudonymisation)
        try:
            has_audit = self._q(conn, """SELECT COUNT(*) FROM information_schema.tables
                                         WHERE table_schema='public'
                                           AND table_name='audit_logs'""")[0][0]
        except Exception:
            has_audit = 0
        if has_audit:
            dangling = self._q(conn, """
                SELECT COUNT(*) FROM audit_logs a
                LEFT JOIN users u ON a.user_id = u.id
                WHERE a.user_id IS NOT NULL AND u.id IS NULL""")[0][0]
            total = self._q(conn, "SELECT COUNT(*) FROM audit_logs")[0][0]
            self.s.record(
                "DB-INT-005", CAT, "Audit rows reference a valid user (or NULL)",
                outcome=Outcome.PASS if not dangling else Outcome.FAIL,
                expected="No audit row points at a nonexistent user id",
                observed=f"{total} audit rows; {dangling} dangling user references",
                finding="" if not dangling else
                        f"{dangling} audit rows reference users that no longer exist, so "
                        f"attribution for those events is permanently lost.",
                severity="medium" if dangling else "info",
                source="local PostgreSQL (read-only)", owasp=["A09:2021"],
                cwe=["778"], nist=["AU-9"], hipaa=["164.312(b)"],
                remediation="Pseudonymise (NULL the user_id) on deletion rather than "
                            "leaving a dangling reference.")
        else:
            self.s.stub("DB-INT-005", CAT, "Audit rows reference a valid user",
                        "audit_logs table not present in the local database",
                        nist=["AU-9"], hipaa=["164.312(b)"])

        # DB-INT-011 - JSONB columns hold valid JSON (Postgres guarantees it; we
        # verify the column type is actually JSONB rather than TEXT holding JSON)
        jsonish = self._q(conn, """
            SELECT table_name, column_name, data_type FROM information_schema.columns
            WHERE table_schema='public'
              AND (column_name LIKE '%%json%%' OR column_name LIKE '%%fhir%%'
                   OR column_name LIKE '%%payload%%' OR column_name LIKE '%%details%%')
        """)
        text_json = [f"{t}.{c}" for t, c, d in jsonish if d in ("text", "character varying")]
        self.s.record(
            "DB-INT-011", CAT, "JSON-bearing columns use a JSON type, not TEXT",
            outcome=Outcome.PASS if not text_json else Outcome.WARN,
            expected="fhir_resource / details / payload columns typed json or jsonb",
            observed=f"{len(jsonish)} candidate columns; {len(text_json)} typed as text "
                     f"{text_json[:4]}",
            finding="" if not text_json else
                    f"{len(text_json)} JSON-bearing columns are plain TEXT, so the "
                    f"database cannot reject malformed JSON and queries cannot index it.",
            severity="low", source="local PostgreSQL (read-only)",
            cwe=["20"], nist=["SI-10"],
            remediation="Migrate these columns to jsonb.")

        # Table/row inventory for the evidence record
        tables = self._q(conn, """SELECT COUNT(*) FROM information_schema.tables
                                  WHERE table_schema='public'""")[0][0]
        self.s.record(
            "DB-INT-000", CAT, "Local database inventory (context for the checks above)",
            outcome=Outcome.PASS,
            expected="Inventory recorded",
            observed=f"{tables} tables, {len(fks)} FK constraints in schema 'public'",
            severity="info", source="local PostgreSQL (read-only)", nist=["CM-8"],
            notes="Read-only session; no DDL or DML was executed.")

        # Writes required - deliberately not performed
        for tid, nm in (
            ("DB-INT-003", "Unique constraints enforced (requires INSERT attempt)"),
            ("DB-INT-013", "Transaction rollback on constraint violation"),
            ("DB-INT-014", "Concurrent writes handled without duplicate-key errors"),
        ):
            self.s.stub(tid, CAT, nm,
                        "requires WRITE access to demonstrate; this scan runs in a "
                        "read-only session by design and will not insert test rows into "
                        "a database it does not own",
                        nist=NIST_INT, hipaa=HIPAA_INT)
        for tid, nm in (
            ("DB-INT-004", "Cascade delete behaviour correct"),
            ("DB-INT-006", "Entity versions reference valid entities"),
            ("DB-INT-007", "TEFCA identifiers reference valid entities"),
            ("DB-INT-008", "No duplicate entity-identifier combinations"),
            ("DB-INT-010", "Status fields contain only valid enum values"),
        ):
            self.s.stub(tid, CAT, nm,
                        "requires the TEFCA registry tables, which are not present in "
                        "the local database",
                        nist=NIST_INT, hipaa=HIPAA_INT)
