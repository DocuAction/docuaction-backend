# Weekly Report Sample

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
| Test Date (UTC) | 2026-08-02T19:24:06.793231+00:00 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| httpx | 0.28.1 |

`POST /api/tefca/arc/reports/generate` -> HTTP 200

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| 1.3 | Generate the weekly ARC report | HTTP 200 with a classification distribution | HTTP 200, buckets see body | PASS |

## Full report payload

```json
{
  "report_id": "WR-2026-W31-RBCD9",
  "report_type": "weekly",
  "period": {
    "start": "2026-07-26",
    "end": "2026-08-02"
  },
  "data": {
    "report_type": "weekly",
    "contract": "7571MN26F80064",
    "period": {
      "start": "2026-07-26",
      "end": "2026-08-02"
    },
    "generated_at": "2026-08-02T19:24:08.120361Z",
    "executive_summary": {
      "entities_reviewed": 33,
      "discrepancies_found": 16,
      "discrepancy_rate": 0.484848,
      "b3_pending_manual_review": 5,
      "b4_requiring_action": 9
    },
    "sampling_summary": {
      "note": "No sample was drawn for this period; the report covers all reviews completed in the window."
    },
    "classification_distribution": {
      "counts": {
        "B1": 17,
        "B2": 2,
        "B3": 5,
        "B4": 9
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
          "REV-2026-000019",
          "REV-2026-000021",
          "REV-2026-000022",
          "REV-2026-000029",
          "REV-2026-000025",
          "REV-2026-000030",
          "REV-2026-000032",
          "REV-2026-000033"
        ],
        "B2": [
          "REV-2026-000005",
          "REV-2026-000020"
        ],
        "B3": [
          "REV-2026-000026",
          "REV-2026-000031",
          "REV-2026-000028",
          "REV-2026-000024",
          "REV-2026-000027"
        ],
        "B4": [
          "REV-2026-000001",
          "REV-2026-000002",
          "REV-2026-000003",
          "REV-2026-000006",
          "REV-2026-000007",
          "REV-2026-000008",
          "REV-2026-000009",
          "REV-2026-000010",
          "REV-2026-000023"
        ]
      }
    },
    "discrepancy_rate": {
      "rate": 0.484848,
      "lower": 0.325038,
      "upper": 0.647819,
      "method": "wilson",
      "confidence": 0.95,
      "n": 33,
      "successes": 16
    },
    "verification_coverage": {
      "sam_gov": {
        "verified": 0,
        "not_found": 0,
        "not_checked": 33,
        "unavailable": 0,
        "failed": 0
      },
      "state_registry": {
        "verified": 0,
        "not_found": 0,
        "not_checked": 33,
        "unavailable": 0,
        "failed": 0
      },
      "irs": {
        "verified": 0,
        "not_found": 0,
        "not_checked": 33,
        "unavailable": 0,
        "failed": 0
      },
      "nppes": {
        "verified": 20,
        "not_found": 13,
        "not_checked": 0,
        "unavailable": 0,
        "failed": 0
      },
      "pecos": {
        "verified": 20,
        "not_found": 13,
        "not_checked": 0,
        "unavailable": 0,
        "failed": 0
      },
      "oig_leie": {
        "verified": 30,
        "not_found": 0,
        "not_checked": 0,
        "unavailable": 0,
        "failed": 0
      }
    },
    "outstanding_items": {
      "b3_pending_manual_review": {
        "count": 5,
        "review_ids": [
          "REV-2026-000026",
          "REV-2026-000031",
          "REV-2026-000028",
          "REV-2026-000024",
          "REV-2026-000027"
        ]
      },
      "b4_requiring_action": {
        "count": 9,
        "review_ids": [
          "REV-2026-000001",
          "REV-2026-000002",
          "REV-2026-000003",
          "REV-2026-000006",
          "REV-2026-000007",
          "REV-2026-000008",
          "REV-2026-000009",
          "REV-2026-000010",
          "REV-2026-000023"
        ]
      },
      "resolved_this_period": 2
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
      "rce_directory: Not checked \u2014 access(entity data provided by ONC)",
      "state_registry: Not checked \u2014 no connector implemented",
      "irs: Not checked \u2014 no connector implemented; IRS data is keyed on EIN, which the registry does not currently hold",
      "5 B3 entities pending manual resolution: REV-2026-000026, REV-2026-000031, REV-2026-000028, REV-2026-000024, REV-2026-000027"
    ],
    "configuration": {
      "rule_set_version": 2,
      "confidence_level": null,
      "margin_of_error": null,
      "proportion": null,
      "use_fpc": null,
      "random_seed": null,
      "generated_at": "2026-08-02T19:24:08.120420Z"
    }
  }
}
```

## Stored reports

`GET /api/tefca/arc/reports` -> HTTP 200

```json
{
  "total": 11,
  "reports": [
    {
      "report_id": "WR-2026-W31-RBCD9",
      "report_type": "weekly",
      "period_start": "2026-07-26",
      "period_end": "2026-08-02",
      "rule_set_version": 2,
      "generated_at": "2026-08-02T19:24:07.880456"
    },
    {
      "report_id": "QR-2026-Q3",
      "report_type": "quarterly",
      "period_start": "2026-05-03",
      "period_end": "2026-08-01",
      "rule_set_version": 1,
      "generated_at": "2026-08-01T23:07:38.447266"
    },
    {
      "report_id": "WR-2026-W31-R21F2",
      "report_type": "weekly",
      "period_start": "2026-07-25",
      "period_end": "2026-08-01",
      "rule_set_version": 1,
      "generated_at": "2026-08-01T23:07:37.650630"
    },
    {
      "report_id": "WR-2026-W31-R20A9",
      "report_type": "weekly",
      "period_start": "2026-07-25",
      "period_end": "2026-08-01",
      "rule_set_version": 1,
      "generated_at": "2026-08-01T22:33:33.057583"
    },
    {
      "report_id": "WR-2026-W31-R3B46",
      "report_type": "weekly",
      "period_start": "2026-07-25",
      "period_end": "2026-08-01",
      "rule_set_version": 1,
      "generated_at": "2026-08-01T22:17:32.065679"
    },
    {
      "report_id": "WR-2026-W31-R5EF9",
      "report_type": "weekly",
      "period_start": "2026-07-25",
      "period_end": "2026-08-01",
      "rule_set_version": 1,
      "generated_at": "2026-08-01T22:08:48.159776"
    },
    {
      "report_id": "PR-2026-08-01-30F2",
      "report_type": "priority",
      "period_start": "2026-08-01",
      "period_end": "2026-08-01",
      "rule_set_version": 1,
      "generated_at": "2026-08-01T18:54:34.996332"
    },
    {
      "report_id": "WR-2026-W31-RC4EA",
      "report_type": "weekly",
      "period_start": "2026-07-25",
      "period_end": "2026-08-01",
      "rule_set_version": 1,
      "generated_at": "2026-08-01T18:54:30.691532"
    },
    {
      "report_id": "PR-2026-08-01",
      "report_type": "priority",
      "period_start": "2026-08-01",
      "period_end": "2026-08-01",
      "rule_set_version": 1,
      "generated_at": "2026-08-01T18:45:05.932169"
    },
    {
      "report_id": "WR-2026-W31-R9EE2",
      "report_type": "weekly",
      "period_start": "2026-07-25",
      "period_end": "2026-08-01",
      "rule_set_version": 1,
      "generated_at": "2026-08-01T18:45:00.775452"
    },
    {
      "report_id": "WR-2026-W31",
      "report_type": "weekly",
      "period_start": "2026-07-25",
      "period_end": "202
... [truncated at 2500 chars]
```
