"""
Request, job and build correlation context.

One place that answers "which request, which job, which delivery, which build
wrote this line". Values live in context variables so that every logger call
made inside a request or inside a delivery job can carry them without threading
them through every signature.

    request_id   preserved from an incoming X-Request-ID (validated) or minted
    trace_id     from the ACTIVE OpenTelemetry span when tracing is enabled
                 (app/core/telemetry.py), else from an incoming W3C
                 `traceparent`, when present
    job_id / intake_id / stage / attempt / report_id / actor
                 bound by the delivery runner and the report generator

The middleware also returns `X-Request-ID` on every response, so a user who
sees an error can quote a value that appears in the logs.

Nothing here logs a token, a secret or a record payload: only identifiers.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

try:  # the API package is a hard dependency; guarded so a broken install
    from opentelemetry import trace as _otel_trace  # degrades to traceparent parsing
except Exception:  # pragma: no cover - defensive  # noqa: BLE001
    _otel_trace = None

logger = logging.getLogger("docuaction.request")

REQUEST_ID_HEADER = "X-Request-ID"
TRACEPARENT_HEADER = "traceparent"

#: What an incoming request id may look like. Anything else is replaced, not
#: trusted: a header is caller-controlled input and ends up in log lines.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
#: W3C trace context: version-traceid-parentid-flags
_TRACEPARENT = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")

_FIELDS = ("request_id", "trace_id", "span_id", "job_id", "intake_id", "stage",
           "attempt", "report_id", "actor", "route")

_vars: Dict[str, contextvars.ContextVar] = {
    name: contextvars.ContextVar(f"docuaction_{name}", default=None)
    for name in _FIELDS
}


def get(name: str) -> Optional[Any]:
    var = _vars.get(name)
    return var.get() if var is not None else None


def current() -> Dict[str, Any]:
    """Every bound field that has a value, plus build identity."""
    out = {name: var.get() for name, var in _vars.items() if var.get() is not None}
    out.update(build_identity())
    return out


def correlation_id() -> str:
    """The id to stamp on evidence rows: the request id, else the job id, else new."""
    return str(get("request_id") or get("job_id") or uuid.uuid4())


@contextmanager
def bind(**fields: Any) -> Iterator[None]:
    """Bind context fields for a block; restores the previous values after."""
    tokens = []
    for name, value in fields.items():
        var = _vars.get(name)
        if var is None:
            raise KeyError(f"unknown context field {name!r}; known: {_FIELDS}")
        tokens.append((var, var.set(None if value is None else str(value)
                                    if name != "attempt" else value)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def new_request_id() -> str:
    return str(uuid.uuid4())


def accept_request_id(candidate: Optional[str]) -> str:
    """Preserve a caller's id when it is well-formed; otherwise mint one."""
    if candidate and _SAFE_ID.match(candidate.strip()):
        return candidate.strip()
    return new_request_id()


def parse_traceparent(value: Optional[str]):
    """(trace_id, span_id) from a W3C traceparent header, or (None, None)."""
    if not value:
        return None, None
    m = _TRACEPARENT.match(value.strip().lower())
    if not m:
        return None, None
    trace_id, span_id = m.group(2), m.group(3)
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None, None
    return trace_id, span_id


def current_trace_ids():
    """(trace_id, span_id) of the ACTIVE OpenTelemetry span as W3C hex strings,
    or (None, None) when there is no recording/valid span (telemetry disabled,
    or the route is excluded from tracing). Never raises."""
    if _otel_trace is None:
        return None, None
    try:
        ctx = _otel_trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return None, None
        return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:  # noqa: BLE001 - correlation is best-effort
        return None, None


def resolve_trace_ids(traceparent: Optional[str]):
    """The active span's ids when tracing is on, else the parsed header."""
    trace_id, span_id = current_trace_ids()
    if trace_id is None:
        trace_id, span_id = parse_traceparent(traceparent)
    return trace_id, span_id


# ── build identity ───────────────────────────────────────────────────────────

APP_VERSION = "6.0.0"
SERVICE_NAME = "docuaction-backend"


def build_identity() -> Dict[str, str]:
    """Commit and build time baked into the image by the release workflow.

    `unknown` means the image was not built by the workflow (a manual build)
    and cannot be attributed to a commit. It is reported, never guessed.
    """
    return {
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
        "build_time": os.environ.get("BUILD_TIME", "unknown"),
        "version": APP_VERSION,
        "environment": os.environ.get("ENVIRONMENT", "unknown"),
    }


def build_sha() -> str:
    """Short form for evidence rows (40 chars max in the schema)."""
    return build_identity()["git_sha"][:40]


# ── middleware ───────────────────────────────────────────────────────────────

class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind request/trace ids for the request, echo X-Request-ID, log one line.

    The access line carries method, route template, status and duration only.
    Query strings, bodies and headers are never logged: they may hold
    identifiers or tokens.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = accept_request_id(request.headers.get(REQUEST_ID_HEADER))
        # When telemetry is enabled the OpenTelemetry server span is already
        # open (its middleware is outside this one) and its ids are the ones
        # App Insights indexes as operation_Id; the header is the fallback.
        trace_id, span_id = resolve_trace_ids(request.headers.get(TRACEPARENT_HEADER))
        route = request.url.path
        started = time.perf_counter()
        status_code = 500
        with bind(request_id=request_id, trace_id=trace_id, span_id=span_id,
                  route=route):
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers[REQUEST_ID_HEADER] = request_id
                return response
            finally:
                duration_ms = int((time.perf_counter() - started) * 1000)
                # Health probes are noise at INFO; everything else is one line.
                level = logging.DEBUG if route in ("/health", "/") else logging.INFO
                logger.log(level, "request", extra={
                    "http_method": request.method, "route": route,
                    "status_code": status_code, "duration_ms": duration_ms,
                })
