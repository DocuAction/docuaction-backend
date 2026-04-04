"""
Admin Endpoints — Retention Policy + System Status
"""
from fastapi import APIRouter, Depends
from app.core.security import require_role, get_current_user
from app.services.retention_worker import run_retention_cleanup, get_retention_config

router = APIRouter(prefix="/api/admin", tags=["Admin"])


@router.get("/retention/config")
async def retention_config(user=Depends(get_current_user)):
    """Get current data retention configuration."""
    return get_retention_config()


@router.post("/retention/run")
async def trigger_retention(days: int = 90, user=Depends(require_role("admin"))):
    """Manually trigger retention cleanup. Admin only."""
    result = await run_retention_cleanup(retention_days=days)
    return result


@router.get("/system/status")
async def system_status(user=Depends(get_current_user)):
    """System health and configuration status."""
    from app.core.config import settings
    return {
        "version": "3.3.0",
        "edition": "Enterprise Fortress",
        "ai_provider": settings.AI_PROVIDER,
        "anthropic_key_set": bool(settings.ANTHROPIC_API_KEY),
        "openai_key_set": bool(settings.OPENAI_API_KEY),
        "security_layers": [
            "JWT 15-min access + 7-day refresh rotation",
            "RBAC 4-tier enforcement",
            "Rate limiting (tier-based)",
            "Multi-tenant isolation",
            "Standardized error handling",
            "Audit logging on all writes",
            "AI disclosure (2026 compliance)",
            "Data retention auto-cleanup",
            "PII masking (12 patterns)",
        ],
    }
