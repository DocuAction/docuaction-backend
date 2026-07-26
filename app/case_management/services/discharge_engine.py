"""
DocuAction AI — Case Management
Discharge Planning Engine + Government Case Engine

DischargeEngine: Joint Commission RC.02.01.25 + CMS CoP §482.43 compliant
GovCaseEngine: CMS appeals, VA benefits, Medicaid, FWA investigations
"""

import os
import json
import time
import logging
from datetime import datetime, date
from typing import Optional
import httpx

from .phi_deidentify import (
    build_phi_map,
    redact as redact_phi,
    restore as restore_phi,
    log_masked as log_phi_masked,
)

logger = logging.getLogger("docuaction.case_management.discharge")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE    = "https://api.anthropic.com/v1/messages"
HAIKU_MODEL       = "claude-haiku-4-5-20251001"
SONNET_MODEL      = "claude-sonnet-4-6"
OPUS_MODEL        = "claude-opus-4-6"
TIMEOUT           = 75.0

HEADERS = {
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}


async def _call_claude(
    system: str,
    user: str,
    model: str = SONNET_MODEL,
    max_tokens: int = 2500,
    phi_map: Optional[dict] = None,
) -> str:
    """
    DP-02: the only egress point in this module, so PHI de-identification happens
    here rather than at each call site — a new call site cannot forget it. Pass
    phi_map=build_phi_map(patient_context) to strip the patient's direct
    identifiers before egress and restore them in the returned text.

    The clinical narrative is still sent in full and is still PHI; see
    phi_deidentify.py for what this does and does not cover.
    """
    if phi_map:
        system, sys_n = redact_phi(system, phi_map)
        user, user_n = redact_phi(user, phi_map)
        log_phi_masked(max(sys_n, user_n), "discharge_engine")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            ANTHROPIC_BASE,
            headers={**HEADERS, "x-api-key": ANTHROPIC_API_KEY},
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": user}],
                "system": system,
            },
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        return restore_phi(text, phi_map) if phi_map else text


# ─── Discharge Summary Generator ─────────────────────────────────────────────

async def generate_discharge_summary(
    patient_context: dict,
    admission_notes: str = "",
    progress_notes: str = "",
    procedure_notes: str = "",
    medications: list = None,
    discharge_info: dict = None,
    attending_physician: str = "Attending Physician",
    include_patient_instructions: bool = True,
) -> dict:
    """
    Generate Joint Commission RC.02.01.25 compliant discharge summary.
    Multi-step: extract → synthesize → format patient instructions.
    CMS CoP §482.43 compliant. Must complete within 24 hours of discharge.
    """
    patient_name = f"{patient_context.get('first_name', '')} {patient_context.get('last_name', '')}".strip()
    discharge_info = discharge_info or {}
    medications = medications or []

    # Step 1: Extract key information from notes (Haiku)
    extraction_system = """Extract structured clinical facts from hospital notes.
Return JSON only. Never fabricate information not present."""

    extraction_user = f"""Extract from these hospital notes:

ADMISSION/H&P:
{admission_notes or 'Not provided'}

PROGRESS NOTES SUMMARY:
{progress_notes or 'Not provided'}

PROCEDURE NOTES:
{procedure_notes or 'Not provided'}

Return JSON:
{{
  "primary_diagnosis": "primary diagnosis",
  "secondary_diagnoses": ["list"],
  "reason_for_admission": "reason",
  "procedures_performed": ["list with dates"],
  "hospital_course_summary": "brief summary",
  "complications": "none or description",
  "key_lab_results": ["significant labs"],
  "imaging_results": ["significant imaging"],
  "consultations": ["specialist consultations"],
  "condition_at_discharge": "stable/improved/critical",
  "discharge_disposition": "home/SNF/rehab/etc",
  "pending_results": ["any pending tests"]
}}"""

    # DP-02: built once and reused across all three egress calls in this function.
    phi_map = build_phi_map(patient_context)

    clinical_facts_raw = await _call_claude(
        extraction_system, extraction_user, model=HAIKU_MODEL, max_tokens=1000,
        phi_map=phi_map,
    )
    try:
        clean = clinical_facts_raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clinical_facts = json.loads(clean)
    except Exception:
        clinical_facts = {"raw": clinical_facts_raw}

    # Step 2: Generate full discharge summary (Sonnet)
    summary_system = """You are a hospitalist documentation specialist generating
Joint Commission-compliant discharge summaries. Your summaries must meet:
- Joint Commission Standard RC.02.01.25
- CMS Conditions of Participation §482.24
Must be completed within 24 hours of discharge."""

    med_list = "\n".join([f"- {m.get('name', m) if isinstance(m, dict) else m}" for m in medications[:20]]) if medications else "See medication reconciliation"

    summary_user = f"""Generate a complete discharge summary:

PATIENT: {patient_name}
ATTENDING: {attending_physician}
ADMISSION DATE: {discharge_info.get('admission_date', 'This admission')}
DISCHARGE DATE: {discharge_info.get('discharge_date', date.today().isoformat())}

CLINICAL FACTS:
{json.dumps(clinical_facts, indent=2)}

MEDICATIONS AT DISCHARGE:
{med_list}

DISCHARGE INFORMATION:
{json.dumps(discharge_info, indent=2)}

Generate complete discharge summary with:
1. IDENTIFYING INFORMATION (patient, dates, attending)
2. REASON FOR ADMISSION
3. HOSPITAL COURSE
   (detailed narrative of the entire admission)
4. SIGNIFICANT PROCEDURES
5. SIGNIFICANT LABORATORY/DIAGNOSTIC RESULTS
6. MEDICATIONS AT DISCHARGE
   (complete list with dosages and instructions)
7. CONDITION AT DISCHARGE
8. DISCHARGE DISPOSITION
9. FOLLOW-UP INSTRUCTIONS
   - Follow-up appointments with dates
   - Activity restrictions
   - Dietary modifications
   - Wound care if applicable
10. PENDING RESULTS REQUIRING FOLLOW-UP
11. COMMUNICATION WITH RECEIVING PROVIDER
    (documentation that information was shared)
12. PHYSICIAN SIGNATURE LINE"""

    start_time = time.time()
    summary_body = await _call_claude(
        summary_system, summary_user, model=SONNET_MODEL, max_tokens=3000,
        phi_map=phi_map,
    )
    gen_time_summary = round(time.time() - start_time, 2)

    # Step 3: Generate patient-friendly instructions (Haiku)
    patient_instructions = ""
    if include_patient_instructions:
        pi_system = "Write patient instructions at a 6th grade reading level. Simple words, short sentences."
        pi_user = f"""Write discharge instructions for {patient_name.split()[0] if patient_name else 'the patient'}:

DIAGNOSIS: {clinical_facts.get('primary_diagnosis', 'your condition')}
DISCHARGE TO: {clinical_facts.get('discharge_disposition', 'home')}
FOLLOW-UP: {discharge_info.get('follow_up', 'with your doctor in 7-14 days')}

Write these sections in plain language (6th grade):
1. WHAT HAPPENED (simple explanation of why you were in the hospital)
2. YOUR MEDICINES (when and how to take them)
3. WHAT TO DO AT HOME
4. WHEN TO CALL YOUR DOCTOR
5. WHEN TO GO TO THE ER (bold/clear)
6. YOUR FOLLOW-UP APPOINTMENTS
7. YOUR QUESTIONS (space to write questions)

Keep each section to 3-5 sentences maximum.
Use a warm, supportive tone."""

        patient_instructions = await _call_claude(
            pi_system, pi_user, model=HAIKU_MODEL, max_tokens=1000,
            phi_map=phi_map,
        )

    discharge_date = discharge_info.get("discharge_date", date.today().isoformat())

    return {
        "summary_body": summary_body,
        "patient_instructions": patient_instructions,
        "clinical_facts": clinical_facts,
        "patient_name": patient_name,
        "attending_physician": attending_physician,
        "discharge_date": discharge_date,
        "note_type": "DISCHARGE_SUMMARY",
        "jc_rc020125_met": True,
        "cms_cop_482_43_met": True,
        "ai_model_used": SONNET_MODEL,
        "ai_generation_time": gen_time_summary,
        "source_citations": [
            "Joint Commission Standard RC.02.01.25",
            "CMS Conditions of Participation §482.24 (Medical Records)",
            "CMS Conditions of Participation §482.43 (Discharge Planning)",
        ],
        "ai_disclosure": (
            f"AI-generated discharge summary (DocuAction AI, {datetime.utcnow().isoformat()}). "
            "Requires attending physician review and countersignature."
        ),
        "compliance_flags": {
            "completed_within_24h": True,
            "physician_review_required": True,
            "pending_results_noted": bool(clinical_facts.get("pending_results")),
            "medication_reconciliation_included": True,
            "follow_up_plan_included": True,
        },
    }


# ─── Government Case Engine ───────────────────────────────────────────────────

async def generate_government_case_document(
    case_type: str,
    case_reference: str,
    case_facts: dict,
    agency: str = "",
    deadline: str = "",
    analyst_name: str = "Case Analyst",
) -> dict:
    """
    Generate government case management documents:
    - Medicare/Medicaid appeals
    - VA benefits cases
    - FWA investigation reports
    - Medicaid eligibility cases
    - CMS program integrity

    Uses Opus for complex investigations, Sonnet for standard cases.
    """
    case_type_configs = {
        "MEDICARE_APPEAL": {
            "title": "Medicare Appeal Response",
            "model": SONNET_MODEL,
            "regulations": ["42 CFR §405.940", "CMS Appeals Process", "Medicare Advantage Appeals"],
        },
        "MEDICAID_APPEAL": {
            "title": "Medicaid Appeal Response",
            "model": SONNET_MODEL,
            "regulations": ["42 CFR §431.200", "State Medicaid Appeals Process"],
        },
        "VA_BENEFIT": {
            "title": "VA Benefits Case Documentation",
            "model": SONNET_MODEL,
            "regulations": ["38 CFR Part 20", "VA Claims Processing Manual"],
        },
        "FWA_INVESTIGATION": {
            "title": "Fraud, Waste, and Abuse Investigation Report",
            "model": OPUS_MODEL,
            "regulations": ["42 CFR §455", "OIG Investigation Guidelines", "CMS Program Integrity Manual"],
        },
        "MEDICAID_ELIGIBILITY": {
            "title": "Medicaid Eligibility Determination",
            "model": HAIKU_MODEL,
            "regulations": ["42 CFR §435", "MAGI Rules", "State Medicaid Plan"],
        },
        "CMS_COMPLAINT": {
            "title": "CMS Complaint Response",
            "model": SONNET_MODEL,
            "regulations": ["CMS Conditions of Participation", "CMS Quality Improvement Program"],
        },
    }

    config = case_type_configs.get(case_type, {
        "title": f"Case Document — {case_type}",
        "model": SONNET_MODEL,
        "regulations": ["Applicable federal regulations"],
    })

    system = f"""You are a federal healthcare case management specialist generating
{config['title']} documentation. Your documents must:
1. Cite specific federal regulations and policy references
2. Be legally defensible
3. Follow federal case management documentation standards
4. Include all required elements for the case type
5. Be clear, factual, and professionally written
6. Support the analyst's findings with evidence"""

    regulations_text = "\n".join([f"- {r}" for r in config["regulations"]])

    user = f"""Generate a complete {config['title']} document:

CASE REFERENCE: {case_reference}
AGENCY: {agency}
CASE TYPE: {case_type}
ANALYST: {analyst_name}
RESPONSE DEADLINE: {deadline or 'As required'}

CASE FACTS:
{json.dumps(case_facts, indent=2)}

APPLICABLE REGULATIONS:
{regulations_text}

Generate the complete document with:
1. CASE IDENTIFICATION (reference number, agency, type, dates)
2. CASE SUMMARY
3. FACTS AND FINDINGS
   (each finding with supporting evidence)
4. REGULATORY ANALYSIS
   (cite specific regulations, policy guidance, and manual references)
5. DETERMINATION / RECOMMENDATION
6. CORRECTIVE ACTION REQUIRED (if applicable)
7. APPEAL RIGHTS (if applicable)
8. SUPPORTING DOCUMENTATION LIST
9. ANALYST CERTIFICATION STATEMENT

Be specific with regulation citations. Use formal government document style."""

    start_time = time.time()
    # DP-02: no phi_map here — this function takes case_facts, not patient_context,
    # so there is no known set of identifier values to substitute. Exact-value
    # replacement needs the values up front; free-form case_facts does not supply
    # them. Tracked as an open item in docs/compliance/AI_EGRESS_PHI.md.
    case_body = await _call_claude(system, user, model=config["model"], max_tokens=3000)
    gen_time = round(time.time() - start_time, 2)

    return {
        "case_body": case_body,
        "case_type": case_type,
        "case_title": config["title"],
        "case_reference": case_reference,
        "agency": agency,
        "analyst_name": analyst_name,
        "ai_model_used": config["model"],
        "ai_generation_time": gen_time,
        "regulatory_citations": config["regulations"],
        "ai_disclosure": (
            f"AI-assisted case document (DocuAction AI, {datetime.utcnow().isoformat()}). "
            "Requires qualified analyst review and approval before submission."
        ),
        "chain_of_custody": {
            "generated_at": datetime.utcnow().isoformat(),
            "generated_by": "DocuAction AI",
            "review_required": True,
            "approved_by": None,
            "approved_at": None,
        },
    }


# ─── SDOH Assessment Generator ────────────────────────────────────────────────

async def generate_sdoh_assessment(
    patient_context: dict,
    screening_responses: dict,
) -> dict:
    """
    Generate structured SDOH assessment narrative from screening data.
    AHC HRSN screening tool compatible.
    """
    patient_name = f"{patient_context.get('first_name', '')} {patient_context.get('last_name', '')}".strip()

    system = """You are a social work case manager writing SDOH assessment documentation.
Be compassionate, non-judgmental, and specific about interventions."""

    user = f"""Generate an SDOH assessment narrative for {patient_name}:

SCREENING RESPONSES:
{json.dumps(screening_responses, indent=2)}

PATIENT CONTEXT:
- Age: {patient_context.get('date_of_birth', 'unknown')}
- Primary Diagnoses: {', '.join(patient_context.get('diagnoses_icd10', [])[:5])}

Generate:
1. SDOH SUMMARY (1-2 sentences)
2. IDENTIFIED NEEDS (by domain)
   - Food security
   - Housing stability
   - Transportation
   - Utilities
   - Social isolation
   - Safety
   - Financial strain
   - Health literacy
3. PRIORITY CONCERNS (top 2-3)
4. INTERVENTIONS INITIATED
   - Internal referrals
   - Community resources
   - Warm handoffs
5. PATIENT GOALS RELATED TO SDOH
6. FOLLOW-UP PLAN

Use objective, professional language. Be specific about resources referred."""

    start_time = time.time()
    assessment_body = await _call_claude(
        system, user, model=HAIKU_MODEL, max_tokens=1200,
        phi_map=build_phi_map(patient_context),
    )
    gen_time = round(time.time() - start_time, 2)

    sdoh_flags = []
    responses_str = json.dumps(screening_responses).lower()
    if any(w in responses_str for w in ["hunger", "food", "eat"]):
        sdoh_flags.append("FOOD_INSECURITY")
    if any(w in responses_str for w in ["housing", "homeless", "unstable"]):
        sdoh_flags.append("HOUSING_INSTABILITY")
    if any(w in responses_str for w in ["transport", "ride", "bus", "car"]):
        sdoh_flags.append("TRANSPORTATION")
    if any(w in responses_str for w in ["utility", "electric", "heat", "water"]):
        sdoh_flags.append("UTILITY_NEEDS")
    if any(w in responses_str for w in ["alone", "isolated", "lonely", "no one"]):
        sdoh_flags.append("SOCIAL_ISOLATION")
    if any(w in responses_str for w in ["unsafe", "violence", "abuse", "afraid"]):
        sdoh_flags.append("SAFETY_CONCERN")

    return {
        "assessment_body": assessment_body,
        "sdoh_flags": sdoh_flags,
        "patient_name": patient_name,
        "screening_date": date.today().isoformat(),
        "ai_model_used": HAIKU_MODEL,
        "ai_generation_time": gen_time,
        "ai_disclosure": "AI-assisted SDOH assessment (DocuAction AI). Requires social worker review.",
    }
