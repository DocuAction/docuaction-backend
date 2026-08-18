"""QA Round 2 regression tests — the 82 defects reported by five testers.

Every test here corresponds to a defect that was CONFIRMED by reading the code,
not to a defect that was merely reported. Where the fix was in the frontend, the
test asserts the backend contract the frontend now depends on — because that
contract is what a future change could break without anyone noticing.

WHAT THESE TESTS CAN AND CANNOT PROVE
-------------------------------------
No database is required for most of them. The suite runs against a machine with
no Postgres, so anything needing rows is marked `db_required` and SKIPS rather
than passing vacuously. A test that silently skips proves nothing, so the
route-level tests below assert the things that are true without data:

  * the route EXISTS (a 404 here means it was renamed or dropped)
  * it is AUTHENTICATED (an anonymous 200 would be the real defect)
  * its input validation rejects what it must reject

That is deliberate. The single most likely future regression is one of these
endpoints being removed or renamed while the frontend still calls it, and that
is exactly what a 404 assertion catches.

Marker: qa_round2
"""
import pytest

from app.core.security import ROLE_HIERARCHY, canonical_role, role_level

pytestmark = pytest.mark.qa_round2

# Bearer-auth failures return 401 (FastAPI 0.140+); a role failure returns 403.
GATED = (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE 1 — Mission Control panels had no endpoint of their own
# DEF-003 (Validation Queue) · DEF-004 (Recent Activity) · DEF-005 (Notifications)
# ─────────────────────────────────────────────────────────────────────────────

MISSION_CONTROL_ENDPOINTS = [
    "/api/tefca/dashboard/validation-queue",
    "/api/tefca/dashboard/recent-activity",
    "/api/tefca/dashboard/notifications",
]


@pytest.mark.parametrize("path", MISSION_CONTROL_ENDPOINTS)
def test_mission_control_panel_endpoints_exist(client, path):
    """Each panel is backed by a real route.

    This is the assertion that matters for DEF-003/4/5: the defect was that NO
    request produced these rows, so the browser was the only source of them. A
    404 here means we have regressed to exactly that state.
    """
    res = client.get(path)
    assert res.status_code != 404, (
        f"{path} does not exist. The Mission Control panel it backs would fall "
        f"back to browser-side data, which is the defect QA reported."
    )


@pytest.mark.parametrize("path", MISSION_CONTROL_ENDPOINTS)
def test_mission_control_panel_endpoints_require_authentication(client, path):
    """Operational queues and audit-derived activity are not public."""
    res = client.get(path)
    assert res.status_code in GATED, (
        f"{path} answered {res.status_code} without a token"
    )


def test_validation_queue_rejects_an_unknown_status_filter(client, auth_headers, db_required):
    """A filter that silently does nothing is worse than one that fails.

    The caller reads the UNFILTERED list as the filtered answer — which is the
    same class of defect as DEF-001 (stale filters) wearing a different hat.
    """
    if not auth_headers:
        pytest.skip("no authenticated test account available")
    res = client.get(
        "/api/tefca/dashboard/validation-queue?status=definitely_not_a_status",
        headers=auth_headers,
    )
    assert res.status_code == 400, (
        "an unrecognised status must be rejected, not ignored"
    )


def test_recent_activity_returns_real_timestamps_not_now(client, auth_headers, db_required):
    """DEF-004 — every entry read 'just now'.

    The cause was that the feed was assembled from records written at request
    time. Each entry now carries the timestamp of the audit row it came from, so
    the field must be present and must be an ISO string the client can format.
    """
    if not auth_headers:
        pytest.skip("no authenticated test account available")
    res = client.get("/api/tefca/dashboard/recent-activity?limit=5", headers=auth_headers)
    if res.status_code != 200:
        pytest.skip(f"activity endpoint unavailable in this environment ({res.status_code})")
    body = res.json()
    assert "activity" in body
    for entry in body["activity"]:
        assert "timestamp" in entry, "an activity entry with no timestamp cannot be aged"
        assert entry["timestamp"] is None or "T" in entry["timestamp"], (
            f"timestamp is not ISO-8601: {entry['timestamp']!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE 3 — filters narrowed a cached page instead of re-querying
# DEF-001 (Pending Reviews) · DEF-006 (Import History)
# ─────────────────────────────────────────────────────────────────────────────

def test_reviews_endpoint_accepts_a_status_filter(client):
    """DEF-001 — the frontend now sends ?status= on every filter change.

    If this parameter were dropped, the page would silently receive the full
    list and go back to filtering locally, which is the original defect.
    """
    from app.Tefca.routes import list_entity_reviews
    import inspect

    params = inspect.signature(list_entity_reviews).parameters
    assert "status" in params, "the reviews endpoint lost its status filter"
    assert "search" in params, "the reviews endpoint lost its search filter"


def test_import_history_accepts_a_status_filter(client):
    """DEF-006 — the same fix for the Import History table."""
    from app.Tefca.routes import import_history
    import inspect

    params = inspect.signature(import_history).parameters
    assert "status" in params, "import history lost its status filter"
    assert "search" in params, "import history lost its search filter"


def test_import_history_rejects_an_unknown_status(client, auth_headers, db_required):
    if not auth_headers:
        pytest.skip("no authenticated test account available")
    res = client.get("/api/tefca/import/history?status=bogus", headers=auth_headers)
    assert res.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE 4 — RBAC: a near-miss role spelling denied everything
# IMP-016 and the wider 403 cluster
# ─────────────────────────────────────────────────────────────────────────────

def test_tefca_router_floor_is_viewer_not_reviewer():
    """The router-level floor is a CEILING on every route beneath it.

    While it read "reviewer", every endpoint in the TEFCA router — including
    read-only GETs — was closed to viewer, contributor and manager. Those are
    the only non-admin roles the admin API could assign, so the module was
    admin-only in practice. Least privilege is enforced per-route; this floor
    exists only so no route can be anonymous.
    """
    from app.Tefca.routes import tefca_router

    floors = [
        d.dependency.minimum_role
        for d in tefca_router.dependencies
        if hasattr(getattr(d, "dependency", None), "minimum_role")
    ]
    assert floors, "the TEFCA router lost its role floor entirely — routes may be anonymous"
    assert floors == ["viewer"], (
        f"router floor is {floors}, not ['viewer'] — this closes read-only routes "
        f"to every non-admin role"
    )


@pytest.mark.parametrize("spelling,expected", [
    ("analyst", "contributor"),      # analyst@docuaction.io is a Level-2 Contributor
    ("Analyst", "contributor"),
    ("senioranalyst", "senior_analyst"),
    ("senior analyst", "senior_analyst"),
    ("qa lead", "qalead"),
    ("qa_lead", "qalead"),
    ("administrator", "admin"),
    ("program manager", "program_manager"),
    ("viewer", "viewer"),            # canonical names pass through untouched
    ("admin", "admin"),
])
def test_role_aliases_resolve_to_a_real_role(spelling, expected):
    """A stored role that is a near-miss spelling used to resolve to level 0 and
    be denied EVERY role-gated route, with a denial indistinguishable from a
    correct one. That is what refused a contributor their own entity import."""
    assert canonical_role(spelling) == expected
    assert role_level(spelling) == ROLE_HIERARCHY[expected]


def test_an_unknown_role_still_gets_no_privilege():
    """Aliasing must not become a way to invent privilege. Anything genuinely
    unrecognised stays at level 0 — fail closed."""
    assert role_level("wizard") == 0
    assert role_level("") == 0
    assert role_level(None) == 0


def test_no_alias_grants_more_than_the_role_it_aliases():
    """An alias may only ever resolve to an EXISTING role, never above it."""
    from app.core.security import ROLE_ALIASES

    for alias, target in ROLE_ALIASES.items():
        assert target in ROLE_HIERARCHY, f"alias {alias!r} points at unknown role {target!r}"
        assert role_level(alias) == ROLE_HIERARCHY[target]


# ─────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE — the Audit Trail page read the wrong endpoint
# AT-001 … AT-009
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_trail_endpoint_exists(client):
    """AT-001 — the page read /api/tefca/qa/audit, the QA GATE trail, which has
    none of the specified columns and none of the specified events."""
    res = client.get("/api/tefca/audit-trail")
    assert res.status_code != 404, "the audit trail endpoint is missing"


def test_audit_trail_requires_authentication(client):
    res = client.get("/api/tefca/audit-trail")
    assert res.status_code in GATED


def test_audit_trail_is_gated_at_qalead():
    """AT — audit access is the QA Lead's remit. A reviewer does not need the
    platform's whole authentication history to adjudicate an entity."""
    from app.Tefca.routes import tefca_dashboard_router

    routes = [
        r for r in tefca_dashboard_router.routes
        if getattr(r, "path", None) == "/api/tefca/audit-trail"
    ]
    assert routes, "the audit trail route is not registered on the dashboard router"

    floors = [
        d.dependency.minimum_role
        for r in routes
        for d in r.dependencies
        if hasattr(getattr(d, "dependency", None), "minimum_role")
    ]
    assert "qalead" in floors, f"audit trail role gate is {floors}, expected qalead"


def test_audit_trail_redacts_credential_shaped_keys():
    """AT-008 — no password, token or key in any record.

    The writers do not put secrets in `details`; this makes it a property of the
    READ path too, so a future careless writer cannot leak through this endpoint.
    """
    from app.Tefca.routes import _safe_audit_details

    out = _safe_audit_details({
        "email": "user@example.com",
        "password": "hunter2",
        "access_token": "eyJhbGciOi...",
        "api_key": "sk-live-123",
        "reason": "invalid_credentials",
    })
    assert out["password"] == "[redacted]"
    assert out["access_token"] == "[redacted]"
    assert out["api_key"] == "[redacted]"
    # Non-secret context must survive — redacting everything would make the
    # trail useless, which is its own kind of failure.
    assert out["email"] == "user@example.com"
    assert out["reason"] == "invalid_credentials"


@pytest.mark.parametrize("action,expected", [
    ("login_success", "authentication"),
    ("login_failed", "authentication"),
    ("entity_import", "data_import"),
    # SUPERSEDED BY QA ROUND 3 (AT-005): file_scan is classified "security",
    # not "data_import". Round 3 specifies the malicious-upload rejection as a
    # security event, and it is — the scan runs before parsing and its whole
    # purpose is to refuse a file, which is not an import outcome. The
    # expectation is updated rather than the test deleted: the case still
    # asserts that the action is categorised, which is what AT-007 needs.
    ("file_scan", "security"),
    ("review_decision", "review"),
    ("user_role_changed", "administration"),
    ("report_generated", "reporting"),
    ("something_unmapped", "other"),
])
def test_audit_events_are_categorised_for_the_type_filter(action, expected):
    """AT-007 — the type filter is only meaningful if events map to types."""
    from app.Tefca.routes import _audit_event_type

    assert _audit_event_type(action) == expected


def test_audit_trail_rejects_an_unknown_event_type(client, auth_headers, db_required):
    if not auth_headers:
        pytest.skip("no authenticated test account available")
    res = client.get("/api/tefca/audit-trail?event_type=nonsense", headers=auth_headers)
    # 403 is acceptable here: the fixture account may be below qalead. What must
    # NOT happen is a 200 carrying the unfiltered trail.
    assert res.status_code in (400, 401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE — decisions were sealed to localStorage and never persisted
# DW-001 … DW-005, DW-009
# ─────────────────────────────────────────────────────────────────────────────

def test_decision_endpoint_exists(client):
    """DW — the Decision Workspace wrote to 'arc-decision-ledger' in the browser
    and stopped. No status changed, no reviewer was recorded, no audit row was
    written, and the decision was lost when storage was cleared."""
    res = client.post("/api/tefca/reviews/00000000-0000-0000-0000-000000000000/decision", json={})
    assert res.status_code != 404, "the decision endpoint is missing"


def test_decision_endpoint_requires_authentication(client):
    res = client.post(
        "/api/tefca/reviews/00000000-0000-0000-0000-000000000000/decision",
        json={"decision": "accept", "rationale": "looks fine"},
    )
    assert res.status_code in GATED


def test_decision_endpoint_is_gated_at_reviewer():
    """DW-006 — a viewer or contributor cannot adjudicate. The gate is on the
    route, not only on the button: hiding a control is not an access control."""
    import inspect
    from app.Tefca.routes import record_review_decision

    gate = inspect.signature(record_review_decision).parameters["user"].default
    assert gate.dependency.minimum_role == "reviewer"


def test_every_decision_maps_to_a_disposition():
    """A decision that resolves to no status would leave the review unchanged
    while telling the reviewer it was recorded."""
    from app.Tefca.routes import _DECISION_STATUS, _MODIFIABLE_STATUSES

    for decision, status in _DECISION_STATUS.items():
        if decision == "modify":
            # modify takes its classification from the caller, and every value
            # it accepts must itself be a real disposition.
            assert status is None
            continue
        assert status in _MODIFIABLE_STATUSES, (
            f"decision {decision!r} maps to {status!r}, which is not a review status"
        )


def test_decision_history_endpoint_exists(client):
    """DW-009 — the history is read from the append-only audit trail, not from a
    column the next decision would overwrite."""
    res = client.get("/api/tefca/reviews/00000000-0000-0000-0000-000000000000/decisions")
    assert res.status_code != 404


# ─────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE — Review Cycles: the list omitted the fields the table renders
# RC-002 · RC-003 · RC-004
# ─────────────────────────────────────────────────────────────────────────────

def test_cycle_creation_endpoint_exists(client):
    """RC-002 — the New Cycle button was hardcoded disabled with a comment
    claiming no endpoint existed. It has existed for some time."""
    res = client.post("/api/v1/tefca/cycles", json={})
    assert res.status_code != 404, "the cycle creation endpoint is missing"


def test_cycle_creation_requires_authentication(client):
    res = client.post(
        "/api/v1/tefca/cycles",
        json={"cycle_type": "WEEKLY", "cycle_start_date": "2026-08-01"},
    )
    assert res.status_code in GATED


def test_cycle_list_returns_the_dates_and_counts_the_table_renders(client, auth_headers, db_required):
    """RC-003 / RC-004 — the list returned neither start/end dates nor sample
    counts, so both date cells and the completion cell rendered '—'. There was
    nothing on screen to check the format of."""
    if not auth_headers:
        pytest.skip("no authenticated test account available")
    res = client.get("/api/v1/tefca/cycles", headers=auth_headers)
    if res.status_code != 200:
        pytest.skip(f"cycles endpoint unavailable in this environment ({res.status_code})")
    body = res.json()
    assert "cycles" in body
    for cycle in body["cycles"]:
        for field in (
            "cycle_start_date", "cycle_end_date",
            "total_entities_sampled", "total_entities_completed",
            "total_entities_remaining",
        ):
            assert field in cycle, f"cycle record is missing {field!r}"


# ─────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE — Administration was read-only; the endpoints existed
# AD-003 (approve) · AD-004 (reject with reason) · AD-005 · AD-006
# ─────────────────────────────────────────────────────────────────────────────

ADMIN_ACTION_PATHS = [
    ("post", "/api/admin/users/{uid}/approve"),
    ("post", "/api/admin/users/{uid}/reject"),
    ("patch", "/api/admin/users/{uid}/role"),
    ("patch", "/api/admin/users/{uid}"),
]


@pytest.mark.parametrize("method,template", ADMIN_ACTION_PATHS)
def test_admin_user_action_endpoints_exist(client, method, template):
    """AD-003 … AD-006 all failed for the same reason: the Administration page
    had no controls. Every endpoint below already existed and was correct."""
    path = template.format(uid="00000000-0000-0000-0000-000000000000")
    res = getattr(client, method)(path, json={})
    assert res.status_code != 404, f"{method.upper()} {path} is missing"


@pytest.mark.parametrize("method,template", ADMIN_ACTION_PATHS)
def test_admin_user_action_endpoints_require_authentication(client, method, template):
    path = template.format(uid="00000000-0000-0000-0000-000000000000")
    res = getattr(client, method)(path, json={"role": "viewer"})
    assert res.status_code in GATED, (
        f"{method.upper()} {path} answered {res.status_code} without a token"
    )


def test_reject_accepts_a_reason():
    """AD-004 — 'Reject with reason'. The endpoint took no reason at all, so the
    reason could not be recorded and the decision could not be reviewed later."""
    import inspect
    from app.api.admin_users import reject_user, RejectReq

    assert "reason" in RejectReq.model_fields
    assert "req" in inspect.signature(reject_user).parameters


def test_all_eight_roles_are_assignable():
    """AD-006 — an admin who can only assign a subset of roles cannot actually
    administer the platform, and the RBAC evidence depends on every role being
    reachable through the UI that claims to manage them."""
    from app.api.admin_users import VALID_ROLES

    for role in ("viewer", "contributor", "manager", "reviewer",
                 "senior_analyst", "qalead", "program_manager", "admin"):
        assert role in VALID_ROLES, f"{role} cannot be assigned"


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN-013 / EQ-003 — a viewer must see no PII
#
# The #1 reported blocker. The role definitions are explicit — viewer@ is
# "Level 1 Viewer (no PII access)", reviewer@ "can see PII" — but the API did
# not implement the distinction: /api/tefca/reviews is gated at viewer and
# returned full 10-digit NPIs to every role.
# ─────────────────────────────────────────────────────────────────────────────

class _FakeUser:
    def __init__(self, role):
        self.role = role


@pytest.mark.parametrize("role,expected", [
    ("viewer", False),
    ("contributor", False),
    ("manager", False),
    ("analyst", False),            # alias of contributor — still below the floor
    ("reviewer", True),
    ("senior_analyst", True),
    ("qalead", True),
    ("program_manager", True),
    ("admin", True),
])
def test_pii_visibility_follows_the_documented_role_floor(role, expected):
    from app.Tefca.routes import _can_see_pii

    assert _can_see_pii(_FakeUser(role)) is expected


def test_an_unknown_role_is_never_shown_pii():
    """Fail closed: a role we cannot place gets the LEAST access, not the most."""
    from app.Tefca.routes import _can_see_pii

    assert _can_see_pii(_FakeUser("wizard")) is False
    assert _can_see_pii(_FakeUser(None)) is False


def test_masking_hides_the_identifier_but_keeps_a_usable_reference():
    """A reviewer and a viewer discussing the same case need a shared handle;
    four digits of a ten-digit NPI is not a re-identifier on its own."""
    from app.Tefca.routes import _mask_identifier

    masked = _mask_identifier("1999000101")
    assert masked.endswith("0101")
    assert "1999" not in masked
    assert len(masked) == len("1999000101")


def test_masking_never_invents_a_value():
    """An absent identifier must stay absent — a masked placeholder where there
    was no value would imply data we do not have."""
    from app.Tefca.routes import _mask_identifier

    assert _mask_identifier(None) is None
    assert _mask_identifier("") == ""


def test_pii_bearing_csv_export_is_gated_above_viewer():
    """The export's own summary has always said 'contains PII' while admitting
    viewer(1)."""
    import inspect
    from app.Tefca.routes import export_reviews, PII_ROLE_FLOOR

    gate = inspect.signature(export_reviews).parameters["user"].default
    assert gate.dependency.minimum_role == PII_ROLE_FLOOR == "reviewer"


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN-007 — the SSO control could not be offered without knowing if it works
# ─────────────────────────────────────────────────────────────────────────────

def test_sso_status_endpoint_is_public(client):
    """The sign-in page must be able to ask before anyone has a token.

    The control is shown only where SSO is configured: the initiation route 503s
    when AZURE_AD_* is unset, and a button that always fails is worse than none.
    """
    res = client.get("/api/auth/sso/status")
    assert res.status_code == 200, "the login page cannot determine whether SSO is available"
    body = res.json()
    assert isinstance(body.get("enabled"), bool)


def test_sso_status_does_not_leak_identifiers(client):
    """An unauthenticated endpoint has no reason to hand out tenant identifiers."""
    res = client.get("/api/auth/sso/status")
    body = res.json()
    assert set(body) <= {"enabled", "provider"}, f"unexpected fields: {set(body)}"


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN-002 / LOGIN-003 — the generic-credentials guarantee
# ─────────────────────────────────────────────────────────────────────────────

def test_wrong_password_and_unknown_email_give_the_same_answer(client):
    """LOGIN-002 and LOGIN-003 assert the SAME error for a wrong password and an
    unknown address. Differing responses turn the login form into an account
    enumeration oracle."""
    unknown = client.post(
        "/api/auth/login",
        json={"email": "nobody-abc123@example.invalid", "password": "whatever"},
    )
    wrong = client.post(
        "/api/auth/login",
        json={"email": "admin@docuaction.io", "password": "definitely-not-the-password"},
    )
    if unknown.status_code == 429 or wrong.status_code == 429:
        pytest.skip("rate limiter engaged; the enumeration property is unaffected")
    assert unknown.status_code == wrong.status_code, (
        "status code differs between an unknown email and a wrong password"
    )
    if unknown.status_code == 401:
        assert unknown.json().get("error") == wrong.json().get("error"), (
            "error message differs — this reveals whether an account exists"
        )


@pytest.mark.parametrize("payload", [
    {"email": "", "password": "something"},           # LOGIN-004
    {"email": "admin@docuaction.io", "password": ""},  # LOGIN-005
])
def test_empty_credentials_are_rejected_without_a_server_error(client, payload):
    """LOGIN-004 / LOGIN-005 — 'Validation error. Login blocked. No HTTP 500.'
    A 500 here would mean an empty field reached something that could not
    handle it."""
    res = client.post("/api/auth/login", json=payload)
    assert res.status_code != 500, "an empty credential field caused a server error"
    assert res.status_code in (400, 401, 422, 429)
