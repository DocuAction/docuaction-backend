# DocuAction API integration (Postman collection + Newman)

Deterministic API tests against an **isolated** backend (disposable PostgreSQL,
synthetic accounts, one synthetic promoted delivery, one deterministic
SAM-extract candidate). Never point these at production — the runner refuses a
non-local `baseUrl`.

## Files
- `docuaction-api.postman_collection.json` — the collection (Postman v2.1.0;
  imports into the Postman app and runs under the Postman CLI).
- `docuaction-itest.postman_environment.json` — baseUrl + role emails; passwords
  are intentionally empty (credentials never live in git).
- `run.mjs` — dependency-free wrapper around the **Postman CLI** (`postman
  collection run`); injects passwords + seeded ids from the fixture manifest
  and enforces the local-host guard. No npm dependency tree.
- `../../scripts/seed_integration_fixtures.py` — seeds the synthetic state and
  writes the manifest.

## Install the Postman CLI (one-time; no API key needed for local runs)
- Windows: `powershell -c "iwr https://dl-cli.pstmn.io/install/win64/latest -UseBasicParsing | iex"`
- Linux/macOS: `curl -o- "https://dl-cli.pstmn.io/install/linux64.sh" | sh`
A local collection-file run needs no Postman login/API key — results are simply
not synced to Postman cloud.

## Local run — Windows PowerShell (exact)
Assumes a local PostgreSQL with a `docuaction_owner` superuser-capable role
(the project's isolated-DB convention) and the backend venv's deps installed.

```powershell
# 0. From the backend repo root. One isolated database.
$pg = "C:\Program Files\PostgreSQL\18\bin"
$env:PGPASSWORD = "test"
& "$pg\psql.exe" -h 127.0.0.1 -p 5499 -U test -d postgres -c "DROP DATABASE IF EXISTS itest;"
& "$pg\psql.exe" -h 127.0.0.1 -p 5499 -U test -d postgres -c "CREATE DATABASE itest OWNER docuaction_owner;"
& "$pg\psql.exe" -h 127.0.0.1 -p 5499 -U test -d itest -c "GRANT USAGE, CREATE ON SCHEMA public TO docuaction_app;"

# 1. Env for migrate + seed + server (isolated instance only).
$env:DATABASE_URL = "postgresql+asyncpg://docuaction_owner:test@127.0.0.1:5499/itest"
$env:SECRET_KEY   = (python -c "import secrets; print(secrets.token_urlsafe(64))")
$env:ALLOWED_HOSTS = "localhost,127.0.0.1"
$env:ALLOWED_ORIGINS = "http://127.0.0.1:8010"
$env:ENABLE_SAM_EXTRACT_SCREENING = "true"
$env:ENTITY_RESOLVER_SOURCE = "db"

# 2. Migrate and seed the deterministic fixtures.
python -m alembic upgrade head
python scripts\seed_integration_fixtures.py --out integration-fixtures.json

# 3. Start the backend (new PowerShell window, same env), then wait for health.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010

# 4. Run the collection (first window; Postman CLI already installed).
cd tests\postman
node run.mjs --manifest ..\..\integration-fixtures.json --baseUrl http://127.0.0.1:8010 --junit newman-results.xml
```

**One supported command for the API tests** (steps 2–4 condensed, server already up, Postman CLI installed):
```powershell
python scripts\seed_integration_fixtures.py --out integration-fixtures.json; cd tests\postman; node run.mjs --manifest ..\..\integration-fixtures.json --baseUrl http://127.0.0.1:8010 --junit newman-results.xml
```

Note on rate limiting: the backend's tiered limiter is never disabled. A single
collection run (~4 logins, ~23 requests) is well within limits; running it many
times in quick succession can trip HTTP 429 — wait ~60s for the window to reset.

## CI
`.github/workflows/api-collection.yml` runs the whole thing on a disposable
`postgres:16` service: create DB/roles → `alembic upgrade head` → start uvicorn
→ seed → install the Postman CLI → `node run.mjs`. It uploads the JUnit report
and the server log, never the fixture manifest. No secrets, least-privilege
token, no `pull_request_target`, no committed npm dependency tree.

## Import into the Postman app (your workspace)
1. Postman → **Import** → **Files** → select `docuaction-api.postman_collection.json`.
2. Import → select `docuaction-itest.postman_environment.json`; pick it as the
   active environment (top-right).
3. Fill the empty env values for a local run: `viewer_password`, `reviewer_password`,
   `qalead_password`, `pm_password` (from your `integration-fixtures.json`), and
   `intake_id`, `job_id`, `candidate_entity_id` (same file). Keep `baseUrl`
   local. Do **not** save real credentials back into the shared environment.
4. Run the collection with the Collection Runner. Folders tagged
   `(requires #193)`/`(requires #194)` self-report "not on this build" until
   those PRs land.

## Scope limitations
- `sam-extract` / snapshot folders assert fully only against a backend that
  includes PRs #193 (`/sam-extract*`, backfill) and #194 (snapshot persist).
  On a `main`-based build they return 404 and the folder records a clear
  "requires #193/#194" result rather than a silent skip.
- The candidate is seeded deterministically at the data layer (one REVIEW
  evidence row) — this exercises the enumeration, not the live GSA extract
  fetch. Real external-source availability is a separate concern and is not
  called here.
