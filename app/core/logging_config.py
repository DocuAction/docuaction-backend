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
import functools
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
# WRITE (PR #85). Rows written before that fix still carry what the driver
# said: the SQLAlchemy rendering of the failed statement, its bound parameters
# (delivered values) and the driver's message, or the bare driver message with
# no SQLAlchemy wrapper at all. Those rows are evidence and are never
# rewritten; what a viewer receives is masked here instead.
#
# THE CONTRACT (one function, every caller; tests/test_stored_error_contract_
# 2026_11_14.py states it case by case):
#
#   1. Every `[SQL: ...]` / `[parameters: ...]` / `(Background on ...)` fragment
#      and every PostgreSQL message field (`DETAIL:`, `HINT:`, ...) goes, to the
#      end of the text.
#   2. Where the text carries driver class markers, it is cut after the FIRST
#      unbroken run of markers. Whatever follows that run is the driver's
#      message and is withheld, including any later marker: a later marker is
#      part of the message, not a new boundary.
#   3. Where it carries no marker, the exception class NAME decides. The class
#      is taken from the caller (`failure_class`, which a stage event stores
#      beside its reason) or from the text itself: every writer this
#      application has had put the class in front of the message
#      ("{stage}: {Class}: ..." / "{stage} did not complete: {Class}: ...").
#      A message is shown only when that class is in the closed domain
#      vocabulary below AND nothing was cut under rule 1 (a removed fragment
#      proves driver text is embedded in it); otherwise the class keeps its
#      name and loses its message. In front of a marker run (rule 2) only the
#      stage and one class name are kept, for the same reason.
#   4. Text that lost a fragment or a message field and names no class at all
#      is a driver rendering by construction, and is withheld whole.
#   5. Anything else is one of this application's controlled strings and is
#      returned unchanged.
#
# KNOWN LIMIT: a string with no marker, no fragment, no message field, no class
# name and no `failure_class` is indistinguishable from a controlled string
# and is returned unchanged. No writer in this repository's history produced a
# job or stage failure reason of that shape; the per-stage `error` values inside
# a job's `stage_detail` written by the first runner (`str(exc)`) can be.

_ERROR_WITHHELD = " (message withheld from evidence; see the server log)"
#: The SQLAlchemy statement/parameter/background fragments. Nothing useful
#: to a viewer follows the first one, so the cut runs to the end.
_SQL_FRAGMENT = re.compile(r"\s*\[(?:SQL|parameters):.*$", re.DOTALL)
_BACKGROUND = re.compile(r"\s*\(Background on this error at:.*$", re.DOTALL)
#: A PostgreSQL server message field. `DETAIL` carries the offending row
#: values ("Key (email)=(...) already exists"); the others carry statement text.
_PG_FIELD = re.compile(r"\s*\b(?:DETAIL|HINT|CONTEXT|QUERY|STATEMENT):\s.*$", re.DOTALL)
#: A driver exception class as SQLAlchemy renders it: "<class 'asyncpg....X'>".
#: Only a class path that begins in a DRIVER OR STANDARD-LIBRARY package is a marker. `<class 'X'>` with any
#: other content is part of a delivered value (CodeRabbit Major on #225) and is not kept.
_DRIVER_PACKAGES = r"(?:asyncpg|psycopg2?|sqlalchemy|builtins|socket|ssl|httpx|httpcore|anyio|asyncio)"
_CLASS_PATH = rf"<class '{_DRIVER_PACKAGES}(?:\.[A-Za-z_]\w*)+'>"
_DRIVER_CLASS = re.compile(rf"({_CLASS_PATH})")
#: A dialect wrapper as SQLAlchemy renders it: "(sqlalchemy.dialects....Error)".
_DIALECT_CLASS = re.compile(r"(\((?:sqlalchemy|asyncpg|psycopg2?|psycopg)[\w.]*\))")
#: Either marker, and what may stand between two markers of one run.
_MARKER_SHAPE = re.compile(rf"{_CLASS_PATH}|\((?:sqlalchemy|asyncpg|psycopg2?|psycopg)[\w.]*\)")


@functools.lru_cache(maxsize=1)
def _known_driver_class_names() -> frozenset:
    """Names of the exception classes the drivers and the standard library really define. A marker is kept ONLY when its last
    path segment is one of them: `<class 'asyncpg.SensitiveRecord4711'>` has a driver-looking prefix but is not a driver class,
    so it is part of a delivered value and is never kept (CodeRabbit on #232, reproduced)."""
    import importlib

    names = set()
    for module in ("builtins", "socket", "ssl", "asyncio", "asyncpg.exceptions", "psycopg2", "psycopg2.errors",
                   "sqlalchemy.exc", "httpx", "httpcore", "anyio"):
        try:
            mod = importlib.import_module(module)
        except Exception:  # noqa: BLE001 - an absent driver contributes nothing
            continue
        for name, obj in vars(mod).items():
            if isinstance(obj, type) and issubclass(obj, BaseException):
                names.add(name)
    names.update({"Error", "DBAPIError", "IntegrityError", "ProgrammingError", "OperationalError", "DataError", "InterfaceError"})
    return frozenset(names)


@functools.lru_cache(maxsize=1)
def _known_path_segments() -> frozenset:
    """The closed vocabulary of dotted-path segments a real driver/stdlib exception path can contain: the module names
    and class qualified-name parts of the exception classes the drivers and the standard library define, plus the
    SQLAlchemy dialect adapter classes. A delivered value cannot add a segment to it."""
    import importlib
    import sys

    segments = {"builtins", "socket", "ssl", "asyncio", "asyncpg", "psycopg", "psycopg2", "sqlalchemy", "httpx", "httpcore",
                "anyio", "exceptions", "errors", "exc", "dialects", "postgresql"}
    for module in ("builtins", "socket", "ssl", "asyncio", "asyncpg.exceptions", "psycopg2", "psycopg2.errors",
                   "sqlalchemy.exc", "sqlalchemy.dialects.postgresql.asyncpg", "sqlalchemy.dialects.postgresql.psycopg2",
                   "sqlalchemy.dialects.postgresql.psycopg", "httpx", "httpcore", "anyio"):
        try:
            mod = importlib.import_module(module)
        except Exception:  # noqa: BLE001 - an absent driver contributes nothing
            continue
        segments.update(module.split("."))
        for name, obj in vars(mod).items():
            if isinstance(obj, type) and (issubclass(obj, BaseException) or name.startswith("AsyncAdapt_")):
                segments.add(name)
                segments.update(getattr(obj, "__module__", "").split("."))
                segments.update(getattr(obj, "__qualname__", name).split("."))
    for name in list(sys.modules):
        if name.split(".")[0] in {"asyncpg", "psycopg2", "psycopg", "sqlalchemy"} and name.count(".") <= 4:
            segments.update(name.split("."))
    segments.discard("")
    return frozenset(segments)


class _ValidatedMarker:
    """The marker regex plus the real-driver-class check, with the same search/match calls."""

    @staticmethod
    def _ok(m):
        """Every segment of the path must be part of a real driver/stdlib path, not only the last one
        (`asyncpg.SensitiveRecord4711.ValueError` ends in a real class but carries a delivered component)."""
        if not m:
            return False
        path = re.sub(r"^(?:<class ')?\(?", "", m.group(0))
        path = re.sub(r"(?:'>)?\)?$", "", path)
        parts = path.split(".")
        return (len(parts) >= 1 and parts[-1] in _known_driver_class_names()
                and all(seg in _known_path_segments() for seg in parts[:-1]))

    def match(self, text, pos=0):
        m = _MARKER_SHAPE.match(text, pos)
        return m if self._ok(m) else None

    def search(self, text, pos=0):
        while True:
            m = _MARKER_SHAPE.search(text, pos)
            if m is None:
                return None
            if self._ok(m):
                return m
            pos = m.start() + 1


_MARKER = _ValidatedMarker()
_MARKER_GAP = re.compile(r"[\s:]*")
#: "{STAGE}: " or "{STAGE} did not complete: " as the runners have written it.
_STAGE_PREFIX = re.compile(r"^[A-Z][A-Z_]*(?: did not complete)?: ")
#: An exception class name in front of its message. After a stage prefix any
#: identifier is a class name ("gaierror", "_RetryableProviderError"); with no
#: stage in front, only a CamelCase word is (it has a lower-case letter, which
#: is what tells it from a stage name or a controlled phrase).
_CLASS_LEAD_IN = re.compile(
    r"^(?:(?P<stage>[A-Z][A-Z_]*(?: did not complete)?: )(?P<cls>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<bare>[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*)): ?")
#: What may stand in front of the first marker: the stage and one class name.
#: Any other text there is part of the message and is withheld with it.
_MARKER_PREFIX = re.compile(
    r"^(?:[A-Z][A-Z_]*(?: did not complete)?: )?(?:[A-Za-z_][A-Za-z_0-9]*: )?")
_CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,127}")

#: The closed vocabulary of exception classes whose stored message is ours to
#: show: the builtin domain errors and the delivery pipeline's own refusals.
#: A class that is not listed keeps its name and loses its message (fail
#: closed); listing a class here is a reviewed decision, checked by a test.
CONTROLLED_FAILURE_CLASSES = frozenset({
    "ValueError", "LookupError", "KeyError", "IndexError",
    "IntakeError", "LineCountMismatch", "NotADelimitedFile",
    "CorrectionRefused", "ReconciliationFailure", "SnapshotRefused",
    "ApprovalRefused", "DeliveryJobConflict",
    # GATE_BLOCKED: the message holds only the intake id and run id (delivery_runner.py), never delivered values.
    "PreflightBlockedError",
})


def _writer_withheld(text: str, cls: str) -> bool:
    """True when `text` is exactly the withheld form a writer or this function
    produces for `cls` - a payload merely containing the phrase is not."""
    return re.fullmatch(
        rf"(?:[A-Z][A-Z_]*(?: did not complete)?: )?{re.escape(cls)} "
        r"\(message withheld from evidence; see the server log"
        r"(?: for correlation id [A-Za-z0-9_.:-]{1,64})?\)", text) is not None


def sanitize_stored_error(value: Any, *, failure_class: str | None = None) -> Any:
    """Mask driver payloads in stored error text on the way to a viewer.

    Pure and idempotent; a no-op on the controlled strings this application
    writes ("worker_stopped_without_reporting", "CURATION: ValueError: ...").
    Non-strings are returned unchanged. `failure_class` is the exception class
    name stored beside the text, where the caller has one. The contract is
    stated in the comment block above.
    """
    if not isinstance(value, str) or not value:
        return value
    text = _BACKGROUND.sub("", _SQL_FRAGMENT.sub("", value))
    text = _PG_FIELD.sub("", text)
    cut = text != value

    first = _MARKER.search(text)
    if first is not None:
        end = first.end()
        while True:                      # the unbroken run of markers only
            following = _MARKER.match(text, _MARKER_GAP.match(text, end).end())
            if following is None:
                break
            end = following.end()
        rest = text[end:]
        before = text[:first.start()]
        kept = _MARKER_PREFIX.match(before).group(0)
        dropped = bool(before[len(kept):].strip(" :\n\t"))
        if dropped and before.rstrip()[-1:] in ('"', "'"):
            # Discarded text AND the marker sits inside a quoted value: it is part of the delivered value.
            return (kept.rstrip(": ").rstrip() or "Error") + _ERROR_WITHHELD
        text = kept + text[first.start():end]
        if cut or dropped or rest.strip(" :\n\t"):
            text = text.rstrip() + _ERROR_WITHHELD
        return text if text != value else value

    if "<class '" in text or _MARKER_SHAPE.search(text):
        # A class-marker shape that is NOT a driver class path (not recognised above) is a delivered value that
        # looks like one; it is never kept.
        stage = _STAGE_PREFIX.match(text)
        return (stage.group(0) if stage else "") + "Error" + _ERROR_WITHHELD

    lead = _CLASS_LEAD_IN.match(text)
    named = (lead.group("cls") or lead.group("bare")) if lead else None
    if failure_class:
        if not cut and _writer_withheld(text, failure_class):
            return value
        if not cut and named == failure_class and named in CONTROLLED_FAILURE_CLASSES:
            return value
        name = failure_class if _CLASS_NAME.fullmatch(failure_class) else "Error"
        return name + _ERROR_WITHHELD
    if named is not None:
        if not cut and named in CONTROLLED_FAILURE_CLASSES:
            return value
        return (lead.group("stage") or "") + named + _ERROR_WITHHELD
    if cut:
        stage = _STAGE_PREFIX.match(text)
        return (stage.group(0) if stage else "") + "Error" + _ERROR_WITHHELD
    return value


#: A bare code as a writer stores it under an error key. NOT any identifier: a
#: delivered value can be one token too (review finding 2026-11-14). A code is
#: an exception class NAME - a listed domain class, or a CamelCase word with one
#: of the endings Python and the drivers give their exception classes - or one
#: of the tokens this application writes itself.
#: An ending is NOT provenance (CodeRabbit on #233: `SensitiveRecord4711Error` is a delivered value): a code is shown only
#: when it is a listed domain class, a real driver/stdlib exception class name, or a token this application writes.
_TRUSTED_ERROR_TOKENS = frozenset({"worker_stopped_without_reporting"})


def _is_trusted_error_code(value: str) -> bool:
    return (value in CONTROLLED_FAILURE_CLASSES or value in _TRUSTED_ERROR_TOKENS
            or value in _known_driver_class_names())
#: The withheld form for ANY class, as `safe_exception_text` and this module write it.
_ANY_WITHHELD = re.compile(
    r"(?:[A-Z][A-Z_]*(?: did not complete)?: )?[A-Za-z_][A-Za-z_0-9]{0,127} "
    r"\(message withheld from evidence; see the server log"
    r"(?: for correlation id [A-Za-z0-9_.:\-]{1,64})?\)")


#: The value a writer stores beside an error value, under "<key>_writer", to
#: say the value came from `safe_exception_text`. Only server code writes
#: `stage_detail` and stage-event `detail`; no request body reaches them.
SAFE_ERROR_WRITER = "safe_exception_text/1"


def _is_error_key(key: Any) -> bool:
    name = str(key).lower()
    return name == "error" or name.endswith("_error")


def _sanitize_error_value(value: str, *, safe_writer: bool = False) -> str:
    """A string stored under an error key (`stage_detail[stage]["error"]`,
    `snapshot_error`, a stage event's `detail["error"]`).

    The first runner stored `str(exc)` there: a bare message with no class
    beside it, which `sanitize_stored_error` cannot tell from a controlled
    string. So here the rule is turned round - only what a writer of this
    application produces is shown:

      * the withheld form ("Class (message withheld ...)");
      * a bare code: an exception class name ("TimeoutError") or a token this
        application writes ("worker_stopped_without_reporting").

    Anything else is withheld. A message with a class name in front keeps the
    name and loses the message, for every class, UNLESS the mark of its writer
    is stored beside it ("<key>_writer" == SAFE_ERROR_WRITER, which the
    delivery runner writes with every per-stage error since 2026-11-14). Rows
    written before that carry no mark: their per-stage message is withheld,
    and the domain message is still shown where its class has provenance - the
    job's `error_reason` (the class there was always written from
    `type(exc).__name__`) and a stage event's `failure_reason` (which has
    `failure_class` beside it).
    """
    masked = sanitize_stored_error(value)
    if masked != value:
        if safe_writer or _MARKER.search(value) is not None:
            return masked
        # UNMARKED (no driver marker, no writer mark): the general rule may have kept a leading CamelCase name that came
        # from the delivered text. Keep a name only when it is a trusted class; otherwise withhold the whole value.
        lead = _CLASS_LEAD_IN.match(value)
        named = (lead.group("cls") or lead.group("bare")) if lead else None
        if named is not None and _is_trusted_error_code(named):
            return masked
        stage = _STAGE_PREFIX.match(value)
        return (stage.group(0) if stage else "") + _ERROR_WITHHELD.strip()
    if safe_writer:
        # Marked by its writer as `safe_exception_text` output: that function
        # keeps a message only for a domain exception, and the class in front
        # was written from the exception itself. The general rule above has
        # been applied; nothing more is withheld.
        return value
    if _ANY_WITHHELD.fullmatch(value) or _is_trusted_error_code(value):
        return value
    lead = _CLASS_LEAD_IN.match(value)
    if lead:
        # A class name in FRONT of the message is not provenance by itself: a
        # legacy str(exc) can begin "ValueError: ...". Without the mark of its
        # writer beside the value the name is kept and the message is withheld,
        # for every class, listed or not.
        return ((lead.group("stage") or "") + (lead.group("cls") or lead.group("bare"))
                + _ERROR_WITHHELD)
    return _ERROR_WITHHELD.strip()


def sanitize_stored_error_tree(value: Any, *, strict_error_keys: bool = False,
                               _under_error_key: bool = False,
                               _safe_writer: bool = False) -> Any:
    """`sanitize_stored_error` applied to every string in a nested structure.
    Dicts and lists are copied; nothing is mutated.

    `strict_error_keys=True` is for structures whose error keys were written by
    the delivery runner (a job's `stage_detail`, a stage event's `detail`):
    a string under a key named `error` or `*_error` is shown only in a shape a
    writer produces (see `_sanitize_error_value`). Other keys, and every caller
    that does not ask, keep the general rule."""
    if isinstance(value, dict):
        return {k: sanitize_stored_error_tree(
                    v, strict_error_keys=strict_error_keys,
                    _under_error_key=strict_error_keys and _is_error_key(k),
                    _safe_writer=value.get(f"{k}_writer") == SAFE_ERROR_WRITER)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_stored_error_tree(v, strict_error_keys=strict_error_keys,
                                           _under_error_key=_under_error_key)
                for v in value]
    if _under_error_key and isinstance(value, str) and value:
        return _sanitize_error_value(value, safe_writer=_safe_writer)
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
