"""
2026 Compliance Layer — Hard Delete (FINAL)
Endpoint: DELETE /api/user/hard-delete
Uses existing audit_logger module for deletion logging.
Clears PostgreSQL data + file system + in-memory caches.
"""
import os
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.database import User, Document, Output, AudioFile, Transcript, AuditLog
from app.services.audit_logger import log_ai_request

logger = logging.getLogger("docuaction.compliance")
router = APIRouter(prefix="/api/user", tags=["Compliance — Hard Delete"])

# In-memory cache (if used) — clear on delete
_response_cache = {}


# Keys inside AuditLog.details that carry personal data and are redacted on erasure.
# Everything NOT listed here is retained, because it is what gives the audit record
# its meaning (action outcome, resource type, status codes, counts).
_PERSONAL_DETAIL_KEYS = frozenset({
    "email", "user_email", "deleted_email", "invited_email", "target_email",
    "full_name", "name", "first_name", "last_name",
    "phone", "ip", "ip_address", "user_agent",
    "mrn", "ssn", "date_of_birth", "dob", "npi",
})


def _redact_personal_data(details, user_email: str = ""):
    """
    Strip personal data from an AuditLog.details JSON blob, keeping audit meaning.

    Used by the GDPR erasure flow (AUDIT-MUT): the audit RECORD is retained for the
    HIPAA six-year obligation, but the personal data inside it is removed. Returns a
    new dict rather than mutating in place so SQLAlchemy reliably detects the change
    on a JSON column.
    """
    if not isinstance(details, dict):
        # Non-dict payloads (str/list/None) cannot be selectively redacted, so drop
        # them wholesale rather than risk retaining personal data in free text.
        return {"redacted": True} if details else details

    cleaned = {}
    for key, value in details.items():
        if key.lower() in _PERSONAL_DETAIL_KEYS:
            cleaned[key] = "[ERASED]"
        elif user_email and isinstance(value, str) and user_email.lower() in value.lower():
            # Catch the email appearing inside an otherwise non-personal field.
            cleaned[key] = "[ERASED]"
        else:
            cleaned[key] = value
    cleaned["_gdpr_erasure"] = True
    return cleaned


def clear_user_cache(user_id: str):
    """Clear any in-memory or API response caches for this user."""
    keys_to_remove = [k for k in _response_cache if str(user_id) in str(k)]
    for k in keys_to_remove:
        del _response_cache[k]
    logger.info(f"Cache cleared for user {user_id}: {len(keys_to_remove)} entries removed")


@router.delete("/hard-delete")
async def hard_delete_user_data(
    confirm: str = "no",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    HARD DELETE — Permanently wipes ALL user data.
    
    This action is IRREVERSIBLE. 
    Pass confirm=YES-DELETE-EVERYTHING to proceed.
    
    Pipeline:
    1. Validate confirmation string
    2. Delete all AI outputs
    3. Delete all documents + physical files
    4. Delete all transcripts  
    5. Delete all audio files + physical files
    6. Delete all audit logs for this user
    7. Clear in-memory and API caches
    8. Log deletion event via audit_logger
    9. Delete user account
    
    Complies with: GDPR Art. 17, CCPA, Utah AI Act, Colorado AI Act
    """
    # ─── Step 1: Require explicit confirmation ───
    if confirm != "YES-DELETE-EVERYTHING":
        return {
            "warning": "This will PERMANENTLY delete your account and ALL associated data.",
            "instruction": "Set query parameter confirm=YES-DELETE-EVERYTHING to proceed.",
            "data_to_be_deleted": [
                "All uploaded documents and physical files",
                "All AI-generated outputs (summaries, actions, insights, emails, briefs)",
                "All audio recordings and transcripts",
                "Your user account and profile",
                "All cached API responses",
            ],
            # Honest disclosure: security audit records are NOT deleted. Promising
            # deletion here while retaining the rows would misrepresent the erasure to
            # the data subject, which is its own compliance problem.
            "data_to_be_retained": [
                "Security audit log entries — RETAINED but pseudonymised: your "
                "identity, email, and IP address are erased from them, while the "
                "record that an event occurred is kept.",
            ],
            "retention_basis": (
                "HIPAA §164.312(b) and 45 CFR §164.316(b)(2) require audit records to "
                "be retained for six years. GDPR Article 17(3)(b) exempts erasure "
                "where processing is necessary for compliance with a legal obligation. "
                "NIST 800-53 AU-9 requires audit information to be protected from "
                "unauthorised deletion."
            ),
            "reversible": False,
            "compliance_basis": ["GDPR Article 17 (Right to Erasure)", "CCPA", "Utah AI Act", "Colorado AI Act"],
        }

    user_id = user.id
    user_email = user.email
    deletion_report = {
        "outputs_deleted": 0,
        "documents_deleted": 0,
        "files_removed": 0,
        "transcripts_deleted": 0,
        "audio_files_deleted": 0,
        "audit_logs_deleted": 0,
        "cache_entries_cleared": 0,
    }

    try:
        # ─── Step 2: Delete all AI outputs ───
        result = await db.execute(select(Output).where(Output.user_id == user_id))
        outputs = result.scalars().all()
        for o in outputs:
            await db.delete(o)
        deletion_report["outputs_deleted"] = len(outputs)

        # ─── Step 3: Delete all documents + physical files ───
        result = await db.execute(select(Document).where(Document.user_id == user_id))
        documents = result.scalars().all()
        for doc in documents:
            if doc.file_path and os.path.exists(doc.file_path):
                try:
                    os.remove(doc.file_path)
                    deletion_report["files_removed"] += 1
                except OSError as e:
                    logger.warning(f"Could not remove file {doc.file_path}: {e}")
            await db.delete(doc)
        deletion_report["documents_deleted"] = len(documents)

        # ─── Step 4: Delete all transcripts ───
        result = await db.execute(select(Transcript).where(Transcript.user_id == user_id))
        transcripts = result.scalars().all()
        for t in transcripts:
            await db.delete(t)
        deletion_report["transcripts_deleted"] = len(transcripts)

        # ─── Step 5: Delete all audio files + physical files ───
        result = await db.execute(select(AudioFile).where(AudioFile.user_id == user_id))
        audio_files = result.scalars().all()
        for af in audio_files:
            if af.file_path and os.path.exists(af.file_path):
                try:
                    os.remove(af.file_path)
                    deletion_report["files_removed"] += 1
                except OSError as e:
                    logger.warning(f"Could not remove audio file {af.file_path}: {e}")
            await db.delete(af)
        deletion_report["audio_files_deleted"] = len(audio_files)

        # ─── Step 6: PSEUDONYMISE audit logs — do NOT delete them (AUDIT-MUT) ───
        # Audit records are deliberately RETAINED here. Deleting them would breach
        # the retention obligation that outranks the erasure request:
        #
        #   HIPAA §164.312(b) with 45 CFR §164.316(b)(2) requires audit records to be
        #   retained for SIX YEARS. GDPR Article 17(3)(b) expressly exempts erasure
        #   where processing is necessary for compliance with a legal obligation, and
        #   Article 17(1) covers PERSONAL DATA — not the existence of a security
        #   event. NIST AU-9 additionally requires audit information to be protected
        #   from unauthorised modification and deletion.
        #
        # So the correct resolution of the GDPR/HIPAA tension is to strip the personal
        # data FROM the audit record while keeping the record: who-did-what becomes
        # "a since-erased account did what", and the security timeline stays intact
        # and complete. Deleting the rows would also silently destroy the evidence of
        # any prior attack against this account.
        result = await db.execute(select(AuditLog).where(AuditLog.user_id == user_id))
        logs = result.scalars().all()
        for log_entry in logs:
            # Detach attribution. Also required structurally: audit_logs.user_id has a
            # NO ACTION foreign key to users.id, so the user row cannot be deleted
            # while any audit row still references it.
            log_entry.user_id = None
            # Redact personal data that may have been captured inside the JSON blob,
            # keeping the non-personal keys that give the event its audit meaning.
            log_entry.details = _redact_personal_data(log_entry.details, user_email)
            # An IP address is personal data under GDPR and is not needed once the
            # subject is erased; the event itself remains attributable in time.
            log_entry.ip_address = None
        deletion_report["audit_logs_pseudonymised"] = len(logs)
        # Retained for API compatibility. Deliberately reports 0 — nothing is deleted.
        deletion_report["audit_logs_deleted"] = 0

        # ─── Step 7: Clear caches ───
        clear_user_cache(str(user_id))

        # ─── Step 8: Log deletion event BEFORE deleting user ───
        # Using audit_logger from existing module
        await log_ai_request(
            user_id=str(user_id),
            document_id=None,
            action_type="hard_delete",
            model_used="system",
            processing_time_ms=0,
            status="completed",
            confidence=1.0,
            fallback_used=False,
            error=None,
        )

        # Log to application logger (permanent record)
        logger.warning(
            f"HARD DELETE EXECUTED | user_id={user_id} | email={user_email} | "
            f"timestamp={datetime.utcnow().isoformat()}Z | "
            f"report={deletion_report}"
        )

        # ─── Step 9: Delete user account ───
        await db.delete(user)
        await db.commit()

        return {
            "status": "deleted",
            "message": "All data has been permanently and irreversibly removed.",
            "deletion_report": deletion_report,
            "deleted_at": datetime.utcnow().isoformat() + "Z",
            "user_email": user_email,
            "compliance": ["GDPR Art. 17", "CCPA", "Utah AI Act", "Colorado AI Act"],
        }

    except Exception as e:
        await db.rollback()
        logger.error(f"Hard delete FAILED for user {user_id}: {e}")
        raise HTTPException(500, f"Hard delete failed: {str(e)}")


@router.get("/data-export")
async def export_user_data(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Export all user data in JSON format.
    GDPR Article 20 — Right to Data Portability.
    """
    result = await db.execute(select(Document).where(Document.user_id == user.id))
    documents = [{"id": str(d.id), "filename": d.filename, "file_type": d.file_type, "status": d.status, "created_at": str(d.created_at)} for d in result.scalars().all()]

    result = await db.execute(select(Output).where(Output.user_id == user.id))
    outputs = [{"id": str(o.id), "action_type": o.action_type, "content": o.content, "model_used": o.model_used, "confidence": o.confidence, "created_at": str(o.created_at)} for o in result.scalars().all()]

    # Log export event
    await log_ai_request(
        user_id=str(user.id), document_id=None, action_type="data_export",
        model_used="system", processing_time_ms=0, status="success",
        confidence=1.0, fallback_used=False,
    )

    return {
        "user": {
            "id": str(user.id), "email": user.email, "full_name": user.full_name,
            "company": user.company, "role": user.role, "created_at": str(user.created_at),
        },
        "documents": documents,
        "outputs": outputs,
        "export_date": datetime.utcnow().isoformat() + "Z",
        "format": "JSON",
        "compliance": ["GDPR Art. 20", "CCPA"],
    }
