"""DocuAction AI v3.7.0 — Bulletproof startup"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("docuaction")

_db_ready = False
_loaded = []
_failed = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_ready
    logger.info("=== DocuAction AI v3.7.0 Starting ===")

    base = os.getenv("UPLOAD_DIR", "./uploads")
    if not os.path.isabs(base):
        base = os.path.join(os.getcwd(), base)
    for d in ["documents", "audio", "audio/chunks", "meetings"]:
        os.makedirs(os.path.join(base, d), exist_ok=True)

    try:
        from app.core.database import init_database
        _db_ready = await init_database(retries=5, delay=2.0)
    except Exception as e:
        logger.error(f"DB: {e}")

    logger.info(f"  DB: {'OK' if _db_ready else 'UNAVAILABLE'}")
    logger.info(f"  Loaded: {', '.join(_loaded)}")
    if _failed:
        logger.warning(f"  Failed: {', '.join(_failed)}")
    logger.info("=== Server ready ===")
    yield
    try:
        from app.core.database import engine
        await engine.dispose()
    except:
        pass

app = FastAPI(title="DocuAction AI", version="3.7.0", docs_url="/docs", lifespan=lifespan)

# ═══ HEALTH — DEFINED BEFORE ANYTHING ELSE ═══
@app.get("/health")
async def health():
    return {"status": "healthy", "service": "docuaction-ai", "version": "3.7.0"}

@app.get("/")
async def root():
    return {"app": "DocuAction AI", "version": "3.7.0", "status": "running"}

# ═══ CORS ═══
allowed = os.getenv("ALLOWED_ORIGINS", "https://app.docuaction.io,http://localhost:3000")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in allowed.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"])

# ═══ MIDDLEWARE — safe ═══
try:
    from app.core.error_handler import ErrorHandlerMiddleware, register_exception_handlers
    app.add_middleware(ErrorHandlerMiddleware)
    register_exception_handlers(app)
    _loaded.append("errors")
except Exception as e:
    _failed.append(f"errors:{e}")

try:
    from app.core.rate_limiter import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
    _loaded.append("ratelimit")
except Exception as e:
    _failed.append(f"ratelimit:{e}")

# ═══ ROUTES — each wrapped individually, never crashes server ═══
def _load(path, name):
    try:
        import importlib
        mod = importlib.import_module(path)
        app.include_router(mod.router)
        _loaded.append(name)
        logger.info(f"  Route loaded: {name}")
    except Exception as e:
        _failed.append(f"{name}")
        logger.warning(f"  Route FAILED: {name} — {e}")

_load("app.api.routes", "api")
_load("app.api.auth_endpoints", "auth")
_load("app.api.password_reset", "pwreset")
_load("app.api.compliance", "compliance")
_load("app.api.security", "security")
_load("app.api.admin", "admin")
_load("app.api.export", "export")
_load("app.api.templates", "templates")
_load("app.api.plans", "plans")
_load("app.api.audio_routes", "audio")
_load("app.api.meeting_routes", "meetings")
