"""
DocuAction AI — API Routes
All endpoints in one clean router.
Updated: proper document text extraction for PDF, DOCX, XLSX, images, TXT
Updated: Context Box fix — Intelligence Mode focus instructions now reach AI engine
"""
import os
import re
import uuid
import time
import hashlib
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, Query  # ← CONTEXT FIX: added Request, Query
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.config import settings
from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_token, create_token_pair, get_current_user, ADMIN_EMAILS
from app.core.email import send_verification_email, app_url
from app.core.upload_security import safe_upload_path
from app.services.file_scanner import FileScanner
from app.services.audit import log_audit_event
from app.models.database import User, Document, Output, AudioFile, Transcript, AuditLog
from app.core.client_ip import get_client_ip
from app.models.schemas import (
    SignupRequest, LoginRequest, VerifyEmailRequest, TokenResponse, UserResponse,
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


def _client_ip(request: Request | None) -> str | None:
    """Best-effort client IP (honours X-Forwarded-For behind the proxy)."""
    if request is None:
        return None
    return get_client_ip(request)


async def _scan_upload_or_reject(db, user, request, content, filename, ext, resource_type):
    """Multi-layer upload security scan (SSP §4.2 Stage 2) run BEFORE the file is
    written to disk or processed. Records a `file_scan` audit event (pass/fail
    with the SHA-256 checksum) and, on failure, raises a GENERIC 422 that never
    discloses which check tripped. Returns the SHA-256 checksum on success."""
    result = FileScanner().scan(content, filename, ext, max_size=50 * 1024 * 1024)
    await log_audit_event(
        db, user=user, action="file_scan", resource_type=resource_type,
        result="pass" if result.ok else "fail", ip_address=_client_ip(request),
        details={
            "filename": os.path.basename(filename or "upload"),
            "claimed_type": ext,
            "size_bytes": len(content),
            "sha256": result.sha256,
            "findings": result.findings,
        },
    )
    await db.commit()  # persist the audit record even when the upload is rejected
    if not result.ok:
        raise HTTPException(422, "File rejected: potentially malicious content")
    return result.sha256
# ═══════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════
# P1 SECURITY FIX — Self-registration no longer produces a login-able account.
# Flow: signup -> (email not verified, status='pending_verification', role='pending',
# inactive) -> user clicks emailed link -> verify-email -> 'pending_approval' (or
# activated directly if REQUIRE_ADMIN_APPROVAL is disabled) -> admin approves &
# activates (app/api/admin_users.py) -> only THEN can the user log in.
VERIFICATION_TOKEN_EXPIRE = timedelta(hours=24)

# ── Brute-force / abuse protection (in-memory sliding windows) ───────────────────
# Mirrors the pattern already used by app/api/password_reset.py. Per-process only:
# a multi-worker / HA deployment should back these with Redis. This is an accepted,
# documented limitation for the current single-process deployment — see the ENTERPRISE
# SECURITY REVIEW report. All windows are best-effort and never persist secrets.
_login_fail_by_account = defaultdict(list)   # normalized email -> [failure timestamps]
_login_attempts_by_ip = defaultdict(list)    # ip -> [attempt timestamps]
_signup_by_ip = defaultdict(list)            # ip -> [signup timestamps]

ACCOUNT_LOCK_THRESHOLD = 5      # failed logins per account before temporary lockout
ACCOUNT_LOCK_WINDOW = 900       # 15 minutes
IP_LOGIN_THRESHOLD = 20         # login attempts per IP per window
IP_LOGIN_WINDOW = 900           # 15 minutes
SIGNUP_IP_THRESHOLD = 5         # registrations per IP per window (email-bomb guard)
SIGNUP_IP_WINDOW = 3600         # 1 hour

# Pre-computed bcrypt hash used to equalize login response time when the email does
# not exist — defeats the user-enumeration timing oracle (always run one bcrypt op).
_DUMMY_PW_HASH = hash_password("docuaction-constant-time-login-equalizer")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Optional server-side disposable-email blocking (anti-abuse). Enforced server-side so
# it cannot be bypassed by calling the API directly (the frontend check is advisory).
# Toggle with BLOCK_DISPOSABLE_EMAILS=false. Curated list of well-known throwaway
# providers — intentionally conservative to avoid blocking legitimate corporate mail.
_BLOCK_DISPOSABLE = os.getenv("BLOCK_DISPOSABLE_EMAILS", "true").strip().lower() in ("1", "true", "yes", "on")
_DISPOSABLE_EMAIL_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "10minutemail.com",
    "temp-mail.org", "tempmail.com", "throwawaymail.com", "yopmail.com", "getnada.com",
    "trashmail.com", "sharklasers.com", "dispostable.com", "maildrop.cc", "fakeinbox.com",
    "mailnesia.com", "mohmal.com", "emailondeck.com", "spamgourmet.com", "mytemp.email",
}


def _normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def _is_disposable_email(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return _BLOCK_DISPOSABLE and domain in _DISPOSABLE_EMAIL_DOMAINS


def _prune(bucket: list, window: int) -> list:
    cutoff = time.time() - window
    return [t for t in bucket if t > cutoff]


def _account_locked(email: str) -> bool:
    _login_fail_by_account[email] = _prune(_login_fail_by_account[email], ACCOUNT_LOCK_WINDOW)
    return len(_login_fail_by_account[email]) >= ACCOUNT_LOCK_THRESHOLD


def _ip_login_throttled(ip: str) -> bool:
    _login_attempts_by_ip[ip] = _prune(_login_attempts_by_ip[ip], IP_LOGIN_WINDOW)
    if len(_login_attempts_by_ip[ip]) >= IP_LOGIN_THRESHOLD:
        return True
    _login_attempts_by_ip[ip].append(time.time())
    return False


def _record_login_failure(email: str):
    _login_fail_by_account[email].append(time.time())


def _clear_login_failures(email: str):
    _login_fail_by_account.pop(email, None)


def _signup_throttled(ip: str) -> bool:
    _signup_by_ip[ip] = _prune(_signup_by_ip[ip], SIGNUP_IP_WINDOW)
    if len(_signup_by_ip[ip]) >= SIGNUP_IP_THRESHOLD:
        return True
    _signup_by_ip[ip].append(time.time())
    return False


def _verification_fingerprint(user_id: str, password_hash: str, is_verified: bool) -> str:
    """State fingerprint embedded in the verification token to make it SINGLE-USE.

    Bound to (user, password hash, verified-flag). The token is minted while the
    account is unverified; the instant it is consumed (is_verified flips to True) the
    recomputed fingerprint no longer matches, so the same link cannot be replayed.
    An admin password reset also changes the hash and thus REVOKES any outstanding
    verification token. No server-side token storage required."""
    raw = f"{user_id}:{password_hash}:{bool(is_verified)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _create_verification_token(user_id: str, email: str, password_hash: str) -> str:
    """Signed, expiring, single-use email-verification token (JWT)."""
    payload = {
        "sub": user_id,
        "email": email,
        "type": "email_verification",
        "jti": str(uuid.uuid4()),
        "vfp": _verification_fingerprint(user_id, password_hash, False),
        "exp": datetime.utcnow() + VERIFICATION_TOKEN_EXPIRE,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


async def _audit_auth(db, user_id, action, details=None, request=None, correlation_id=None):
    """Write an enterprise auth audit row. Records timestamp, correlation id, user id,
    email/reason/result (in details), IP address, and user agent. Best-effort — an
    audit-log failure must never break the auth operation."""
    ip = None
    ua = None
    if request is not None:
        ip = get_client_ip(request)
        ua = request.headers.get("user-agent")
    try:
        db.add(AuditLog(
            tenant_id="default",
            user_id=str(user_id) if user_id else None,
            action=action,                       # result (e.g. login_success/login_failed)
            resource_type="auth",
            details={
                "correlation_id": correlation_id or str(uuid.uuid4()),
                "user_agent": ua,
                **(details or {}),               # carries email + reason
                "at": datetime.utcnow().isoformat() + "Z",   # timestamp
            },
            ip_address=ip,
        ))
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass


@router.post("/api/auth/signup", status_code=201, tags=["Auth"])
async def signup(data: SignupRequest, request: Request, db: AsyncSession = Depends(get_db)):
    cid = str(uuid.uuid4())
    ip = get_client_ip(request)
    email = _normalize_email(data.email)

    # Registration throttling — cap self-registrations per IP so the endpoint can't be
    # used to mass-create accounts or bomb the SendGrid sender.
    if _signup_throttled(ip):
        await _audit_auth(db, None, "signup_throttled", {"email": email, "reason": "ip_rate_limited"}, request, cid)
        raise HTTPException(429, "Too many registration attempts. Please try again later.")

    # Server-side input validation (never trust the client).
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address")
    if _is_disposable_email(email):
        await _audit_auth(db, None, "signup_rejected", {"email": email, "reason": "disposable_email"}, request, cid)
        raise HTTPException(400, "Disposable email addresses are not allowed. Please use a permanent email address.")
    if len(data.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    # Duplicate protection — case-insensitive so Foo@x.com and foo@x.com collide.
    existing = await db.execute(select(User).where(func.lower(User.email) == email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")

    # SECURITY: create the account in the PENDING state — NOT active, NOT a real role.
    # role/is_active/status are hard-coded server-side; SignupRequest does not accept
    # them, so a public registrant can never self-assign a role or activate.
    user = User(
        email=email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        company=data.company,
        # HARDENING: a public registrant must not self-assign a paid entitlement.
        # Plan is forced to 'free' server-side regardless of the request body; an
        # administrator assigns the real plan/role during approval.
        plan="free",
        role="pending",
        is_verified=False,
        is_active=False,
        status="pending_verification",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Generate a secure, single-use verification token and email a verification link
    # via the existing SendGrid integration. NO token is returned; user is NOT signed in.
    token = _create_verification_token(str(user.id), user.email, user.password_hash)
    verify_url = f"{app_url()}/verify-email?token={token}"
    email_result = await send_verification_email(user.email, user.full_name or "", verify_url)

    await _audit_auth(db, user.id, "user_registered",
                      {"email": user.email, "reason": "self_registration",
                       "email_sent": email_result.get("sent", False)}, request, cid)

    return {
        "status": "pending_verification",
        "message": "Account created. Please check your email and click the verification "
                   "link to continue. After verifying, an administrator must approve and "
                   "activate your account before you can sign in.",
    }


@router.post("/api/auth/verify-email", tags=["Auth"])
async def verify_email(data: VerifyEmailRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Validate the emailed verification token and mark the email verified.

    The token is single-use and replay-protected via a state fingerprint (see
    _verification_fingerprint). On success the account moves to 'pending_approval'
    (awaiting an administrator), or straight to 'active' when REQUIRE_ADMIN_APPROVAL
    is disabled via config."""
    cid = str(uuid.uuid4())
    token = (data.token or "").strip()
    if not token:
        raise HTTPException(400, "Verification token is required")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != "email_verification":
            raise HTTPException(400, "Invalid verification token")
        user_id = payload.get("sub")
    except JWTError:
        # Covers expired, tampered, and malformed tokens.
        await _audit_auth(db, None, "email_verification_failed",
                          {"reason": "invalid_or_expired"}, request, cid)
        raise HTTPException(400, "Invalid or expired verification token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(400, "Invalid or expired verification token")

    # SINGLE-USE / REPLAY / REVOCATION: the token's fingerprint must match the account's
    # CURRENT state. Once consumed (is_verified True) or after a password reset, the
    # fingerprint changes and the token is rejected — it can never be replayed.
    expected = _verification_fingerprint(str(user.id), user.password_hash, user.is_verified)
    if payload.get("vfp") != expected:
        await _audit_auth(db, user.id, "email_verification_failed",
                          {"email": user.email, "reason": "token_used_or_revoked"}, request, cid)
        raise HTTPException(400, "This verification link has already been used or is no longer valid.")

    user.is_verified = True
    if settings.REQUIRE_ADMIN_APPROVAL:
        user.status = "pending_approval"
        user.is_active = False
        message = ("Email verified. Your account is now awaiting administrator approval. "
                   "You'll be able to sign in once an administrator activates it.")
    else:
        # Approval disabled by configuration — verification alone activates the account.
        user.status = "active"
        user.is_active = True
        if (user.role or "pending") == "pending":
            user.role = "contributor"
        message = "Email verified. Your account is active — you can now sign in."
    await db.commit()
    await _audit_auth(db, user.id, "email_verified",
                      {"email": user.email, "reason": "token_valid", "new_status": user.status}, request, cid)
    return {"status": user.status, "message": message}


@router.post("/api/auth/login", response_model=TokenResponse, tags=["Auth"])
async def login(data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    cid = str(uuid.uuid4())
    email = _normalize_email(data.email)

    # LOGIN-004 / LOGIN-005 — an absent email or password is a malformed request,
    # not a failed authentication attempt. Rejecting it here means an empty field
    # never reaches the database or the password hasher.
    #
    # This is NOT an enumeration leak: the response depends only on what the
    # client sent, never on whether any account exists. It also must not count
    # toward account lockout — there is no account to lock, and letting empty
    # submissions consume attempts would let anyone lock out a known address by
    # posting a blank form.
    if not email or not (data.password or "").strip():
        raise HTTPException(400, "Email and password are both required.")

    # Brute-force protection: throttle per IP, and temporarily lock an account after
    # repeated failures. Both return 429 without confirming whether the email exists.
    if _ip_login_throttled(get_client_ip(request)):
        await _audit_auth(db, None, "login_throttled", {"email": email, "reason": "ip_rate_limited"}, request, cid)
        raise HTTPException(429, "Too many login attempts. Please try again later.")
    if _account_locked(email):
        await _audit_auth(db, None, "login_blocked", {"email": email, "reason": "account_locked"}, request, cid)
        raise HTTPException(429, "Account temporarily locked due to repeated failed logins. Try again later.")

    # Case-insensitive lookup so existing rows stored with any casing still resolve.
    result = await db.execute(select(User).where(func.lower(User.email) == email))
    user = result.scalar_one_or_none()

    # TIMING-ATTACK / ENUMERATION MITIGATION: always perform exactly one bcrypt
    # comparison, even when the email is unknown, so response time cannot reveal
    # whether an account exists.
    password_ok = verify_password(data.password, user.password_hash) if user else verify_password(data.password, _DUMMY_PW_HASH)
    if not user or not password_ok:
        _record_login_failure(email)
        await _audit_auth(db, user.id if user else None, "login_failed",
                          {"email": email, "reason": "invalid_credentials"}, request, cid)
        raise HTTPException(401, "Invalid email or password")

    # SECURITY GATES — order matters (verification -> approval -> disabled). Existing
    # accounts are grandfathered (is_verified/status default to verified/active), so
    # these blocks only affect accounts moving through the new registration flow.
    # These are valid-credential outcomes and do NOT count toward account lockout.
    if not getattr(user, "is_verified", True):
        await _audit_auth(db, user.id, "login_blocked", {"email": user.email, "reason": "email_not_verified"}, request, cid)
        raise HTTPException(403, "Please verify your email before signing in.")

    status = (getattr(user, "status", "active") or "active")
    if status == "pending_approval" and not user.is_active:
        await _audit_auth(db, user.id, "login_blocked", {"email": user.email, "reason": "pending_approval"}, request, cid)
        raise HTTPException(403, "Your account is awaiting administrator approval.")

    if not user.is_active or status == "disabled":
        await _audit_auth(db, user.id, "login_blocked", {"email": user.email, "reason": "disabled"}, request, cid)
        raise HTTPException(403, "Your account has been disabled. Contact your administrator.")

    _clear_login_failures(email)
    await _audit_auth(db, user.id, "login_success", {"email": user.email, "reason": "credentials_valid"}, request, cid)
    tokens = create_token_pair(str(user.id), user.role, user.email)
    return TokenResponse(access_token=tokens["access_token"], user=UserResponse.model_validate(user))


@router.post("/api/auth/logout", tags=["Auth"])
async def logout(request: Request, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Server-side session invalidation. Stamps the user's revocation epoch so every
    outstanding access/refresh token (which carry an issued-at) is rejected on its next
    use — a real logout, not just a client-side token discard."""
    cid = str(uuid.uuid4())
    user.tokens_revoked_at = datetime.utcnow()
    await db.commit()
    await _audit_auth(db, user.id, "logout", {"email": user.email, "reason": "user_logout"}, request, cid)
    return {"status": "logged_out"}
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
    request: Request,
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
    # Validate extension and build a traversal-safe UUID storage path BEFORE reading
    # the body (client filename never touches the path — see app/core/upload_security).
    fpath, ext = safe_upload_path(UPLOAD_DIR / "documents", file.filename, allowed=ALLOWED_DOCS)
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 50MB)")
    # Malware/file security scan (SSP §4.2 Stage 2) — BEFORE writing or processing.
    checksum = await _scan_upload_or_reject(db, user, request, content, file.filename, ext.replace(".", ""), "document")
    fpath.write_bytes(content)
    # Save to database — keep the ORIGINAL filename only as (basename) metadata.
    doc = Document(
        user_id=user.id, filename=os.path.basename(file.filename or "upload"), file_path=str(fpath),
        file_type=ext.replace(".", ""), file_size_bytes=len(content), checksum_sha256=checksum, status="processing",
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
    # Validate extension and build a traversal-safe UUID storage path (see
    # app/core/upload_security) — the client filename is never used to build the path.
    fpath, ext = safe_upload_path(UPLOAD_DIR / "audio", file.filename, allowed=ALLOWED_AUDIO)
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(400, "Audio file too large. Maximum 25MB.")
    # Check transcription plan limits
    from app.services.plan_enforcement import check_transcription_limit
    await check_transcription_limit(db, user.id, getattr(user, 'plan', 'free'))
    fpath.write_bytes(content)
    audio = AudioFile(
        user_id=user.id, filename=os.path.basename(file.filename or "audio"), file_path=str(fpath),
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
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Upload a document for AI processing."""
    # Validate extension and build a traversal-safe UUID storage path (see
    # app/core/upload_security) — the client filename is never used to build the path.
    fpath, ext = safe_upload_path(UPLOAD_DIR / "documents", file.filename, allowed=(ALLOWED_DOCS | ALLOWED_AUDIO))
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 50MB)")
    # Malware/file security scan (SSP §4.2 Stage 2) — BEFORE writing to disk.
    checksum = await _scan_upload_or_reject(db, user, request, content, file.filename, ext.replace(".", ""), "document")
    fpath.write_bytes(content)
    doc = Document(
        user_id=user.id, filename=os.path.basename(file.filename or "upload"), file_path=str(fpath),
        file_type=ext.replace(".", ""), file_size_bytes=len(content), checksum_sha256=checksum, status="uploaded",
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
    request: Request,                                                     # ← CONTEXT FIX: added Request
    action_type: str = Query("summary", description="AI action type"),    # ← CONTEXT FIX: explicit Query
    context: str = Query("", description="Intelligence Mode focus instructions"),  # ← CONTEXT FIX: reads context param
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Generate AI output from an uploaded document using proper text extraction.
    
    The 'context' parameter receives Intelligence Mode focus instructions from the frontend.
    When a user selects Proposal Response, Legal Risk, Executive Decision, or Compliance Audit
    mode, the frontend sends the playbook focus text as the 'context' query parameter.
    This is prepended to the document text so the AI focuses its analysis accordingly.
    """
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

    # ═══════════════════════════════════════════════════
    # CONTEXT FIX — Intelligence Mode focus injection
    # When context is provided (from playbook cards or custom focus box),
    # prepend it as analysis instructions BEFORE the document content.
    # This makes the AI focus on what the user asked for.
    # Without this, clicking "Proposal Response" or "Legal Risk" has
    # ZERO effect on AI output — the context is sent but ignored.
    # ═══════════════════════════════════════════════════
    document_text = text[:50000]
    if context.strip():                                                    # ← CONTEXT FIX
        document_text = (                                                  # ← CONTEXT FIX
            f"ANALYSIS FOCUS INSTRUCTIONS:\n"                              # ← CONTEXT FIX
            f"{context}\n"                                                 # ← CONTEXT FIX
            f"\n---\n"                                                     # ← CONTEXT FIX
            f"DOCUMENT CONTENT:\n"                                         # ← CONTEXT FIX
            f"{document_text}"                                             # ← CONTEXT FIX
        )                                                                  # ← CONTEXT FIX
        logger.info(f"Context focus applied: {context[:100]}...")          # ← CONTEXT FIX

    # Process through AI engine
    from app.services.ai_engine import process_document
    import json
    ai_result = await process_document(
        document_text=document_text,                                       # ← CONTEXT FIX: uses focused text
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
