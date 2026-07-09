"""
Request correlation context (NIST 800-53 AU-3 / AU-10 — content of audit records
& non-repudiation; SI-4 — monitoring).

A single, dependency-free source of truth for the identifiers that tie a client
request to its audit records and logs:
  • request_id      — unique per HTTP request (generated fresh each hop)
  • correlation_id  — stable across a call chain (honors an inbound
                      X-Correlation-ID so upstream gateways / Microsoft Sentinel
                      can stitch traces); defaults to request_id
  • session_id      — the authenticated token's jti (set by the auth layer)
  • request_start   — perf counter at ingress, for execution-duration metrics

Implemented as a PURE ASGI middleware (not BaseHTTPMiddleware) so the contextvars
set here are reliably visible to the route handler and to audit logging, and so
response headers can be added without buffering the body.

This module adds NO behavior to request/response semantics beyond two response
headers (X-Request-ID, X-Correlation-ID). It is fully backward compatible and is
the seam through which a future FedRAMP/Sentinel/OpenTelemetry exporter can read
correlation state without touching business code.
"""
import time
import uuid
import contextvars
from typing import Optional

from starlette.datastructures import MutableHeaders

request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("correlation_id", default=None)
session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("session_id", default=None)
request_start_var: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar("request_start", default=None)


def new_id() -> str:
    return uuid.uuid4().hex


def get_request_id() -> Optional[str]:
    return request_id_var.get()


def get_correlation_id() -> Optional[str]:
    return correlation_id_var.get()


def get_session_id() -> Optional[str]:
    return session_id_var.get()


def set_session_id(value: Optional[str]) -> None:
    """Called by the auth layer once a token is decoded (session_id = token jti)."""
    if value:
        session_id_var.set(str(value))


def get_duration_ms() -> Optional[float]:
    start = request_start_var.get()
    if start is None:
        return None
    return round((time.perf_counter() - start) * 1000.0, 2)


def audit_context() -> dict:
    """Correlation fields for inclusion in an audit record's `details` JSON.
    Only non-null fields are returned, so existing audit payloads are unchanged
    when the context is absent (e.g. background jobs)."""
    ctx = {
        "request_id": get_request_id(),
        "correlation_id": get_correlation_id(),
        "session_id": get_session_id(),
        "duration_ms": get_duration_ms(),
    }
    return {k: v for k, v in ctx.items() if v is not None}


def _inbound(headers, name: bytes) -> Optional[str]:
    for k, v in headers:
        if k == name:
            try:
                return v.decode("latin-1")
            except Exception:
                return None
    return None


class RequestContextMiddleware:
    """Pure-ASGI middleware: stamps request/correlation IDs and start time, and
    echoes X-Request-ID / X-Correlation-ID on the response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        raw_headers = scope.get("headers") or []
        rid = new_id()
        cid = _inbound(raw_headers, b"x-correlation-id") or rid

        request_id_var.set(rid)
        correlation_id_var.set(cid)
        session_id_var.set(None)
        request_start_var.set(time.perf_counter())

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = rid
                headers["X-Correlation-ID"] = cid
            await send(message)

        await self.app(scope, receive, send_wrapper)
