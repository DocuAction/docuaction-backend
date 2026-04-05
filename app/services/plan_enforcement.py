"""
Plan Enforcement System
Tracks AI action usage per user and enforces plan limits.

Plan Limits:
  Free:       10 AI actions/month, 1 transcription, 500MB storage
  Pro:       100 AI actions/month, 10 transcriptions, 5GB storage
  Business:  500 AI actions/month, 50 transcriptions, 20GB storage
  Enterprise: Unlimited
"""
import logging
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.database import Output, AudioFile, Document

logger = logging.getLogger("docuaction.plans")

# ═══ PLAN DEFINITIONS ═══

PLAN_LIMITS = {
    "free": {
        "ai_actions_per_month": 10,
        "transcriptions_per_month": 1,
        "storage_bytes": 500 * 1024 * 1024,      # 500MB
        "max_file_size_bytes": 10 * 1024 * 1024,  # 10MB
        "export_enabled": False,
        "custom_templates": False,
        "api_access": False,
    },
    "pro": {
        "ai_actions_per_month": 100,
        "transcriptions_per_month": 10,
        "storage_bytes": 5 * 1024 * 1024 * 1024,  # 5GB
        "max_file_size_bytes": 25 * 1024 * 1024,   # 25MB
        "export_enabled": True,
        "custom_templates": False,
        "api_access": False,
    },
    "business": {
        "ai_actions_per_month": 500,
        "transcriptions_per_month": 50,
        "storage_bytes": 20 * 1024 * 1024 * 1024,  # 20GB
        "max_file_size_bytes": 50 * 1024 * 1024,    # 50MB
        "export_enabled": True,
        "custom_templates": True,
        "api_access": True,
    },
    "enterprise": {
        "ai_actions_per_month": 999999,
        "transcriptions_per_month": 999999,
        "storage_bytes": 999999 * 1024 * 1024 * 1024,
        "max_file_size_bytes": 100 * 1024 * 1024,
        "export_enabled": True,
        "custom_templates": True,
        "api_access": True,
    },
}

PLAN_PRICES = {
    "free": {"monthly": 0, "annual": 0},
    "pro": {"monthly": 25, "annual": 20},
    "business": {"monthly": 65, "annual": 52},
    "enterprise": {"monthly": None, "annual": None},
}

PLAN_NAMES = {
    "free": "Free",
    "pro": "Pro",
    "business": "Business",
    "enterprise": "Enterprise",
}


async def get_usage(db: AsyncSession, user_id, plan: str) -> dict:
    """Get current month's usage for a user."""
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)

    # Count AI actions this month
    result = await db.execute(
        select(func.count(Output.id)).where(
            Output.user_id == user_id,
            Output.created_at >= month_start,
        )
    )
    ai_actions_used = result.scalar() or 0

    # Count transcriptions this month
    result = await db.execute(
        select(func.count(AudioFile.id)).where(
            AudioFile.user_id == user_id,
            AudioFile.created_at >= month_start,
            AudioFile.status == "transcribed",
        )
    )
    transcriptions_used = result.scalar() or 0

    # Calculate storage used
    result = await db.execute(
        select(func.coalesce(func.sum(Document.file_size_bytes), 0)).where(
            Document.user_id == user_id,
        )
    )
    storage_used = result.scalar() or 0

    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    return {
        "plan": plan,
        "plan_name": PLAN_NAMES.get(plan, "Free"),
        "billing_period": f"{month_start.strftime('%B %Y')}",
        "ai_actions": {
            "used": ai_actions_used,
            "limit": limits["ai_actions_per_month"],
            "remaining": max(0, limits["ai_actions_per_month"] - ai_actions_used),
            "percentage": round((ai_actions_used / limits["ai_actions_per_month"]) * 100, 1) if limits["ai_actions_per_month"] < 999999 else 0,
        },
        "transcriptions": {
            "used": transcriptions_used,
            "limit": limits["transcriptions_per_month"],
            "remaining": max(0, limits["transcriptions_per_month"] - transcriptions_used),
        },
        "storage": {
            "used_bytes": storage_used,
            "used_display": _format_bytes(storage_used),
            "limit_bytes": limits["storage_bytes"],
            "limit_display": _format_bytes(limits["storage_bytes"]),
            "percentage": round((storage_used / limits["storage_bytes"]) * 100, 1) if limits["storage_bytes"] < 999999 * 1024 * 1024 * 1024 else 0,
        },
        "features": {
            "export_enabled": limits["export_enabled"],
            "custom_templates": limits["custom_templates"],
            "api_access": limits["api_access"],
        },
    }


async def check_ai_action_limit(db: AsyncSession, user_id, plan: str):
    """Check if user can perform an AI action. Raises 403 if over limit."""
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_actions = limits["ai_actions_per_month"]

    if max_actions >= 999999:
        return  # Enterprise — unlimited

    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)

    result = await db.execute(
        select(func.count(Output.id)).where(
            Output.user_id == user_id,
            Output.created_at >= month_start,
        )
    )
    used = result.scalar() or 0

    if used >= max_actions:
        plan_name = PLAN_NAMES.get(plan, "Free")
        logger.warning(f"PLAN LIMIT HIT | user={user_id} plan={plan} used={used}/{max_actions}")
        raise HTTPException(
            403,
            {
                "error": f"Monthly AI action limit reached ({used}/{max_actions} for {plan_name} plan)",
                "code": "PLAN_LIMIT_EXCEEDED",
                "used": used,
                "limit": max_actions,
                "plan": plan,
                "upgrade_url": "https://app.docuaction.io/pricing",
            }
        )


async def check_transcription_limit(db: AsyncSession, user_id, plan: str):
    """Check if user can transcribe audio. Raises 403 if over limit."""
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_transcriptions = limits["transcriptions_per_month"]

    if max_transcriptions >= 999999:
        return

    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)

    result = await db.execute(
        select(func.count(AudioFile.id)).where(
            AudioFile.user_id == user_id,
            AudioFile.created_at >= month_start,
            AudioFile.status == "transcribed",
        )
    )
    used = result.scalar() or 0

    if used >= max_transcriptions:
        raise HTTPException(
            403,
            {
                "error": f"Monthly transcription limit reached ({used}/{max_transcriptions})",
                "code": "TRANSCRIPTION_LIMIT_EXCEEDED",
                "used": used,
                "limit": max_transcriptions,
                "plan": plan,
                "upgrade_url": "https://app.docuaction.io/pricing",
            }
        )


def check_export_permission(plan: str):
    """Check if user's plan allows exports."""
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    if not limits["export_enabled"]:
        raise HTTPException(
            403,
            {
                "error": "Export is available on Pro plan and above",
                "code": "FEATURE_NOT_AVAILABLE",
                "plan": plan,
                "upgrade_url": "https://app.docuaction.io/pricing",
            }
        )


def _format_bytes(b):
    """Format bytes to human-readable string."""
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.1f} GB"
