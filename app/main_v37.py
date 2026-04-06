"""DocuAction AI v3.6.0 - Zero-failure audio pipeline"""
import os
import asyncio
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
    logger.info("=== DocuAction AI v3.6.0 Starting ===")

    base = os.getenv("UPLOAD_DIR", "./uploads")
    if not os.path.isabs(base):
        base = os.path.join(os.getcwd(), base)
    os.makedirs(os.path.join(base, "documents"), exist_ok=True)
    os.makedirs(os.path.join(base, "audio"), exist_ok=True)
    os.makedirs(os.path.join(base, "audio", "chunks"), exist_ok=True)

    # Check FFmpeg
    import subprocess
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        logger.info("  FFmpeg: Available")
    except Exception:
        logger.warning("  FFmpeg: NOT FOUND - install via nixpacks.toml")

    try:
        from app.core.database import init_database
        _db_ready = await init_database(retries=5, delay=2.0)
    except Exception as e:
        logger.error(f"DB error: {e}")

    logger.info(f"  DB: {'OK' if _db_ready else 'UNAVAILABLE'}")
    logger.info(f"  Routes: {', '.join(_loaded)}")
    if _failed:
        logger.warning(f"  Failed: {', '.join(_failed)}")
    logger.info("=== Server ready ===")
    yield
    try:
        from app.core.database import engine
        await engine.dispose()
    except:
        pass

app = FastAPI(title="DocuAction AI", version="3.6.0", docs_url="/docs", lifespan=lifespan)

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "service": "docuaction-ai", "version": "3.6.0"}

@app.get("/", tags=["Health"])
async def root():
    return {"app": "DocuAction AI", "version": "3.6.0", "status": "running"}

allowed = os.getenv("ALLOWED_ORIGINS", "https://app.docuaction.io,http://localhost:3000")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in allowed.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"])

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

def _load(path, name):
    try:
        import importlib
        mod = importlib.import_module(path)
        app.include_router(mod.router)
        _loaded.append(name)
    except Exception as e:
        _failed.append(f"{name}:{e}")

# Audio route FIRST — overrides old /api/transcribe in routes.py
_load("app.api.audio_routes", "audio")
_load("app.api.routes", "api")
_load("app.api.auth_endpoints", "auth")
_load("app.api.password_reset", "pwreset")
_load("app.api.compliance", "compliance")
_load("app.api.security", "security")
_load("app.api.admin", "admin")
_load("app.api.export", "export")
_load("app.api.templates", "templates")
_load("app.api.plans", "plans")
