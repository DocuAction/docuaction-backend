"""CI/DevOps gate — QA defect report, August 2026.

Runs on every deploy. If any test here fails, the deploy is blocked.

These are deliberately THIN. Each one asserts the single property the defect was
about and delegates the detailed cases to the per-module files
(test_qa_entity_import.py, test_qa_priority_reviews.py, test_qa_review_cycles.py).
A gate that duplicates every assertion drifts from the suite it is meant to
summarise; a gate that names each defect once does not.
"""
import asyncio
import hashlib
from datetime import datetime, timedelta

import pytest

from app.core.security import ROLE_HIERARCHY
from app.main import app
from app.services.file_scanner import FileScanner
from app.tefca_registry import sla
from app.tefca_registry.csv_import import EmptyCSVError, _parse_row, import_csv

pytestmark = [pytest.mark.regression, pytest.mark.qa_defect]

HEADER = "TEFCAID,HCID,EntityName,EntityLevel,NPI\n"
VALID_NPI = "1234567893"


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


def _required_role(path, method):
    for route in _routes():
        if route.path == path and method.upper() in getattr(route, "methods", set()):
            best, level = None, -1
            for dep in route.dependant.dependencies:
                role = getattr(dep.call, "minimum_role", None)
                if role and ROLE_HIERARCHY.get(role, 0) > level:
                    best, level = role, ROLE_HIERARCHY[role]
            return best
    raise AssertionError(f"route not found: {method} {path}")


class TestRegressionQA:
    """
    Regression tests from QA defect report August 2026.
    These tests must pass on every deployment.
    They verify fixes for confirmed production defects.
    """

    # === ENTITY IMPORT ===

    def test_csv_script_injection_rejected(self):
        """QA-1.1: Malicious CSV must be rejected"""
        for payload in (b"<script>alert(1)</script>", b"javascript:void(0)",
                        b'<img onerror="x">', b"<iframe src=e>", b"eval(x)",
                        b"expression(x)", b"<object data=e>", b"<embed src=e>",
                        b'<b onclick="x">'):
            content = HEADER.encode() + b"T-1,H-1," + payload + b",participant,\n"
            assert FileScanner().scan(content, "e.csv", "csv").ok is False, payload

    def test_clean_csv_still_accepted(self):
        """The control. A scanner that rejects everything passes the test above
        while breaking every real import."""
        good = HEADER + f"T-1,H-1,Acme Health,participant,{VALID_NPI}\n"
        assert FileScanner().scan(good.encode(), "e.csv", "csv").ok is True

    def test_5_digit_npi_rejected(self):
        """QA-1.2: NPI must be exactly 10 digits"""
        with pytest.raises(ValueError):
            _parse_row({"TEFCAID": "T", "HCID": "H", "EntityName": "A",
                        "EntityLevel": "participant", "NPI": "12345"})

    def test_alpha_npi_rejected(self):
        """QA-1.3: NPI must be numeric only"""
        with pytest.raises(ValueError):
            _parse_row({"TEFCAID": "T", "HCID": "H", "EntityName": "A",
                        "EntityLevel": "participant", "NPI": "ABC1234567"})

    def test_empty_csv_rejected(self):
        """QA-1.4: Empty CSV returns 422"""
        with pytest.raises(EmptyCSVError):
            asyncio.run(import_csv(None, HEADER))

    def test_duplicate_npi_skipped(self):
        """QA-1.5: Duplicate NPI skipped not created"""
        from app.tefca_registry.fhir_import import _DUP_KEY_TYPES

        assert "npi" in _DUP_KEY_TYPES

    def test_import_history_has_sha256(self):
        """QA-1.6: Import records SHA-256 hash"""
        from app.tefca_registry import models as reg
        from app.tefca_registry.routes import _batch_summary

        assert "file_checksum" in reg.TefcaImportBatch.__table__.columns.keys()

        class Batch:
            id = "b1"; source_type = "csv"; filename = "e.csv"
            file_checksum = "a" * 64; file_size_bytes = 1; imported_by = "u1"
            status = "completed"; total_records = 1; imported_count = 1
            skipped_count = 0; error_count = 0; duration_ms = 1
            started_at = None; completed_at = None; created_at = None

        summary = _batch_summary(Batch())
        assert summary["file_checksum"] == "a" * 64
        assert len(summary["file_checksum"]) == 64
        assert summary["imported_by"] == "u1"

    def test_import_audit_logged(self):
        """QA-1.7: Import creates audit entry"""
        import inspect

        from app.tefca_registry import fhir_import

        src = inspect.getsource(fhir_import.persist_import)
        assert 'action="entity_import"' in src
        assert '"file_hash": file_checksum' in src

    def test_analyst_can_upload(self):
        """QA-1.8: Contributor role can import"""
        role = _required_role("/api/tefca/registry/import/csv", "POST")
        assert ROLE_HIERARCHY[role] <= ROLE_HIERARCHY["contributor"]

    def test_viewer_cannot_upload(self):
        """QA-1.8: Viewer role blocked from import"""
        role = _required_role("/api/tefca/registry/import/csv", "POST")
        assert ROLE_HIERARCHY[role] > ROLE_HIERARCHY["viewer"]

    # === PRIORITY REVIEWS ===

    def test_overdue_metrics_calculated(self):
        """QA-2.1: Dashboard shows overdue count"""
        import inspect

        from app.tefca_registry import review_routes

        src = inspect.getsource(review_routes.priority_review_dashboard)
        assert '"overdue_count"' in src and '"overdue_reviews"' in src

        now = datetime(2026, 8, 7, 12, 0, 0)
        due = sla.due_date_for(now - timedelta(days=10), "weekly")
        assert sla.is_overdue(due, now) is True

    def test_sla_status_assigned(self):
        """QA-2.3: SLA status on every review"""
        now = datetime(2026, 8, 7, 12, 0, 0)
        statuses = {
            sla.sla_status(now - timedelta(days=1), now),
            sla.sla_status(now + timedelta(days=1), now),
            sla.sla_status(now + timedelta(days=10), now),
        }
        assert statuses == {sla.OVERDUE, sla.AT_RISK, sla.ON_TRACK}

    def test_dates_iso_format(self):
        """QA-2.2: API dates are ISO 8601"""
        block = sla.describe(datetime(2026, 8, 1), "weekly",
                             now=datetime(2026, 8, 7))
        assert datetime.fromisoformat(block["due_date"]) == datetime(2026, 8, 8)

    # === REVIEW CYCLES ===

    def test_create_review_cycle(self):
        """QA-3.1: Admin can create cycle"""
        for route in _routes():
            if route.path == "/api/tefca/arc/cycles" and "POST" in route.methods:
                assert route.status_code == 201
                break
        else:
            raise AssertionError("POST /api/tefca/arc/cycles is not registered")
        assert _required_role("/api/tefca/arc/cycles", "POST") == "admin"

    def test_cycle_stats_returned(self):
        """QA-3.3: Cycle stats include completion rate"""
        import inspect

        from app.tefca_registry import review_routes

        src = inspect.getsource(review_routes.arc_cycle_stats)
        for field in ('"total"', '"reviewed"', '"pending"',
                      '"completion_rate"', '"bucket_counts"', '"overdue"'):
            assert field in src, f"missing {field}"

    # === AUDIT TRAIL ===

    def test_audit_shows_imports(self):
        """QA-4.1: Import events in audit trail"""
        from app.tefca_registry import audit as reg_audit

        assert reg_audit.ENTITY_IMPORT == "entity_import"

    def test_sha256_in_audit(self):
        """QA-4.2: SHA-256 hash in audit entries"""
        import inspect

        from app.api import routes as api_routes
        from app.tefca_registry import fhir_import

        assert '"file_hash": file_checksum' in \
            inspect.getsource(fhir_import.persist_import)
        # The upload scan writes its own audit row carrying the same hash.
        assert '"sha256": result.sha256' in \
            inspect.getsource(api_routes._scan_upload_or_reject)

    def test_sha256_matches_file(self):
        """QA-4.2: the stored hash is the hash of the uploaded bytes"""
        content = (HEADER + f"T-1,H-1,Acme,participant,{VALID_NPI}\n").encode()
        assert FileScanner().scan(content, "e.csv", "csv").sha256 == \
            hashlib.sha256(content).hexdigest()

    # === AUTHORIZATION ===

    def test_rbac_viewer_read_only(self):
        """QA-5.1: Viewer can only read"""
        for route in _routes():
            if not route.path.startswith("/api/tefca/registry"):
                continue
            methods = set(getattr(route, "methods", set()))
            if methods & {"POST", "PUT", "PATCH", "DELETE"}:
                role = _required_role(route.path, sorted(methods & {
                    "POST", "PUT", "PATCH", "DELETE"})[0])
                assert ROLE_HIERARCHY[role] > ROLE_HIERARCHY["viewer"], \
                    f"{route.path} is writable by a viewer"

    def test_rbac_analyst_can_import(self):
        """QA-5.1: Analyst can import + verify"""
        for path in ("/api/tefca/registry/import/csv",
                     "/api/tefca/registry/entities/{entity_id}/verify"):
            role = _required_role(path, "POST")
            assert ROLE_HIERARCHY[role] <= ROLE_HIERARCHY["contributor"], \
                f"{path} requires {role}; an analyst cannot reach it"

    def test_rbac_analyst_cannot_resolve(self):
        """QA-5.1: Analyst blocked from reviewer-level resolution"""
        role = _required_role("/api/tefca/registry/entities/{entity_id}/status",
                              "PATCH")
        assert ROLE_HIERARCHY[role] > ROLE_HIERARCHY["contributor"]

    def test_rbac_reviewer_can_resolve(self):
        """QA-5.1: Reviewer can resolve B3"""
        role = _required_role("/api/tefca/registry/entities/{entity_id}/status",
                              "PATCH")
        assert ROLE_HIERARCHY["reviewer"] >= ROLE_HIERARCHY[role]

    def test_rbac_admin_full_access(self):
        """QA-5.1: Admin clears every gate on the registry and ARC routers"""
        for route in _routes():
            if not route.path.startswith(("/api/tefca/registry", "/api/tefca/arc")):
                continue
            for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
                role = _required_role(route.path, method)
                if role is None:
                    continue
                assert ROLE_HIERARCHY["admin"] >= ROLE_HIERARCHY[role], \
                    f"admin cannot reach {method} {route.path}"

    def test_rbac_cycle_creation_is_admin_only(self):
        """QA-5.1: Create cycle — reviewer 403, admin 200"""
        role = _required_role("/api/tefca/arc/cycles", "POST")
        assert ROLE_HIERARCHY["reviewer"] < ROLE_HIERARCHY[role]
        assert ROLE_HIERARCHY["admin"] >= ROLE_HIERARCHY[role]

    def test_rbac_priority_review_is_admin_only(self):
        """QA-5.1: Priority review — admin only"""
        role = _required_role("/api/tefca/arc/priority-review", "POST")
        assert ROLE_HIERARCHY["reviewer"] < ROLE_HIERARCHY[role]

    # === MONDAY WORKFLOW ===

    def test_monday_workflow_end_to_end(self):
        """Full workflow: import → verify → classify → sample → report.

        Asserts the stages are all REACHABLE and correctly gated. The behavioural
        end-to-end lives in test_monday_workflow.py, which needs a database; this
        one guards the wiring so a missing route is caught in CI even when that
        suite skips.
        """
        stages = [
            ("/api/tefca/registry/import/csv", "POST"),
            ("/api/tefca/registry/entities/{entity_id}/verify", "POST"),
            ("/api/tefca/arc/samples", "POST"),
            ("/api/tefca/arc/reports/generate", "POST"),
        ]
        for path, method in stages:
            role = _required_role(path, method)
            assert role is not None, f"{method} {path} has no gate"
            assert ROLE_HIERARCHY["admin"] >= ROLE_HIERARCHY[role]
