"""
Template System — Multi-Tenant Safe
Predefined templates for all users + custom templates for Enterprise.
Strict tenant_id isolation on all queries.
"""
import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import Column, String, Text, DateTime, Boolean, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.core.database import get_db, Base
from app.core.security import get_current_user

logger = logging.getLogger("docuaction.templates")
router = APIRouter(prefix="/api/templates", tags=["Templates"])


# ═══ DATABASE MODEL ═══

class OutputTemplate(Base):
    __tablename__ = "output_templates"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(50), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(String(500), default="")
    action_type = Column(String(50), nullable=False)  # summary, actions, insights, email, brief
    template_content = Column(Text, nullable=False)    # JSON structure defining the template
    is_system = Column(Boolean, default=False)         # True = predefined, False = custom
    is_active = Column(Boolean, default=True)
    created_by = Column(String(255), default="system")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══ SCHEMAS ═══

class TemplateCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    action_type: str
    template_content: str

class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    template_content: Optional[str] = None
    is_active: Optional[bool] = None

class TemplateResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    action_type: str
    template_content: str
    is_system: bool
    is_active: bool
    created_at: str


# ═══ PREDEFINED TEMPLATES ═══

PREDEFINED_TEMPLATES = [
    {
        "name": "Meeting Summary",
        "description": "Standard meeting summary with attendees, decisions, and action items",
        "action_type": "summary",
        "template_content": '{"sections": ["attendees", "agenda", "summary", "decisions", "action_items", "next_meeting"], "tone": "professional", "length": "detailed"}',
    },
    {
        "name": "Action Report",
        "description": "Focused action item extraction with owners, deadlines, and priorities",
        "action_type": "actions",
        "template_content": '{"sections": ["tasks", "decisions", "follow_ups"], "include_priority": true, "include_deadline": true, "include_owner": true}',
    },
    {
        "name": "Email Draft",
        "description": "Professional email draft with subject, body, and action items",
        "action_type": "email",
        "template_content": '{"format": "professional", "include_subject": true, "include_action_items": true, "tone": "business_formal"}',
    },
    {
        "name": "Executive Brief",
        "description": "Board-ready executive brief with situation analysis and recommendations",
        "action_type": "brief",
        "template_content": '{"sections": ["situation", "key_findings", "metrics", "risk_assessment", "recommendation"], "format": "executive"}',
    },
    {
        "name": "Key Insights",
        "description": "Prioritized insights with impact levels and risk factors",
        "action_type": "insights",
        "template_content": '{"sections": ["insights", "risk_factors", "opportunities"], "include_impact": true, "include_evidence": true}',
    },
]


# ═══ ENDPOINTS ═══

@router.get("/")
async def list_templates(
    action_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    List all available templates for the current user's tenant.
    Includes system templates (available to all) + custom tenant templates.
    TENANT ISOLATION: Only returns templates for the user's tenant_id.
    """
    tenant_id = getattr(user, 'tenant_id', 'default') or 'default'

    # Get system templates (available to all tenants)
    query = select(OutputTemplate).where(
        OutputTemplate.is_active == True,
        (OutputTemplate.is_system == True) | (OutputTemplate.tenant_id == tenant_id)
    )
    if action_type:
        query = query.where(OutputTemplate.action_type == action_type)

    result = await db.execute(query.order_by(OutputTemplate.is_system.desc(), OutputTemplate.name))
    templates = result.scalars().all()

    # If no templates in DB, return predefined defaults
    if not templates:
        return [
            {
                "id": f"predefined-{i}",
                "tenant_id": "system",
                "name": t["name"],
                "description": t["description"],
                "action_type": t["action_type"],
                "template_content": t["template_content"],
                "is_system": True,
                "is_active": True,
                "created_at": datetime.utcnow().isoformat(),
            }
            for i, t in enumerate(PREDEFINED_TEMPLATES)
            if not action_type or t["action_type"] == action_type
        ]

    return [
        TemplateResponse(
            id=str(t.id), tenant_id=t.tenant_id, name=t.name,
            description=t.description or "", action_type=t.action_type,
            template_content=t.template_content, is_system=t.is_system,
            is_active=t.is_active, created_at=str(t.created_at),
        ) for t in templates
    ]


@router.post("/", status_code=201)
async def create_template(
    data: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Create a custom template for the user's organization.
    Enterprise feature — templates are tenant-isolated.
    """
    tenant_id = getattr(user, 'tenant_id', 'default') or 'default'

    template = OutputTemplate(
        tenant_id=tenant_id,
        name=data.name,
        description=data.description,
        action_type=data.action_type,
        template_content=data.template_content,
        is_system=False,
        created_by=user.email,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    return TemplateResponse(
        id=str(template.id), tenant_id=template.tenant_id, name=template.name,
        description=template.description or "", action_type=template.action_type,
        template_content=template.template_content, is_system=False,
        is_active=True, created_at=str(template.created_at),
    )


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Delete a custom template. Cannot delete system templates."""
    tenant_id = getattr(user, 'tenant_id', 'default') or 'default'

    result = await db.execute(
        select(OutputTemplate).where(
            OutputTemplate.id == template_id,
            OutputTemplate.tenant_id == tenant_id,
            OutputTemplate.is_system == False,
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(404, "Template not found or cannot be deleted")

    await db.delete(template)
    await db.commit()
    return {"detail": "Template deleted"}
