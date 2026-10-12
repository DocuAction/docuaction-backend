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
    # Server-local drop directory for operator-placed IQVIA extracts (the
    # `/sources/{source}/stage` route) — the only directory a stage request's
    # client-supplied file_path is allowed to resolve into; see
    # app.core.upload_security.safe_existing_path.
    IQVIA_IMPORT_DIR: str = "./uploads/iqvia-source"
    WHISPER_MODEL: str = "whisper-1"

    # ── Optional integrations ─────────────────────────────────────────────────
    ZOOM_CLIENT_ID: str = ""
    ZOOM_CLIENT_SECRET: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"

    # ── ONC/RCE delivery pipeline ─────────────────────────────────────────────
    # When true, curated records flagged `is_test_record` (BUS-002 name pattern)
    # are EXCLUDED from promotion and accounted as EXCLUDED / EXCLUDED_TEST_RECORD
    # in the disposition ledger. Default false: a real organisation may carry
    # "Test" in its name, and dropping on a substring is the silent loss the
    # pipeline exists to prevent. Excluded rows stay in Area 1 and Area 2.
    RCE_EXCLUDE_TEST_RECORDS: bool = False

    # ── DEV-only governed original-artifact restore (DEF-004 governed restoration) ──
    # A SECOND gate beyond ENVIRONMENT=development: the admin restore endpoint
    # is only REGISTERED (it does not exist in the route table at all, in any
    # environment) when this is true AND is_development is true. Defaults
    # False so the surface is absent by default even in a dev deployment;
    # an operator turns it on, performs one restore, then turns it back off
    # and restarts — the same on/off/restart pattern already used for
    # REPORT_ARTIFACT_BACKEND.
    ENABLE_DEV_RESTORE_ORIGINAL: bool = False

    # IQVIA HCP_AFFIL consumption (advisory candidates only; never a verification
    # source, never confirmed). Default False: the routes do not exist. Also needs
    # ENABLE_IQVIA_SOURCES and an APPROVED snapshot. Not a policy activation.
    ENABLE_IQVIA_AFFILIATION_CONSUMPTION: bool = False

    # Preflight ENFORCEMENT (not the engine itself, which always exists and
    # is always reachable via the admin dry-run route regardless of this
    # flag). Default False: zero behavior change for any official delivery
    # or reference-snapshot write. See
    # docs/review/DELTA-2026-10-04.md and delivery_runner._stage_preflight.
    # New enforcement stays in shadow (off) until explicitly approved.
    ENABLE_PREFLIGHT_ENFORCEMENT: bool = False

    # Controlled rechecks (rce/rechecks.py) re-query real, rate-limited
    # sources. Default False: the routes refuse. Nothing schedules a recheck
    # automatically in either state.
    ENABLE_CONTROLLED_RECHECKS: bool = False

    # Cross-delivery issue history (minimum slice: NPI and partOf/QHIN rules).
    # Both default False: with both off the quality engine writes exactly what
    # it wrote before and the history route answers 404.
    #   ENABLE_RECORD_CHECK_RESULTS  write one rce_record_check_results row per
    #                                record per run (needs the migration).
    #   ENABLE_ISSUE_HISTORY         serve GET .../by-oid/{oid}/issue-history.
    # The two feed lists are comma-separated rce_source_intakes
    # source_metadata->>'feed' tags each role may read. Empty means NOTHING is
    # visible to that role (fail closed); reviewer and above also see the
    # viewer feeds.
    ENABLE_RECORD_CHECK_RESULTS: bool = False
    ENABLE_ISSUE_HISTORY: bool = False
    ISSUE_HISTORY_FEEDS_VIEWER: str = ""
    ISSUE_HISTORY_FEEDS_REVIEWER: str = ""
    # Read-only mapping "<intake uuid>:<FEED>,..." that lets a LEGACY intake with
    # NO feed tag be read as a member of FEED for history purposes only. It never
    # overrides a tag, writes nothing, and the FEED must still be allowed to the
    # caller. Empty (default) = untagged intakes stay in no feed (fail closed).
    ISSUE_HISTORY_INTAKE_FEEDS: str = ""

    # PROPOSED, INACTIVE. Under the active rules an entity is classified B1
    # and marked verified while SAM.gov or CMS-revocation screening was
    # unavailable / never evaluated (only OIG LEIE is required). The gap is
    # always RECORDED on the review record (verification_claim). Turning
    # this on additionally withholds `verified` for such records. It is a
    # policy decision, not a default: with no SAM key configured it would
    # withhold `verified` for every entity.
    ENFORCE_COMPLETE_EXCLUSION_SCREENING: bool = False

    # ── Track A3: QA independence + deadline/notification controls ────────────
    # EVERY flag below defaults OFF; turning one on is a policy decision, not a
    # deployment detail. See docs/A3-qa-independence-and-deadline-controls.md.
    #
    # Closes the "SoD exception is self-attestable" gap: when on, the named
    # grantor must be a real, active, admin-role user other than the QA actor
    # and other than the analyst whose determination is being reviewed.
    ENABLE_SOD_GRANTOR_VERIFICATION: bool = False
    # PROPOSAL (open owner decision O-01, not an approved requirement): the
    # person who generated a report may not record its PM review / ready-for-
    # delivery decision.
    ENABLE_RELEASE_GENERATOR_SEPARATION: bool = False
    # PROPOSAL: READY_FOR_DELIVERY additionally requires an explicit
    # `acknowledge_read=true` from the releaser (recorded in the history).
    ENABLE_RELEASE_READ_ACK: bool = False
    # Writes a durable audit row (machine code) for a refused QA / release act.
    ENABLE_DENIAL_AUDIT: bool = False
    # Exposes the deadline dry-run endpoint (computes, sends nothing).
    ENABLE_DEADLINE_DRY_RUN: bool = False
    # Deadline configuration (JSON). Empty = nothing resolved; every deadline
    # reports UNCONFIGURED. No clock-start or hours-vs-business-day default.
    DEADLINE_CONFIG_JSON: str = ""
    # Notifications are INACTIVE. Sending needs BOTH the flag AND a named,
    # registered transport, and is always suppressed under pytest.
    ENABLE_NOTIFICATIONS: bool = False
    NOTIFICATION_TRANSPORT: str = ""

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
