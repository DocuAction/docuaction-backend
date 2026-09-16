"""OpenTelemetry / Azure Monitor increment (app/core/telemetry.py), 2026-09-17.

Pure tests: no network, no Application Insights. The Azure distro's
`configure_azure_monitor` is replaced by a fake that records its arguments and
installs an in-memory tracer provider, so the enabled path is proven end to
end (sampler, excluded urls, redaction, instrumentation) without an exporter.

What is pinned:
  * disabled by default, with a reason string; disabled when only one of the
    two switches is set; the connection string never appears in the status;
  * the enabled path passes the redaction processor, the resource identity and
    the FastAPI-disabled instrumentation option to the distro, installs the
    error-keeping sampler, and instruments THIS app with /health and
    /api/admin/health excluded;
  * the redaction processor drops authorization / password / connection_string
    values, header attributes, query strings and statement parameters;
  * error spans are always sampled, children follow their parent, roots follow
    the ratio; a configuration exception leaves the app importable and disabled;
  * RequestContextMiddleware binds the ACTIVE span's ids (in-memory provider),
    an inbound W3C traceparent becomes the parent of the server span, and with
    telemetry off the middleware still parses the header;
  * stage spans carry job_id / intake_id / stage / attempt and nest under the
    job span; never a credential-shaped attribute.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, Decision
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context

from app.core import request_context as rc
from app.core import telemetry
from app.core.logging_config import JsonFormatter

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


# -- fixtures --------------------------------------------------------------------

@pytest.fixture
def in_memory():
    """(provider, exporter) with the error-keeping sampler at ratio 1.0 so every
    span is recorded, plus the redaction processor in front of the exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=telemetry.ErrorKeepingSampler(1.0),
                              resource=telemetry.build_resource())
    provider.add_span_processor(telemetry.RedactingSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.use_tracer_provider(provider)
    try:
        yield provider, exporter
    finally:
        telemetry.use_tracer_provider(None)
        provider.shutdown()


@pytest.fixture
def status_reset():
    before = telemetry.telemetry_status()
    yield
    telemetry._set_status(before["enabled"], before["reason"], before["sampler"],
                          before["exporter"])
    telemetry.use_tracer_provider(None)


def _tiny_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe():
        return {"trace_id": rc.get("trace_id"), "span_id": rc.get("span_id"),
                "request_id": rc.get("request_id")}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/api/admin/health")
    async def admin_health():
        return {"status": "healthy"}

    app.add_middleware(rc.RequestContextMiddleware)
    return app


# -- switches --------------------------------------------------------------------

def test_disabled_by_default_with_reason(monkeypatch, status_reset):
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    status = telemetry.configure_telemetry(FastAPI())
    assert status == {"enabled": False, "reason": "OTEL_ENABLED is not true",
                      "sampler": None, "exporter": "none"}
    assert telemetry.telemetry_status() == status


def test_enabled_flag_without_connection_string_is_disabled(monkeypatch, status_reset):
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    status = telemetry.configure_telemetry(FastAPI())
    assert status["enabled"] is False
    assert status["reason"] == "APPLICATIONINSIGHTS_CONNECTION_STRING is not set"
    assert status["exporter"] == "none"


def test_connection_string_without_flag_is_disabled(monkeypatch, status_reset):
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING",
                       "InstrumentationKey=00000000-0000-0000-0000-000000000000")
    status = telemetry.configure_telemetry(FastAPI())
    assert status["enabled"] is False and status["reason"] == "OTEL_ENABLED is not true"
    assert "InstrumentationKey" not in json.dumps(status)


# -- the enabled path with a fake distro ------------------------------------------

class _FakeDistro:
    """Stands in for azure.monitor.opentelemetry.configure_azure_monitor: records
    the kwargs and installs a provider with an in-memory exporter, the way the
    real distro installs one with the Azure exporter."""

    def __init__(self):
        self.kwargs = None
        self.exporter = InMemorySpanExporter()
        self.provider = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        # The distro's own default sampler is NOT ours; configure_telemetry must
        # replace it.
        self.provider = TracerProvider(sampler=ALWAYS_ON, resource=kwargs["resource"])
        for proc in kwargs.get("span_processors", []):
            self.provider.add_span_processor(proc)
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        # a tracer created by the distro before our sampler is installed
        self.provider.get_tracer("distro-early-tracer")


@pytest.fixture
def fake_distro(monkeypatch, status_reset):
    fake = _FakeDistro()
    import azure.monitor.opentelemetry as distro
    monkeypatch.setattr(distro, "configure_azure_monitor", fake)
    # The real global provider must not be set by a unit test (it can only be
    # set once per process); hand the fake's provider back instead.
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: fake.provider)
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING",
                       "InstrumentationKey=11111111-2222-3333-4444-555555555555;"
                       "IngestionEndpoint=https://example.invalid/")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.35")
    monkeypatch.setenv("GIT_SHA", "abc123def456")
    monkeypatch.setenv("BUILD_TIME", "2026-09-17T00:00:00Z")
    monkeypatch.setenv("ENVIRONMENT", "test")
    yield fake
    try:
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        AsyncPGInstrumentor().uninstrument()
    except Exception:  # noqa: BLE001
        pass


def test_enabled_path_configures_sampler_resource_redaction_and_excluded_urls(fake_distro):
    app = _tiny_app()
    status = telemetry.configure_telemetry(app)

    assert status["enabled"] is True and status["exporter"] == "azure_monitor"
    assert status["sampler"] == "ErrorKeepingParentBasedTraceIdRatio{0.35}"
    assert "InstrumentationKey" not in json.dumps(status)
    assert "InstrumentationKey" not in json.dumps({k: str(v) for k, v in fake_distro.kwargs.items()})

    kwargs = fake_distro.kwargs
    assert "connection_string" not in kwargs  # read from the environment by the distro
    assert any(isinstance(p, telemetry.RedactingSpanProcessor) for p in kwargs["span_processors"])
    assert kwargs["instrumentation_options"] == {"fastapi": {"enabled": False}}
    resource = kwargs["resource"].attributes
    assert resource["service.name"] == "docuaction-backend"
    assert resource["service.version"] == rc.APP_VERSION
    assert resource["deployment.environment"] == "test"
    assert resource["git.sha"] == "abc123def456"
    assert resource["build.time"] == "2026-09-17T00:00:00Z"

    provider = fake_distro.provider
    assert isinstance(provider.sampler, telemetry.ErrorKeepingSampler)
    assert provider.sampler.ratio == 0.35
    # the tracer the distro created BEFORE the swap uses our sampler too
    early = provider.get_tracer("distro-early-tracer")
    assert isinstance(early.sampler, telemetry.ErrorKeepingSampler)

    # this app is instrumented: a request produces a server span, the health
    # probes do not. Every request carries a SAMPLED upstream traceparent so
    # the parent-based rule keeps it: at ratio 0.35 a root span would be a coin
    # toss, which is exactly what the excluded-url assertion must not depend on.
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is True
    client = TestClient(app, headers={"traceparent": TRACEPARENT})
    assert client.get("/health").status_code == 200
    assert client.get("/api/admin/health").status_code == 200
    assert fake_distro.exporter.get_finished_spans() == ()
    r = client.get("/probe?token=abc")
    assert r.status_code == 200
    spans = fake_distro.exporter.get_finished_spans()
    server = [s for s in spans if s.kind == trace.SpanKind.SERVER]
    assert len(server) == 1, [s.name for s in spans]
    # the log context carries the REAL trace id of the server span
    assert r.json()["trace_id"] == format(server[0].context.trace_id, "032x")
    assert r.json()["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    # ... and the query string never reaches an exported attribute
    for span in spans:
        for key, value in span.attributes.items():
            assert "token=abc" not in str(value), (span.name, key, value)


def test_configuration_exception_is_logged_once_and_app_stays_up(monkeypatch, status_reset, caplog):
    import azure.monitor.opentelemetry as distro

    def _boom(**kwargs):
        raise RuntimeError("exporter refused the connection string InstrumentationKey=secret")

    monkeypatch.setattr(distro, "configure_azure_monitor", _boom)
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=secret")
    with caplog.at_level(logging.WARNING, logger="docuaction.telemetry"):
        status = telemetry.configure_telemetry(_tiny_app())
    assert status["enabled"] is False and status["exporter"] == "none"
    assert status["reason"].startswith("configuration failed")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                and r.name == "docuaction.telemetry"]
    assert len(warnings) == 1 and warnings[0].exc_info is not None
    # the JSON formatter redacts the exception text before it reaches a sink
    line = json.loads(JsonFormatter().format(warnings[0]))
    assert "secret" not in line["error_message"] or "[REDACTED]" in line["error_message"]
    # importing the app again must not raise either
    import importlib
    import app.main  # noqa: F401
    importlib.import_module("app.main")


def test_health_reports_telemetry_status(status_reset):
    from app.api import admin_health
    assert admin_health.telemetry_status() == telemetry.telemetry_status()
    assert set(telemetry.telemetry_status()) == {"enabled", "reason", "sampler", "exporter"}


# -- redaction ---------------------------------------------------------------------

def test_redaction_processor_scrubs_credentials_headers_queries_and_parameters(in_memory):
    provider, exporter = in_memory
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span("db.query", attributes={
        "authorization": "Bearer eyJabc.def.ghi",
        "password": "hunter2",
        "connection_string": "postgresql://u:p@h/db",
        "db.connection_string": "postgresql://u:p@h/db",
        "http.request.header.authorization": ["Bearer x"],
        "http.request.header.x_api_key": ["k"],
        "http.response.header.set_cookie": ["session=1"],
        "http.url": "https://host/api/x?token=abc&b=2",
        "url.full": "https://host/api/x?sig=xyz",
        "http.target": "/api/x?token=abc",
        "url.query": "token=abc",
        "db.statement": "select * from users where email = 'a@b.c' and id = 42 and x = $1",
        "db.statement.parameters": "('a@b.c', 42)",
        "job_id": "job-1",
        "note": "sent Bearer abcdefghijklmnop and sas=xyz123",
    }) as span:
        span.set_attribute("http.request.header.cookie", ["late=1"])
        span.set_attribute("api_key", "late-secret")
        span.set_attribute("http.status_code", 200)

    (finished,) = exporter.get_finished_spans()
    a = dict(finished.attributes)
    assert a["authorization"] == "[REDACTED]"
    assert a["password"] == "[REDACTED]"
    assert a["connection_string"] == "[REDACTED]"
    assert a["db.connection_string"] == "[REDACTED]"
    assert a["api_key"] == "[REDACTED]"  # set after start: caught at end
    assert not any(k.startswith("http.request.header.") for k in a), a
    assert not any(k.startswith("http.response.header.") for k in a), a
    assert "url.query" not in a and "db.statement.parameters" not in a
    assert a["http.url"] == "https://host/api/x"
    assert a["url.full"] == "https://host/api/x"
    assert a["http.target"] == "/api/x"
    assert a["db.statement"] == "select * from users where email = ? and id = ? and x = $1"
    assert a["job_id"] == "job-1" and a["http.status_code"] == 200
    assert a["note"] == "sent Bearer [REDACTED] and sas=[REDACTED]"
    blob = json.dumps(a)
    for leak in ("hunter2", "eyJabc", "u:p@h", "a@b.c", "token=abc", "sig=xyz", "late-secret"):
        assert leak not in blob, leak


def test_strip_sql_literals_keeps_placeholders():
    assert telemetry.strip_sql_literals("update t set a = 'it''s' where id = $2 and n > 10.5") == \
        "update t set a = ? where id = $2 and n > ?"
    assert telemetry.strip_sql_literals("select col1, t2.x from t2") == "select col1, t2.x from t2"


def test_safe_attributes_keeps_identifiers_only():
    import uuid
    jid = uuid.uuid4()
    out = telemetry.safe_attributes({"job_id": jid, "attempt": 2, "held": True,
                                     "token": "abc", "api_key": "k", "none": None,
                                     "text": "Bearer abcdefghijklmnop"})
    assert out == {"job_id": str(jid), "attempt": 2, "held": True, "text": "Bearer [REDACTED]"}


# -- sampling ----------------------------------------------------------------------

def _remote_parent(sampled: bool):
    ctx = SpanContext(trace_id=0x4BF92F3577B34DA6A3CE929D0E0E4736, span_id=0x00F067AA0BA902B7,
                      is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED if sampled else 0))
    return set_span_in_context(NonRecordingSpan(ctx))


def test_error_spans_are_always_sampled_even_at_ratio_zero():
    sampler = telemetry.ErrorKeepingSampler(0.0)
    assert sampler.should_sample(None, 1, "rce.stage.QUALITY").decision is Decision.DROP
    assert sampler.should_sample(None, 1, "stage failed").decision is Decision.RECORD_AND_SAMPLE
    assert sampler.should_sample(None, 1, "x", attributes={"error": True}).decision \
        is Decision.RECORD_AND_SAMPLE
    assert sampler.should_sample(None, 1, "x", attributes={"exception.type": "ValueError"}).decision \
        is Decision.RECORD_AND_SAMPLE
    assert sampler.should_sample(None, 1, "x", attributes={"http.status_code": 503}).decision \
        is Decision.RECORD_AND_SAMPLE
    assert sampler.should_sample(None, 1, "x", attributes={"otel.status_code": "ERROR"}).decision \
        is Decision.RECORD_AND_SAMPLE
    # the delivery job root asks to be kept
    assert sampler.should_sample(None, 1, "rce.delivery_job",
                                 attributes={telemetry.ALWAYS_SAMPLE_ATTRIBUTE: True}).decision \
        is Decision.RECORD_AND_SAMPLE
    # an error child of a NOT-sampled parent is still kept
    assert sampler.should_sample(_remote_parent(False), 1, "x",
                                 attributes={"error": True}).decision is Decision.RECORD_AND_SAMPLE


def test_children_follow_their_parent_and_roots_follow_the_ratio():
    sampler = telemetry.ErrorKeepingSampler(0.0)
    assert sampler.should_sample(_remote_parent(True), 1, "child").decision is Decision.RECORD_AND_SAMPLE
    assert sampler.should_sample(_remote_parent(False), 1, "child").decision is Decision.DROP
    always = telemetry.ErrorKeepingSampler(1.0)
    assert always.should_sample(None, 1, "root").decision is Decision.RECORD_AND_SAMPLE
    assert always.should_sample(_remote_parent(False), 1, "child").decision is Decision.DROP
    # ratio behaves like TraceIdRatioBased on roots: low trace ids sampled at 0.5
    half = telemetry.ErrorKeepingSampler(0.5)
    assert half.should_sample(None, 1, "root").decision is Decision.RECORD_AND_SAMPLE
    assert half.should_sample(None, (1 << 64) - 1, "root").decision is Decision.DROP
    assert half.get_description() == "ErrorKeepingParentBasedTraceIdRatio{0.5}"


def test_sampler_ratio_from_env_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv("OTEL_TRACES_SAMPLER_ARG", raising=False)
    assert telemetry.sample_ratio_from_env() == 0.2
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.05")
    assert telemetry.sample_ratio_from_env() == 0.05
    for bad in ("1.5", "-1", "abc", "nan"):
        monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", bad)
        assert telemetry.sample_ratio_from_env() == 0.2, bad


# -- request middleware correlation ----------------------------------------------

def test_middleware_binds_trace_ids_from_the_active_span(in_memory):
    provider, exporter = in_memory
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    app = _tiny_app()
    FastAPIInstrumentor.instrument_app(app, excluded_urls=",".join(telemetry.EXCLUDED_URLS),
                                       tracer_provider=provider)
    try:
        body = TestClient(app).get("/probe").json()
        server = [s for s in exporter.get_finished_spans() if s.kind == trace.SpanKind.SERVER]
        assert len(server) == 1
        assert body["trace_id"] == format(server[0].context.trace_id, "032x")
        assert body["span_id"] is not None and len(body["span_id"]) == 16
        assert server[0].parent is None  # no inbound traceparent: a new trace
    finally:
        FastAPIInstrumentor.uninstrument_app(app)


def test_inbound_traceparent_is_the_parent_of_the_server_span(in_memory):
    provider, exporter = in_memory
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    app = _tiny_app()
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    try:
        body = TestClient(app).get("/probe", headers={"traceparent": TRACEPARENT}).json()
        server = [s for s in exporter.get_finished_spans() if s.kind == trace.SpanKind.SERVER]
        assert len(server) == 1
        assert format(server[0].context.trace_id, "032x") == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert server[0].parent is not None and server[0].parent.is_remote
        assert format(server[0].parent.span_id, "016x") == "00f067aa0ba902b7"
        # the log context carries the shared trace id and the SERVER span's id,
        # not the upstream parent's
        assert body["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert body["span_id"] == format(server[0].context.span_id, "016x")
        assert body["span_id"] != "00f067aa0ba902b7"
    finally:
        FastAPIInstrumentor.uninstrument_app(app)


def test_middleware_falls_back_to_the_header_when_telemetry_is_off():
    body = TestClient(_tiny_app()).get("/probe", headers={"traceparent": TRACEPARENT}).json()
    assert body["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert body["span_id"] == "00f067aa0ba902b7"
    body = TestClient(_tiny_app()).get("/probe").json()
    assert body["trace_id"] is None and body["span_id"] is None and body["request_id"]


# -- job / stage spans -----------------------------------------------------------

def test_stage_spans_carry_identifiers_and_nest_under_the_job_span(in_memory):
    provider, exporter = in_memory
    with telemetry.span("rce.delivery_job", job_id="job-7", attempt=2,
                        **{telemetry.ALWAYS_SAMPLE_ATTRIBUTE: True}) as job_span:
        with telemetry.span("rce.stage.PARSING", job_id="job-7", stage="PARSING", attempt=2) as s:
            s.set_attribute("intake_id", "intake-9")
        with telemetry.span("rce.stage.QUALITY", job_id="job-7", intake_id="intake-9",
                            stage="QUALITY", attempt=2, password="never"):
            pass
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert set(spans) == {"rce.delivery_job", "rce.stage.PARSING", "rce.stage.QUALITY"}
    job = spans["rce.delivery_job"]
    assert dict(job.attributes) == {"job_id": "job-7", "attempt": 2,
                                    telemetry.ALWAYS_SAMPLE_ATTRIBUTE: True}
    for name in ("rce.stage.PARSING", "rce.stage.QUALITY"):
        assert spans[name].parent.span_id == job.context.span_id
        assert spans[name].attributes["job_id"] == "job-7"
        assert spans[name].attributes["intake_id"] == "intake-9"
        assert spans[name].attributes["attempt"] == 2
    assert spans["rce.stage.PARSING"].attributes["stage"] == "PARSING"
    assert spans["rce.stage.QUALITY"].attributes["stage"] == "QUALITY"
    assert "password" not in spans["rce.stage.QUALITY"].attributes


def test_stage_span_records_the_exception_and_reraises(in_memory):
    provider, exporter = in_memory
    with pytest.raises(ValueError):
        with telemetry.span("rce.stage.CURATION", job_id="job-8", stage="CURATION", attempt=1):
            raise ValueError("boom password=hunter2")
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is trace.StatusCode.ERROR
    assert span.events and span.events[0].name == "exception"


def test_span_is_a_no_op_when_telemetry_is_disabled():
    telemetry.use_tracer_provider(None)
    with telemetry.span("rce.stage.PARSING", job_id="job-1", stage="PARSING", attempt=1) as s:
        assert not s.is_recording()
        assert rc.current_trace_ids() == (None, None)


def test_delivery_runner_wraps_stages_in_spans():
    """Source-level check: the runner names the spans the docs and KQL rely on
    (the runner itself needs a database; its behaviour suites cover the rest)."""
    import inspect
    from app.tefca_registry.rce import delivery_runner
    src = inspect.getsource(delivery_runner)
    assert 'telemetry.span("rce.delivery_job"' in src
    assert 'telemetry.span("rce.stage.PARSING"' in src
    assert 'telemetry.span(f"rce.stage.{stage_name}"' in src
    assert 'telemetry.span(f"rce.stage.{RceDeliveryJob.STAGE_RECONCILIATION}"' in src
    assert "telemetry.ALWAYS_SAMPLE_ATTRIBUTE: True" in src


def test_admin_health_carries_the_telemetry_block(client):
    """The real app: an admin sees {enabled, reason, sampler, exporter} and
    never a connection string (the test process has none set)."""
    import uuid
    from app.core.database import get_db
    from app.core.security import create_access_token
    from app.main import app

    class _User:
        id = str(uuid.uuid4()); email = "admin@test.local"; role = "admin"
        is_active = True; status = "active"; tokens_revoked_at = None

    class _Result:
        def scalar_one_or_none(self): return _User()
        def scalar(self): return None

    class _Session:
        async def execute(self, *a, **k): return _Result()
        async def commit(self): return None
        async def rollback(self): return None
        async def close(self): return None

    async def _override():
        yield _Session()

    app.dependency_overrides[get_db] = _override
    try:
        token = create_access_token({"sub": _User.id, "role": "admin"}, is_admin=True)
        r = client.get("/api/admin/health", headers={"Authorization": f"Bearer {token}"})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 200, r.text
    block = r.json()["telemetry"]
    assert set(block) == {"enabled", "reason", "sampler", "exporter"}
    assert block["enabled"] is False and block["exporter"] == "none"
    assert "InstrumentationKey" not in r.text and "IngestionEndpoint" not in r.text
