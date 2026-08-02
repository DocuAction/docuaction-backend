# Review Rules Audit

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


`GET /api/tefca/arc/review-rules?include_retired=true` -> HTTP 200


**5 active rule(s), in evaluation order (lowest priority first).**


## RULE-005 — B4 Non-Compliant

| Field | Value |
|-------|-------|
| bucket | B4 |
| priority | 5 |
| version | 1 |
| effective_date | 2026-08-01 |
| retired_date | None |
| is_active | True |
| description | Exclusion, debarment or an invalid identifier. Disqualifying regardless of what other sources say. |

**Conditions:**

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


## RULE-001 — B1 No Discrepancy

| Field | Value |
|-------|-------|
| bucket | B1 |
| priority | 10 |
| version | 1 |
| effective_date | 2026-08-01 |
| retired_date | None |
| is_active | True |
| description | Every required authoritative source was reached and confirmed the entity, and the NPI passed its check digit. |

**Conditions:**

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


## RULE-002 — B1 Partial Pass

| Field | Value |
|-------|-------|
| bucket | B1 |
| priority | 20 |
| version | 1 |
| effective_date | 2026-08-01 |
| retired_date | None |
| is_active | True |
| description | The sources that answered all confirmed the entity; the remainder were unreachable. An outage is not a discrepancy. |

**Conditions:**

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


## RULE-003 — B2 Minor/Administrative

| Field | Value |
|-------|-------|
| bucket | B2 |
| priority | 30 |
| version | 1 |
| effective_date | 2026-08-01 |
| retired_date | None |
| is_active | True |
| description | Administrative variance — name, address or taxonomy differs in form but not in identity. |

**Conditions:**

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


## RULE-004 — B3 Inexplicable

| Field | Value |
|-------|-------|
| bucket | B3 |
| priority | 40 |
| version | 1 |
| effective_date | 2026-08-01 |
| retired_date | None |
| is_active | True |
| description | Sources reached and disagreed, or the primary source has no record. Requires manual review — not auto-resolvable. |

**Conditions:**

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
