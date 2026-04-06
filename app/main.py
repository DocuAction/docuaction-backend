"""
DocuAction AI — FastAPI Backend
v3.5.0 — Infrastructure Hardened
- Resilient startup: retries DB connection instead of crashing
- Independent healthcheck: returns 200 even if DB is slow
- File storage: absolute paths with auto-creation
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("docuaction")

# Track DB status globally so health check doesn't depend on DB
_db_ready = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_ready

    logger.info("═══════════════════════════════════════════════")
    logger.info("  DocuAction AI Backend v3.5.0")
    logger.info("  Infrastructure: Hardened")
    logger.info("═══════════════════════════════════════════════")
    logger.info(f"  AI: Anthropic {'✓' if settings.ANTHROPIC_API_KEY else '✗'} | OpenAI {'✓' if settings.OPENAI_API_KEY else '✗'}")
    logger.info(f"  Port: {settings.PORT}")
    logger.info("═══════════════════════════════════════════════")

    # Initialize database with retries — won't crash if DB is slow
    from app.core.database import init_database
    _db_ready = await init_database(retries=5, delay=3.0)

    if _db_ready:
        logger.info("  Database: ✓ Connected")
    else:
        logger.warning("  Database: ✗ Not connected (will retry on first request)")

    # Start retention worker only if DB is ready
    retention_task = None
    if _db_ready:
        try:
            from app.services.retention_worker import retention_background_task
            retention_task = asyncio.create_task(retention_background_task())
            logger.info("  Retention Worker: ✓ Started")
        except Exception as e:
            logger.warning(f"  Retention Worker: ✗ {e}")

    logger.info("═══════════════════════════════════════════════")
    logger.info("  Server ready to accept requests")
    logger.info("═══════════════════════════════════════════════")

    yield

    # Shutdown
    if retention_task:
        retention_task.cancel()
    from app.core.database import engine
    await engine.dispose()
    logger.info("DocuAction AI Backend stopped.")


app = FastAPI(
    title="DocuAction AI",
    description="Enterprise Document & Voice Intelligence Platform",
    version="3.5.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── MIDDLEWARE ───
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

# ─── ROUTES ───
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

from app.api.export import router as export_router
app.include_router(export_router)

from app.api.templates import router as templates
app.include_router(templates)

from app.api.plans import router as plans
app.include_router(plans)


# ─── HEALTH CHECK (INDEPENDENT OF DATABASE) ───
@app.get("/", tags=["Health"])
async def root():
    return {"app": "DocuAction AI", "version": "3.5.0", "status": "running"}


@app.get("/health", tags=["Health"])
async def health():
    """
    Health check endpoint — returns 200 OK without requiring database.
    Railway uses this to determine if the service is alive.
    Database status is reported but doesn't block the response.
    """
    db_status = "connected" if _db_ready else "connecting"

    # Optional: quick DB check (non-blocking)
    if _db_ready:
        try:
            from app.core.database import check_db_connection
            db_ok = await check_db_connection()
            db_status = "healthy" if db_ok else "degraded"
        except Exception:
            db_status = "degraded"

    return {
        "status": "healthy",
        "service": "docuaction-ai",
        "version": "3.5.0",
        "database": db_status,
    }
