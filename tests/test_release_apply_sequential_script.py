"""scripts/release/apply_migrations_sequentially.sh, run for real against a throwaway PostgreSQL database.

A template database is migrated to 20261003_iqvia_upload_durability (the last confirmed DEV revision) with
DEV's role split: docuaction_owner owns the schema, docuaction_app is the runtime role, and a dedicated
login role that can only SET ROLE docuaction_owner stands in for the OIDC migration identity. `az` is a
stub that returns that role's password as the "token". The script then applies the remaining revisions.

Proved:
  * every remaining revision is applied in order, the database revision is read back after each,
    and the sequence ends at the repository head
  * a wrong EXPECTED_CURRENT executes nothing
  * a failure part-way stops the sequence: later revisions are not attempted, the failed step's DDL is
    rolled back, and the database stays at the last verified revision
  * a database already at head is a no-op that reports applied=false

Needs a superuser DATABASE_URL, the docuaction_owner / docuaction_app roles and psql; skips otherwise.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "release", "apply_migrations_sequentially.sh")
BASH = shutil.which("bash")
OWNER, APP = "docuaction_owner", "docuaction_app"
IDENTITY = "seq_release_identity_test"
TEMPLATE = "seq_release_tpl_test"
START = "20261003_iqvia_upload_durability"
CHAIN = ["20261003_preflight_shadow", "20261004_preflight_exec_held",
         "20261004_stage_event_preflight", "20261004_recheck_jobs"]
HEAD = CHAIN[-1]


def _sync(url):
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _su(url, db, sql):
    u = urlsplit(_sync(url))
    env = dict(os.environ, PGPASSWORD=unquote(u.password or ""), PGCONNECT_TIMEOUT="15")
    r = subprocess.run(
        [shutil.which("psql"), "-h", u.hostname, "-p", str(u.port or 5432), "-U", unquote(u.username or "postgres"),
         "-d", db, "-X", "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", sql],
        env=env, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed on {db}: {r.stderr.strip()[-600:]}")
    return [line for line in r.stdout.splitlines() if line != ""]


def _db_url(url, db, driver="postgresql://"):
    base, _ = _sync(url).rsplit("/", 1)
    return base.replace("postgresql://", driver) + "/" + db


def _alembic_as_superuser(url, db, *args):
    env = dict(os.environ, DATABASE_URL=_db_url(url, db, "postgresql+asyncpg://"), DB_MIGRATION_ROLE=OWNER,
               DB_APP_ROLE=APP, SECRET_KEY=os.environ.get("SECRET_KEY", "t" * 64), ALLOWED_HOSTS="*")
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"alembic {' '.join(args)} failed:\n{r.stdout[-1200:]}\n{r.stderr[-2000:]}"


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    if not shutil.which("psql") or BASH is None:
        pytest.skip("psql and bash are required")
    url = os.environ["DATABASE_URL"]
    home = urlsplit(_sync(url)).path.lstrip("/") or "postgres"
    try:
        sup = _su(url, home, "select rolsuper from pg_roles where rolname = current_user")
        roles = _su(url, home, f"select rolname from pg_roles where rolname in ('{OWNER}', '{APP}')")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"No database reachable for the sequential-apply test: {exc}")
    if sup != ["t"] or sorted(roles) != sorted([OWNER, APP]):
        pytest.skip("needs a superuser DATABASE_URL and the docuaction_owner / docuaction_app roles")

    bindir = tmp_path_factory.mktemp("bin")
    az = bindir / "az"
    az.write_text('#!/usr/bin/env bash\n'
                  '[ "$1 $2" = "account get-access-token" ] || { echo "unexpected az $*" >&2; exit 9; }\n'
                  'echo "$STUB_TOKEN"\n', encoding="utf-8", newline="\n")
    az.chmod(0o755)

    created = []

    def drop(name):
        _su(url, home, f"select pg_terminate_backend(pid) from pg_stat_activity where datname='{name}' and pid<>pg_backend_pid()")
        _su(url, home, f'DROP DATABASE IF EXISTS "{name}"')

    def clone(name):
        drop(name)
        _su(url, home, f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE}" OWNER "{OWNER}"')
        created.append(name)
        return name

    drop(TEMPLATE)
    _su(url, home, f'DROP ROLE IF EXISTS "{IDENTITY}"')
    _su(url, home, f"CREATE ROLE \"{IDENTITY}\" LOGIN PASSWORD 'x' NOSUPERUSER")
    _su(url, home, f'GRANT "{OWNER}" TO "{IDENTITY}" WITH INHERIT TRUE, SET TRUE')
    _su(url, home, f'CREATE DATABASE "{TEMPLATE}" OWNER "{OWNER}"')
    created.append(TEMPLATE)
    _su(url, TEMPLATE, f'GRANT USAGE, CREATE ON SCHEMA public TO "{OWNER}", "{APP}"')
    _alembic_as_superuser(url, TEMPLATE, "upgrade", START)
    assert _su(url, TEMPLATE, "select version_num from alembic_version") == [START]

    def run(db, expected, tmp, **extra):
        u = urlsplit(_sync(url))
        out_file = tmp / "gh_output.txt"; out_file.write_text("")
        summ_file = tmp / "gh_summary.md"; summ_file.write_text("")
        env = dict(os.environ)
        env.update({
            "PATH": str(bindir) + os.pathsep + env["PATH"],
            "STUB_TOKEN": "x", "EXPECTED_CURRENT": expected,
            "PGHOST": u.hostname, "PGPORT": str(u.port or 5432), "PGDATABASE": db,
            "PG_PRINCIPAL": IDENTITY, "DB_MIGRATION_ROLE": OWNER, "DB_APP_ROLE": APP,
            "MIGRATION_SSL": "disable", "PYTHON": sys.executable,
            "GITHUB_OUTPUT": str(out_file), "GITHUB_STEP_SUMMARY": str(summ_file),
            "SECRET_KEY": os.environ.get("SECRET_KEY", "t" * 64), "ALLOWED_HOSTS": "*",
            "TMPDIR": str(tmp),
        })
        env.update(extra)
        r = subprocess.run([BASH, SCRIPT], cwd=REPO, env=env, capture_output=True, text=True, timeout=900)
        outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines() if "=" in line)
        return r.returncode, r.stdout + r.stderr, outputs

    def revision(db):
        return _su(url, db, "select version_num from alembic_version")

    try:
        yield {"url": url, "su": lambda db, sql: _su(url, db, sql), "clone": clone, "run": run, "revision": revision}
    finally:
        for name in reversed(created):
            drop(name)
        _su(url, home, f'DROP ROLE IF EXISTS "{IDENTITY}"')


def test_applies_every_remaining_revision_in_order_and_ends_at_head(cluster, tmp_path):
    db = cluster["clone"]("seq_release_ok_test")
    rc, out, outputs = cluster["run"](db, START, tmp_path)
    assert rc == 0, out[-3000:]
    for i, rev in enumerate(CHAIN, 1):
        assert f"[{i}/{len(CHAIN)}]" in out and rev in out
    positions = [out.index(f"-> {rev} ===") for rev in CHAIN]
    assert positions == sorted(positions), "revisions must run in chain order"
    assert cluster["revision"](db) == [HEAD]
    assert outputs["applied"] == "true" and outputs["dev_revision_after"] == HEAD and outputs["applied_count"] == "4"
    assert "Running upgrade" in out and "true no-op" in out
    # the schema really changed: a table from the last revision exists and the runtime role can use it
    assert cluster["su"](db, "select to_regclass('public.rce_recheck_job') is not null") == ["t"]
    assert cluster["su"](db, f"select has_table_privilege('{APP}','rce_recheck_job','UPDATE')") == ["t"]


def test_a_wrong_expected_revision_executes_nothing(cluster, tmp_path):
    db = cluster["clone"]("seq_release_wrong_test")
    rc, out, outputs = cluster["run"](db, "20261002_iqvia_observations", tmp_path)
    assert rc != 0 and "operator expected 20261002_iqvia_observations" in out
    assert cluster["revision"](db) == [START]
    assert "applied" not in outputs


def test_an_unknown_expected_revision_is_refused_before_connecting(cluster, tmp_path):
    db = cluster["clone"]("seq_release_unknown_test")
    rc, out, _ = cluster["run"](db, "not_a_revision", tmp_path)
    assert rc != 0 and "is not a revision in this repository" in out
    assert cluster["revision"](db) == [START]


def test_a_failure_stops_the_sequence_at_the_last_verified_revision(cluster, tmp_path):
    """Sabotage step 3 of 4 for real: drop the constraint that revision drops, so its DROP CONSTRAINT fails."""
    db = cluster["clone"]("seq_release_fail_test")
    cluster["su"](db, "ALTER TABLE rce_delivery_stage_events DROP CONSTRAINT ck_rce_stage_event_stage")
    rc, out, outputs = cluster["run"](db, START, tmp_path)
    assert rc != 0
    assert "[1/4]" in out and "[2/4]" in out and "[3/4]" in out
    assert "[4/4]" not in out, "nothing after the failing revision may be attempted"
    assert cluster["revision"](db) == ["20261004_preflight_exec_held"], \
        "the database must stay at the last revision that was verified"
    assert cluster["su"](db, "select to_regclass('public.rce_recheck_job') is not null") == ["f"]
    assert outputs.get("dev_revision_after") == "20261004_preflight_exec_held"
    assert "applied" not in outputs, "a failed sequence must not report success"


def test_already_at_head_is_a_reported_no_op(cluster, tmp_path):
    db = cluster["clone"]("seq_release_head_test")
    cluster["su"](db, "select 1")
    rc, out, outputs = cluster["run"](db, START, tmp_path)
    assert rc == 0, out[-2000:]
    rc, out, outputs = cluster["run"](db, HEAD, tmp_path)
    assert rc == 0 and "already the repository head" in out
    assert outputs == {"applied": "false"}
    assert cluster["revision"](db) == [HEAD]
