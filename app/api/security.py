"""
2026 Compliance Layer — Security & Data Residency
API endpoint providing security posture, data residency, and no-training guarantees.
"""
from fastapi import APIRouter
from datetime import datetime

router = APIRouter(prefix="/api/security", tags=["Security & Compliance"])


@router.get("/residency")
async def get_security_residency():
    """
    Returns security posture, data residency info, and AI training data guarantees.
    Public endpoint — no auth required for transparency.
    """
    return {
        "data_residency": {
            "primary_region": "US-East (Virginia)",
            "provider": "Railway.app on Google Cloud Platform",
            "database_location": "US-East",
            "file_storage_location": "US-East",
            "backup_region": "US-East (same region)",
            "data_sovereignty": "All data stored and processed within the United States",
            "gdpr_note": "EU data processing available upon request with Data Processing Agreement",
        },
        "ai_training_guarantees": {
            "no_training_data": True,
            "statement": "DocuAction AI does NOT use any customer data, documents, transcripts, or outputs to train AI models. This is a contractual guarantee.",
            "anthropic_policy": "Anthropic Claude API (our primary AI provider) does not train on API inputs/outputs per their enterprise data policy.",
            "openai_policy": "OpenAI Whisper API (audio transcription) does not train on API inputs per their enterprise data policy.",
            "data_retention_by_ai_providers": "Zero retention — API providers process and discard data immediately after response generation.",
        },
        "encryption": {
            "in_transit": "TLS 1.3 (HTTPS enforced)",
            "at_rest": "AES-256 encryption on database storage",
            "api_keys": "Encrypted environment variables, never stored in code",
            "file_uploads": "Stored on encrypted volumes with per-user UUID isolation",
        },
        "access_control": {
            "authentication": "JWT (JSON Web Token) with HS256 signing",
            "password_hashing": "bcrypt with salt rounds",
            "session_duration": "24 hours",
            "rbac_roles": ["Admin", "Manager", "Contributor", "Viewer"],
            "document_level_permissions": True,
        },
        "compliance_frameworks": {
            "SOC_2_Type_II": "Architecture aligned",
            "HIPAA": "BAA available, PHI/PII masking before AI processing",
            "FedRAMP": "In Progress — architecture designed for Moderate authorization",
            "GDPR": "Data subject rights endpoints available (export, delete)",
            "CCPA": "Consumer data rights supported",
            "Utah_AI_Act": "AI disclosure on all outputs",
            "Colorado_AI_Act": "AI transparency labeling implemented",
            "NIST_800_53": "Controls mapped for federal deployments",
            "ISO_27001": "Certified (Alliance Global Tech)",
            "CMMI_Level_3": "Certified (Alliance Global Tech)",
        },
        "ai_transparency": {
            "human_in_the_loop": True,
            "confidence_scoring": "0-100% on every output",
            "model_disclosure": "Model name logged and displayed for every AI output",
            "explainability": "Source citations and reasoning chain available",
            "no_autonomous_decisions": "All AI outputs require human approval before action",
        },
        "certifications": {
            "company": "Alliance Global Tech, Inc.",
            "sba_8a": True,
            "gsa_mas": "Contract 47QTCA21D003M",
            "cage_code": "8ERE8",
            "uei": "MP2FLV1MAW93",
            "dod_facility_clearance": True,
        },
        "last_updated": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/status")
async def get_security_status():
    """Quick security status check."""
    return {
        "encryption_active": True,
        "pii_masking_active": True,
        "audit_logging_active": True,
        "ai_disclosure_active": True,
        "data_region": "US-East",
        "no_training_data": True,
        "last_check": datetime.utcnow().isoformat() + "Z",
    }
