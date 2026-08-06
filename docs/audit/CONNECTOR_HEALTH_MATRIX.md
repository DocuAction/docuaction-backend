# Connector Health Matrix

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service |
| Build | Git SHA `7e2ca47e3d5e80db0d89ec776c7ab23455a129bf` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-02T19:25:50.360026+00:00 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| httpx | 0.28.1 |

| Connector | Status | Last Successful | Last Failure | Scoring Impact | Evidence File | Notes |
|---|---|---|---|---|---|---|
| NPPES | Operational | 2026-08-02T19:25:50.360026+00:00 | N/A | Included | CONNECTOR_RESPONSES.md | CMS NPI Registry |
| PECOS | Operational | 2026-08-02T19:25:50.360026+00:00 | N/A | Included | CONNECTOR_RESPONSES.md | CMS Provider Enrollment (same CMS endpoint as NPPES) |
| OIG LEIE | Operational | 2026-08-02T19:25:52.002883+00:00 | N/A | Included | CONNECTOR_RESPONSES.md | HHS OIG exclusions CSV |
| SAM.gov | Not Operational | N/A | 2026-08-02T18:59:52+00:00 | **Excluded** | (see notes) | Key valid (api.data.gov quota 1000/hr vs DEMO_KEY 10); entity/exclusions endpoints return empty HTTP 404 at SAM ingress (`server: istio-envoy`, no gateway headers). Key present in prod app settings, **absent in dev**. |
| State Registries | Not Implemented | N/A | N/A | Excluded | N/A | Connector not built |
| IRS | Not Applicable | N/A | N/A | Excluded | N/A | No public API exists for for-profit entity verification. IRS TEOS covers only tax-exempt organizations (501(c)(3)). IRS data is additionally keyed on EIN, which the registry does not hold. This is a permanent gap, not an unbuilt connector — it will not be scheduled. |
| RCE Directory | ONC-Provided | N/A | N/A | **Excluded** | N/A | Data provided by HHS/ONC. Direct access not authorized. Case #00055525 |

**"Excluded"** = not counted in confidence scoring (neither helps nor hurts).  
**"Operational"** = queried on every verification run.

## IQVIA OneKey — removed from the health probe (2026-08-05)

IQVIA_ONEKEY is no longer probed by the connector health check. It has no key and is pending federal ODC, so it reported `UNAVAILABLE` on every health call — which reads as an outage when nothing is wrong. A connector that was never provisioned is not a health finding.

The `IQVIAOneKeyConnector` class is retained and unchanged; only its participation in the health probe was removed. It re-enables in one line when a key is issued.

## SAM.gov — status change from the previous matrix

The prior matrix recorded SAM.gov as *Pending — requires API key*. That is no longer accurate. A key is now provisioned and **the key itself is valid**: against the api.data.gov gateway it returns `x-ratelimit-limit: 1000`, where `DEMO_KEY` returns `10`. The failure is downstream of the key — every Entity Management and Exclusions path returns an empty `HTTP 404` at SAM's own ingress (`server: istio-envoy`) without ever reaching the authenticating gateway. A deliberately invalid key produces a byte-identical response, which is why earlier sprints read this as a missing-key problem.

It remains **Not Operational and excluded from scoring** — it returns no data. But the remediation is an endpoint/entitlement question (likely a SAM System Account with registered IPs), not another key request.

~~A second, separate defect: `SAM_GOV_API_KEY` is set on the **prod** App Service only. `docuaction-dev` does not have it.~~

**Corrected 2026-08-05.** `SAM_GOV_API_KEY` is now present on **both** `docuaction-dev` and `Docuaction` (prod), verified directly against App Service settings. A 40-character key is set on each and neither is `DEMO_KEY`. The dev-key gap recorded above has been closed; the 404 persists independently of it, which further supports the entitlement diagnosis rather than a credential one.

## Why excluded sources do not reduce coverage

Coverage is measured against connectors that EXIST (`nppes`, `pecos`, `oig_leie`). Counting an unbuilt connector as a missing source would report permanently degraded coverage for work that was never scheduled — full coverage would be unreachable by construction, which makes the platform look broken rather than incomplete. Unimplemented sources are disclosed separately in every verification response and in the mandatory limitations section of every report.

Unbuilt connectors report `not_checked`, never `unavailable`. "Unavailable" implies a source that normally answers is temporarily down and will recover, which invites a retry; "not implemented" needs a decision.
