"""
DocuAction — Module Gate Middleware
Enforces feature flags and RBAC permissions at the API layer.

Architecture:
  - Tenant-level feature flags (module_data_systems, module_healthcare, etc.)
  - Role-based permissions (migration.schema.upload, migration.mapping.approve, etc.)
  - Three-layer enforcement: UI, API, Service
  - This file handles the API layer. UI and Service layers check independently.

Security:
  - Returns HTTP 403 (not 404) when module disabled — no metadata leakage
  - Checks module flag BEFORE RBAC — fail fast on disabled modules
  - All checks logged to audit trail
"""
import logging
from typing import Optional, List
from fastapi import HTTPException, Depends
from app.core.security import get_current_user

logger = logging.getLogger("docuaction.module_gate")


# ═══════════════════════════════════════════════════════
# MODULE REGISTRY
# ═══════════════════════════════════════════════════════

MODULE_REGISTRY = {
    "documents":    {"default": True,  "plans": ["free", "pro", "business", "enterprise", "federal"], "locked": True},
    "audio":        {"default": False, "plans": ["pro", "business", "enterprise", "federal"], "locked": False},
    "healthcare":   {"default": False, "plans": ["business", "enterprise", "federal"], "locked": False},
    "data_systems": {"default": False, "plans": ["enterprise", "federal"], "locked": False},
    "foip":         {"default": False, "plans": ["federal"], "locked": False},
}

# ═══════════════════════════════════════════════════════
# PERMISSION MATRIX
# ═══════════════════════════════════════════════════════

ROLE_PERMISSIONS = {
    "admin": [
        "migration.schema.upload", "migration.schema.view", "migration.mapping.approve",
        "migration.mapping.override", "migration.decision.escalate", "migration.manifest.export",
        "migration.manifest.api", "migration.validation.run", "migration.settings.configure",
    ],
    "data_architect": [
        "migration.schema.upload", "migration.schema.view", "migration.mapping.approve",
        "migration.mapping.override", "migration.decision.escalate", "migration.manifest.export",
        "migration.manifest.api", "migration.validation.run",
    ],
    "manager": [
        "migration.schema.view", "migration.mapping.approve", "migration.decision.escalate",
        "migration.manifest.export", "migration.validation.run",
    ],
    "analyst": [
        "migration.schema.view", "migration.decision.escalate",
        "migration.manifest.export", "migration.validation.run",
    ],
    "etl_developer": [
        "migration.schema.view", "migration.manifest.export",
        "migration.manifest.api", "migration.validation.run",
    ],
    "viewer": [
        "migration.schema.view",
    ],
}


# ═══════════════════════════════════════════════════════
# IN-MEMORY FLAG STORE (Phase 1 — move to DB in Phase 2)
# ═══════════════════════════════════════════════════════

# Tenant ID → {flag_name: bool}
_tenant_flags = {}

# Global kill switch — overrides all tenant flags
_global_kill_switch = {}


def set_tenant_flag(tenant_id: str, flag: str, enabled: bool):
    """Set a feature flag for a tenant."""
    if tenant_id not in _tenant_flags:
        _tenant_flags[tenant_id] = {}
    _tenant_flags[tenant_id][flag] = enabled
    logger.info(f"Flag set: tenant={tenant_id} flag={flag} enabled={enabled}")


def set_kill_switch(module: str, killed: bool):
    """Emergency kill switch — disables module for ALL tenants."""
    _global_kill_switch[module] = killed
    logger.warning(f"KILL SWITCH: module={module} killed={killed}")


def is_module_enabled(module: str, user_plan: str, tenant_id: str = "default") -> bool:
    """
    Check if a module is enabled for this tenant.
    
    Check order:
    1. Global kill switch (overrides everything)
    2. Tenant-specific flag (if set)
    3. Plan-based default (from MODULE_REGISTRY)
    """
    # Kill switch check
    if _global_kill_switch.get(module, False):
        return False

    # Tenant-specific override
    flag_name = f"module_{module}"
    tenant_flags = _tenant_flags.get(tenant_id, {})
    if flag_name in tenant_flags:
        return tenant_flags[flag_name]

    # Plan-based default
    registry = MODULE_REGISTRY.get(module)
    if not registry:
        return False

    if registry.get("locked"):
        return True  # Core modules always on

    return user_plan in registry.get("plans", [])


def has_permission(user_role: str, permission: str) -> bool:
    """Check if a role has a specific permission."""
    role_perms = ROLE_PERMISSIONS.get(user_role, [])
    return permission in role_perms


# ═══════════════════════════════════════════════════════
# FASTAPI DEPENDENCIES
# ═══════════════════════════════════════════════════════

def require_module(module: str):
    """
    FastAPI dependency that enforces module access.
    Use: Depends(require_module("data_systems"))
    
    Returns 403 with structured error if module disabled.
    NEVER returns 404 — that would leak endpoint existence.
    """
    async def _check(user=Depends(get_current_user)):
        user_plan = getattr(user, "plan", "free")
        tenant_id = str(getattr(user, "tenant_id", "default"))

        if not is_module_enabled(module, user_plan, tenant_id):
            logger.warning(f"Module access denied: module={module} user={user.email} plan={user_plan}")
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "module_not_enabled",
                    "module": module,
                    "message": f"This feature is not enabled for your organization. Available on: {', '.join(MODULE_REGISTRY.get(module, {}).get('plans', []))} plans.",
                    "upgrade_url": "https://docuaction.io/pricing",
                },
            )
        return user
    return _check


def require_permission(module: str, permission: str):
    """
    FastAPI dependency that enforces module + RBAC.
    Use: Depends(require_permission("data_systems", "migration.schema.upload"))
    
    Check order: module flag FIRST, then RBAC. Fail fast on disabled modules.
    """
    async def _check(user=Depends(require_module(module))):
        user_role = getattr(user, "role", "viewer")

        if not has_permission(user_role, permission):
            logger.warning(f"Permission denied: permission={permission} user={user.email} role={user_role}")
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "insufficient_permissions",
                    "required": permission,
                    "message": f"You do not have permission to perform this action. Required: {permission}",
                },
            )
        return user
    return _check


# ═══════════════════════════════════════════════════════
# ADMIN API FOR FLAG MANAGEMENT
# ═══════════════════════════════════════════════════════

def get_all_flags(tenant_id: str = "default") -> dict:
    """Return all module flags for a tenant with effective values."""
    result = {}
    for module, config in MODULE_REGISTRY.items():
        flag_name = f"module_{module}"
        result[flag_name] = {
            "module": module,
            "enabled": is_module_enabled(module, "enterprise", tenant_id),  # Check at highest plan
            "locked": config.get("locked", False),
            "plans": config.get("plans", []),
            "kill_switch": _global_kill_switch.get(module, False),
            "tenant_override": _tenant_flags.get(tenant_id, {}).get(flag_name),
        }
    return result
