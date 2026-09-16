"""
OpenTelemetry / Azure Monitor export (2026-09-17 observability increment).

WHAT THIS IS
────────────
Distributed tracing for the request path (FastAPI server spans), the database
(asyncpg client spans) and the delivery pipeline (`rce.delivery_job` and
`rce.stage.<STAGE>` spans from `delivery_runner`), exported to Application
Insights through the Azure Monitor distro. The JSON log lines keep working
exactly as before; when tracing is on they carry the REAL trace id of the
active span (see `request_context.current_trace_ids`), so a log line, a
`requests` row and a `dependencies` row join on `operation_Id`.

WHEN IT IS ON
─────────────
Only when BOTH are true:
    OTEL_ENABLED=true
    APPLICATIONINSIGHTS_CONNECTION_STRING is set (its value is never logged,
    never returned by any health surface; only its presence is reported)
Otherwise nothing is configured: `opentelemetry.trace.get_tracer` hands out
no-op spans and every `telemetry.span(...)` block is a plain `with` block.

SAMPLING
────────
Parent-based ratio (OTEL_TRACES_SAMPLER_ARG, default 0.2) with two
exceptions that are ALWAYS kept: a span whose name or initial attributes mark
an error, and a span carrying `docuaction.always_sample=true` (the delivery
job root span sets it, so a delivery's stage spans are the evidence trail, not
a 20 % sample of it). A child follows its parent's decision, so a kept root
keeps its whole trace and a dropped root drops it, except an error child which
is kept on its own.

REDACTION
─────────
`RedactingSpanProcessor` runs before the exporter on every span: attribute
values whose key matches the SAME credential regex as `logging_config` become
[REDACTED]; `http.request.header.*` / `http.response.header.*` are removed;
query strings are stripped from `http.url` / `url.full` / `http.target` and
`url.query` is removed; `db.statement.parameters` is removed and the statement
itself keeps its shape without literals. String values are passed through
`logging_config.redact_text` (bearer tokens, key=value credentials). Span
attributes carry identifiers only; never a payload, a record or PII.

SAFE FAILURE
────────────
Any exception while configuring is logged ONCE at WARNING with the traceback
and the application starts without tracing. `configure_telemetry` never
raises. `telemetry_status()` says what happened, and `/api/admin/health`
reports it under `telemetry`.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Mapping, MutableMapping, Optional, Sequence

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.sampling import (
    Decision,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import Link, SpanKind, TraceState, get_current_span
from opentelemetry.util.types import Attributes

from app.core import request_context
from app.core.logging_config import _SENSITIVE_KEY, redact, redact_text, safe_exception_text

try:  # the logs signal is stable enough to import, but never required
    from opentelemetry.sdk._logs import LogRecordProcessor as _LogRecordProcessor
except Exception:  # noqa: BLE001 - pragma: no cover
    _LogRecordProcessor = object  # type: ignore[assignment,misc]

logger = logging.getLogger("docuaction.telemetry")

TRACER_NAME = "docuaction"
DEFAULT_SAMPLE_RATIO = 0.2
#: Health probes are polled every few seconds by the platform and the deploy
#: gate; they are noise as server spans. Anchored so `/health` does not also
#: swallow a route that merely ends in "health". `ExcludeList` searches the
#: full URL (scheme://host/path), hence the optional authority prefix.
EXCLUDED_URLS = (
    r"^(https?://[^/]+)?/health$",
    r"^(https?://[^/]+)?/api/admin/health$",
)
#: Attribute a span sets to be kept regardless of the ratio (identifiers only).
ALWAYS_SAMPLE_ATTRIBUTE = "docuaction.always_sample"

_status: Dict[str, Any] = {
    "enabled": False,
    "reason": "configure_telemetry not called",
    "sampler": None,
    "exporter": "none",
}
_provider: Optional[trace.TracerProvider] = None


# ── status ───────────────────────────────────────────────────────────────────

def telemetry_status() -> Dict[str, Any]:
    """What `configure_telemetry` decided. Safe for health surfaces: it names
    the sampler and the exporter, never the connection string."""
    return dict(_status)


def _set_status(enabled: bool, reason: str, sampler: Optional[str],
                exporter: str) -> Dict[str, Any]:
    _status.update(enabled=enabled, reason=reason, sampler=sampler,
                   exporter=exporter)
    return telemetry_status()


# ── sampling ─────────────────────────────────────────────────────────────────

_ERROR_NAME = re.compile(r"(error|fail|exception)", re.IGNORECASE)


def _marks_error(name: str, attributes: Attributes) -> bool:
    """True when a span, at creation, already says it is about an error."""
    if name and _ERROR_NAME.search(name):
        return True
    if not attributes:
        return False
    if attributes.get("error") in (True, "true", "True", 1):
        return True
    if attributes.get("exception.type") or attributes.get("exception.message"):
        return True
    if str(attributes.get("otel.status_code", "")).upper() == "ERROR":
        return True
    for key in ("http.status_code", "http.response.status_code"):
        try:
            if int(attributes.get(key, 0)) >= 500:
                return True
        except (TypeError, ValueError):
            pass
    return False


class ErrorKeepingSampler(Sampler):
    """Parent-based ratio sampler that never drops a span that is an error
    WHEN IT STARTS.

    Sampling is decided at span start from the initial attributes. A server
    span learns its HTTP status only at its end, so a request that fails with
    a 5xx is kept at the ratio, not always (independent review M2, 2026-09-16).
    What IS always kept: spans created with an error marker, the delivery-job
    tree (`docuaction.always_sample`), and every child of a sampled parent.
    Operators who need every failed request keep the ratio at 1.0 - the DEV
    volume makes that cheap - and rely on the redacted error LOG, which is
    exported for every 5xx regardless of the trace decision.

    Decision order for every span:
      1. name/attributes mark an error, or `docuaction.always_sample` is set
         -> RECORD_AND_SAMPLE (regardless of parent or ratio);
      2. a valid parent exists -> follow the parent's sampled flag
         (remote and local parents alike);
      3. root span -> TraceIdRatioBased(ratio).
    """

    def __init__(self, ratio: float = DEFAULT_SAMPLE_RATIO):
        ratio = _clamp_ratio(ratio)
        self.ratio = ratio
        self._root = TraceIdRatioBased(ratio)

    def should_sample(
        self,
        parent_context: Optional[Context],
        trace_id: int,
        name: str,
        kind: Optional[SpanKind] = None,
        attributes: Attributes = None,
        links: Optional[Sequence[Link]] = None,
        trace_state: Optional[TraceState] = None,
    ) -> SamplingResult:
        parent = get_current_span(parent_context).get_span_context()
        parent_state = parent.trace_state if parent.is_valid else None
        if _marks_error(name, attributes) or (
                attributes and attributes.get(ALWAYS_SAMPLE_ATTRIBUTE) in (True, "true", 1)):
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes, parent_state)
        if parent.is_valid:
            decision = (Decision.RECORD_AND_SAMPLE if parent.trace_flags.sampled
                        else Decision.DROP)
            return SamplingResult(decision,
                                  attributes if decision is Decision.RECORD_AND_SAMPLE else None,
                                  parent_state)
        return self._root.should_sample(parent_context, trace_id, name, kind,
                                        attributes, links, trace_state)

    def get_description(self) -> str:
        return f"ErrorKeepingParentBasedTraceIdRatio{{{self.ratio}}}"


def _clamp_ratio(value: Any) -> float:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SAMPLE_RATIO
    if ratio < 0.0 or ratio > 1.0 or ratio != ratio:  # NaN guard
        return DEFAULT_SAMPLE_RATIO
    return ratio


def sample_ratio_from_env() -> float:
    return _clamp_ratio(os.environ.get("OTEL_TRACES_SAMPLER_ARG", DEFAULT_SAMPLE_RATIO))


# ── redaction ────────────────────────────────────────────────────────────────

_HEADER_PREFIXES = ("http.request.header.", "http.response.header.")
_URL_WITH_QUERY = ("http.url", "url.full", "http.target", "url.path")
_DROP_KEYS = frozenset({"url.query", "db.statement.parameters", "db.query.parameter",
                        "http.request.body", "http.response.body"})
_STATEMENT_KEYS = ("db.statement", "db.query.text")
_SQL_STRING = re.compile(r"'(?:[^']|'')*'")
_SQL_NUMBER = re.compile(r"(?<![A-Za-z_$\d])\d+(?:\.\d+)?(?![A-Za-z_\d])")
_SQL_DOLLAR_QUOTE = re.compile(r"\$[A-Za-z_]*\$.*?\$[A-Za-z_]*\$", re.DOTALL)


def strip_sql_literals(statement: str) -> str:
    """Keep the statement's shape, drop its values. `$1` placeholders survive."""
    text = _SQL_DOLLAR_QUOTE.sub("?", statement)
    text = _SQL_STRING.sub("?", text)
    return _SQL_NUMBER.sub("?", text)


def redact_attributes(attrs: MutableMapping[str, Any]) -> None:
    """Redact a span's attribute mapping IN PLACE (see module docstring)."""
    for key in list(attrs.keys()):
        value = attrs[key]
        if key.startswith(_HEADER_PREFIXES) or key in _DROP_KEYS:
            del attrs[key]
            continue
        if _SENSITIVE_KEY.search(key):
            attrs[key] = "[REDACTED]"
            continue
        if key in _STATEMENT_KEYS and isinstance(value, str):
            attrs[key] = strip_sql_literals(value)
            continue
        if key in _URL_WITH_QUERY and isinstance(value, str):
            value = value.split("?", 1)[0].split("#", 1)[0]
            attrs[key] = redact_text(value)
            continue
        if isinstance(value, str):
            redacted = redact_text(value)
            if redacted != value:
                attrs[key] = redacted


class RedactingSpanProcessor(SpanProcessor):
    """Runs before the exporter; scrubs attributes when the span starts and
    again when it ends (attributes set during the span, such as the response
    status or a captured header, are only visible at the end).

    The SDK freezes a span's attributes in `end()` before calling `on_end`; the
    freeze is lifted for the duration of the scrub and restored. This touches a
    private flag of `BoundedAttributes`, which is why it is wrapped in a broad
    except: a scrub that cannot run must not break the request.
    """

    def on_start(self, span, parent_context: Optional[Context] = None) -> None:
        self._scrub(span)

    def on_end(self, span) -> None:
        self._scrub(span)

    def shutdown(self) -> None:  # noqa: D401 - SpanProcessor protocol
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    @staticmethod
    def _scrub(span) -> None:
        attrs = getattr(span, "_attributes", None)
        if not isinstance(attrs, MutableMapping):
            return
        was_immutable = getattr(attrs, "_immutable", None)
        try:
            if was_immutable:
                attrs._immutable = False  # type: ignore[attr-defined]
            redact_attributes(attrs)
        except Exception:  # noqa: BLE001 - never let a scrub break a span
            logger.debug("span attribute redaction skipped", exc_info=True)
        finally:
            if was_immutable is not None:
                attrs._immutable = was_immutable  # type: ignore[attr-defined]


# ── resource ─────────────────────────────────────────────────────────────────

def service_name() -> str:
    return os.environ.get("OTEL_SERVICE_NAME") or request_context.SERVICE_NAME


def build_resource() -> Resource:
    identity = request_context.build_identity()
    return Resource.create({
        "service.name": service_name(),
        "service.version": identity["version"],
        "deployment.environment": identity["environment"],
        "git.sha": identity["git_sha"],
        "build.time": identity["build_time"],
    })


# ── tracer / spans ───────────────────────────────────────────────────────────

def use_tracer_provider(provider: Optional[trace.TracerProvider]) -> None:
    """Route `telemetry.span` through this provider (None -> the global one).
    Used after configuration and by tests with an in-memory exporter."""
    global _provider
    _provider = provider


def get_tracer() -> trace.Tracer:
    """The app's tracer. With telemetry disabled this is the API's no-op
    tracer: every span it starts is a NonRecordingSpan and costs nothing."""
    if _provider is not None:
        return _provider.get_tracer(TRACER_NAME, request_context.APP_VERSION)
    return trace.get_tracer(TRACER_NAME, request_context.APP_VERSION)


_ATTR_TYPES = (str, bool, int, float)


def safe_attributes(attrs: Mapping[str, Any]) -> Dict[str, Any]:
    """Identifiers only: drop None, drop credential-shaped keys, stringify
    anything that is not a primitive (a UUID, an Enum), cap length."""
    out: Dict[str, Any] = {}
    for key, value in attrs.items():
        if value is None or _SENSITIVE_KEY.search(str(key)):
            continue
        if not isinstance(value, _ATTR_TYPES):
            value = str(value)
        if isinstance(value, str):
            value = redact_text(value)[:256]
        out[str(key)] = value
    return out


def record_redacted_exception(current: trace.Span, exc: BaseException) -> None:
    """An `exception` event carrying the class and a controlled message only.

    The SDK's default `record_exception=True` exports `exception.message` and
    the full `exception.stacktrace` verbatim; driver messages and stack frames
    carry SQL parameters, connection strings and delivered values (review
    finding M3, 2026-09-16). No stack trace is exported; it stays in the log.
    """
    try:
        if not current.is_recording():
            return
        current.add_event("exception", attributes={
            "exception.type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "exception.message": safe_exception_text(exc, 512),
            "exception.escaped": "True",
        })
    except Exception:  # noqa: BLE001 - never let telemetry break the caller
        logger.debug("exception event not recorded", exc_info=True)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """`with telemetry.span("rce.stage.QUALITY", job_id=..., stage=...):`

    A no-op when telemetry is disabled. Exceptions propagate unchanged (the
    span is marked ERROR and carries a REDACTED exception event first).
    """
    with get_tracer().start_as_current_span(
            name, attributes=safe_attributes(attributes),
            record_exception=False, set_status_on_exception=True) as current:
        try:
            yield current
        except BaseException as exc:
            record_redacted_exception(current, exc)
            raise


class RedactingLogRecordProcessor(_LogRecordProcessor):  # type: ignore[misc]
    """Runs before the Azure Monitor log exporter.

    The distro attaches an OpenTelemetry `LoggingHandler` to the `docuaction`
    logger and exports the raw LogRecord - message, `extra` fields and the
    exception - WITHOUT the JSON formatter's redaction that protects stdout
    (review finding M4, 2026-09-16). This processor applies the same redaction
    to the exported record, and withholds stack traces entirely: they stay in
    the container log under the correlation id.
    """

    def on_emit(self, log_record) -> None:  # SDK >= 1.35 signature
        self._scrub(getattr(log_record, "log_record", log_record))

    def emit(self, log_data) -> None:  # older SDK signature
        self._scrub(getattr(log_data, "log_record", log_data))

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    @staticmethod
    def _scrub(record) -> None:
        try:
            body = getattr(record, "body", None)
            if isinstance(body, str):
                record.body = redact_text(body)
            elif isinstance(body, (dict, list)):
                record.body = redact(body)
            attrs = getattr(record, "attributes", None)
            if isinstance(attrs, MutableMapping):
                was_immutable = getattr(attrs, "_immutable", None)
                if was_immutable:
                    attrs._immutable = False  # type: ignore[attr-defined]
                try:
                    redact_attributes(attrs)
                    if "exception.stacktrace" in attrs:
                        attrs["exception.stacktrace"] = "[withheld; see container log]"
                    if "exception.message" in attrs:
                        attrs["exception.message"] = redact_text(
                            str(attrs["exception.message"]))[:512]
                finally:
                    if was_immutable is not None:
                        attrs._immutable = was_immutable  # type: ignore[attr-defined]
            elif isinstance(attrs, dict):
                redact_attributes(attrs)
        except Exception:  # noqa: BLE001 - never let a scrub break logging
            logger.debug("log record redaction skipped", exc_info=True)


# ── configuration ────────────────────────────────────────────────────────────

def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def configure_telemetry(app) -> Dict[str, Any]:
    """Configure Azure Monitor export for `app`, or explain why not.

    Returns {"enabled", "reason", "sampler", "exporter"}; the same dict is
    available later from `telemetry_status()`. Never raises.
    """
    if not _truthy(os.environ.get("OTEL_ENABLED")):
        return _set_status(False, "OTEL_ENABLED is not true", None, "none")
    if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip():
        return _set_status(False, "APPLICATIONINSIGHTS_CONNECTION_STRING is not set",
                           None, "none")

    ratio = sample_ratio_from_env()
    sampler = ErrorKeepingSampler(ratio)
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        # The distro reads the connection string from the environment itself;
        # it is not passed as an argument so it never appears in a repr, a
        # traceback or a log line of ours.
        configure_azure_monitor(
            resource=build_resource(),
            span_processors=[RedactingSpanProcessor()],
            log_record_processors=[RedactingLogRecordProcessor()],
            # FastAPI is instrumented below on THIS app with the excluded urls;
            # the distro's global FastAPI patch would only catch apps created
            # later and would trace the health probes.
            instrumentation_options={"fastapi": {"enabled": False}},
            logger_name="docuaction",
            enable_live_metrics=False,
        )

        provider = trace.get_tracer_provider()
        _install_sampler(provider, sampler)
        use_tracer_provider(provider)

        FastAPIInstrumentor.instrument_app(
            app, excluded_urls=",".join(EXCLUDED_URLS), tracer_provider=provider)
        AsyncPGInstrumentor(capture_parameters=False).instrument(
            tracer_provider=provider)
    except Exception:  # noqa: BLE001 - tracing must never take the app down
        logger.warning("telemetry configuration failed; continuing without "
                       "tracing", exc_info=True)
        use_tracer_provider(None)
        return _set_status(False, "configuration failed (see WARNING log)",
                           None, "none")

    logger.info("telemetry enabled", extra={
        "telemetry_sampler": sampler.get_description(),
        "telemetry_exporter": "azure_monitor",
        "telemetry_service_name": service_name(),
    })
    return _set_status(True, "OTEL_ENABLED and connection string present",
                       sampler.get_description(), "azure_monitor")


def _install_sampler(provider, sampler: Sampler) -> None:
    """Put our sampler on the distro's TracerProvider.

    `configure_azure_monitor` builds the provider itself and only accepts a
    sampler by NAME through OTEL_TRACES_SAMPLER, so the error-keeping sampler
    is installed afterwards. The SDK `Tracer` copies the provider's sampler at
    creation and caches tracers, so any tracer the distro already created is
    updated too. Both are documented SDK attributes on the public classes.
    """
    if not hasattr(provider, "sampler"):
        raise RuntimeError(
            f"tracer provider {type(provider).__name__} has no sampler; refusing "
            "to run with an unknown sampling policy")
    provider.sampler = sampler
    for tracer in list(getattr(provider, "_tracers", {}).values()):
        if hasattr(tracer, "sampler"):
            tracer.sampler = sampler
