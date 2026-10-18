# Live Part A/B browser proof — exact recipe (2026-10-04, R27-3)

The script that originally produced `SEED_JSON` for
`tests/e2e/live-partb.spec.mjs` was never committed and no copy survived
into this session. `seed_live_partb.py` in this folder is a reconstruction
that reuses the SAME real pipeline and the SAME committed corpus
(`manifest_b.json`) `tests/test_seeded_corpus_b_2026_10_04.py` already
exercises, by importing that test module's own helper functions directly.
It does not duplicate that corpus; there is exactly one place that builds
it.

**What changed from the original spec, and why (read this before treating
a number below as historical fact):**

- The original spec's comment said "4 of 12 Verified — checks incomplete,"
  and that number is confirmed correct against the current corpus too —
  but not for the reason first assumed while rebuilding this script. The
  committed corpus (`manifest_b.json`) has exactly three SAM.gov
  source-fault seeds (B04, B05, B06). A direct query against the seeded
  database initially found only those three and the adapted spec was
  written to assert 3, which then failed against the real, running page:
  it reads 4. The cause, found by querying `tefca_dimension_evidence`
  directly rather than guessing again: `_Sources.install()` never fakes
  CMS PPEF (`SourceResult.unavailable(..., "synthetic: CMS data API not
  exercised by this corpus")`, deliberately, per that test module's own
  comment), so EVERY NPI-bearing entity in the corpus — including B01,
  the one seed meant to be entirely clean — carries its own CMS PPEF
  enrolment gap on top of whatever SAM.gov/NPPES/LEIE scenario applies.
  B01 + B04 + B05 + B06 = 4. The adapted spec asserts **4 of 12**, matching
  the original, confirmed against the real database rather than assumed
  from the manifest alone.
- The original spec clicked "View details" from the deliveries LIST to
  reach the detail page. This reconstruction's corpus is built with
  `ingest_delivery()` directly (the same call `test_seeded_corpus_b_2026_
  10_04.py` uses), which creates an `rce_source_intakes` row but no
  `rce_delivery_jobs` row — and the deliveries LIST page
  (`GET /api/tefca/rce/delivery-jobs`) only lists delivery-job
  registrations, not bare intakes, so this delivery would never appear
  there. The adapted spec opens the detail page directly by URL
  (`/tefca-arc/deliveries/detail/?job=<intake_id>`) instead of clicking
  through the list. The list → row → detail click path is NOT re-proven
  here because it is already proven, separately and specifically, by
  `tests/e2e/live-journeys-round26.spec.mjs` Journey A (Program Manager
  registers a delivery end to end through the real upload form and the
  real list). Re-seeding a second delivery through the full asynchronous
  registration pipeline just to repeat that one click was judged
  unnecessary duplication, not a dropped assertion.

Everything else — Overview source readiness and its four-column findings
table, Verification's completeness split and the recheck panel's
maker/checker behaviour, the case workspace's uncleared-earlier-concern
banner, Sources & Connectors' read-only policy display, the QA Lead's
approve-and-run of the recheck, and the no-raw-JSON check on every page
visited — is preserved with the same intent as the original spec.

## 1. Disposable database

```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5534 -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS test_partb_r27;" \
  -c "CREATE DATABASE test_partb_r27 OWNER docuaction_owner;" \
  -c "ALTER DATABASE test_partb_r27 SET timezone = 'UTC';"
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5534 -U postgres -d test_partb_r27 -v ON_ERROR_STOP=1 \
  -c "ALTER SCHEMA public OWNER TO docuaction_owner;" \
  -c "GRANT ALL ON SCHEMA public TO docuaction_owner;" \
  -c "GRANT USAGE, CREATE ON SCHEMA public TO docuaction_app;"
```

(Port `5534` and the `docuaction_owner`/`docuaction_app` role names match
this repository's other isolated-DB recipes. Adjust to the local
Postgres instance in use; never point this at a shared or production
database.)

## 2. Migrate to head

```bash
SECRET_KEY=$(printf 't%.0s' {1..64}) ALLOWED_HOSTS=* DB_APP_ROLE=docuaction_app \
DATABASE_URL=postgresql+asyncpg://docuaction_owner:x@127.0.0.1:5534/test_partb_r27 \
python -m alembic upgrade head
```

## 3. Seed (produces the SEED_JSON the spec reads)

`ENTITY_RESOLVER_SOURCE=db` is REQUIRED here, not optional — without it the
entity resolver defaults to its mock path and no
`tefca_dimension_evidence` rows are written at all, which silently empties
every completeness/recheck assertion downstream. This was the first
failure mode hit while rebuilding this script and is recorded here so it
is not rediscovered.

```bash
cd <backend repo root>
SECRET_KEY=$(printf 't%.0s' {1..64}) ALLOWED_HOSTS=* \
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5534/test_partb_r27 \
ENTITY_RESOLVER_SOURCE=db \
python tests/fixtures/seeded/seed_live_partb.py /path/to/seed_partb.json
```

No network access is required or attempted for NPPES/OIG LEIE/SAM.gov/CMS
PPEF — every one of those is a deterministic in-process fake installed by
`test_seeded_corpus_b_2026_10_04._Sources.install()`. The "PPEF enrollment
unavailable… All connection attempts failed" lines this prints are
expected: CMS PPEF/revocation are genuinely not faked by this corpus (the
corpus does not exercise them) and this sandboxed environment has no
outbound network, so the real connector fails closed immediately. That is
correct, pre-existing behaviour, not an error in this script.

## 4. Start the real backend (no mocks at this layer — see note on step 6)

```bash
SECRET_KEY=$(printf 't%.0s' {1..64}) ALLOWED_HOSTS=* \
ALLOWED_ORIGINS=http://127.0.0.1:8103,http://127.0.0.1:4178 \
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5534/test_partb_r27 \
ENABLE_IQVIA_SOURCES=true ENABLE_CONTROLLED_RECHECKS=true \
python -m uvicorn app.main:app --host 127.0.0.1 --port 8103
```

`ALLOWED_ORIGINS` MUST include the static file server's own origin
(step 5), or every browser request fails CORS preflight with a generic
"Cannot reach server" message that has nothing to do with the server
actually being reachable — the second failure mode hit while rebuilding
this recipe.

`ENABLE_CONTROLLED_RECHECKS=true` is REQUIRED for journey C/F (the
recheck panel): without it `GET /api/tefca/rce/rechecks/.../status`
returns `enabled: false`, and the panel renders NEITHER the "Approve
recheck" button NOR the "a different person must approve" message for
ANY job, even one that was genuinely requested — it looks identical to
"recheck support is off here," which is correct, since that is exactly
what it means. This is the one feature flag this whole journey needs
that is OFF by default; every other flag used in this recipe
(`ENABLE_IQVIA_SOURCES`, `ENTITY_RESOLVER_SOURCE=db`) was already known
from earlier rounds. SEED_RULES_V4 and the proposed (inactive) source
policies are deliberately left at their defaults throughout this
recipe — nothing here activates either.

## 5. Build and serve the static frontend export

```bash
cd <frontend repo root>
NEXT_PUBLIC_API_URL=http://127.0.0.1:8103 npm run build
node tests/e2e/serve-out.mjs 4178
```

## 6. Run the spec

```bash
LIVE_API=http://127.0.0.1:8103 \
SEED_JSON=/path/to/seed_partb.json \
E2E_OUT=/path/to/e2e-out-partb \
LIVE_ANALYST_USER=journey-analyst@synthetic-test.docuaction.invalid \
LIVE_ANALYST_PASSWORD=JourneyAnalyst!2026 \
LIVE_QALEAD_USER=journey-qalead@synthetic-test.docuaction.invalid \
LIVE_QALEAD_PASSWORD=JourneyQALead!2026 \
npx playwright test tests/e2e/live-partb-round27.spec.mjs --config playwright.config.mjs
```

The journey-analyst/journey-qalead accounts are the same fixed synthetic
identities `test_sam_e2e_delivery_path._ensure_journey_users()` creates
(called by the seeding script itself); no password is written to the
seed JSON or to either spec file.

**Why no mock is needed for the live SAM.gov recheck run (journey G):**
when the QA Lead clicks "Run next batch" in the browser, that recheck
executes inside the LIVE SERVER process (step 4), which is a separate
process from the seeding script (step 3) and was never monkeypatched —
monkeypatches do not cross process boundaries. This sandboxed environment
has no outbound network reachability (confirmed repeatedly this round:
every real external connector call fails immediately with a connection
error, not a slow timeout), so the real SAM.gov connector genuinely
cannot be reached and the recheck correctly, truthfully stops
"Stopped — source still unavailable." No external source is actually
queried; the attempt fails closed at the network layer before any
request leaves the machine, matching this round's "no live external
source queries" boundary without requiring a second set of mocks inside
the running server.

## 7. Cleanup

```bash
# stop the two processes started in steps 4 and 5 (Ctrl-C, or):
#   Windows: Stop-Process on their PIDs
#   POSIX:   kill the two PIDs
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5534 -U postgres -d postgres \
  -c "DROP DATABASE test_partb_r27;"
```

The database is disposable and safe to drop after the run; nothing it
contains is referenced from anywhere else. Keep the written `SEED_JSON`
and `E2E_OUT` directory only as long as the evidence is needed.
