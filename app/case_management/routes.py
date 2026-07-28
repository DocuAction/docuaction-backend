"""
DocuAction AI — Case Management Module
FastAPI Routes — Complete API Surface

Add to main.py:
    safe_load("app.case_management", "case-management")
"""

import uuid
import logging
from datetime import datetime, date
from typing import Optional, List
from fastapi import APIRouter, UploadFile, File, Form, Query, HTTPException, Depends
from pydantic import BaseModel

from app.core.security import get_current_user
from .phi_audit import audit_phi_access

from .services.ccm_engine import (
    voice_to_ccm_note,
    extract_clinical_facts,
    generate_ccm_note,
    generate_tcm_note,
    generate_care_plan,
    generate_patient_education,
    determine_billing_code,
    CPT_REQUIREMENTS,
)
from .services.discharge_engine import (
    generate_discharge_summary,
    generate_government_case_document,
    generate_sdoh_assessment,
)

logger = logging.getLogger("docuaction.case_management.routes")

# SECURITY (AUTHZ-01): authentication is enforced at the ROUTER level, so every
# endpoint below — present and future — requires a valid bearer token. This module
# accepts PHI in request bodies (patient names, MRNs, DOBs, clinical transcripts)
# and forwards it to the Anthropic API; before this gate it was reachable
# anonymously, which is an unauthenticated PHI disclosure under HIPAA
# §164.502 and an unmetered LLM-cost abuse vector.
#
# Do NOT move this to per-endpoint decorators: a router-level dependency cannot be
# forgotten when a new route is added. get_current_user also enforces account
# disable / pending-approval / session-revocation state on every request.
cm_router = APIRouter(
    prefix="/api/v1/case-management",
    tags=["Case Management"],
    # audit_phi_access is router-level for the same reason get_current_user is:
    # a control attached to individual handlers is a control that route 23
    # will be missing. HIPAA 164.312(b).
    dependencies=[Depends(get_current_user), Depends(audit_phi_access)],
)
router = cm_router  # safe_load expects mod.router


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────

class PatientBase(BaseModel):
    first_name: str
    last_name: str
    mrn: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    pcp_name: Optional[str] = None
    pcp_npi: Optional[str] = None
    diagnoses_icd10: Optional[List[str]] = []
    hcc_codes: Optional[List[str]] = []
    risk_tier: Optional[str] = "MODERATE"
    cm_module_type: Optional[str] = "CCM"
    insurance_primary: Optional[dict] = None
    medications: Optional[list] = []


class CCMNoteRequest(BaseModel):
    patient_context: dict
    voice_transcript: Optional[str] = None
    clinical_notes: Optional[str] = None
    case_manager_name: Optional[str] = "Case Manager"
    total_minutes: Optional[int] = 20
    provider_type: Optional[str] = "clinical_staff"
    note_type: Optional[str] = "CCM_PROGRESS"
    complexity: Optional[str] = "non_complex"
    cumulative_minutes_this_month: Optional[int] = 0
    service_date: Optional[str] = None


class TCMNoteRequest(BaseModel):
    patient_context: dict
    discharge_info: dict
    contact_info: dict
    visit_info: dict
    complexity: Optional[str] = "moderate"


class CarePlanRequest(BaseModel):
    patient_context: dict
    clinical_notes: Optional[str] = None
    goals_input: Optional[str] = ""
    include_sdoh: Optional[bool] = True
    language: Optional[str] = "English"


class DischargeRequest(BaseModel):
    patient_context: dict
    admission_notes: Optional[str] = ""
    progress_notes: Optional[str] = ""
    procedure_notes: Optional[str] = ""
    medications: Optional[List] = []
    discharge_info: Optional[dict] = {}
    attending_physician: Optional[str] = "Attending Physician"
    include_patient_instructions: Optional[bool] = True


class EducationRequest(BaseModel):
    patient_context: dict
    topic: str
    diagnosis: str
    reading_level: Optional[int] = 6
    language: Optional[str] = "English"


class SDOHRequest(BaseModel):
    patient_context: dict
    screening_responses: dict


class GovCaseRequest(BaseModel):
    case_type: str
    case_reference: str
    agency: Optional[str] = ""
    deadline: Optional[str] = ""
    analyst_name: Optional[str] = "Case Analyst"
    case_facts: dict


class BillingCodeRequest(BaseModel):
    total_minutes: int
    provider_type: str
    note_type: str = "CCM_PROGRESS"
    complexity: str = "non_complex"
    cumulative_minutes_this_month: int = 0


# ─── Dashboard / Overview ────────────────────────────────────────────────────

@cm_router.get("/dashboard/stats", summary="Case management dashboard statistics")
async def get_dashboard_stats(tenant_id: str = Query("default")):
    """Dashboard statistics for case management home screen."""
    return {
        "tenant_id": tenant_id,
        "stats": {
            "active_patients": 0,
            "notes_this_month": 0,
            "billable_minutes_this_month": 0,
            "estimated_monthly_revenue": 0.0,
            "ccm_eligible_not_enrolled": 0,
            "tcm_open_transitions": 0,
            "care_plans_due_review": 0,
            "action_items_pending": 0,
        },
        "billing_summary": {
            "99490_count": 0,
            "99491_count": 0,
            "99487_count": 0,
            "99495_count": 0,
            "99496_count": 0,
            "total_billed_ytd": 0.0,
        },
        "quality_metrics": {
            "documentation_completion_rate": 0.0,
            "avg_minutes_per_patient": 0,
            "monthly_contact_rate": 0.0,
            "care_plan_current_rate": 0.0,
        },
        "note": "Wire to database for production metrics.",
    }


# ─── Patient Management ───────────────────────────────────────────────────────

@cm_router.get("/patients", summary="List case management patients")
async def list_patients(
    tenant_id: str = Query("default"),
    status: Optional[str] = None,
    risk_tier: Optional[str] = None,
    module_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List patients enrolled in case management."""
    return {
        "patients": [],
        "total": 0,
        "filters_applied": {
            "status": status,
            "risk_tier": risk_tier,
            "module_type": module_type,
        },
        "note": "Wire to database for production use.",
    }


@cm_router.post("/patients", summary="Create case management patient")
async def create_patient(patient: PatientBase, tenant_id: str = Query("default")):
    """Enroll a patient in case management."""
    patient_id = str(uuid.uuid4())
    return {
        "patient_id": patient_id,
        "tenant_id": tenant_id,
        "status": "PENDING_CONSENT",
        "created_at": datetime.utcnow().isoformat(),
        "patient": patient.dict(),
        "next_steps": [
            "Obtain and document patient consent",
            "Complete initial assessment",
            "Generate initial care plan",
            "Schedule first case management call",
        ],
    }


@cm_router.get("/patients/{patient_id}", summary="Get patient details")
async def get_patient(patient_id: str, tenant_id: str = Query("default")):
    """Get patient details with billing summary."""
    return {
        "patient_id": patient_id,
        "note": "Wire to database for production use.",
    }


# ─── CCM Note Generation (Core Feature) ──────────────────────────────────────

@cm_router.post("/notes/voice-to-note",
    summary="Voice transcript → billable CCM note (Core WOW Feature)")
async def voice_to_note(request: CCMNoteRequest):
    """
    THE CORE FEATURE: Transform voice transcript into a complete,
    billing-compliant CCM/TCM/PCM note in under 30 seconds.

    Pipeline:
    1. Extract clinical facts from transcript (Haiku)
    2. Determine CPT billing code (rules-based)
    3. Generate note body (Sonnet/Opus)
    4. Compliance check (Haiku)

    Input: Voice transcript OR clinical notes text
    Output: Complete billable note + CPT code + estimated reimbursement
    """
    if not request.voice_transcript and not request.clinical_notes:
        raise HTTPException(400, "Provide voice_transcript or clinical_notes")

    result = await voice_to_ccm_note(
        voice_transcript=request.voice_transcript or request.clinical_notes or "",
        patient_context=request.patient_context,
        case_manager_name=request.case_manager_name,
        total_minutes=request.total_minutes,
        provider_type=request.provider_type,
        note_type=request.note_type,
        complexity=request.complexity,
        cumulative_minutes_this_month=request.cumulative_minutes_this_month,
        service_date=request.service_date,
    )

    return {
        **result,
        "generated_at": datetime.utcnow().isoformat(),
        "note_id": str(uuid.uuid4()),
        "status": "AI_GENERATED",
        "requires_review": True,
        "ai_disclosure_required": True,
    }


@cm_router.post("/notes/generate",
    summary="Generate CCM note from structured input")
async def generate_note(request: CCMNoteRequest):
    """Generate a CCM note from structured text input (no voice)."""
    clinical_facts = await extract_clinical_facts(
        voice_transcript=request.voice_transcript or "",
        clinical_notes=request.clinical_notes or "",
        patient_context=request.patient_context,
    )
    billing_info = determine_billing_code(
        total_minutes=request.total_minutes,
        provider_type=request.provider_type,
        note_type=request.note_type,
        complexity=request.complexity,
        cumulative_minutes_this_month=request.cumulative_minutes_this_month,
    )
    note_result = await generate_ccm_note(
        clinical_facts=clinical_facts,
        patient_context=request.patient_context,
        billing_info=billing_info,
        case_manager_name=request.case_manager_name,
        service_date=request.service_date,
        total_minutes=request.total_minutes,
    )

    return {
        **note_result,
        "note_id": str(uuid.uuid4()),
        "clinical_facts": clinical_facts,
        "billing_info": billing_info,
        "generated_at": datetime.utcnow().isoformat(),
        "status": "AI_GENERATED",
        "requires_review": True,
    }


@cm_router.post("/notes/tcm",
    summary="Generate TCM note — Transitional Care Management")
async def generate_tcm(request: TCMNoteRequest):
    """
    Generate CPT 99495 or 99496 Transitional Care Management note.
    Covers: discharge review, initial contact, face-to-face visit,
    medication reconciliation, and care coordination.
    """
    result = await generate_tcm_note(
        patient_context=request.patient_context,
        discharge_info=request.discharge_info,
        contact_info=request.contact_info,
        visit_info=request.visit_info,
        complexity=request.complexity,
    )
    return {
        **result,
        "note_id": str(uuid.uuid4()),
        "generated_at": datetime.utcnow().isoformat(),
        "status": "AI_GENERATED",
        "requires_review": True,
    }


@cm_router.get("/notes", summary="List case management notes")
async def list_notes(
    patient_id: Optional[str] = None,
    note_type: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None,
    tenant_id: str = Query("default"),
    limit: int = 50,
):
    """List case management notes with filters."""
    return {
        "notes": [],
        "total": 0,
        "filters": {"patient_id": patient_id, "note_type": note_type, "status": status, "month": month},
        "note": "Wire to database for production use.",
    }


@cm_router.patch("/notes/{note_id}/approve",
    summary="Approve and sign case management note")
async def approve_note(note_id: str, signed_by: str = Query(...)):
    """Clinician approval and signature of AI-generated note."""
    return {
        "note_id": note_id,
        "status": "SIGNED",
        "signed_by": signed_by,
        "signed_at": datetime.utcnow().isoformat(),
        "ready_to_bill": True,
        "audit_entry": {
            "action": "NOTE_SIGNED",
            "performed_by": signed_by,
            "timestamp": datetime.utcnow().isoformat(),
            "ai_disclosure_acknowledged": True,
        },
    }


# ─── Care Plan ────────────────────────────────────────────────────────────────

@cm_router.post("/care-plans/generate",
    summary="Generate comprehensive care plan")
async def generate_care_plan_route(request: CarePlanRequest):
    """
    Generate a comprehensive care plan with SMART goals.
    Uses Opus for complex/high-risk patients, Sonnet for standard.
    CMS CCM care plan requirements compliant.
    """
    clinical_facts = {}
    if request.clinical_notes:
        clinical_facts = await extract_clinical_facts(
            clinical_notes=request.clinical_notes,
            patient_context=request.patient_context,
        )

    result = await generate_care_plan(
        patient_context=request.patient_context,
        clinical_facts=clinical_facts,
        goals_input=request.goals_input,
        include_sdoh=request.include_sdoh,
        language=request.language,
    )

    return {
        **result,
        "plan_id": str(uuid.uuid4()),
        "generated_at": datetime.utcnow().isoformat(),
        "status": "AI_GENERATED",
        "requires_review": True,
        "review_date_recommended": (
            datetime.utcnow().replace(month=datetime.utcnow().month + 3
            if datetime.utcnow().month <= 9 else 1).date().isoformat()
            if datetime.utcnow().month <= 9
            else f"{datetime.utcnow().year + 1}-{str(datetime.utcnow().month - 9).zfill(2)}-01"
        ),
    }


@cm_router.get("/care-plans", summary="List care plans")
async def list_care_plans(patient_id: Optional[str] = None, tenant_id: str = Query("default")):
    """List care plans."""
    return {
        "care_plans": [],
        "total": 0,
        "note": "Wire to database for production use.",
    }


# ─── Discharge Planning ───────────────────────────────────────────────────────

@cm_router.post("/discharge/generate",
    summary="Generate Joint Commission compliant discharge summary")
async def generate_discharge_route(request: DischargeRequest):
    """
    Generate a complete discharge summary meeting:
    - Joint Commission Standard RC.02.01.25
    - CMS Conditions of Participation §482.24
    - CMS Conditions of Participation §482.43 (Discharge Planning)

    Multi-document synthesis: H&P + progress notes + procedure notes
    → Complete discharge summary + patient instructions
    Must be completed within 24 hours of discharge.
    """
    result = await generate_discharge_summary(
        patient_context=request.patient_context,
        admission_notes=request.admission_notes,
        progress_notes=request.progress_notes,
        procedure_notes=request.procedure_notes,
        medications=request.medications,
        discharge_info=request.discharge_info,
        attending_physician=request.attending_physician,
        include_patient_instructions=request.include_patient_instructions,
    )

    return {
        **result,
        "discharge_id": str(uuid.uuid4()),
        "generated_at": datetime.utcnow().isoformat(),
        "status": "AI_GENERATED",
        "requires_physician_signature": True,
        "compliance_note": "Must be countersigned by attending physician. CMS requires completion within 24 hours of discharge.",
    }


# ─── Patient Education ────────────────────────────────────────────────────────

@cm_router.post("/education/generate",
    summary="Generate patient education materials")
async def generate_education(request: EducationRequest):
    """
    Generate patient education at specified reading level.
    Section 1557 compliant — available in English and Spanish.
    """
    result = await generate_patient_education(
        topic=request.topic,
        diagnosis=request.diagnosis,
        patient_context=request.patient_context,
        reading_level=request.reading_level,
        language=request.language,
    )
    return {
        **result,
        "education_id": str(uuid.uuid4()),
        "generated_at": datetime.utcnow().isoformat(),
        "section_1557_compliant": True,
    }


@cm_router.get("/education/topics",
    summary="Available education topic templates")
async def get_education_topics():
    """Returns available education topics by condition category."""
    return {
        "topics": {
            "Diabetes": [
                "Blood Glucose Monitoring", "Insulin Administration",
                "Hypoglycemia Management", "Diabetic Foot Care",
                "Carbohydrate Counting", "HbA1c Understanding",
            ],
            "Heart Failure": [
                "Daily Weight Monitoring", "Sodium Restriction",
                "Fluid Restriction", "Medication Compliance",
                "Warning Signs", "When to Call Your Doctor",
            ],
            "COPD": [
                "Inhaler Technique", "Breathing Exercises",
                "Oxygen Use", "Avoiding Triggers",
                "Action Plan for Exacerbations",
            ],
            "Hypertension": [
                "Blood Pressure Monitoring", "DASH Diet",
                "Medication Adherence", "Lifestyle Modifications",
                "Target Blood Pressure Goals",
            ],
            "CKD": [
                "Kidney-Friendly Diet", "Fluid Management",
                "Medication Safety with CKD", "Dialysis Preparation",
                "Lab Values Explained",
            ],
            "Mental Health": [
                "Medication Management", "Coping Strategies",
                "Crisis Plan", "Community Resources",
                "Sleep Hygiene",
            ],
            "General": [
                "Fall Prevention", "Advance Directives",
                "Medication Safety", "Nutrition Basics",
                "Exercise for Chronic Disease",
            ],
        }
    }


# ─── SDOH Assessment ──────────────────────────────────────────────────────────

@cm_router.post("/sdoh/assess",
    summary="Generate SDOH assessment narrative")
async def generate_sdoh_route(request: SDOHRequest):
    """
    Generate SDOH assessment narrative from AHC HRSN screening responses.
    Identifies social needs and recommends community resources.
    """
    result = await generate_sdoh_assessment(
        patient_context=request.patient_context,
        screening_responses=request.screening_responses,
    )
    return {
        **result,
        "assessment_id": str(uuid.uuid4()),
        "generated_at": datetime.utcnow().isoformat(),
    }


# ─── Government Case Management ───────────────────────────────────────────────

@cm_router.post("/government/cases/generate",
    summary="Generate government case document")
async def generate_gov_case(request: GovCaseRequest):
    """
    Generate government case management documents:
    - MEDICARE_APPEAL
    - MEDICAID_APPEAL
    - VA_BENEFIT
    - FWA_INVESTIGATION (uses Opus for complex reasoning)
    - MEDICAID_ELIGIBILITY
    - CMS_COMPLAINT
    """
    result = await generate_government_case_document(
        case_type=request.case_type,
        case_reference=request.case_reference,
        agency=request.agency,
        deadline=request.deadline,
        analyst_name=request.analyst_name,
        case_facts=request.case_facts,
    )
    return {
        **result,
        "case_id": str(uuid.uuid4()),
        "generated_at": datetime.utcnow().isoformat(),
        "status": "AI_GENERATED",
        "requires_analyst_review": True,
    }


@cm_router.get("/government/cases",
    summary="List government cases")
async def list_gov_cases(
    case_type: Optional[str] = None,
    status: Optional[str] = None,
    tenant_id: str = Query("default"),
):
    """List government case management cases."""
    return {
        "cases": [],
        "total": 0,
        "note": "Wire to database for production use.",
    }


# ─── Billing Intelligence ─────────────────────────────────────────────────────

@cm_router.post("/billing/determine-code",
    summary="Determine appropriate CPT billing code")
async def determine_code(request: BillingCodeRequest):
    """
    Rules-based CPT code determination for CCM/TCM/PCM services.
    Returns primary code, add-on codes, and estimated reimbursement.
    """
    result = determine_billing_code(
        total_minutes=request.total_minutes,
        provider_type=request.provider_type,
        note_type=request.note_type,
        complexity=request.complexity,
        cumulative_minutes_this_month=request.cumulative_minutes_this_month,
    )
    return {
        **result,
        "checked_at": datetime.utcnow().isoformat(),
        "documentation_requirements": result.get("requirements", {}).get("required_elements", []),
    }


@cm_router.get("/billing/cpt-reference",
    summary="CPT code reference guide")
async def get_cpt_reference():
    """Complete CCM/TCM/PCM CPT code reference with requirements."""
    return {
        "cpt_codes": CPT_REQUIREMENTS,
        "2026_updates": {
            "note": "CMS increased CCM reimbursement rates ~10% for 2026",
            "effective_date": "2026-01-01",
        },
        "billing_tips": [
            "CCM requires documented patient consent — once per calendar year",
            "Only one provider can bill CCM per patient per month",
            "TCM and CCM cannot be billed in the same calendar month",
            "Document specific minutes — not just 'approximately 20 minutes'",
            "Include physician supervision statement for clinical staff services",
            "Care plan must be in place before billing CCM",
        ],
    }


@cm_router.get("/billing/monthly-summary",
    summary="Monthly billing summary by patient")
async def get_monthly_billing(
    month: str = Query(..., description="YYYY-MM format"),
    tenant_id: str = Query("default"),
):
    """Monthly CCM/TCM billing summary for revenue cycle reporting."""
    return {
        "month": month,
        "tenant_id": tenant_id,
        "summary": {
            "patients_with_billable_ccm": 0,
            "total_estimated_revenue": 0.0,
            "codes_breakdown": {
                "99490": {"count": 0, "revenue": 0.0},
                "99491": {"count": 0, "revenue": 0.0},
                "99487": {"count": 0, "revenue": 0.0},
                "99495": {"count": 0, "revenue": 0.0},
                "99496": {"count": 0, "revenue": 0.0},
            },
            "patients_not_reached": 0,
            "patients_missing_consent": 0,
            "patients_missing_care_plan": 0,
        },
        "note": "Wire to database for production billing data.",
    }


# ─── Meeting Minutes ───────────────────────────────────────────────────────────

@cm_router.post("/meetings/generate-minutes",
    summary="Generate care team meeting minutes from transcript")
async def generate_meeting_minutes(
    transcript: str = Form(...),
    meeting_type: str = Form("CARE_TEAM"),
    attendees: str = Form(""),
    meeting_date: str = Form(""),
    patient_context: str = Form("{}"),
):
    """
    Generate care team meeting minutes from recording transcript.
    Extracts: decisions, action items, assigned owners, deadlines.
    """
    import json as json_module
    try:
        pt_ctx = json_module.loads(patient_context)
    except Exception:
        pt_ctx = {}

    from .services.ccm_engine import _call_claude, HAIKU_MODEL
    from .services.phi_deidentify import build_phi_map

    system = """You are a clinical documentation specialist generating care team meeting minutes.
Structure and document all decisions and action items clearly."""

    user = f"""Generate meeting minutes from this transcript:

MEETING TYPE: {meeting_type}
DATE: {meeting_date or date.today().isoformat()}
ATTENDEES: {attendees or 'Care team members'}
PATIENT: {pt_ctx.get('first_name', '')} {pt_ctx.get('last_name', '')}

TRANSCRIPT:
{transcript}

Generate structured minutes:
1. MEETING OVERVIEW (date, type, attendees)
2. AGENDA ITEMS DISCUSSED
3. CLINICAL UPDATES
4. DECISIONS MADE
5. ACTION ITEMS
   (each with: owner, deadline, priority)
6. NEXT MEETING DATE/AGENDA
7. DOCUMENTATION ATTESTATION"""

    # DP-02: this endpoint bypasses the engine wrappers and calls _call_claude
    # directly, so it must supply its own phi_map.
    minutes_body = await _call_claude(
        system, user, model=HAIKU_MODEL, max_tokens=1500,
        phi_map=build_phi_map(pt_ctx),
    )

    return {
        "minutes_body": minutes_body,
        "meeting_type": meeting_type,
        "meeting_date": meeting_date or date.today().isoformat(),
        "attendees": attendees,
        "minutes_id": str(uuid.uuid4()),
        "generated_at": datetime.utcnow().isoformat(),
        "ai_disclosure": "AI-generated meeting minutes. Requires review and approval by meeting chair.",
    }


# ─── Module Info ───────────────────────────────────────────────────────────────

@cm_router.get("/info",
    summary="Case Management module information")
async def module_info():
    """Module capabilities and compliance coverage."""
    return {
        "module": "DocuAction Case Management",
        "version": "1.0.0",
        "modes": ["Standard AI", "Agentic"],
        "capabilities": {
            "CCM": "Chronic Care Management — CPT 99490/99439/99491/99487/99489",
            "TCM": "Transitional Care Management — CPT 99495/99496",
            "PCM": "Principal Care Management — CPT 99424-99427",
            "Clinical_CM": "Hospital acute care management — Joint Commission",
            "Discharge_Planning": "CMS CoP §482.43 compliant",
            "Government_CM": "CMS appeals, VA benefits, FWA investigations",
            "SDOH": "AHC HRSN screening — community resource referral",
            "Education": "Multilingual, 6th grade reading level, Section 1557",
            "Billing": "Automated CPT code determination with reimbursement calculation",
        },
        "compliance": {
            "billing_codes": ["99490", "99439", "99491", "99437", "99487", "99489", "99495", "99496", "99424-99427"],
            "regulations": [
                "CMS Chronic Care Management Program",
                "CMS Transitional Care Management",
                "CMS Principal Care Management",
                "Joint Commission RC.02.01.25",
                "CMS CoP §482.24 and §482.43",
                "HIPAA",
                "42 CFR Part 2 (SUD)",
                "Section 1557 (Translation)",
                "Texas SB 1188 (Human Review)",
            ],
            "ai_disclosure": "Every output includes AI disclosure per state AI transparency laws",
            "human_in_loop": "Clinician approval required before signing or billing",
        },
        "ai_pipeline": {
            "extraction": "Claude Haiku 4.5 — fast clinical fact extraction",
            "generation": "Claude Sonnet 4.6 — standard note/plan generation",
            "complex_reasoning": "Claude Opus 4.6 — complex multi-condition patients",
            "voice_transcription": "OpenAI Whisper — call recording transcription",
        },
        "agt_credentials": {
            "company": "Alliance Global Tech, Inc. (AGT)",
            "uei": "MP2FLV1MAW93",
            "certifications": "SBA 8(a) · GSA MAS · CMMI Level 3 · ISO 27001 · DoD FCL",
        },
    }
