"""
DocuAction AI — Application Entry Point
v6.0.0 — Migration Intelligence Module Added
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
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

    # Ensure per-user area access column exists on the live DB.
    # create_all() only creates missing tables, never adds columns — so add it
    # idempotently here for databases that predate this feature.
    #
    # GRANDFATHERING: the column DEFAULT is the full set of areas, so that when
    # this column is first added to an existing database every current user
    # keeps the all-areas access they had before gating existed (nobody gets
    # locked out). New users created afterward start with [] via the ORM model
    # default (SQLAlchemy always sends allowed_modules explicitly on insert), so
    # they still get "nothing until an admin grants access". The DEFAULT only
    # affects rows present at the moment the column is created.
    try:
        import json
        from app.api.admin_users import AREAS
        all_areas = json.dumps([a["id"] for a in AREAS])  # e.g. ["actions","bulletin",...]
        async with engine.begin() as conn:
            await conn.execute(text(
                f"ALTER TABLE users ADD COLUMN IF NOT EXISTS allowed_modules JSON DEFAULT '{all_areas}'::json"
            ))
        logger.info("users.allowed_modules column verified (existing users grandfathered to all areas)")
    except Exception as e:
        logger.warning(f"allowed_modules column migration skipped: {e}")

    # Bulletin Intelligence — durable store + restore prior state across restarts
    try:
        from app.bulletin_intelligence.bulletin_store import init_store
        from app.bulletin_intelligence.engine import hydrate_from_store
        if await init_store():
            await hydrate_from_store()
    except Exception as e:
        logger.warning(f"Bulletin store init/hydrate skipped: {e}")

    # Bulletin Intelligence — 6AM daily delivery scheduler
    try:
        from app.bulletin_intelligence.scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"Bulletin scheduler not started: {e}")


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
            "case_management": "active",
            "bulletin_intelligence": "active",
        },
    }


def safe_load(module_path: str, prefix: str):
    """Safely load a router module — if it fails, log and continue."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        app.include_router(mod.router)
        logger.info(f"Loaded: {prefix}")
    except Exception as e:
        logger.warning(f"Skipped {prefix}: {e}")


# ═══ CORE ROUTES ═══
safe_load("app.api.routes", "core")

# ═══ ADMIN — USER & AREA ACCESS MANAGEMENT ═══
safe_load("app.api.admin_users", "admin-users")

# ═══ ENTERPRISE ROUTES ═══
safe_load("app.api.enterprise_routes", "enterprise")
safe_load("app.api.validation_routes", "validation")
safe_load("app.api.decision_intel_routes", "decision-intel")
safe_load("app.api.intelligence_routes", "intelligence")
safe_load("app.api.export", "export")
safe_load("app.api.templates", "templates")
safe_load("app.api.meeting_routes", "meetings")
safe_load("app.api.password_reset", "password")
safe_load("app.api.plans", "plans")

# ═══ SLA + ESCALATION ENGINE ═══
safe_load("app.api.sla_routes", "sla")

# ═══ HEALTHCARE CLAIMS INTELLIGENCE ═══
safe_load("app.api.healthcare_claims_routes", "healthcare-claims")

# ═══ WOW FEATURES ═══
safe_load("app.api.wow_routes", "wow-features")

# ═══ MIGRATION INTELLIGENCE ═══
safe_load("app.api.migration_routes", "migration")

# ═══ TEFCA REVIEW PROTOCOL (ONC Contract) ═══
safe_load("app.Tefca", "tefca-review-protocol")

# ═══ CASE MANAGEMENT ═══
safe_load("app.case_management", "case-management")

# ═══ BULLETIN INTELLIGENCE (FCC Daily News — 6AM ET) ═══
safe_load("app.bulletin_intelligence.routes", "bulletin-intelligence")
safe_load("app.bulletin_intelligence.bulletin_download_routes", "bulletin-downloads")

logger.info("DocuAction AI v6.0.0 ready — TEFCA + Bulletin Intelligence + All Modules registered")
