"""CI/DevOps gate — security regressions. Runs on every deploy.

Each test here corresponds to a finding that reached a real environment. They
are cheap and dependency-free on purpose: a security gate that needs a database
skips in CI, and a skipped gate is an open one.
"""
import inspect

import pytest

from app.core.security import ROLE_HIERARCHY
from app.main import app
from app.services.file_scanner import FileScanner

pytestmark = [pytest.mark.regression, pytest.mark.security]

CSV_HEADER = "TEFCAID,HCID,EntityName,EntityLevel,NPI\n"


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


def _role_of(route):
    best, level = None, -1
    for dep in route.dependant.dependencies:
        role = getattr(dep.call, "minimum_role", None)
        if role and ROLE_HIERARCHY.get(role, 0) > level:
            best, level = role, ROLE_HIERARCHY[role]
    return best


class TestRegressionSecurity:
    """
    Security regression tests. Must pass on every deployment.
    """

    def test_null_byte_rejected(self):
        """F-002: Null bytes return 422.

        Postgres cannot store a NUL inside text, so an unescaped one reaches the
        driver and surfaces as a 500 — reporting bad input as a server fault and
        putting a stack trace where a validation message belongs.
        """
        from fastapi import HTTPException

        from app.core.input_sanitize import reject_null_bytes

        reject_null_bytes("clean value", "field")          # must not raise
        with pytest.raises(HTTPException) as exc:
            reject_null_bytes("bad\x00value", "search query")
        assert exc.value.status_code == 422

    def test_csv_error_sanitized(self):
        """F-001: No DB internals in error messages."""
        from app.tefca_registry.fhir_import import safe_import_error

        message = safe_import_error(
            "Row 3",
            Exception('duplicate key value violates unique constraint '
                      '"uq_sample_entity" DETAIL: Key (sample_id)=(x) exists.'),
            "csv parse")
        lowered = message.lower()
        for leak in ("violates unique constraint", "detail:", "uq_sample_entity",
                     "psycopg", "sqlalchemy", "traceback"):
            assert leak not in lowered, f"error message leaks {leak!r}: {message}"

    def test_script_injection_rejected(self):
        """QA-1.1: Malicious content rejected."""
        for payload in (b"<script>x</script>", b"javascript:x", b"<iframe src=x>",
                        b'<img onerror="x">', b"eval(x)", b"expression(x)",
                        b"<object data=x>", b"<embed src=x>", b'<b onclick="x">'):
            content = CSV_HEADER.encode() + b"T,H," + payload + b",participant,\n"
            assert FileScanner().scan(content, "e.csv", "csv").ok is False, payload

    def test_no_stack_trace_in_errors(self):
        """D-029: Error responses contain no stack traces.

        Asserted on the actual response body rather than on the source text.
        error_handler does call traceback.format_exc(), and that is correct — it
        goes to logger.error. What matters is that the only function which builds
        a client-facing body cannot carry one.
        """
        import json

        from app.core.error_handler import create_error_response

        response = create_error_response(
            500, "An internal error occurred. Please try again or contact support.",
            "INTERNAL_ERROR", request_id="req-1")
        body = json.loads(bytes(response.body).decode())

        assert set(body) == {"error", "code", "request_id"}, \
            f"error body shape drifted: {sorted(body)}"
        blob = json.dumps(body).lower()
        for leak in ("traceback", "file \"", "line ", ".py", "raise ",
                     "sqlalchemy", "asyncpg"):
            assert leak not in blob, f"error body leaks {leak!r}: {body}"

    def test_the_500_handler_sends_a_fixed_message_not_the_exception(self):
        """The 500 path must not pass str(exc) through to the caller."""
        from app.core import error_handler

        src = inspect.getsource(error_handler)
        five_hundred = src.split("status_code=500")[1][:400]
        assert "An internal error occurred" in five_hundred
        assert "str(e)" not in five_hundred, "the exception text reaches the client"

    def test_no_db_info_in_errors(self):
        """D-031: Error responses contain no DB info."""
        from app.tefca_registry.fhir_import import safe_import_error

        message = safe_import_error(
            "Entity X",
            Exception("connection to server at \"10.0.0.4\", port 5432 failed: "
                      "FATAL: password authentication failed for user \"pgadmin\""),
            "entity")
        for leak in ("5432", "pgadmin", "10.0.0.4", "password"):
            assert leak not in message, f"error message leaks {leak!r}: {message}"

    def test_guarded_endpoints_require_auth(self):
        """All guarded endpoints return 401 without a token.

        Asserted structurally: every route on the TEFCA registry and ARC routers
        must carry a require_role dependency. An endpoint added without one is
        anonymous, and that is invisible to a test that only probes known paths.
        """
        checked = 0
        for route in _routes():
            if not route.path.startswith(("/api/tefca/registry", "/api/tefca/arc")):
                continue
            checked += 1
            assert _role_of(route) is not None, f"{route.path} has no auth gate"
        assert checked >= 25, f"only {checked} routes checked — walker is broken"

    def test_viewer_cannot_write(self):
        """Viewer role blocked from all write endpoints.

        The registry floor was lowered to viewer for QA-1.8, which makes this the
        test that keeps that change honest: reads opened up, writes did not.
        """
        writes = 0
        for route in _routes():
            if not route.path.startswith(("/api/tefca/registry", "/api/tefca/arc")):
                continue
            methods = set(getattr(route, "methods", set()))
            if not methods & {"POST", "PUT", "PATCH", "DELETE"}:
                continue
            writes += 1
            role = _role_of(route)
            assert role is not None and \
                ROLE_HIERARCHY[role] > ROLE_HIERARCHY["viewer"], \
                f"{sorted(methods)} {route.path} is writable by a viewer"
        assert writes >= 5, f"only {writes} write routes found — walker is broken"

    def test_upload_scanner_returns_a_generic_rejection(self):
        """An attacker must not learn which check tripped from the response."""
        from app.api import routes as api_routes

        src = inspect.getsource(api_routes._scan_upload_or_reject)
        rejection = src.split("HTTPException(422")[1][:200]
        for leak in ("findings", "dangerous_content", "result.findings"):
            assert leak not in rejection, f"rejection message leaks {leak!r}"

    def test_scan_failures_are_audited_even_when_rejected(self):
        """A rejected upload is the one most worth having a record of."""
        from app.api import routes as api_routes

        src = inspect.getsource(api_routes._scan_upload_or_reject)
        audit_pos = src.find("log_audit_event")
        raise_pos = src.find("raise HTTPException")
        assert 0 <= audit_pos < raise_pos, \
            "the audit row must be written before the rejection is raised"
