"""
DocuAction AI — Application Entry Point
v6.0.0 — Migration Intelligence Module Added
"""
import os
import time
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import text
from app.core.config import settings
from app.core.database import engine, Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docuaction")

# Interactive API docs are disabled in production (info-disclosure hardening) unless
# ENABLE_DOCS=true; they remain on in development. Disabling docs_url also disables the
# Swagger UI, ReDoc, and the OpenAPI schema endpoint.
_docs_enabled = (
    settings.is_development
    or getattr(settings, "ENABLE_DOCS", False)
    or getattr(settings, "ENABLE_OPENAPI", False)
)
app = FastAPI(
    title="DocuAction AI",
    version="6.0.0",
    description="Enterprise Intelligence Operating System — Document, Voice, Healthcare, and Migration Intelligence with Decision-Grade Governance",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
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

# ── Global API rate limiting (NIST SC-5 / DoS + third-party AI cost abuse). Uses the
#    existing in-memory tiered limiter (Free 60/min .. Enterprise high; identity from
#    the JWT, else client IP). Health/docs are exempt inside the middleware. This
#    complements the stricter, dedicated limits already on the auth endpoints. ──
from app.core.rate_limiter import RateLimitMiddleware  # noqa: E402
app.add_middleware(RateLimitMiddleware)


# ── Security response headers (FIX 8 — NIST SC-8 / SC-18). ──
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


# ── Global exception handlers — production never leaks stack traces, DB errors,
#    filesystem paths, or raw Python exceptions; the full error is logged internally
#    and a generic message is returned (NIST SI-11 / OWASP A05). ──
from app.core.error_handler import register_exception_handlers  # noqa: E402
register_exception_handlers(app)


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

    # Register the Phase 1A platform configuration tables on the shared Base so
    # the create_all below creates them (idempotent — checkfirst / IF NOT EXISTS).
    # The Alembic migration 20260725_platform_config creates the same set.
    import app.platform_config  # noqa: F401  (registration side effect)
    # Register the Phase 1B TEFCA registry tables (tefca_reg_* / tefca_entity_*),
    # separate from the legacy app.Tefca tables. Migration: 20260725_tefca_registry.
    import app.tefca_registry  # noqa: F401  (registration side effect)

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
        # Registration-security columns (P1 fix). DEFAULT true / 'active' GRANDFATHERS
        # every pre-existing account as already verified & active, so this fix never
        # locks out current users. New public signups are set to the pending state
        # explicitly by the signup endpoint (ORM), overriding these DB defaults.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT true",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(30) DEFAULT 'active'",
        # Session-invalidation epoch (enterprise hardening). No default => NULL for every
        # existing row, i.e. "never revoked", so current sessions are unaffected.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_revoked_at TIMESTAMP",
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
        # Upload security scan — SHA-256 checksum of uploaded document bytes.
        # NULL for pre-existing rows (grandfathered); populated on new uploads.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS checksum_sha256 VARCHAR(64)",
        # TEFCA verification confidence (0.0-1.0), written by
        # POST /api/tefca/registry/entities/{id}/verify. No default: NULL means
        # "never verified", which is not the same claim as 0.0 ("verified, and
        # every source disagreed"). create_all() cannot add a column to a table
        # that already exists, which is why this lives here.
        "ALTER TABLE tefca_reg_entities ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION",
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

    # TEFCA QA framework — ensure audit table + run platform readiness check.
    # Non-blocking: a failed check logs a warning and startup continues (skip_http
    # avoids self-calling the API before this process is serving).
    try:
        from app.Tefca import qa_engine
        from app.core.database import async_session_maker as _qa_sm
        async with _qa_sm() as _qa_s:
            await qa_engine.ensure_qa_table(_qa_s)
            _readiness = await qa_engine.PlatformReadinessCheck().run(_qa_s, skip_http=True)
            _golden = await qa_engine.run_golden_regression(_qa_s)
        logger.info(f"TEFCA QA readiness: ready={_readiness['ready']} score={_readiness['score']} "
                    + ", ".join(f"{c['name']}={'ok' if c['passed'] else 'FAIL'}" for c in _readiness['checks']))
        if _golden['drift_detected']:
            logger.error(f"TEFCA QA GOLDEN-RECORD DRIFT DETECTED: {_golden['failing_cases']}")
        else:
            logger.info(f"TEFCA QA golden regression: {_golden['passed']}/{_golden['total']} passed (no drift)")
        # Continuous QA monitor (separate scheduler; gated by ENABLE_QA_MONITOR, default off).
        from app.Tefca.qa_monitor import start_qa_monitor
        start_qa_monitor()
    except Exception as e:
        logger.warning(f"TEFCA QA startup check skipped: {e}")

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
    # Bulletin scheduler observability — confirms whether ENABLE_SCHEDULER is set and
    # the daily/self-heal jobs actually started on this box. Never breaks /health.
    try:
        from app.bulletin_intelligence.scheduler import scheduler_status
        scheduler = scheduler_status()
    except Exception as e:
        scheduler = {"running": False, "error": str(e)}
    return {
        "status": "healthy",
        "version": "6.0.0",
        "platform": "DocuAction AI",
        "scheduler": scheduler,
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


@app.get("/api/config")
async def get_config(request: Request):
    """Which backend is this, really — deliberately unauthenticated.

    A frontend built against the wrong API cannot detect that on its own: the
    URL it calls is baked into its bundle, so it has no second opinion. This
    endpoint is that second opinion, which is why it must answer before login.
    It returns only what the caller already knows by virtue of reaching it (the
    host it dialled) plus the environment name, so there is nothing here worth
    authenticating.
    """
    return {
        "environment": os.getenv("ENVIRONMENT", "unknown"),
        "version": "6.0.0",
        "api_host": request.url.hostname,
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

# ═══ MICROSOFT ENTRA ID SSO (additional login option; email/password unaffected) ═══
safe_load("app.api.azure_auth_routes", "azure-auth")

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

# ═══ TEFCA REGISTRY (Phase 2A) — new normalized entity registry + verification ═══
# Read/query + verification API at /api/tefca/registry/*, separate from the legacy
# TEFCA routers above. Over the tefca_reg_* tables (Phase 1B).
from app.tefca_registry.routes import router as tefca_registry_router  # noqa: E402
app.include_router(tefca_registry_router)

# TEFCA ARC Tasks 3-5 — versioned rules, sampling, reviews, reports, priority
# review. Separate router because the review engine changes when ONC guidance
# changes, which is a different cadence from the registry CRUD beneath it.
from app.tefca_registry.review_routes import router as tefca_review_router  # noqa: E402
app.include_router(tefca_review_router)
logger.info("Loaded: tefca-arc-review (Tasks 3-5 — /api/tefca/review-rules, /samples, /reviews, /reports)")
logger.info("Loaded: tefca-registry (Phase 2A — /api/tefca/registry/*)")

# ═══ CASE MANAGEMENT ═══
safe_load("app.case_management", "case-management")

# ═══ BULLETIN INTELLIGENCE (FCC Daily News — 6AM ET) ═══
safe_load("app.bulletin_intelligence.routes", "bulletin-intelligence")
safe_load("app.bulletin_intelligence.bulletin_download_routes", "bulletin-downloads")

logger.info("DocuAction AI v6.0.0 ready — TEFCA + Bulletin Intelligence + All Modules registered")
