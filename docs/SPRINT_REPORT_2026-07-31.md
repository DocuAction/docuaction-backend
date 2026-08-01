# Sprint Report — 2026-07-31

**Mode:** autonomous · **Gate:** tests pass + /health 200 + no new Criticals · **Result:** gate met, deployed to dev and prod.

---

## Headline numbers

| Metric | Before | After |
|--------|--------|-------|
| Security score | 55.1 | **81.4** |
| Critical findings | 118 | **0** |
| High findings | 140 | **5** |
| Medium / Low | 262 / 124 | 31 / 124 |
| Total active findings | 644 | **160** |
| Backend tests | 103 | **161** (0 failed, 9 skipped) |
| Release gate | FAIL (block_on_critical) | WARN (reduced scanner coverage only) |

The brief cited a starting score of 59.2; the scan at the start of this sprint
measured **55.1**. The difference is explained below — it was self-inflicted and
is the single largest contributor to the improvement.

---

## The finding that mattered most

**All 118 Criticals were in `pydeps_new/`, a build artifact — not in `app/`.**

`config/projects/docuaction.json` already excluded `**/pydeps/**`, with a comment
recording exactly why: it is the vendored Linux wheel tree used for Azure
deploys and would otherwise be scanned as first-party code. Earlier in the day I
created a *staging copy* named `pydeps_new/` for a deploy rebuild. It did not
match the glob, so 5,472 files of third-party library code entered the scan and
produced 118 Critical + 59 High findings, failing the gate on `block_on_critical`.

Fixed by widening the pattern to `**/pydeps*/**`. Third-party risk is still
covered — properly — by `pip_audit`, which reads the manifest rather than the
unpacked tree.

Worth stating plainly: roughly two thirds of the score improvement is the
removal of noise I introduced, not new hardening. The real hardening is below.

---

## BLOCK 1 — Security

### 1.1 Unguarded endpoints — 62 findings addressed
- **25 dormant routers guarded** (`app/routers/*`). This package is never
  mounted, and its own `__init__.py` calls the missing auth "one deployment
  accident away from being real" and names adding it as step 1 of ever mounting
  it. Each router now carries `dependencies=[Depends(require_role("contributor"))]`,
  so a handler added later inherits the check rather than arriving unguarded.
- **8 live bulletin endpoints guarded** (`viewer`): `/agencies`,
  `/agencies/{id}`, `/coverage/{id}`, `/coverage-assurance/{id}`, `/today/{id}`,
  `/queue/{id}`, `/source-classifications`, `/briefings/{id}`.
- **5 other live endpoints guarded** (`viewer`): migration `/projects`,
  `/projects/{id}`; meetings `/domains`; TEFCA `/methodology`,
  `/discrepancy-taxonomy`.
- **Public-by-design routes declared, not guarded** — the bulletin read surface
  (`/latest/*`, `/history/*`, `/sources*`, `/quality/latest`,
  `/briefings/*/preview`, `/briefings/*/excel`), the auth flows
  (`/api/auth/verify-email`, `/saml/config`), `/api/config`, `/residency`,
  `/status`. Guarding any of these breaks delivery to FCC contacts, who have no
  accounts, or makes sign-in impossible.

Two of the guards initially referenced `require_role` without importing it —
caught before deploy by importing the app, which would otherwise have been a
startup crash rather than a failed test.

### 1.2 XXE — 4 sites fixed
`engine.py`, `fcc_qa_verification.py`, `fcc_talkwalker.py`,
`cspan_fcc_ingest.py` parsed remote RSS with stdlib ElementTree. All now use
`defusedxml` with a stdlib fallback, so a missing dependency degrades collection
rather than breaking it. `defusedxml==0.7.1` added to `requirements.txt` and
vendored into the deploy tree.

Verified with a real payload, not just an import check: stdlib parses a
billion-laughs bomb; defusedxml raises `EntitiesForbidden`. External-entity file
reads are refused too, and ordinary RSS still parses.

### 1.3 DAST bug fixed
`/api/v1/bulletin/costs` was listed in `PUBLIC_OK`, asserting a **guarded**
endpoint should serve anonymous callers. The test was inverted: a correct guard
read as a failure, and had the guard ever been dropped the scanner would have
called it a pass. Removed, and the list corrected to genuinely public routes.

### 1.4 Suppression made exact
The `/excel` entry was a substring hint, so it also whitelisted `/excel-qa` —
the guarded QA sheet carrying relevance scores. Added a separate
`PUBLIC_PATH_EXACT` set checked before the substring hints; `/excel-qa` is
correctly flagged again and cleared by its real guard.

### 1.5 Tests: 103 → 161
New: `test_bulletin_auth.py` (public vs guarded policy), `test_bulletin_excel.py`
(workbook shape, two-sheet agreement, round-trip through a real reader),
`test_injection.py` (SQL/XSS/traversal/XXE at the boundary), `test_rbac.py`
(role hierarchy exactly; endpoints in the deny direction).

`test_npi_validation.py`, `test_tefca_state_machine.py` and
`test_security_headers.py` were requested as new files but already existed with
10, 16 and 5 tests; they were left alone rather than duplicated.

Flag-gated bulletin routes are asserted only when `BULLETIN_AUTH_ENABLED` is on.
Asserting them unconditionally fails in a default environment and proves nothing
about the guard.

### 1.6 Semgrep — BLOCKED, with a better diagnosis
Semgrep 1.172.0 installs and runs on this host, so the standing note that it
"hangs on Windows" is **out of date**. The actual failure is narrower:
**directory targets return empty output; file targets work.** The platform's
probe correctly disables it. Not forced on — `force_on_windows` would have made
the report claim SAST coverage the scan never performed. Needs WSL, Docker or
Linux CI, as the plugin message already says.

**Hazard found:** `pip install semgrep` **downgraded FastAPI 0.140.13 → 0.115.0**,
which is exactly the boundary where auth failures return 403 instead of 401. A
full suite run completed against the wrong stack and was discarded. Pins
restored and the suite re-run. Do not install semgrep into the application's
environment.

### 1.7 False positives suppressed — 15, each with a recorded reason
- 4 × SQL injection — every value is a bound parameter; only fixed literal
  fragments are interpolated.
- 3 × Key Vault literal in `config.py` — this *is* the control; the rule matches
  the detection pattern inside the guard that refuses to start on it.
- 5 × Key Vault literal in Bicep — declaring a KV reference in IaC is the
  correct way to configure one.
- 2 × PHI in logs — the logged value is an **NPI**, a provider identifier that
  is publicly searchable in NPPES. Not patient PHI.
- 1 × FHIR route without access control — the router carries a router-level
  `require_role("reviewer")`; the rule inspects per-route decorators only.

### Accepted residual (5 High)
`/api/plans/info` is public pricing data. It is deliberately **not** added to the
exact-public set: the rule sees only the decorator path, so an entry for
`/info` would also whitelist `/api/v1/case-management/info`, which is protected
and an explicit DAST target. Silencing a real finding to clear a cosmetic one is
the wrong trade. Remaining items are in `docs/` and `frontend/` scanner scope.

---

## Deployments

| Target | Result |
|--------|--------|
| dev backend | deployed (see incident below), verified |
| prod backend | status 4 active, verified |
| dev SWA | deployed, verified |
| prod SWA | deployed, verified |

### Incident: dev outage during this sprint
The first dev deploy failed (status 3) and, because `--clean` empties wwwroot
before unpacking, **dev returned 503 on every route** until redeployed. Cause was
a transient Oryx packaging race, visible only in the container log:

```
tar: ./antenv/lib/python3.12/site-packages/pydantic/_internal/_typing_extra.py:
     file changed as we read it
Falling back to gzip compression.
Deployment Failed.
```

Not a code fault — the Oryx build itself logged 0 errors. Redeploying recovered
it. Worth recording: on dev, **status 4 + active is not sufficient evidence the
new code is running** — an earlier deploy reported success while the old
container kept serving until an explicit `az webapp restart`. Confirm with an
endpoint that exists only in the new build.

### Ordering hazard handled
Guarding `/agencies` and `/coverage` on prod would have broken the bulletin
page, because the deployed frontend called both anonymously. Frontend was
updated to send `authHeaders()` and deployed to both SWAs; verified in the
served bundle (`{headers:z()}` on both call sites).

---

## Not done

**BLOCK 2 (TEFCA gaps) — not started.** Wiring the NPI validator and state
machine into the API, entity CRUD, and the audit hash chain. The gap analysis
from the previous sprint (`docs/TEFCA_IMPLEMENTATION_GAPS.md`) still stands and
already specifies each item.

**BLOCK 3 (bulletin sources / editorial) — not started.** ~25 RSS sources,
commissioner names in the boolean profiles, the Google News + GDELT QA layer,
and the 8 editorial improvements.

**BLOCK 4 (infrastructure) — not started.** Key Vault migration for
DATABASE_URL and PERIGON_API_KEY; N+1 query fixes.

These were deprioritised against the stated priority order: Block 1 is security
(priority 2) and the dev outage was priority 1. Blocks 2–4 are each a sprint's
worth of work on their own — Block 3 alone requires verifying ~25 feed URLs and
changing the collection pipeline, which is not something to land unattended
alongside a security change.

**Also interpreted, not confirmed:** a mid-sprint message reading `yes && yes`
was taken as "continue, and yes to branding the QA sheet." The QA sheet was
**not** restyled — it is a working internal tool with different columns and
different consumers, and it sat behind the unfinished blocks above.

---

## Recommended next sprint

1. **Verify the dev deploy path.** Two of four dev deploys this sprint reported
   failure; one caused an outage. The Oryx race is the immediate cause but the
   `--clean` blast radius is the real risk. Consider deploying dev the way prod
   is deployed (vendored `pydeps`, build disabled), which removes Oryx entirely.
2. **Semgrep in CI**, not on a workstation — clears the WARN gate and adds the
   SAST coverage this scan is missing.
3. **Block 2**, in the order the gap analysis gives: the NPI validator and state
   machine already exist and are unit-tested; they need routes, not code.
4. **Gitleaks** — the other missing scanner; `block_on_secrets` is policy but
   nothing currently enforces it.
5. Revisit `max_high_cves: 25` now that Highs are at 5.

---

## Files changed

Backend (43): scanner config and rules (4), XXE sites (4), guarded live routes
(4), dormant routers (25), `requirements.txt`, 4 new test files, this report.
Frontend (4): `page.js`, `CoverageAssurance.js`, `QaDashboard.js`,
`OpsConsole.js` — all sending `authHeaders()` to newly guarded routes.
