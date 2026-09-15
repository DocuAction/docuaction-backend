"""Deployment program profile and server-side module gate.

WHY THIS EXISTS

DocuAction is one codebase carrying three product domains — the Core platform,
the TEFCA ARC federal module and the GovCon / business-operations module. A
federal TEFCA deployment must not expose GovCon. Hiding navigation is not a
boundary: a caller who knows an API URL can still reach the endpoint. The only
boundary that counts is the one the server enforces, so this module answers one
question for every request before any router runs:

    "Does the module that owns this path exist in THIS deployment?"

When it does not, the request is answered 404 with the platform's standard
NOT_FOUND body. 404 (not 403) is deliberate: a module that is absent from a
deployment should not be discoverable from the deployment (concealment
semantics, see docs/architecture/FEDERAL_DEPLOYMENT_ISOLATION_ASSESSMENT.md
section "HTTP semantics"). Role authorization inside an enabled module is
unchanged and still answers 401 / 403 exactly as before.

WHAT IT DOES NOT DO

- It does not change role definitions, authentication or RBAC.
- It does not delete GovCon code or data.
- It does not gate anything unless a program profile is selected.

CONFIGURATION (environment)

    DOCUACTION_PROGRAM             ALL (default) | TEFCA_ARC
    DOCUACTION_MODULES_DISABLED    comma-separated module ids disabled in addition
    DOCUACTION_MODULES_ENABLED     comma-separated module ids re-enabled inside the profile

The default profile ALL keeps every module enabled — identical to the behaviour
before this file existed — so the shared team-QA deployment is unaffected until
an operator selects a profile. An unrecognised DOCUACTION_PROGRAM value fails
closed to the most restrictive profile (TEFCA_ARC) and logs an error, because a
typo in a federal deployment must never widen exposure.

Paths that map to no module are Core (authentication, users, documents, admin,
platform configuration) and are always served.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Dict, FrozenSet, Iterable, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("docuaction.modules")

# ── Module registry: module id -> (domain, path prefixes it owns) ───────────
# Prefixes are matched on path-segment boundaries ("/api/ats" matches
# "/api/ats" and "/api/ats/jobs" but not "/api/atsx"). The GovCon routers in
# app/routers declare prefixes without "/api" (e.g. "/ats"); both spellings are
# listed so the gate holds whichever way those routers are ever mounted.
MODULE_REGISTRY: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "tefca_arc": ("TEFCA", ("/api/tefca", "/api/reports", "/api/learning", "/api/v1/usps")),
    "govcon": ("GOVCON", tuple(
        p for base in (
            "ats", "rfq", "rfqs", "quotes", "deals", "deal-registrations", "deal-tracker",
            "invoices", "finance", "opportunities", "proposal-library", "projects", "staffing",
            "suppliers", "customers", "products", "pricing", "company-profile", "agency-contacts",
            "support", "bom", "ai",
        ) for p in (f"/api/{base}", f"/{base}")
    ) + (
        # app/routers/export.py and app/routers/intel.py declare bare "/export" and
        # "/intel". Their "/api/..." spellings are OWNED BY CORE (app/api/export.py
        # document export, app/api/cross_intel_routes.py meeting intelligence) and
        # must not be gated as GovCon, so only the bare forms are listed here.
        "/export", "/intel",
    )),
    "healthcare_claims": ("CORE_OPTIONAL", ("/api/healthcare",)),
    "case_management": ("CORE_OPTIONAL", ("/api/v1/case-management",)),
    "bulletin_intelligence": ("CORE_OPTIONAL", ("/api/v1/bulletin",)),
    "migration_intelligence": ("CORE_OPTIONAL", ("/api/migration",)),
    "meeting_intelligence": ("CORE_OPTIONAL", ("/api/meetings", "/api/intel", "/api/transcribe")),
    "document_automation": ("CORE_OPTIONAL", (
        "/api/automations", "/api/compare-documents", "/api/comparison-modes",
        "/api/document-memory", "/api/extract-structured", "/api/extraction-templates",
    )),
}

# ── Program profiles: which modules a deployment serves ─────────────────────
PROGRAM_PROFILES: Dict[str, FrozenSet[str]] = {
    # Everything: the pre-existing behaviour and the shared team-QA deployment.
    "ALL": frozenset(MODULE_REGISTRY),
    # Federal TEFCA ARC deployment: Core (implicit) + the TEFCA module, which
    # carries Reporting, Audit and the Learning Center. Nothing else.
    "TEFCA_ARC": frozenset({"tefca_arc"}),
}
DEFAULT_PROGRAM = "ALL"
FAIL_CLOSED_PROGRAM = "TEFCA_ARC"


def _csv(value: Optional[str]) -> FrozenSet[str]:
    return frozenset(v.strip() for v in (value or "").split(",") if v.strip())


def _resolve_program(raw: Optional[str]) -> str:
    name = (raw or DEFAULT_PROGRAM).strip().upper().replace("-", "_")
    if name in PROGRAM_PROFILES:
        return name
    logger.error(
        "DOCUACTION_PROGRAM=%r is not a known profile (%s); failing closed to %s",
        raw, ", ".join(sorted(PROGRAM_PROFILES)), FAIL_CLOSED_PROGRAM,
    )
    return FAIL_CLOSED_PROGRAM


@lru_cache(maxsize=1)
def deployment_profile() -> Tuple[str, FrozenSet[str]]:
    """(program name, enabled module ids) for this process. Cached per process;
    tests call ``reset_profile_cache()`` after changing the environment."""
    program = _resolve_program(os.getenv("DOCUACTION_PROGRAM"))
    enabled = set(PROGRAM_PROFILES[program])
    enabled -= _csv(os.getenv("DOCUACTION_MODULES_DISABLED"))
    for extra in _csv(os.getenv("DOCUACTION_MODULES_ENABLED")):
        if extra in MODULE_REGISTRY:
            enabled.add(extra)
        else:
            logger.warning("DOCUACTION_MODULES_ENABLED names unknown module %r (ignored)", extra)
    return program, frozenset(enabled)


def reset_profile_cache() -> None:
    deployment_profile.cache_clear()


def module_for_path(path: str) -> Optional[str]:
    """The module id owning ``path``, or None for Core (always served)."""
    for module_id, (_domain, prefixes) in MODULE_REGISTRY.items():
        for prefix in prefixes:
            if path == prefix or path.startswith(prefix + "/"):
                return module_id
    return None


def module_enabled(module_id: Optional[str]) -> bool:
    if module_id is None:
        return True
    _program, enabled = deployment_profile()
    return module_id in enabled


def disabled_modules() -> FrozenSet[str]:
    _program, enabled = deployment_profile()
    return frozenset(m for m in MODULE_REGISTRY if m not in enabled)


def profile_summary() -> Dict[str, object]:
    """Public-safe description of the deployment profile: module ids only — no
    hosts, no keys, no topology."""
    program, enabled = deployment_profile()
    return {
        "program": program,
        "enabled_modules": sorted(enabled),
        "disabled_modules": sorted(disabled_modules()),
    }


class ModuleGateMiddleware(BaseHTTPMiddleware):
    """Answer 404 NOT_FOUND for any path owned by a module that is disabled in
    this deployment. Runs before routing, so a disabled module's endpoints are
    indistinguishable from endpoints that were never mounted."""

    async def dispatch(self, request: Request, call_next):
        module_id = module_for_path(request.url.path)
        if module_id is not None and not module_enabled(module_id):
            from app.core.error_handler import create_error_response
            logger.info("module gate: %s %s -> 404 (module %s disabled in profile %s)",
                        request.method, request.url.path, module_id, deployment_profile()[0])
            return create_error_response(status_code=404, error="Not Found", code="NOT_FOUND")
        return await call_next(request)
