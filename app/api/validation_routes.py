"""
Human-in-the-Loop Validation Queue
- Auto-queues outputs when confidence < 85% or domain = government/healthcare
- Persistent in database
- Approve / Edit / Reject workflow
"""
import uuid
import json
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, Column, String, Float, DateTime, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db, Base, engine
from app.core.security import get_current_user

logger = logging.getLogger("docuaction.validation")
router = APIRouter(prefix="/api/validation", tags=["Validation Queue"])


# ═══ DATABASE MODEL ═══
class ValidationItem(Base):
    __tablename__ = "validation_queue"
    __table_args__ = {"extend_existing": True}

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    tenant_id = Column(String, default="default")
    document_id = Column(String)
    document_name = Column(String)
    output_id = Column(String)
    action_type = Column(String)
    confidence = Column(Float, default=0)
    risk_level = Column(String, default="medium")
    domain = Column(String, default="enterprise")
    correlation_id = Column(String)
    status = Column(String, default="pending")  # pending | approved | rejected | revision
    reviewer_id = Column(String)
    reviewer_notes = Column(Text)
    content_preview = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime)


# Auto-create table on first use
_table_created = False

async def _ensure_table():
    global _table_created
    if not _table_created:
        try:
            real_engine = engine.__class__.__mro__  # Check if proxy
            from app.core.database import _get_engine
            e = _get_engine()
            async with e.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            _table_created = True
        except Exception as ex:
            logger.warning(f"Table creation: {ex}")
            _table_created = True  # Don't retry every request


# ═══ REQUEST MODELS ═══
class ValidationAction(BaseModel):
    action: str  # approve | reject | revision
    notes: Optional[str] = None
    edited_content: Optional[str] = None


# ═══ AUTO-QUEUE FUNCTION (called by other routes) ═══
async def auto_queue_output(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
    document_id: str,
    document_name: str,
    output_id: str,
    action_type: str,
    confidence: float,
    domain: str = "enterprise",
    content_preview: str = "",
):
    """Automatically queue an output for validation if criteria met."""
    await _ensure_table()

    should_queue = False
    risk_level = "low"

    # Auto-queue rules
    if confidence < 0.85:
        should_queue = True
        risk_level = "high" if confidence < 0.70 else "medium"
    if domain in ("government", "healthcare", "legal"):
        should_queue = True
        if risk_level == "low":
            risk_level = "medium"

    if not should_queue:
        return None

    correlation_id = "DA-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper()

    item = ValidationItem(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tenant_id=tenant_id,
        document_id=document_id,
        document_name=document_name,
        output_id=output_id,
        action_type=action_type,
        confidence=confidence,
        risk_level=risk_level,
        domain=domain,
        correlation_id=correlation_id,
        status="pending",
        content_preview=content_preview[:500] if content_preview else "",
        created_at=datetime.utcnow(),
    )

    try:
        db.add(item)
        await db.commit()
        logger.info(f"Queued for validation: {document_name} / {action_type} (confidence: {confidence:.0%})")
        return correlation_id
    except Exception as e:
        logger.warning(f"Queue failed: {e}")
        await db.rollback()
        return None


# ═══ ENDPOINTS ═══

@router.get("/queue")
async def get_validation_queue(
    status: str = "pending",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get all items in the validation queue."""
    await _ensure_table()

    try:
        query = select(ValidationItem).where(
            ValidationItem.user_id == str(user.id)
        ).order_by(desc(ValidationItem.created_at))

        if status != "all":
            query = query.where(ValidationItem.status == status)

        results = await db.execute(query)
        items = results.scalars().all()

        return {
            "total": len(items),
            "pending": len([i for i in items if i.status == "pending"]),
            "approved": len([i for i in items if i.status == "approved"]),
            "rejected": len([i for i in items if i.status == "rejected"]),
            "items": [
                {
                    "id": i.id,
                    "document_id": i.document_id,
                    "document_name": i.document_name,
                    "output_id": i.output_id,
                    "action_type": i.action_type,
                    "confidence": i.confidence,
                    "risk_level": i.risk_level,
                    "domain": i.domain,
                    "correlation_id": i.correlation_id,
                    "status": i.status,
                    "content_preview": i.content_preview,
                    "created_at": i.created_at.isoformat() if i.created_at else None,
                    "reviewed_at": i.reviewed_at.isoformat() if i.reviewed_at else None,
                    "reviewer_notes": i.reviewer_notes,
                }
                for i in items
            ],
        }
    except Exception as e:
        logger.error(f"Queue query failed: {e}")
        return {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "items": []}


@router.post("/review/{item_id}")
async def review_item(
    item_id: str,
    action: ValidationAction,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Approve, reject, or request revision for a validation item."""
    await _ensure_table()

    result = await db.execute(
        select(ValidationItem).where(ValidationItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Validation item not found")

    if action.action == "approve":
        item.status = "approved"
    elif action.action == "reject":
        item.status = "rejected"
    elif action.action == "revision":
        item.status = "revision"
    else:
        raise HTTPException(400, f"Invalid action: {action.action}")

    item.reviewer_id = str(user.id)
    item.reviewer_notes = action.notes
    item.reviewed_at = datetime.utcnow()

    await db.commit()

    return {
        "id": item.id,
        "status": item.status,
        "reviewed_by": user.email,
        "reviewed_at": item.reviewed_at.isoformat(),
        "notes": item.reviewer_notes,
    }


@router.get("/stats")
async def validation_stats(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get validation queue statistics."""
    await _ensure_table()

    try:
        results = await db.execute(
            select(ValidationItem).where(ValidationItem.user_id == str(user.id))
        )
        items = results.scalars().all()

        return {
            "total": len(items),
            "pending": len([i for i in items if i.status == "pending"]),
            "approved": len([i for i in items if i.status == "approved"]),
            "rejected": len([i for i in items if i.status == "rejected"]),
            "avg_confidence": round(sum(i.confidence for i in items) / max(len(items), 1), 2),
            "by_risk": {
                "critical": len([i for i in items if i.risk_level == "critical"]),
                "high": len([i for i in items if i.risk_level == "high"]),
                "medium": len([i for i in items if i.risk_level == "medium"]),
                "low": len([i for i in items if i.risk_level == "low"]),
            },
            "by_domain": {
                "government": len([i for i in items if i.domain == "government"]),
                "healthcare": len([i for i in items if i.domain == "healthcare"]),
                "legal": len([i for i in items if i.domain == "legal"]),
                "enterprise": len([i for i in items if i.domain == "enterprise"]),
            },
        }
    except Exception as e:
        return {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "avg_confidence": 0, "by_risk": {}, "by_domain": {}}
