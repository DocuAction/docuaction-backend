# Connector Health Matrix

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service |
| Build | Git SHA `706a2f641f3a48f3dc117f57d579ddc82dbd5686` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-01T22:11:55+00:00 |


| Connector | Status | Last Successful | Last Failure | Scoring Impact | Evidence File | Notes |
|-----------|--------|-----------------|--------------|----------------|---------------|-------|
| NPPES | Operational | 2026-08-01T22:11:55+00:00 | N/A | Included | CONNECTOR_RESPONSES.md | CMS NPI Registry |
| PECOS | Operational | 2026-08-01T22:11:55+00:00 | N/A | Included | CONNECTOR_RESPONSES.md | CMS Provider Enrollment |
| OIG LEIE | Operational | 2026-08-01T22:11:55+00:00 | N/A | Included | CONNECTOR_RESPONSES.md | HHS Exclusion List |
| SAM.gov | Pending | N/A | N/A | Excluded | N/A | Requires API key (api.data.gov); also keyed on UEI, which the registry does not capture |
| State Registries | Not Implemented | N/A | N/A | Excluded | N/A | Connector not built |
| IRS | Not Implemented | N/A | N/A | Excluded | N/A | Connector not built; keyed on EIN, which the registry does not capture |
| RCE Directory | Pending | N/A | N/A | Excluded | N/A | Case #00055525 with The Sequoia Project |

**"Excluded"** = not counted in confidence scoring (neither helps nor hurts).  
**"Operational"** = queried on every verification run.


## Why excluded sources do not reduce coverage

Coverage is measured against connectors that EXIST (`nppes`, `pecos`, `oig_leie`). Counting an unbuilt connector as a missing source would report permanently degraded coverage for work that was never scheduled — full coverage would be unreachable by construction, which makes the platform look broken rather than incomplete. Unimplemented sources are disclosed separately in every verification response and in the mandatory limitations section of every report.


Unbuilt connectors report `not_checked`, never `unavailable`. "Unavailable" implies a source that normally answers is temporarily down and will recover, which invites a retry; "not implemented" needs a decision.
