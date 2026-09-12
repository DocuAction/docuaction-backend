# Performance and database validation report — 2026-09-12 (synthetic data only)

## Synthetic large-file run

`scripts/ei_perf.py` builds a synthetic NPPES V2 bundle at full width (330-column main file with 10% Type 1 noise rows, Other Name and Practice Location reference files), then for every organisation runs the whole pipeline: parse → bundle → adapter observations → comparison → delta against a prior delivery → System Evidence Assessment. Delivery mix: 70% exact, 15% delivered under an NPPES-recorded DBA, 10% delivered at an additional practice location, 5% conflicting name and address. Machine: developer laptop, Windows 11, Python 3.13, single process. Every NPI is in the reserved 9999xxxxxx range; every name is "SYNTHETIC …".

| Organisations | Main file (MB) | Rows read (incl. Type 1) | Parse + bundle (s) | Evaluate all (s) | Per entity (ms) | Peak memory (MB) | Observations |
|---|---|---|---|---|---|---|---|
| 2,000 | 2.5 | 2,200 | 0.07 | 1.91 | 0.96 | 12.9 | 8,503 |
| 5,000 | 6.2 | 5,500 | 0.20 | 4.60 | 0.92 | 32.2 | 21,267 |
| 10,000 | 12.3 | 11,000 | 0.40 | 11.41 | 1.14 | 64.4 | 42,503 |
| 25,000 | 30.8 | 27,500 | 1.16 | 28.32 | 1.13 | 161.2 | 106,183 |
| 50,000 | 61.7 | 55,000 | 2.70 | 56.98 | 1.14 | 322.5 | 212,388 |

All parse reports: `OK`. Scaling is linear in entities (≈1.1 ms per entity end-to-end, ≈6.4 KB peak memory per entity when the whole bundle is held in memory). A real monthly bundle is ~8.9 M rows; the design streams it and observes only in-scope NPIs, so the in-memory bundle above is the worst case for the observation side, not the acquisition side.

### 25,000 SOURCE RECORDS != 25,000 HUMAN REVIEWS

| Organisations | EVIDENCE_CORROBORATES | EXPLAINABLE_VARIATION_IDENTIFIED | CONFLICTING_EVIDENCE | Entities with something to look at | Share |
|---|---|---|---|---|---|
| 2,000 | 1,398 | 503 | 99 | 602 | 30.1% |
| 25,000 | 17,580 | 6,183 | 1,237 | 7,420 | 29.7% |
| 50,000 | 35,117 | 12,388 | 2,495 | 14,883 | 29.8% |

Every entity still requires human review under the methodology (the engine never finishes a case). What the numbers show is triage capacity: with a synthetic 30% variation rate, 70% of entities arrive with every dimension corroborated by an independent federal source and a stated basis, and the remaining 30% arrive with a named reason (DBA record, additional location, conflict) and the evidence attached. The engine turns 25,000 source records into 25,000 evidence summaries, not 25,000 investigations. The real mix will differ; the method does not.

## PostgreSQL isolated validation

`POSTGRESQL_ISOLATED_VALIDATION = PASSED`

| Item | Result |
|---|---|
| Method | ephemeral PostgreSQL 18.3 cluster created with `initdb` in the session scratchpad, trust auth, `listen_addresses=127.0.0.1`, port 54329, no password, no service; stopped and deleted at the end (`scripts/ei_pg_isolated_validation.py`) |
| Credentials invented or requested | none; the shared DEV/QA database and the local 5432 instance were not touched |
| Refusal path with a live server available | Azure host URL → exit 2, "refusing: URL is not local"; engine never created |
| DDL apply | `python -m app.core.entity_intelligence.migrations.apply --database-url postgresql://ei_test@127.0.0.1:54329/postgres` → created the five `ei_*` tables |
| Platform tables present | none (0 of the shared schema) |
| `ei_evidence_observations` columns | 19 |
| ORM round trip | `EiSourceDelivery` inserted through SQLAlchemy; 1 row read back |
| Re-apply | idempotent (rc 0; still 1 row) |
| Cluster deleted, port released | yes |

SQLite validation (test suite) remains in place for every run.

## Security review

| Check | Result |
|---|---|
| bandit on `app/core/entity_intelligence`, `app/evidence_sources`, `scripts/ei_perf.py` (2,211 lines) | 0 issues (the import-time `assert` in `assessment.py` was replaced with a `RuntimeError` guard; `random` in the perf script is annotated `# nosec B311`) |
| Secret patterns (AWS/Google keys, `password=`, `secret=`, private keys, credential URLs) in the isolated packages | none (tested) |
| Network / exec / unsafe deserialisation imports in the isolated packages | none (tested) |
| Shared DB session imports (`get_db`, `AsyncSessionLocal`, `app.core.database`) in the isolated packages | none (tested) |
| Logging of observed values | none; no `logging` or `print` except the migration script's operator messages (tested) |
| Zip-slip, archive bomb, member allowlist, member count/size, CSV formula injection, column/field/row limits, decoding, log redaction | helpers in `intake_safety.py`, 30 tests |
| ReDoS | the only regexes are `[^\w\s]`, `\s+`, `\D` — linear; no user-supplied patterns |
| Path traversal | `safe_extract_path` resolves and checks containment; tested |
| pip-audit | not run against the whole platform tonight (out of scope; no new dependency was added) |
