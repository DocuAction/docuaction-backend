"""Production guards on the mock-data seeders.

Both seeders fabricate entities and label them with REAL QHIN names
('Health Gorilla', 'Epic Nexus', ...). On production those rows would sit in the
same tables as ONC-provided data while naming a genuine QHIN as their source.

The registry seeder (app/tefca_registry/routes.py) has refused production since
it was written. The ARC seeder (app/Tefca/routes.py) did NOT — its own comment
said "Run once per environment (dev + prod)", i.e. seeding production was the
documented intent. This file is the regression guard on closing that.

Why the existing controls were not sufficient:

  * The ADMIN_EMAILS allowlist is an AUTHORIZATION control. It stops the wrong
    person seeding; it does not stop the right person seeding the wrong
    environment.
  * The "skip if rows already exist" check only protects a NON-EMPTY table. An
    empty production table is exactly the case it does not cover.
  * is_mock_data=true marks the row, but the UI's MockDataBanner keys off the
    GLOBAL /api/tefca/status data_source rather than the per-row flag, so once
    real and mock rows coexist nothing on screen distinguishes them.

The environment check is asserted by inspection rather than by driving the
endpoint, because reaching the endpoint body requires an admin bearer token AND
a live database. A test that skipped without both would assert nothing about the
guard — and "0 tests, 0 failures" reads as a pass in a deploy gate.
"""
import ast
import os
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ARC_ROUTES = BACKEND / "app" / "Tefca" / "routes.py"
REGISTRY_ROUTES = BACKEND / "app" / "tefca_registry" / "routes.py"


def _function_source(path: Path, func_name: str) -> str:
    """Return the source of `func_name`, located structurally via AST so the test
    cannot drift onto a neighbouring function when the file is edited."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError("%s not found in %s" % (func_name, path.name))


PRODUCTION_CHECK = re.compile(
    r'os\.getenv\(\s*["\']ENVIRONMENT["\'].*?\)'      # reads ENVIRONMENT
    r'(?:.|\n)*?'
    r'==\s*["\']production["\']',                      # compares to production
    re.S)


def test_seed_mock_data_blocked_on_production():
    """The ARC mock-data seeder must refuse to run when ENVIRONMENT=production."""
    body = _function_source(ARC_ROUTES, "seed_mock_data")
    assert PRODUCTION_CHECK.search(body), (
        "seed_mock_data() has no ENVIRONMENT=production guard. It inserts 50 "
        "fabricated reviews carrying real QHIN names into tefca_reviews.")
    assert re.search(r"raise\s+HTTPException\(\s*\n?\s*403", body), (
        "seed_mock_data() detects production but does not raise 403")


def test_production_guard_precedes_any_write_in_seed_mock_data():
    """The guard must fire BEFORE the first ALTER/INSERT.

    A check placed after the additive ALTER TABLE statements would still mutate
    the production schema before refusing — the refusal has to come first to mean
    anything."""
    body = _function_source(ARC_ROUTES, "seed_mock_data")
    m = PRODUCTION_CHECK.search(body)
    assert m, "no production guard present"
    guard_at = m.start()
    for write in ("ALTER TABLE", "INSERT INTO", "_SEED_REVIEWS_SQL",
                  "_SEED_FINDINGS_SQL", "_SEED_LOGS_SQL"):
        at = body.find(write)
        if at != -1:
            assert guard_at < at, (
                "production guard appears AFTER %r — production would be mutated "
                "before the request is refused" % write)


def test_registry_seeder_still_blocked_on_production():
    """The pre-existing registry guard must not regress while the ARC one is added."""
    body = _function_source(REGISTRY_ROUTES, "seed_dev_registry")
    assert PRODUCTION_CHECK.search(body), (
        "seed_dev_registry() lost its ENVIRONMENT=production guard")


@pytest.mark.parametrize("path,func", [
    (ARC_ROUTES, "seed_mock_data"),
    (REGISTRY_ROUTES, "seed_dev_registry"),
])
def test_both_seeders_are_admin_gated(path, func):
    """Environment blocking replaces nothing — the authorization gate stays."""
    body = _function_source(path, func)
    assert 'require_role("admin")' in body or "ADMIN_EMAILS" in body, (
        "%s is no longer admin-gated" % func)


def test_os_is_importable_in_arc_routes():
    """The guard calls os.getenv; a missing import would raise NameError at request
    time — i.e. the endpoint would fail open in whichever environment hit it first."""
    src = ARC_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "os" in imported, "app/Tefca/routes.py uses os.getenv but never imports os"


def test_guard_matches_the_registry_wording_convention():
    """Both refusals must be 403 and name production, so an operator who hits one
    gets the same answer as the other."""
    for path, func in ((ARC_ROUTES, "seed_mock_data"),
                       (REGISTRY_ROUTES, "seed_dev_registry")):
        body = _function_source(path, func)
        # A function may raise 403 for more than one reason (seed_mock_data also
        # rejects a non-allowlisted admin), so collect every 403 and require that
        # at least one of them is the production refusal. Matching only the first
        # would assert against whichever guard happens to be written earliest.
        raises = re.findall(r"raise HTTPException\(\s*\n?\s*403,\s*((?:.|\n)*?)\)\n",
                            body)
        assert raises, "%s: no 403 refusal found" % func
        assert any("production" in r.lower() for r in raises), (
            "%s: no 403 refusal mentions production; found %d refusal(s)"
            % (func, len(raises)))


def test_seeded_rows_are_flagged_as_mock():
    """Defence in depth: even blocked from production, seeded rows must carry
    is_mock_data=true so any environment that DOES seed can identify them later."""
    src = ARC_ROUTES.read_text(encoding="utf-8")
    assert "is_mock_data" in src
    assert re.search(r"_SEED_REVIEWS_SQL\s*=\s*\"\"\"(?:.|\n)*?true(?:.|\n)*?\"\"\"", src), (
        "_SEED_REVIEWS_SQL no longer marks its rows is_mock_data=true")
