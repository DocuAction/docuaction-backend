"""
DocuAction AI — Application Entry Point
v6.0.0 — Migration Intelligence Module Added
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import engine, Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docuaction")

app = FastAPI(
    title="DocuAction AI",
    version="6.0.0",
    description="Enterprise Intelligence Operating System — Document, Voice, Healthcare, and Migration Intelligence with Decision-Grade Governance",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables verified")
    except Exception as e:
        logger.warning(f"Database init deferred: {e}")

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "6.0.0",
        "platform": "DocuAction AI",
        "modules": {
            "documents": "active",
            "audio": "active",
            "healthcare": "active",
            "data_systems": "active",
            "comparison": "active",
            "extraction": "active",
            "automation": "active",
            "tefca_review_protocol": "active",
        },
    }

def safe_load(module_path: str, prefix: str):
    """Safely load a router module — if it fails, log and continue.
    This ensures one module's failure never affects other modules."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        app.include_router(mod.router)
        logger.info(f"Loaded: {prefix}")
    except Exception as e:
        logger.warning(f"Skipped {prefix}: {e}")

# ═══ CORE ROUTES (Documents, Auth, Process) ═══
safe_load("app.api.routes", "core")

# ═══ ENTERPRISE ROUTES ═══
safe_load("app.api.enterprise_routes", "enterprise")
safe_load("app.api.validation_routes", "validation")
safe_load("app.api.decision_intel_routes", "decision-intel")
safe_load("app.api.intelligence_routes", "intelligence")
safe_load("app.api.cross_meeting_routes", "cross-meeting")
safe_load("app.api.export_routes", "export")
safe_load("app.api.template_routes", "templates")
safe_load("app.api.meeting_routes", "meetings")
safe_load("app.api.password_routes", "password")
safe_load("app.api.plan_routes", "plans")

# ═══ SLA + ESCALATION ENGINE ═══
safe_load("app.api.sla_routes", "sla")

# ═══ HEALTHCARE CLAIMS INTELLIGENCE ═══
safe_load("app.api.healthcare_claims_routes", "healthcare-claims")

# ═══ WOW FEATURES — Multi-Doc Comparison, Extraction, Automation ═══
safe_load("app.api.wow_routes", "wow-features")

# ═══ MIGRATION INTELLIGENCE (Feature-Gated) ═══
# This module is protected by module_gate.py feature flags.
# Even though routes are registered, every endpoint checks
# module_data_systems flag before processing.
# If the flag is FALSE, all endpoints return 403.
safe_load("app.api.migration_routes", "migration")

# ═══ TEFCA REVIEW PROTOCOL (ONC Contract) ═══
# AGT — ONC TEFCA Participant & Subparticipant Data Accuracy Review
# Tier 1 automated validation: NPPES · OIG LEIE · SAM.gov · PECOS
# 5-element evidence records · All ONC SOW deliverables
safe_load("app.Tefca", "tefca-review-protocol")

logger.info("DocuAction AI v6.0.0 ready — Migration Intelligence + TEFCA Review Protocol registered")