"""
File Storage Service
Handles document and audio file storage with:
- Absolute paths (no relative path issues)
- Auto-creates directories if they don't exist
- S3/Azure Blob toggle via STORAGE_PROVIDER env var

Storage Providers:
  local  — filesystem storage (default)
  s3     — AWS S3 (placeholder, ready for integration)
  azure  — Azure Blob Storage (placeholder)
"""
import os
import uuid
import logging
from pathlib import Path

logger = logging.getLogger("docuaction.storage")

# ═══ CONFIGURATION ═══
STORAGE_PROVIDER = os.getenv("STORAGE_PROVIDER", "local")
UPLOAD_BASE = os.getenv("UPLOAD_DIR", "/app/uploads")

# Ensure absolute path
if not os.path.isabs(UPLOAD_BASE):
    UPLOAD_BASE = os.path.join(os.getcwd(), UPLOAD_BASE)

# Sub-directories
DOCUMENTS_DIR = os.path.join(UPLOAD_BASE, "documents")
AUDIO_DIR = os.path.join(UPLOAD_BASE, "audio")


def _ensure_dirs():
    """Create storage directories if they don't exist."""
    for d in [UPLOAD_BASE, DOCUMENTS_DIR, AUDIO_DIR]:
        os.makedirs(d, exist_ok=True)
        logger.debug(f"Storage dir ensured: {d}")


# Initialize on import
_ensure_dirs()


def save_document(content: bytes, filename: str) -> str:
    """
    Save a document file and return the absolute path.
    
    Args:
        content: file bytes
        filename: original filename
    
    Returns:
        Absolute path to the saved file
    """
    if STORAGE_PROVIDER == "local":
        _ensure_dirs()
        safe_name = f"{uuid.uuid4().hex}_{filename}"
        filepath = os.path.join(DOCUMENTS_DIR, safe_name)
        with open(filepath, "wb") as f:
            f.write(content)
        logger.info(f"Document saved: {filepath} ({len(content)} bytes)")
        return filepath

    elif STORAGE_PROVIDER == "s3":
        # TODO: Implement S3 upload
        # import boto3
        # s3 = boto3.client('s3')
        # key = f"documents/{uuid.uuid4().hex}_{filename}"
        # s3.put_object(Bucket=os.getenv('S3_BUCKET'), Key=key, Body=content)
        # return f"s3://{os.getenv('S3_BUCKET')}/{key}"
        raise NotImplementedError("S3 storage not yet configured. Set STORAGE_PROVIDER=local")

    elif STORAGE_PROVIDER == "azure":
        # TODO: Implement Azure Blob upload
        raise NotImplementedError("Azure storage not yet configured. Set STORAGE_PROVIDER=local")

    else:
        raise ValueError(f"Unknown storage provider: {STORAGE_PROVIDER}")


def save_audio(content: bytes, filename: str) -> str:
    """Save an audio file and return the absolute path."""
    if STORAGE_PROVIDER == "local":
        _ensure_dirs()
        safe_name = f"{uuid.uuid4().hex}_{filename}"
        filepath = os.path.join(AUDIO_DIR, safe_name)
        with open(filepath, "wb") as f:
            f.write(content)
        logger.info(f"Audio saved: {filepath} ({len(content)} bytes)")
        return filepath
    else:
        raise NotImplementedError(f"Audio storage for {STORAGE_PROVIDER} not yet configured")


def delete_file(filepath: str) -> bool:
    """Delete a file from storage. Returns True if deleted."""
    if STORAGE_PROVIDER == "local":
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.info(f"File deleted: {filepath}")
                return True
            except OSError as e:
                logger.warning(f"Failed to delete {filepath}: {e}")
                return False
        return False
    else:
        # TODO: Implement S3/Azure delete
        return False


def file_exists(filepath: str) -> bool:
    """Check if a file exists in storage."""
    if STORAGE_PROVIDER == "local":
        return os.path.exists(filepath)
    return False


def get_storage_info() -> dict:
    """Return current storage configuration."""
    return {
        "provider": STORAGE_PROVIDER,
        "base_path": UPLOAD_BASE,
        "documents_dir": DOCUMENTS_DIR,
        "audio_dir": AUDIO_DIR,
        "dirs_exist": {
            "base": os.path.exists(UPLOAD_BASE),
            "documents": os.path.exists(DOCUMENTS_DIR),
            "audio": os.path.exists(AUDIO_DIR),
        },
    }
