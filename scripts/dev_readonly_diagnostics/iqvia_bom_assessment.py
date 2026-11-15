"""READ-ONLY assessment of IQVIA reference snapshots staged by an earlier reader.

DRAFT / NOT FINAL / INDEPENDENT QA PENDING. DEV only. Writes nothing to the
database. Returns snapshot metadata and aggregate counts, never a record value.

    python -I scripts/dev_readonly_diagnostics/iqvia_bom_assessment.py --out result.json

Environment (the same pattern as the other scripts in this folder; the token is
read from the environment or from an existing `az` sign-in and is never printed):

    PGHOST  PGDATABASE  PG_USER  [PGTOKEN]

WHAT ENFORCES READ-ONLY. Three independent layers; the assessment stops if any
cannot be established:

  1. `inspect_readonly_sql`: only a single SELECT, over an allowed list of
     functions, ever reaches the server. Other SQL is refused before it is sent.
  2. A SELECT-only database role. By default the session switches to the
     existing role `DocuAction-DEV-DB-Readers` (`SET ROLE`) and checks
     `current_user` before every query. No role is created and nothing is granted.
  3. A read-only transaction (`BEGIN READ ONLY`, plus `default_transaction_read_only`
     at connection time). `SHOW transaction_read_only` must answer `on` before
     every query; each query runs in its own transaction that ends in ROLLBACK.

WHAT IT READS. Metadata of the registration-time snapshot rows and of their
decision rows; the import job's state; COUNT(*) of staged rows; a boolean from the
KEY NAMES of one staged row's payload (the file's header as the reader saw it:
no value is selected); a count of rows whose `npi` column is empty; and counts of
rows in foreign-key referencing tables. It does not read preserved files in this
run (see `preserved_bytes_bom`): that is reported as not inspected.

A statement list is exported with `--print-queries` so the same SELECTs can be run
by someone else if this script cannot connect.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.getcwd())
from app.tefca_registry.rce import iqvia_reader_assessment as ra  # noqa: E402

READER_ROLE = "DocuAction-DEV-DB-Readers"
SOURCES = ("IQVIA_HCO", "IQVIA_HCP", "IQVIA_AFFILIATION")
OBSERVATION_TABLE = {
    "IQVIA_HCO": "iqvia_hco_observation",
    "IQVIA_HCP": "iqvia_hcp_observation",
    "IQVIA_AFFILIATION": "iqvia_affiliation_observation",
}
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
#: Foreign-key tables that are the import PIPELINE, not use of the snapshot.
PIPELINE_TABLES = frozenset({"iqvia_import_job", "iqvia_upload_session"})

Q_INVENTORY = """
select s.id::text as snapshot_id, s.source_system, s.status, s.record_count,
       s.metadata->>'import_status' as import_status,
       s.metadata->>'rows_rejected' as rows_rejected,
       s.metadata->>'reference_reader' as reader,
       (s.metadata ? 'reference_reader_refused') as reader_refused,
       (s.metadata ? 'reference_preflight') as preflight_recorded,
       s.metadata->'reference_preflight'->>'gate' as preflight_gate,
       s.metadata->'reference_preflight'->>'preflight_version' as preflight_version,
       ((s.metadata->'reference_preflight'->'findings') @> jsonb_build_array(jsonb_build_object('code', 'REF-SCH-010'))) as preflight_bom,
       (s.metadata ? 'durable_original') as durable_original_recorded,
       s.created_at::date::text as created_on
from source_snapshot s
where s.source_system in ('IQVIA_HCO', 'IQVIA_HCP', 'IQVIA_AFFILIATION')
  and s.supersedes_snapshot_id is null
order by s.created_at
"""
Q_DECISIONS = """
select d.supersedes_snapshot_id::text as root_id, d.id::text as decision_id, d.status as decision_status
from source_snapshot d
where d.supersedes_snapshot_id = any(%(ids)s::uuid[])
"""
Q_JOB = """
select j.state, j.attempt_count
from iqvia_import_job j
where j.snapshot_id = %(id)s
order by j.created_at desc
limit 1
"""
Q_FK_TABLES = """
select c.conrelid::regclass::text as tbl, a.attname as col
from pg_constraint c
join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
where c.contype = 'f' and c.confrelid = 'source_snapshot'::regclass
order by 1, 2
"""


def q_staged(source: str) -> str:
    # The table name comes from the fixed OBSERVATION_TABLE mapping, never from input.
    return f"select count(*) from {OBSERVATION_TABLE[source]} where source_snapshot_id = %(id)s"  # noqa: S608


def q_key_sample(source: str) -> str:
    # Key NAMES of one staged row (the header as read). No value is selected.
    return (  # noqa: S608 - fixed table mapping
        "select bool_or(left(k, 1) = chr(65279)) as has_bom_key, count(*) as key_count "  # noqa: S608
        f"from (select payload from {OBSERVATION_TABLE[source]} where source_snapshot_id = %(id)s limit 1) r "
        "cross join lateral jsonb_object_keys(r.payload) k")


def q_npi_missing(source: str) -> str:
    return (f"select count(*) filter (where npi is null) from {OBSERVATION_TABLE[source]} "  # noqa: S608
            "where source_snapshot_id = %(id)s")


def q_downstream(table: str, column: str) -> str:
    if not (_IDENT.match(table) and _IDENT.match(column)):
        raise ValueError("unexpected identifier from the catalog")
    # Both identifiers were validated against _IDENT above (they come from the catalog).
    return f"select count(*) from {table} where {column} = any(%(ids)s::uuid[])"  # noqa: S608


def all_queries() -> List[str]:
    out = [Q_INVENTORY, Q_DECISIONS, Q_JOB, Q_FK_TABLES]
    for src in SOURCES:
        out += [q_staged(src), q_key_sample(src)]
        if src != "IQVIA_AFFILIATION":
            out.append(q_npi_missing(src))
    # One statement per foreign-key referencing table, found from the catalog at run time.
    out.append(q_downstream("referencing_table", "referencing_column"))
    return out


# ── the enforced read-only session ───────────────────────────────────────────

class ReadOnlySession:
    """One connection; every query is inspected, role-checked and read-only-checked."""

    def __init__(self, conn, *, expected_role: Optional[str]):
        self.conn = conn
        self.expected_role = expected_role
        self.queries_run = 0

    def fetch(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Tuple]:
        ra.inspect_readonly_sql(sql)
        cur = self.conn.cursor()
        try:
            cur.execute("show transaction_read_only")
            if cur.fetchone()[0] != "on":
                raise RuntimeError("transaction_read_only is not on; refusing to run the query")
            if self.expected_role:
                cur.execute("select current_user")
                if cur.fetchone()[0] != self.expected_role:
                    raise RuntimeError("the session is not running as the read-only role; refusing")
            cur.execute(sql, params or {})
            rows = cur.fetchall()
            self.queries_run += 1
            return rows
        finally:
            self.conn.rollback()                      # every query ends its own transaction

    def close(self) -> None:
        try:
            self.conn.rollback()
        finally:
            self.conn.close()


def connect_readonly(*, host: str, dbname: str, user: str, role: Optional[str]) -> ReadOnlySession:
    import psycopg2

    token = os.environ.get("PGTOKEN") or subprocess.run(  # noqa: S603
        ["az", "account", "get-access-token", "--resource", "https://ossrdbms-aad.database.windows.net",  # noqa: S607
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=(sys.platform == "win32")).stdout.strip()
    if not token:
        raise RuntimeError("no access token available from the environment or an existing az sign-in")
    conn = psycopg2.connect(
        host=host, port=5432, dbname=dbname, user=user, password=token, sslmode="require", connect_timeout=15,
        options="-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=2000 "
                "-c idle_in_transaction_session_timeout=60000")
    token = ""                                        # not kept
    return harden_connection(conn, role)


_ROLE_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,63}$")


def harden_connection(conn, role: Optional[str]) -> "ReadOnlySession":
    """Apply the enforcement layers to an open connection and wrap it: switch to
    the SELECT-only role (when given) and make every transaction read-only."""
    if role:
        if not _ROLE_NAME.match(role):
            raise ValueError("unexpected role name")
        conn.autocommit = True                        # SET ROLE must survive a rollback
        cur = conn.cursor()
        cur.execute(f'set role "{role}"')
    conn.autocommit = False
    conn.set_session(readonly=True, autocommit=False)
    return ReadOnlySession(conn, expected_role=role)


# ── gathering ────────────────────────────────────────────────────────────────

def _int(value) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def gather(session: ReadOnlySession) -> Tuple[List[ra.SnapshotFacts], Dict[str, Any]]:
    inventory = session.fetch(Q_INVENTORY)
    ids = [row[0] for row in inventory]
    decisions: Dict[str, Tuple[str, str]] = {}
    if ids:
        for root, decision_id, status in session.fetch(Q_DECISIONS, {"ids": ids}):
            decisions[root] = (decision_id, status)
    fk_tables = [(t.split(".")[-1], c) for t, c in session.fetch(Q_FK_TABLES)]
    skip = {"source_snapshot", *OBSERVATION_TABLE.values(), *PIPELINE_TABLES}
    referencing = [(t, c) for t, c in fk_tables if t not in skip]

    facts: List[ra.SnapshotFacts] = []
    coverage = {"snapshots_listed": len(inventory), "referencing_tables": [f"{t}.{c}" for t, c in referencing],
                "queries_run_before_snapshots": session.queries_run, "notes": []}
    for (sid, source, status, record_count, import_status, rejected, reader, refused, pf_rec, pf_gate,
         pf_version, pf_bom, durable, created_on) in inventory:
        f = ra.SnapshotFacts(
            snapshot_id=sid, source_system=source, status=status, declared_record_count=_int(record_count),
            import_status=import_status, rows_rejected=_int(rejected), reader=reader,
            reader_refused=bool(refused), preflight_recorded=bool(pf_rec), preflight_gate=pf_gate,
            preflight_version=pf_version, created_on=created_on, durable_original_recorded=bool(durable),
            preflight_bom_finding=(bool(pf_bom) if pf_rec and pf_bom is not None else (False if pf_rec else None)))
        decision = decisions.get(sid)
        if decision:
            f.decision_snapshot_id, f.decision_status = decision
        try:
            job = session.fetch(Q_JOB, {"id": sid})
            f.job_state = job[0][0] if job else None
        except Exception:                             # noqa: BLE001 - recorded as not read
            f.job_state = None
        try:
            f.staged_count = int(session.fetch(q_staged(source), {"id": sid})[0][0])
        except Exception:                             # noqa: BLE001
            f.staged_count = None
        if f.staged_count:
            try:
                has_bom_key, key_count = session.fetch(q_key_sample(source), {"id": sid})[0]
                f.sampled_payload_has_bom_key = None if has_bom_key is None else bool(has_bom_key)
                f.sampled_payload_key_count = _int(key_count)
            except Exception:                         # noqa: BLE001 - the fact stays "not established" (None)
                coverage["notes"].append(f"{sid[:8]}: key-name sample not read")
            if source != "IQVIA_AFFILIATION":
                try:
                    f.npi_missing_count = int(session.fetch(q_npi_missing(source), {"id": sid})[0][0])
                except Exception:                     # noqa: BLE001 - stays None
                    coverage["notes"].append(f"{sid[:8]}: empty-npi count not read")
        target_ids = [sid] + ([decision[0]] if decision else [])
        for table, column in referencing:
            try:
                f.downstream_by_table[f"{table}.{column}"] = int(
                    session.fetch(q_downstream(table, column), {"ids": target_ids})[0][0])
            except Exception:                         # noqa: BLE001 - recorded as unreadable, not as zero
                f.downstream_by_table[f"{table}.{column}"] = None
        facts.append(f)
    return facts, coverage


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="write the screened JSON result here")
    ap.add_argument("--role", default=READER_ROLE, help="existing SELECT-only role to SET ROLE to ('' for none)")
    ap.add_argument("--print-queries", action="store_true", help="print the SELECT statements and exit")
    ap.add_argument("--from-facts", help="recompute the assessment offline from a saved `.facts.json` (no database)")
    args = ap.parse_args(argv)
    if args.print_queries:
        for q in all_queries():
            ra.inspect_readonly_sql(q)
            print(q.strip() + ";\n")
        return 0
    if args.from_facts:
        with open(args.from_facts, encoding="utf-8") as fh:
            facts = [ra.SnapshotFacts(**row) for row in json.load(fh)]
        coverage = {"recomputed_offline_from": os.path.basename(args.from_facts)}
    else:
        host = os.environ["PGHOST"]
        if "docuaction-db-dev" not in host:
            print("refusing: the target is not the DEV server", file=sys.stderr)
            return 2
        session = connect_readonly(host=host, dbname=os.environ.get("PGDATABASE", "postgres"),
                                   user=os.environ["PG_USER"], role=(args.role or None))
        try:
            facts, coverage = gather(session)
        finally:
            session.close()
        coverage["queries_run_total"] = session.queries_run
    assessments = [ra.assess(f) for f in facts]
    if args.out and not args.from_facts:
        # The facts hold only ids, flags, enumerations and counts (see SnapshotFacts);
        # saved before screening so a refusal below cannot lose the run. An offline
        # recompute (`--from-facts`) never overwrites the saved facts or coverage.
        from dataclasses import asdict
        with open(args.out + ".facts.json", "w", encoding="utf-8") as fh:
            json.dump([asdict(f) for f in facts], fh, indent=2, default=str)
        with open(args.out + ".coverage.json", "w", encoding="utf-8") as fh:
            json.dump(coverage, fh, indent=2)
    try:
        text = ra.screen_output(assessments)
    except ValueError as exc:
        print(f"output refused by the screen: {exc}", file=sys.stderr)
        return 3
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
