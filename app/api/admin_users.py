"""
Admin — User & Area Access Management.

Lets an admin list users, see the grantable areas, and control each user's
role / active status / allowed areas. Wired to the LIVE auth stack
(app/models/database.py User + app/core/security.py IAM).

Access is admin-only: either the user's role is "admin" or their email is in
the ADMIN_EMAILS allow-list (imran@agtbi.com, etc.), which core/security.py
auto-promotes to admin on first authenticated request.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, ADMIN_EMAILS
from app.models.database import User

router = APIRouter(prefix="/api/admin", tags=["Admin — Users"])


# Canonical list of grantable areas. The `id` MUST match the nav item ids in
# frontend/src/components/AppLayout.js so the sidebar can filter by them.
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


async def require_admin(user=Depends(get_current_user)):
    """Allow only admins (by role or by the ADMIN_EMAILS allow-list)."""
    if getattr(user, "role", "") != "admin" and user.email not in ADMIN_EMAILS:
        raise HTTPException(403, "Admin access required")
    return user


def _serialize(u: User) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "full_name": u.full_name or "",
        "company": u.company or "",
        "role": u.role,
        "plan": getattr(u, "plan", "free"),
        "is_active": bool(u.is_active),
        "allowed_modules": list(u.allowed_modules or []),
        "created_at": str(u.created_at) if u.created_at else None,
    }


@router.get("/areas")
async def list_areas(_admin=Depends(require_admin)):
    """Return the list of areas an admin can grant to users."""
    return AREAS


@router.get("/users")
async def list_users(_admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return [_serialize(u) for u in result.scalars().all()]


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    payload: dict,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update a user's role, active status, and/or allowed areas."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")

    if "role" in payload:
        role = str(payload["role"]).lower()
        if role not in {"admin", "manager", "contributor", "viewer"}:
            raise HTTPException(400, "Invalid role")
        # Don't let an admin demote themselves out of admin by accident.
        if str(target.id) == str(admin.id) and role != "admin":
            raise HTTPException(400, "You cannot remove your own admin role")
        target.role = role

    if "is_active" in payload:
        if str(target.id) == str(admin.id) and not payload["is_active"]:
            raise HTTPException(400, "You cannot deactivate your own account")
        target.is_active = bool(payload["is_active"])

    if "allowed_modules" in payload:
        requested = payload["allowed_modules"] or []
        if not isinstance(requested, list):
            raise HTTPException(400, "allowed_modules must be a list")
        # Keep only known area ids, de-duplicated, in canonical order.
        target.allowed_modules = [a["id"] for a in AREAS if a["id"] in set(requested)]

    await db.commit()
    await db.refresh(target)
    return _serialize(target)
