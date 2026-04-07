"""
Enterprise Intelligence Engine
Vertical Modes: Government | Healthcare (CDI + Coding) | Legal | Enterprise
Outputs: Director's Brief, Decision Ledger, Action Matrix, KPI Signals, Risk Score, Encounter Audit Packet
"""
import json
import logging
from datetime import datetime

logger = logging.getLogger("docuaction.intelligence_engine")

# ═══════════════════════════════════════════════════════════
# PII/PHI MASKING - 12+ CATEGORIES (PRE-PROCESSING)
# ═══════════════════════════════════════════════════════════

PII_CATEGORIES = [
    "SSN", "Phone", "Email", "Credit Card", "Date of Birth",
    "Street Address", "Passport Number", "Driver License",
    "Bank Account", "IP Address", "Medical Record Number",
    "Health Plan Beneficiary Number", "Patient Name",
    "Biometric Identifier", "Device Identifier",
]

PHI_MASKING_PROMPT = """Before analyzing this content, identify and mask ALL of the following PHI/PII categories:
1. SSN (XXX-XX-XXXX) → [SSN-REDACTED]
2. Phone numbers → [PHONE-REDACTED]
3. Email addresses → [EMAIL-REDACTED]
4. Credit card numbers → [CC-REDACTED]
5. Dates of birth → [DOB-REDACTED]
6. Street addresses → [ADDR-REDACTED]
7. Passport numbers → [PASSPORT-REDACTED]
8. Driver license numbers → [DL-REDACTED]
9. Bank account numbers → [BANK-REDACTED]
10. IP addresses → [IP-REDACTED]
11. Medical record numbers → [MRN-REDACTED]
12. Health plan beneficiary numbers → [HPBN-REDACTED]
13. Patient names → [PATIENT-REDACTED]
14. Biometric identifiers → [BIO-REDACTED]
15. Device identifiers → [DEVICE-REDACTED]

CRITICAL: Redaction must happen BEFORE any analysis. Include a masking verification log in your output confirming categories checked."""

# ═══════════════════════════════════════════════════════════
# DOMAIN-SPECIFIC INTELLIGENCE PROMPTS
# ═══════════════════════════════════════════════════════════

ENTERPRISE_PROMPT = """You are an Enterprise Intelligence Engine analyzing business content for C-suite decision-makers.

OUTPUTS REQUIRED (return as JSON):
{
  "directors_brief": {
    "title": "Brief title",
    "strategic_alignment": "How this aligns to organizational goals",
    "executive_synthesis": "200-word synthesis for director/VP level",
    "blockers": ["List of blockers identified"],
    "cost_of_inaction": "What happens if no action is taken — quantify delay impact",
    "time_saved_estimate": "Hours saved vs manual processing"
  },
  "decision_ledger": [
    {
      "id": "DEC-001",
      "decision": "What was decided",
      "decided_by": "Who decided",
      "timestamp": "When (from content context)",
      "rationale": "Why",
      "impact": "Business impact",
      "status": "confirmed|pending|deferred",
      "confidence": 0.95
    }
  ],
  "action_matrix": [
    {
      "id": "ACT-001",
      "task": "Task description",
      "owner": "Responsible person",
      "deadline": "Due date or timeframe",
      "priority": "critical|high|medium|low",
      "crm_target": "jira|salesforce|servicenow|email",
      "status": "pending",
      "dependencies": "Prerequisites"
    }
  ],
  "kpi_signals": [
    {
      "metric": "KPI name",
      "value": "Current or stated value",
      "trend": "up|down|stable|new",
      "context": "What was said about it",
      "dashboard_field": "Suggested dashboard mapping",
      "financial_impact": "Dollar impact if applicable"
    }
  ],
  "risk_indicator": {
    "overall_score": 45,
    "factors": [
      {
        "risk": "Description",
        "severity": "critical|high|medium|low",
        "probability": "high|medium|low",
        "financial_exposure": "Estimated dollar impact",
        "mitigation": "Recommended action",
        "source": "Where in content this was identified"
      }
    ],
    "compliance_flags": []
  },
  "document_pulse": [
    {
      "type": "contract|report|regulation|financial",
      "reference": "Document name mentioned",
      "priority": "critical|high|medium|low",
      "recommended_action": "What to do",
      "metadata_extracted": "Key data from document reference"
    }
  ],
  "roi_metrics": {
    "time_to_artifact_seconds": 45,
    "manual_equivalent_hours": 2.5,
    "risks_detected": 3,
    "actions_generated": 5,
    "decisions_captured": 2,
    "kpis_extracted": 4,
    "estimated_value": "Description of value delivered"
  },
  "masking_verification": {
    "categories_checked": 15,
    "entities_found": 0,
    "entities_redacted": 0,
    "verification_status": "complete"
  }
}"""

GOVERNMENT_PROMPT = """You are a Government Intelligence Engine producing audit-ready outputs for federal/state agencies.

COMPLIANCE REQUIREMENTS:
- All outputs must be suitable for permanent public record
- NIST 800-53 Rev. 5 control mapping required where applicable
- FOIA sensitivity flagging required
- Formal meeting minutes format required

OUTPUTS REQUIRED (return as JSON):
{
  "directors_brief": {
    "title": "Meeting title",
    "agency_context": "Agency/department and program context",
    "strategic_alignment": "Alignment to agency mission and strategic plan",
    "executive_synthesis": "200-word formal synthesis",
    "legislative_references": ["Any laws, regulations, or directives referenced"],
    "blockers": ["Blockers with agency impact"],
    "cost_of_inaction": "Impact to taxpayers, mission, or compliance if no action taken",
    "foia_sensitivity": "low|medium|high",
    "classification_level": "unclassified"
  },
  "formal_minutes": {
    "meeting_date": "Date",
    "attendees": ["List of attendees with titles"],
    "agenda_items": [
      {
        "item": "Agenda topic",
        "discussion_summary": "What was discussed",
        "motions": ["Any formal motions"],
        "votes": "Vote results if applicable",
        "action_required": "Next steps"
      }
    ],
    "next_meeting": "Scheduled follow-up"
  },
  "decision_ledger": [
    {
      "id": "DEC-001",
      "decision": "Formal decision language",
      "authority": "Decision-maker title and authority",
      "timestamp": "When",
      "rationale": "Documented rationale",
      "nist_controls": ["Related NIST 800-53 controls"],
      "regulatory_basis": "Regulatory authority for decision",
      "status": "confirmed|pending|deferred",
      "confidence": 0.95
    }
  ],
  "action_matrix": [
    {
      "id": "ACT-001",
      "task": "Action item",
      "owner": "Responsible official (title)",
      "deadline": "Due date",
      "priority": "critical|high|medium|low",
      "tracking_id": "Agency tracking reference",
      "status": "pending"
    }
  ],
  "compliance_traceability": {
    "nist_controls_referenced": [
      {
        "control_id": "AC-2",
        "control_name": "Account Management",
        "relevance": "How this control relates to discussion",
        "status": "implemented|planned|gap"
      }
    ],
    "regulatory_references": ["Laws and regulations cited"],
    "audit_findings_referenced": ["Any audit findings discussed"]
  },
  "risk_indicator": {
    "overall_score": 35,
    "factors": [
      {
        "risk": "Description",
        "severity": "critical|high|medium|low",
        "agency_impact": "Impact to agency operations",
        "mitigation": "Recommended action",
        "inspector_general_relevance": "IG relevance flag"
      }
    ]
  },
  "kpi_signals": [
    {
      "metric": "Performance metric",
      "value": "Reported value",
      "target": "Target value",
      "trend": "up|down|stable",
      "program": "Associated program"
    }
  ],
  "roi_metrics": {
    "time_to_artifact_seconds": 45,
    "manual_equivalent_hours": 3.0,
    "compliance_items_tracked": 5,
    "risks_detected": 2
  },
  "masking_verification": {
    "categories_checked": 15,
    "entities_found": 0,
    "entities_redacted": 0,
    "verification_status": "complete"
  }
}"""

HEALTHCARE_PROMPT = """You are a Clinical Intelligence Engine for healthcare organizations. You MUST produce HIPAA-compliant, CDI-aware, revenue-intelligent outputs.

CRITICAL REQUIREMENTS:
- ALL patient names MUST be masked as [PATIENT-REDACTED]
- ALL MRNs MUST be masked as [MRN-REDACTED]
- PHI masking verification is MANDATORY
- Clinical Documentation Integrity (CDI) analysis is REQUIRED
- ICD-10 / CPT code suggestions are REQUIRED
- Revenue impact analysis is REQUIRED

OUTPUTS REQUIRED (return as JSON):
{
  "directors_brief": {
    "title": "Clinical/operational meeting title",
    "facility_context": "Hospital/department context",
    "strategic_alignment": "Alignment to quality metrics, revenue goals, patient outcomes",
    "executive_synthesis": "200-word synthesis for healthcare admin",
    "patient_safety_flags": ["Any patient safety concerns identified"],
    "blockers": ["Operational blockers"],
    "cost_of_inaction": "Revenue, compliance, or patient impact if no action"
  },
  "clinical_documentation_integrity": {
    "cdi_score": 78,
    "findings": [
      {
        "id": "CDI-001",
        "type": "missing_diagnosis|incomplete_rationale|unclear_treatment|documentation_gap",
        "description": "What is missing or incomplete",
        "clinical_impact": "How this affects patient care documentation",
        "revenue_impact": "How this affects coding and reimbursement",
        "recommended_query": "Suggested physician query to resolve",
        "severity": "critical|high|medium|low"
      }
    ],
    "documentation_completeness": "percentage estimate",
    "query_opportunities": 3
  },
  "coding_intelligence": {
    "suggested_codes": [
      {
        "code": "ICD-10 or CPT code",
        "description": "Code description",
        "confidence": 0.85,
        "source": "What in the content supports this code",
        "revenue_estimate": "Estimated reimbursement impact",
        "currently_captured": true
      }
    ],
    "missed_opportunities": [
      {
        "code": "Missed code",
        "description": "Why this should be captured",
        "estimated_revenue_loss": "$X per encounter",
        "frequency": "How often this likely occurs"
      }
    ],
    "total_estimated_revenue_impact": "$XX,XXX",
    "coding_accuracy_score": 82
  },
  "clinical_risk_scoring": {
    "overall_score": 65,
    "dimensions": {
      "legal_exposure": {
        "score": 45,
        "factors": ["Contributing factors"]
      },
      "missing_documentation": {
        "score": 70,
        "factors": ["What is missing"]
      },
      "compliance_gaps": {
        "score": 55,
        "factors": ["Compliance issues"]
      },
      "patient_safety": {
        "score": 30,
        "factors": ["Safety concerns"]
      }
    },
    "recommended_actions": ["Priority actions to reduce risk"]
  },
  "encounter_audit_packet": {
    "clinical_summary": "Structured clinical summary of encounter/meeting",
    "decision_justification": [
      {
        "decision": "Clinical or operational decision",
        "medical_necessity": "Justification",
        "evidence_basis": "Supporting evidence",
        "alternatives_considered": "Other options discussed"
      }
    ],
    "patient_mention_tracking": {
      "total_mentions": 0,
      "all_masked": true,
      "categories_masked": ["PATIENT-REDACTED", "MRN-REDACTED"]
    },
    "compliance_validation": {
      "hipaa_compliant": true,
      "phi_masking_verified": true,
      "documentation_standard_met": true,
      "audit_ready": true
    },
    "correlation_id": "DA-XXXX-YYYY"
  },
  "decision_ledger": [
    {
      "id": "DEC-001",
      "decision": "Clinical or operational decision",
      "decided_by": "Clinician/administrator",
      "clinical_rationale": "Medical rationale",
      "timestamp": "When",
      "hipaa_reviewed": true,
      "confidence": 0.90
    }
  ],
  "action_matrix": [
    {
      "id": "ACT-001",
      "task": "Follow-up action",
      "owner": "Responsible party",
      "deadline": "Timeframe",
      "priority": "critical|high|medium|low",
      "patient_related": false,
      "phi_cleared": true
    }
  ],
  "kpi_signals": [
    {
      "metric": "Healthcare KPI",
      "value": "Reported value",
      "benchmark": "Industry benchmark",
      "trend": "up|down|stable",
      "revenue_impact": "Dollar impact"
    }
  ],
  "roi_metrics": {
    "time_to_artifact_seconds": 45,
    "manual_equivalent_hours": 4.0,
    "cdi_queries_generated": 3,
    "missed_codes_identified": 2,
    "estimated_revenue_recovery": "$XX,XXX",
    "risks_detected": 4,
    "compliance_items": 6
  },
  "masking_verification": {
    "categories_checked": 15,
    "phi_categories_checked": ["Patient Name", "MRN", "DOB", "SSN", "Address", "Phone", "Email", "Health Plan ID"],
    "entities_found": 0,
    "entities_redacted": 0,
    "hipaa_compliance_status": "verified",
    "verification_status": "complete"
  }
}"""

LEGAL_PROMPT = """You are a Legal Intelligence Engine for law firms and compliance departments.

CRITICAL REQUIREMENTS:
- Attorney-client privilege detection is MANDATORY
- Clause-level risk analysis required
- Billable time attribution required
- Conflict of interest detection required

OUTPUTS REQUIRED (return as JSON):
{
  "directors_brief": {
    "title": "Matter or meeting title",
    "matter_reference": "Case/matter number if mentioned",
    "strategic_alignment": "Alignment to firm/client objectives",
    "executive_synthesis": "200-word synthesis for managing partner",
    "privilege_flags": ["Content flagged as potentially privileged"],
    "blockers": ["Legal blockers or obstacles"],
    "cost_of_inaction": "Legal/financial exposure if no action",
    "statute_of_limitations_flags": ["Any deadline-sensitive items"]
  },
  "case_analysis": {
    "case_notes": [
      {
        "topic": "Discussion topic",
        "analysis": "Legal analysis summary",
        "precedent_references": ["Cases or regulations cited"],
        "risk_level": "critical|high|medium|low",
        "recommended_action": "Next legal step"
      }
    ],
    "clause_analysis": [
      {
        "clause_reference": "Section or clause identified",
        "risk_assessment": "Risk description",
        "severity": "critical|high|medium|low",
        "missing_protections": ["Protections that should be added"],
        "recommended_language": "Suggested revision"
      }
    ],
    "obligation_tracking": [
      {
        "obligation": "Contractual or regulatory obligation",
        "responsible_party": "Who must perform",
        "deadline": "When",
        "status": "compliant|at_risk|overdue"
      }
    ]
  },
  "privilege_detection": {
    "privileged_segments": [
      {
        "topic": "Topic of privileged discussion",
        "privilege_type": "attorney_client|work_product|joint_defense",
        "participants": "Who was present",
        "handling_instruction": "How to protect this content"
      }
    ],
    "conflict_check": {
      "potential_conflicts": ["Any conflicts identified"],
      "adverse_parties_mentioned": ["Opposing parties discussed"],
      "recommendation": "Conflict clearance status"
    }
  },
  "billable_time": [
    {
      "attorney": "Name or role",
      "activity": "Work performed",
      "duration_minutes": 30,
      "matter": "Associated matter",
      "billing_code": "Activity code",
      "narrative": "Billing narrative"
    }
  ],
  "risk_indicator": {
    "overall_score": 55,
    "litigation_risk": {
      "score": 60,
      "factors": ["Contributing factors"]
    },
    "regulatory_risk": {
      "score": 40,
      "factors": ["Regulatory concerns"]
    },
    "financial_exposure": {
      "estimated_range": "$X - $Y",
      "basis": "How estimated"
    }
  },
  "decision_ledger": [
    {
      "id": "DEC-001",
      "decision": "Legal decision or strategy",
      "decided_by": "Attorney/partner",
      "legal_basis": "Legal authority",
      "timestamp": "When",
      "privilege_protected": true,
      "confidence": 0.88
    }
  ],
  "action_matrix": [
    {
      "id": "ACT-001",
      "task": "Legal action item",
      "owner": "Responsible attorney",
      "deadline": "Due date (court date, filing deadline)",
      "priority": "critical|high|medium|low",
      "court_date": false,
      "filing_required": false
    }
  ],
  "roi_metrics": {
    "time_to_artifact_seconds": 45,
    "manual_equivalent_hours": 3.5,
    "billable_time_captured_minutes": 120,
    "risks_detected": 3,
    "obligations_tracked": 5,
    "privilege_segments_flagged": 2
  },
  "masking_verification": {
    "categories_checked": 15,
    "entities_found": 0,
    "entities_redacted": 0,
    "verification_status": "complete"
  }
}"""


def get_domain_prompt(domain: str) -> str:
    """Return the domain-specific intelligence prompt."""
    prompts = {
        "enterprise": ENTERPRISE_PROMPT,
        "government": GOVERNMENT_PROMPT,
        "healthcare": HEALTHCARE_PROMPT,
        "legal": LEGAL_PROMPT,
    }
    return prompts.get(domain, ENTERPRISE_PROMPT)


def get_system_prompt(domain: str) -> str:
    """Return the system-level instruction for the AI model."""
    base = "You are an Enterprise Intelligence Engine. Return ONLY valid JSON. No markdown fences. No explanation text."

    if domain == "healthcare":
        return base + " CRITICAL: You MUST mask ALL PHI before analysis. You MUST include CDI analysis, ICD-10/CPT code suggestions, clinical risk scoring, and encounter audit packet. HIPAA compliance is mandatory."
    elif domain == "government":
        return base + " All outputs must be suitable for permanent public record. Include NIST 800-53 control mapping. Flag FOIA-sensitive content."
    elif domain == "legal":
        return base + " Detect attorney-client privilege. Include clause-level risk analysis. Track billable time. Flag conflict of interest."
    else:
        return base + " Focus on strategic alignment, financial impact, and operational efficiency."


def build_full_prompt(content: str, domain: str, participants: str = "", context_docs: str = "") -> str:
    """Build the complete prompt with PHI masking + domain intelligence."""
    domain_prompt = get_domain_prompt(domain)

    return f"""{PHI_MASKING_PROMPT}

{domain_prompt}

CONTENT TO ANALYZE:
{content[:15000]}

PARTICIPANTS: {participants or 'Not specified'}
CONTEXT DOCUMENTS: {context_docs or 'None provided'}

IMPORTANT:
- Return ONLY the JSON object. No markdown. No explanation.
- All PII/PHI must be masked in your output.
- Include masking_verification in your response.
- Be specific with numbers, dates, and names (masked where required).
"""


def calculate_roi(domain: str, content_length: int, processing_time_ms: int, outputs: dict) -> dict:
    """Calculate ROI metrics for the processing."""
    manual_hours = {
        "enterprise": 2.5,
        "government": 3.0,
        "healthcare": 4.0,
        "legal": 3.5,
    }.get(domain, 2.5)

    actions = len(outputs.get("action_matrix", []))
    decisions = len(outputs.get("decision_ledger", []))
    risks = len(outputs.get("risk_indicator", {}).get("factors", []))
    kpis = len(outputs.get("kpi_signals", []))

    roi = {
        "time_to_artifact_seconds": round(processing_time_ms / 1000, 1),
        "manual_equivalent_hours": manual_hours,
        "time_saved_hours": round(manual_hours - (processing_time_ms / 3600000), 2),
        "time_saved_percent": round((1 - (processing_time_ms / (manual_hours * 3600000))) * 100, 1),
        "actions_generated": actions,
        "decisions_captured": decisions,
        "risks_detected": risks,
        "kpis_extracted": kpis,
        "content_words_processed": content_length,
    }

    if domain == "healthcare":
        cdi = outputs.get("clinical_documentation_integrity", {})
        coding = outputs.get("coding_intelligence", {})
        roi["cdi_queries_generated"] = len(cdi.get("findings", []))
        roi["missed_codes_identified"] = len(coding.get("missed_opportunities", []))
        roi["estimated_revenue_recovery"] = coding.get("total_estimated_revenue_impact", "$0")
        roi["cdi_score"] = cdi.get("cdi_score", 0)
        roi["coding_accuracy_score"] = coding.get("coding_accuracy_score", 0)

    elif domain == "government":
        compliance = outputs.get("compliance_traceability", {})
        roi["nist_controls_mapped"] = len(compliance.get("nist_controls_referenced", []))
        roi["regulatory_references"] = len(compliance.get("regulatory_references", []))

    elif domain == "legal":
        privilege = outputs.get("privilege_detection", {})
        billable = outputs.get("billable_time", [])
        roi["privilege_segments_flagged"] = len(privilege.get("privileged_segments", []))
        roi["billable_minutes_captured"] = sum(b.get("duration_minutes", 0) for b in billable)
        roi["obligations_tracked"] = len(outputs.get("case_analysis", {}).get("obligation_tracking", []))

    return roi


def should_auto_queue_hitl(domain: str, confidence: float, outputs: dict) -> dict:
    """Determine if output should be auto-queued for HITL validation."""
    should_queue = False
    reasons = []
    risk_level = "low"

    # Rule 1: Low confidence
    if confidence < 0.85:
        should_queue = True
        reasons.append(f"Confidence below threshold: {confidence:.0%} < 85%")
        risk_level = "high" if confidence < 0.70 else "medium"

    # Rule 2: Healthcare or Government mode always requires validation
    if domain in ("healthcare", "government"):
        should_queue = True
        reasons.append(f"Regulated domain: {domain}")
        if risk_level == "low":
            risk_level = "medium"

    # Rule 3: Healthcare with PHI concerns
    if domain == "healthcare":
        masking = outputs.get("masking_verification", {})
        if masking.get("entities_found", 0) > 0:
            should_queue = True
            risk_level = "critical"
            reasons.append(f"PHI entities detected: {masking.get('entities_found')}")

        cdi = outputs.get("clinical_documentation_integrity", {})
        if cdi.get("cdi_score", 100) < 70:
            should_queue = True
            reasons.append(f"CDI score below threshold: {cdi.get('cdi_score')}")

    # Rule 4: High risk score
    risk_score = outputs.get("risk_indicator", {}).get("overall_score", 0)
    if risk_score > 70:
        should_queue = True
        reasons.append(f"High risk score: {risk_score}")
        risk_level = "critical" if risk_score > 85 else "high"

    # Rule 5: Legal privilege detected
    if domain == "legal":
        privilege = outputs.get("privilege_detection", {})
        if len(privilege.get("privileged_segments", [])) > 0:
            should_queue = True
            reasons.append("Attorney-client privilege content detected")
            risk_level = "high"

    return {
        "should_queue": should_queue,
        "risk_level": risk_level,
        "reasons": reasons,
        "validation_required": should_queue,
        "auto_release_allowed": not should_queue,
    }
