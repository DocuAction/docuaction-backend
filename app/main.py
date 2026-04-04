"""
DocuAction AI — FastAPI Backend
Production entry point with 2026 Compliance Layer
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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("═══ DocuAction AI Backend Started ═══")
    logger.info(f"  AI Provider: {settings.AI_PROVIDER}")
    logger.info(f"  Haiku Model: {settings.ANTHROPIC_MODEL}")
    logger.info(f"  Sonnet Model: {settings.ANTHROPIC_SONNET_MODEL}")
    logger.info(f"  Anthropic Key: {'✓ Set' if settings.ANTHROPIC_API_KEY else '✗ Missing'}")
    logger.info(f"  OpenAI Key: {'✓ Set' if settings.OPENAI_API_KEY else '✗ Missing'}")
    logger.info(f"  Database: Connected")
    logger.info(f"  2026 Compliance Layer: Active")
    logger.info("═══════════════════════════════════════")
    yield
    await engine.dispose()


app = FastAPI(
    title="DocuAction AI",
    description="Enterprise Document & Voice Intelligence Platform API — 2026 Compliance Ready",
    version="3.2.0",
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

# Core routes
from app.api.routes import router as api_router
app.include_router(api_router)

# 2026 Compliance routes
from app.api.compliance import router as compliance_router
app.include_router(compliance_router)

# Security & Residency routes
from app.api.security import router as security_router
app.include_router(security_router)


@app.get("/", tags=["Health"])
async def root():
    return {
        "app": "DocuAction AI",
        "version": "3.2.0",
        "status": "running",
        "compliance": "2026 Layer Active",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "service": "docuaction-ai", "version": "3.2.0"}
