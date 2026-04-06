"""DocuAction AI v3.8.0 — Persistent Intelligence"""
import os, sys, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docuaction")
_db_ready = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_ready
    logger.info("STARTING v3.8.0")
    base = os.getenv("UPLOAD_DIR", "./uploads")
    if not os.path.isabs(base):
        base = os.path.join(os.getcwd(), base)
    for d in ["documents","audio","audio/chunks","meetings"]:
        os.makedirs(os.path.join(base, d), exist_ok=True)
    try:
        from app.core.database import init_database
        _db_ready = await init_database(retries=5, delay=2.0)
    except Exception as e:
        logger.error(f"DB: {e}")
    logger.info("READY")
    yield
    try:
        from app.core.database import engine
        await engine.dispose()
    except:
        pass

app = FastAPI(title="DocuAction AI", version="3.8.0", docs_url="/docs", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "docuaction-ai", "version": "3.8.0"}

@app.get("/")
async def root():
    return {"app": "DocuAction AI", "version": "3.8.0"}

allowed = os.getenv("ALLOWED_ORIGINS", "https://app.docuaction.io,http://localhost:3000")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in allowed.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def safe_load(path, name):
    try:
        import importlib
        mod = importlib.import_module(path)
        app.include_router(mod.router)
        logger.info(f"OK: {name}")
    except Exception as e:
        logger.error(f"FAIL: {name} - {e}")

safe_load("app.api.routes", "api")
safe_load("app.api.auth_endpoints", "auth")
safe_load("app.api.password_reset", "pwreset")
safe_load("app.api.compliance", "compliance")
safe_load("app.api.security", "security")
safe_load("app.api.admin", "admin")
safe_load("app.api.templates", "templates")
safe_load("app.api.plans", "plans")
safe_load("app.api.export", "export")
safe_load("app.api.audio_routes", "audio")
safe_load("app.api.meeting_routes", "meetings")
safe_load("app.api.intelligence_routes", "intelligence")
safe_load("app.api.validation_routes", "validation")