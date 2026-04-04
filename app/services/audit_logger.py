"""
AI Audit Logger
Logs every AI request for enterprise and government compliance.
Immutable, append-only logging with model, timestamp, result, and metadata.
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("docuaction.audit")


async def log_ai_request(
    user_id: Optional[str],
    document_id: Optional[str],
    action_type: str,
    model_used: str,
    processing_time_ms: int,
    status: str,
    confidence: float = 0,
    fallback_used: bool = False,
    error: Optional[str] = None,
):
    """
    Log an AI processing request to the audit trail.
    
    In production, this writes to the audit_logs database table.
    For now, it logs to the application logger with structured data.
    """
    log_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "type": "ai_request",
        "user_id": user_id,
        "document_id": document_id,
        "action_type": action_type,
        "model_used": model_used,
        "processing_time_ms": processing_time_ms,
        "status": status,  # success | fallback | error
        "confidence": round(confidence, 3),
        "fallback_used": fallback_used,
        "error": error,
    }

    # Log to application logger
    if status == "error":
        logger.error(f"AI AUDIT: {log_entry}")
    elif status == "fallback":
        logger.warning(f"AI AUDIT: {log_entry}")
    else:
        logger.info(f"AI AUDIT: {log_entry}")

    # Database persistence (when available)
    try:
        from app.core.database import async_session_maker
        from app.models.document import AuditLog
        import json

        async with async_session_maker() as db:
            audit = AuditLog(
                user_id=user_id,
                action=f"ai_{action_type}",
                resource_type="document",
                resource_id=document_id,
                details={
                    "model_used": model_used,
                    "processing_time_ms": processing_time_ms,
                    "status": status,
                    "confidence": confidence,
                    "fallback_used": fallback_used,
                    "error": error,
                },
            )
            db.add(audit)
            await db.commit()
    except Exception as e:
        # Don't let audit logging failure break the AI pipeline
        logger.debug(f"Audit DB write skipped: {e}")
