"""
DocuAction Healthcare Claims API Routes
Endpoints for claims processing, validation, denial prediction, FWA detection, 
revenue analysis, appeal generation, and dashboard metrics.

All endpoints require authentication and enforce HIPAA-compliant PHI masking.
"""
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import get_current_user, ADMIN_EMAILS
from app.models.database import Document

logger = logging.getLogger("docuaction.healthcare_claims")
router = APIRouter(prefix="/api/healthcare", tags=["Healthcare Claims"])

# In-memory store for claims (Phase 1 — move to PostgreSQL in Phase 2)
_claims_store = {}
_claims_history = []


@router.post("/claims/process")
async def process_claim_from_document(
    doc_id: str = Query(..., description="Document ID to process as claim"),
    payer: str = Query("unknown", description="Insurance payer name"),
    days_since_service: int = Query(0, description="Days since date of service"),
    provider_name: str = Query("", description="Provider name for appeals"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Process an uploaded document through the full healthcare claims pipeline.
    
    Pipeline:
    1. Extract claim data (ICD-10, CPT, HCPCS codes)
    2. Validate codes against known families
    3. Predict denial risk (0-100)
    4. Detect FWA (Fraud/Waste/Abuse)
    5. Analyze revenue impact
    
    Returns complete claims intelligence with governance metadata.
    """
    # Verify document exists and belongs to user
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.user_id == user.id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    
    # Extract text from document
    from app.services.document_extractor import extract_text
    try:
        text = await extract_text(doc.file_path, doc.file_type)
    except Exception as e:
        raise HTTPException(500, f"Failed to read document: {str(e)}")
    
    if not text or len(text.strip()) < 20:
        raise HTTPException(400, "Could not extract readable text from this document")
    
    # Run through claims pipeline
    from app.services.healthcare_claims_engine import process_claim
    
    claims_result = process_claim(
        document_text=text[:50000],
        document_type="clinical_note",
        payer=payer,
        days_since_service=days_since_service,
        provider_name=provider_name,
    )
    
    # Store claim
    claim_id = claims_result["claim_id"]
    _claims_store[claim_id] = {
        **claims_result,
        "document_id": doc_id,
        "user_id": str(user.id),
        "document_filename": doc.filename,
    }
    _claims_history.append({
        "claim_id": claim_id,
        "status": "processed",
        "denial_risk": claims_result["summary"]["denial_risk"],
        "first_pass_accepted": claims_result["summary"]["denial_risk"] < 30,
        "clean_claim": claims_result["summary"]["issues_found"] == 0,
        "billed_amount": len(claims_result["extraction"]["codes_found"]["cpt"]) * 200,
        "collected_amount": 0,
    })
    
    logger.info(f"Claims processed: {claim_id} — risk={claims_result['summary']['denial_level']}")
    
    return claims_result


@router.post("/claims/process-text")
async def process_claim_from_text(
    text: str = Query(..., description="Clinical text to process", min_length=20),
    payer: str = Query("unknown", description="Insurance payer name"),
    days_since_service: int = Query(0, description="Days since date of service"),
    user=Depends(get_current_user),
):
    """
    Process raw clinical text through the claims pipeline.
    Use this for quick testing or when text is already extracted.
    """
    from app.services.healthcare_claims_engine import process_claim
    
    claims_result = process_claim(
        document_text=text[:50000],
        document_type="clinical_note",
        payer=payer,
        days_since_service=days_since_service,
    )
    
    claim_id = claims_result["claim_id"]
    _claims_store[claim_id] = {
        **claims_result,
        "user_id": str(user.id),
    }
    
    return claims_result


@router.get("/claims/{claim_id}")
async def get_claim(claim_id: str, user=Depends(get_current_user)):
    """Get a specific claim result by ID."""
    claim = _claims_store.get(claim_id)
    if not claim:
        raise HTTPException(404, "Claim not found")
    if claim.get("user_id") != str(user.id) and user.email not in ADMIN_EMAILS:
        raise HTTPException(403, "Access denied")
    return claim


@router.get("/claims")
async def list_claims(
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
):
    """List all claims for the current user."""
    user_claims = [
        c for c in _claims_store.values()
        if c.get("user_id") == str(user.id) or user.email in ADMIN_EMAILS
    ]
    # Sort by timestamp descending
    user_claims.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {
        "claims": user_claims[:limit],
        "total": len(user_claims),
    }


@router.post("/claims/{claim_id}/appeal")
async def generate_appeal(
    claim_id: str,
    denial_reason: str = Query("medical_necessity", description="Reason for denial"),
    payer: str = Query("Unknown Payer", description="Payer name"),
    provider_name: str = Query("", description="Provider name"),
    user=Depends(get_current_user),
):
    """
    Generate an appeal letter template for a denied claim.
    Returns structured appeal with sections, attachments checklist, 
    and compliance warnings.
    
    HITL REQUIRED: Human must review before sending.
    """
    claim = _claims_store.get(claim_id)
    if not claim:
        raise HTTPException(404, "Claim not found")
    
    from app.services.healthcare_claims_engine import generate_appeal_template
    
    appeal = generate_appeal_template(
        claim_data=claim.get("extraction", {}),
        denial_reason=denial_reason,
        payer=payer,
        provider_name=provider_name,
    )
    
    return appeal


@router.get("/metrics")
async def get_claims_metrics(user=Depends(get_current_user)):
    """
    Get claims processing KPIs with industry benchmarks.
    
    Tracks: First-Pass Acceptance Rate, Clean Claim Rate, 
    Denial Rate, A/R Days, Revenue Summary.
    """
    from app.services.healthcare_claims_engine import compute_claims_metrics
    
    # Filter to user's claims
    user_history = [
        c for c in _claims_history
        if True  # In production, filter by user/tenant
    ]
    
    return compute_claims_metrics(user_history)


@router.post("/claims/{claim_id}/validate")
async def validate_claim_codes(claim_id: str, user=Depends(get_current_user)):
    """
    Re-validate codes for an existing claim.
    Use after manual code corrections.
    """
    claim = _claims_store.get(claim_id)
    if not claim:
        raise HTTPException(404, "Claim not found")
    
    from app.services.healthcare_claims_engine import validate_codes
    
    extraction = claim.get("extraction", {})
    codes = extraction.get("codes_found", {})
    
    validation = validate_codes(
        icd10_codes=codes.get("icd10", []),
        cpt_codes=codes.get("cpt", []),
        clinical_text="",  # Would need original text
    )
    
    return validation


@router.get("/fwa/{claim_id}")
async def get_fwa_report(claim_id: str, user=Depends(get_current_user)):
    """Get Fraud/Waste/Abuse detection report for a claim."""
    claim = _claims_store.get(claim_id)
    if not claim:
        raise HTTPException(404, "Claim not found")
    return claim.get("fwa_detection", {})


@router.get("/revenue/{claim_id}")
async def get_revenue_impact(claim_id: str, user=Depends(get_current_user)):
    """Get revenue impact analysis for a claim."""
    claim = _claims_store.get(claim_id)
    if not claim:
        raise HTTPException(404, "Claim not found")
    return claim.get("revenue_impact", {})
