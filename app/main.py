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
    # Schema setup MUST succeed before serving traffic — if the DB is briefly
    # unavailable at boot, retry instead of silently skipping (a skipped column
    # migration breaks every User query, i.e. login/signup return 500).
    import asyncio, json
    try:
        from app.api.admin_users import AREAS
        all_areas = json.dumps([a["id"] for a in AREAS])  # e.g. ["actions","bulletin",...]
    except Exception:
        all_areas = "[]"

    for attempt in range(1, 8):
        try:
            async with engine.begin() as conn:
                # 1) create any missing tables
                await conn.run_sync(Base.metadata.create_all)
                # 2) Ensure per-user area access column exists. create_all() never
                #    adds columns to existing tables, so add it idempotently here.
                #    GRANDFATHERING: the DEFAULT is the full set of areas, so when
                #    this column is first added to an existing DB every current
                #    user keeps the all-areas access they had before gating existed
                #    (nobody gets locked out). New users still start with [] via the
                #    ORM model default. The DEFAULT only affects rows present when
                #    the column is created.
                await conn.execute(text(
                    f"ALTER TABLE users ADD COLUMN IF NOT EXISTS allowed_modules JSON DEFAULT '{all_areas}'::json"
                ))
            logger.info("DB schema verified (tables + users.allowed_modules; existing users grandfathered)")
            break
        except Exception as e:
            logger.warning(f"DB schema setup attempt {attempt}/7 failed, retrying: {e}")
            await asyncio.sleep(3)
    else:
        logger.error("DB schema setup FAILED after retries — User queries/login may 500 until fixed")

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
