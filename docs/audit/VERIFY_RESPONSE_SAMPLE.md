# Verification Response Sample

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


**Entity:** Inova Fairfax Hospital  
**Entity ID:** `74452505-01f5-49bc-8b8f-17aa82268a9d`


`POST /api/tefca/registry/entities/{id}/verify` -> HTTP 200


## Per-source verification status

| Source | Status | Reason / Label |
|--------|--------|----------------|
| sam_gov | `not_checked` | Connector implemented but not operational — API key required (free registration at api.data.gov). Also keyed on UEI, which the registry does not currently hold. |
| state_registry | `not_checked` | Connector not implemented |
| irs | `not_checked` | Connector not implemented — IRS data is keyed on EIN, which the registry does not currently hold |
| nppes | `verified` | NPI Registry — CMS/HHS |
| pecos | `verified` | Provider Enrollment — CMS |
| oig_leie | `clear` | Exclusion List — OIG/HHS |

## Classification

| Field | Value |
|-------|-------|
| bucket | B1 |
| rule_code | RULE-001 |
| rule_version | 1 |
| rule_name | B1 No Discrepancy |
| classified_at | 2026-08-01T22:08:46.914953Z |
| rationale | B1 No Discrepancy (RULE-001 v1): nppes is verified; oig_leie is clear; pecos is verified |

## Coverage

| Field | Value |
|-------|-------|
| sources_checked | 3 |
| sources_available | 3 |
| sources_verified | 3 |
| sources_unavailable | 0 |
| sources_not_checked | 0 |
| sources_failed | 0 |
| sources_not_implemented | 3 |
| not_implemented | ['irs', 'sam_gov', 'state_registry'] |
| coverage_note | 3 of 3 implemented sources checked. Not implemented (excluded from coverage): irs, sam_gov, state_registry. |

## Full response

```json
{
  "entity_id": "74452505-01f5-49bc-8b8f-17aa82268a9d",
  "review_id": "REV-2026-000019",
  "verification": {
    "sam_gov": {
      "status": "not_checked",
      "reason": "Connector implemented but not operational \u2014 API key required (free registration at api.data.gov). Also keyed on UEI, which the registry does not currently hold.",
      "label": "Federal Registration \u2014 GSA"
    },
    "state_registry": {
      "status": "not_checked",
      "reason": "Connector not implemented",
      "label": "State licensure registry"
    },
    "irs": {
      "status": "not_checked",
      "reason": "Connector not implemented \u2014 IRS data is keyed on EIN, which the registry does not currently hold",
      "label": "IRS Exempt Organizations"
    },
    "nppes": {
      "status": "verified",
      "label": "NPI Registry \u2014 CMS/HHS",
      "verified_at": "2026-08-01T22:08:39.454713Z",
      "lookup_identifier": "1770626038"
    },
    "pecos": {
      "status": "verified",
      "label": "Provider Enrollment \u2014 CMS",
      "verified_at": "2026-08-01T22:08:39.623328Z",
      "lookup_identifier": "1770626038"
    },
    "oig_leie": {
      "status": "clear",
      "label": "Exclusion List \u2014 OIG/HHS",
      "exclusion_count": 0,
      "verified_at": "2026-08-01T22:08:46.497189Z",
      "lookup_identifier": "1770626038"
    }
  },
  "classification": {
    "bucket": "B1",
    "rule_code": "RULE-001",
    "rule_version": 1,
    "rule_name": "B1 No Discrepancy",
    "rationale": "B1 No Discrepancy (RULE-001 v1): nppes is verified; oig_leie is clear; pecos is verified",
    "matched_conditions": [
      "nppes is verified",
      "oig_leie is clear",
      "pecos is verified"
    ],
    "evidence_summary": {
      "sources_total": 6,
      "sources_checked": 3,
      "sources_verified": 3,
      "sources_not_found": 0,
      "sources_unavailable": 0,
      "sources_not_checked": 3,
      "sources_failed": 0,
      "by_state": {
        "verified": 2,
        "not_found": 0,
        "not_checked": 3,
        "unavailable": 0,
        "failed": 0,
        "clear": 1,
        "excluded": 0
      }
    },
    "evaluated_rules": [
      "RULE-005v1",
      "RULE-001v1"
    ],
    "classified_at": "2026-08-01T22:08:46.914953Z"
  },
  "confidence": {
    "sources_checked": 3,
    "sources_available": 3,
    "sources_verified": 3,
    "sources_unavailable": 0,
    "sources_not_checked": 0,
    "sources_failed": 0,
    "sources_not_implemented": 3,
    "not_implemented": [
      "irs",
      "sam_gov",
      "state_registry"
    ],
    "coverage_note": "3 of 3 implemented sources checked. Not implemented (excluded from coverage): irs, sam_gov, state_registry."
  },
  "findings": {
    "entities_verified": 1,
    "jobs": 1,
    "findings_created": 0,
    "findings_by_severity": {},
    "external_included": false
  },
  "transition": null,
  "operational_status": "pending_verification"
}
```
