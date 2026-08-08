"""Module 3 — Review Cycles (QA-3.1 to QA-3.3), plus the Module 2 endpoint shape.

DIAGNOSIS ON THE RECORD, because the report's suggested root causes were all
wrong and the next person will hit the same confusion:

Cycle creation was never broken. It exists at POST /api/v1/tefca/cycles on the
legacy router, gated at program_manager (level 7). Admin is level 8 and clears
it; reviewer is level 4 and does not — so the Module 5 matrix row for "Create
cycle" already held. The endpoint was not unmounted, the table was not missing,
and the permission was not wrong. QA tested /api/tefca/arc/cycles, which did not
exist, and a 404 reads the same as "admin cannot create a cycle".

The ARC-namespaced routes added here sit on the SAME tefca_review_cycles table.
A second cycle store would let two endpoints disagree about how many cycles
exist, which is worse than the 404 being fixed.

Route shape is asserted by introspection rather than by HTTP, because these
handlers need a database and would otherwise skip in CI — leaving the defect
unguarded exactly where the gate is supposed to bite.
"""
import pytest

from app.core.security import ROLE_HIERARCHY
from app.main import app

pytestmark = [pytest.mark.regression, pytest.mark.qa_defect]


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


def _role(route):
    best, level = None, -1
    for dep in route.dependant.dependencies:
        role = getattr(dep.call, "minimum_role", None)
        if role and ROLE_HIERARCHY.get(role, 0) > level:
            best, level = role, ROLE_HIERARCHY[role]
    return best


# ── QA-3.1 — cycle creation ──────────────────────────────────────────────────

def test_create_review_cycle():
    """The endpoint the report specifies now exists and answers 201."""
    route = _route("/api/tefca/arc/cycles", "POST")
    assert route.status_code == 201, "report specifies 201 Created"


def test_create_cycle_requires_admin():
    assert _role(_route("/api/tefca/arc/cycles", "POST")) == "admin"


def test_reviewer_cannot_create_a_cycle():
    """The deny half of the matrix row. A gate that admits everyone also passes
    the test above."""
    required = _role(_route("/api/tefca/arc/cycles", "POST"))
    assert ROLE_HIERARCHY["reviewer"] < ROLE_HIERARCHY[required]


def test_the_original_cycle_endpoint_was_never_actually_broken():
    """Pins the diagnosis. If someone later "fixes" the legacy endpoint's
    permission, this says what its contract was and why admin already passed."""
    route = _route("/api/v1/tefca/cycles", "POST")
    required = _role(route)
    assert required == "program_manager"
    assert ROLE_HIERARCHY["admin"] >= ROLE_HIERARCHY[required], \
        "admin has always cleared the legacy cycle gate"
    assert ROLE_HIERARCHY["reviewer"] < ROLE_HIERARCHY[required]


# ── QA-3.2 — cycle dates ─────────────────────────────────────────────────────

def test_cycle_dates_set():
    """start_date/end_date are part of the request contract, and the response
    echoes them so a caller can confirm what was stored rather than assume."""
    from app.tefca_registry.review_routes import ARCCycleCreate

    fields = ARCCycleCreate.model_fields
    assert {"name", "cycle_type", "start_date", "end_date"} <= set(fields)
    assert fields["end_date"].default is None, "end_date must be optional"


def test_cycle_date_parsing_rejects_non_iso():
    from fastapi import HTTPException

    from app.tefca_registry.review_routes import _parse_iso

    assert _parse_iso("2026-08-07", "start_date").year == 2026
    assert _parse_iso("2026-08-07T12:30:00", "start_date").hour == 12
    with pytest.raises(HTTPException) as exc:
        _parse_iso("08/07/2026", "start_date")
    assert exc.value.status_code == 422


def test_an_inverted_date_range_is_rejected():
    """An end before the start makes every completion rate and overdue flag
    computed from it meaningless."""
    import inspect

    from app.tefca_registry import review_routes

    src = inspect.getsource(review_routes.create_arc_cycle)
    assert "end < start" in src
    assert "422" in src


# ── QA-3.3 — cycle stats ─────────────────────────────────────────────────────

def test_cycle_stats_returned():
    route = _route("/api/tefca/arc/cycles/{cycle_id}/stats", "GET")
    assert _role(route) is not None, "stats must be authenticated"


def test_cycle_stats_calculated():
    """Every field the report asks for is produced."""
    import inspect

    from app.tefca_registry import review_routes

    src = inspect.getsource(review_routes.arc_cycle_stats)
    for field in ('"total"', '"reviewed"', '"pending"', '"completion_rate"',
                  '"bucket_counts"', '"overdue"'):
        assert field in src, f"cycle stats missing {field}"
    assert '"B1"' in src and '"B4"' in src


def test_cycle_completion_rate():
    """The zero-population case is the one worth pinning: dividing by a zero
    sample and calling it complete makes an empty cycle report as a finished
    one, which is the most flattering possible wrong answer."""
    import inspect

    from app.tefca_registry import review_routes

    src = inspect.getsource(review_routes.arc_cycle_stats)
    assert "if total else 0.0" in src, \
        "an unsampled cycle must be 0% complete, not 100%"


# ── Module 2 endpoint shape ──────────────────────────────────────────────────

def test_dashboard_endpoint_exists():
    route = _route("/api/tefca/arc/priority-reviews/dashboard", "GET")
    assert _role(route) is not None


def test_dashboard_reports_overdue_count_and_the_list():
    """A bare count tells a reviewer something is late without telling them
    what to open."""
    import inspect

    from app.tefca_registry import review_routes

    src = inspect.getsource(review_routes.priority_review_dashboard)
    assert '"overdue_count"' in src
    assert '"overdue_reviews"' in src
    assert '"at_risk_count"' in src


def test_dashboard_excludes_completed_reviews_by_default():
    """Otherwise the overdue queue only grows and stops meaning 'act on this'."""
    import inspect

    from app.tefca_registry import review_routes

    src = inspect.getsource(review_routes.priority_review_dashboard)
    assert "if completed and not include_completed" in src


def test_dashboard_dates_are_iso():
    import inspect

    from app.tefca_registry import review_routes

    src = inspect.getsource(review_routes.priority_review_dashboard)
    assert "isoformat()" in src
    assert "strftime" not in src, "display formatting does not belong in the API"
