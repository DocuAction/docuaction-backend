# Code Architecture Review (Section 2M)

## Structure
- **Layout:** `app/{api,routers,services,models,schemas,core,middleware}` + feature packages (`Tefca`, `tefca_registry`, `platform_config`, `bulletin_intelligence`, `case_management`). Generally **logical**, but with **inconsistent conventions**: routes live in both `app/api/*` and `app/routers/*`; some features are self-contained packages (good), others are spread across `api/` + `services/` + `models/`.
- **Module boundaries:** the **newer feature packages are well-isolated** (`tefca_registry` explicitly avoids reaching into `Tefca`; `platform_config` is self-contained). Older code shares the giant `app/models/__init__.py` (1,142 LOC) and cross-imports services freely — **weaker boundaries**.
- **Service layer:** present (`app/services/*_engine.py`) and mostly reusable; some business logic also lives directly in route handlers (esp. legacy TEFCA routes).
- **API patterns:** mostly RESTful, but **naming is inconsistent** — prefixes mix `/api/v1/...`, `/api/...`, and bare `/rfq`, `/quotes`, `/suppliers` (no `/api` prefix). Versioning is inconsistent (`/api/v1/tefca` vs `/api/tefca/registry`).
- **DB design:** the registry/platform schemas are well-normalized and documented; legacy/commercial models vary. **Two declarative Bases** (`app.core.database.Base`, `app.database.Base`) is the central structural issue.
- **Config:** centralized in `app/core/config.py` (pydantic-settings), env-driven, with strong guards (SECRET_KEY ≥64 floor). Good.

## Code quality
| Aspect | Finding |
|---|---|
| **Naming** | Mostly descriptive; conventions vary between the polished newer packages and older modules. |
| **Duplication** | Notable: multiple `*_engine.py` services with similar AI-call/parse scaffolding; two API-client patterns on the frontend; repeated auth-dependency boilerplate. Model definitions duplicated across the two Bases (e.g., two `User`, two `AuditLog`). |
| **Dead / dormant code** | Large: the entire **commercial stack (GovCon/ATS/ERP/migration)** is mounted but its tables aren't deployed; `@tanstack/react-table` unused; `azure.storage` referenced but no Blob usage; legacy `tefca_dashboard` is a redirect stub. |
| **Complexity hotspots** | `bulletin_intelligence/engine.py` (3,618), `Tefca/routes.py` (2,852), `Tefca/qa_engine.py` (1,163), `Tefca/connectors.py` (1,145), `models/__init__.py` (1,142), `Tefca/mock_data.py` (786). These concentrate risk and resist change. |
| **Error handling** | **Good baseline** — global exception handlers (`app/core/error_handler.py`) that never leak stack traces/DB errors in prod (NIST SI-11). Per-module consistency varies. |
| **Logging** | Standard `logging` with named loggers; **not structured (no JSON/log-context)**; some sensitive values printed (e.g., DB engine log prints URL prefix incl. partial creds — minor). |
| **Tests** | **~none** — 1 test file total (`bulletin_intelligence/test_bulletin_enhancements.py`). The single biggest code-quality gap. |

## Critical items (called out in the prompt)
1. **113 models vs 51 tables / two `Base` classes.** Two independent SQLAlchemy metadata registries: the **federal stack** (`app.core.database.Base`) is created at startup via `create_all` and deployed; the **commercial stack** (`app.database.Base`, in `app/models/__init__.py`) is **not materialized** in the assessed DB. This is deliberate history (a GovCon product + a federal product in one repo) but creates drift, duplicate models, and confusion. **Recommendation: split into two services or clearly quarantine the commercial Base behind a feature flag.**
2. **No Alembic on prod.** Tables are created by runtime `Base.metadata.create_all` (creates missing tables, never alters) + hand-written `ALTER TABLE … IF NOT EXISTS` in `main.py` startup. **No migration history, no down-migrations, no governed schema change.** Alembic exists but is used only for a couple of scoped TEFCA/platform migrations, not as the prod mechanism.
3. **Unpinned dependencies** (`weasyprint`, `gunicorn`) + supply-chain flags → non-reproducible builds.
4. **Manual Kudu VFS deployment** — no pipeline, CI security scans not enforced at deploy, rollback manual.

## Strengths
- Strong config + error-handling + security-header middleware baseline.
- The **newest package (`tefca_registry`) is a model of good structure** — isolated, documented, indexed, audited, idempotent — and should be the template for refactors.
- Async throughout (FastAPI + async SQLAlchemy + httpx).
