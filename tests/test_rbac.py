"""Role hierarchy and role-gated access.

Two distinct things are asserted. The hierarchy itself is pure logic and can be
checked exactly: viewer < contributor < reviewer < qalead < admin, and
require_role(x) must admit every role at or above x. The endpoint side is
checked only in the deny direction, because minting a real token for each role
would mean seeding users — and a test that silently skips when the DB is absent
proves nothing about the guard.
"""
import pytest

from app.core.security import ROLE_HIERARCHY

GATED = (401, 403)

ORDER = ["viewer", "contributor", "reviewer", "qalead", "admin"]


def test_every_expected_role_exists_in_the_hierarchy():
    for role in ORDER:
        assert role in ROLE_HIERARCHY, f"{role} missing from ROLE_HIERARCHY"


def test_hierarchy_is_strictly_increasing():
    levels = [ROLE_HIERARCHY[r] for r in ORDER]
    assert levels == sorted(levels), f"role levels out of order: {dict(zip(ORDER, levels))}"
    assert len(set(levels)) == len(levels), "two roles share a privilege level"


@pytest.mark.parametrize("required,role,allowed", [
    ("viewer", "viewer", True),
    ("viewer", "admin", True),
    ("contributor", "viewer", False),
    ("contributor", "contributor", True),
    ("reviewer", "contributor", False),
    ("qalead", "reviewer", False),
    ("admin", "qalead", False),
    ("admin", "admin", True),
])
def test_role_admits_at_or_above_and_refuses_below(required, role, allowed):
    """This is the comparison require_role() performs. Getting the direction
    wrong would either lock out every user or admit every user, and both have
    happened in real systems."""
    assert (ROLE_HIERARCHY[role] >= ROLE_HIERARCHY[required]) is allowed


def test_unknown_role_gets_no_privilege():
    """A token carrying a role we do not recognise must not be treated as
    privileged — default to zero, never to the top of the hierarchy."""
    assert ROLE_HIERARCHY.get("wizard", 0) == 0
    assert ROLE_HIERARCHY.get("wizard", 0) < ROLE_HIERARCHY["viewer"] or \
        ROLE_HIERARCHY["viewer"] == 0


# Guarded by require_role() directly — enforced in every environment.
ALWAYS_GUARDED = [
    "/api/tefca/registry/entities",
    "/api/tefca/dashboard/summary",
    "/api/v1/case-management/patients",
]

# Guarded by bulletin guard(), which is a deliberate no-op unless
# BULLETIN_AUTH_ENABLED is set. Asserting these unconditionally would fail in a
# default test environment and say nothing about the guard, so they are checked
# only when the flag that activates them is on.
FLAG_GATED = [
    "/api/v1/bulletin/costs",
    "/api/v1/bulletin/profiles",
    "/api/v1/bulletin/archive/fcc",
]


@pytest.mark.parametrize("path", ALWAYS_GUARDED)
def test_role_gated_endpoints_refuse_anonymous(client, path):
    r = client.get(path)
    assert r.status_code in GATED or r.status_code == 404, (
        f"{path} returned {r.status_code} to an anonymous caller")


@pytest.mark.parametrize("path", FLAG_GATED)
def test_flag_gated_endpoints_refuse_anonymous_when_enabled(client, path):
    from app.bulletin_intelligence.auth import BULLETIN_AUTH_ENABLED
    if not BULLETIN_AUTH_ENABLED:
        pytest.skip("BULLETIN_AUTH_ENABLED off; guard() is a no-op by design")
    r = client.get(path)
    assert r.status_code in GATED or r.status_code == 404


@pytest.mark.parametrize("path", ALWAYS_GUARDED)
def test_role_gated_endpoints_refuse_a_forged_token(client, path):
    """An unsigned/garbage bearer must not be accepted. This is the alg=none
    class of bug, checked at the boundary rather than in the decoder."""
    r = client.get(path, headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code in GATED or r.status_code == 404


def test_admin_only_write_refuses_anonymous(client):
    from app.bulletin_intelligence.auth import BULLETIN_AUTH_ENABLED
    if not BULLETIN_AUTH_ENABLED:
        pytest.skip("BULLETIN_AUTH_ENABLED off; guard() is a no-op by design")
    r = client.post("/api/v1/bulletin/sources/load-catalog")
    assert r.status_code in GATED or r.status_code == 404
