"""Module 1 — Entity Import (Admin → Entity Import).

Covers QA defects 1.1-1.8. The import path is the only place operator-supplied
data enters the registry, so every one of these is a case where bad input was
accepted and became a database row.

Role assertions are made by introspecting the effective gate on each route
rather than by minting a token per role. The defect (QA-1.8) was a configuration
fault — a router-level dependency silently overriding every endpoint's own
declaration — and a test that only checks the deny direction for one role cannot
see that. Introspection also runs without a database, so it holds in CI.
"""
import asyncio
import io

import pytest

from app.core.security import ROLE_HIERARCHY
from app.main import app
from app.services.file_scanner import FileScanner
from app.tefca_registry.csv_import import EmptyCSVError, _parse_row, import_csv

pytestmark = [pytest.mark.regression, pytest.mark.qa_defect]

HEADER = "TEFCAID,HCID,EntityName,EntityLevel,NPI\n"
VALID_NPI = "1234567893"          # canonical CMS worked example


def _row(npi=VALID_NPI, tefcaid="T-1", name="Acme Health"):
    return f"{tefcaid},H-1,{name},participant,{npi}\n"


# ── QA-1.1 — CSV script injection ────────────────────────────────────────────

@pytest.mark.security
@pytest.mark.parametrize("payload", [
    b"<script>alert(1)</script>",
    b"javascript:alert(1)",
    b'<img src=x onerror="alert(1)">',
    b'<div onclick="steal()">',
    b"<iframe src=evil></iframe>",
    b"<object data=evil>",
    b"<embed src=evil>",
    b"eval(atob('ZXZpbA=='))",
    b"width: expression(alert(1))",
])
def test_csv_script_injection_rejected(payload):
    """Every vector named in QA-1.1 must fail the scan.

    A CSV is not rendered by this application, but an exported one is opened in
    a spreadsheet and its cells get pasted into pages that do render. Stopping
    it at ingest is the only point where one check covers every consumer.
    """
    content = HEADER.encode() + b"T-1,H-1," + payload + b",participant,\n"
    result = FileScanner().scan(content, "entities.csv", "csv")
    assert result.ok is False, f"scanner accepted {payload!r}"
    assert any("dangerous_content" in f for f in result.findings)


@pytest.mark.security
def test_a_clean_csv_still_passes_the_scan():
    """The counterpart that keeps the patterns honest. A scanner that rejects
    everything also passes every injection test."""
    result = FileScanner().scan((HEADER + _row()).encode(), "entities.csv", "csv")
    assert result.ok is True, result.findings


@pytest.mark.security
def test_rejection_message_names_no_specific_check():
    """The response must not tell an attacker which pattern tripped."""
    from app.api import routes as api_routes
    import inspect

    src = inspect.getsource(api_routes._scan_upload_or_reject)
    assert "File rejected: potentially malicious content" in src
    assert "dangerous_content" not in src.split("HTTPException(422")[-1]


# ── QA-1.2 / QA-1.3 — NPI validation ─────────────────────────────────────────

@pytest.mark.parametrize("npi", ["12345", "123456789", "12345678901"])
def test_5_digit_npi_rejected(npi):
    """QA-1.2 — an NPI is exactly 10 digits."""
    with pytest.raises(ValueError) as exc:
        _parse_row({"TEFCAID": "T-1", "HCID": "H-1", "EntityName": "Acme",
                    "EntityLevel": "participant", "NPI": npi})
    assert "NPI" in str(exc.value)


@pytest.mark.parametrize("npi", ["ABC1234567", "123456789O", "1234-56789"])
def test_alpha_npi_rejected(npi):
    """QA-1.3 — digits only. 'O' for '0' is the realistic version of this."""
    with pytest.raises(ValueError) as exc:
        _parse_row({"TEFCAID": "T-1", "HCID": "H-1", "EntityName": "Acme",
                    "EntityLevel": "participant", "NPI": npi})
    assert "NPI" in str(exc.value)


def test_valid_npi_accepted():
    parsed = _parse_row({"TEFCAID": "T-1", "HCID": "H-1", "EntityName": "Acme",
                         "EntityLevel": "participant", "NPI": VALID_NPI})
    assert any(t == "npi" and v == VALID_NPI for (t, v, _u, _p) in parsed.identifiers)


def test_absent_npi_is_not_an_error():
    """NPI is an optional column. Rejecting a blank cell would refuse every QHIN
    row, which does not carry one."""
    parsed = _parse_row({"TEFCAID": "T-1", "HCID": "H-1", "EntityName": "Acme",
                         "EntityLevel": "participant", "NPI": ""})
    assert all(t != "npi" for (t, _v, _u, _p) in parsed.identifiers)


def test_the_fhir_import_flagging_policy_is_untouched():
    """The CSV boundary rejects; validate_for_import still flags. Existing seed
    and RCE records carry bad check digits, and making that function reject
    would refuse data the rule postdates — a different decision from this one."""
    from app.tefca_registry.fhir_import import validate_for_import

    bad = validate_for_import("1234567890")
    assert bad["npi_valid"] is False
    assert bad["requires_review"] is True


# ── QA-1.4 — empty CSV ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "",
    HEADER,                                   # header only
    HEADER + "\n\n",                          # header + blank lines
    HEADER + ",,,,\n",                        # a row of empty cells
])
def test_empty_csv_rejected(text):
    """QA-1.4 — "0 imported, 0 errors" is indistinguishable from a successful
    no-op, which is how an empty upload used to look like a success."""
    with pytest.raises(EmptyCSVError):
        asyncio.run(import_csv(None, text))


def test_empty_csv_error_maps_to_422():
    import inspect
    from app.tefca_registry import routes

    src = inspect.getsource(routes.import_csv_route)
    assert "EmptyCSVError" in src
    assert "422" in src


# ── QA-1.5 — duplicate NPI ───────────────────────────────────────────────────

def test_duplicate_npi_is_a_dedup_key():
    """QA-1.5 — NPI must join TEFCAID/HCID as an identity key. Re-importing a
    roster under fresh TEFCAIDs is routine and used to duplicate providers."""
    from app.tefca_registry.fhir_import import _DUP_KEY_TYPES

    assert "npi" in _DUP_KEY_TYPES
    assert "tefcaid" in _DUP_KEY_TYPES and "hcid" in _DUP_KEY_TYPES


def test_dedup_keys_are_compared_per_type_not_pooled():
    """A pooled set would let an NPI collide with a TEFCAID that happens to be
    the same string, skipping a legitimate row as a duplicate."""
    import inspect
    from app.tefca_registry import fhir_import

    src = inspect.getsource(fhir_import.persist_import)
    assert "existing.get(t," in src, "duplicate check must be keyed by identifier type"


def test_skipped_rows_are_reported_structurally():
    """QA-1.5 asks for skipped count AND reasons. Prose in an errors list is not
    something a caller reconciling a roster can act on."""
    import inspect
    from app.tefca_registry import fhir_import

    src = inspect.getsource(fhir_import.persist_import)
    assert "skipped_details" in src
    assert '"reason": "duplicate_identifier"' in src


# ── QA-1.6 — import history metadata ─────────────────────────────────────────

def test_import_history_has_sha256():
    """The batch row always stored the checksum; the history LIST omitted it, so
    the page could not answer "which file" without opening each batch."""
    from app.tefca_registry.routes import _batch_summary

    class Batch:
        id = "b1"; source_type = "csv"; filename = "entities.csv"
        file_checksum = "a" * 64; file_size_bytes = 1234
        imported_by = "u1"; status = "completed"; total_records = 3
        imported_count = 3; skipped_count = 0; error_count = 0
        duration_ms = 12; started_at = None; completed_at = None; created_at = None

    summary = _batch_summary(Batch())
    assert summary["file_checksum"] == "a" * 64
    assert len(summary["file_checksum"]) == 64, "SHA-256 is 64 hex characters"
    assert summary["imported_by"] == "u1"
    assert summary["filename"] == "entities.csv"


def test_import_history_records_every_required_field():
    from app.tefca_registry import models as reg

    columns = set(reg.TefcaImportBatch.__table__.columns.keys())
    for required in ("filename", "file_checksum", "imported_by", "created_at",
                     "total_records", "imported_count", "skipped_count",
                     "error_count"):
        assert required in columns, f"import history is missing {required}"


def test_sha256_matches_file():
    """QA-4.2 — the stored hash must be the hash of the bytes that were uploaded,
    not of anything derived from them."""
    import hashlib

    content = (HEADER + _row()).encode()
    assert FileScanner().scan(content, "e.csv", "csv").sha256 == \
        hashlib.sha256(content).hexdigest()


# ── QA-1.7 — audit entry ─────────────────────────────────────────────────────

def test_import_audit_logged():
    import inspect
    from app.tefca_registry import fhir_import

    src = inspect.getsource(fhir_import.persist_import)
    assert 'action="entity_import"' in src
    for field in ('"filename": filename', '"file_hash": file_checksum',
                  '"imported": imported', '"skipped": skipped'):
        assert field in src, f"audit metadata missing {field}"


def test_entity_import_is_in_the_audit_vocabulary():
    from app.tefca_registry import audit as reg_audit

    assert reg_audit.ENTITY_IMPORT == "entity_import"


# ── QA-1.8 — authorization ───────────────────────────────────────────────────

def _all_routes():
    """Every APIRoute, flattened.

    This FastAPI version does not flatten include_router() into app.routes — it
    inserts an _IncludedRouter wrapper — so walking app.routes alone finds
    nothing under /api/tefca/registry and every assertion over it passes
    vacuously. Descend into original_router.
    """
    seen = []

    def walk(routes):
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                walk(getattr(original, "routes", []))
            elif hasattr(route, "path") and hasattr(route, "dependant"):
                seen.append(route)

    walk(app.routes)
    return seen


def _role_of(route) -> str:
    """Strictest require_role on a route. Its dependant already merges the
    router-level floor with the endpoint's own declaration, which is exactly the
    combination QA-1.8 got wrong."""
    best, best_level = None, -1
    for dep in route.dependant.dependencies:
        role = getattr(dep.call, "minimum_role", None)
        if role and ROLE_HIERARCHY.get(role, 0) > best_level:
            best, best_level = role, ROLE_HIERARCHY[role]
    return best


def _effective_role(path: str, method: str) -> str:
    for route in _all_routes():
        if route.path == path and method.upper() in getattr(route, "methods", set()):
            role = _role_of(route)
            assert role is not None, f"{method} {path} has no role gate"
            return role
    raise AssertionError(f"route not found: {method} {path}")


def test_the_route_walker_actually_finds_the_registry():
    """Guards the assertions below. If the walker returns nothing, every test
    that iterates it passes while checking nothing — which is how the first
    version of this file reported green on an unregistered router."""
    registry = [r for r in _all_routes()
                if r.path.startswith("/api/tefca/registry")]
    assert len(registry) >= 20, f"only found {len(registry)} registry routes"


def test_analyst_can_upload():
    """QA-1.8 — contributor ("analyst") must reach the import endpoints."""
    for path in ("/api/tefca/registry/import/csv",
                 "/api/tefca/registry/import/fhir-bundle"):
        role = _effective_role(path, "POST")
        assert ROLE_HIERARCHY[role] <= ROLE_HIERARCHY["contributor"], (
            f"{path} requires {role}; a contributor cannot import")


def test_viewer_cannot_upload():
    """The other half. An import gate that admits everyone also passes the test
    above, so the deny direction has to be asserted too."""
    for path in ("/api/tefca/registry/import/csv",
                 "/api/tefca/registry/import/fhir-bundle"):
        role = _effective_role(path, "POST")
        assert ROLE_HIERARCHY[role] > ROLE_HIERARCHY["viewer"], (
            f"{path} is reachable by a viewer")


def test_reads_are_reachable_by_a_viewer():
    """The router floor used to be reviewer, so a viewer got 403 on reads."""
    for path in ("/api/tefca/registry/entities", "/api/tefca/registry/stats",
                 "/api/tefca/registry/import/history"):
        assert _effective_role(path, "GET") == "viewer", f"{path} is not viewer-readable"


def test_no_registry_endpoint_is_unauthenticated():
    """Lowering the floor must not have removed it."""
    checked = 0
    for route in _all_routes():
        if not route.path.startswith("/api/tefca/registry"):
            continue
        checked += 1
        assert _role_of(route) is not None, f"{route.path} has no role gate at all"
    assert checked >= 20, f"only checked {checked} registry routes"


def test_writes_still_require_more_than_a_viewer():
    """The floor moved to viewer. Nothing that mutates may sit on that floor."""
    for route in _all_routes():
        if not route.path.startswith("/api/tefca/registry"):
            continue
        methods = set(getattr(route, "methods", set()))
        if not methods & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        role = _role_of(route)
        assert ROLE_HIERARCHY[role] > ROLE_HIERARCHY["viewer"], (
            f"{sorted(methods)} {route.path} is writable by a viewer")
