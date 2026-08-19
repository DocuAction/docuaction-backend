"""
Role change must take effect immediately — JWT role trust + session revocation.

THE DEFECT THESE TESTS PIN
──────────────────────────
An admin demoted a reviewer to contributor (or removed their access) in
Admin -> Users & Access. The database changed. Nothing else did. The user's
browser still held a valid, correctly-signed JWT asserting `role: reviewer`,
and `require_role` made its authorisation decision from that token claim — so
the demoted user kept reviewer privileges until the token expired on its own
(up to 24h for an admin token, 15 minutes otherwise).

Two independent holes, and either one alone leaves the door open:

  1. No path that changed a role stamped `tokens_revoked_at`, so the existing
     session was never invalidated.
  2. `require_role` authorised on the TOKEN role rather than the DATABASE role,
     so even a correct revocation stamp would not have stopped the request that
     was already in flight with an old token.

NO DATABASE REQUIRED. Every test here drives the real functions with stub
session objects. That is deliberate: this suite skips its database-backed tests
when no Postgres is reachable, and a security regression test that silently
skips is not a test. These run everywhere, every time.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.core.security import (
    ROLE_HIERARCHY,
    _token_revoked,
    create_access_token,
    decode_token,
    require_role,
    role_level,
)

pytestmark = [pytest.mark.regression, pytest.mark.security]


# ── Stubs ────────────────────────────────────────────────────────────────────

class FakeUser:
    def __init__(self, user_id="u-1", role="reviewer", email="user@docuaction.io",
                 is_active=True, status="active", tokens_revoked_at=None,
                 allowed_modules=None):
        self.id = user_id
        self.role = role
        self.email = email
        self.is_active = is_active
        self.status = status
        self.tokens_revoked_at = tokens_revoked_at
        self.allowed_modules = allowed_modules or []
        self.full_name = "Test User"
        self.company = ""
        self.is_verified = True
        self.created_at = datetime(2026, 1, 1)
        self.plan = "enterprise"


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj

    def scalars(self):
        class _S:
            def __init__(self, o):
                self._o = o

            def all(self_inner):
                return [self._obj] if self._obj is not None else []
        return _S(self._obj)


class FakeDB:
    """Stub AsyncSession: returns one user, records adds/commits."""

    def __init__(self, user=None):
        self.user = user
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, *_a, **_k):
        return _Result(self.user)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _obj):
        return None


class FakeCreds:
    def __init__(self, token):
        self.credentials = token


def token_for(user_id="u-1", role="reviewer", is_admin=False, **extra):
    data = {"sub": user_id, "role": role}
    data.update(extra)
    return create_access_token(data, is_admin=is_admin)


async def call_gate(minimum_role, token, user):
    """Invoke the real require_role dependency against a stub session."""
    checker = require_role(minimum_role)
    return await checker(creds=FakeCreds(token), db=FakeDB(user))


# ── FIX 3 — the database role is the authority ───────────────────────────────

class TestDatabaseRoleIsAuthoritative:
    async def test_database_role_used_not_jwt_role(self):
        """The exact defect: token says reviewer, database says contributor."""
        stale = token_for(role="reviewer")
        demoted = FakeUser(role="contributor")

        with pytest.raises(HTTPException) as exc:
            await call_gate("reviewer", stale, demoted)
        assert exc.value.status_code == 403
        # The message must name what the user ACTUALLY is now, not what the
        # stale token claimed — otherwise the log of this denial is misleading.
        assert "contributor" in str(exc.value.detail)

    async def test_contributor_cannot_access_tefca_after_downgrade(self):
        """Reviewer-gated TEFCA routes reject a downgraded user immediately."""
        stale = token_for(role="reviewer")
        demoted = FakeUser(role="contributor")
        for gate in ("reviewer", "senior_analyst", "qalead", "program_manager", "admin"):
            with pytest.raises(HTTPException) as exc:
                await call_gate(gate, stale, demoted)
            assert exc.value.status_code == 403, gate

    async def test_promotion_also_takes_effect_immediately(self):
        """The fix must work in both directions, or it just moves the problem.

        A user promoted to reviewer should not have to sign out and back in to
        use their new access.
        """
        old_token = token_for(role="contributor")
        promoted = FakeUser(role="reviewer")
        user = await call_gate("reviewer", old_token, promoted)
        assert user.role == "reviewer"

    async def test_role_removed_entirely_is_denied(self):
        """Fail-closed: a NULL/blank/unknown role resolves to level 0."""
        stale = token_for(role="admin")
        for bad_role in (None, "", "not_a_real_role"):
            stripped = FakeUser(role=bad_role)
            with pytest.raises(HTTPException) as exc:
                await call_gate("viewer", stale, stripped)
            assert exc.value.status_code == 403, bad_role

    async def test_forged_high_role_in_token_cannot_elevate(self):
        """A token minted with a role the database does not grant buys nothing.

        The token here is legitimately signed — this is not a forgery test so
        much as proof that the claim itself no longer carries authority.
        """
        inflated = token_for(role="admin")
        actually_viewer = FakeUser(role="viewer")
        with pytest.raises(HTTPException) as exc:
            await call_gate("admin", inflated, actually_viewer)
        assert exc.value.status_code == 403

    async def test_matching_role_still_passes(self):
        """The fix must not break the ordinary case."""
        token = token_for(role="reviewer")
        user = FakeUser(role="reviewer")
        assert await call_gate("reviewer", token, user) is user

    async def test_higher_role_satisfies_lower_gate(self):
        token = token_for(role="admin")
        admin = FakeUser(role="admin")
        assert await call_gate("viewer", token, admin) is admin


# ── FIX 2 — a revoked token is rejected ──────────────────────────────────────

class TestTokenRevocation:
    async def test_revoked_token_returns_401(self):
        token = token_for(role="reviewer")
        revoked_user = FakeUser(role="reviewer",
                                tokens_revoked_at=datetime.utcnow() + timedelta(seconds=5))
        with pytest.raises(HTTPException) as exc:
            await call_gate("reviewer", token, revoked_user)
        assert exc.value.status_code == 401
        assert "sign in again" in str(exc.value.detail).lower()

    async def test_old_token_rejected_after_role_change(self):
        """End state of the whole fix, expressed as one assertion.

        Demoted AND revoked — which is what an admin role change now produces.
        401 (not 403) is correct here: the session itself is over, and the user
        needs to sign in again rather than merely being told no.
        """
        stale = token_for(role="reviewer")
        demoted_and_revoked = FakeUser(
            role="contributor",
            tokens_revoked_at=datetime.utcnow() + timedelta(seconds=5),
        )
        with pytest.raises(HTTPException) as exc:
            await call_gate("viewer", stale, demoted_and_revoked)
        assert exc.value.status_code == 401

    def test_token_issued_after_revocation_is_accepted(self):
        """Re-login must work. A revocation epoch is a line in time, not a ban."""
        revoked_at = datetime.utcnow() - timedelta(minutes=10)
        user = FakeUser(tokens_revoked_at=revoked_at)
        fresh = decode_token(token_for(role="contributor"))
        assert _token_revoked(user, fresh) is False

    def test_token_issued_before_revocation_is_rejected(self):
        old = decode_token(token_for(role="reviewer"))
        user = FakeUser(tokens_revoked_at=datetime.utcnow() + timedelta(minutes=10))
        assert _token_revoked(user, old) is True

    def test_user_who_never_revoked_is_unaffected(self):
        """Grandfathering: a NULL revocation epoch must not lock anyone out."""
        user = FakeUser(tokens_revoked_at=None)
        assert _token_revoked(user, decode_token(token_for())) is False

    def test_token_without_iat_is_rejected_when_a_revocation_exists(self):
        """Fail-closed for pre-epoch tokens that carry no issued-at claim."""
        user = FakeUser(tokens_revoked_at=datetime.utcnow())
        assert _token_revoked(user, {"sub": "u-1", "role": "reviewer"}) is True


# ── FIX 1 — every authorisation change revokes the session ───────────────────

class TestRoleChangeStampsRevocation:
    async def test_role_change_stamps_tokens_revoked_at(self):
        from app.api.admin_users import RoleReq, set_role

        target = FakeUser(user_id="u-2", role="reviewer", email="reviewer@docuaction.io")
        admin = FakeUser(user_id="u-1", role="admin", email="admin@docuaction.io")
        assert target.tokens_revoked_at is None

        before = datetime.utcnow()
        await set_role("u-2", RoleReq(role="contributor"), admin=admin, db=FakeDB(target))

        assert target.role == "contributor"
        assert target.tokens_revoked_at is not None, "role change did not revoke sessions"
        assert target.tokens_revoked_at >= before

    async def test_permissions_change_stamps_tokens_revoked_at(self):
        """Removing module access is an authorisation change like any other."""
        from app.api.admin_users import PermissionsReq, set_permissions

        target = FakeUser(user_id="u-2", role="reviewer")
        admin = FakeUser(user_id="u-1", role="admin", email="admin@docuaction.io")
        await set_permissions("u-2", PermissionsReq(permissions=[]), admin=admin,
                              db=FakeDB(target))
        assert target.tokens_revoked_at is not None

    async def test_combined_update_stamps_on_role_change(self):
        from app.api.admin_users import update_user

        target = FakeUser(user_id="u-2", role="reviewer")
        admin = FakeUser(user_id="u-1", role="admin", email="admin@docuaction.io")
        await update_user("u-2", {"role": "contributor"}, admin=admin, db=FakeDB(target))
        assert target.role == "contributor"
        assert target.tokens_revoked_at is not None

    async def test_combined_update_without_role_change_does_not_revoke(self):
        """Do not sign people out for an edit that changed nothing about access.

        Revoking on a no-op write would train users to expect random logouts,
        which is its own security problem.
        """
        from app.api.admin_users import update_user

        target = FakeUser(user_id="u-2", role="reviewer")
        admin = FakeUser(user_id="u-1", role="admin", email="admin@docuaction.io")
        await update_user("u-2", {"role": "reviewer"}, admin=admin, db=FakeDB(target))
        assert target.tokens_revoked_at is None

    def test_every_role_mutating_endpoint_revokes(self):
        """Static sweep: no future path may change a role without revoking.

        The defect was one endpoint forgetting. This asserts the property over
        the whole module rather than over the endpoints someone remembered to
        write a test for.
        """
        import ast

        source = open("app/api/admin_users.py", encoding="utf-8").read()
        tree = ast.parse(source)

        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(source, node) or ""
            assigns_role = ".role = " in body
            revokes = "_revoke_sessions(" in body or "tokens_revoked_at" in body
            if assigns_role and not revokes and node.name not in {"_revoke_sessions"}:
                offenders.append(node.name)
        assert not offenders, (
            "these functions change a user's role without revoking their sessions: "
            + ", ".join(offenders)
        )


class TestRoleHierarchyUnchanged:
    """The fix must not quietly redefine what the roles mean."""

    def test_hierarchy_values_are_stable(self):
        assert ROLE_HIERARCHY["viewer"] == 1
        assert ROLE_HIERARCHY["contributor"] == 2
        assert ROLE_HIERARCHY["reviewer"] == 4
        assert ROLE_HIERARCHY["admin"] == 8

    def test_contributor_is_below_reviewer(self):
        assert role_level("contributor") < role_level("reviewer")
