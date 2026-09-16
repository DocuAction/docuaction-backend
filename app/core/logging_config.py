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
    r"cookie|set-cookie|client[_-]?secret|sas|signature)", re.IGNORECASE)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
#: `instrumentationkey` / `sharedaccesskey` / `accountkey`: the credential
#: halves of an Application Insights, Service Bus or Storage connection string,
#: which an Azure SDK exception message can echo back verbatim (found by
#: tests/test_telemetry.py, 2026-09-17).
_KV_SECRET = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|sig|sas|instrumentationkey|"
    r"sharedaccesskey|accountkey)=([^\s&;'\"]+)")

#: Attributes every LogRecord has; anything else on the record was passed via
#: `extra=` and is emitted as a field.
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message", "asctime"}


def redact_text(text: str) -> str:
    text = _BEARER.sub("Bearer [REDACTED]", text)
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
