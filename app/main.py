"""
DocuAction AI — FastAPI Backend
Production entry point with lifespan management
"""
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
    """Create database tables on startup, cleanup on shutdown."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("═══ DocuAction AI Backend Started ═══")
    logger.info(f"  AI Provider: {settings.AI_PROVIDER}")
    logger.info(f"  Haiku Model: {settings.ANTHROPIC_MODEL}")
    logger.info(f"  Sonnet Model: {settings.ANTHROPIC_SONNET_MODEL}")
    logger.info(f"  Anthropic Key: {'✓ Set' if settings.ANTHROPIC_API_KEY else '✗ Missing'}")
    logger.info(f"  OpenAI Key: {'✓ Set' if settings.OPENAI_API_KEY else '✗ Missing'}")
    logger.info(f"  Database: Connected")
    logger.info("═══════════════════════════════════════")
    yield
    await engine.dispose()
    logger.info("DocuAction AI Backend stopped.")


app = FastAPI(
    title="DocuAction AI",
    description="Enterprise Document & Voice Intelligence Platform API",
    version="3.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS
origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Import and mount routes ───
from app.api.routes import router as api_router
app.include_router(api_router)


@app.get("/", tags=["Health"])
async def root():
    return {
        "app": "DocuAction AI",
        "version": "3.1.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "service": "docuaction-ai"}
