# Connector Operational Monitoring

**Contract:** 7571MN26F80064

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL 16 (Azure Flexible Server) |
| Deployment | Azure App Service (Linux) |
| Build | Git SHA `706a2f641f3a48f3dc117f57d579ddc82dbd5686` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-02T01:08:45+00:00 |
| Contract | 7571MN26F80064 |

## Method

Each connector was called **5 times** directly against its authoritative
endpoint, in sequence, from the test workstation. "Uptime" below is the observed
success rate across those 5 calls during this testing window — it is **not** a
historical availability figure, and no long-run uptime is claimed. "Last
Failure" reads "None recorded" only where no failure occurred in this window.

## Summary

| Connector | Uptime | Last Success | Last Failure | Avg Latency |
|-----------|--------|-------------|--------------|-------------|
| NPPES | 5/5 (100.0%) | 2026-08-02T01:05:08+00:00 | None recorded | 391 ms |
| PECOS | 5/5 (100.0%) | 2026-08-02T01:05:12+00:00 | None recorded | 242 ms |
| OIG LEIE | 5/5 (100.0%) | 2026-08-02T01:05:17+00:00 | None recorded | 428 ms |
| SAM.gov | 0/5 (0.0%) | None recorded | 2026-08-02T01:05:22+00:00 | 252 ms |

## Latency detail

| Connector | Avg (ms) | Min (ms) | Max (ms) | HTTP codes | Notes |
|-----------|----------|----------|----------|------------|-------|
| NPPES | 391 | 291 | 541 | 200 | CMS NPI Registry — key-less |
| PECOS | 242 | 152 | 334 | 200 | Resolved through the same CMS NPI dataset; there is no separate key-less PECOS endpoint |
| OIG LEIE | 428 | 395 | 466 | 200 | HHS Exclusion List — public CSV, key-less |
| SAM.gov | 252 | 167 | 374 | 404 | Requires a registered api.data.gov key; DEMO_KEY is used here only to record the actual response |

## Interpretation

**NPPES, PECOS and OIG LEIE were 5/5 available** with sub-second average
latency. These are the three connectors that carry confidence scoring.

**PECOS resolves through the same CMS NPI dataset as NPPES** — there is no
separate key-less PECOS endpoint. Its timing is therefore correlated with NPPES
by construction, not independently sampled. Recorded as observed rather than
presented as an independent source.

**SAM.gov returned HTTP 404 on all 5 calls.** This is the documented behaviour
of `DEMO_KEY` against the entity-information API, not an outage: the endpoint
requires a registered api.data.gov key. SAM.gov is therefore **not operational**
and is excluded from confidence scoring. Note the key alone is necessary but not
sufficient — SAM is keyed on UEI, which the registry does not currently capture.

## Limitations

- Sample size is 5 calls per connector from a single client. Enough to establish
  order of magnitude and current reachability; not enough for an availability
  SLA or a tail-latency claim, and none is made.
- Latency includes network transit from the test workstation and third-party
  server time. It is not a measure of platform performance.
- No continuous monitoring is in place. These figures are a point-in-time
  measurement taken during this test window, not an ongoing uptime record.
- Historical uptime prior to this window: **Not Executed** — no monitoring
  history exists to report.
