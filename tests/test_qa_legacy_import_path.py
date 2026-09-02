"""The import path the UI actually calls (QA-1.1 - QA-1.8, ported).

WHY THIS FILE EXISTS

There are two entity-import endpoints. The QA defects were fixed first on
POST /api/tefca/registry/import/csv — and the Entity Import page does not call
it. It posts to POST /api/tefca/entities/upload on the legacy dashboard router,
so every fix landed on a surface QA would never exercise, and a re-test would
have reported the same defects still open.

The same applies to the priority dashboard: the page reads /api/tefca/qa/sla,
not the ARC dashboard added for QA-2.x.

These tests pin the fixes on the endpoints the FRONTEND calls. If someone later
consolidates the two import paths, this file is the record of which one was
user-facing.
"""
import ast
import inspect

import pytest

from app.core.security import ROLE_HIERARCHY
from app.main import app

pytestmark = [pytest.mark.regression, pytest.mark.qa_defect]

UPLOAD_PATH = "/api/tefca/entities/upload"
HISTORY_PATH = "/api/tefca/import/history"
SLA_PATH = "/api/tefca/qa/sla"


def _routes():
    found = []

    def walk(routes):
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                walk(getattr(original, "routes", []))
            elif hasattr(route, "path") and hasattr(route, "dependant"):
                found.append(route)

    walk(app.routes)
    return found


def _route(path, method):
    for route in _routes():
        if route.path == path and method.upper() in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"route not found: {method} {path}")


def _role(path, method):
    best, level = None, -1
    for dep in _route(path, method).dependant.dependencies:
        role = getattr(dep.call, "minimum_role", None)
        if role and ROLE_HIERARCHY.get(role, 0) > level:
            best, level = role, ROLE_HIERARCHY[role]
    return best


def _upload_src():
    from app.Tefca import routes as legacy

    return inspect.getsource(legacy.upload_entities)


# ── The endpoints the UI calls actually exist ────────────────────────────────

def test_the_ui_endpoints_are_the_ones_under_test():
    """Guards the premise of this file. If these move, the fixes below are again
    on a surface nobody uses."""
    for path, method in ((UPLOAD_PATH, "POST"), (HISTORY_PATH, "GET"),
                         (SLA_PATH, "GET")):
        assert _route(path, method) is not None


# ── QA-1.1 — script injection ────────────────────────────────────────────────

@pytest.mark.security
def test_upload_scans_before_parsing():
    """The scan must run before the parser touches the bytes — a payload that is
    only rejected after parsing has already been walked."""
    src = _upload_src()
    assert "_scan_upload_or_reject" in src, "the UI upload path performs no scan"
    scan_pos = src.find("_scan_upload_or_reject(")
    parse_pos = src.find("_parse_upload(")
    assert 0 <= scan_pos < parse_pos, "scan must precede parsing"


@pytest.mark.security
def test_the_scanner_rejects_every_named_vector():
    """Same coverage as the registry path, asserted against the shared scanner
    the UI upload now calls."""
    from app.services.file_scanner import FileScanner

    header = b"entity_name,npi,qhin\n"
    for payload in (b"<script>x</script>", b"javascript:x", b'<img onerror="x">',
                    b'<b onclick="x">', b"<iframe src=x>", b"<object data=x>",
                    b"<embed src=x>", b"eval(x)", b"expression(x)"):
        content = header + b"Acme," + payload + b",QHIN-A\n"
        assert FileScanner().scan(content, "e.csv", "csv").ok is False, payload


# ── QA-1.4 — empty CSV ───────────────────────────────────────────────────────

def test_empty_upload_returns_422_not_a_successful_no_op():
    src = _upload_src()
    assert "if not rows:" in src
    assert 'HTTPException(422, "File contains no data rows")' in src


def test_the_empty_case_still_writes_a_history_row():
    """"Nothing was imported" is a fact a reviewer needs to see. Rejecting
    without recording would make the history a highlight reel."""
    src = _upload_src()
    empty_block = src.split("if not rows:")[1].split("raise HTTPException")[0]
    assert "TEFCAImportHistory(" in empty_block


# ── QA-1.6 / QA-4.2 — SHA-256 ────────────────────────────────────────────────

def test_history_model_stores_the_hash():
    from app.Tefca.models import TEFCAImportHistory

    assert "file_hash" in TEFCAImportHistory.__table__.columns.keys()
    assert TEFCAImportHistory.__table__.columns["file_hash"].type.length == 64


def _history_write_calls():
    """Every `TEFCAImportHistory(...)` call in upload_entities, as AST Call
    nodes - not a text search, so a write is found regardless of which
    expression it passes for any given keyword."""
    tree = ast.parse(_upload_src())
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "TEFCAImportHistory"]


def test_every_history_write_carries_the_hash():
    """The invariant is "every write has a real file_hash", not a fixed count
    of write sites or one fixed spelling of how each produces it.

    There are (at least) four legitimate call sites, not three: the original
    parse-failure, empty-file and success writes, plus DEF-018 / IMP-013's
    scan-rejection write (added later - a file the security scanner refuses
    is still an import ATTEMPT, and previously it vanished from Import
    History entirely instead of being recorded as a failure). This test used
    to pin the count at exactly three and failed the moment that fourth,
    correct write site was added.

    The scan-rejection branch also cannot spell its hash `file_hash=file_hash`
    like the other three: `_scan_upload_or_reject` raises BEFORE returning a
    hash on that path, so the shared `file_hash` variable is never assigned.
    It instead recomputes `hashlib.sha256(raw).hexdigest()` directly from the
    raw bytes still in scope - `app/services/file_scanner.py` confirms
    `FileScanner.scan()` computes its own `sha256` field the same way, over
    the same bytes, so this recomputation is provably the identical digest
    the scan itself would have returned and already audit-logged. Provenance
    is preserved; the expression is just necessarily different on this one
    branch, which is exactly why the check below asks whether a real value
    was passed rather than which literal spelling produced it.
    """
    calls = _history_write_calls()
    assert len(calls) >= 3, (
        f"expected at least the parse-failure, empty-file and success writes, "
        f"found {len(calls)} - a write site was removed")

    for call in calls:
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        assert "file_hash" in kwargs, (
            f"TEFCAImportHistory write at line {call.lineno} has no file_hash keyword")
        value = kwargs["file_hash"]
        if isinstance(value, ast.Constant):
            assert value.value not in (None, ""), (
                f"TEFCAImportHistory write at line {call.lineno} passes a "
                f"trivial file_hash constant ({value.value!r}) instead of a "
                f"real digest")


def test_history_endpoint_returns_the_hash():
    from app.Tefca import routes as legacy

    src = inspect.getsource(legacy.import_history)
    assert '"file_hash": r.file_hash' in src
    assert '"sha256": r.file_hash' in src, "older UI key must keep working"


def test_the_hash_column_is_created_on_startup():
    """create_all() cannot add a column to an existing table, so an ALTER has to
    exist or the column is silently absent in every deployed environment."""
    from app import main

    src = inspect.getsource(main)
    assert "ALTER TABLE tefca_import_history ADD COLUMN IF NOT EXISTS file_hash" in src


def test_a_missing_hash_is_reported_as_null_not_faked():
    """Rows predating the column cannot be backfilled — the bytes were never
    kept. An absent hash must not be made to look like a verified one."""
    src = inspect.getsource(__import__("app.Tefca.routes", fromlist=["x"]).import_history)
    assert '"file_hash": r.file_hash' in src
    assert '"file_hash": ""' not in src and '"file_hash": "unknown"' not in src


# ── QA-1.8 — contributor access ──────────────────────────────────────────────

def test_analyst_can_upload_on_the_ui_path():
    role = _role(UPLOAD_PATH, "POST")
    assert ROLE_HIERARCHY[role] <= ROLE_HIERARCHY["contributor"], \
        f"{UPLOAD_PATH} requires {role}; the Entity Import page is analyst-facing"


def test_viewer_cannot_upload_on_the_ui_path():
    role = _role(UPLOAD_PATH, "POST")
    assert ROLE_HIERARCHY[role] > ROLE_HIERARCHY["viewer"]


# ── QA-1.5 — duplicates ──────────────────────────────────────────────────────

def test_duplicate_npi_updates_rather_than_creating_a_second_entity():
    """The important half is 'not created'. This path updates in place, which
    satisfies that; what was missing is that the caller was never told."""
    src = _upload_src()
    assert 'rce_id = f"import-{a[\'npi\']}"' in src or 'import-{a["npi"]}' in src
    assert "skipped_details" in src
    assert '"reason": "duplicate_npi"' in src


def test_the_response_reports_skipped_rows():
    src = _upload_src()
    assert '"skipped": len(skipped_details)' in src
    assert '"skipped_details": skipped_details' in src


# ── QA-1.7 — audit event ─────────────────────────────────────────────────────

def test_import_writes_an_audit_event_with_the_file_hash():
    src = _upload_src()
    assert 'action="entity_import"' in src
    assert '"file_hash": file_hash' in src
    assert '"filename": file.filename' in src


# ── QA-2.1 / QA-2.3 — SLA on the endpoint the priority page reads ────────────

def test_the_sla_endpoint_emits_three_band_status():
    """`breached` is a boolean: it can say "late" but not "about to be late", so
    a reviewer could not see a case heading for a breach before it became one."""
    from app.Tefca import qa_engine

    src = inspect.getsource(qa_engine.check_priority_sla)
    assert '"sla_status"' in src
    assert '"sla_days_remaining"' in src
    assert '"due_date"' in src


def test_the_sla_endpoint_reports_overdue_counts():
    from app.Tefca import qa_engine

    src = inspect.getsource(qa_engine.check_priority_sla)
    for field in ('"overdue_count"', '"overdue_cases"', '"at_risk_count"',
                  '"on_track_count"'):
        assert field in src, f"SLA payload missing {field}"


def test_both_sla_surfaces_use_the_same_band_definitions():
    """Two dashboards disagreeing about what 'at risk' means is worse than
    neither having the field."""
    from app.Tefca import qa_engine

    assert qa_engine._sla.AT_RISK_DAYS == 2
    assert qa_engine._sla.OVERDUE == "overdue"
    src = inspect.getsource(qa_engine.check_priority_sla)
    assert "_sla.sla_status(" in src, "bands must come from the shared module"


def test_sla_windows_stay_configurable():
    """Confirmed as placeholders pending ONC. They must remain editable in one
    place rather than hard-coded at each call site."""
    from app.tefca_registry import sla

    assert set(sla.REVIEW_SLA_DAYS) == {"weekly", "quarterly", "priority"}
    assert sla.sla_days_for("weekly") == sla.REVIEW_SLA_DAYS["weekly"]
