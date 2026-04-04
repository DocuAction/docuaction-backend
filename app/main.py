"""
DocuAction AI — FastAPI Backend
Global Enterprise Fortress Layer
v3.3.0 — IAM, Multi-Tenant, Rate Limiting, Compliance
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import engine, Base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("docuaction")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("═══════════════════════════════════════════════")
    logger.info("  DocuAction AI Backend v3.3.0")
    logger.info("  Enterprise Fortress Layer: ACTIVE")
    logger.info("═══════════════════════════════════════════════")
    logger.info(f"  AI Provider: {settings.AI_PROVIDER}")
    logger.info(f"  Haiku: {settings.ANTHROPIC_MODEL}")
    logger.info(f"  Sonnet: {settings.ANTHROPIC_SONNET_MODEL}")
    logger.info(f"  Anthropic Key: {'✓' if settings.ANTHROPIC_API_KEY else '✗'}")
    logger.info(f"  OpenAI Key: {'✓' if settings.OPENAI_API_KEY else '✗'}")
    logger.info(f"  Database: Connected")
    logger.info("  ── Security Layers ──")
    logger.info("  JWT: 15-min access + 7-day refresh rotation")
    logger.info("  RBAC: Admin > Manager > Contributor > Viewer")
    logger.info("  Rate Limiting: Active (tier-based)")
    logger.info("  Error Handling: Standardized JSON (no stack traces)")
    logger.info("  Multi-Tenant: Isolation enforced")
    logger.info("  Audit Logging: All write operations")
    logger.info("  AI Disclosure: 2026 compliance active")
    logger.info("  Retention Policy: Auto-cleanup enabled")
    logger.info("═══════════════════════════════════════════════")

    # Start retention background worker
    from app.services.retention_worker import retention_background_task
    retention_task = asyncio.create_task(retention_background_task())

    yield

    # Shutdown
    retention_task.cancel()
    await engine.dispose()
    logger.info("DocuAction AI Backend stopped.")


app = FastAPI(
    title="DocuAction AI",
    description="Enterprise Document & Voice Intelligence Platform — Fortress Edition",
    version="3.3.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── MIDDLEWARE STACK (order matters: outermost first) ───

# 1. Standardized Error Handler (catches all unhandled exceptions)
from app.core.error_handler import ErrorHandlerMiddleware, register_exception_handlers
app.add_middleware(ErrorHandlerMiddleware)
register_exception_handlers(app)

# 2. Rate Limiting
from app.core.rate_limiter import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# 3. CORS
origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
)

# ─── ROUTES ───

# Core API routes
from app.api.routes import router as api_router
app.include_router(api_router)

# Auth token refresh endpoint
from app.api.auth_endpoints import router as auth_extras_router
app.include_router(auth_extras_router)

# Compliance (Hard Delete, Data Export)
from app.api.compliance import router as compliance_router
app.include_router(compliance_router)

# Security & Residency
from app.api.security import router as security_router
app.include_router(security_router)

# Admin endpoints (retention, system status)
from app.api.admin import router as admin_router
app.include_router(admin_router)


@app.get("/", tags=["Health"])
async def root():
    return {
        "app": "DocuAction AI",
        "version": "3.3.0",
        "edition": "Enterprise Fortress",
        "status": "running",
        "security": {
            "jwt": "15-min access + refresh rotation",
            "rbac": "4-tier enforced",
            "rate_limiting": "active",
            "multi_tenant": "isolated",
            "audit_logging": "enabled",
            "ai_disclosure": "2026 compliant",
        },
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "healthy",
        "service": "docuaction-ai",
        "version": "3.3.0",
        "fortress": "active",
    }
