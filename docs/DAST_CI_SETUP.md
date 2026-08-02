# DAST in CI — OWASP ZAP + StackHawk

**Contract:** 7571MN26F80064 · **Date:** 2026-08-02

Dynamic Application Security Testing could not be executed from the development
workstation (no container runtime, no JRE — see
`docs/security/ZAP_FINDING_VALIDATION.md`). It is instead wired into GitHub
Actions, where a Linux runner provides both.

## Two scanners, deliberately

| | OWASP ZAP | StackHawk |
|---|---|---|
| Workflow | `.github/workflows/zap-scan.yml` | `.github/workflows/stackhawk-scan.yml` |
| Surface | **Unauthenticated** | **Authenticated** (bearer token) |
| Schedule | Mondays 06:00 UTC | Mondays 06:30 UTC |
| Input | OpenAPI spec | OpenAPI spec + minted JWT |
| Cost | Free, open source | Free tier, no card |
| Secrets needed | none | `HAWK_API_KEY`, `DAST_USER`, `DAST_PASSWORD` |

They are not redundant. ZAP scans what an attacker sees before authenticating.
StackHawk scans past the login boundary, which is where the TEFCA ARC endpoints,
the review workflow and every role-guarded route actually live — an
unauthenticated scanner records those as `401` and moves on. Running only the
authenticated scan would skip the boundary an attacker hits first; running only
the unauthenticated scan would skip almost the entire application.

## Both are DEV ONLY

Each workflow begins with a guard that fails the job if the target matches a
production host. An active scan submits hostile input to every discovered
endpoint; against production that is indistinguishable from an attack on live
records.

Neither scanner gates a merge (`fail_action: false` / `continue-on-error`). A
DAST report is evidence to be triaged, not a build gate — gating on raw findings
trains people to silence the scanner rather than read it.

## StackHawk setup (one-time, requires a human)

1. Sign up at **https://app.stackhawk.com** — free tier, no credit card.
2. Create an application; note its **Application ID** (a UUID).
3. Create an **API key** under Settings → API Keys.
4. Add repository secrets in GitHub → Settings → Secrets and variables → Actions:

   | Secret | Value |
   |--------|-------|
   | `HAWK_API_KEY` | the API key from step 3 |
   | `DAST_USER` | a dev account email used to mint the scan token |
   | `DAST_PASSWORD` | that account's password |

5. Set `APP_ID` in `stackhawk.yml` to the Application ID from step 2.

Until `HAWK_API_KEY` exists the StackHawk job **skips** rather than fails, so the
pipeline does not sit permanently red and get ignored.

**Use a dedicated scan account, not a real operator's.** The scan will generate
audit-log noise and may trip lockouts; that should not land on an account
someone depends on.

## Running a scan manually

GitHub → Actions → *OWASP ZAP DAST Scan* (or *StackHawk DAST Scan*) → **Run
workflow**.

## Reading the results

Both jobs upload artifacts with 90-day retention:

- ZAP → `zap-report-<run>`: `report_html.html`, `report_json.json`, `report_md.md`
- StackHawk → `stackhawk-report-<run>` plus the StackHawk dashboard, which
  tracks findings across runs so a regression is visible as a trend rather than
  a fresh discovery.

### Finding validation workflow — mandatory

**DAST tools produce false positives. Do not auto-fix.**

1. **Detect** — scanner reports a finding.
2. **Reproduce manually** — reconstruct the request with `curl`. If it cannot be
   reproduced by hand, it is not yet a finding.
3. **Confirm exploitability** — does it actually disclose data, bypass a control
   or crash the service? A non-standard status code is not a vulnerability.
4. **Fix only if confirmed.**
5. **Re-test after fix**, and re-test against the *deployed* build, not the
   working copy.

Record every finding and its disposition — including the ones dismissed — in the
assessment package. A validation log showing two findings investigated and
dismissed is stronger evidence than a report showing zero findings.

This is not theoretical. In the Security Validation sprint, both reported
failures were confirmed **false positives in the test assertions**, and one
"fix" was deployed, re-tested and **still failed**, which is what revealed the
actual root cause. Neither would have been caught by auto-fixing.

## Known scan constraints

- **Rate limiting.** The app allows 20 login attempts / 15 min per IP. The
  StackHawk config injects a pre-minted bearer token rather than re-logging in,
  because replaying the login would trip the limiter within seconds and the rest
  of the scan would read as a wall of 429s rather than real findings.
- **Excluded paths**, each for a stated reason in `stackhawk.yml`: auth
  endpoints (lockout), registry import and seed (they mutate the ARC registry,
  which has no entity delete for pre-existing rows and would permanently
  contaminate the sample frame every weekly report draws from), and bulletin
  `/send` (sends real email).
- **Cold start.** Both workflows poll `/health` before scanning; a scan against
  a cold App Service produces timeouts that look like findings.
