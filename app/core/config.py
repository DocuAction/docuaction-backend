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

    # ── Registration security (P1 fix) ────────────────────────────────────────
    # When True (default), a self-registered user who verifies their email lands in
    # 'pending_approval' and an administrator must assign a role and activate the
    # account before it can log in. Set REQUIRE_ADMIN_APPROVAL=false to let email
    # verification alone activate the account ("Verified" per the security spec).
    REQUIRE_ADMIN_APPROVAL: bool = True

    # ── Interactive API docs. OFF by default (also implicitly on in development).
    #    Set ENABLE_DOCS=true (or ENABLE_OPENAPI=true) to expose /docs, /redoc, and
    #    /openapi.json in production. ──
    ENABLE_DOCS: bool = False
    # Alias flag (Task 2.6) — either flag being true exposes the OpenAPI surfaces.
    ENABLE_OPENAPI: bool = False

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

# ── Unresolved Azure Key Vault reference guard (SEC-01, NIST IA-5 / SC-12) ──────
#    Secrets reach this app as plain environment variables. On Azure App Service the
#    sensitive ones are Key Vault REFERENCES — app settings of the form
#    "@Microsoft.KeyVault(VaultName=...;SecretName=...)" that the platform resolves
#    with the site's managed identity before the process starts.
#
#    When resolution FAILS (managed identity loses Key Vault Secrets User, vault
#    firewall change, secret renamed/disabled/expired, vault outage), App Service
#    does NOT fail the start — it injects the LITERAL reference string as the value.
#
#    That is silently dangerous for SECRET_KEY, because the literal string is long
#    enough to satisfy the entropy floor below:
#        "@Microsoft.KeyVault(VaultName=docuaction-kv-prod;SecretName=SECRET-KEY)"
#        -> 71 characters, and the floor is 64.
#    So without this guard the app boots and signs every JWT with a value anyone who
#    knows the vault and secret name can reconstruct — i.e. forge admin tokens.
#    Order matters: this check MUST run before the length check for that reason.
#
#    Fail loudly instead. A deploy that cannot reach its secrets must not serve
#    traffic on a predictable signing key.
_KV_REFERENCE_PREFIX = "@Microsoft.KeyVault("


def _assert_resolved(name: str, value: str) -> None:
    if (value or "").strip().startswith(_KV_REFERENCE_PREFIX):
        raise RuntimeError(
            f"FATAL: {name} is an UNRESOLVED Azure Key Vault reference — the platform "
            "passed the literal '@Microsoft.KeyVault(...)' string through instead of "
            "the secret value. The application is refusing to start rather than run "
            f"with {name} set to a publicly derivable constant.\n"
            "Check, in this order: (1) the site's managed identity still holds the "
            "'Key Vault Secrets User' role on the vault; (2) the secret exists, is "
            "enabled, and has not expired; (3) the vault firewall still permits the "
            "site (trusted service or private endpoint); (4) the SecretName in the "
            "app setting matches the vault exactly (it is case-sensitive).\n"
            "Azure reports per-setting status via: az rest --method get --uri "
            "'/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Web/sites/"
            "<site>/config/configreferences/appsettings?api-version=2022-03-01'"
        )


# Required settings: an unresolved reference is a hard startup failure.
_assert_resolved("SECRET_KEY", settings.SECRET_KEY)
_assert_resolved("DATABASE_URL", settings.DATABASE_URL)

# Optional secret-bearing settings: warn rather than fail, so an unrelated
# integration's misconfiguration cannot take the whole application down. The
# feature that consumes the value will fail on its own, and this makes the reason
# obvious in the startup log instead of surfacing as a confusing upstream 401.
for _optional in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    _value = getattr(settings, _optional, "") or ""
    if _value.strip().startswith(_KV_REFERENCE_PREFIX):
        import logging as _logging

        _logging.getLogger("docuaction.config").error(
            "%s is an UNRESOLVED Key Vault reference — features depending on it will "
            "fail. See the SECRET_KEY guidance in app/core/config.py.",
            _optional,
        )

# ── SECRET_KEY minimum entropy (NIST SP 800-131A / IA-5). Policy requires a 64+
#    character high-entropy key; refuse to start on anything weaker rather than sign
#    JWTs with a low-entropy key. ──
_MIN_SECRET_KEY_LEN = 64
if len(settings.SECRET_KEY or "") < _MIN_SECRET_KEY_LEN:
    raise RuntimeError(
        f"FATAL: SECRET_KEY is too weak — it must be at least {_MIN_SECRET_KEY_LEN} "
        "characters of high-entropy random data. Generate one with e.g. "
        "`python -c \"import secrets; print(secrets.token_urlsafe(64))\"` and set it "
        "in the environment before starting the application."
    )
