"""Role-based default module grants, and the bulk role-assignment endpoint.

Both exist to answer the same operational need — "get a group of people usable
quickly" — WITHOUT inferring privilege from anything about the account. The
rejected alternative was automatic elevation by email domain, which
core/security.py forbids in terms:

    # REMOVED: email-based auto-admin escalation. Role/privilege assignment must
    # happen only via database/admin action, never by email domain
    # (HHSAR 352.204-71, FAR 52.212-4 - least privilege, authorized personnel only).
    # Never reintroduce automatic role elevation based on email address.

So the split asserted here is the important one:

  * MODULE VISIBILITY may be defaulted from role (this file's first half). It
    grants no privilege — every write stays behind require_role.
  * ROLE may only be set by an authenticated admin, per account, audited (second
    half). Never derived from the email address.

test_no_privilege_is_derived_from_email_domain is the regression guard on that
prohibition, and it is the reason this file exists rather than a domain rule.
"""
import re
from pathlib import Path

import pytest

from app.api.admin_users import (
    DEFAULT_MODULES_BY_ROLE,
    MODULES,
    default_modules_for_role,
)
from app.core.security import ROLE_HIERARCHY

ALL_MODULE_IDS = {m["id"] for m in MODULES}
BASE = {"tefca_review", "bulletin_intelligence"}


# ── A. default module grant by role ──────────────────────────────────────────

def test_every_role_has_a_default_module_grant():
    """A role with no entry would fall back to the base set silently; making the
    table total keeps that a decision rather than an accident."""
    missing = set(ROLE_HIERARCHY) - set(DEFAULT_MODULES_BY_ROLE)
    assert not missing, f"roles with no default module grant: {sorted(missing)}"


@pytest.mark.parametrize("role", ["viewer", "contributor", "reviewer", "qalead"])
def test_new_user_gets_tefca_and_bulletin_not_an_empty_array(role):
    """The gap this closes: allowed_modules defaulted to [], so a freshly created
    account could log in and reach nothing until an admin ticked boxes."""
    granted = set(default_modules_for_role(role))
    assert BASE <= granted, f"{role} default is missing {sorted(BASE - granted)}"
    assert granted, "default grant must never be empty"


def test_admin_gets_every_module():
    assert set(default_modules_for_role("admin")) == ALL_MODULE_IDS


@pytest.mark.parametrize("role", ["viewer", "contributor", "manager", "reviewer",
                                  "senior_analyst", "qalead", "program_manager"])
def test_non_admin_defaults_are_not_the_full_module_set(role):
    """Defaulting a non-admin to everything would make the module system
    decorative."""
    assert set(default_modules_for_role(role)) != ALL_MODULE_IDS


def test_unknown_role_does_not_get_widened_access():
    """An unrecognised role must fail toward LESS access, never more."""
    granted = set(default_modules_for_role("wizard"))
    assert granted == BASE
    assert granted != ALL_MODULE_IDS


def test_defaults_only_contain_real_module_ids():
    for role, ids in DEFAULT_MODULES_BY_ROLE.items():
        unknown = set(ids) - ALL_MODULE_IDS
        assert not unknown, f"{role} default grants non-existent modules: {sorted(unknown)}"


def test_default_grant_is_not_shared_mutable_state():
    """The base list is referenced by seven roles; handing out the same list object
    would let one caller's mutation rewrite every role's default."""
    a = default_modules_for_role("viewer")
    a.append("compliance")
    assert "compliance" not in default_modules_for_role("contributor")
    assert "compliance" not in default_modules_for_role("viewer")


def test_module_default_is_visibility_not_privilege():
    """Granting tefca_review to a viewer must not imply any write capability —
    that stays with require_role on each endpoint."""
    assert "tefca_review" in default_modules_for_role("viewer")
    assert ROLE_HIERARCHY["viewer"] < ROLE_HIERARCHY["reviewer"]


# ── B. bulk role assignment ──────────────────────────────────────────────────

def test_bulk_role_endpoint_is_admin_only(client):
    for headers in ({}, {"Authorization": "Bearer not-a-real-token"}):
        r = client.post("/api/admin/users/bulk-role",
                        json={"emails": ["a@b.c"], "role": "reviewer"}, headers=headers)
        assert r.status_code in (401, 403), (
            f"bulk-role answered {r.status_code} to an unauthenticated caller")


def test_bulk_role_is_registered_and_audited():
    """Every grant must leave an attributable trail — that is the whole reason this
    endpoint is an acceptable substitute for automatic elevation."""
    src = Path(__file__).resolve().parents[1] / "app" / "api" / "admin_users.py"
    body = src.read_text(encoding="utf-8")
    fn = body[body.index("async def bulk_set_role"):]
    fn = fn[:fn.index("\n@router") if "\n@router" in fn else len(fn)]
    assert "_audit(" in fn, "bulk role change writes no audit record"
    assert "role_changed" in fn
    assert "is_super_admin" in fn, "bulk path does not gate the admin role"
    assert "VALID_ROLES" in fn, "bulk path does not validate the role"


# ── the prohibition this feature set replaces ────────────────────────────────

def test_no_privilege_is_derived_from_email_domain():
    """Standing prohibition (core/security.py). Role must never be assigned from the
    email address — not at signup, not at login, not on refresh. Asserted against
    the auth surface rather than a doc so it fails when someone writes the code."""
    root = Path(__file__).resolve().parents[1] / "app"
    surface = [root / "core" / "security.py",
               root / "api" / "routes.py",
               root / "api" / "admin_users.py"]

    # A role literal appearing on the same line as a domain/email test is the shape
    # of "if email endswith <domain>: role = <privileged>".
    roles = "|".join(sorted(ROLE_HIERARCHY))
    suspicious = re.compile(
        r"(endswith\s*\(|\bsplit\s*\(\s*[\"']@|@[a-z0-9-]+\.[a-z]{2,}).*"
        r"(role\s*=|\brole\b.*(%s))" % roles, re.I)

    hits = []
    for path in surface:
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if suspicious.search(line):
                hits.append(f"{path.name}:{i}: {stripped[:100]}")

    assert not hits, (
        "possible role assignment derived from email address — this is forbidden "
        "(HHSAR 352.204-71 / FAR 52.212-4, least privilege):\n  " + "\n  ".join(hits))


def test_the_removal_note_is_still_present_in_security():
    """The comment is the institutional memory for why domain-based elevation keeps
    getting rejected. If someone deletes it, the next person will rebuild it."""
    src = (Path(__file__).resolve().parents[1] / "app" / "core" / "security.py"
           ).read_text(encoding="utf-8")
    assert "Never reintroduce automatic role elevation based on email address" in src
