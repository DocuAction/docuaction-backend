"""
DocuAction AI — FastAPI Backend
v3.4.0 — Enterprise Features: Password Reset, Export, Templates
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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("═══════════════════════════════════════════════")
    logger.info("  DocuAction AI Backend v3.4.0")
    logger.info("  Enterprise Fortress + Features Layer")
    logger.info("═══════════════════════════════════════════════")
    logger.info(f"  AI: {settings.AI_PROVIDER} | Haiku + Sonnet")
    logger.info(f"  Keys: Anthropic {'✓' if settings.ANTHROPIC_API_KEY else '✗'} | OpenAI {'✓' if settings.OPENAI_API_KEY else '✗'}")
    logger.info("  Security: JWT 15-min | RBAC | Rate Limiting | Multi-Tenant")
    logger.info("  Features: Export (PDF/DOCX/TXT) | Templates | Password Reset")
    logger.info("  Compliance: AI Disclosure | Hard Delete | Retention | WCAG 2.1 AA")
    logger.info("═══════════════════════════════════════════════")

    from app.services.retention_worker import retention_background_task
    retention_task = asyncio.create_task(retention_background_task())

    yield

    retention_task.cancel()
    await engine.dispose()


app = FastAPI(
    title="DocuAction AI",
    description="Enterprise Document & Voice Intelligence Platform",
    version="3.4.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Middleware
from app.core.error_handler import ErrorHandlerMiddleware, register_exception_handlers
app.add_middleware(ErrorHandlerMiddleware)
register_exception_handlers(app)

from app.core.rate_limiter import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
)

# ─── ALL ROUTES ───
from app.api.routes import router as api_router
app.include_router(api_router)

from app.api.auth_endpoints import router as auth_extras
app.include_router(auth_extras)

from app.api.password_reset import router as password_reset
app.include_router(password_reset)

from app.api.compliance import router as compliance
app.include_router(compliance)

from app.api.security import router as security
app.include_router(security)

from app.api.admin import router as admin
app.include_router(admin)

from app.api.export import router as export
app.include_router(export)

from app.api.templates import router as templates
app.include_router(templates)


@app.get("/", tags=["Health"])
async def root():
    return {"app": "DocuAction AI", "version": "3.4.0", "status": "running"}

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "service": "docuaction-ai", "version": "3.4.0"}
