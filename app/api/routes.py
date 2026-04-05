"""
DocuAction AI — API Routes
All endpoints in one clean router.
Updated: proper document text extraction for PDF, DOCX, XLSX, images, TXT
"""
import os
import uuid
import time
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_token, get_current_user
from app.models.database import User, Document, Output, AudioFile, Transcript
from app.models.schemas import (
    SignupRequest, LoginRequest, TokenResponse, UserResponse,
    ProcessRequest, ProcessResponse,
    DocumentResponse, OutputResponse,
    TranscribeResponse,
)

logger = logging.getLogger("docuaction.api")
router = APIRouter()

UPLOAD_DIR = Path(settings.UPLOAD_DIR)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
(UPLOAD_DIR / "documents").mkdir(exist_ok=True)
(UPLOAD_DIR / "audio").mkdir(exist_ok=True)

ALLOWED_DOCS = {".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".webm", ".ogg"}


# ═══════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════

@router.post("/api/auth/signup", response_model=TokenResponse, status_code=201, tags=["Auth"])
async def signup(data: SignupRequest, db: AsyncSession = Depends(get_db)):
    if len(data.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    existing = await db.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")
    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        company=data.company,
        plan=getattr(data, 'plan', 'free') or 'free',
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.post("/api/auth/login", response_model=TokenResponse, tags=["Auth"])
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    token = create_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.get("/api/auth/me", response_model=UserResponse, tags=["Auth"])
async def get_profile(user=Depends(get_current_user)):
    return UserResponse.model_validate(user)


# ═══════════════════════════════════════════════════════
# PROCESS ENDPOINT (AI ENGINE — TEXT INPUT)
# ═══════════════════════════════════════════════════════

@router.post("/api/process", tags=["AI Engine"])
async def process_text(request: ProcessRequest, user=Depends(get_current_user)):
    """
    Process text through the AI engine.
    Input: raw text + action_type
    Output: structured JSON (summary, tasks, decisions, follow_ups)
    """
    if not request.text or len(request.text.strip()) < 20:
        raise HTTPException(400, "Text must be at least 20 characters")

    # Plan enforcement
    from app.services.plan_enforcement import check_ai_action_limit
    from app.core.database import get_db as _get_db
    # Note: plan check happens at output generation level

    from app.services.ai_engine import process_document

    result = await process_document(
        document_text=request.text,
        action_type=request.action_type,
        user_id=str(user.id),
        output_language=request.output_language,
    )
    return result


# ═══════════════════════════════════════════════════════
# PROCESS FILE ENDPOINT (UPLOAD + AI IN ONE STEP)
# ═══════════════════════════════════════════════════════

@router.post("/api/process-file", tags=["AI Engine"])
async def process_file(
    file: UploadFile = File(...),
    action_type: str = "summary",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Upload a file and process it through the AI engine in one step.
    Supports: PDF, DOCX, XLSX, TXT, CSV, images
    Returns: structured AI output
    """
    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_DOCS:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {', '.join(ALLOWED_DOCS)}")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 50MB)")

    # Save file
    fname = f"{uuid.uuid4().hex}_{file.filename}"
    fpath = UPLOAD_DIR / "documents" / fname
    fpath.write_bytes(content)

    # Save to database
    doc = Document(
        user_id=user.id, filename=file.filename, file_path=str(fpath),
        file_type=ext.replace(".", ""), file_size_bytes=len(content), status="processing",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # Extract text properly
    from app.services.document_extractor import extract_text
    try:
        text = await extract_text(str(fpath), ext.replace(".", ""))
    except Exception as e:
        doc.status = "failed"
        await db.commit()
        raise HTTPException(500, f"Failed to read document: {str(e)}")

    if not text or len(text.strip()) < 20:
        doc.status = "failed"
        await db.commit()
        raise HTTPException(400, "Could not extract readable text from this file")

    # Process through AI engine
    from app.services.ai_engine import process_document
    import json

    result = await process_document(
        document_text=text[:50000],
        action_type=action_type,
        user_id=str(user.id),
        document_id=str(doc.id),
    )

    # Save output
    meta = result.pop("_meta", {})
    output = Output(
        document_id=doc.id, user_id=user.id, action_type=action_type,
        content=json.dumps(result, ensure_ascii=False),
        model_used=meta.get("model_used", "unknown"),
        confidence=result.get("confidence", 0),
        processing_time_ms=meta.get("processing_time_ms", 0),
        status="draft",
    )
    db.add(output)
    doc.status = "processed"
    await db.commit()
    await db.refresh(output)

    return {
        "document": {
            "id": str(doc.id),
            "filename": doc.filename,
            "file_type": doc.file_type,
            "status": doc.status,
        },
        "output": {
            "id": str(output.id),
            "action_type": action_type,
            "model_used": meta.get("model_used"),
            "confidence": result.get("confidence", 0),
            "processing_time_ms": meta.get("processing_time_ms", 0),
        },
        "result": result,
    }


# ═══════════════════════════════════════════════════════
# TRANSCRIBE ENDPOINT (WHISPER)
# ═══════════════════════════════════════════════════════

@router.post("/api/transcribe", response_model=TranscribeResponse, tags=["Audio"])
async def transcribe_audio(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Upload audio file and transcribe using OpenAI Whisper."""
    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_AUDIO:
        raise HTTPException(400, f"Unsupported format. Allowed: {', '.join(ALLOWED_AUDIO)}")

    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(400, "Audio file too large. Maximum 25MB.")

    # Check transcription plan limits
    from app.services.plan_enforcement import check_transcription_limit
    await check_transcription_limit(db, user.id, getattr(user, 'plan', 'free'))

    fname = f"{uuid.uuid4().hex}_{file.filename}"
    fpath = UPLOAD_DIR / "audio" / fname
    fpath.write_bytes(content)

    audio = AudioFile(
        user_id=user.id, filename=file.filename, file_path=str(fpath),
        file_size_bytes=len(content), file_type=ext.replace(".", ""), status="transcribing",
    )
    db.add(audio)
    await db.commit()
    await db.refresh(audio)

    try:
        from app.services.audio_service import transcribe_audio_file
        result = await transcribe_audio_file(str(fpath))

        transcript = Transcript(
            audio_file_id=audio.id, user_id=user.id, full_text=result["text"],
            word_count=result["word_count"], language=result["language"],
            confidence=0.96, segments=result.get("segments"),
            model_used=result["model"], processing_time_ms=result["processing_time_ms"],
        )
        db.add(transcript)
        audio.status = "transcribed"
        audio.duration_seconds = result["duration_seconds"]
        audio.language_detected = result["language"]
        audio.transcription_cost = result["cost_usd"]
        await db.commit()

        return TranscribeResponse(
            transcript=result["text"], word_count=result["word_count"],
            language=result["language"], duration_seconds=result["duration_seconds"],
            confidence=0.96, cost_usd=result["cost_usd"],
            model=result["model"], segments=result.get("segments"),
        )
    except Exception as e:
        audio.status = "failed"
        await db.commit()
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(500, f"Transcription failed: {str(e)}")


# ═══════════════════════════════════════════════════════
# DOCUMENT ENDPOINTS
# ═══════════════════════════════════════════════════════

@router.post("/api/documents/upload", response_model=DocumentResponse, status_code=201, tags=["Documents"])
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Upload a document for AI processing."""
    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_DOCS and ext not in ALLOWED_AUDIO:
        raise HTTPException(400, f"Unsupported file type: {ext}")
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 50MB)")

    fname = f"{uuid.uuid4().hex}_{file.filename}"
    fpath = UPLOAD_DIR / "documents" / fname
    fpath.write_bytes(content)

    doc = Document(
        user_id=user.id, filename=file.filename, file_path=str(fpath),
        file_type=ext.replace(".", ""), file_size_bytes=len(content), status="uploaded",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return DocumentResponse.model_validate(doc)


@router.get("/api/documents", response_model=list[DocumentResponse], tags=["Documents"])
async def list_documents(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    result = await db.execute(select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc()))
    return [DocumentResponse.model_validate(d) for d in result.scalars().all()]


@router.delete("/api/documents/{doc_id}", tags=["Documents"])
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    result = await db.execute(select(Document).where(Document.id == doc_id, Document.user_id == user.id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)
    await db.delete(doc)
    await db.commit()
    return {"detail": "Document deleted"}


# ═══════════════════════════════════════════════════════
# OUTPUT ENDPOINTS (Generate AI from uploaded doc)
# ═══════════════════════════════════════════════════════

@router.post("/api/outputs/generate/{doc_id}", response_model=OutputResponse, status_code=201, tags=["AI Outputs"])
async def generate_output(
    doc_id: str,
    action_type: str = "summary",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Generate AI output from an uploaded document using proper text extraction."""
    result = await db.execute(select(Document).where(Document.id == doc_id, Document.user_id == user.id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    # Check plan limits before processing
    from app.services.plan_enforcement import check_ai_action_limit
    await check_ai_action_limit(db, user.id, getattr(user, 'plan', 'free'))

    # Extract text PROPERLY using document_extractor
    from app.services.document_extractor import extract_text
    try:
        text = await extract_text(doc.file_path, doc.file_type)
    except Exception as e:
        raise HTTPException(500, f"Failed to read document: {str(e)}")

    if not text or len(text.strip()) < 20:
        raise HTTPException(400, "Could not extract readable text from this document. Try uploading a text-based PDF or DOCX file.")

    # Process through AI engine
    from app.services.ai_engine import process_document
    import json

    ai_result = await process_document(
        document_text=text[:50000],
        action_type=action_type,
        user_id=str(user.id),
        document_id=str(doc.id),
    )

    meta = ai_result.pop("_meta", {})

    output = Output(
        document_id=doc.id, user_id=user.id, action_type=action_type,
        content=json.dumps(ai_result, ensure_ascii=False),
        model_used=meta.get("model_used", "unknown"),
        confidence=ai_result.get("confidence", 0),
        processing_time_ms=meta.get("processing_time_ms", 0),
        status="draft",
    )
    db.add(output)
    doc.status = "processed"
    await db.commit()
    await db.refresh(output)
    return OutputResponse.model_validate(output)


@router.get("/api/outputs", response_model=list[OutputResponse], tags=["AI Outputs"])
async def list_outputs(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    result = await db.execute(select(Output).where(Output.user_id == user.id).order_by(Output.created_at.desc()))
    return [OutputResponse.model_validate(o) for o in result.scalars().all()]


@router.get("/api/outputs/{output_id}", response_model=OutputResponse, tags=["AI Outputs"])
async def get_output(output_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    result = await db.execute(select(Output).where(Output.id == output_id, Output.user_id == user.id))
    output = result.scalar_one_or_none()
    if not output:
        raise HTTPException(404, "Output not found")
    return OutputResponse.model_validate(output)
