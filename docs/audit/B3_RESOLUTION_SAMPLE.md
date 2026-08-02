# B3 Manual Resolution Sample

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
| Test Date (UTC) | 2026-08-01T22:08:31+00:00 |


No pending B3 review existed at audit time; creating one by verifying a synthetic-NPI entity (which has no NPPES record).


**Review ID:** `REV-2026-000020`


## Before

| Field | Value |
|-------|-------|
| bucket | B3 |
| rule | RULE-004 |
| reviewer_resolution | None |
| effective_bucket | B3 |

`PATCH /api/tefca/arc/reviews/REV-2026-000020/resolve` -> HTTP 200


## After

| Field | Value |
|-------|-------|
| reviewer_resolution | reclassified |
| reclassified_to | B2 |
| effective_bucket | B2 |
| resolution_rationale | Audit sample: administrative name variance confirmed with participant (DBA vs legal name). |
| reclassified_at | None |