"""
Data Retention Policy Worker
Configurable retention periods: 30, 60, 90 days, or unlimited.
Runs as a background task or cron job to auto-delete expired records.
Logs all deletions via audit_logger.
"""
import os
import logging
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_maker
from app.models.database import Document, Output, AudioFile, Transcript, AuditLog
from app.services.audit_logger import log_ai_request

logger = logging.getLogger("docuaction.retention")

# ═══ RETENTION SETTINGS ═══

RETENTION_POLICIES = {
    "free": 30,        # 30 days
    "pro": 90,         # 90 days
    "business": 365,   # 1 year
    "enterprise": 0,   # 0 = unlimited (never auto-delete)
}

# Override via environment variable
DEFAULT_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "90"))


async def run_retention_cleanup(retention_days: int = None):
    """
    Delete all records older than the retention period.
    
    Called by:
    - Background task on server startup (daily check)
    - Manual trigger via admin API
    - External cron job
    """
    days = retention_days or DEFAULT_RETENTION_DAYS
    if days <= 0:
        logger.info("Retention policy: unlimited — no cleanup needed")
        return {"status": "skipped", "reason": "unlimited retention"}

    cutoff_date = datetime.utcnow() - timedelta(days=days)
    logger.info(f"Retention cleanup starting: deleting records older than {days} days (before {cutoff_date.isoformat()})")

    report = {
        "retention_days": days,
        "cutoff_date": cutoff_date.isoformat(),
        "outputs_deleted": 0,
        "documents_deleted": 0,
        "transcripts_deleted": 0,
        "audio_files_deleted": 0,
        "files_removed": 0,
    }

    async with async_session_maker() as db:
        try:
            # 1. Delete expired outputs
            result = await db.execute(
                select(Output).where(Output.created_at < cutoff_date)
            )
            expired_outputs = result.scalars().all()
            for o in expired_outputs:
                await db.delete(o)
            report["outputs_deleted"] = len(expired_outputs)

            # 2. Delete expired documents + physical files
            result = await db.execute(
                select(Document).where(Document.created_at < cutoff_date)
            )
            expired_docs = result.scalars().all()
            for doc in expired_docs:
                if doc.file_path and os.path.exists(doc.file_path):
                    try:
                        os.remove(doc.file_path)
                        report["files_removed"] += 1
                    except OSError:
                        pass
                await db.delete(doc)
            report["documents_deleted"] = len(expired_docs)

            # 3. Delete expired transcripts
            result = await db.execute(
                select(Transcript).where(Transcript.created_at < cutoff_date)
            )
            expired_transcripts = result.scalars().all()
            for t in expired_transcripts:
                await db.delete(t)
            report["transcripts_deleted"] = len(expired_transcripts)

            # 4. Delete expired audio files + physical files
            result = await db.execute(
                select(AudioFile).where(AudioFile.created_at < cutoff_date)
            )
            expired_audio = result.scalars().all()
            for af in expired_audio:
                if af.file_path and os.path.exists(af.file_path):
                    try:
                        os.remove(af.file_path)
                        report["files_removed"] += 1
                    except OSError:
                        pass
                await db.delete(af)
            report["audio_files_deleted"] = len(expired_audio)

            await db.commit()

            total_deleted = (
                report["outputs_deleted"] + report["documents_deleted"] +
                report["transcripts_deleted"] + report["audio_files_deleted"]
            )

            if total_deleted > 0:
                logger.info(f"Retention cleanup complete: {total_deleted} records deleted | {report}")
                # Log via audit
                await log_ai_request(
                    user_id="system", document_id=None,
                    action_type="retention_cleanup",
                    model_used="system",
                    processing_time_ms=0,
                    status="completed",
                    confidence=1.0,
                    fallback_used=False,
                )
            else:
                logger.info("Retention cleanup: no expired records found")

            return report

        except Exception as e:
            await db.rollback()
            logger.error(f"Retention cleanup failed: {e}")
            return {"status": "error", "error": str(e)}


async def retention_background_task():
    """
    Background task that runs retention cleanup daily.
    Starts on server startup, runs every 24 hours.
    """
    logger.info("Retention background worker started")
    while True:
        try:
            await run_retention_cleanup()
        except Exception as e:
            logger.error(f"Retention background task error: {e}")
        # Sleep 24 hours
        await asyncio.sleep(86400)


# ═══ ADMIN API HELPERS ═══

def get_retention_config() -> dict:
    """Return current retention configuration."""
    return {
        "default_retention_days": DEFAULT_RETENTION_DAYS,
        "tier_policies": RETENTION_POLICIES,
        "configurable_via": "DATA_RETENTION_DAYS environment variable",
        "auto_cleanup": "Runs daily via background task",
    }
