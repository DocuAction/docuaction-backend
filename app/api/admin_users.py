"""
Admin — User & Area Access Management (role-level security).

Two tiers:
  - SUPER ADMIN  : emails in core.security.ADMIN_EMAILS (e.g. admin@docuaction.io,
                   imran@agtbi.com). Can do everything, including creating/deleting
                   other admins and changing anyone's role.
  - ADMIN        : role == "admin". Can manage NON-admin users (create, edit areas,
                   reset password, deactivate) but cannot create/delete/▲-promote
                   admins — only a super admin can touch admin accounts.

Every mutating action is written to audit_logs for the per-user activity trail.
Wired to the LIVE stack: app/models/database.py User + app/core/security.py IAM.
"""
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

# Canonical grantable areas — ids MUST match the frontend nav item ids.
AREAS = [
    {"id": "actions",        "label": "Action Center"},
    {"id": "validation",     "label": "Validation Queue"},
    {"id": "decisions",      "label": "Decision Bank"},
    {"id": "meetings",       "label": "Meetings"},
    {"id": "healthcare",     "label": "Healthcare Claims"},
    {"id": "casemanagement", "label": "Case Management"},
    {"id": "bulletin",       "label": "Bulletin Intelligence"},
    {"id": "tefca",          "label": "TEFCA Review"},
    {"id": "opportunities",  "label": "Opportunities"},
    {"id": "analytics",      "label": "Analytics"},
    {"id": "trust",          "label": "Trust Center"},
]
_VALID_AREA_IDS = {a["id"] for a in AREAS}
VALID_ROLES = {"admin", "manager", "contributor", "viewer"}


def is_super_admin(user) -> bool:
    return user.email in ADMIN_EMAILS


def is_admin_account(user) -> bool:
    """Is the target an admin (by role or super-admin email)?"""
    return (getattr(user, "role", "") or "").lower() == "admin" or user.email in ADMIN_EMAILS


async def require_admin(user=Depends(get_current_user)):
    """Any admin (role 'admin' or a super-admin email)."""
    if (getattr(user, "role", "") or "").lower() != "admin" and user.email not in ADMIN_EMAILS:
        raise HTTPException(403, "Admin access required")
    return user


def _clean_areas(requested) -> list:
    if not isinstance(requested, list):
        raise HTTPException(400, "allowed_modules must be a list")
    return [a["id"] for a in AREAS if a["id"] in set(requested)]


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
        "allowed_modules": list(u.allowed_modules or []),
        "created_at": str(u.created_at) if u.created_at else None,
        "last_active_at": str(getattr(u, "last_active_at", "") or "") or None,
    }


async def _audit(db: AsyncSession, actor, action: str, target_id=None, target_email=None, details: dict | None = None):
    """Write an admin action to the activity trail in its OWN transaction.

    Best-effort: must run AFTER the main change is committed, and must never be
    able to break the action — so it commits separately and swallows errors
    (e.g. if the audit_logs table is briefly unavailable).
    """
    try:
        entry = AuditLog(
            tenant_id="default",
            user_id=str(target_id) if target_id is not None else str(actor.id),
            action=action,
            resource_type="user",
            resource_id=str(target_id) if target_id is not None else None,
            details={
                "actor": actor.email,
                "actor_id": str(actor.id),
                "target": target_email,
                **(details or {}),
                "at": datetime.utcnow().isoformat() + "Z",
            },
        )
        db.add(entry)
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass


# ─────────────────────────── read ───────────────────────────

@router.get("/areas")
async def list_areas(_admin=Depends(require_admin)):
    return AREAS


@router.get("/users")
async def list_users(_admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(desc(User.created_at)))
    return [_serialize(u) for u in result.scalars().all()]


@router.get("/users/{user_id}/activity")
async def user_activity(user_id: str, _admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Recent activity / audit trail for one user."""
    result = await db.execute(
        select(AuditLog).where(AuditLog.user_id == user_id).order_by(desc(AuditLog.created_at)).limit(100)
    )
    return [{
        "action": a.action,
        "resource_type": a.resource_type,
        "details": a.details,
        "ip_address": a.ip_address,
        "created_at": str(a.created_at) if a.created_at else None,
    } for a in result.scalars().all()]


# ─────────────────────────── create ───────────────────────────

class CreateUserReq(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = ""
    company: Optional[str] = ""
    role: Optional[str] = "contributor"
    allowed_modules: Optional[List[str]] = []
    is_active: Optional[bool] = True


@router.post("/users", status_code=201)
async def create_user(req: CreateUserReq, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    email = req.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Invalid email")
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    role = (req.role or "contributor").lower()
    if role not in VALID_ROLES:
        raise HTTPException(400, "Invalid role")
    # Role-level security: only a super admin may create an admin.
    if role == "admin" and not is_super_admin(admin):
        raise HTTPException(403, "Only a super admin can create admin accounts")

    exists = await db.execute(select(User).where(User.email == email))
    if exists.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")

    user = User(
        email=email,
        password_hash=hash_password(req.password),
        full_name=(req.full_name or "").strip(),
        company=(req.company or "").strip(),
        role=role,
        is_active=bool(req.is_active),
        allowed_modules=_clean_areas(req.allowed_modules or []),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await _audit(db, admin, "user_created", str(user.id), user.email, {"role": role})
    return _serialize(user)


# ─────────────────────────── update ───────────────────────────

@router.patch("/users/{user_id}")
async def update_user(user_id: str, payload: dict, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")

    changes = {}

    if "role" in payload:
        new_role = str(payload["role"]).lower()
        if new_role not in VALID_ROLES:
            raise HTTPException(400, "Invalid role")
        if str(target.id) == str(admin.id) and new_role != "admin":
            raise HTTPException(400, "You cannot remove your own admin role")
        # Role-level security: only a super admin may grant or revoke admin,
        # or change the role of an existing admin account.
        if (new_role == "admin" or is_admin_account(target)) and not is_super_admin(admin):
            raise HTTPException(403, "Only a super admin can change admin roles")
        changes["role"] = {"from": target.role, "to": new_role}
        target.role = new_role

    if "is_active" in payload:
        if str(target.id) == str(admin.id) and not payload["is_active"]:
            raise HTTPException(400, "You cannot deactivate your own account")
        if is_admin_account(target) and not is_super_admin(admin) and not payload["is_active"]:
            raise HTTPException(403, "Only a super admin can deactivate an admin")
        changes["is_active"] = bool(payload["is_active"])
        target.is_active = bool(payload["is_active"])

    if "allowed_modules" in payload:
        cleaned = _clean_areas(payload["allowed_modules"] or [])
        changes["allowed_modules"] = cleaned
        target.allowed_modules = cleaned

    if "full_name" in payload:
        target.full_name = str(payload["full_name"]).strip()
        changes["full_name"] = target.full_name

    await db.commit()
    await db.refresh(target)
    await _audit(db, admin, "user_updated", str(target.id), target.email, {"changes": changes})
    return _serialize(target)


# ─────────────────────────── password ───────────────────────────

class SetPasswordReq(BaseModel):
    new_password: str


@router.post("/users/{user_id}/set-password")
async def set_password(user_id: str, req: SetPasswordReq, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Admin sets a user's password (e.g. after a lockout)."""
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


# ─────────────────────────── delete ───────────────────────────

@router.delete("/users/{user_id}")
async def delete_user(user_id: str, admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if str(user_id) == str(admin.id):
        raise HTTPException(400, "You cannot delete your own account")
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    # Role-level security: only a super admin may delete an admin; super-admin
    # (allow-listed) accounts can never be deleted via the API.
    if target.email in ADMIN_EMAILS:
        raise HTTPException(403, "Super admin accounts cannot be deleted")
    if is_admin_account(target) and not is_super_admin(admin):
        raise HTTPException(403, "Only a super admin can delete an admin")
    email = target.email
    target_id = str(target.id)
    # FK-safe delete: remove the user's owned content (outputs/transcripts before
    # their parent documents/audio), unlink — but keep — their audit history, then
    # delete the user. Without this the FKs to users.id block the delete.
    await db.execute(sa_delete(Output).where(Output.user_id == target_id))
    await db.execute(sa_delete(Transcript).where(Transcript.user_id == target_id))
    await db.execute(sa_delete(Document).where(Document.user_id == target_id))
    await db.execute(sa_delete(AudioFile).where(AudioFile.user_id == target_id))
    await db.execute(sa_update(AuditLog).where(AuditLog.user_id == target_id).values(user_id=None))
    await db.delete(target)
    await db.commit()
    # Log under the actor (the deleted user's id no longer satisfies the FK).
    await _audit(db, admin, "user_deleted", None, email, {"deleted_email": email, "deleted_id": target_id})
    return {"status": "deleted", "email": email}
