# API Version 1.0 Baseline

**Contract:** 7571MN26F80064

| Field | Value |
|-------|-------|
| API Version | 1.0 |
| Frozen Date | 2026-08-02 |
| Git SHA | `706a2f641f3a48f3dc117f57d579ddc82dbd5686` |
| OpenAPI version | 3.1.0 |
| Total documented paths | 294 |
| Total documented operations | 308 |
| Source | `GET https://docuaction-dev.azurewebsites.net/openapi.json` |
| Archived baseline | `docs/api/openapi_v1.0.json` |

## Breaking change policy

Any **endpoint removal** or **response format change** requires a version
increment (v1.1, v2.0). **Additive** changes — new endpoints, new optional
request fields, new response fields — are permitted within v1.0.

### What counts as breaking

| Change | Breaking | Version action |
|--------|----------|----------------|
| Remove an endpoint | Yes | Increment |
| Rename a response field | Yes | Increment |
| Change a field's type | Yes | Increment |
| Remove a response field | Yes | Increment |
| Make an optional request field required | Yes | Increment |
| Change a success status code | Yes | Increment |
| Add a new endpoint | No | Permitted in v1.0 |
| Add an optional request field | No | Permitted in v1.0 |
| Add a response field | No | Permitted in v1.0 |
| Widen an accepted enum | No | Permitted in v1.0 |

### Error-response codes are in scope

The 401/403 boundary is part of the contract. FastAPI 0.140 returns **401** for a
missing or malformed bearer token and **403** for a valid token with insufficient
role. A framework upgrade that changes this changes the contract and requires a
version increment, even though no application code was edited. This is recorded
explicitly because that exact change has already happened once on this platform.

## Verifying a candidate build against the baseline

```
curl -s https://docuaction-dev.azurewebsites.net/openapi.json -o candidate.json
python -m json.tool candidate.json > /dev/null   # well-formed
# diff the operation sets; any removal is a breaking change
```

## Limitations

- The baseline freezes the **documented** contract. It does not by itself prove
  every operation's runtime response matches its declared schema; see
  `docs/audit/API_CONTRACT_VALIDATION.md` for what was actually exercised.
- The archived spec was captured from the **development** environment.
