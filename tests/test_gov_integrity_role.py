"""The Government-integrity gate reads under one explicit, reviewed role.

Reviewed read-only in Azure DEV on 2026-09-03: public.rce_source_records is a
plain table owned by docuaction_owner, ACL {docuaction_owner=arwdDxt,
docuaction_app=ar}, RLS off, FORCE RLS off, no policies, no views, no
BYPASSRLS/SUPERUSER on either role, pg_catalog functions only. So the digest
is identical whether computed as docuaction_app or docuaction_owner - and
docuaction_owner is the only role the dedicated release identity can assume
without a new grant or membership. These tests pin the ten properties the
release plan requires of that change.
"""
import io
import os
import re

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT = os.path.join(REPO, ".github", "scripts", "gov_integrity_snapshot.py")
VERIFY = os.path.join(REPO, ".github", "scripts", "gov_integrity_verify.py")
DEV_RELEASE = os.path.join(REPO, ".github", "workflows", "dev-release.yml")
MIGRATION = os.path.join(REPO, ".github", "workflows", "migration-preflight.yml")

pytestmark = pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")


def _src(p):
    return io.open(p, encoding="utf-8").read()


def _doc(p):
    return yaml.safe_load(_src(p))


def _code(p):
    """Source with the module docstring and comments removed."""
    s = _src(p)
    s = re.sub(r'^""".*?"""', "", s, count=1, flags=re.S)
    return "\n".join(l for l in s.splitlines() if not l.strip().startswith("#"))


def _sql(p):
    m = re.search(r'SNAPSHOT_SQL = """(.*?)"""', _src(p), re.S)
    assert m, f"no SNAPSHOT_SQL in {p}"
    return re.sub(r"\s+", " ", m.group(1)).strip()


# 1 + 2: both scripts use the intended, explicitly named integrity role
@pytest.mark.parametrize("path", [SNAPSHOT, VERIFY])
def test_integrity_scripts_assume_the_explicit_integrity_role(path):
    code = _code(path)
    assert 'os.environ.get("DB_INTEGRITY_ROLE"' in code
    assert 'cur.execute("set role " + role)' in code
    assert "set role docuaction_app" not in code, "must not hard-code the runtime role"
    assert "DB_APP_ROLE" not in code and "DB_MIGRATION_ROLE" not in code, (
        "the integrity role must not be coupled to the grant target or the DDL role")


# 3: snapshot and verify compute the digest with byte-identical SQL, same role source
def test_snapshot_and_verify_share_identical_visibility_semantics():
    assert _sql(SNAPSHOT) == _sql(VERIFY)
    for path in (SNAPSHOT, VERIFY):
        assert _sql(path).startswith("SELECT source_intake_id::text")
        assert "FROM rce_source_records" in _sql(path)
    assert _doc(MIGRATION)["env"]["DB_INTEGRITY_ROLE"] == _doc(DEV_RELEASE)["env"]["DB_INTEGRITY_ROLE"]


# 4: no Government record is written by either script
@pytest.mark.parametrize("path", [SNAPSHOT, VERIFY])
def test_integrity_scripts_never_write(path):
    code = _code(path).upper()
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE", "ALTER ", "DROP ", "CREATE "):
        assert verb not in code, f"{os.path.basename(path)} contains {verb.strip()}"


# 5 + 6 + 7: no runtime-privilege, migration-privilege, or membership change anywhere in the change
def test_no_grant_membership_or_role_changes_introduced():
    """Executable text only (script code, workflow `run:` blocks) - prose in
    docstrings/comments may mention grants; statements may not appear."""
    executable = {SNAPSHOT: _code(SNAPSHOT), VERIFY: _code(VERIFY),
                  DEV_RELEASE: _code_yaml_runs(DEV_RELEASE), MIGRATION: _code_yaml_runs(MIGRATION)}
    for path, text in executable.items():
        up = "\n".join(l for l in text.upper().splitlines()
                       if not l.strip().startswith("#") and not l.strip().startswith("ECHO"))
        for stmt in ("GRANT ", "REVOKE ", "ALTER ROLE", "CREATE ROLE", "ALTER DEFAULT PRIVILEGES", "ALTER TABLE"):
            assert stmt not in up, f"{os.path.basename(path)} executes {stmt.strip()}"
    assert _doc(MIGRATION)["env"]["DB_APP_ROLE"] == "docuaction_app"
    assert _doc(MIGRATION)["env"]["DB_MIGRATION_ROLE"] == "docuaction_owner"


def _code_yaml_runs(path):
    doc = _doc(path)
    return "\n".join(s.get("run") or "" for j in doc["jobs"].values() for s in j.get("steps", [])).upper()


# 8: no firewall-management authority in any DB job
def test_workflows_hold_no_firewall_authority():
    for path in (DEV_RELEASE, MIGRATION):
        assert "firewall-rule" not in _code_yaml_runs(path).lower()


# 9: unreachable DB is reported as readable=false, never a PASS
def test_unreachable_snapshot_is_not_a_pass():
    code = _code(SNAPSHOT)
    assert "except psycopg2.OperationalError" in code
    assert 'print("readable=false")' in code
    assert 'print("readable=true")' in code
    baseline = next(s for j in _doc(MIGRATION)["jobs"].values() for s in j.get("steps", []) if s.get("id") == "baseline")
    assert "readable == 'true'" in baseline["if"]
    assert "baseline_readable == 'true'" in _doc(DEV_RELEASE)["jobs"]["deploy"]["if"]


# 10: a digest mismatch (or a missing pre-existing delivery) still fails closed
def test_digest_mismatch_fails_closed():
    code = _code(VERIFY)
    assert "DIGEST CHANGED" in code and "PRESENT BEFORE, MISSING AFTER" in code
    assert re.search(r"if mutated:.*?sys\.exit\(1\)", code, re.S)
    assert "CERTIFICATION FAIL" in code
