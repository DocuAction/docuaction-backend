# QA baseline isolation proof — Entity Identity & Location Intelligence foundation

Branch `feat/entity-intelligence-foundation` against `main` 0cf0284 (2026-09-11). CURRENT_QA_BASELINE_CHANGED = NO.

| Check | Method | Result |
|---|---|---|
| Existing routes / OpenAPI | `app.openapi()` dumped from a clean `git archive main` and from the branch, paths + component schemas compared as JSON | identical: 411 paths, 106 schemas, path diff ∅, schema diff ∅ |
| API response schemas | same comparison (components.schemas) | identical |
| RBAC / authentication code | `git diff main --stat` on existing files | only `app/core/config.py` changed (+12 lines: five boolean settings, default False); `app/core/security.py` untouched |
| Background job registration | no scheduler/job registration added; isolation test walks `app/` and fails if anything outside the new packages references them | pass |
| DB migration head | `alembic ScriptDirectory.get_heads()` | `['20260903_delivery_grants']` unchanged; no file added to `alembic/versions/` |
| Shared schema | new models on `EntityIntelligenceBase`, disjoint from `app.core.database.Base` metadata (tested) | startup `create_all()` and autogenerate cannot see them |
| ONC/RCE ingestion, 41-field processing, curation, reconciliation | no file under `app/tefca_registry/rce` or `app/Tefca` changed | untouched |
| Stratification, sampling, work creation, assignment | no file under `app/tefca_registry` changed | untouched |
| Analyst determination, independent QA, four categories | `qa_gate.py`, `review_routes.py`, `sow_report_data.py` untouched | untouched |
| Priority reviews, Contract Reports, audit | untouched | untouched |
| Learning Center | knowledge version 1.2.0; no content change (nothing user-facing changed) | untouched |
| Feature OFF behaviour | `FeatureDisabled` from service and adapters with defaults; sub-flags meaningless without master; flags read at call time | tested |
| External calls | no `httpx`/`requests`/`aiohttp`/`urllib.request`/`socket` in the new packages (tested); Google and state-registry have no code | none possible |
| Full backend suite | `pytest tests -q` on the branch with defaults | 2919 passed, 333 skipped, 0 failed (baseline 2860 + 59 new entity-intelligence tests) |

Not deployed: no image built, no container set, no SWA deploy, no flag or configuration changed in the shared QA environment, no migration applied anywhere but SQLite in tests.
