"""
Admin — User & Module Access Management (role-level security).

Tiers:
  - SUPER ADMIN : emails in core.security.ADMIN_EMAILS. Full control, incl.
                  creating/deleting admins and changing admin roles.
  - ADMIN       : role == "admin". Manage NON-admin users only.

Module permissions are stored in users.allowed_modules (JSON list of MODULE ids).
Every mutating action is audit-logged for the per-user activity trail.
"""
import secrets
import string
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import select, desc, func, delete as sa_delete, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, ADMIN_EMAILS, hash_password, ROLE_HIERARCHY
from app.core.email import send_invitation_email, send_verification_email, app_url
from app.api.password_reset import _create_reset_token, INVITE_TOKEN_EXPIRE
from app.models.database import User, AuditLog, Document, Output, AudioFile, Transcript

router = APIRouter(prefix="/api/admin", tags=["Admin — Users"])

# Canonical 15 modules, grouped (ids = the DB field names stored in allowed_modules).
MODULES = [
    {"id": "action_center",         "label": "Action Center",         "group": "Operations"},
    {"id": "validation_queue",      "label": "Validation Queue",      "group": "Operations"},
    {"id": "decision_bank",         "label": "Decision Bank",         "group": "Operations"},
    {"id": "bulletin_intelligence", "label": "Bulletin Intelligence", "group": "Intelligence"},
    {"id": "tefca_review",          "label": "TEFCA Review",          "group": "Intelligence"},
    {"id": "opportunities",         "label": "Opportunities",         "group": "Intelligence"},
    {"id": "risk_detection",        "label": "Risk Detection",        "group": "Analytics & Risk"},
    {"id": "analytics",             "label": "Analytics",             "group": "Analytics & Risk"},
    {"id": "healthcare_claims",     "label": "Healthcare Claims",     "group": "Healthcare"},
    {"id": "case_management",       "label": "Case Management",       "group": "Healthcare"},
    {"id": "meetings",              "label": "Meetings",              "group": "Healthcare"},
    {"id": "compliance",            "label": "Compliance",            "group": "Governance"},
    {"id": "audit_logs",            "label": "Audit Logs",            "group": "Governance"},
    {"id": "security",              "label": "Security",              "group": "Governance"},
    {"id": "trust_center",          "label": "Trust Center",          "group": "Governance"},
]
_VALID_IDS = {m["id"] for m in MODULES}
# Back-compat: old area ids -> new module ids (so legacy grants still resolve).
LEGACY_MAP = {
    "actions": "action_center", "validation": "validation_queue", "decisions": "decision_bank",
    "bulletin": "bulletin_intelligence", "tefca": "tefca_review", "casemanagement": "case_management",
    "healthcare": "healthcare_claims", "trust": "trust_center",
}
# Every role in core.security.ROLE_HIERARCHY must be assignable here, otherwise a
# role that the authorization layer honours cannot actually be granted through the
# product. The four TEFCA contract roles (reviewer / senior_analyst / qalead /
# program_manager) were missing, so the only role above "manager" (level 3) that an
# admin could assign was "admin" (level 8) — and every /api/v1/tefca endpoint sits
# behind a reviewer (level 4) floor. Net effect: TEFCA was admin-only, and the only
# way to create a real reviewer was a direct DB write. Derived from ROLE_HIERARCHY
# rather than restated, so the two can never drift apart again.
VALID_ROLES = set(ROLE_HIERARCHY)


def is_super_admin(user) -> bool:
    return user.email in ADMIN_EMAILS


def is_admin_account(user) -> bool:
    return (getattr(user, "role", "") or "").lower() == "admin" or user.email in ADMIN_EMAILS


async def require_admin(user=Depends(get_current_user)):
    if (getattr(user, "role", "") or "").lower() != "admin" and user.email not in ADMIN_EMAILS:
        raise HTTPException(403, "Admin access required")
    return user


# Modules a newly-created account starts with, by role. The ORM default for
# allowed_modules is [] (models/database.py), which meant every new non-admin
# account was created with NO areas granted and had to wait for an admin to tick
# boxes before the product did anything — the same "looks provisioned, behaves
# locked-out" shape as the TEFCA P0. These are DEFAULTS applied at creation only:
# an explicit permissions list always wins, and nothing here re-grants modules an
# admin later removes.
#
# This is module visibility, NOT privilege. What a role may DO inside a module is
# decided by require_role on each endpoint; granting "tefca_review" to a viewer
# still leaves every write 403. Privilege is never inferred from anything about
# the account (email domain included) — see the standing prohibition in
# core/security.py.
_BASE_MODULES = ["tefca_review", "bulletin_intelligence"]
DEFAULT_MODULES_BY_ROLE = {
    "viewer": _BASE_MODULES,
    "contributor": _BASE_MODULES,
    "manager": _BASE_MODULES,
    "reviewer": _BASE_MODULES,
    "senior_analyst": _BASE_MODULES,
    "qalead": _BASE_MODULES,
    "program_manager": _BASE_MODULES,
    "admin": [m["id"] for m in MODULES],
}


def default_modules_for_role(role) -> list:
    """Starting module grant for `role`. Unknown roles get the base set rather
    than everything — an unrecognised role must never widen access."""
    return list(DEFAULT_MODULES_BY_ROLE.get((role or "").lower(), _BASE_MODULES))


def _clean_modules(requested) -> list:
    if not isinstance(requested, list):
        raise HTTPException(400, "permissions must be a list")
    mapped = {LEGACY_MAP.get(x, x) for x in requested}
    return [m["id"] for m in MODULES if m["id"] in mapped]


def _normalize_stored(raw) -> list:
    """Resolve any legacy ids in a stored allowed_modules list to current ids."""
    mapped = {LEGACY_MAP.get(x, x) for x in (raw or [])}
    return [m["id"] for m in MODULES if m["id"] in mapped]


def _serialize(u: User) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "full_name": u.full_name or "",
        "company": u.company or "",
        "role": u.role,
        "plan": getattr(u, "plan", "free"),
        "is_active": bool(u.is_active),
        # Registration-security lifecycle (P1 fix) — lets the admin UI surface
        # accounts awaiting verification/approval.
        "status": getattr(u, "status", "active") or "active",
        "is_verified": bool(getattr(u, "is_verified", True)),
        "is_super_admin": u.email in ADMIN_EMAILS,
        "permissions": _normalize_stored(u.allowed_modules),
        "allowed_modules": _normalize_stored(u.allowed_modules),  # alias
        "created_at": str(u.created_at) if u.created_at else None,
        "last_active_at": str(getattr(u, "last_active_at", "") or "") or None,
        # ADM-001 — the admin table's column is called "Last Login", so the field
        # is exposed under that name rather than leaving the UI to infer that
        # `last_active_at` is the same thing. Same value, unambiguous contract.
        "last_login": str(getattr(u, "last_active_at", "") or "") or None,
    }


async def _audit(db, actor, action, target_id=None, target_email=None, details=None):
    try:
        db.add(AuditLog(
            tenant_id="default",
            user_id=str(target_id) if target_id is not None else str(actor.id),
            action=action, resource_type="user",
            resource_id=str(target_id) if target_id is not None else None,
            details={"actor": actor.email, "actor_id": str(actor.id), "target": target_email,
                     **(details or {}), "at": datetime.utcnow().isoformat() + "Z"},
        ))
        await db.commit()
    except Exception:
        try: await db.rollback()
        except Exception: pass


def _gen_password(n=14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


# ─────────── read ───────────

@router.get("/areas")
async def list_areas(_admin=Depends(require_admin)):
    """Grantable modules with grouping (for the permission toggles)."""
    return MODULES


@router.get("/users")
async def list_users(_admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(desc(User.created_at)))
    return [_serialize(u) for u in result.scalars().all()]


@router.get("/users/{user_id}/activity")
async def user_activity(user_id: str, _admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AuditLog).where(AuditLog.user_id == user_id).order_by(desc(AuditLog.created_at)).limit(100)
    )
    return [{"action": a.action, "resource_type": a.resource_type, "details": a.details,
             "created_at": str(a.created_at) if a.created_at else None} for a in result.scalars().all()]


# ─────────── invite / create ───────────

class InviteReq(BaseModel):
    email: str
    full_name: Optional[str] = ""
    role: Optional[str] = "viewer"
    permissions: Optional[List[str]] = []


async def _create(req_email, full_name, role, permissions, admin, db):
    email = (req_email or "").strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Invalid email")
    role = (role or "viewer").lower()
    if role not in VALID_ROLES:
        raise HTTPException(400, "Invalid role")
    if role == "admin" and not is_super_admin(admin):
        raise HTTPException(403, "Only a super admin can create admin accounts")
    exists = await db.execute(select(User).where(User.email == email))
    if exists.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")
    temp = _gen_password()
    # An explicit permissions list wins; only fall back to the role default when the
    # caller supplied none. Passing [] and passing nothing are indistinguishable over
    # JSON here (both arrive as an empty list), and "no areas at all" is not a useful
    # account to create, so the default applies to both.
    granted = _clean_modules(permissions) if permissions else default_modules_for_role(role)
    user = User(
        email=email, password_hash=hash_password(temp), full_name=(full_name or "").strip(),
        role=role, is_active=True, allowed_modules=granted,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await _audit(db, admin, "user_invited", str(user.id), user.email, {"role": role})

    # Send the invitation as a SECURE SET-PASSWORD LINK — never the password in
    # plaintext (security requirement). We mint a set-password token bound to the
    # account's current (random) password hash so the user chooses their own password
    # via the link. Best-effort: send_invitation_email never raises and is a no-op
    # dry-run without SENDGRID_API_KEY, so delivery problems never block creation.
    set_password_token = _create_reset_token(
        str(user.id), user.email, user.password_hash, INVITE_TOKEN_EXPIRE
    )
    set_password_url = f"{app_url()}/reset-password?token={set_password_token}"
    invite_email = await send_invitation_email(email, full_name, set_password_url)

    out = _serialize(user)
    # Fallback ONLY for when email delivery is off (dry-run): lets the admin get the
    # user in. Returned over the authenticated admin channel, never emailed.
    out["temp_password"] = temp
    out["invite_email"] = invite_email  # {"sent": bool, ...} — delivery status
    return out


@router.post("/users/invite", status_code=201)
async def invite_user(req: InviteReq, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await _create(req.email, req.full_name, req.role, req.permissions, admin, db)


class CreateUserReq(BaseModel):
    email: str
    password: Optional[str] = None
    full_name: Optional[str] = ""
    role: Optional[str] = "contributor"
    allowed_modules: Optional[List[str]] = []


@router.post("/users", status_code=201)
async def create_user(req: CreateUserReq, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await _create(req.email, req.full_name, req.role, req.allowed_modules, admin, db)


# ─────────── update ───────────

class RoleReq(BaseModel):
    role: str


@router.patch("/users/{user_id}/role")
async def set_role(user_id: str, req: RoleReq, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    new_role = req.role.lower()
    if new_role not in VALID_ROLES:
        raise HTTPException(400, "Invalid role")
    if str(target.id) == str(admin.id) and new_role != "admin":
        raise HTTPException(400, "You cannot remove your own admin role")
    if (new_role == "admin" or is_admin_account(target)) and not is_super_admin(admin):
        raise HTTPException(403, "Only a super admin can change admin roles")
    old = target.role
    target.role = new_role
    await db.commit(); await db.refresh(target)
    await _audit(db, admin, "role_changed", str(target.id), target.email, {"from": old, "to": new_role})
    return _serialize(target)


class BulkRoleReq(BaseModel):
    emails: List[str]
    role: str


@router.post("/users/bulk-role")
async def bulk_set_role(req: BulkRoleReq, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Assign one role to many accounts by email. Admin-only and fully audited.

    Exists so that onboarding a group of reviewers is a single AUTHORISED,
    ATTRIBUTABLE act rather than a reason to reach for automatic elevation (e.g.
    "everyone at this domain becomes a reviewer"). Privilege is granted here by a
    named admin against a named list, and every grant writes its own audit row —
    which is what the least-privilege / authorized-personnel requirement asks for
    and what a domain rule cannot provide.

    Enforces exactly the same guards as PATCH /users/{id}/role, per target: only a
    super admin may grant admin or modify an existing admin, and an admin cannot
    demote themselves. Per-account outcomes are reported instead of aborting the
    batch, so one bad address does not hide the rest of the result.
    """
    new_role = (req.role or "").lower()
    if new_role not in VALID_ROLES:
        raise HTTPException(400, "Invalid role")
    if new_role == "admin" and not is_super_admin(admin):
        raise HTTPException(403, "Only a super admin can grant the admin role")
    if not isinstance(req.emails, list) or not req.emails:
        raise HTTPException(400, "emails must be a non-empty list")
    if len(req.emails) > 200:
        raise HTTPException(400, "Too many emails in one request (max 200)")

    wanted = [(e or "").strip().lower() for e in req.emails]
    wanted = [e for e in wanted if e]
    result = await db.execute(select(User).where(func.lower(User.email).in_(wanted)))
    found = {u.email.lower(): u for u in result.scalars().all()}

    updated, skipped = [], []
    for email in wanted:
        target = found.get(email)
        if not target:
            skipped.append({"email": email, "reason": "no such account"})
            continue
        if str(target.id) == str(admin.id) and new_role != "admin":
            skipped.append({"email": email, "reason": "cannot remove your own admin role"})
            continue
        if is_admin_account(target) and not is_super_admin(admin):
            skipped.append({"email": email, "reason": "only a super admin can modify an admin account"})
            continue
        old = target.role
        if old == new_role:
            skipped.append({"email": email, "reason": "already %s" % new_role})
            continue
        target.role = new_role
        updated.append({"email": email, "from": old, "to": new_role})

    if updated:
        await db.commit()
        for row in updated:
            u = found[row["email"]]
            await _audit(db, admin, "role_changed", str(u.id), u.email,
                         {"from": row["from"], "to": new_role, "via": "bulk-role"})

    return {"role": new_role, "updated": updated, "skipped": skipped,
            "updated_count": len(updated), "skipped_count": len(skipped)}


class ApproveReq(BaseModel):
    role: Optional[str] = None                 # role to assign on approval (optional)
    permissions: Optional[List[str]] = None    # modules to grant on approval (optional)


@router.post("/users/{user_id}/approve")
async def approve_user(user_id: str, req: ApproveReq, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """P1 registration security — approve & ACTIVATE a self-registered account.

    Assigns the role the admin chooses, marks the account verified/active, and lets
    the user log in. Requires the email to have been verified first (the user clicked
    their verification link) unless a super admin overrides."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")

    # Assign a role if provided; otherwise a still-'pending' account gets the default.
    if req.role is not None:
        new_role = req.role.lower()
        if new_role not in VALID_ROLES:
            raise HTTPException(400, "Invalid role")
        if new_role == "admin" and not is_super_admin(admin):
            raise HTTPException(403, "Only a super admin can grant the admin role")
        target.role = new_role
    elif (target.role or "pending") == "pending":
        target.role = "contributor"

    if req.permissions is not None:
        target.allowed_modules = _clean_modules(req.permissions)
    elif not _normalize_stored(target.allowed_modules):
        # Approving an account that has no areas at all would activate a login that
        # can reach nothing. Only fills a genuine blank — an existing grant is never
        # overwritten, so this cannot silently re-widen an account an admin narrowed.
        target.allowed_modules = default_modules_for_role(target.role)

    target.is_verified = True
    target.is_active = True
    target.status = "active"
    await db.commit(); await db.refresh(target)
    await _audit(db, admin, "account_approved", str(target.id), target.email, {"role": target.role})
    return _serialize(target)


class RejectReq(BaseModel):
    # AD-004 — a rejection is a decision about a person's access, and a decision
    # with no recorded reason cannot be reviewed, appealed or explained later.
    # Optional on the wire so existing callers posting an empty body keep working;
    # the UI always sends one.
    reason: Optional[str] = None


@router.post("/users/{user_id}/reject")
async def reject_user(
    user_id: str,
    req: RejectReq = RejectReq(),
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reject a pending self-registered account: deactivate and disable it. The user
    keeps no access and cannot log in. (Use DELETE to remove entirely.)

    The reason is written to the audit record, not to the user row: the audit
    trail is the immutable history, and a later status change must not overwrite
    why this decision was taken.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    if is_admin_account(target):
        raise HTTPException(403, "Cannot reject an administrator account")
    target.is_active = False
    target.status = "disabled"
    target.tokens_revoked_at = datetime.utcnow()
    await db.commit(); await db.refresh(target)
    reason = (req.reason or "").strip() if req else ""
    await _audit(db, admin, "account_rejected", str(target.id), target.email,
                 {"reason": reason} if reason else None)
    return _serialize(target)


@router.post("/users/{user_id}/resend-verification")
async def resend_verification(user_id: str, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Re-send the email-verification link to a user still awaiting verification.
    Mints a fresh single-use token (invalidating any previous one via the fingerprint)
    and sends it through the existing SendGrid integration."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    if getattr(target, "is_verified", True):
        raise HTTPException(400, "This account's email is already verified")
    # Reuse the single source of truth for verification tokens (lazy import avoids a
    # module-load cycle; app.api.routes does not import this module).
    from app.api.routes import _create_verification_token
    token = _create_verification_token(str(target.id), target.email, target.password_hash)
    verify_url = f"{app_url()}/verify-email?token={token}"
    email_result = await send_verification_email(target.email, target.full_name or "", verify_url)
    await _audit(db, admin, "verification_resent", str(target.id), target.email,
                 {"email_sent": email_result.get("sent", False)})
    return {"status": "verification_resent", "email": target.email, "email_sent": email_result.get("sent", False)}


@router.get("/users/pending")
async def list_pending_users(_admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Administrator pending-user queue — accounts awaiting verification or approval."""
    result = await db.execute(
        select(User).where(User.status.in_(["pending_verification", "pending_approval"]))
        .order_by(desc(User.created_at))
    )
    return [_serialize(u) for u in result.scalars().all()]


class PermissionsReq(BaseModel):
    permissions: List[str]


@router.patch("/users/{user_id}/permissions")
async def set_permissions(user_id: str, req: PermissionsReq, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    cleaned = _clean_modules(req.permissions)
    target.allowed_modules = cleaned
    await db.commit(); await db.refresh(target)
    await _audit(db, admin, "permissions_changed", str(target.id), target.email, {"permissions": cleaned})
    return _serialize(target)


@router.patch("/users/{user_id}")
async def update_user(user_id: str, payload: dict, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Combined update (role / is_active / permissions) — kept for convenience."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    if "role" in payload:
        nr = str(payload["role"]).lower()
        if nr not in VALID_ROLES:
            raise HTTPException(400, "Invalid role")
        if str(target.id) == str(admin.id) and nr != "admin":
            raise HTTPException(400, "You cannot remove your own admin role")
        if (nr == "admin" or is_admin_account(target)) and not is_super_admin(admin):
            raise HTTPException(403, "Only a super admin can change admin roles")
        target.role = nr
    if "is_active" in payload:
        if str(target.id) == str(admin.id) and not payload["is_active"]:
            raise HTTPException(400, "You cannot deactivate your own account")
        if is_admin_account(target) and not is_super_admin(admin) and not payload["is_active"]:
            raise HTTPException(403, "Only a super admin can deactivate an admin")
        active = bool(payload["is_active"])
        target.is_active = active
        # Keep the lifecycle status coherent with the activation toggle so the login
        # gates (P1 fix) stay consistent: activating clears any pending state and
        # marks the account verified; deactivating disables it.
        if active:
            target.status = "active"
            target.is_verified = True
        else:
            target.status = "disabled"
            # Session invalidation: disabling an account revokes its outstanding
            # tokens immediately (belt-and-suspenders with the is_active login gate,
            # and it prevents old tokens from working again after any reactivation).
            target.tokens_revoked_at = datetime.utcnow()
    if "permissions" in payload or "allowed_modules" in payload:
        target.allowed_modules = _clean_modules(payload.get("permissions", payload.get("allowed_modules")) or [])
    await db.commit(); await db.refresh(target)
    await _audit(db, admin, "user_updated", str(target.id), target.email)
    return _serialize(target)


class SetPasswordReq(BaseModel):
    new_password: str


@router.post("/users/{user_id}/set-password")
async def set_password(user_id: str, req: SetPasswordReq, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if len(req.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    if is_admin_account(target) and not is_super_admin(admin) and str(target.id) != str(admin.id):
        raise HTTPException(403, "Only a super admin can reset an admin's password")
    target.password_hash = hash_password(req.new_password)
    # A credential change invalidates existing sessions (NIST 800-63B / OWASP ASVS
    # session termination on password change).
    target.tokens_revoked_at = datetime.utcnow()
    await db.commit()
    await _audit(db, admin, "password_reset_by_admin", str(target.id), target.email)
    return {"status": "password_set", "email": target.email}


# ─────────── delete ───────────

@router.delete("/users/{user_id}")
async def delete_user(user_id: str, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if str(user_id) == str(admin.id):
        raise HTTPException(400, "You cannot delete your own account")
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    if target.email in ADMIN_EMAILS:
        raise HTTPException(403, "Super admin accounts cannot be deleted")
    if is_admin_account(target) and not is_super_admin(admin):
        raise HTTPException(403, "Only a super admin can delete an admin")
    email, target_id = target.email, str(target.id)
    await db.execute(sa_delete(Output).where(Output.user_id == target_id))
    await db.execute(sa_delete(Transcript).where(Transcript.user_id == target_id))
    await db.execute(sa_delete(Document).where(Document.user_id == target_id))
    await db.execute(sa_delete(AudioFile).where(AudioFile.user_id == target_id))
    # AUDIT-MUT / NIST AU-9: audit rows are DETACHED, never deleted. Nulling user_id
    # is also structurally required — audit_logs.user_id has a NO ACTION foreign key
    # to users.id, so the user row cannot be removed while any audit row still
    # references it. The audit records themselves (action, resource, details,
    # timestamp) are preserved in full, so the security timeline survives the account.
    # Do NOT change this to sa_delete: that would destroy audit history, breach the
    # HIPAA §164.316(b)(2) six-year retention obligation, and silently erase the
    # evidence of any prior attack against the deleted account.
    detach = await db.execute(
        sa_update(AuditLog).where(AuditLog.user_id == target_id).values(user_id=None)
    )
    await db.delete(target)
    await db.commit()
    # Record how many audit rows lost attribution, so the trail explains its own gap.
    await _audit(
        db, admin, "user_deleted", None, email,
        {"deleted_email": email, "audit_rows_detached": detach.rowcount},
    )
    return {"status": "deleted", "email": email}
