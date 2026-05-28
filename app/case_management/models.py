"""
DocuAction AI — Case Management Module
Database Models (SQLAlchemy ORM)
Covers: CCM, TCM, PCM, Clinical CM, Government CM
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean,
    DateTime, Text, ForeignKey, Enum, JSON
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum

try:
    from app.core.database import Base
except ImportError:
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()


# ─── Enums ────────────────────────────────────────────────────────────────────

class CMModuleType(str, enum.Enum):
    CLINICAL_CM     = "CLINICAL_CM"       # Hospital acute care
    CCM             = "CCM"               # Chronic care management
    TCM             = "TCM"               # Transitional care management
    PCM             = "PCM"               # Principal care management
    BEHAVIORAL_CM   = "BEHAVIORAL_CM"     # Behavioral health
    GOVERNMENT_CM   = "GOVERNMENT_CM"     # Federal/state case management
    DISCHARGE_CM    = "DISCHARGE_CM"      # Discharge planning

class NoteType(str, enum.Enum):
    CCM_PROGRESS        = "CCM_PROGRESS"
    TCM_FOLLOWUP        = "TCM_FOLLOWUP"
    PCM_PROGRESS        = "PCM_PROGRESS"
    CARE_PLAN_UPDATE    = "CARE_PLAN_UPDATE"
    DISCHARGE_SUMMARY   = "DISCHARGE_SUMMARY"
    EDUCATION_NOTE      = "EDUCATION_NOTE"
    REFERRAL_NOTE       = "REFERRAL_NOTE"
    MEETING_MINUTES     = "MEETING_MINUTES"
    SDOH_ASSESSMENT     = "SDOH_ASSESSMENT"
    GOVERNMENT_CASE     = "GOVERNMENT_CASE"

class BillingCode(str, enum.Enum):
    CPT_99490 = "99490"   # CCM non-complex 20 min, clinical staff
    CPT_99439 = "99439"   # CCM add-on 20 min, clinical staff
    CPT_99491 = "99491"   # CCM 30 min, physician/NPP
    CPT_99437 = "99437"   # CCM add-on 30 min, physician/NPP
    CPT_99487 = "99487"   # Complex CCM 60 min
    CPT_99489 = "99489"   # Complex CCM add-on 30 min
    CPT_99495 = "99495"   # TCM moderate complexity 14-day
    CPT_99496 = "99496"   # TCM high complexity 7-day
    CPT_99424 = "99424"   # PCM 30 min, physician/NPP
    CPT_99425 = "99425"   # PCM add-on 30 min, physician/NPP
    CPT_99426 = "99426"   # PCM 30 min, clinical staff
    CPT_99427 = "99427"   # PCM add-on 30 min, clinical staff

class CaseStatus(str, enum.Enum):
    ACTIVE          = "ACTIVE"
    INACTIVE        = "INACTIVE"
    ENROLLED        = "ENROLLED"
    PENDING_CONSENT = "PENDING_CONSENT"
    DISCHARGED      = "DISCHARGED"
    CLOSED          = "CLOSED"

class NoteStatus(str, enum.Enum):
    DRAFT       = "DRAFT"
    AI_GENERATED = "AI_GENERATED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED    = "APPROVED"
    SIGNED      = "SIGNED"
    BILLED      = "BILLED"

class InputMode(str, enum.Enum):
    VOICE       = "VOICE"
    TEXT        = "TEXT"
    STRUCTURED  = "STRUCTURED"
    EHR_IMPORT  = "EHR_IMPORT"


# ─── Models ───────────────────────────────────────────────────────────────────

class CMPatient(Base):
    """Patient enrolled in case management program."""
    __tablename__ = "cm_patients"

    patient_id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id           = Column(String(255), nullable=False, index=True)
    mrn                 = Column(String(100))
    first_name          = Column(String(255))
    last_name           = Column(String(255))
    date_of_birth       = Column(String(20))
    gender              = Column(String(20))
    phone               = Column(String(20))
    address             = Column(JSONB)
    insurance_primary   = Column(JSONB)
    insurance_secondary = Column(JSONB)
    pcp_name            = Column(String(255))
    pcp_npi             = Column(String(10))
    diagnoses_icd10     = Column(JSONB)   # Array of ICD-10 codes
    hcc_codes           = Column(JSONB)   # Active HCC codes
    risk_score          = Column(Float)
    risk_tier           = Column(String(20))  # LOW, MODERATE, HIGH, COMPLEX
    sdoh_flags          = Column(JSONB)   # Social determinants
    cm_module_type      = Column(Enum(CMModuleType), default=CMModuleType.CCM)
    case_status         = Column(Enum(CaseStatus), default=CaseStatus.PENDING_CONSENT)
    consent_date        = Column(DateTime)
    enrollment_date     = Column(DateTime)
    assigned_case_manager_id = Column(String(255))
    care_plan_id        = Column(UUID(as_uuid=True))
    monthly_contact_required = Column(Boolean, default=True)
    last_contact_date   = Column(DateTime)
    next_contact_due    = Column(DateTime)
    total_ccm_minutes_ytd = Column(Integer, default=0)
    notes               = Column(Text)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    cm_notes = relationship("CMNote", back_populates="patient")
    care_plans = relationship("CMCarePlan", back_populates="patient")


class CMNote(Base):
    """Case management progress note — CCM, TCM, PCM, or clinical CM."""
    __tablename__ = "cm_notes"

    note_id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id           = Column(String(255), nullable=False, index=True)
    patient_id          = Column(UUID(as_uuid=True), ForeignKey("cm_patients.patient_id"))
    case_manager_id     = Column(String(255), nullable=False)
    note_type           = Column(Enum(NoteType), nullable=False)
    note_status         = Column(Enum(NoteStatus), default=NoteStatus.DRAFT)
    input_mode          = Column(Enum(InputMode))

    # Time documentation (critical for billing)
    service_date        = Column(DateTime, nullable=False)
    time_start          = Column(String(10))
    time_end            = Column(String(10))
    total_minutes       = Column(Integer)
    billable_minutes    = Column(Integer)
    billing_code        = Column(Enum(BillingCode))
    billing_rationale   = Column(Text)
    cumulative_minutes_this_month = Column(Integer)

    # Content
    voice_transcript    = Column(Text)       # Raw Whisper transcript
    clinical_summary    = Column(Text)       # Extracted clinical facts
    note_body           = Column(Text)       # Full AI-generated note
    care_plan_updates   = Column(JSONB)
    action_items        = Column(JSONB)
    risk_flags          = Column(JSONB)

    # CMS documentation requirements met
    patient_consent_verified     = Column(Boolean, default=False)
    care_plan_reviewed           = Column(Boolean, default=False)
    coordination_activities      = Column(JSONB)
    medications_reconciled       = Column(Boolean, default=False)
    followup_scheduled           = Column(Boolean, default=False)
    physician_supervision_noted  = Column(Boolean, default=False)

    # 42 CFR Part 2 SUD flag
    contains_sud_content        = Column(Boolean, default=False)
    sud_content_redacted        = Column(Boolean, default=False)

    # AI metadata
    ai_model_used       = Column(String(100))
    ai_confidence       = Column(Float)
    ai_generation_time  = Column(Float)
    source_citations    = Column(JSONB)

    # Review tracking
    reviewed_by         = Column(String(255))
    reviewed_at         = Column(DateTime)
    signed_by           = Column(String(255))
    signed_at           = Column(DateTime)
    override_reason     = Column(Text)

    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = relationship("CMPatient", back_populates="cm_notes")


class CMCarePlan(Base):
    """Comprehensive care plan with SMART goals."""
    __tablename__ = "cm_care_plans"

    plan_id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id           = Column(String(255), nullable=False, index=True)
    patient_id          = Column(UUID(as_uuid=True), ForeignKey("cm_patients.patient_id"))
    created_by          = Column(String(255))
    plan_version        = Column(Integer, default=1)
    effective_date      = Column(DateTime)
    review_date         = Column(DateTime)
    status              = Column(String(50), default="ACTIVE")

    # Core care plan content
    primary_diagnosis   = Column(String(500))
    diagnoses           = Column(JSONB)      # All active diagnoses
    medications         = Column(JSONB)      # Current medication list
    allergies           = Column(JSONB)
    functional_status   = Column(Text)
    cognitive_status    = Column(Text)
    caregiver_info      = Column(JSONB)
    advance_directive   = Column(String(100))

    # Goals (SMART)
    goals               = Column(JSONB)      # Array of goal objects
    interventions       = Column(JSONB)      # Care interventions
    barriers            = Column(JSONB)      # Identified barriers
    strengths           = Column(JSONB)      # Patient strengths

    # Care team
    care_team           = Column(JSONB)      # Array of care team members
    specialist_referrals = Column(JSONB)

    # SDOH
    sdoh_assessment     = Column(JSONB)
    community_resources = Column(JSONB)

    # Education
    education_topics    = Column(JSONB)
    education_materials_generated = Column(JSONB)

    # AI generation
    ai_generated        = Column(Boolean, default=False)
    ai_model_used       = Column(String(100))
    source_documents    = Column(JSONB)

    plan_body           = Column(Text)       # Full formatted plan text
    patient_signature   = Column(Boolean, default=False)
    patient_signature_date = Column(DateTime)

    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = relationship("CMPatient", back_populates="care_plans")


class CMDischargeRecord(Base):
    """Discharge planning record — Joint Commission & CMS CoP compliant."""
    __tablename__ = "cm_discharge_records"

    discharge_id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id           = Column(String(255), nullable=False, index=True)
    patient_id          = Column(UUID(as_uuid=True), ForeignKey("cm_patients.patient_id"))
    admission_date      = Column(DateTime)
    discharge_date      = Column(DateTime)
    attending_physician = Column(String(255))
    created_by          = Column(String(255))

    # Required Joint Commission elements
    primary_diagnosis   = Column(String(500))
    secondary_diagnoses = Column(JSONB)
    procedures_performed = Column(JSONB)
    hospital_course     = Column(Text)
    complications       = Column(Text)
    condition_at_discharge = Column(String(100))

    # Discharge disposition
    discharge_disposition = Column(String(200))
    discharge_facility  = Column(String(500))
    follow_up_provider  = Column(String(500))
    follow_up_date      = Column(String(50))
    follow_up_instructions = Column(Text)

    # Medications
    medications_at_discharge = Column(JSONB)
    medication_changes   = Column(JSONB)
    medication_reconciliation_completed = Column(Boolean, default=False)

    # Patient education
    education_provided  = Column(JSONB)
    patient_verbalized_understanding = Column(Boolean)
    caregiver_educated  = Column(Boolean)

    # Warning signs
    warning_signs       = Column(JSONB)
    when_to_call_doctor = Column(Text)
    er_criteria         = Column(Text)

    # Patient instructions (6th grade reading level)
    patient_instructions = Column(Text)
    instructions_language = Column(String(50), default="English")

    # AI generation
    ai_generated        = Column(Boolean, default=False)
    ai_model_used       = Column(String(100))
    source_notes        = Column(JSONB)

    discharge_summary_body = Column(Text)
    status              = Column(String(50), default="DRAFT")
    signed_by           = Column(String(255))
    signed_at           = Column(DateTime)

    # CMS compliance flags
    completed_within_24h = Column(Boolean)
    jc_rc020125_met     = Column(Boolean)

    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CMGovernmentCase(Base):
    """Government case management — CMS, VA, State Medicaid, investigations."""
    __tablename__ = "cm_government_cases"

    case_id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id           = Column(String(255), nullable=False, index=True)
    case_type           = Column(String(100))  # MEDICARE_APPEAL, VA_BENEFIT, MEDICAID_ELIGIBILITY, FWA_INVESTIGATION
    agency              = Column(String(200))
    case_reference      = Column(String(200), index=True)
    assigned_analyst    = Column(String(255))
    status              = Column(String(100), default="OPEN")
    priority            = Column(String(50), default="STANDARD")

    case_summary        = Column(Text)
    findings            = Column(JSONB)
    evidence_documents  = Column(JSONB)
    recommendations     = Column(JSONB)
    regulatory_citations = Column(JSONB)

    # Deadlines
    received_date       = Column(DateTime)
    response_deadline   = Column(DateTime)
    completed_date      = Column(DateTime)

    # Investigation fields (FWA, OIG)
    investigation_type  = Column(String(100))
    subjects            = Column(JSONB)
    chain_of_custody    = Column(JSONB)

    case_body           = Column(Text)
    ai_generated        = Column(Boolean, default=False)
    ai_model_used       = Column(String(100))

    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CMBillingSummary(Base):
    """Monthly CCM/TCM billing summary per patient."""
    __tablename__ = "cm_billing_summaries"

    summary_id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id           = Column(String(255), nullable=False, index=True)
    patient_id          = Column(UUID(as_uuid=True), ForeignKey("cm_patients.patient_id"))
    billing_month       = Column(String(7))   # YYYY-MM
    case_manager_id     = Column(String(255))
    billing_provider_npi = Column(String(10))

    total_minutes       = Column(Integer, default=0)
    billable_minutes    = Column(Integer, default=0)
    primary_cpt_code    = Column(String(10))
    addon_cpt_codes     = Column(JSONB)
    estimated_reimbursement = Column(Float)
    notes_count         = Column(Integer, default=0)

    consent_on_file     = Column(Boolean, default=False)
    care_plan_active    = Column(Boolean, default=False)
    documentation_complete = Column(Boolean, default=False)
    ready_to_bill       = Column(Boolean, default=False)
    billed_date         = Column(DateTime)
    claim_number        = Column(String(100))

    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
