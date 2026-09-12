# QA baseline isolation proof — Entity Identity & Location Intelligence foundation

Branch `feat/entity-intelligence-foundation` against `main` 0cf0284. First proven 2026-09-11 (commit e8c4de0); re-proven 2026-09-12 after the overnight hardening sprint. CURRENT_QA_BASELINE_CHANGED = NO.

| Check | Method | Result 2026-09-11 | Result 2026-09-12 |
|---|---|---|---|
| Existing routes / OpenAPI | `app.openapi()` dumped from a clean `git archive main` and from the branch working tree, paths + component schemas compared as JSON | identical: 411 paths, 106 schemas | identical: 411 paths, 106 schemas, byte-identical JSON |
| API response schemas | same comparison (components.schemas) | identical | identical |
| RBAC / authentication code | `git diff main --stat` on pre-existing files | only `app/core/config.py` (+12 lines: five boolean settings, default False) | unchanged: still only `app/core/config.py` (+12) |
| Background job registration | no scheduler/job registration added; isolation test walks `app/` and fails if anything outside the new packages references them | pass | pass |
| DB migration head | `alembic ScriptDirectory.get_heads()` | `['20260903_delivery_grants']` | `['20260903_delivery_grants']`; no file in `alembic/versions/` references `ei_` |
| Shared schema | new models on `EntityIntelligenceBase`, disjoint from `app.core.database.Base` metadata (tested) | startup `create_all()` and autogenerate cannot see them | same; additionally proven on an ephemeral PostgreSQL 18.3 cluster: only the five `ei_*` tables exist after apply |
| ONC/RCE ingestion, 41-field processing, curation, reconciliation | no file under `app/tefca_registry/rce` or `app/Tefca` changed | untouched | untouched |
| Stratification, sampling, work creation, assignment | no file under `app/tefca_registry` changed | untouched | untouched |
| Analyst determination, independent QA, four categories | `qa_gate.py`, `review_routes.py`, `sow_report_data.py` untouched | untouched | untouched |
| Priority reviews, Contract Reports, audit | untouched | untouched | untouched |
| Learning Center | knowledge version 1.2.0; no content change | untouched | untouched |
| Feature OFF behaviour | `FeatureDisabled` from service and adapters with defaults; sub-flags meaningless without master; flags read at call time | tested | tested + strict parsing: "false"/"maybe"/1/None never enable; master string "false" wins over a True sub-flag |
| External calls | no `httpx`/`requests`/`aiohttp`/`urllib.request`/`socket` in the new packages (tested); Google and state-registry have no code | none possible | none possible; static review adds exec/pickle/yaml/subprocess/os.environ checks |
| Full backend suite | `pytest tests -q` on the branch with defaults | 2919 passed, 333 skipped, 0 failed | **3202 passed, 333 skipped, 0 failed** |

## Test accounting

| | Count |
|---|---|
| BASELINE EXISTING (main, before the foundation) | 2860 passed / 333 skipped |
| NEW — foundation sprint (2026-09-11) | 59 |
| NEW — overnight hardening sprint (2026-09-12) | 283 |
| NEW total (entity-intelligence, all synthetic) | 342 |
| TOTAL on the branch | 3202 passed / 333 skipped / 0 failed |

Skipped count unchanged (333): the skips are pre-existing environment-conditional tests; none were added or removed.

Not deployed: no image built, no container set, no SWA deploy, no flag or configuration changed in the shared QA environment, no migration applied anywhere except SQLite in tests and a throw-away local PostgreSQL cluster that was deleted.
