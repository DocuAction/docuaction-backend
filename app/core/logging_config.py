"""
Structured (JSON) logging with request/job/build correlation and redaction.

Every record carries: timestamp, level, logger, message, and whatever the
request context holds (request_id, trace_id, span_id, job_id, intake_id,
stage, attempt, report_id, route) plus git_sha, environment and version.
Exceptions are rendered with class, safe message and the traceback.

REDACTION. Values whose key looks like a credential (token, secret, password,
authorization, api_key, connection string, cookie) are replaced with
[REDACTED]; bearer tokens and `key=value` credential pairs embedded in free
text are masked too. This is a last line of defence, not permission to log
credentials.

Opt out with DOCUACTION_LOG_FORMAT=plain (the pre-remediation basicConfig).
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
import re
import traceback
from datetime import datetime, timezone
from typing import Any, Dict

from app.core import request_context

_SENSITIVE_KEY = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|connection[_-]?string|"
    r"cookie|set-cookie|client[_-]?secret|(?:^|[_.-])sas(?:[_.-]|$)|signature|"
    # Licensed-source content (IQVIA OneKey HCO/HCP identifiers and payloads)
    # is never written to a log line, whatever key it arrives under.
    r"iqvia|onekey|one[_-]?key|(?:^|[_.-])hcp(?:[_.-]|$)|hcp[_-]?(?:id|name|record)|"
    r"licensed[_-]?(?:payload|record|content))",
    re.IGNORECASE)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
#: `instrumentationkey` / `sharedaccesskey` / `accountkey`: the credential
#: halves of an Application Insights, Service Bus or Storage connection string,
#: which an Azure SDK exception message can echo back verbatim (found by
#: tests/test_telemetry.py, 2026-09-17).
#: `client_secret=`, `access_token=`, `"password": "x"`, `PASSWORD: x` and
#: `key = x` forms are all matched: the key may carry a word prefix joined by
#: `_` or `-`, may be quoted, and the separator may be `=` or `:` with spaces
#: (independent review M5, 2026-09-16, found the `\b...=` form missed every
#: OAuth/Entra spelling).
#: Every quantifier here is bounded (CodeQL py/polynomial-redos, 2026-09-16):
#: an unbounded repeated group next to an unbounded tail let a long
#: credential-shaped log line take polynomial time to reject. The bounds are
#: generous for a real key/URL and turn the worst case into a constant.
_KV_SECRET = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]{1,40}[_-]){0,4}(?:password|passwd|pwd|secret|token|"
    r"api[_-]?key|sig|sas|instrumentationkey|sharedaccesskey|accountkey))"
    r"[\"']?\s*[:=]\s*[\"']?([^\s&;,'\"]{1,4096})")
_BASIC = re.compile(r"(?i)basic\s+[A-Za-z0-9+/=]{8,64}")
#: `scheme://user:password@host` - the password half of a URL credential.
_URL_CREDENTIAL = re.compile(r"(://[^/\s:@]{1,256}:)([^@\s]{1,512})@")

#: Exception classes whose messages are ours to show: raised by our own code
#: (module under `app.`) or the plain ValueError/LookupError the pipeline uses
#: for domain refusals. Driver and library exceptions echo SQL, bound
#: parameters, URLs and delivered values; only their class name is kept.
_DRIVER_MODULES = ("sqlalchemy", "asyncpg", "psycopg", "httpx", "aiohttp",
                   "azure", "requests", "urllib", "ssl", "socket", "botocore")


def _is_domain_exception(exc: BaseException) -> bool:
    for cls in type(exc).__mro__:
        module = cls.__module__ or ""
        if module.startswith(_DRIVER_MODULES):
            return False
    module = type(exc).__module__ or ""
    return module.startswith("app.") or isinstance(exc, (ValueError, LookupError))


def safe_exception_text(exc: BaseException, limit: int = 800) -> str:
    """Exception text fit for evidence that a viewer can read.

    Domain exceptions keep their (redacted) message; anything else keeps only
    the class name and a pointer to the server log, where the full traceback is
    kept under the correlation id (review finding F3, 2026-09-16).
    """
    name = type(exc).__name__
    if _is_domain_exception(exc):
        return redact_text(f"{name}: {exc}")[:limit]
    try:
        correlation = request_context.correlation_id()[:64]
    except Exception:  # noqa: BLE001
        correlation = "unknown"
    return (f"{name} (message withheld from evidence; see the server log for "
            f"correlation id {correlation})")[:limit]

#: Attributes every LogRecord has; anything else on the record was passed via
#: `extra=` and is emitted as a field.
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message", "asctime"}


def redact_text(text: str) -> str:
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _BASIC.sub("Basic [REDACTED]", text)
    text = _URL_CREDENTIAL.sub(lambda m: f"{m.group(1)}[REDACTED]@", text)
    return _KV_SECRET.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SENSITIVE_KEY.search(str(k)) else redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def safe_error(exc: BaseException) -> Dict[str, str]:
    """Class and redacted message; never the raw payload of a record."""
    return {"error_class": type(exc).__name__,
            "error_message": redact_text(str(exc))[:800]}


# ── stored error text, sanitised on READ ────────────────────────────────────
#
# `safe_exception_text` keeps driver payloads out of the evidence tables on
# WRITE (PR #85). Rows written before that fix still carry the raw SQLAlchemy
# rendering — the statement, the bound parameters (delivered values) and the
# driver's message — in `rce_delivery_jobs.error_reason` and `stage_detail`.
# Those rows are evidence and are not rewritten; what a viewer receives is
# masked here instead. The stage prefix and the exception class names stay,
# because they are what an operator needs; everything after the innermost
# driver class, and every `[SQL: ...]` / `[parameters: ...]` fragment, goes.

_ERROR_WITHHELD = " (message withheld from evidence; see the server log)"
#: The SQLAlchemy statement/parameter/background fragments. Nothing useful
#: to a viewer follows the first one, so the cut runs to the end.
_SQL_FRAGMENT = re.compile(r"\s*\[(?:SQL|parameters):.*$", re.DOTALL)
_BACKGROUND = re.compile(r"\s*\(Background on this error at:.*$", re.DOTALL)
#: A driver exception class as SQLAlchemy renders it: "<class 'asyncpg....X'>".
_DRIVER_CLASS = re.compile(r"(<class '[\w.]+'>)")
#: A dialect wrapper as SQLAlchemy renders it: "(sqlalchemy.dialects....Error)".
_DIALECT_CLASS = re.compile(r"(\((?:sqlalchemy|asyncpg|psycopg2?|psycopg)[\w.]*\))")


def sanitize_stored_error(value: Any) -> Any:
    """Mask driver payloads in an error string persisted before PR #85.

    Pure, idempotent, and a no-op on the controlled strings this application
    writes ("worker_stopped_without_reporting", "CURATION: ValueError: ...").
    Non-strings are returned unchanged.
    """
    if not isinstance(value, str) or not value:
        return value
    text = _SQL_FRAGMENT.sub("", value)
    text = _BACKGROUND.sub("", text)
    withheld = False
    match = None
    for m in _DRIVER_CLASS.finditer(text):
        match = m  # the innermost (last) driver class
    if match is None:
        for m in _DIALECT_CLASS.finditer(text):
            match = m
    if match is not None:
        rest = text[match.end():]
        text = text[:match.end()]
        withheld = bool(rest.strip(" :\n\t"))
    if withheld:
        text = text.rstrip() + _ERROR_WITHHELD
    return text if text != value else value


def sanitize_stored_error_tree(value: Any) -> Any:
    """`sanitize_stored_error` applied to every string in a nested structure
    (a job's `stage_detail`). Dicts and lists are copied; nothing is mutated."""
    if isinstance(value, dict):
        return {k: sanitize_stored_error_tree(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_stored_error_tree(v) for v in value]
    return sanitize_stored_error(value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        payload.update(request_context.current())
        for key, value in vars(record).items():
            if key in _STANDARD or key.startswith("_"):
                continue
            payload[key] = redact(value)
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            payload.update(safe_error(exc))
            payload["stack"] = redact_text("".join(
                traceback.format_exception(*record.exc_info))[-6000:])
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str | int | None = None) -> str:
    """Install the JSON formatter on the root logger. Returns the format used."""
    fmt = os.environ.get("DOCUACTION_LOG_FORMAT", "json").strip().lower()
    level = level or os.environ.get("LOG_LEVEL", "INFO")
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    if fmt == "plain":
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s:%(name)s:%(message)s"))
    else:
        fmt = "json"
        handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    # Third-party chatter stays at WARNING; the app's own loggers follow `level`.
    for noisy in ("httpx", "httpcore", "urllib3", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return fmt
