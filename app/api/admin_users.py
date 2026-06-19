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
from sqlalchemy import select, desc, delete as sa_delete, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, ADMIN_EMAILS, hash_password
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
VALID_ROLES = {"admin", "manager", "contributor", "viewer"}


def is_super_admin(user) -> bool:
    return user.email in ADMIN_EMAILS


def is_admin_account(user) -> bool:
    return (getattr(user, "role", "") or "").lower() == "admin" or user.email in ADMIN_EMAILS


async def require_admin(user=Depends(get_current_user)):
    if (getattr(user, "role", "") or "").lower() != "admin" and user.email not in ADMIN_EMAILS:
        raise HTTPException(403, "Admin access required")
    return user


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
        "is_super_admin": u.email in ADMIN_EMAILS,
        "permissions": _normalize_stored(u.allowed_modules),
        "allowed_modules": _normalize_stored(u.allowed_modules),  # alias
        "created_at": str(u.created_at) if u.created_at else None,
        "last_active_at": str(getattr(u, "last_active_at", "") or "") or None,
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
    user = User(
        email=email, password_hash=hash_password(temp), full_name=(full_name or "").strip(),
        role=role, is_active=True, allowed_modules=_clean_modules(permissions or []),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await _audit(db, admin, "user_invited", str(user.id), user.email, {"role": role})
    out = _serialize(user)
    out["temp_password"] = temp  # no email service yet — return so admin can share it
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
        target.is_active = bool(payload["is_active"])
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
    await db.execute(sa_update(AuditLog).where(AuditLog.user_id == target_id).values(user_id=None))
    await db.delete(target)
    await db.commit()
    await _audit(db, admin, "user_deleted", None, email, {"deleted_email": email})
    return {"status": "deleted", "email": email}
