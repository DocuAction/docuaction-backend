"""
DocuAction AI Platform — Main Application
v6.0.0 — All modules registered via safe_load
File: app/main.py
"""
import os
import sys
import importlib
import logging
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="DocuAction AI Platform",
    version="6.0.0",
    description="Enterprise Intelligence Platform — Alliance Global Tech, Inc.",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://app.docuaction.io,http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Bulletin Scheduler (start before routes) ─────────────────────────────────
try:
    from app.bulletin_intelligence.scheduler import start_scheduler
    start_scheduler()
    logger.info("✅ Bulletin scheduler started")
except Exception as e:
    logger.warning(f"⚠️ Bulletin scheduler: {e}")


# ── safe_load: modular-monolith pattern ──────────────────────────────────────
_loaded_modules = {}

def safe_load(module_path: str, prefix: str, tag: str = None):
    """
    Import a module's router and register it under /api/{prefix}.
    If the module fails to import, log the error and continue.
    One module failure does not affect others.
    """
    tag = tag or prefix
    try:
        mod = importlib.import_module(module_path)
        router = getattr(mod, "router", None)
        if router:
            app.include_router(router, prefix=f"/api/{prefix}", tags=[tag])
            _loaded_modules[prefix] = "active"
            logger.info(f"✅ Loaded: {module_path} → /api/{prefix}")
        else:
            logger.warning(f"⚠️ No router in {module_path}")
            _loaded_modules[prefix] = "no_router"
    except Exception as e:
        logger.error(f"❌ Failed to load {module_path}: {e}")
        _loaded_modules[prefix] = f"error: {e}"


# ── Health endpoint (always responds 200) ────────────────────────────────────
@app.get("/health")
@app.get("/api/health")
async def health():
    return {
        "status": "healthy",
        "version": "6.0.0",
        "platform": "DocuAction AI",
        "timestamp": datetime.utcnow().isoformat(),
        "modules": _loaded_modules,
    }


# ── Core routes ──────────────────────────────────────────────────────────────
safe_load("app.api.routes", "process", "Core Processing")
safe_load("app.api.routes", "documents", "Documents")
safe_load("app.api.routes", "auth", "Authentication")
safe_load("app.api.routes", "outputs", "Outputs")

# ── Feature modules ──────────────────────────────────────────────────────────
safe_load("app.api.healthcare_claims_routes", "healthcare", "Healthcare Claims")
safe_load("app.api.wow_routes", "wow", "Intelligence Engine")
safe_load("app.api.migration_routes", "migration", "Migration Intelligence")
safe_load("app.api.sla_routes", "sla", "SLA Engine")

# ── Bulletin Intelligence ────────────────────────────────────────────────────
safe_load("app.bulletin_intelligence.routes", "bulletin-intelligence", "Bulletin Intelligence")

# ── TEFCA (if available) ─────────────────────────────────────────────────────
safe_load("app.Tefca.routes", "tefca", "TEFCA Review Protocol")

# ── Case Management (if available) ───────────────────────────────────────────
safe_load("app.case_management.routes", "case-management", "Case Management")


# ── Error handler ────────────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "path": str(request.url)},
    )


# ── Startup log ──────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    active = [k for k, v in _loaded_modules.items() if v == "active"]
    failed = [k for k, v in _loaded_modules.items() if "error" in str(v)]
    logger.info(f"DocuAction AI v6.0.0 started — {len(active)} modules active, {len(failed)} failed")
    if failed:
        logger.warning(f"Failed modules: {failed}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
