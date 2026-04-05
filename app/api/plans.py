"""
Plan Management API
GET /api/plan/usage — current usage and limits
GET /api/plan/info — plan details and pricing
POST /api/plan/upgrade — change plan (placeholder for payment integration)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.database import User
from app.services.plan_enforcement import (
    get_usage, PLAN_LIMITS, PLAN_PRICES, PLAN_NAMES
)
from pydantic import BaseModel

router = APIRouter(prefix="/api/plan", tags=["Plan & Usage"])


@router.get("/usage")
async def get_plan_usage(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get current month's usage and remaining limits for the user's plan."""
    plan = getattr(user, 'plan', 'free') or 'free'
    usage = await get_usage(db, user.id, plan)
    return usage


@router.get("/info")
async def get_plan_info():
    """Get all available plans with pricing and features."""
    plans = []
    for plan_id in ["free", "pro", "business", "enterprise"]:
        limits = PLAN_LIMITS[plan_id]
        prices = PLAN_PRICES[plan_id]
        plans.append({
            "id": plan_id,
            "name": PLAN_NAMES[plan_id],
            "price_monthly": prices["monthly"],
            "price_annual": prices["annual"],
            "ai_actions_per_month": limits["ai_actions_per_month"] if limits["ai_actions_per_month"] < 999999 else "Unlimited",
            "transcriptions_per_month": limits["transcriptions_per_month"] if limits["transcriptions_per_month"] < 999999 else "Unlimited",
            "storage": _format_storage(limits["storage_bytes"]),
            "export_enabled": limits["export_enabled"],
            "custom_templates": limits["custom_templates"],
            "api_access": limits["api_access"],
        })
    return {"plans": plans}


class UpgradeRequest(BaseModel):
    plan: str  # pro, business, enterprise


@router.post("/upgrade")
async def upgrade_plan(
    data: UpgradeRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Upgrade user's plan.
    In production: integrate with Stripe/payment processor.
    For now: directly updates the plan in the database.
    """
    valid_plans = ["free", "pro", "business", "enterprise"]
    if data.plan not in valid_plans:
        raise HTTPException(400, f"Invalid plan. Choose from: {', '.join(valid_plans)}")

    current_plan = getattr(user, 'plan', 'free') or 'free'
    if data.plan == current_plan:
        raise HTTPException(400, f"You are already on the {PLAN_NAMES[data.plan]} plan")

    # Update plan in database
    await db.execute(
        update(User).where(User.id == user.id).values(plan=data.plan)
    )
    await db.commit()

    return {
        "message": f"Plan upgraded to {PLAN_NAMES[data.plan]}",
        "previous_plan": current_plan,
        "new_plan": data.plan,
        "new_limits": PLAN_LIMITS[data.plan],
        "note": "In production, this would process payment via Stripe before upgrading.",
    }


def _format_storage(b):
    if b >= 999999 * 1024 * 1024 * 1024:
        return "Unlimited"
    elif b >= 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024 * 1024):.0f} GB"
    else:
        return f"{b / (1024 * 1024):.0f} MB"
