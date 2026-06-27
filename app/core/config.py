"""Application settings — reads from environment variables or .env file.

SECURITY (HHSAR 352.204-71 / NIST 800-53 IA-5, SC-12):
SECRET_KEY and DATABASE_URL have NO defaults. If either is unset the application
fails to start immediately with a clear error — there is no insecure fallback
that could silently sign JWTs with a public key or point at a throwaway database.
"""
from pydantic_settings import BaseSettings
from pydantic import ValidationError


class Settings(BaseSettings):
    # ── REQUIRED — no defaults, fail-fast on boot if unset ───────────────────
    DATABASE_URL: str            # e.g. postgresql+asyncpg://user:pass@host:5432/db
    SECRET_KEY: str              # JWT signing key — 64+ random chars in production

    # ── Deployment environment. Defaults to "production" so that development-only
    #    surfaces (e.g. the TEFCA demo router) are NEVER exposed unless explicitly
    #    opted into via ENVIRONMENT=development. ─────────────────────────────────
    ENVIRONMENT: str = "production"

    # ── AI ───────────────────────────────────────────────────────────────────
    AI_PROVIDER: str = "anthropic"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_SONNET_MODEL: str = "claude-sonnet-4-20250514"
    OPENAI_API_KEY: str = ""

    # ── CORS / Trusted hosts (FIX 8 — NIST SC-7) ──────────────────────────────
    # Comma-separated. No wildcard default. Override per environment.
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173,https://app.docuaction.io"
    ALLOWED_HOSTS: str = "api.docuaction.io,api-prod.docuaction.io,healthcheck.railway.app,*.railway.app,*.up.railway.app,localhost,127.0.0.1"

    # ── Storage ───────────────────────────────────────────────────────────────
    STORAGE_PROVIDER: str = "local"
    UPLOAD_DIR: str = "./uploads"
    WHISPER_MODEL: str = "whisper-1"

    # ── Optional integrations ─────────────────────────────────────────────────
    ZOOM_CLIENT_ID: str = ""
    ZOOM_CLIENT_SECRET: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"

    class Config:
        env_file = ".env"
        extra = "allow"

    # ── Parsed list helpers (CSV env var -> list) ─────────────────────────────
    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def trusted_hosts(self) -> list[str]:
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in ("development", "dev", "local")


try:
    settings = Settings()
except ValidationError as e:
    missing = [str(err["loc"][0]) for err in e.errors() if err.get("type") == "missing"]
    raise RuntimeError(
        "FATAL: required environment variable(s) not set: "
        f"{missing or 'see error below'}. "
        "SECRET_KEY and DATABASE_URL must be provided explicitly — there are no "
        "insecure defaults. Set them in the environment (or .env) before starting "
        "the application.\n"
        f"Underlying validation error: {e}"
    ) from e
