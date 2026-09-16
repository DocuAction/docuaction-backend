"""
Standardized Error Response Handler
ALL errors return consistent JSON format. NEVER expose stack traces.
Format: {"error": "message", "code": "ERROR_CODE", "request_id": "uuid"}

The `request_id` is the one `RequestContextMiddleware` bound for this request
(and echoed in the `X-Request-ID` response header), so the value a user quotes
from an error body is the value that appears in the JSON log lines. Outside a
request context - or before the middleware ran - a fresh id is minted, exactly
as before the 2026-09-17 remediation.
"""
import uuid
import logging
import traceback
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.client_ip import get_client_ip
from app.core import request_context

logger = logging.getLogger("docuaction.errors")


def _resolve_request_id(request_id: Optional[str], request=None) -> str:
    if request_id:
        return request_id
    try:
        bound = request_context.get("request_id")
    except Exception:  # noqa: BLE001 - never let correlation break an error body
        bound = None
    if not bound and request is not None:
        # The generic 500 handler runs outside RequestContextMiddleware; the
        # middleware left the accepted id on request.state for exactly this.
        bound = getattr(getattr(request, "state", None), "request_id", None)
    return str(bound) if bound else str(uuid.uuid4())


def create_error_response(status_code: int, error: str, code: str,
                          request_id: str = None,
                          extra: Optional[Dict[str, Any]] = None,
                          headers: Optional[Dict[str, str]] = None):
    """Create standardized error JSON response.

    `extra` adds structured, non-sensitive fields beside the three standard
    ones (e.g. `required_role` / `current_role` on a 403, `candidates` on an
    ambiguous-lookup 409). It can never override the standard keys.
    """
    content: Dict[str, Any] = {}
    if extra:
        content.update({k: v for k, v in extra.items()
                        if k not in ("error", "code", "request_id")})
    resolved = _resolve_request_id(request_id)
    content.update({
        "error": error,
        "code": code,
        "request_id": resolved,
    })
    # The header always agrees with the body, including on a 500 that never
    # passes back through the middleware that normally echoes it.
    out_headers = dict(headers or {})
    out_headers.setdefault("X-Request-ID", resolved)
    return JSONResponse(status_code=status_code, content=content, headers=out_headers)


# Standard error codes
ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
}


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    Global error handler middleware.
    Catches all unhandled exceptions and returns standardized JSON.
    NEVER exposes internal stack traces to the client.
    """
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())

        try:
            response = await call_next(request)

            # Add request ID to all responses
            response.headers["X-Request-ID"] = request_id
            return response

        except Exception as e:
            # Log full stack trace INTERNALLY
            logger.error(
                f"Unhandled error | request_id={request_id} | "
                f"path={request.url.path} | method={request.method} | "
                f"error={str(e)}\n{traceback.format_exc()}"
            )

            # Return safe error to client — NO stack trace
            return create_error_response(
                status_code=500,
                error="An internal error occurred. Please try again or contact support.",
                code="INTERNAL_ERROR",
                request_id=request_id,
            )


def register_exception_handlers(app):
    """Register custom exception handlers on the FastAPI app."""
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        request_id = _resolve_request_id(None)
        code = ERROR_CODES.get(exc.status_code, "ERROR")
        # 5xx details can carry internal specifics (raw exception text, DB errors,
        # filesystem paths). Log the real detail internally but NEVER return it to the
        # client — replace 5xx bodies with a generic message. 4xx details are
        # intentional, user-facing validation messages and are preserved.
        if exc.status_code >= 500:
            logger.error(
                f"5xx | request_id={request_id} | path={request.url.path} | "
                f"status={exc.status_code} | detail={exc.detail}"
            )
            safe_detail = "An internal error occurred. Please try again or contact support."
        else:
            safe_detail = str(exc.detail)
        # NIST AU-2 — capture Failed Authorization (403) events in the audit
        # trail. Centralized here so every 403 (require_role denials, disabled
        # accounts, area-access blocks) is recorded in one place without touching
        # the auth hot paths. Best-effort: a short-lived session, fully wrapped, so
        # an audit failure can NEVER alter or break the error response.
        if exc.status_code == 403:
            try:
                from app.core.database import async_session_maker
                from app.models.database import AuditLog
                from datetime import datetime
                _ip = get_client_ip(request)
                async with async_session_maker() as _db:
                    _db.add(AuditLog(
                        tenant_id="default",
                        action="authorization_denied",
                        resource_type="authz",
                        ip_address=_ip,
                        details={
                            "path": request.url.path,
                            "method": request.method,
                            "reason": str(exc.detail)[:200],
                            "request_id": request_id,
                            "at": datetime.utcnow().isoformat() + "Z",
                        },
                    ))
                    await _db.commit()
            except Exception:
                logger.debug(f"403 audit write skipped | request_id={request_id}")
        # `require_role` attaches the two facts of a role refusal to the
        # exception (app/core/security.py); surface them structurally so a
        # client can act on them without parsing the message. Role NAMES only.
        extra = None
        required_role = getattr(exc, "required_role", None)
        if exc.status_code == 403 and required_role:
            extra = {"required_role": str(required_role),
                     "current_role": str(getattr(exc, "current_role", None) or "unknown")}
        # Headers the raiser attached (WWW-Authenticate on a 401, Deprecation /
        # Sunset / Link on the deprecated upload route) are part of the answer.
        return create_error_response(
            status_code=exc.status_code,
            error=safe_detail,
            code=code,
            request_id=request_id,
            extra=extra,
            headers=getattr(exc, "headers", None) or None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = _resolve_request_id(None)
        # Simplify validation errors — don't expose internal field paths
        errors = []
        for err in exc.errors():
            field = " → ".join(str(loc) for loc in err.get("loc", []))
            errors.append(f"{field}: {err.get('msg', 'invalid')}")
        return create_error_response(
            status_code=422,
            error="; ".join(errors),
            code="VALIDATION_ERROR",
            request_id=request_id,
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        request_id = _resolve_request_id(None, request)
        logger.error("Unhandled: request_id=%s error_class=%s", request_id,
                     type(exc).__name__, exc_info=True)
        return create_error_response(
            status_code=500,
            error="An internal error occurred. Please try again or contact support.",
            code="INTERNAL_ERROR",
            request_id=request_id,
        )
