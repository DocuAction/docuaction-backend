# Performance Baseline

**Contract:** 7571MN26F80064

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service (Linux) |
| Build | Git SHA `706a2f641f3a48f3dc117f57d579ddc82dbd5686` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-01T22:33:41+00:00 |
| Contract | 7571MN26F80064 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| pytest | pytest 9.1.1 |
| Bandit | __main__.py 1.9.4 |
| openapi-spec-validator | 0.9.0 |
| curl | curl 8.21.0 (Windows) libcurl/8.21.0 Schannel zlib/1.3.2 WinIDN WinLDAP |
| OWASP ZAP | Not Available — see ZAP_FINDING_VALIDATION.md |


## 1. CSV parse + validate stage (in-process, no database)

| Rows | Seconds | Rows/sec | Parsed OK | Errors |
|------|---------|----------|-----------|--------|
| 100 | 0.0025 | 39,331 | 100 | 0 |
| 1000 | 0.0168 | 59,640 | 1000 | 0 |
| 5000 | 0.0808 | 61,884 | 5000 | 0 |

## 2. End-to-end CSV import (dev, HTTP)

| Rows | HTTP | Seconds | Rows/sec | Imported | Errors |
|------|------|---------|----------|----------|--------|
| 50 | 200 | 11.94 | 4.2 | 48 | 2 |

**Large-volume end-to-end import (1,000+ rows): Not Executed.**  
No delete endpoint exists; a 1,000+ row benchmark would permanently contaminate the dev ARC registry and every subsequent sample draw and report.

The parse stage above is measured at 5,000 rows precisely because it is the part that can be measured without writing anything. The end-to-end figure is dominated by database round-trips and per-entity savepoints, not parsing — the two numbers are not interchangeable and no throughput was extrapolated from one to the other.

## 3. Read-path latency (5 samples per endpoint)

| Endpoint | n | Mean (s) | Median (s) | Min (s) | Max (s) |
|----------|---|----------|------------|---------|---------|
| `/api/tefca/registry/entities?limit=50` | 5 | 0.814 | 0.831 | 0.775 | 0.843 |
| `/api/tefca/registry/stats` | 5 | 1.363 | 1.077 | 0.929 | 2.703 |
| `/api/tefca/arc/reviews?limit=50` | 5 | 0.771 | 0.794 | 0.687 | 0.806 |
| `/api/tefca/arc/review-rules` | 5 | 0.71 | 0.704 | 0.652 | 0.783 |

## 4. Entity verification latency (live authoritative registries)

| n | Mean (s) | Median (s) | Min (s) | Max (s) |
|---|----------|------------|---------|---------|
| 10 | 1.84 | 1.68 | 1.56 | 2.53 |

Each verification queries NPPES, PECOS and OIG LEIE over the public internet. These timings therefore include third-party latency outside the platform's control and will vary with upstream load.

## 5. Report generation

| Report | HTTP | Seconds | Entities Reviewed |
|--------|------|---------|-------------------|
| weekly | 200 | 0.86 | 32 |
| quarterly | 200 | 0.9 | 32 |

## Limitations

- Single-workstation client; network latency to Azure is included in every dev figure and was not isolated.
- No concurrent-load or soak test was run. All figures are single-request serial measurements. **Concurrency behaviour: Not Executed.**
- Sample size is 5 per read endpoint and 10 for verification — enough to show magnitude, not enough for a tail-latency (p95/p99) claim. None is made.
- The dev App Service is an S1 tier instance shared with other activity; these are not capacity-planning numbers for production.
- No cold-start measurement is included; the app was already warm.