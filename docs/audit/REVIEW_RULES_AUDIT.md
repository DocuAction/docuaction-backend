# Review Rules Audit

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
| Test Date (UTC) | 2026-08-02T19:23:49.310002+00:00 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| httpx | 0.28.1 |

`GET /api/tefca/arc/review-rules?include_retired=true` -> HTTP 200

**10 rule(s) returned: 5 active, 5 retired.**

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| 1.1 | Retrieve all review rules with conditions | HTTP 200, rules with `conditions` present | HTTP 200, 10 rules, all with `conditions` | PASS |

## Active rules (evaluation order, lowest priority first)

| Priority | Rule | Bucket | Version | Effective | Name |
|---|---|---|---|---|---|
| 5 | `RULE-005` | B4 | v2 | 2026-08-02 | B4 Non-Compliant |
| 10 | `RULE-001` | B1 | v2 | 2026-08-02 | B1 No Discrepancy |
| 20 | `RULE-002` | B1 | v2 | 2026-08-02 | B1 Partial Pass |
| 30 | `RULE-003` | B2 | v2 | 2026-08-02 | B2 Minor/Administrative |
| 40 | `RULE-004` | B3 | v2 | 2026-08-02 | B3 Inexplicable |

## Retired rules

| Rule | Bucket | Version | Effective | Retired | Name |
|---|---|---|---|---|---|
| `RULE-001` | B1 | v1 | 2026-08-01 | 2026-08-02 | B1 No Discrepancy |
| `RULE-002` | B1 | v1 | 2026-08-01 | 2026-08-02 | B1 Partial Pass |
| `RULE-003` | B2 | v1 | 2026-08-01 | 2026-08-02 | B2 Minor/Administrative |
| `RULE-004` | B3 | v1 | 2026-08-01 | 2026-08-02 | B3 Inexplicable |
| `RULE-005` | B4 | v1 | 2026-08-01 | 2026-08-02 | B4 Non-Compliant |

## Full rule definitions with conditions

### `RULE-001` v1 — B1 No Discrepancy [RETIRED] -> B1

Every required authoritative source was reached and confirmed the entity, and the NPI passed its check digit.

```json
{
  "all_of": [
    {
      "source": "nppes",
      "status": "verified"
    },
    {
      "source": "oig_leie",
      "status": "clear"
    },
    {
      "source": "pecos",
      "status": "verified"
    }
  ],
  "none_of": [
    {
      "field": "npi_validation",
      "status": "flagged"
    }
  ]
}
```

### `RULE-001` v2 — B1 No Discrepancy [ACTIVE] -> B1

Every required authoritative source was reached and confirmed the entity, and the NPI passed its check digit.

```json
{
  "all_of": [
    {
      "source": "nppes",
      "status": "verified"
    },
    {
      "source": "oig_leie",
      "status": "clear"
    },
    {
      "source": "pecos",
      "status": "verified"
    }
  ],
  "none_of": [
    {
      "field": "npi_validation",
      "status": "flagged"
    },
    {
      "source": "sam_gov",
      "status": "excluded"
    },
    {
      "source": "sam_gov",
      "status": "debarred"
    }
  ]
}
```

### `RULE-002` v1 — B1 Partial Pass [RETIRED] -> B1

The sources that answered all confirmed the entity; the remainder were unreachable. An outage is not a discrepancy.

```json
{
  "all_of": [
    {
      "source": "nppes",
      "status": "verified"
    },
    {
      "source": "oig_leie",
      "status": "clear"
    }
  ],
  "none_of": [
    {
      "field": "npi_validation",
      "status": "flagged"
    },
    {
      "source": "pecos",
      "status": "not_found"
    },
    {
      "source": "sam_gov",
      "status": "not_found"
    }
  ],
  "any_unavailable": [
    "pecos",
    "sam_gov"
  ]
}
```

### `RULE-002` v2 — B1 Partial Pass [ACTIVE] -> B1

The sources that answered all confirmed the entity; the remainder were unreachable. An outage is not a discrepancy.

```json
{
  "all_of": [
    {
      "source": "nppes",
      "status": "verified"
    },
    {
      "source": "oig_leie",
      "status": "clear"
    }
  ],
  "none_of": [
    {
      "field": "npi_validation",
      "status": "flagged"
    },
    {
      "source": "pecos",
      "status": "not_found"
    },
    {
      "source": "sam_gov",
      "status": "not_found"
    },
    {
      "source": "sam_gov",
      "status": "excluded"
    },
    {
      "source": "sam_gov",
      "status": "debarred"
    }
  ],
  "any_unavailable": [
    "pecos",
    "sam_gov"
  ]
}
```

### `RULE-003` v1 — B2 Minor/Administrative [RETIRED] -> B2

Administrative variance — name, address or taxonomy differs in form but not in identity.

```json
{
  "any_of": [
    {
      "field": "name_mismatch",
      "severity": "minor"
    },
    {
      "field": "address_mismatch",
      "severity": "minor"
    },
    {
      "field": "taxonomy_mismatch",
      "severity": "minor"
    }
  ],
  "none_of": [
    {
      "source": "oig_leie",
      "status": "excluded"
    },
    {
      "field": "npi_validation",
      "status": "flagged"
    }
  ]
}
```

### `RULE-003` v2 — B2 Minor/Administrative [ACTIVE] -> B2

Administrative variance — name, address or taxonomy differs in form but not in identity.

```json
{
  "any_of": [
    {
      "field": "name_mismatch",
      "severity": "minor"
    },
    {
      "field": "address_mismatch",
      "severity": "minor"
    },
    {
      "field": "taxonomy_mismatch",
      "severity": "minor"
    }
  ],
  "none_of": [
    {
      "source": "oig_leie",
      "status": "excluded"
    },
    {
      "field": "npi_validation",
      "status": "flagged"
    },
    {
      "source": "sam_gov",
      "status": "excluded"
    },
    {
      "source": "sam_gov",
      "status": "debarred"
    }
  ]
}
```

### `RULE-004` v1 — B3 Inexplicable [RETIRED] -> B3

Sources reached and disagreed, or the primary source has no record. Requires manual review — not auto-resolvable.

```json
{
  "any_of": [
    {
      "field": "nppes_pecos_conflict",
      "status": true
    },
    {
      "field": "multiple_source_conflict",
      "status": true
    },
    {
      "source": "nppes",
      "status": "not_found"
    },
    {
      "field": "confidence_below",
      "threshold": 0.5
    }
  ],
  "none_of": [
    {
      "source": "oig_leie",
      "status": "excluded"
    }
  ]
}
```

### `RULE-004` v2 — B3 Inexplicable [ACTIVE] -> B3

Sources reached and disagreed, or the primary source has no record. Requires manual review — not auto-resolvable.

```json
{
  "any_of": [
    {
      "field": "nppes_pecos_conflict",
      "status": true
    },
    {
      "field": "multiple_source_conflict",
      "status": true
    },
    {
      "source": "nppes",
      "status": "not_found"
    },
    {
      "field": "confidence_below",
      "threshold": 0.5
    }
  ],
  "none_of": [
    {
      "source": "oig_leie",
      "status": "excluded"
    }
  ]
}
```

### `RULE-005` v1 — B4 Non-Compliant [RETIRED] -> B4

Exclusion, debarment or an invalid identifier. Disqualifying regardless of what other sources say.

```json
{
  "any_of": [
    {
      "source": "oig_leie",
      "status": "excluded"
    },
    {
      "source": "sam_gov",
      "status": "debarred"
    },
    {
      "field": "npi_validation",
      "status": "invalid"
    },
    {
      "field": "required_verification_failed",
      "status": true
    }
  ]
}
```

### `RULE-005` v2 — B4 Non-Compliant [ACTIVE] -> B4

Exclusion, debarment or an invalid identifier. Disqualifying regardless of what other sources say.

```json
{
  "any_of": [
    {
      "source": "oig_leie",
      "status": "excluded"
    },
    {
      "source": "sam_gov",
      "status": "debarred"
    },
    {
      "field": "npi_validation",
      "status": "invalid"
    },
    {
      "field": "required_verification_failed",
      "status": true
    },
    {
      "source": "sam_gov",
      "status": "excluded"
    }
  ]
}
```

## Rule change history

`GET /api/tefca/arc/review-rules/history` -> HTTP 200

```json
{
  "total": 10,
  "versions": [
    {
      "id": "99d61f11-c7f1-4d8c-997a-499db1754cb3",
      "rule_code": "RULE-001",
      "name": "B1 No Discrepancy",
      "bucket": "B1",
      "priority": 10,
      "conditions": {
        "all_of": [
          {
            "source": "nppes",
            "status": "verified"
          },
          {
            "source": "oig_leie",
            "status": "clear"
          },
          {
            "source": "pecos",
            "status": "verified"
          }
        ],
        "none_of": [
          {
            "field": "npi_validation",
            "status": "flagged"
          }
        ]
      },
      "description": "Every required authoritative source was reached and confirmed the entity, and the NPI passed its check digit.",
      "version": 1,
      "effective_date": "2026-08-01",
      "retired_date": "2026-08-02",
      "is_active": false
    },
    {
      "id": "724d27d3-99c6-4f11-94b2-4f7b80555c2a",
      "rule_code": "RULE-001",
      "name": "B1 No Discrepancy",
      "bucket": "B1",
      "priority": 10,
      "conditions": {
        "all_of": [
          {
            "source": "nppes",
            "status": "verified"
          },
          {
            "source": "oig_leie",
            "status": "clear"
          },
          {
            "source": "pecos",
            "status": "verified"
          }
        ],
        "none_of": [
          {
            "field": "npi_validation",
            "status": "flagged"
          },
          {
            "source": "sam_gov",
            "status": "excluded"
          },
          {
            "source": "sam_gov",
            "status": "debarred"
          }
        ]
      },
      "description": "Every required authoritative source was reached and confirmed the entity, and the NPI passed its check digit.",
      "version": 2,
      "effective_date": "2026-08-02",
      "retired_date": null,
      "is_active": true
    },
    {
      "id": "3cd73006-baf2-460d-a04f-2bda82196b02",
      "rule_code": "RULE-002",
      "name": "B1 Partial Pass",
      "bucket": "B1",
      "priority": 20,
      "conditions": {
        "all_of": [
          {
            "source": "nppes",
            "status": "verified"
          },
          {
            "source": "oig_leie",
            "status": "clear"
          }
        ],
        "none_of": [
          {
            "field": "npi_validation",
            "status": "flagged"
          },
          {
            "source": "pecos",
            "status": "not_found"
          },
          {
            "source": "sam_gov",
            "status": "not_found"
          }
        ],
        "any_unavailable": [
          "pecos",
          "sam_gov"
        ]
      },
      "description": "The sources that answered all confirmed the entity; the remainder were unreachable. An outage is not a discrepancy.",
      "version": 1,
      "effective_date": "2026-08-01",
      "retired_date": "2026-08-02",
    
... [truncated at 3000 chars]
```
