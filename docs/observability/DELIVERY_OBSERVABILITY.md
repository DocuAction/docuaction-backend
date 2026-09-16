# Delivery Workflow Observability (2026-09-17)

How a failed, stalled or disputed ONC/RCE delivery is found in the logs and in
the database, what every log line carries, and what an operator must configure
in Azure. **No Azure change was executed as part of this remediation**; section
8 lists the operator steps.

---

## 1. Structured log fields

`app/core/logging_config.py` emits one JSON object per line
(`JsonFormatter`). Every record carries:

| Field | Source | Meaning |
|---|---|---|
| `ts` | formatter | ISO-8601 UTC |
| `level`, `logger`, `message` | logging | standard |
| `request_id` | `RequestContextMiddleware` | preserved from a well-formed incoming `X-Request-ID`, else minted; echoed on every response |
| `trace_id`, `span_id` | W3C `traceparent` when present | correlation with an upstream tracer |
| `route`, `http_method`, `status_code`, `duration_ms` | middleware access line | one line per request; no query string, body or header is logged |
| `job_id`, `intake_id`, `stage`, `attempt` | `request_context.bind` in the delivery runner | which delivery and which stage attempt wrote the line |
| `report_id` | report generator | which report generation wrote the line |
| `git_sha`, `build_time`, `version`, `environment` | `request_context.build_identity()` | the exact build; `unknown` means a manual image |
| `exc_info` | runner error logs | every runner failure logs with a traceback |

Redaction: `logging_config.redact` removes tokens, passwords, connection
strings and record payloads; `safe_error` reduces an exception to class and
message. **No Government data value, secret or bearer token is ever logged.**

Evidence rows carry the same identity: `correlation_id` (request id, else job
id) and `build_sha` on every stage event, disposition, snapshot, identifier
decision and report link; `audit_logs.correlation_id` on `report_generated`
and `report_downloaded`.

## 2. Correlation model

```
X-Request-ID  ──►  request_id (log)  ──►  audit_logs.correlation_id
                                     └──►  rce_*.correlation_id (route-written evidence)
job_id        ──►  job_id (log)      ──►  rce_delivery_stage_events.job_id / correlation_id
                                     └──►  rce_reconciliation_snapshots.job_id
                                     └──►  rce_delivery_report_links.job_id
report_id     ──►  report_id (log)   ──►  audit_logs.resource_id, rce_delivery_report_links.report_id
```

A user who sees an error quotes the `X-Request-ID` from the response; the same
value is on the log line and on any evidence row that request wrote. A stage
written by the background runner carries the job id as its correlation id.

## 3. Where the logs are (App Service, Linux)

| Table | What lands there | Notes |
|---|---|---|
| `AppServiceConsoleLogs` | the JSON lines above (`ResultDescription`) | requires *Diagnostic settings → Application logging* to a Log Analytics workspace |
| `AppServiceHTTPLogs` | front-door HTTP access log (`CsUriStem`, `ScStatus`, `TimeTaken`) | independent of the application; useful when the app never answered |
| `AppServicePlatformLogs` | container start/stop, health-check restarts | a stalled job that died with its worker shows here |
| `traces` / `requests` / `exceptions` | App Insights, **if** the SDK/agent is enabled | not wired today (see memory: App Insights not wired); the KQL below is written for both |

Parse the JSON once per query:

```kusto
let lines = AppServiceConsoleLogs
| where TimeGenerated > ago(24h)
| extend j = parse_json(ResultDescription)
| where isnotempty(j.level)
| project TimeGenerated, level=tostring(j.level), logger=tostring(j.logger), message=tostring(j.message),
          request_id=tostring(j.request_id), job_id=tostring(j.job_id), intake_id=tostring(j.intake_id),
          stage=tostring(j.stage), attempt=toint(j.attempt), report_id=tostring(j.report_id),
          route=tostring(j.route), status_code=toint(j.status_code), duration_ms=toint(j.duration_ms),
          git_sha=tostring(j.git_sha), raw=ResultDescription;
```

## 4. KQL — the six questions

**Failed deliveries (last 24 h)**
```kusto
lines
| where logger == "app.tefca_registry.rce.stage_events" and message == "stage failed"
| summarize failed_at=min(TimeGenerated), stages=make_set(stage), attempts=max(attempt) by job_id, intake_id, git_sha
| order by failed_at desc
```
Database confirmation (evidence, not logs):
```sql
select job_id, stage, attempt, failure_class, left(failure_reason, 200), completed_at, build_sha
from rce_delivery_stage_events where status = 'FAILED' and started_at > now() - interval '24 hours'
order by completed_at desc;
```

**Stalled jobs** (started, no completion, no log line for 15 minutes)
```kusto
lines
| where logger == "app.tefca_registry.rce.stage_events"
| summarize last_seen=max(TimeGenerated), last_stage=arg_max(TimeGenerated, stage), completed=countif(message == "stage completed" and stage == "READY_FOR_REVIEW") by job_id
| where completed == 0 and last_seen < ago(15m)
```
```sql
select id, stage, state, heartbeat_at, now() - heartbeat_at as silent_for
from rce_delivery_jobs where state = 'RUNNING' and heartbeat_at < now() - interval '15 minutes';
select job_id, stage, attempt, started_at from rce_delivery_stage_events
where status = 'STARTED' and started_at < now() - interval '15 minutes';
```

**Reconciliation failures**
```kusto
lines
| where stage == "RECONCILIATION" and (message == "stage failed" or message has "reconcil")
| where level in ("ERROR", "WARNING")
| project TimeGenerated, job_id, intake_id, message, request_id, git_sha
```
```sql
select job_id, sequence, passed, failure_reason, received, created+updated+matched_unchanged+held+rejected+missing_key+excluded as accounted, created_at
from rce_reconciliation_snapshots where passed = false order by created_at desc;
```

**Report failures**
```kusto
lines
| where logger startswith "app.reports" and level == "ERROR"
| project TimeGenerated, report_id, message, request_id, git_sha
```
Signals to expect: `durable artifact registration FAILED`, `FAILED automated
accessibility checks`, `audit row (report_generated) FAILED`,
`rce_delivery_report_links write FAILED`, `REPORT_GENERATION stage event FAILED`.
```sql
-- reports generated with no link (a delivery report whose bookkeeping failed)
select a.resource_id as report_id, a.created_at, a.details->>'job_id' as job_id
from audit_logs a left join rce_delivery_report_links l on l.report_id = a.resource_id
where a.action = 'report_generated' and a.details->>'job_id' is not null and l.id is null;
```

**Correlation tracing** (one request or one job, end to end)
```kusto
let rid = "<X-Request-ID>";
lines | where request_id == rid or job_id == rid | order by TimeGenerated asc
```
```sql
select 'audit' as kind, created_at, action as what from audit_logs where correlation_id = :rid
union all select 'stage', started_at, stage || ':' || status from rce_delivery_stage_events where correlation_id = :rid
union all select 'snapshot', created_at, 'sequence ' || sequence from rce_reconciliation_snapshots where correlation_id = :rid
union all select 'report_link', generated_at, report_id from rce_delivery_report_links where correlation_id = :rid
order by 2;
```

**Authorization failures**
```kusto
lines
| where logger == "docuaction.request" and status_code in (401, 403)
| summarize n=count(), routes=make_set(route) by bin(TimeGenerated, 5m), status_code
| order by TimeGenerated desc
```
```kusto
AppServiceHTTPLogs | where ScStatus in (401, 403) and TimeGenerated > ago(1h)
| summarize n=count() by CsUriStem, ScStatus, bin(TimeGenerated, 5m)
```
```sql
select created_at, action, outcome, resource_type, details->>'route' as route, ip_address
from audit_logs where event_type in ('authentication', 'security') and outcome in ('failure', 'blocked', 'rejected')
and created_at > now() - interval '1 hour' order by created_at desc;
```

## 5. Sampling, retention, redaction, cost

- **Sampling.** None on the application log: a delivery is minutes of work
  producing tens of lines, and the stage lines are the evidence trail. If App
  Insights is enabled, keep adaptive sampling **off** for `traces` at WARNING
  and above and for anything carrying `job_id`; sample `requests` for `/health`
  only.
- **Retention.** Log Analytics: 90 days interactive is sufficient for
  operations; the durable evidence is in the database tables, not in the logs.
  Keep `audit_logs` and the five traceability tables under the database's
  retention (D8 remains a Government decision; nothing here sets it).
- **Redaction.** Enforced in code (`logging_config.redact`); do not add a
  workspace-side transformation that re-parses payloads. The access line has
  no query string by design.
- **Cost.** At the observed volume (one delivery of ~24k records ≈ a few
  hundred stage/quality lines plus access lines) ingestion is well under
  1 GB/day per environment. Health probes log at DEBUG and are excluded by the
  default INFO level.

## 6. Alert thresholds (proposed; not configured)

| Alert | Condition | Severity |
|---|---|---|
| Delivery failed | any `stage failed` line, or `rce_delivery_jobs.state='FAILED'` | Sev 2 |
| Delivery stalled | RUNNING job with `heartbeat_at` older than 15 min | Sev 2 |
| Reconciliation did not pass | snapshot with `passed=false` | Sev 2 |
| Report bookkeeping failed | any `app.reports` ERROR containing `FAILED` | Sev 3 |
| Report without link | the "reports generated with no link" query returns rows | Sev 3 |
| Authorization spike | > 20 × 401/403 in 5 min from one IP, or any 403 on `/api/admin/*` | Sev 3 |
| Unknown build | any line with `git_sha == "unknown"` in PROD | Sev 3 |

## 7. Health

`GET /health` (public): `status, service, version, git_sha, build_time,
environment, modules`. `GET /api/admin/health` (admin) adds
`migration_revision`, `database{reachable, latency_ms}`, scheduler, connectors,
USPS, program profile and `request_id`. The migration revision must equal
`20260917_delivery_traceability` on a converged environment; the report's
identity block prints the same value.

## 8. Azure operator steps (NOT executed in this remediation)

1. App Service → *Diagnostic settings* → send `AppServiceConsoleLogs`,
   `AppServiceHTTPLogs`, `AppServicePlatformLogs` to the Log Analytics
   workspace (DEV and PROD separately).
2. Set `LOG_LEVEL=INFO` and confirm `configure_logging()` runs at startup
   (JSON lines visible in *Log stream*).
3. Save the section-4 queries as workspace functions; create the section-6
   alert rules with an action group that reaches Data Operations.
4. Optional: enable Application Insights (agent-based) and set
   `APPLICATIONINSIGHTS_CONNECTION_STRING`; keep sampling off for WARNING+.
5. Confirm `GIT_SHA` / `BUILD_TIME` are set by the release workflow (a
   `git_sha == "unknown"` in PROD is an alert, not a default).

---

## 9. Implementation (2026-09-17): OpenTelemetry / Azure Monitor export

`app/core/telemetry.py` adds distributed tracing on top of the JSON log lines
above: FastAPI server spans, asyncpg client spans and pipeline spans
(`rce.delivery_job`, `rce.stage.<STAGE>`), exported to Application Insights
through the Azure Monitor distro (`azure-monitor-opentelemetry==1.8.10`,
`opentelemetry-instrumentation-fastapi==0.65b0`,
`opentelemetry-instrumentation-asyncpg==0.65b0`, SDK/API 1.44.0; pinned in
`requirements.txt`). `configure_telemetry(app)` runs once at import in
`app/main.py`, after every other middleware. **Nothing is exported unless an
operator sets the two variables below; no Azure change was made here.**

### 9.1 Environment variables

| Variable | Effect | Default |
|---|---|---|
| `OTEL_ENABLED` | `true` turns tracing on. Anything else: off, with reason `OTEL_ENABLED is not true`. | off |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Must be **present** for tracing to start; read by the distro directly from the environment. Its value is never passed as an argument, logged, or returned by any endpoint; `/api/admin/health` only implies its presence through `telemetry.enabled`. | unset, reason `APPLICATIONINSIGHTS_CONNECTION_STRING is not set` |
| `OTEL_TRACES_SAMPLER_ARG` | Root-span sampling ratio in `[0, 1]`. Out of range or unparsable: default. | `0.2` |
| `OTEL_SERVICE_NAME` | Overrides `service.name` on the resource. | `docuaction-backend` |

Resource attributes on every span: `service.name`, `service.version` (6.0.0),
`deployment.environment` (`ENVIRONMENT`), `git.sha` (`GIT_SHA`), `build.time`
(`BUILD_TIME`). `unknown` is reported, never guessed, as on `/health`.

Both switches are required so that a connection string left in an App Service
slot cannot start exporting on its own, and a flag flipped without a
destination fails closed rather than buffering to disk.

### 9.1a What leaves the process (redaction points)

Three export paths exist when telemetry is on, and each is redacted
independently (independent review M3/M4, 2026-09-16):

| Path | Redaction |
|---|---|
| Span attributes | `RedactingSpanProcessor` (start and end): headers dropped, query strings stripped, credential-shaped keys masked, SQL literals stripped |
| Span `exception` events | `telemetry.span` records the exception itself with `record_exception=False`: class name plus `safe_exception_text` (domain message redacted; driver/library message withheld); **no stack trace is exported** |
| Log records (`docuaction*` loggers via the distro's `LoggingHandler`) | `RedactingLogRecordProcessor` registered through `log_record_processors`: body through `redact_text`, attributes through the span rules, `exception.stacktrace` withheld, `exception.message` redacted |

Stack traces stay in the container log (stdout JSON, itself redacted) under
the correlation id.

### 9.2 Sampling

`ErrorKeepingSampler` (parent-based `TraceIdRatioBased`), installed on the
distro's `TracerProvider` after `configure_azure_monitor` returns (the distro
accepts a sampler by name only, not an object):

1. a span whose **name or initial attributes mark an error** (`error=true`,
   `exception.*`, `otel.status_code=ERROR`, HTTP status >= 500, or a name
   containing error/fail/exception) is **always kept**, whatever its parent.
   **Limit (independent review M2, 2026-09-16):** the decision is made when
   the span STARTS. A FastAPI server span learns its status only at its end,
   so a request that fails with a 5xx is kept at the ratio, not always. The
   redacted error LOG line is exported for every 5xx regardless. On DEV set
   `OTEL_TRACES_SAMPLER_ARG=1.0` (the volume is small) so every request trace
   is kept; a lower ratio is a production cost decision, not a DEV default;
2. a span carrying `docuaction.always_sample=true` is always kept. The
   `rce.delivery_job` root sets it, so every delivery's stage spans are
   exported in full (they are the evidence trail; section 5 already says the
   job lines are not to be sampled);
3. otherwise a child **follows its parent's** sampled flag (remote `traceparent`
   or local), so traces are whole or absent, never partial;
4. a root span follows the ratio.

Limitation, stated plainly: a span that only becomes an error **after** it
started (a 500 decided in the handler) is sampled by rule 3/4 at start; its
error is still in the JSON log (unsampled) and in `exceptions` if the request
was sampled. Rule 1 covers spans created *because of* an error and rule 2
covers the pipeline, which is where the Government evidence lives.

### 9.3 Redaction

`RedactingSpanProcessor` is registered ahead of the exporter and scrubs
attributes when a span starts and again when it ends (attributes set during
the span are only visible at the end):

| Attribute | Treatment |
|---|---|
| key matching `logging_config._SENSITIVE_KEY` (token, secret, password, authorization, api_key, connection_string, cookie, client_secret, sas, signature) | value becomes `[REDACTED]` |
| `http.request.header.*`, `http.response.header.*` | removed |
| `http.url`, `url.full`, `http.target` | query string and fragment stripped |
| `url.query`, `db.statement.parameters`, request/response bodies | removed |
| `db.statement`, `db.query.text` | quoted strings and numeric literals become `?`; `$n` placeholders kept |
| any other string | `logging_config.redact_text` (bearer tokens, `key=value` credentials) |

`telemetry.span(name, **attrs)` additionally drops `None`, drops
credential-shaped keys before the span exists, stringifies non-primitives and
caps strings at 256 characters. Pipeline spans carry `job_id`, `intake_id`,
`stage`, `attempt` and nothing else: **no token, password, connection string,
file content, record payload or PII is ever placed on a span.** The asyncpg
instrumentation runs with `capture_parameters=False`.

### 9.4 Retention and cost estimate (DEV)

Assumptions: ~2,000 requests/day, 20 % root sampling, health probes excluded
from server spans, 90-day retention (Log Analytics interactive default is 30;
90 is the operations figure from section 5).

```
requests exported        = 2,000 x 0.20                      =   400 / day
dependency spans (~4 DB calls per request, follow the parent)
                         = 400 x 4                            = 1,600 / day
pipeline spans (~2 deliveries/day x 7 spans, always kept)    =    14 / day
exceptions (all kept)    ~ 1 % of requests                    =    20 / day
total rows               ~ 2,034 / day  ~ 61,000 / month
bytes per row (App Insights average, requests/dependencies)  ~ 1.5 KB
ingestion                ~ 2,034 x 1.5 KB ~ 3.0 MB/day ~ 0.09 GB/month
```

Application Insights (workspace-based) includes 5 GB/month of ingestion at no
charge, after which ingestion is about $2.30/GB (pay-as-you-go list price;
verify against the current price sheet for the subscription's region). At
0.09 GB/month DEV stays inside the free grant; the 90-day retention costs
about $0.10/GB/month beyond the included 31 days (`0.09 GB x $0.10 x 2 = $0.02`).
Order of magnitude: **under $1/month for DEV.** If PROD volume is 10x DEV
(20k requests/day) the same arithmetic gives about 0.9 GB/month, still inside
the grant. The JSON console log (section 5) is unchanged and remains the
larger stream; it is not duplicated into `traces` because the distro's logging
handler is scoped to the `docuaction` logger name and log export is not
enabled by this increment.

### 9.5 Safe failure

`configure_telemetry` never raises. Any exception while importing the distro,
configuring it, or instrumenting is logged **once** at WARNING with the
traceback (through the JSON formatter, so the message is redacted) and the
application starts with `telemetry.enabled=false` and reason
`configuration failed (see WARNING log)`. With tracing off,
`opentelemetry.trace.get_tracer` hands out no-op spans, so every
`telemetry.span` block in the runner is a plain `with` block: **no behaviour
change in the delivery pipeline.** `RequestContextMiddleware` binds
`trace_id`/`span_id` from the active span when there is one and falls back to
parsing the inbound `traceparent` when there is not, so the log fields in
section 1 behave identically in both modes. When tracing is on, the
OpenTelemetry middleware sits outside `RequestContextMiddleware` (it is added
last), which is what lets the log line carry the server span's ids.

`GET /api/admin/health` reports
`telemetry: {enabled, reason, sampler, exporter}`; `sampler` is the sampler's
description string (`ErrorKeepingParentBasedTraceIdRatio{0.2}`), `exporter`
is `azure_monitor` or `none`.

### 9.6 KQL joins on `operation_Id`

App Insights maps the W3C trace id to `operation_Id` on `requests`,
`dependencies`, `exceptions` and `traces`. The JSON log line's `trace_id` is
the same 32-hex value, so console logs join to traces:

```kusto
// one request, end to end: server span, DB calls, exceptions, console log lines
let op = "<trace_id from a JSON log line or X-Request-ID lookup>";
union
  (requests     | where operation_Id == op | project timestamp, kind="request",    name, duration, resultCode),
  (dependencies | where operation_Id == op | project timestamp, kind="dependency", name, duration, resultCode),
  (exceptions   | where operation_Id == op | project timestamp, kind="exception",  name=type, duration=0.0, resultCode=""),
  (AppServiceConsoleLogs | extend j = parse_json(ResultDescription) | where tostring(j.trace_id) == op
     | project timestamp=TimeGenerated, kind="log", name=tostring(j.message), duration=0.0, resultCode=tostring(j.level))
| order by timestamp asc
```

```kusto
// every delivery job span with its stages and durations (always sampled)
dependencies
| where name startswith "rce.stage." or name == "rce.delivery_job"
| extend job_id = tostring(customDimensions.job_id), stage = tostring(customDimensions.stage),
         attempt = toint(customDimensions.attempt), intake_id = tostring(customDimensions.intake_id)
| summarize started=min(timestamp), total_ms=sum(duration), failed=countif(success == false),
            stages=make_list(pack("stage", stage, "ms", duration, "ok", success)) by job_id, operation_Id
| order by started desc
```

```kusto
// from an X-Request-ID to the trace: the console line carries both ids
AppServiceConsoleLogs
| extend j = parse_json(ResultDescription)
| where tostring(j.request_id) == "<X-Request-ID>"
| distinct trace_id = tostring(j.trace_id)
| join kind=inner (requests) on $left.trace_id == $right.operation_Id
```

```kusto
// slow database calls behind a route, with the statement shape (literals stripped)
dependencies
| where type == "postgresql" or name startswith "SELECT" or name startswith "INSERT" or name startswith "UPDATE"
| join kind=inner (requests | project operation_Id, route=name) on operation_Id
| where duration > 500
| project timestamp, route, statement=tostring(customDimensions["db.statement"]), duration
| order by duration desc
```

### 9.7 Operator steps for this increment (NOT executed)

1. Create or reuse a workspace-based Application Insights resource per
   environment; copy its connection string into the App Service setting
   `APPLICATIONINSIGHTS_CONNECTION_STRING` (Key Vault reference preferred).
2. Set `OTEL_ENABLED=true`; optionally `OTEL_TRACES_SAMPLER_ARG` (default 0.2).
3. Confirm on `GET /api/admin/health` that `telemetry.enabled` is `true` and
   `telemetry.exporter` is `azure_monitor`; confirm one `requests` row appears
   within five minutes and that no `customDimensions` key carries a header,
   a query string or a credential.
4. Leave adaptive sampling in the portal **off**: sampling is decided in the
   process by section 9.2 and a second sampler would drop always-kept spans.
