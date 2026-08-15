"""Per-role endpoint authorization — asserted in BOTH directions.

WHY THIS FILE EXISTS (the P0 this suite failed to catch)

test_rbac.py checks the hierarchy as pure arithmetic and the endpoints only in the
DENY direction, because minting a token per role "would mean seeding users". That
gap is exactly where the P0 lived: every TEFCA endpoint was reachable ONLY by admin,
and nothing failed, because no test ever asserted that a non-admin role gets IN.
A guard that denies everyone passes a deny-only suite perfectly.

Three defects composed into it:

  1. app/api/admin_users.py VALID_ROLES omitted reviewer / senior_analyst / qalead /
     program_manager, so the highest assignable non-admin role was manager (3).
  2. app/Tefca/routes.py floored the whole /api/v1/tefca router at reviewer (4) —
     above every assignable non-admin role — so all TEFCA reads were admin-only.
  3. AppLayout.js omitted bulletin_intelligence from ALWAYS_ALLOWED, so with
     allowed_modules defaulting to [], bulletin was admin-only in the UI.

HOW THE ALLOW DIRECTION IS TESTED WITHOUT A DATABASE

get_db is overridden with a stub session that resolves the bearer's user lookup to
an in-memory User. That is all require_role() and get_current_user() need. The
endpoint body then runs against the same stub and will usually fail — which is
fine and is the point: this file asserts the AUTHORIZATION outcome only.

  denied  == status 403
  allowed != status 403   (200 / 404 / 422 / 500 all mean the guard let it through)

Asserting "not 403" rather than "200" is deliberate. Requiring 200 would force this
file to carry a working database and a fixture per endpoint, and the thing under
test — the role gate — would be the least of what could break it.
"""
import re
import uuid
from pathlib import Path

import pytest

from app.core.database import get_db
from app.core.security import ROLE_HIERARCHY, create_access_token
from app.main import app

DENIED = 403


# ── in-memory user + db stub ─────────────────────────────────────────────────

class _User:
    """The attributes _enforce_account_state() and the route bodies read."""

    def __init__(self, role):
        self.id = str(uuid.uuid4())
        self.email = f"{role}@test.local"
        self.full_name = f"{role} tester"
        self.company = ""
        self.role = role
        self.plan = "enterprise"
        self.tenant_id = "default"
        self.is_active = True
        self.is_verified = True
        self.status = "active"
        self.tokens_revoked_at = None
        self.allowed_modules = []
        self.created_at = None


class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user

    def scalar(self):
        return 0

    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None

    def __iter__(self):
        return iter(())


class _Session:
    def __init__(self, user):
        self._user = user

    async def execute(self, *a, **k):
        return _Result(self._user)

    async def commit(self):
        return None

    async def refresh(self, *a, **k):
        return None

    async def close(self):
        return None

    def add(self, *a, **k):
        return None


@pytest.fixture
def as_role(client):
    """Return a caller for `role`: (method, path, **kw) -> Response.

    Installs a get_db override resolving to a user with that role, and signs a
    real access token carrying it — the same token shape login mints.
    """
    def _make(role):
        assert role in ROLE_HIERARCHY, f"unknown role {role!r}"
        user = _User(role)

        async def _override():
            yield _Session(user)

        app.dependency_overrides[get_db] = _override
        token = create_access_token({"sub": user.id, "role": role})
        headers = {"Authorization": f"Bearer {token}"}

        def call(method, path, **kw):
            kw.setdefault("headers", {}).update(headers)
            return client.request(method, path, **kw)

        return call

    yield _make
    app.dependency_overrides.pop(get_db, None)


# Endpoints that a viewer (level 1) must be able to READ. Before the fix every one
# of these answered 403 to every non-admin role.
VIEWER_READS = [
    "/api/tefca/dashboard/summary",
    "/api/tefca/dashboard/trends",
    "/api/tefca/reports",
    "/api/tefca/reviews",
    "/api/tefca/findings",
    "/api/tefca/registry/entities",
    "/api/tefca/registry/stats",
    "/api/v1/tefca/cycles",
    "/api/v1/tefca/reports",
    "/api/v1/tefca/priority-cases",
]

WRITE_ENDPOINTS = {
    "import_upload": ("POST", "/api/tefca/entities/upload"),
    "import_csv": ("POST", "/api/tefca/registry/import/csv"),
    "verify_entity": ("POST", f"/api/tefca/registry/entities/{uuid.uuid4()}/verify"),
    "approve_weekly": ("POST", "/api/tefca/reports/weekly"),
}


# ── viewer ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", VIEWER_READS)
def test_viewer_can_read_tefca_dashboard(as_role, path):
    """The regression that defines this P0: a viewer reading TEFCA must not 403."""
    r = as_role("viewer")("GET", path)
    assert r.status_code != DENIED, (
        f"viewer denied read access to {path} (got {r.status_code})")


def test_viewer_cannot_import_entities(as_role):
    """Import/verify sit at the contributor floor (QA-1.8: importing a roster is
    data entry, not adjudication), so a viewer is below them and a reviewer is
    above. Opening TEFCA reads to viewer must not have opened its writes."""
    viewer = as_role("viewer")
    for key in ("import_upload", "import_csv", "verify_entity"):
        method, path = WRITE_ENDPOINTS[key]
        r = viewer(method, path)
        assert r.status_code == DENIED, (
            f"viewer was NOT denied {method} {path} (got {r.status_code})")


def test_viewer_cannot_approve(as_role):
    method, path = WRITE_ENDPOINTS["approve_weekly"]
    assert as_role("viewer")(method, path).status_code == DENIED


def test_viewer_can_read_bulletin(as_role):
    """Bulletin's API is open to any authenticated caller (BULLETIN_AUTH_ENABLED
    off), so a viewer must never be denied it. The block was in the UI shell —
    covered by test_bulletin_is_reachable_for_non_admins_in_the_app_shell."""
    r = as_role("viewer")("GET", "/api/v1/bulletin/latest/fcc")
    assert r.status_code != DENIED


# ── reviewer ─────────────────────────────────────────────────────────────────

def test_reviewer_can_access_tefca(as_role):
    reviewer = as_role("reviewer")
    for path in VIEWER_READS:
        assert reviewer("GET", path).status_code != DENIED, f"reviewer denied {path}"


def test_reviewer_can_import_entities(as_role):
    reviewer = as_role("reviewer")
    for key in ("import_upload", "import_csv"):
        method, path = WRITE_ENDPOINTS[key]
        r = reviewer(method, path)
        assert r.status_code != DENIED, (
            f"reviewer denied {method} {path} (got {r.status_code})")


def test_reviewer_can_verify_entities(as_role):
    method, path = WRITE_ENDPOINTS["verify_entity"]
    r = as_role("reviewer")(method, path)
    assert r.status_code != DENIED, f"reviewer denied entity verification ({r.status_code})"


def test_reviewer_can_access_bulletin(as_role):
    assert as_role("reviewer")("GET", "/api/v1/bulletin/latest/fcc").status_code != DENIED


def test_reviewer_cannot_approve_deliverables(as_role):
    """Reviewer stops short of QA approval — the ladder must still have rungs."""
    method, path = WRITE_ENDPOINTS["approve_weekly"]
    assert as_role("reviewer")(method, path).status_code == DENIED


# ── qalead ───────────────────────────────────────────────────────────────────

def test_qalead_can_approve_reviews(as_role):
    qalead = as_role("qalead")
    for method, path in (WRITE_ENDPOINTS["approve_weekly"],
                         ("POST", "/api/tefca/qa/alerts/test")):
        r = qalead(method, path)
        assert r.status_code != DENIED, f"qalead denied {method} {path} ({r.status_code})"


def test_qalead_can_access_qa_endpoints(as_role):
    qalead = as_role("qalead")
    for path in ("/api/tefca/qa/score", "/api/tefca/qa/audit", "/api/tefca/qa/report-gate"):
        assert qalead("GET", path).status_code != DENIED, f"qalead denied {path}"


# ── admin boundary (must NOT regress while opening up non-admins) ────────────

@pytest.mark.parametrize("role", ["viewer", "contributor", "manager", "reviewer",
                                  "senior_analyst", "qalead", "program_manager"])
def test_non_admin_cannot_manage_users(as_role, role):
    caller = as_role(role)
    for method, path in (("GET", "/api/admin/users"),
                         ("POST", "/api/admin/users"),
                         ("GET", "/api/admin/users/pending")):
        r = caller(method, path)
        assert r.status_code == DENIED, (
            f"{role} reached {method} {path} (got {r.status_code}) — user management "
            "must stay admin-only")


def test_admin_retains_full_access(as_role):
    """The fix must not have narrowed admin anywhere."""
    admin = as_role("admin")
    for path in VIEWER_READS + ["/api/admin/users"]:
        assert admin("GET", path).status_code != DENIED, f"admin denied {path}"
    for method, path in WRITE_ENDPOINTS.values():
        assert admin(method, path).status_code != DENIED, f"admin denied {method} {path}"


# ── role assignability (Defect 1) ────────────────────────────────────────────

def test_all_8_roles_assignable_via_admin_api():
    """The admin API must be able to grant every role the authorization layer
    honours. While these two sets disagreed, reviewer/qalead accounts could only be
    created by writing to the database by hand."""
    from app.api.admin_users import VALID_ROLES

    missing = set(ROLE_HIERARCHY) - set(VALID_ROLES)
    assert not missing, f"roles honoured by require_role but not assignable: {sorted(missing)}"
    unknown = set(VALID_ROLES) - set(ROLE_HIERARCHY)
    assert not unknown, f"assignable roles with no privilege level: {sorted(unknown)}"
    assert len(VALID_ROLES) == 8


def test_frontend_role_picker_offers_every_backend_role():
    """The picker and VALID_ROLES drifting apart is how Defect 1 stayed invisible:
    the UI simply never showed the roles it could not send."""
    src = (Path(__file__).resolve().parents[2]
           / "frontend" / "src" / "components" / "UsersAdmin.js").read_text(encoding="utf-8")
    block = re.search(r"const ROLES = \[(.*?)\]", src, re.S)
    assert block, "could not locate the ROLES list in UsersAdmin.js"
    offered = set(re.findall(r"'([a-z_]+)'", block.group(1)))
    assert offered == set(ROLE_HIERARCHY), (
        f"picker/backend mismatch — missing {sorted(set(ROLE_HIERARCHY) - offered)}, "
        f"extra {sorted(offered - set(ROLE_HIERARCHY))}")


# ── Defect 3: the UI shell gate ──────────────────────────────────────────────

def test_bulletin_is_reachable_for_non_admins_in_the_app_shell():
    """Defect 3 was pure frontend: canAccess() is `isAdmin || allowed.has(id)`, and
    a new user's allowed_modules is []. Omitting bulletin_intelligence from
    ALWAYS_ALLOWED therefore made bulletin admin-only in the UI while its API was
    open to everyone. Checked here because no JS test runs in CI."""
    src = (Path(__file__).resolve().parents[2]
           / "frontend" / "src" / "components" / "AppLayout.js").read_text(encoding="utf-8")
    block = re.search(r"const ALWAYS_ALLOWED = \[(.*?)\];", src, re.S)
    assert block, "could not locate ALWAYS_ALLOWED in AppLayout.js"
    ids = set(re.findall(r"'([a-z_]+)'", block.group(1)))
    assert "bulletin_intelligence" in ids, (
        "bulletin_intelligence missing from ALWAYS_ALLOWED — non-admins get the "
        "'Access restricted' screen on /bulletin")


# ── the property that made the P0 possible ───────────────────────────────────

def test_no_tefca_read_endpoint_sits_above_the_viewer_floor():
    """The root-cause shape, asserted directly: a read gated above viewer is
    invisible to the roles the product can actually assign. Router-level floors are
    included, which is what the reviewer-floored router evaded."""
    from app.core.security import ROLE_HIERARCHY as H

    def routes(container, seen=None):
        seen = seen if seen is not None else set()
        if id(container) in seen:
            return
        seen.add(id(container))
        for r in getattr(container, "routes", []):
            if hasattr(r, "dependant"):
                yield r
            else:
                inner = getattr(r, "original_router", None)
                yield from routes(inner if inner is not None else r, seen)

    def deps(d, seen=None):
        seen = seen if seen is not None else set()
        if id(d) in seen:
            return
        seen.add(id(d))
        if d.call is not None:
            yield d.call
        for s in d.dependencies:
            yield from deps(s, seen)

    # Deliberate exceptions, each justified rather than blanket-ignored.
    ALLOWED_ABOVE_VIEWER = {
        "/api/v1/tefca/queue/tier3",  # Bucket-3 escalation queue — senior_analyst by contract
        # Platform audit trail — qalead (6). This is the ONE TEFCA read that is
        # not entity data: it is every user's authentication history, with their
        # email addresses and source IPs. Level 6 is "QA Lead — audit access, no
        # entity changes", which is precisely this capability. Opening it to
        # viewer would also contradict the rule the viewer role exists to
        # enforce (LOGIN-013: a viewer sees no PII anywhere), so the general
        # principle behind this test argues FOR the exception here rather than
        # against it.
        "/api/tefca/audit-trail",
        # CSV export of reviews — reviewer (4). The route's own summary has
        # always read "contains PII" while admitting viewer(1). Masking the file
        # instead would hand out an evidence artefact that silently differs from
        # the record it claims to be, so this one is a denial rather than a
        # redaction. The equivalent data IS available to a viewer through
        # /api/tefca/reviews, with identifiers masked.
        "/api/tefca/reports/export",
        # QA sweep — qalead (6). Modelled as a GET, but it EXECUTES every QA
        # gate, writes audit rows and can dispatch threshold alert emails. It is
        # an operational action with side effects rather than a dashboard read,
        # and QA-004 specifies that a viewer is denied it. The read-only QA
        # results a viewer legitimately needs remain open: /api/tefca/qa/score,
        # /qa/health, /qa/audit and /qa/evidence-summary are all still viewer.
        "/api/tefca/qa/sweep",
    }

    offenders = []
    for r in routes(app):
        path = getattr(r, "path", "")
        if not path.startswith(("/api/v1/tefca", "/api/tefca")):
            continue
        if "GET" not in (getattr(r, "methods", None) or set()):
            continue
        if path in ALLOWED_ABOVE_VIEWER:
            continue
        floors = [H.get(mr, 0) for mr in
                  (getattr(fn, "minimum_role", None) for fn in deps(r.dependant))
                  if mr is not None]
        if floors and max(floors) > H["viewer"]:
            offenders.append((path, max(floors)))

    assert not offenders, (
        "TEFCA GET endpoints gated above viewer: "
        + ", ".join(f"{p} (level {l})" for p, l in sorted(offenders)))
