# Dynamic Application Security Testing (DAST) — Execution Record

**Contract:** 7571MN26F80064

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service (Linux) |
| Build | Git SHA `706a2f641f3a48f3dc117f57d579ddc82dbd5686` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-01T22:33:41+00:00 |
| Contract | 7571MN26F80064 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| pytest | pytest 9.1.1 |
| Bandit | __main__.py 1.9.4 |
| openapi-spec-validator | 0.9.0 |
| curl | curl 8.21.0 (Windows) libcurl/8.21.0 Schannel zlib/1.3.2 WinIDN WinLDAP |
| OWASP ZAP | Not Available — see ZAP_FINDING_VALIDATION.md |

## Status: NOT EXECUTED

Dynamic Application Security Testing using OWASP ZAP — a scanner widely used in
federal secure development workflows — **was not executed.** It is recorded here
as Not Executed rather than reported with assumed or illustrative findings.

### Blockers

| # | Blocker | Detail |
|---|---------|--------|
| 1 | No container runtime | Docker is not installed on the test workstation, so the official `zaproxy/zap-stable` image cannot be run. |
| 2 | No Java runtime | The ZAP desktop/daemon distribution requires a JRE, which is not present. |
| 3 | `zapv2` is a client only | The installable Python package is an API *client*. It requires a running ZAP daemon to talk to; it does not contain a scanner. |

### Scope constraint (independent of the blockers)

Per the governing instruction, DAST is authorised against the **development
environment only** (https://docuaction-dev.azurewebsites.net). Production is out of scope and was not scanned.

### What would unblock this

1. A workstation or CI runner with Docker, then:
   `docker run -t zaproxy/zap-stable zap-baseline.py -t https://docuaction-dev.azurewebsites.net -r zap.html`
2. Or a JRE plus the ZAP daemon, driven through `zapv2`.
3. Either path needs the dev environment reachable from the runner and the
   per-IP rate limit temporarily raised — the active limit (20 login attempts /
   15 min per IP) will otherwise throttle an active scan and produce
   false negatives.

### Compensating coverage actually performed

DAST was not run, but the following **was** executed against dev and is reported
with real results:

| Activity | Result | Evidence |
|----------|--------|----------|
| Security Validation (36 tests) | 36 PASS / 0 FAIL / 0 Not Executed | `AGT-SA-001` |
| Static analysis (Bandit) | See `AGT-SA-001` | `AGT-SA-001` |
| API contract validation | See `API_CONTRACT_VALIDATION.md` | Block 7 |

**This is compensating coverage, not a substitute.** Security Validation
exercises paths chosen deliberately; a DAST crawler exercises paths nobody
thought to choose. The two find different things, and the gap remains open until
a scan is run.

## Finding Validation Log

No DAST findings were produced, so none were validated. The finding workflow
(detect → reproduce manually → confirm exploitability → fix only if confirmed →
re-test) was applied to the Security Validation findings instead; that log is in
`AGT-SA-001`.
