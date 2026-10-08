"""Schema-downgrade recovery rehearsal -- DISPOSABLE CLUSTER, SYNTHETIC DATA ONLY.

Companion to docs/review/SCHEMA-DOWNGRADE-RECOVERY-20261004.md.

It provisions its OWN PostgreSQL cluster with initdb in a new directory on an
unused local port, applies the real Alembic chain, seeds synthetic rows, and
exercises: downgrade refusals, single-command vs per-revision transaction
behaviour, and detect -> archive -> verify -> delete -> downgrade -> re-upgrade.

It never reads DATABASE_URL, never connects anywhere but 127.0.0.1:<its port>,
and re-verifies data directory, port, database name and a random marker token
before every destructive step. The DELETE statements here exist to rehearse
the mechanics on synthetic fixtures. They are NOT a recommended procedure for
any shared environment -- see the runbook.

    set DOCUACTION_REHEARSAL_DISPOSABLE=YES-SYNTHETIC-ONLY
    python scripts/schema_downgrade_recovery_rehearsal.py ^
        --pg-bin "C:/Program Files/PostgreSQL/18/bin" ^
        --workdir C:/tmp/rehearsal_new_dir --port 5547 --evidence C:/tmp/evidence
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg2

GUARD_ENV = "DOCUACTION_REHEARSAL_DISPOSABLE"
GUARD_VALUE = "YES-SYNTHETIC-ONLY"
OWNER, APP = "docuaction_owner", "docuaction_app"
PREV = "20261003_preflight_shadow"
R_HELD = "20261004_preflight_exec_held"
R_STAGE = "20261004_stage_event_preflight"
HEAD = "20261007_preflight_run_finalize"
CK_EXEC = "ck_rce_preflight_finding_execution"
CK_STAGE = "ck_rce_stage_event_stage"
TAG = "SYNTHETIC-REHEARSAL"
TEMPLATE = "rehearsal_template"

DETECT_SQL = """
SELECT 'preflight_held' AS blocker, count(*) AS rows
FROM public.rce_preflight_finding
WHERE execution = 'held'
UNION ALL
SELECT 'stage_preflight', count(*)
FROM public.rce_delivery_stage_events
WHERE stage = 'PREFLIGHT'
UNION ALL
SELECT 'recheck_jobs', count(*)
FROM public.rce_recheck_job
UNION ALL
SELECT 'recheck_items', count(*)
FROM public.rce_recheck_item
"""

#: (archive table, source table, row filter) -- every column is preserved.
ARCHIVE_SETS = (
    ("rce_preflight_finding_held", "rce_preflight_finding", "execution = 'held'"),
    ("rce_delivery_stage_events_preflight", "rce_delivery_stage_events", "stage = 'PREFLIGHT'"),
    ("rce_recheck_job", "rce_recheck_job", "true"),
    ("rce_recheck_item", "rce_recheck_item", "true"),
)

RESULTS: list[dict] = []
LOG: list[str] = []


def log(msg: str) -> None:
    line = f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%SZ')}] {msg}"
    LOG.append(line)
    print(line, flush=True)


def record(exercise: str, status: str, **facts) -> None:
    RESULTS.append({"exercise": exercise, "status": status, **facts})
    log(f"RESULT {status:8} {exercise} :: {json.dumps(facts, default=str)[:900]}")


class Cluster:
    def __init__(self, pg_bin: Path, workdir: Path, port: int, backend: Path):
        self.pg_bin, self.workdir, self.port, self.backend = pg_bin, workdir, port, backend
        self.pgdata = workdir / "pgdata"
        self.token = secrets.token_hex(16)

    def exe(self, name: str) -> str:
        return str(self.pg_bin / (name + (".exe" if os.name == "nt" else "")))

    # -- provisioning ---------------------------------------------------------
    def provision(self) -> None:
        if self.workdir.exists() and any(self.workdir.iterdir()):
            raise SystemExit(f"REFUSED: workdir {self.workdir} exists and is not empty. "
                             "This rehearsal only ever uses a cluster it created itself.")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", self.port)) == 0:
                raise SystemExit(f"REFUSED: something already listens on 127.0.0.1:{self.port}.")
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "REHEARSAL_DISPOSABLE_MARKER").write_text(self.token)
        subprocess.run([self.exe("initdb"), "-D", str(self.pgdata), "-U", "postgres",
                        "-A", "trust", "--locale=C", "-E", "UTF8"],
                       check=True, capture_output=True, text=True)
        subprocess.run([self.exe("pg_ctl"), "-D", str(self.pgdata), "-w",
                        "-o", f"-p {self.port} -c listen_addresses=127.0.0.1",
                        "-l", str(self.workdir / "postgres.log"), "start"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.conn("postgres") as c:
            cur = c.cursor()
            cur.execute(f'CREATE ROLE "{OWNER}" NOLOGIN')
            cur.execute(f'CREATE ROLE "{APP}" LOGIN')
        log(f"cluster provisioned: {self.pgdata} port {self.port}")

    def stop(self) -> None:
        subprocess.run([self.exe("pg_ctl"), "-D", str(self.pgdata), "-m", "fast", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("cluster stopped")

    # -- connections ----------------------------------------------------------
    @contextlib.contextmanager
    def conn(self, db: str, role: str | None = None):
        """Autocommit connection, closed on exit. (psycopg2's own `with conn`
        opens a transaction block, which CREATE DATABASE refuses.)"""
        c = psycopg2.connect(host="127.0.0.1", port=self.port, user="postgres", dbname=db,
                             application_name="schema-downgrade-rehearsal")
        c.autocommit = True
        try:
            if role:
                c.cursor().execute(f'SET ROLE "{role}"')
            yield c
        finally:
            c.close()

    def q(self, db: str, sql: str, params=None, role: str | None = None):
        with self.conn(db, role) as c:
            cur = c.cursor()
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def assert_disposable(self, db: str) -> None:
        """Run before every destructive step. Any mismatch aborts the rehearsal."""
        if not db.startswith("rehearsal_"):
            raise SystemExit(f"GUARD: database {db!r} is not a rehearsal database")
        datadir = self.q(db, "SHOW data_directory")[0][0]
        port = int(self.q(db, "SHOW port")[0][0])
        token = self.q(db, "SELECT token FROM public.rehearsal_marker")[0][0]
        on_disk = (self.workdir / "REHEARSAL_DISPOSABLE_MARKER").read_text()
        same_dir = Path(datadir).resolve() == self.pgdata.resolve()
        if not (same_dir and port == self.port and token == on_disk == self.token):
            raise SystemExit(f"GUARD FAILED for {db}: datadir={datadir} port={port} "
                             f"token_match={token == self.token}")

    def new_db(self, name: str) -> str:
        assert name.startswith("rehearsal_")
        with self.conn("postgres") as c:
            c.cursor().execute(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE}" OWNER "{OWNER}"')
        self.assert_disposable(name)
        return name

    # -- alembic --------------------------------------------------------------
    def alembic(self, db: str, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        env.update({
            "DATABASE_URL": f"postgresql+asyncpg://postgres@127.0.0.1:{self.port}/{db}",
            "DB_MIGRATION_ROLE": OWNER, "DB_APP_ROLE": APP,
            "SECRET_KEY": secrets.token_urlsafe(64), "ALLOWED_HOSTS": "*",
        })
        r = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
                           cwd=self.backend, env=env, capture_output=True, text=True, timeout=900)
        log(f"alembic {' '.join(args)} on {db} -> exit {r.returncode}")
        return r

    # -- inspection -----------------------------------------------------------
    def state(self, db: str) -> dict:
        def one(sql):
            return self.q(db, sql)[0][0]
        s = {
            "alembic_version": [r[0] for r in self.q(db, "SELECT version_num FROM public.alembic_version")],
            CK_EXEC: one(f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='{CK_EXEC}'"),
            CK_STAGE: one(f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='{CK_STAGE}'"),
            "rce_recheck_job_exists": one("SELECT to_regclass('public.rce_recheck_job') IS NOT NULL"),
            "rce_recheck_item_exists": one("SELECT to_regclass('public.rce_recheck_item') IS NOT NULL"),
            "finding_total": one("SELECT count(*) FROM public.rce_preflight_finding"),
            "finding_held": one("SELECT count(*) FROM public.rce_preflight_finding WHERE execution='held'"),
            "stage_total": one("SELECT count(*) FROM public.rce_delivery_stage_events"),
            "stage_preflight": one("SELECT count(*) FROM public.rce_delivery_stage_events WHERE stage='PREFLIGHT'"),
        }
        if s["rce_recheck_job_exists"]:
            s["recheck_jobs"] = one("SELECT count(*) FROM public.rce_recheck_job")
            s["recheck_items"] = one("SELECT count(*) FROM public.rce_recheck_item")
        return s

    def detect(self, db: str) -> dict:
        return {k: int(v) for k, v in self.q(db, DETECT_SQL)}


def err_summary(r: subprocess.CompletedProcess) -> dict:
    text = (r.stdout or "") + (r.stderr or "")
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    classes = sorted(set(re.findall(r"\b([A-Za-z_.]*(?:Error|Violation)[A-Za-z]*)\b", text)))
    msg = next((ln for ln in reversed(lines)
                if "Error" in ln or "violat" in ln.lower() or "refused" in ln.lower()), "")
    detail = next((ln for ln in reversed(lines)
                   if "is violated by some row" in ln or "downgrade refused" in ln), "")
    return {"exit": r.returncode, "error_classes": classes[:8], "final_line": msg[:400],
            "key_message": detail[:400]}


# ── synthetic seed ───────────────────────────────────────────────────────────
def seed(cl: Cluster, db: str, *, held: bool, preflight: bool, recheck: bool) -> dict:
    """Synthetic parents + two CONTROL rows (old values) + the requested blockers.

    Written as the RUNTIME role: these are values the application role is
    permitted to write, so the seed also proves the grant shape."""
    cl.assert_disposable(db)
    ids = {k: str(uuid.uuid4()) for k in
           ("intake", "record", "job", "run", "finding_done", "finding_held",
            "stage_parsing", "stage_preflight", "recheck_job", "recheck_item")}
    sha = lambda ch: ch * 64  # noqa: E731
    with cl.conn(db, APP) as c:
        cur = c.cursor()
        cur.execute(
            "INSERT INTO public.rce_source_intakes (id, original_filename, storage_path, sha256, "
            "file_size_bytes, headers, schema_fingerprint, record_count, received_by, status) "
            "VALUES (%s, 'synthetic-rehearsal.psv', '(synthetic)', %s, 10, '[\"id\"]'::jsonb, %s, 1, %s, 'PROCESSED')",
            (ids["intake"], secrets.token_hex(32), sha("f"), TAG))
        cur.execute(
            "INSERT INTO public.rce_source_records (id, source_intake_id, line_number, raw_line, "
            "record_sha256, field_count, parse_status, promotion_status) "
            "VALUES (%s, %s, 2, '9.99.999.9.2', %s, 1, 'ok', 'pending')",
            (ids["record"], ids["intake"], secrets.token_hex(32)))
        cur.execute(
            "INSERT INTO public.rce_delivery_jobs (id, identity, original_filename, storage_path, sha256, "
            "file_size_bytes, registered_by, state, stage) "
            "VALUES (%s, %s, 'synthetic-rehearsal.psv', '(synthetic)', %s, 10, %s, 'QUEUED', 'ACCEPTED')",
            (ids["job"], secrets.token_hex(32), secrets.token_hex(32), TAG))
        cur.execute(
            "INSERT INTO public.rce_preflight_run (id, source_intake_id, field_map_version, rule_set_version, "
            "preflight_version, status, classification_gate, records_evaluated, actor, correlation_id) "
            "VALUES (%s, %s, 'v1', 'v1', 'v1', 'COMPLETE', 'CLEAR', 1, %s, 'rehearsal')",
            (ids["run"], ids["intake"], TAG))
        # CONTROL rows: old, always-allowed values. Must survive everything unchanged.
        cur.execute(
            "INSERT INTO public.rce_preflight_finding (id, run_id, source_record_id, line_number, sequence, "
            "category, code, applicability, execution, disposition, description) "
            "VALUES (%s, %s, %s, 2, 1, 'IDENTIFIER', 'REHEARSAL-CONTROL', 'applies', 'done', 'open', %s)",
            (ids["finding_done"], ids["run"], ids["record"], TAG + " control finding"))
        cur.execute(
            "INSERT INTO public.rce_delivery_stage_events (id, job_id, stage, status, started_at, correlation_id) "
            "VALUES (%s, %s, 'PARSING', 'COMPLETED', now(), 'rehearsal-control')",
            (ids["stage_parsing"], ids["job"]))
        if held:
            cur.execute(
                "INSERT INTO public.rce_preflight_finding (id, run_id, source_record_id, line_number, sequence, "
                "category, code, applicability, execution, disposition, description) "
                "VALUES (%s, %s, %s, 2, 2, 'IDENTIFIER', 'REHEARSAL-HELD', 'applies', 'held', 'open', %s)",
                (ids["finding_held"], ids["run"], ids["record"], TAG + " held finding"))
        if preflight:
            cur.execute(
                "INSERT INTO public.rce_delivery_stage_events (id, job_id, stage, status, started_at, correlation_id) "
                "VALUES (%s, %s, 'PREFLIGHT', 'COMPLETED', now(), 'rehearsal-blocker')",
                (ids["stage_preflight"], ids["job"]))
        if recheck:
            cur.execute(
                "INSERT INTO public.rce_recheck_job (id, intake_id, trigger_kind, source_id, trigger_ref, "
                "idempotency_key, requested_by, rationale, baseline_hash) "
                "VALUES (%s, %s, 'SOURCE_RECOVERY', 'sam_gov', 'rehearsal-ref', %s, %s, %s, %s)",
                (ids["recheck_job"], ids["intake"], secrets.token_hex(32), TAG, TAG + " rationale", sha("b")))
            cur.execute(
                "INSERT INTO public.rce_recheck_item (id, job_id, entity_id, entity_ref) "
                "VALUES (%s, %s, %s, 'rehearsal-entity')",
                (ids["recheck_item"], ids["recheck_job"], str(uuid.uuid4())))
    return ids


def controls_fingerprint(cl: Cluster, db: str, ids: dict) -> str:
    return cl.q(db,
                "SELECT md5(coalesce((SELECT f::text FROM public.rce_preflight_finding f WHERE id=%s),'MISSING') || "
                "coalesce((SELECT e::text FROM public.rce_delivery_stage_events e WHERE id=%s),'MISSING') || "
                "coalesce((SELECT i::text FROM public.rce_source_intakes i WHERE id=%s),'MISSING') || "
                "coalesce((SELECT r::text FROM public.rce_source_records r WHERE id=%s),'MISSING') || "
                "coalesce((SELECT j::text FROM public.rce_delivery_jobs j WHERE id=%s),'MISSING'))",
                (ids["finding_done"], ids["stage_parsing"], ids["intake"], ids["record"], ids["job"]))[0][0]


# ── exercises ────────────────────────────────────────────────────────────────
def build_template(cl: Cluster) -> dict:
    with cl.conn("postgres") as c:
        c.cursor().execute(f'CREATE DATABASE "{TEMPLATE}" OWNER "{OWNER}"')
    with cl.conn(TEMPLATE) as c:
        cur = c.cursor()
        cur.execute(f'ALTER SCHEMA public OWNER TO "{OWNER}"')
        cur.execute(f'GRANT ALL ON SCHEMA public TO "{OWNER}"')
        cur.execute(f'GRANT USAGE, CREATE ON SCHEMA public TO "{APP}"')
        cur.execute("CREATE TABLE public.rehearsal_marker (token text NOT NULL)")
        cur.execute("INSERT INTO public.rehearsal_marker VALUES (%s)", (cl.token,))
        cur.execute(f'GRANT SELECT ON public.rehearsal_marker TO "{OWNER}", "{APP}"')
    cl.assert_disposable(TEMPLATE)
    r = cl.alembic(TEMPLATE, "upgrade", PREV)
    if r.returncode:
        raise SystemExit("base upgrade failed:\n" + (r.stdout + r.stderr)[-3000:])
    ref = {CK_EXEC: cl.state_ck(TEMPLATE, CK_EXEC), CK_STAGE: cl.state_ck(TEMPLATE, CK_STAGE)}
    r = cl.alembic(TEMPLATE, "upgrade", "head")
    if r.returncode:
        raise SystemExit("head upgrade failed:\n" + (r.stdout + r.stderr)[-3000:])
    st = cl.state(TEMPLATE)
    record("0. full chain applied to disposable template", "PASS" if st["alembic_version"] == [HEAD] else "FAIL",
           alembic_version=st["alembic_version"], reference_defs_at_prev=ref,
           defs_at_head={CK_EXEC: st[CK_EXEC], CK_STAGE: st[CK_STAGE]})
    return ref


def _state_ck(self, db, name):
    return self.q(db, f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='{name}'")[0][0]


Cluster.state_ck = _state_ck


def inventory_findings(cl: Cluster, db: str) -> dict:
    tables = ("rce_preflight_finding", "rce_delivery_stage_events", "rce_recheck_job", "rce_recheck_item")
    out = {"triggers_on_four_tables": cl.q(db,
        "SELECT c.relname, t.tgname, p.proname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_proc p ON p.oid=t.tgfoid WHERE NOT t.tgisinternal AND c.relname = ANY(%s)", (list(tables),)),
        "all_user_triggers": cl.q(db,
        "SELECT c.relname, t.tgname, p.proname, pg_get_triggerdef(t.oid) FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid "
        "WHERE NOT t.tgisinternal ORDER BY 1,2"),
        "event_triggers": cl.q(db, "SELECT evtname, evtevent FROM pg_event_trigger"),
        "rules": cl.q(db, "SELECT tablename, rulename FROM pg_rules WHERE schemaname='public' AND tablename = ANY(%s)",
                      (list(tables),)),
        "owners": cl.q(db, "SELECT tablename, tableowner FROM pg_tables WHERE schemaname='public' AND tablename = ANY(%s) ORDER BY 1",
                       (list(tables),)),
        "runtime_privileges": cl.q(db,
        "SELECT t, p, has_table_privilege(%s, 'public.'||t, p) FROM unnest(%s::text[]) t, "
        "unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) p ORDER BY 1,2", (APP, list(tables))),
        "inbound_foreign_keys": cl.q(db,
        "SELECT conrelid::regclass::text, conname, confrelid::regclass::text, pg_get_constraintdef(oid) "
        "FROM pg_constraint WHERE contype='f' AND confrelid = ANY(%s::regclass[])",
        (["public." + t for t in tables],)),
        "outbound_foreign_keys": cl.q(db,
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
        "FROM pg_constraint WHERE contype='f' AND conrelid = ANY(%s::regclass[]) ORDER BY 1,2",
        (["public." + t for t in tables],)),
        "check_constraints": cl.q(db,
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE contype='c' AND conrelid = ANY(%s::regclass[]) ORDER BY 1,2", (["public." + t for t in tables],)),
        "indexes": cl.q(db, "SELECT tablename, indexname FROM pg_indexes WHERE schemaname='public' AND tablename = ANY(%s) ORDER BY 1,2",
                        (list(tables),)),
    }
    return out


def runtime_delete_denied(cl: Cluster, db: str, ids: dict) -> dict:
    out = {}
    for table, key in (("rce_preflight_finding", "finding_held"), ("rce_delivery_stage_events", "stage_preflight"),
                       ("rce_recheck_item", "recheck_item"), ("rce_recheck_job", "recheck_job")):
        try:
            cl.q(db, f"DELETE FROM public.{table} WHERE id = %s", (ids[key],), role=APP)
            out[table] = "DELETE SUCCEEDED (unexpected)"
        except psycopg2.Error as e:
            out[table] = f"{type(e).__name__}: {str(e).strip().splitlines()[0]}"
    try:
        cl.q(db, "UPDATE public.rce_preflight_finding SET description = description WHERE id = %s",
             (ids["finding_held"],), role=APP)
        out["rce_preflight_finding UPDATE"] = "UPDATE SUCCEEDED (unexpected)"
    except psycopg2.Error as e:
        out["rce_preflight_finding UPDATE"] = f"{type(e).__name__}: {str(e).strip().splitlines()[0]}"
    return out


def refusal_scenarios(cl: Cluster) -> None:
    # S1: recheck job present -> named refusal on the first step down.
    db = cl.new_db("rehearsal_s1_recheck")
    seed(cl, db, held=False, preflight=False, recheck=True)
    before = cl.state(db)
    r = cl.alembic(db, "downgrade", R_STAGE)
    after = cl.state(db)
    record("S1. recheck downgrade refused while a job exists",
           "PASS" if r.returncode != 0 and after == before and "downgrade refused" in (r.stdout + r.stderr) else "FAIL",
           error=err_summary(r), state_unchanged=after == before, state_after=after)

    # S2: only a PREFLIGHT stage event -> step 1 succeeds, step 2 raw CheckViolation.
    db = cl.new_db("rehearsal_s2_stage")
    seed(cl, db, held=False, preflight=True, recheck=False)
    r1 = cl.alembic(db, "downgrade", R_STAGE)
    mid = cl.state(db)
    r2 = cl.alembic(db, "downgrade", R_HELD)
    after = cl.state(db)
    record("S2. stage CHECK downgrade fails raw while a 'PREFLIGHT' row exists",
           "PASS" if r1.returncode == 0 and r2.returncode != 0 and after == mid
           and after["alembic_version"] == [R_STAGE] and "'PREFLIGHT'" in after[CK_STAGE] else "FAIL",
           step1_exit=r1.returncode, error=err_summary(r2), state_unchanged_by_failure=after == mid, state_after=after)

    # S3: only a 'held' finding -> steps 1-2 succeed, step 3 raw CheckViolation.
    db = cl.new_db("rehearsal_s3_held")
    seed(cl, db, held=True, preflight=False, recheck=False)
    r1 = cl.alembic(db, "downgrade", R_STAGE)
    r2 = cl.alembic(db, "downgrade", R_HELD)
    mid = cl.state(db)
    r3 = cl.alembic(db, "downgrade", PREV)
    after = cl.state(db)
    record("S3. execution CHECK downgrade fails raw while a 'held' row exists",
           "PASS" if (r1.returncode, r2.returncode) == (0, 0) and r3.returncode != 0 and after == mid
           and after["alembic_version"] == [R_HELD] and "'held'" in after[CK_EXEC] else "FAIL",
           step_exits=[r1.returncode, r2.returncode], error=err_summary(r3),
           state_unchanged_by_failure=after == mid, state_after=after)


def transaction_scenarios(cl: Cluster) -> None:
    # T-A: ONE command spanning three revisions; the LAST one fails.
    db = cl.new_db("rehearsal_ta_single")
    seed(cl, db, held=True, preflight=False, recheck=False)
    before = cl.state(db)
    r = cl.alembic(db, "downgrade", PREV)
    after = cl.state(db)
    rolled_back_fully = after == before
    record("T-A. single downgrade command spanning 3 revisions, last step fails",
           "PASS" if r.returncode != 0 else "FAIL",
           observed=("ALL-OR-NOTHING: the two earlier, individually-successful revisions were rolled back too"
                     if rolled_back_fully else "PARTIAL: earlier revisions stayed applied"),
           error=err_summary(r), state_before=before, state_after=after)

    # T-B: three separate commands, one revision each; the third fails.
    db = cl.new_db("rehearsal_tb_separate")
    seed(cl, db, held=True, preflight=False, recheck=False)
    steps = []
    for target in (R_STAGE, R_HELD, PREV):
        r = cl.alembic(db, "downgrade", target)
        steps.append({"target": target, "exit": r.returncode, "state_after": cl.state(db),
                      **({"error": err_summary(r)} if r.returncode else {})})
    final = steps[-1]["state_after"]
    record("T-B. separate downgrade commands, one revision per invocation, third fails",
           "PASS" if [s["exit"] == 0 for s in steps] == [True, True, False] else "FAIL",
           observed=("each successful invocation COMMITTED; the failed one left the database at "
                     f"{final['alembic_version']} with recheck tables "
                     f"{'present' if final['rce_recheck_job_exists'] else 'dropped'}"),
           steps=steps)


def recovery_path(cl: Cluster, ref: dict, evidence: Path) -> None:
    db = cl.new_db("rehearsal_recovery")
    ids = seed(cl, db, held=True, preflight=True, recheck=True)
    fp_before = controls_fingerprint(cl, db, ids)

    inv = inventory_findings(cl, db)
    (evidence / "inventory_from_live_catalog.json").write_text(json.dumps(inv, indent=2, default=str))
    record("G1. triggers / rules on the four tables (live catalog, full chain applied)",
           "PASS" if not inv["triggers_on_four_tables"] and not inv["rules"] else "FAIL",
           triggers_on_four_tables=inv["triggers_on_four_tables"], rules=inv["rules"],
           event_triggers=inv["event_triggers"],
           all_user_triggers=[(a, b, c) for a, b, c, _ in inv["all_user_triggers"]],
           inbound_foreign_keys=inv["inbound_foreign_keys"])
    record("G2. owner and runtime privileges on the four tables", "PASS",
           owners=inv["owners"],
           runtime_privileges={f"{t}.{p}": v for t, p, v in inv["runtime_privileges"]})
    denied = runtime_delete_denied(cl, db, ids)
    record("G3. runtime role cannot DELETE blockers (and cannot UPDATE a finding)",
           "PASS" if all("InsufficientPrivilege" in v for v in denied.values()) else "FAIL", attempts=denied)

    # 1. detect
    d1 = cl.detect(db)
    record("R1. detect blockers", "PASS" if d1 == {"preflight_held": 1, "stage_preflight": 1,
                                                  "recheck_jobs": 1, "recheck_items": 1} else "FAIL", counts=d1)

    # 2. the downgrade is refused with everything present
    before = cl.state(db)
    r = cl.alembic(db, "downgrade", PREV)
    record("R2. downgrade refused with all blockers present",
           "PASS" if r.returncode != 0 and cl.state(db) == before else "FAIL", error=err_summary(r))

    # 2b. optional logical backup + restore INTO A SEPARATE DATABASE
    dump = evidence / "rehearsal_recovery_before_delete.dump"
    cl.assert_disposable(db)
    pd = subprocess.run([cl.exe("pg_dump"), "-h", "127.0.0.1", "-p", str(cl.port), "-U", "postgres",
                         "-Fc", "-f", str(dump), db], capture_output=True, text=True)
    pl = subprocess.run([cl.exe("pg_restore"), "--list", str(dump)], capture_output=True, text=True)
    with cl.conn("postgres") as c:
        c.cursor().execute('CREATE DATABASE "rehearsal_restore_check"')
    pr = subprocess.run([cl.exe("pg_restore"), "-h", "127.0.0.1", "-p", str(cl.port), "-U", "postgres",
                         "-d", "rehearsal_restore_check", "--exit-on-error", str(dump)],
                        capture_output=True, text=True)
    restored = cl.detect("rehearsal_restore_check") if pr.returncode == 0 else None
    record("R2b. pg_dump, then pg_restore into a SEPARATE database (never over the source)",
           "PASS" if pd.returncode == 0 and pr.returncode == 0 and restored == d1 else "FAIL",
           pg_dump_exit=pd.returncode, list_entries=len(pl.stdout.splitlines()),
           pg_restore_exit=pr.returncode, restored_blocker_counts=restored,
           restore_stderr_tail=pr.stderr[-300:])

    # 3. archive -- one explicit transaction, as the owner role, unique schema name
    schema = "recovery_archive_20261004_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dt%H%M%Sz")
    cl.assert_disposable(db)
    with cl.conn(db, OWNER) as c:
        c.autocommit = False
        cur = c.cursor()
        cur.execute(f'CREATE SCHEMA "{schema}"')            # no IF NOT EXISTS: never reuse an archive
        cur.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
        for arch, src, cond in ARCHIVE_SETS:
            cur.execute(f'CREATE TABLE "{schema}"."{arch}" AS SELECT * FROM public."{src}" WHERE {cond}')
        c.commit()
    try:
        cl.q(db, f'CREATE SCHEMA "{schema}"', role=OWNER)
        overwrite = "SECOND CREATE SUCCEEDED (unexpected)"
    except psycopg2.Error as e:
        overwrite = f"{type(e).__name__}: {str(e).strip().splitlines()[0]}"

    # 4. verify archive: counts + content in both directions
    recon = {}
    for arch, src, cond in ARCHIVE_SETS:
        s_n = cl.q(db, f'SELECT count(*) FROM public."{src}" WHERE {cond}')[0][0]
        a_n = cl.q(db, f'SELECT count(*) FROM "{schema}"."{arch}"')[0][0]
        s_minus_a = cl.q(db, f'SELECT count(*) FROM (SELECT * FROM public."{src}" WHERE {cond} '
                             f'EXCEPT SELECT * FROM "{schema}"."{arch}") d')[0][0]
        a_minus_s = cl.q(db, f'SELECT count(*) FROM (SELECT * FROM "{schema}"."{arch}" '
                             f'EXCEPT SELECT * FROM public."{src}" WHERE {cond}) d')[0][0]
        recon[arch] = {"source_rows": s_n, "archive_rows": a_n,
                       "in_source_not_archive": s_minus_a, "in_archive_not_source": a_minus_s}
    arch_fp = lambda: {a: cl.q(db, f'SELECT md5(coalesce(string_agg(t::text, \'|\' ORDER BY t::text), \'\')) '  # noqa: E731
                                   f'FROM "{schema}"."{a}" t')[0][0] for a, _, _ in ARCHIVE_SETS}
    arch_fp_before = arch_fp()
    app_can_use = cl.q(db, "SELECT has_schema_privilege(%s, %s, 'USAGE')", (APP, schema))[0][0]
    ok = all(v["source_rows"] == v["archive_rows"] == 1 and v["in_source_not_archive"] == 0
             and v["in_archive_not_source"] == 0 for v in recon.values())
    record("R3. archive created and reconciled (counts + EXCEPT both directions)",
           "PASS" if ok and "DuplicateSchema" in overwrite and not app_can_use else "FAIL",
           archive_schema=schema, reconciliation=recon, second_create_of_same_schema=overwrite,
           runtime_role_has_usage_on_archive=app_can_use)

    # 5. delete ONLY the seeded synthetic blockers, as the owner, one transaction
    cl.assert_disposable(db)
    deleted, stopped = {}, None
    with cl.conn(db, OWNER) as c:
        c.autocommit = False
        cur = c.cursor()
        plan = (("rce_recheck_item", "DELETE FROM public.rce_recheck_item WHERE id = %s AND job_id = %s",
                 (ids["recheck_item"], ids["recheck_job"])),
                ("rce_recheck_job", "DELETE FROM public.rce_recheck_job WHERE id = %s AND requested_by = %s",
                 (ids["recheck_job"], TAG)),
                ("rce_preflight_finding", "DELETE FROM public.rce_preflight_finding WHERE id = %s AND execution = 'held'",
                 (ids["finding_held"],)),
                ("rce_delivery_stage_events", "DELETE FROM public.rce_delivery_stage_events WHERE id = %s AND stage = 'PREFLIGHT'",
                 (ids["stage_preflight"],)))
        try:
            for table, sql, params in plan:
                cur.execute(sql, params)
                deleted[table] = cur.rowcount
                if cur.rowcount != 1:
                    raise RuntimeError(f"{table}: expected exactly 1 row, deleted {cur.rowcount}")
            c.commit()
        except Exception as e:  # noqa: BLE001 -- stop, roll back, report
            c.rollback()
            stopped = f"{type(e).__name__}: {e}"
    d2 = cl.detect(db)
    record("R4. delete the four seeded synthetic blockers (owner role, items before jobs)",
           "PASS" if stopped is None and all(v == 1 for v in deleted.values()) else "BLOCKED",
           deleted_rows=deleted, stopped=stopped)
    record("R5. detect returns zero", "PASS" if set(d2.values()) == {0} else "FAIL", counts=d2)
    if stopped or set(d2.values()) != {0}:
        return

    # 6. downgrade, single command
    cl.assert_disposable(db)
    r = cl.alembic(db, "downgrade", PREV)
    st = cl.state(db)
    arch_ok = arch_fp() == arch_fp_before
    fp_after = controls_fingerprint(cl, db, ids)
    record("R6. downgrade to 20261003_preflight_shadow after blockers removed",
           "PASS" if r.returncode == 0 and st["alembic_version"] == [PREV] and st[CK_EXEC] == ref[CK_EXEC]
           and st[CK_STAGE] == ref[CK_STAGE] and not st["rce_recheck_job_exists"]
           and not st["rce_recheck_item_exists"] else "FAIL",
           exit=r.returncode, alembic_version=st["alembic_version"],
           execution_check_equals_original=st[CK_EXEC] == ref[CK_EXEC],
           stage_check_equals_original=st[CK_STAGE] == ref[CK_STAGE],
           recheck_tables_present=[st["rce_recheck_job_exists"], st["rce_recheck_item_exists"]],
           defs={CK_EXEC: st[CK_EXEC], CK_STAGE: st[CK_STAGE]})
    record("R7. archives intact and unrelated seeded rows unchanged after downgrade",
           "PASS" if arch_ok and fp_after == fp_before and st["finding_total"] == 1 and st["stage_total"] == 1 else "FAIL",
           archive_fingerprints_unchanged=arch_ok, controls_fingerprint_unchanged=fp_after == fp_before,
           remaining_findings=st["finding_total"], remaining_stage_events=st["stage_total"])

    # 7. re-upgrade
    r = cl.alembic(db, "upgrade", "head")
    st = cl.state(db)
    privs = {f"{t}.{p}": v for t, p, v in cl.q(db,
             "SELECT t, p, has_table_privilege(%s, 'public.'||t, p) FROM unnest(ARRAY['rce_recheck_job','rce_recheck_item']) t, "
             "unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) p", (APP,))}
    record("R8. re-upgrade: the three migrations apply again",
           "PASS" if r.returncode == 0 and st["alembic_version"] == [HEAD] and "'held'" in st[CK_EXEC]
           and "'PREFLIGHT'" in st[CK_STAGE] and st["rce_recheck_job_exists"] and st.get("recheck_jobs") == 0
           and arch_fp() == arch_fp_before and controls_fingerprint(cl, db, ids) == fp_before else "FAIL",
           exit=r.returncode, alembic_version=st["alembic_version"],
           recheck_tables_empty=[st.get("recheck_jobs"), st.get("recheck_items")],
           runtime_privileges_recreated=privs, archives_still_intact=arch_fp() == arch_fp_before)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pg-bin", required=True, type=Path)
    ap.add_argument("--workdir", required=True, type=Path, help="NEW, empty directory for the disposable cluster")
    ap.add_argument("--port", required=True, type=int)
    ap.add_argument("--evidence", required=True, type=Path)
    ap.add_argument("--backend", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--leave-running", action="store_true",
                    help="leave the cluster up (and create rehearsal_code_rollback, fully seeded)")
    a = ap.parse_args()
    if os.environ.get(GUARD_ENV) != GUARD_VALUE:
        raise SystemExit(f"REFUSED: set {GUARD_ENV}={GUARD_VALUE}. This script creates and deletes data.")
    a.evidence.mkdir(parents=True, exist_ok=True)
    cl = Cluster(a.pg_bin, a.workdir.resolve(), a.port, a.backend.resolve())
    cl.provision()
    try:
        versions = {
            "postgres": cl.q("postgres", "SELECT version()")[0][0],
            "alembic": __import__("alembic").__version__,
            "sqlalchemy": __import__("sqlalchemy").__version__,
            "python": sys.version.split()[0],
            "backend_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=cl.backend,
                                          capture_output=True, text=True).stdout.strip(),
        }
        log("versions " + json.dumps(versions))
        ref = build_template(cl)
        refusal_scenarios(cl)
        transaction_scenarios(cl)
        recovery_path(cl, ref, a.evidence)
        if a.leave_running:
            db = cl.new_db("rehearsal_code_rollback")
            ids = seed(cl, db, held=True, preflight=True, recheck=True)
            (a.evidence / "code_rollback_seed_ids.json").write_text(json.dumps(ids, indent=2))
            log(f"left running for the code-rollback exercise: {db} on port {cl.port}")
    finally:
        (a.evidence / "rehearsal_results.json").write_text(
            json.dumps({"versions": locals().get("versions"), "results": RESULTS}, indent=2, default=str))
        (a.evidence / "rehearsal_log.txt").write_text("\n".join(LOG))
        if not a.leave_running:
            cl.stop()
    failed = [r for r in RESULTS if r["status"] not in ("PASS",)]
    log(f"{len(RESULTS)} exercises, {len(failed)} not PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
