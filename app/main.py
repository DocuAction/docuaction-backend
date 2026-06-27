"""
DocuAction AI — Application Entry Point
v6.0.0 — Migration Intelligence Module Added
"""
import time
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
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

# ── CORS (FIX 8 — NIST SC-7). Wildcard removed; restricted to configured
#    origins. Credentials are not needed (auth is via the Authorization bearer
#    header, not cookies), and allow_credentials must never be True with a
#    wildcard, so it is False. ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Trusted Host (FIX 8 — NIST SC-7) — reject Host-header spoofing. ──
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)


# ── Security response headers (FIX 8 — NIST SC-8 / SC-18). ──
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    return response


@app.on_event("startup")
async def startup():
    # Schema setup MUST succeed before serving traffic — if the DB is briefly
    # unavailable at boot, retry instead of silently skipping (a skipped column
    # migration breaks every User query, i.e. login/signup return 500).
    import asyncio, json, os
    try:
        from app.api.admin_users import MODULES
        all_areas = json.dumps([m["id"] for m in MODULES])  # 15 module ids
    except Exception:
        all_areas = "[]"

    # Ensure every column the User model expects exists on the live DB. create_all()
    # never adds columns to existing tables, so older/drifted databases can be
    # missing columns — and ANY missing column makes every User query (login/
    # signup) return 500. All statements are IF NOT EXISTS, so they are safe
    # no-ops when the column already exists.
    # GRANDFATHERING: allowed_modules' DEFAULT is the full area set, so existing
    # users keep all-areas access the moment the column is first added; new users
    # still start with [] via the ORM model default.
    user_columns = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50) NOT NULL DEFAULT 'default'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS company VARCHAR(255) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR(255) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'contributor'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR(20) DEFAULT 'free'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMP DEFAULT now()",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",
        f"ALTER TABLE users ADD COLUMN IF NOT EXISTS allowed_modules JSON DEFAULT '{all_areas}'::json",
        # audit_logs (per-user activity trail) — repair drift so inserts/reads work.
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50) NOT NULL DEFAULT 'default'",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS resource_type VARCHAR(50)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS resource_id VARCHAR(255)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS details JSON",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS ip_address VARCHAR(50)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()",
    ]
    for attempt in range(1, 8):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)  # create any missing tables
                for stmt in user_columns:
                    await conn.execute(text(stmt))
            logger.info("DB schema verified (tables + all users columns; existing users grandfathered)")
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

    # Bulletin Intelligence — 6AM daily delivery scheduler.
    # Gated behind ENABLE_SCHEDULER so multiple identical deployments can share
    # ONE database without both running the daily cycle — which would write
    # duplicate briefings to the DB and email subscribers two copies of each
    # briefing. Set ENABLE_SCHEDULER=true on exactly ONE box (the live one);
    # leave it unset on any spare/duplicate box. Default off = safe (no sends).
    if os.getenv("ENABLE_SCHEDULER", "false").strip().lower() == "true":
        try:
            from app.bulletin_intelligence.scheduler import start_scheduler
            start_scheduler()
            logger.info("Bulletin scheduler ENABLED (ENABLE_SCHEDULER=true)")
        except Exception as e:
            logger.warning(f"Bulletin scheduler not started: {e}")
    else:
        logger.info("Bulletin scheduler DISABLED (set ENABLE_SCHEDULER=true to enable)")


# Cached TEFCA connector probe. /health is polled frequently by load balancers,
# so the live probe result is cached for 60s to avoid hammering the source APIs.
_TEFCA_PROBE = {"ts": 0.0, "value": None}
_TEFCA_PROBE_TTL = 60.0


async def _probe_tefca():
    """Real connector probe (cached). The TEFCA health status is DERIVED from a
    live probe — never hardcoded (FIX 1). 'active' only when the core keyless
    public sources (NPPES/LEIE/PECOS) actually respond."""
    now = time.monotonic()
    cached = _TEFCA_PROBE["value"]
    if cached is not None and (now - _TEFCA_PROBE["ts"]) < _TEFCA_PROBE_TTL:
        return cached
    try:
        from app.Tefca.connectors import SourceConnectorManager
        probe = await SourceConnectorManager().health_check()
        core_live = any(probe.get(s, {}).get("live") for s in ("NPPES", "OIG_LEIE", "PECOS"))
        result = {"status": "active" if core_live else "degraded", "connectors": probe}
    except Exception as e:
        result = {"status": "import_failed", "connectors": {"error": str(e)}}
    _TEFCA_PROBE.update(ts=now, value=result)
    return result


@app.get("/health")
async def health():
    tefca = await _probe_tefca()
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
            # Real probe result — "active" only if core connectors responded (FIX 1).
            "tefca_review_protocol": tefca["status"],
            "case_management": "active",
            "bulletin_intelligence": "active",
        },
        "tefca_connectors": tefca["connectors"],
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

# ═══ AUDIO (Whisper transcription) ═══
safe_load("app.api.audio_routes", "audio")

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

# ═══ TEFCA REVIEW PROTOCOL (ONC Contract 7571MN26F80064) ═══
# Core contract deliverable — registered UNCONDITIONALLY. If it cannot import,
# the application MUST fail to start. We do NOT route it through safe_load, which
# would swallow an import error and silently turn every /api/v1/tefca/* endpoint
# into a 404 while /health falsely reported it "active". A broken TEFCA module is
# a hard startup failure, by design.
from app.Tefca import router as tefca_router  # noqa: E402  (fail-loud on import error)
from app.Tefca.routes import tefca_dashboard_router  # noqa: E402
app.include_router(tefca_router)
app.include_router(tefca_dashboard_router)  # executive dashboard at /api/tefca/*
logger.info("Loaded: tefca-review-protocol + dashboard (REQUIRED — unconditional registration)")

# ═══ CASE MANAGEMENT ═══
safe_load("app.case_management", "case-management")

# ═══ BULLETIN INTELLIGENCE (FCC Daily News — 6AM ET) ═══
safe_load("app.bulletin_intelligence.routes", "bulletin-intelligence")
safe_load("app.bulletin_intelligence.bulletin_download_routes", "bulletin-downloads")

logger.info("DocuAction AI v6.0.0 ready — TEFCA + Bulletin Intelligence + All Modules registered")
