# B3 Manual Resolution Sample

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
| Test Date (UTC) | 2026-08-02T19:25:48.706724+00:00 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| httpx | 0.28.1 |

B3 means the rule set could not explain the evidence, so the engine cannot close it. Resolution requires an explicit human decision with a mandatory rationale.

`PATCH /api/tefca/arc/reviews/REV-2026-000031/resolve` -> HTTP 200

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| 1.4 | Resolve a B3 review with a human decision | HTTP 200, `reviewer_resolution` set | HTTP 200, resolution = `confirmed` | PASS |

## Before

```json
{
  "review_id": "REV-2026-000031",
  "entity_id": "4a0af755-d556-423e-bd72-5ac7813637c1",
  "sample_id": null,
  "classification": {
    "bucket": "B3",
    "rule_code": "RULE-004",
    "rule_version": 1,
    "rationale": "B3 Inexplicable (RULE-004 v1): nppes is not_found"
  },
  "reviewer_resolution": null,
  "reclassified_to": null,
  "resolution_rationale": null,
  "reviewed_at": "2026-08-01T23:07:35.316278",
  "created_at": "2026-08-01T23:07:34.833038",
  "effective_bucket": "B3"
}
```

## Resolve response

```json
{
  "review_id": "REV-2026-000031",
  "entity_id": "4a0af755-d556-423e-bd72-5ac7813637c1",
  "sample_id": null,
  "classification": {
    "bucket": "B3",
    "rule_code": "RULE-004",
    "rule_version": 1,
    "rationale": "B3 Inexplicable (RULE-004 v1): nppes is not_found"
  },
  "reviewer_resolution": "confirmed",
  "reclassified_to": null,
  "resolution_rationale": "Block 1.4 audit evidence: NPPES returned not_found for this identifier and no other source contradicts it. B3 confirmed rather than reclassified because the evidence remains unexplained, which is itself the finding.",
  "reviewed_at": "2026-08-02T19:25:50.544836",
  "created_at": "2026-08-01T23:07:34.833038",
  "effective_bucket": "B3"
}
```

## After (re-read)

```json
{
  "review_id": "REV-2026-000031",
  "entity_id": "4a0af755-d556-423e-bd72-5ac7813637c1",
  "sample_id": null,
  "classification": {
    "bucket": "B3",
    "rule_code": "RULE-004",
    "rule_version": 1,
    "rationale": "B3 Inexplicable (RULE-004 v1): nppes is not_found"
  },
  "reviewer_resolution": "confirmed",
  "reclassified_to": null,
  "resolution_rationale": "Block 1.4 audit evidence: NPPES returned not_found for this identifier and no other source contradicts it. B3 confirmed rather than reclassified because the evidence remains unexplained, which is itself the finding.",
  "reviewed_at": "2026-08-02T19:25:50.544836",
  "created_at": "2026-08-01T23:07:34.833038",
  "effective_bucket": "B3",
  "verification_results": {
    "fields": {
      "npi_validation": "valid",
      "nppes_pecos_conflict": false,
      "multiple_source_conflict": false
    },
    "sources": {
      "irs": {
        "label": "IRS Exempt Organizations",
        "reason": "Connector not implemented \u2014 IRS data is keyed on EIN, which the registry does not currently hold",
        "status": "not_checked"
      },
      "nppes": {
        "label": "NPI Registry \u2014 CMS/HHS",
        "status": "not_found",
        "verified_at": "2026-08-01T23:07:35.076829Z",
        "lookup_identifier": "2000000051"
      },
      "pecos": {
        "label": "Provider Enrollment \u2014 CMS",
        "status": "not_found",
        "verified_at": "2026-08-01T23:07:35.223267Z",
        "lookup_identifier": "2000000051"
      },
      "sam_gov": {
        "label": "Federal Registration \u2014 GSA",
        "reason": "Connector implemented but not operational \u2014 API key required (free registration at api.data.gov). Also keyed on UEI, which the registry does not currently hold.",
        "status": "not_checked"
      },
      "oig_leie": {
        "label": "Exclusion List \u2014 OIG/HHS",
        "status": "clear",
        "verified_at": "2026-08-01T23:07:35.223671Z",
        "exclusion_count": 0,
        "lookup_identifier": "2000000051"
      },
      "state_registry": {
        "label": "State licensure registry",
        "reason": "Connector not implemented",
        "status": "not_checked"
      }
    },
    "confidence_score": null
  }
}
```
