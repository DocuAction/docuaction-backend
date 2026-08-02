# Weekly Report Sample

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


`POST /api/tefca/arc/reports/generate` -> HTTP 200  
**Report ID:** `WR-2026-W31-R5EF9`


## Executive Summary

| Metric | Value |
|--------|-------|
| entities_reviewed | 19 |
| discrepancies_found | 9 |
| discrepancy_rate | 0.473684 |
| b3_pending_manual_review | 0 |
| b4_requiring_action | 8 |

## Classification Distribution

| Bucket | Count |
|--------|-------|
| B1 | 10 |
| B2 | 1 |
| B3 | 0 |
| B4 | 8 |

## Limitations (mandatory section)

- sam_gov: Unavailable — API key not provisioned (SAM_GOV_API_KEY)
- rce_directory: Not checked — access pending Case #00055525
- state_registry: Not checked — no connector implemented
- irs: Not checked — no connector implemented; IRS data is keyed on EIN, which the registry does not currently hold

## Full report JSON

```json
{
  "report_type": "weekly",
  "contract": "7571MN26F80064",
  "period": {
    "start": "2026-07-25",
    "end": "2026-08-01"
  },
  "generated_at": "2026-08-01T22:08:48.532543Z",
  "executive_summary": {
    "entities_reviewed": 19,
    "discrepancies_found": 9,
    "discrepancy_rate": 0.473684,
    "b3_pending_manual_review": 0,
    "b4_requiring_action": 8
  },
  "sampling_summary": {
    "note": "No sample was drawn for this period; the report covers all reviews completed in the window."
  },
  "classification_distribution": {
    "counts": {
      "B1": 10,
      "B2": 1,
      "B3": 0,
      "B4": 8
    },
    "labels": {
      "B1": "No Discrepancy",
      "B2": "Minor / Administrative",
      "B3": "Inexplicable \u2014 manual review",
      "B4": "Non-Compliant"
    },
    "review_ids": {
      "B1": [
        "REV-2026-000004",
        "REV-2026-000011",
        "REV-2026-000012",
        "REV-2026-000013",
        "REV-2026-000014",
        "REV-2026-000015",
        "REV-2026-000016",
        "REV-2026-000017",
        "REV-2026-000018",
        "REV-2026-000019"
      ],
      "B2": [
        "REV-2026-000005"
      ],
      "B3": [],
      "B4": [
        "REV-2026-000001",
        "REV-2026-000002",
        "REV-2026-000003",
        "REV-2026-000006",
        "REV-2026-000007",
        "REV-2026-000008",
        "REV-2026-000009",
        "REV-2026-000010"
      ]
    }
  },
  "discrepancy_rate": {
    "rate": 0.473684,
    "lower": 0.273295,
    "upper": 0.682925,
    "method": "wilson",
    "confidence": 0.95,
    "n": 19,
    "successes": 9
  },
  "verification_coverage": {
    "sam_gov": {
      "verified": 0,
      "not_found": 0,
      "not_checked": 19,
      "unavailable": 0,
      "failed": 0
    },
    "state_registry": {
      "verified": 0,
      "not_found": 0,
      "not_checked": 19,
      "unavailable": 0,
      "failed": 0
    },
    "irs": {
      "verified": 0,
      "not_found": 0,
      "not_checked": 19,
      "unavailable": 0,
      "failed": 0
    },
    "nppes": {
      "verified": 13,
      "not_found": 6,
      "not_checked": 0,
      "unavailable": 0,
      "failed": 0
    },
    "pecos": {
      "verified": 13,
      "not_found": 6,
      "not_checked": 0,
      "unavailable": 0,
      "failed": 0
    },
    "oig_leie": {
      "verified": 16,
      "not_found": 0,
      "not_checked": 0,
      "unavailable": 0,
      "failed": 0
    }
  },
  "outstanding_items": {
    "b3_pending_manual_review": {
      "count": 0,
      "review_ids": []
    },
    "b4_requiring_action": {
      "count": 8,
      "review_ids": [
        "REV-2026-000001",
        "REV-2026-000002",
        "REV-2026-000003",
        "REV-2026-000006",
        "REV-2026-000007",
        "REV-2026-000008",
        "REV-2026-000009",
        "REV-2026-000010"
      ]
    },
    "resolved_this_period": 1
  },
  "data_sources_used": [
    "irs",
    "nppes",
    "oig_leie",
    "pecos",
    "sam_gov",
    "state_registry"
  ],
  "methodology": {
    "sample_size_formula": "Cochran, with finite population correction",
    "interval_method": "Wilson score interval",
    "interval_note": "Wilson rather than the normal approximation: at these sample sizes and rates the normal interval can extend below zero, which is not a reportable figure.",
    "bucket_definitions": {
      "B1": "No Discrepancy",
      "B2": "Minor / Administrative",
      "B3": "Inexplicable \u2014 manual review",
      "B4": "Non-Compliant"
    },
    "discrepancy_definition": "Any review not classified B1. B3 is counted as a discrepancy: unexplained is not the same as clean.",
    "unavailable_handling": "A source that could not be reached is recorded as unavailable and does NOT count against the entity. Only a source that was reached and returned no record counts as a finding."
  },
  "limitations": [
    "sam_gov: Unavailable \u2014 API key not provisioned (SAM_GOV_API_KEY)",
    "rce_directory: Not checked \u2014 access pending Case #00055525",
    "state_registry: Not checked \u2014 no connector implemented",
    "irs: Not checked \u2014 no connector implemented; IRS data is keyed on EIN, which the registry does not currently hold"
  ],
  "configuration": {
    "rule_set_version": 1,
    "confidence_level": null,
    "margin_of_error": null,
    "proportion": null,
    "use_fpc": null,
    "random_seed": null,
    "generated_at": "2026-08-01T22:08:48.532611Z"
  }
}
```
