"""Request correlation and structured logging (app/core/request_context.py,
app/core/logging_config.py) - the 2026-09-17 remediation's observability layer.

What is pinned:
  * a well-formed incoming X-Request-ID is PRESERVED; a malformed one is
    REPLACED (it is caller-controlled input that ends up in log lines);
  * a W3C traceparent is parsed into trace/span ids; garbage is ignored;
  * every response echoes X-Request-ID, including error responses, and the
    error body's request_id is the same value;
  * a JSON log line written inside a request carries request_id;
  * bearer tokens and credential-shaped keys are redacted;
  * bind() restores the previous context on exit.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.core import request_context as rc
from app.core.logging_config import JsonFormatter, redact, redact_text, safe_error


# -- ids -----------------------------------------------------------------------

def test_valid_incoming_request_id_is_preserved():
    assert rc.accept_request_id("lane-a-test-0001") == "lane-a-test-0001"
    assert rc.accept_request_id("  abc.def:ghi-jkl  ") == "abc.def:ghi-jkl"


@pytest.mark.parametrize("bad", [None, "", "short", "has space in it",
                                 "x" * 65, "<script>alert(1)</script>",
                                 "a\nb-injected-line-here"])
def test_malformed_incoming_request_id_is_replaced(bad):
    minted = rc.accept_request_id(bad)
    assert minted != bad
    assert rc._SAFE_ID.match(minted), minted


def test_traceparent_is_parsed_and_garbage_ignored():
    trace, span = rc.parse_traceparent(
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
    assert trace == "4bf92f3577b34da6a3ce929d0e0e4736" and span == "00f067aa0ba902b7"
    assert rc.parse_traceparent("nonsense") == (None, None)
    assert rc.parse_traceparent(None) == (None, None)
    # all-zero ids are invalid per W3C and are not trusted
    assert rc.parse_traceparent("00-" + "0" * 32 + "-00f067aa0ba902b7-01") == (None, None)


def test_bind_sets_and_restores():
    assert rc.get("request_id") is None
    with rc.bind(request_id="outer-request-id-1"):
        assert rc.get("request_id") == "outer-request-id-1"
        with rc.bind(request_id="inner-request-id-2", job_id="job-1", attempt=2):
            assert rc.get("request_id") == "inner-request-id-2"
            assert rc.get("job_id") == "job-1"
            assert rc.get("attempt") == 2
            assert rc.correlation_id() == "inner-request-id-2"
        assert rc.get("request_id") == "outer-request-id-1"
        assert rc.get("job_id") is None
    assert rc.get("request_id") is None


def test_bind_rejects_unknown_fields():
    with pytest.raises(KeyError):
        with rc.bind(not_a_field="x"):
            pass


def test_correlation_id_falls_back_to_job_then_mints():
    with rc.bind(job_id="job-only"):
        assert rc.correlation_id() == "job-only"
    minted = rc.correlation_id()
    assert minted and len(minted) == 36


def test_build_identity_reports_unknown_not_guessed(monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("BUILD_TIME", raising=False)
    identity = rc.build_identity()
    assert identity["git_sha"] == "unknown" and identity["build_time"] == "unknown"
    monkeypatch.setenv("GIT_SHA", "abc123")
    monkeypatch.setenv("BUILD_TIME", "2026-09-17T00:00:00Z")
    identity = rc.build_identity()
    assert identity["git_sha"] == "abc123" and identity["build_time"] == "2026-09-17T00:00:00Z"
    assert rc.build_sha() == "abc123"


# -- the middleware on the real app ----------------------------------------------

def test_header_is_echoed_and_preserved_when_valid(client):
    r = client.get("/health", headers={"X-Request-ID": "lane-a-echo-0001"})
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID") == "lane-a-echo-0001"


def test_header_is_replaced_when_malformed(client):
    r = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})
    assert r.status_code == 200
    echoed = r.headers.get("X-Request-ID")
    assert echoed and echoed != "bad id with spaces"
    assert rc._SAFE_ID.match(echoed)


def test_header_is_minted_when_absent(client):
    r = client.get("/health")
    echoed = r.headers.get("X-Request-ID")
    assert echoed and rc._SAFE_ID.match(echoed)


def test_error_body_request_id_matches_the_header(client):
    r = client.get("/api/tefca/rce/delivery-jobs/not-a-job/detail",
                   headers={"X-Request-ID": "lane-a-error-0002"})
    assert r.status_code == 401  # anonymous
    assert r.headers.get("X-Request-ID") == "lane-a-error-0002"
    assert r.json()["request_id"] == "lane-a-error-0002"


def test_404_body_carries_the_same_request_id(client):
    r = client.get("/api/v1/definitely-not-a-route-xyz",
                   headers={"X-Request-ID": "lane-a-404-0003"})
    assert r.status_code == 404
    assert r.json()["request_id"] == "lane-a-404-0003"
    assert r.headers.get("X-Request-ID") == "lane-a-404-0003"


def test_request_context_middleware_is_outermost():
    from app.main import app
    names = [m.cls.__name__ for m in app.user_middleware]
    assert names[0] == "RequestContextMiddleware", names


# -- the JSON formatter ----------------------------------------------------------

def _format(record_msg: str, **extra) -> dict:
    record = logging.LogRecord("docuaction.test", logging.INFO, __file__, 1,
                               record_msg, (), None)
    for k, v in extra.items():
        setattr(record, k, v)
    return json.loads(JsonFormatter().format(record))


def test_json_log_line_carries_request_id_and_build():
    with rc.bind(request_id="lane-a-log-0004", job_id="job-9", stage="QUALITY"):
        line = _format("stage started")
    assert line["request_id"] == "lane-a-log-0004"
    assert line["job_id"] == "job-9" and line["stage"] == "QUALITY"
    assert line["message"] == "stage started"
    for key in ("ts", "level", "logger", "git_sha", "version", "environment"):
        assert key in line


def test_bearer_tokens_are_redacted_in_messages_and_extras():
    line = _format("auth header was Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
                   authorization="Bearer abcdefghijklmnop", detail="token=supersecretvalue")
    assert "eyJhbGciOiJIUzI1NiJ9" not in line["message"]
    assert "Bearer [REDACTED]" in line["message"]
    # a top-level extra is text-redacted (the token is gone); nested keys are
    # key-redacted, see test_secret_keys_are_redacted_in_nested_structures
    assert "abcdefghijklmnop" not in line["authorization"]
    assert "[REDACTED]" in line["authorization"]
    assert line["detail"] == "token=[REDACTED]"


def test_secret_keys_are_redacted_in_nested_structures():
    out = redact({"secret_key": "s3cr3t", "nested": {"api_key": "k", "ok": "fine"},
                  "items": [{"password": "p"}], "connection_string": "postgresql://u:p@h/db"})
    assert out["secret_key"] == "[REDACTED]"
    assert out["nested"]["api_key"] == "[REDACTED]" and out["nested"]["ok"] == "fine"
    assert out["items"][0]["password"] == "[REDACTED]"
    assert out["connection_string"] == "[REDACTED]"


def test_redact_text_masks_key_value_credentials():
    assert redact_text("sig=abc123&sas=def456") == "sig=[REDACTED]&sas=[REDACTED]"
    assert redact_text("nothing sensitive here") == "nothing sensitive here"


def test_safe_error_never_carries_the_raw_payload_unredacted():
    err = safe_error(RuntimeError("failed with password=hunter2 for user"))
    assert err["error_class"] == "RuntimeError"
    assert "hunter2" not in err["error_message"]


def test_configure_logging_honours_plain_format(monkeypatch):
    from app.core.logging_config import configure_logging

    monkeypatch.setenv("DOCUACTION_LOG_FORMAT", "plain")
    assert configure_logging() == "plain"
    monkeypatch.setenv("DOCUACTION_LOG_FORMAT", "json")
    assert configure_logging() == "json"
    root = logging.getLogger()
    assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)


# -- current_trace_ids(): the active OpenTelemetry span, else nothing -------------

def test_current_trace_ids_is_empty_without_an_active_span():
    assert rc.current_trace_ids() == (None, None)
    # and the resolver then falls back to the header
    assert rc.resolve_trace_ids(
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01") == (
        "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7")
    assert rc.resolve_trace_ids(None) == (None, None)


def test_current_trace_ids_reads_the_active_span_and_wins_over_the_header():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    provider = TracerProvider(sampler=ALWAYS_ON)
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("unit") as span:
        ctx = span.get_span_context()
        trace_id, span_id = rc.current_trace_ids()
        assert trace_id == format(ctx.trace_id, "032x") and len(trace_id) == 32
        assert span_id == format(ctx.span_id, "016x") and len(span_id) == 16
        # an inbound header does not override the span that is actually open
        assert rc.resolve_trace_ids(
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01") == (trace_id, span_id)
    assert rc.current_trace_ids() == (None, None)
    provider.shutdown()


def test_current_trace_ids_ignores_a_non_recording_invalid_span():
    from opentelemetry import trace
    from opentelemetry.trace import INVALID_SPAN

    with trace.use_span(INVALID_SPAN):
        assert rc.current_trace_ids() == (None, None)
