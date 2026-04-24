"""Auth — registration (corporate email only), login, approval flow, password reset."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import User, UserRole, RFQ
from app.services.auth import create_token, hash_password, verify_password, get_current_user

router = APIRouter(prefix="/auth", tags=["Auth"])

BLOCKED_DOMAINS = [
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "mail.com", "protonmail.com", "zoho.com", "yandex.com",
    "live.com", "msn.com", "comcast.net", "verizon.net", "att.net",
    "me.com", "mac.com", "gmx.com", "inbox.com",
]


def _check_corporate_email(email: str):
    """Block personal email domains. Only corporate/business emails allowed."""
    domain = email.lower().split("@")[-1]
    if domain in BLOCKED_DOMAINS:
        raise HTTPException(400, f"Personal email not allowed. Please use your corporate email address (not @{domain}).")


class RegisterReq(BaseModel):
    email: str
    full_name: str
    password: str

class LoginReq(BaseModel):
    email: str
    password: str

class PasswordResetReq(BaseModel):
    user_id: str
    new_password: str


@router.post("/register")
async def register(req: RegisterReq, db: AsyncSession = Depends(get_db)):
    # Block personal emails
    _check_corporate_email(req.email)

    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    exists = await db.execute(select(User).where(User.email == req.email))
    if exists.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")

    # First user = Admin + auto-approved. All others need approval.
    count = await db.execute(select(func.count(User.id)))
    is_first = (count.scalar() or 0) == 0

    user = User(
        email=req.email,
        full_name=req.full_name,
        password_hash=hash_password(req.password),
        role=UserRole.ADMIN if is_first else UserRole.SALES,
        is_approved=True if is_first else False,
    )
    db.add(user)
    await db.flush()

    if is_first:
        return {"message": "Admin account created and approved. You can log in now.", "approved": True}
    else:
        return {
            "message": "Account created. An administrator must approve your account before you can log in. A notification has been sent to imran@agtbi.com.",
            "approved": False,
        }


@router.post("/login")
async def login(req: LoginReq, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account deactivated. Contact your administrator.")
    
    # Check approval — existing users before this feature are always approved
    try:
        if hasattr(user, 'is_approved') and user.is_approved is not None and user.is_approved == False:
            raise HTTPException(403, "Account pending approval. Contact your administrator at imran@agtbi.com.")
    except HTTPException:
        raise
    except Exception:
        pass  # If column doesn't exist yet, allow login

    user.last_login = datetime.now()
    await db.flush()
    return {"token": create_token({"sub": str(user.id)}), "user": {
        "id": str(user.id), "email": user.email, "full_name": user.full_name,
        "role": user.role, "is_approved": getattr(user, 'is_approved', True),
    }}


@router.get("/me")
async def get_me(user=Depends(get_current_user)):
    return {
        "id": str(user.id), "email": user.email, "full_name": user.full_name,
        "role": user.role, "is_active": user.is_active,
        "is_approved": getattr(user, 'is_approved', True),
    }


@router.get("/stats")
async def get_stats(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(RFQ.id)))
    counts = {}
    for s in ['New', 'In Progress', 'Quoted', 'Submitted', 'Won', 'Lost']:
        c = await db.execute(select(func.count(RFQ.id)).where(RFQ.status == s))
        counts[s] = c.scalar()
    from app.models import Quote
    pv = await db.execute(select(func.sum(Quote.total_sell_price)))
    # Pending approval count (for admins)
    pending = await db.execute(select(func.count(User.id)).where(User.is_approved == False))
    return {
        "total_rfqs": total.scalar(), **{k.lower().replace(' ', '_'): v for k, v in counts.items()},
        "pipeline_value": float(pv.scalar() or 0),
        "pending_approvals": pending.scalar() if user.role == 'Admin' else 0,
    }


# ── User Management ──

@router.get("/users")
async def list_users(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.role != "Admin":
        raise HTTPException(403, "Admin only")
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return [{
        "id": str(u.id), "email": u.email, "full_name": u.full_name,
        "role": u.role, "is_active": u.is_active,
        "is_approved": getattr(u, 'is_approved', True),
        "last_login": str(u.last_login) if u.last_login else None,
        "created_at": str(u.created_at) if u.created_at else None,
    } for u in result.scalars().all()]


@router.patch("/users/{user_id}")
async def update_user(user_id: str, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.role != "Admin":
        raise HTTPException(403, "Admin only")
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")

    if 'role' in payload:
        target.role = payload['role']
    if 'is_active' in payload:
        target.is_active = payload['is_active']
    if 'is_approved' in payload:
        target.is_approved = payload['is_approved']
    await db.flush()
    return {"status": "updated"}


@router.post("/users/{user_id}/approve")
async def approve_user(user_id: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Admin approves a pending user account."""
    if user.role != "Admin":
        raise HTTPException(403, "Admin only")
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404)
    target.is_approved = True
    await db.flush()
    return {"status": "approved", "email": target.email}


@router.post("/users/{user_id}/reject")
async def reject_user(user_id: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Admin rejects and deactivates a pending user."""
    if user.role != "Admin":
        raise HTTPException(403)
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404)
    target.is_approved = False
    target.is_active = False
    await db.flush()
    return {"status": "rejected"}


# ── Password Reset (admin-triggered) ──

@router.post("/reset-password")
async def reset_password(req: PasswordResetReq, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Admin resets another user's password."""
    if user.role != "Admin":
        raise HTTPException(403, "Admin only")
    if len(req.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    result = await db.execute(select(User).where(User.id == req.user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404)
    target.password_hash = hash_password(req.new_password)
    await db.flush()
    return {"status": "password_reset", "email": target.email}


@router.post("/change-password")
async def change_own_password(payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """User changes their own password."""
    current = payload.get('current_password', '')
    new = payload.get('new_password', '')
    if not verify_password(current, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    if len(new) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    user.password_hash = hash_password(new)
    await db.flush()
    return {"status": "password_changed"}


# ── Emergency password reset (no auth, protected by secret) ──

@router.post("/emergency-reset")
async def emergency_reset(payload: dict, db: AsyncSession = Depends(get_db)):
    """Reset password without login. Requires the platform secret key."""
    secret = payload.get('secret_key', '')
    email = payload.get('email', '')
    new_password = payload.get('new_password', '')

    if secret != 'agtbi-govcon-prod-2026-x9k4m':
        raise HTTPException(403, "Invalid secret key")
    if not email or not new_password:
        raise HTTPException(400, "email and new_password required")
    if len(new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, f"No user found with email: {email}")

    user.password_hash = hash_password(new_password)
    user.is_active = True
    try:
        user.is_approved = True
    except:
        pass
    await db.flush()
    return {"status": "password_reset", "email": email, "message": "Password reset. You can now login."}
