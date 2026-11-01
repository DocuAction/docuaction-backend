# DocuAction API integration (Postman collection + Postman CLI)

Deterministic API tests against an **isolated** backend (disposable PostgreSQL,
synthetic accounts, one synthetic promoted delivery, one deterministic
SAM-extract candidate). Never point these at production — the runner refuses a
non-local `baseUrl`.

The collection is driven on the command line by the **Postman CLI** (the
official `postman` binary) through `run.mjs`. There is **no Newman and no npm
dependency tree** — `run.mjs` uses Node built-ins only and shells out to the
Postman CLI. A local collection-file run needs **no Postman API key** (results
are simply not synced to the Postman cloud).

## Files
- `docuaction-api.postman_collection.json` — the collection (Postman v2.1.0;
  imports into the Postman app and runs under the Postman CLI).
- `docuaction-itest.postman_environment.json` — baseUrl + role emails; passwords
  are intentionally empty (credentials never live in git).
- `run.mjs` — dependency-free wrapper around the **Postman CLI** (`postman
  collection run`); injects passwords + seeded ids from the fixture manifest,
  enforces the local-host guard, and selects baseline vs release-candidate mode.
- `../../scripts/seed_integration_fixtures.py` — seeds the synthetic state and
  writes the manifest.

## Install the Postman CLI (one-time; no API key needed for local runs)
- Windows: `powershell -c "iwr https://dl-cli.pstmn.io/install/win64/latest -UseBasicParsing | iex"`
- Linux/macOS: `curl -o- "https://dl-cli.pstmn.io/install/linux64.sh" | sh`

## Two modes (baseline vs release-candidate)
- **baseline** (default): run against the main-based integration backend. The
  folders tagged `(requires #193)`/`(requires #194)` record a CONDITIONAL "not
  on this build" result when those routes are absent — visible, never a silent
  skip.
- **release-candidate** (`--rc`): run against the **combined #193+#194
  candidate revision** (branch `ci/rc-candidate-193-194-2026-11-01`). Those
  routes are REQUIRED; a missing capability FAILS the run. Executed assertions
  are then reported separately from conditional ones.

## Runtime identity
Migrations and seeding run as the privileged **owner** role
(`docuaction_owner`). The backend server itself should run as the **restricted
runtime role** (`docuaction_app`), whose DML privileges are granted by the
migrations — the same identity the app uses in production. Do not run the server
as the owner just to make the harness pass.

## PDF rendering vs generic download
Folder 7 keeps **PDF rendering** coverage distinct from **generic (CSV)
download** coverage. A CSV pass never counts as a PDF pass. The PDF assertion
requires the native WeasyPrint stack (Pango/Cairo; **GTK3 on Windows**); when it
is absent the PDF test records an explicit **BLOCKED** failure naming the
missing stack rather than silently passing. CI installs the stack so the PDF
test runs for real.

## Local run — Windows PowerShell (exact)
Assumes a local PostgreSQL with a `docuaction_owner` role (migrate/seed) and a
non-owning `docuaction_app` role (runtime), the project's isolated-DB
convention, and the backend venv's deps installed.

```powershell
# 0. From the backend repo root. One isolated database.
$pg = "C:\Program Files\PostgreSQL\18\bin"
$env:PGPASSWORD = "test"
& "$pg\psql.exe" -h 127.0.0.1 -p 5499 -U test -d postgres -c "DROP DATABASE IF EXISTS itest;"
& "$pg\psql.exe" -h 127.0.0.1 -p 5499 -U test -d postgres -c "CREATE DATABASE itest OWNER docuaction_owner;"
& "$pg\psql.exe" -h 127.0.0.1 -p 5499 -U test -d itest -c "GRANT USAGE, CREATE ON SCHEMA public TO docuaction_app;"

# 1. Migrate + seed as OWNER (the migrations also grant docuaction_app its runtime DML).
$env:SECRET_KEY   = (python -c "import secrets; print(secrets.token_urlsafe(64))")
$env:ALLOWED_HOSTS = "localhost,127.0.0.1"
$env:ALLOWED_ORIGINS = "http://127.0.0.1:8010"
$env:ENABLE_SAM_EXTRACT_SCREENING = "true"
$env:ENTITY_RESOLVER_SOURCE = "db"
$env:DB_MIGRATION_ROLE = "docuaction_owner"
$env:DB_APP_ROLE = "docuaction_app"
$env:DATABASE_URL = "postgresql+asyncpg://docuaction_owner:test@127.0.0.1:5499/itest"
python -m alembic upgrade head
python scripts\seed_integration_fixtures.py --out integration-fixtures.json

# 2. Start the backend as the RESTRICTED runtime role (new window, same env except DATABASE_URL).
$env:DATABASE_URL = "postgresql+asyncpg://docuaction_app:test@127.0.0.1:5499/itest"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010

# 3. Run the collection (first window; Postman CLI already installed).
cd tests\postman
node run.mjs --manifest ..\..\integration-fixtures.json --baseUrl http://127.0.0.1:8010 --junit postman-results.xml
```

**One supported command for the API tests** (server already up, Postman CLI installed):
```powershell
python scripts\seed_integration_fixtures.py --out integration-fixtures.json; cd tests\postman; node run.mjs --manifest ..\..\integration-fixtures.json --baseUrl http://127.0.0.1:8010 --junit postman-results.xml
```
Add `--rc` to require the #193/#194 routes (release-candidate mode).

Note on rate limiting: the backend's tiered limiter is never disabled. A single
collection run (~4 logins, ~25 requests) is well within limits; running it many
times in quick succession can trip HTTP 429 — wait ~60s for the window to reset.

## CI
`.github/workflows/api-collection.yml` runs the whole thing on a disposable
`postgres:16` service: create DB/roles → `alembic upgrade head` (OWNER) → verify
`docuaction_app` is a genuine non-owner → seed (OWNER) → start uvicorn as
**docuaction_app** → install the Postman CLI → `node run.mjs`. It installs the
native PDF stack so the PDF test runs for real. It uploads the JUnit report, the
server log and `build-identity.json` (exact SHA + mode), never the fixture
manifest. No secrets, least-privilege token, no `pull_request_target`, no
committed npm dependency tree. On `ci/rc-candidate-193-194-2026-11-01` it runs in
release-candidate mode automatically.

## Import into the Postman app (your workspace)
1. Postman → **Import** → **Files** → select `docuaction-api.postman_collection.json`.
2. Import → select `docuaction-itest.postman_environment.json`; pick it as the
   active environment (top-right).
3. Fill the empty env values for a local run: `viewer_password`, `reviewer_password`,
   `qalead_password`, `pm_password` (from your `integration-fixtures.json`), and
   `intake_id`, `job_id`, `candidate_entity_id` (same file). Keep `baseUrl`
   local. Do **not** save real credentials back into the shared environment.
4. Run the collection with the Collection Runner. In baseline mode the folders
   tagged `(requires #193)`/`(requires #194)` self-report "not on this build";
   set the collection variable `rc_mode` to `yes` to require them.

## Scope limitations
- In baseline mode the `sam-extract` / snapshot folders assert fully only against
  a backend that includes PRs #193 (`/sam-extract*`, backfill) and #194 (snapshot
  persist). On a `main`-based build they return 404 and the folder records a
  clear CONDITIONAL "requires #193/#194" result rather than a silent skip.
  Release-candidate mode (the combined candidate revision) executes them for real.
- The candidate is seeded deterministically at the data layer (one REVIEW
  evidence row) — this exercises the enumeration, not the live GSA extract
  fetch. Real external-source availability is a separate concern and is not
  called here.
