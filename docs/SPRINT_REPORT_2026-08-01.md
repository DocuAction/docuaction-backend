# Sprint Report — 2026-08-01 (Blocks 2 and 3)

**Mode:** autonomous · **Gate:** tests pass + /health 200 · **Result:** gate met, deployed to dev and prod.

---

## Headline

| Metric | Before | After |
|--------|--------|-------|
| Backend tests | 161 | **188** (0 failed, 9 skipped) |
| TEFCA registry endpoints | 19 | **22** |
| Dev registry entities | 0 | **18** |
| NPI validator reachable from the API | no | **yes** |
| State machine reachable from the API | no | **yes** |
| Dev deploy model | Oryx build | **vendored pydeps** (same as prod) |

Block 2 is delivered. **Block 3 turned out to be already built** — see below; that
finding is the substantive result there, not new code.

---

## BLOCK 2 — TEFCA wiring

Two modules were implemented, unit-tested, and reachable from nothing. The rules
existed on paper while the API let any status follow any other. Nothing below
re-implements them; it calls them from the request path.

### 2.1 NPI validator wired into import
`persist_import` — the shared path for BOTH the CSV and FHIR importers — now
calls `validate_for_import` on every NPI identifier. It **flags, never rejects**:
existing RCE and seed data contains NPIs with bad check digits, and refusing the
import would break a working system to enforce a rule those records predate.

Each flag writes an `npi_flagged` audit row, and the batch result gained
`validation_warnings` and `npi_flagged_count` so an operator sees them without
trawling the audit table.

Also removed a **duplicate implementation**: `verification.py` carried its own
copy of the CMS Luhn check. Two implementations of one rule is one opportunity
for them to disagree, and the copy was the one nothing exercised. It now
delegates to `app/services/npi_validator.py`.

### 2.2 / 2.3 State machine wired + transition endpoint
New `PATCH /api/tefca/registry/entities/{id}/status`, `guard("contributor")`,
validated by `state_machine.py`.

Refusals are **audited, not just returned** — an attempt to move an entity
straight from draft to active is exactly the event a reviewer wants to see, and
recording only successes hides it. The message names the missing step rather
than saying "invalid":

```
Cannot transition from draft to active. An entity must pass verification before
becoming active; submit it for verification first. Allowed from draft:
pending_verification.
```

### 2.4 Verification endpoint with confidence scoring
`POST /entities/{id}/verify` now runs the internal checks, probes the external
sources, scores confidence, transitions `draft → pending_verification`, and
writes `verification_started` / `verification_completed` audit rows.

The scoring decision that matters: **confidence is computed over the sources
that ANSWERED.** A source that is down contributes neither credit nor penalty
and the divisor shrinks to match. Scoring an outage as a mismatch would turn a
third-party's bad minute into an accusation against the entity. When nothing
answers the score is `null`, never `0.0` — "we do not know" and "everyone
disagreed" are different claims, and `coverage` is returned alongside so a high
score over thin coverage can be discounted.

Weights: NPPES 40, PECOS 20, SAM 10, OIG LEIE 10, State 10, IRS 10.

Two corrections made during verification:
* My first probe guessed connector method names that do not exist
  (`check_nppes`); the real interface is `connector.lookup_by_npi`. Caught by
  running it end-to-end rather than trusting the unit tests.
* **OIG LEIE is an exclusion list** — a hit is bad news and absence is the good
  outcome. It is inverted before scoring so "matched" means the same thing
  (corroborates the entity) across every source.

SAM.gov is deliberately **not** probed: it is keyed on UEI, not NPI, and the
registry holds no UEI for these entities. Asking with the wrong identifier would
produce a confident "no match" that means nothing. It stays unavailable, which
shrinks the divisor honestly.

### 2.5 confidence_score column
Added to `tefca_reg_entities` as a nullable float, plus an
`ADD COLUMN IF NOT EXISTS` in the startup schema repair (`create_all` cannot add
a column to an existing table). No default: NULL means "never verified", which
is not the same claim as 0.0.

### 2.6 Dev seed — 18 entities
`POST /api/tefca/registry/dev/seed` (admin), running through the **real CSV
importer** rather than inserting rows, so seeding exercises the same parser, NPI
validation and audit writes a real import does. A seed that bypassed the import
path could pass while the path was broken.

18 entities: 2 QHINs, 6 participants, 10 sub-participants across VA/MD/NY/CA/TX,
spanning draft, pending_verification, active, suspended and inactive. **Two NPIs
fail the check digit on purpose** so the flag-don't-reject behaviour has
something to flag. Refuses a populated registry unless `force=true`.

Verified on dev: 18 imported, 0 errors, **2 NPIs flagged**.

### 2.7 Audit logging
New `app/tefca_registry/audit.py` — one call site for the table that already
existed. It never raises (a lost audit row is recoverable; a 500 on a status
change is not) and never commits (the caller owns the transaction, so the audit
row lands in the same commit as the change it describes).

Covered: entity created (import), NPI flagged, status changed, status change
refused, verification started, verification completed, import completed.

### 2.8 Tests — 161 → 188
`tests/test_tefca_lifecycle.py`, 27 cases: transition legality against the state
machine, refusal messages that name the missing step, the null-vs-zero
confidence distinction, unavailable-source divisor handling, NPI flag-not-reject,
the shared-validator delegation, seed-data shape, and anonymous gating on all
three new endpoints.

### End-to-end proof on dev
```
seed                     -> 18 imported, 0 errors, 2 NPIs flagged
draft -> active          -> 400 with the actionable message above
draft -> pending_verif.  -> 200, allowed_next: [active, draft]
verify                   -> 200, confidence null, coverage 0.0
```

The null confidence is **correct, not a defect**: the seeded NPIs are synthetic
by design, so no real registry can corroborate them, and every source reports
unavailable. It does mean the non-null scoring path is proven by unit tests
rather than end-to-end — demonstrating a real score needs an entity with a real,
NPPES-listed NPI, which the seed deliberately avoids.

---

## BLOCK 3 — already built

Checked before writing anything. **20 of the 21 requested sources are already
wired**, across 755 feed URLs in 3 pools:

| Requested | Status |
|-----------|--------|
| FCC all-content, Federal Register API | present |
| Fierce, RCR, Light Reading, Broadband Breakfast, Telecompetitor, TVNewsCheck, SpaceNews, Radio World | present |
| NYT, WaPo, CNBC, Politico, The Hill, Ars Technica, TechCrunch, CNET, Fox Business | present |
| Google News RSS | present |
| GDELT | present as a module (`gdelt_doc_ingest`, `gdelt_tv_ingest`), wired at engine.py:3123/3170 |

* **3.4 QA layer** — `fcc_qa_verification.run_qa_verification` already exists and
  is already called at `engine.py:3355`, wrapped so a failure leaves the
  briefing untouched. Nothing to build.
* **3.5 Commissioner names** — all five appear **8 times each** in
  `fcc_boolean_search.py`, evenly across profiles. Nothing to add.

### The finding that replaces "add more sources"

Prod telemetry (`/api/v1/bulletin/sources/health`):

```
total_sources     276
ever_produced      45
active_last_24h    22
never_produced    231
```

**Only 22 of 276 registered sources produced an article in 24 hours; 231 have
never produced one.** The gap is not the size of the source list — it is that
84% of registered sources yield nothing, which the endpoint's own note
attributes partly to "sources the collectors do not yet call". That is a wiring
gap, and adding a 277th source would not move it.

### Feed reachability spot-check
16 of 21 requested URLs return a parseable feed from this workstation. Five do
not: FCC all-content, FCC headlines, Politico and Telecompetitor return **403**,
GDELT returned 429 (my own probe rate). The 403s persist with a browser UA, so
they are blocking by network rather than user-agent — I could not conclude from
here that they are dead in production, and did not change the feed list on that
basis. The engine already retries fcc.gov with a browser UA; that fallback is
scoped to fcc.gov only, which is worth widening if prod telemetry shows the
other two failing there too.

---

## Deployments

| Target | Result |
|--------|--------|
| dev backend | **converted to vendored pydeps**, deployed, verified |
| prod backend | deployed, verified (needed an explicit restart — see below) |

### Dev moved off Oryx
Per the brief, dev now uses the prod model: `SCM_DO_BUILD_DURING_DEPLOYMENT=false`,
`ENABLE_ORYX_BUILD=false`, `PYTHONPATH=/home/site/wwwroot/pydeps`, startup
`python -m gunicorn`. Both dev deploys this session reported **status 4 on the
first attempt**, against two failures and one outage under Oryx yesterday. The
transient `tar: file changed as we read it` race is gone because the failing
component is gone.

### Status 4 still is not proof
Prod reported status 4 / active while continuing to serve the previous build —
`PATCH /entities/{id}/status` returned 404 for several minutes. An explicit
`az webapp restart` fixed it. Confirm with a route that exists **only** in the
new build; `/health` answers 200 from old code the whole time and proves nothing.

---

## Not done

* **2.4 confidence end-to-end against a live match** — proven by unit tests and
  by the null path on dev; a non-null score needs a real NPPES-listed NPI.
* **Task 2.4's IRS and State sources** — weighted in the model but no connector
  exists for either, so they always report unavailable.
* **Block 3 code changes** — none needed; see above.
* **Block 4 (infrastructure)** — not in this sprint's scope.

### Local environment drift (does not affect production)
Yesterday's semgrep install moved **11 pinned packages**, including
`python-multipart` back to 0.0.22 (5 High CVEs) and `pyasn1` off the fixed
0.6.4. Both restored. `asyncpg==0.29.0` cannot be restored on this workstation —
no Windows wheel for Python 3.13 — so the local env runs 0.30.0. The deploy
artifact is built from `requirements.txt` with Linux wheels and was verified to
carry the correct pins (`python_multipart-0.0.31`, `pyasn1-0.6.4`,
`fastapi-0.140.13`, `pydantic-2.9.2`), so production is unaffected.

---

## Recommended next

1. **Chase the 231 silent sources**, not a longer source list. Start with the
   endpoint's own hint: which registered sources have no collector calling them.
2. **Widen the browser-UA retry** beyond fcc.gov if prod telemetry confirms
   Politico/Telecompetitor 403 there too.
3. **Connectors for State and IRS**, or drop their 20% combined weight — right
   now they can only ever reduce coverage.
4. Seed one entity with a real NPPES NPI in a non-prod fixture so the confidence
   path can be demonstrated, not just unit-tested.
