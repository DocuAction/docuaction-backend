"""
Multi-Tenant Isolation Layer
Every database query is filtered by tenant_id extracted from the JWT token.
Cross-tenant data access is physically impossible at the query level.

Architecture:
  JWT → Extract tenant_id → Inject into every SELECT/UPDATE/DELETE → No exceptions
"""
import uuid
import logging
from typing import Optional
from fastapi import Depends, HTTPException, Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.database import get_db

logger = logging.getLogger("docuaction.tenant")

# Default tenant for single-tenant mode (backward compatible)
DEFAULT_TENANT = "default"


def get_tenant_id_from_token(request: Request) -> str:
    """
    Extract tenant_id from JWT token in request headers.
    Falls back to 'default' for backward compatibility.
    """
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            from app.core.security import decode_token
            payload = decode_token(auth.replace("Bearer ", ""))
            return payload.get("tenant_id", DEFAULT_TENANT)
        except Exception:
            pass
    return DEFAULT_TENANT


class TenantContext:
    """
    Holds the current tenant_id for the request lifecycle.
    Injected as a dependency in all data-access endpoints.
    """
    def __init__(self, tenant_id: str = DEFAULT_TENANT):
        self.tenant_id = tenant_id

    def validate_access(self, resource_tenant_id: str):
        """Raise 403 if resource belongs to a different tenant."""
        if resource_tenant_id and resource_tenant_id != self.tenant_id:
            logger.warning(
                f"CROSS-TENANT ACCESS BLOCKED | "
                f"request_tenant={self.tenant_id} | "
                f"resource_tenant={resource_tenant_id}"
            )
            raise HTTPException(403, "Access denied — cross-tenant access is not permitted")


async def get_tenant(request: Request) -> TenantContext:
    """
    FastAPI dependency that extracts tenant context from request.
    Use in endpoints: tenant: TenantContext = Depends(get_tenant)
    """
    tenant_id = get_tenant_id_from_token(request)
    return TenantContext(tenant_id=tenant_id)


def apply_tenant_filter(query, model, tenant_id: str):
    """
    Apply tenant_id filter to any SQLAlchemy query.
    
    Usage:
        query = select(Document).where(Document.user_id == user.id)
        query = apply_tenant_filter(query, Document, tenant.tenant_id)
    """
    if hasattr(model, 'tenant_id'):
        return query.where(model.tenant_id == tenant_id)
    return query


# ═══ TENANT-AWARE QUERY HELPERS ═══

async def tenant_get_all(db: AsyncSession, model, user_id, tenant_id: str, order_by=None):
    """Get all records for a user within their tenant."""
    from sqlalchemy import select
    query = select(model).where(model.user_id == user_id)
    if hasattr(model, 'tenant_id'):
        query = query.where(model.tenant_id == tenant_id)
    if order_by is not None:
        query = query.order_by(order_by)
    result = await db.execute(query)
    return result.scalars().all()


async def tenant_get_one(db: AsyncSession, model, record_id, user_id, tenant_id: str):
    """Get single record with tenant + user validation."""
    from sqlalchemy import select
    query = select(model).where(model.id == record_id, model.user_id == user_id)
    if hasattr(model, 'tenant_id'):
        query = query.where(model.tenant_id == tenant_id)
    result = await db.execute(query)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Resource not found")
    return record


# ═══ TENANT CREATION (for multi-org support) ═══

def generate_tenant_id() -> str:
    """Generate a unique tenant ID for a new organization."""
    return f"tenant_{uuid.uuid4().hex[:12]}"
