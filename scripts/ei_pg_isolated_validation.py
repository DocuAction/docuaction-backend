"""Ephemeral, credential-free local PostgreSQL cluster for the isolated EI schema.

    python scripts/ei_pg_isolated_validation.py        (PG_BIN=<postgres bin dir> to override)

ISOLATED VALIDATION ONLY. Requires local PostgreSQL binaries (initdb, pg_ctl).
initdb with trust auth in the OS temp directory, listening on 127.0.0.1 and a
non-default port only; no password exists; the cluster is stopped and deleted
at the end. It never touches the shared DEV/QA database or any other local
instance. Output: a JSON status block with POSTGRESQL_ISOLATED_VALIDATION.
"""
import json
import os
import shutil
import subprocess  # nosec B404 — fixed local binaries, no shell, argument lists only
import sys
import tempfile
import time

BIN = os.environ.get("PG_BIN", r"C:\Program Files\PostgreSQL\18\bin")   # local PostgreSQL binaries only
PORT = "54329"
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(tempfile.gettempdir(), "docuaction_ei_pg_ephemeral")
status = {"POSTGRESQL_ISOLATED_VALIDATION": "NOT_RUN",
          "method": "ephemeral initdb cluster, trust auth, 127.0.0.1:" + PORT}
QUIET = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # nosec B603


try:
    if not os.path.exists(os.path.join(BIN, "initdb.exe")) and not os.path.exists(os.path.join(BIN, "initdb")):
        status["reason"] = f"no PostgreSQL binaries at {BIN}"
        raise SystemExit
    if os.path.exists(DATA):
        shutil.rmtree(DATA, ignore_errors=True)
    r = run([os.path.join(BIN, "initdb"), "-D", DATA, "-U", "ei_test", "-A", "trust", "-E", "UTF8", "--no-locale"])
    status["initdb_rc"] = r.returncode
    if r.returncode != 0:
        status["initdb_err"] = (r.stderr or r.stdout)[-800:]
        raise SystemExit
    log = os.path.join(DATA, "server.log")
    # The postmaster inherits pg_ctl's stdio; a captured pipe would never close. Use DEVNULL.
    r = subprocess.run([os.path.join(BIN, "pg_ctl"), "-D", DATA, "-l", log, "-w", "-t", "60",  # nosec B603
                        "-o", f"-p {PORT} -c listen_addresses=127.0.0.1 -c unix_socket_directories=", "start"], **QUIET)
    status["pg_ctl_start_rc"] = r.returncode
    if r.returncode != 0:
        try:
            status["server_log"] = open(log, encoding="utf-8", errors="replace").read()[-1200:]
        except OSError:
            pass
        raise SystemExit
    url = f"postgresql://ei_test@127.0.0.1:{PORT}/postgres"
    env = dict(os.environ, SECRET_KEY="t" * 64,   # throw-away, in-process only; never a real key
               DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:5432/test")  # tests' placeholder; never connected
    # 1. refusal path: an Azure-looking URL must be refused even with a live local server available
    r = run([sys.executable, "-m", "app.core.entity_intelligence.migrations.apply", "--database-url",
             "postgresql://u@docuaction-db.postgres.database.azure.com/docuaction"], cwd=BACKEND, env=env)
    status["refusal_rc"] = r.returncode
    status["refusal_msg"] = (r.stdout or r.stderr).strip()[-200:]
    # 2. apply the isolated DDL
    r = run([sys.executable, "-m", "app.core.entity_intelligence.migrations.apply", "--database-url", url],
            cwd=BACKEND, env=env)
    status["apply_rc"] = r.returncode
    status["apply_out"] = (r.stdout + r.stderr).strip()[-600:]
    # 3. verify tables, round-trip through the ORM, check platform tables are absent
    import psycopg2
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute("select table_name from information_schema.tables where table_schema='public' order by 1")
    tables = [t[0] for t in cur.fetchall()]
    status["tables"] = tables
    status["platform_tables_present"] = [t for t in tables if not t.startswith("ei_")]
    cur.execute("select count(*) from information_schema.columns where table_name='ei_evidence_observations'")
    status["ei_evidence_observations_column_count"] = cur.fetchone()[0]
    snippet = ("from sqlalchemy import create_engine; from sqlalchemy.orm import Session; "
               "from app.core.entity_intelligence.models import EiSourceDelivery; "
               f"e = create_engine('{url}'); s = Session(e); "
               "s.add(EiSourceDelivery(source_id='NPPES_V2', source_owner='CMS NPPES', delivery_path='FILE_DOWNLOAD', "
               "file_sha256='0'*64, schema_fields=['NPI'])); s.commit(); print('orm insert ok')")
    r = run([sys.executable, "-c", snippet], cwd=BACKEND, env=env)
    status["orm_insert"] = (r.stdout + r.stderr).strip()[-300:]
    cur.execute("select count(*) from ei_source_deliveries")
    status["round_trip_rows"] = cur.fetchone()[0]
    r = run([sys.executable, "-m", "app.core.entity_intelligence.migrations.apply", "--database-url", url],
            cwd=BACKEND, env=env)
    status["reapply_rc"] = r.returncode
    cur.execute("select count(*) from ei_source_deliveries")
    status["rows_after_reapply"] = cur.fetchone()[0]
    cur.execute("show server_version")
    status["server_version"] = cur.fetchone()[0]
    conn.close()
    ok = (status["apply_rc"] == 0 and not status["platform_tables_present"]
          and len([t for t in tables if t.startswith("ei_")]) == 5 and status["refusal_rc"] == 2
          and status["round_trip_rows"] == 1)
    status["POSTGRESQL_ISOLATED_VALIDATION"] = "PASSED" if ok else "FAILED"
except SystemExit:
    status["POSTGRESQL_ISOLATED_VALIDATION"] = "BLOCKED_BY_LOCAL_ENVIRONMENT"
except Exception as exc:  # noqa: BLE001
    status["POSTGRESQL_ISOLATED_VALIDATION"] = "FAILED"
    status["error"] = repr(exc)[:800]
finally:
    if os.path.exists(DATA):
        try:
            subprocess.run([os.path.join(BIN, "pg_ctl"), "-D", DATA, "-m", "fast", "-w", "stop"], **QUIET)  # nosec B603
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(1)
        shutil.rmtree(DATA, ignore_errors=True)
    status["cluster_deleted"] = not os.path.exists(DATA)
print(json.dumps(status, indent=1, default=str))
