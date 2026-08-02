# API Contract Validation — Block 7

**Contract:** 7571MN26F80064

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service |
| Build | Git SHA `ebfcd38e067fd2b879e095eee547e40931a8e027` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-02T22:48:08.555708+00:00 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| httpx | 0.28.1 |

## Result: 14/14 PASS

| Field | Value |
|---|---|
| OpenAPI version | 3.1.0 |
| Documented paths | 294 |
| Documented operations | 309 |

## 7.1 — Specification validation

Validated with `openapi-spec-validator` against the OpenAPI 3.1 meta-schema.

| Test | Expected | Actual | Result |
|---|---|---|---|
| 7.1 | no schema errors | valid against the OpenAPI 3.1 meta-schema | paths=294, ops=309 | **PASS** |

## 7.2 — Schema conformance (10 endpoints)

| Test | Endpoint | Expected | Actual | Result |
|---|---|---|---|---|
| 7.2.1 | GET /health conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | **PASS** |
| 7.2.2 | GET /api/auth/me conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | **PASS** |
| 7.2.3 | GET /registry/stats conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | **PASS** |
| 7.2.4 | GET /registry/entities conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | **PASS** |
| 7.2.5 | GET /registry/findings conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | **PASS** |
| 7.2.6 | GET /registry/search conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | **PASS** |
| 7.2.7 | GET /arc/review-rules conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | **PASS** |
| 7.2.8 | GET /arc/reviews conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | **PASS** |
| 7.2.9 | GET /arc/reports conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | **PASS** |
| 7.2.10 | GET /arc/samples conforms | 200 + parseable JSON | HTTP 200, ct=application/json, in_spec=True | **PASS** |

## 7.3 — Response consistency (10 identical calls)

| Endpoint | Calls | Distinct digests | Result |
|---|---|---|---|
| GET /registry/stats | 10 | 1 | **PASS** |
| GET /arc/review-rules | 10 | 1 | **PASS** |

Responses were hashed after serialisation with sorted keys, so an identical digest across ten calls means byte-identical content, not merely an equal status code.

## 7.4 — Backward compatibility against the frozen v1.0 baseline

Baseline: `docs/api/openapi_v1.0.json` (frozen 2026-08-02, SHA `706a2f6`).

| Measure | Count |
|---|---|
| Operations in baseline | 308 |
| Operations currently | 309 |
| **Removed (breaking)** | **0** |
| Added (permitted in v1.0) | 1 |

Added since baseline:

- `DELETE /api/tefca/registry/entities/{entity_id}`

Under the documented policy, endpoint removal or a response-format change requires
a version increment; additive changes are permitted within v1.0. No operation was
removed, so the current build remains backward compatible with the v1.0 contract.

## Limitation

7.2 verifies that each endpoint returns HTTP 200 with a parseable JSON body of the
declared content type, and that the operation is declared in the specification. It
does **not** validate each response body against its declared response schema
field by field. That is a deeper check than was performed, and is stated here
rather than implied by the word "conformance".
