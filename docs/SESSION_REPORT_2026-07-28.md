# Session Report — 2026-07-28

**Platform:** DocuAction TEFCA ARC v6.0.0
**Organization:** Alliance Global Tech, Inc.
**Roles operated:** Lead DevSecOps Engineer · Security Architect · QA Lead · Release Manager

---

## Executive summary

- **Security posture roughly halved the defect surface**: score 38.0 → 59.2,
  Criticals 6 → 0, Highs 119 → 40, total findings 309 → 203. About half the High
  reduction is suppression with expiry rather than repair, and that distinction is
  stated wherever the number appears.
- **Two production deployments, both verified**, plus two frontend Static Web App
  deployments. Production answered 200 throughout; the only outage risk taken was
  on dev.
- **A production defect was found and fixed**: the FCC source catalogue was never
  in the deployment artifact, so `load-catalog` had been failing in every deployed
  environment while working perfectly on every developer machine.
- **49 compliance documents generated** across four tiers, including a System
  Security Plan covering 92 controls in 20 NIST families.
- **Test coverage went from effectively nothing to a working suite** — 27 passing,
  5 skipping deliberately, wired into CI.

### Key decisions

1. **Suppression carries an expiry, never permanence.** The 48 dead-code findings
   in `app/routers/` are unreachable today; the fingerprint excludes line numbers,
   so a suppression would survive the code being mounted. The 90-day expiry is the
   only mechanism that brings them back.
2. **Compliance mapping tables show partial statuses.** A matrix showing only
   "Met" reads as concealment to an assessor. Every open finding appears
   consistently across every document that touches it.
3. **Guarding 13 endpoints was a judgment call taken to the user**, not made
   unilaterally — public-by-design bulletin reads were kept public.
4. **Production `create_all()` was left alone.** The Alembic gap is documented
   with a plan and a trigger condition rather than changed mid-session.

---

## Security

| Metric | Before | After |
|---|--:|--:|
| Score | 38.0 | **59.2** |
| Gate | FAIL | **WARN** |
| Critical | 6 | **0** |
| High | 119 | 40 |
| Medium | 50 | 45 |
| Low | 132 | 116 |
| **Total active** | **309** | **203** |

Final figures are from a post-merge `full --all` on the live platform, so they
include the TEFCA Registry code that main brought in. Highs are 40 rather than the
39 measured pre-merge — the merged registry code contributes one.

### A gate FAIL that was investigated, not accepted

The post-merge scan initially returned **gate FAIL**: one secret detected, policy
`block_on_secrets=true`. The flagged value was
`infra/modules/appService.bicep:140` — the Azure **built-in role definition GUID**
for "Key Vault Secrets User". That constant is public, Microsoft-documented, and
identical in every Azure tenant on earth; the declaring line's own comment names
the role. It is an identifier, not a credential. Gitleaks matched it on entropy
and GUID shape alone.

Suppressed with that reasoning and a one-year expiry, and the scan re-run. Gate
returned to WARN at 59.2.

Worth separating from the other gate failure this session, which had a completely
different cause: a scan accidentally run from the repo copy of the platform
reported 44.5 / FAIL / 5 Criticals purely because that copy has no suppression
history and is missing four scanners.

### CVEs resolved — 15

| Package | From | To | CVEs |
|---|---|---|--:|
| starlette | 0.38.6 | 1.3.1 | 7 |
| python-multipart | 0.0.22 | 0.0.31 | 5 |
| pyasn1 | 0.6.3 | 0.6.4 | 3 |

The FastAPI upgrade (0.115.0 → 0.140.13) is what made these fixable. Every
FastAPI below 0.135 pins a starlette ceiling — first `<0.39`, then `<0.51` —
which made all seven starlette advisories structurally unresolvable regardless of
effort. Verified safe: `app.openapi()` identical across both stacks, 255 paths /
266 operations, nothing added or lost.

### Endpoints guarded — 13

| Role | Endpoints |
|---|---|
| `contributor` | `/costs`, `/admin/last-window` |
| `viewer` | `/archive/*` (3), `/briefings/*/docx`, `/briefings/*/pdf`, `/download/*` (3), `/briefings/*/excel`, TEFCA `/dashboard/summary`, `/dashboard/trends` |

`/costs` publishes spend and per-call token counts — precisely the measurement an
attacker needs to size a cost-amplification attack.

### Suppressions — 57, all with expiry

| Count | Reason | Expiry |
|--:|---|---|
| 48 | `app/routers/*` unreachable: no importer anywhere, `app/main.py` is the only entrypoint | 90 days |
| 3 | Scanner blind spot: `require_module()` wrapper declared past where the rule inspects | 180 days |
| 6 | Public by design: bulletin news content and pre-authentication verify-email | 180 days |

### Azure hardening

| Resource | Setting | Before | After |
|---|---|---|---|
| `docuaction-dev` | `healthCheckPath` | `null` | `/health` |
| `docuaction-dev` | `ftpsState` | `FtpsOnly` | `Disabled` |
| `Docuaction` | `ftpsState` | `FtpsOnly` | `Disabled` |

Prod `healthCheckPath` was already configured. `ftpsState` was `FtpsOnly` on both,
not `AllAllowed` as earlier documentation claimed — cleartext FTP was already off.

Key Vault: prod 4 of 8 sensitive settings are references; dev 0 of 5, despite
`docuaction-kv-dev` existing.

---

## Compliance

**49 documents generated**, all rendering, none corrupt.

| Tier | Count | Contents |
|---|--:|---|
| 1 — Policies | 23 | AGT-IAM-004 → AGT-MCM-026 |
| 2 — SSP | 1 | AGT-SSP-001, 92 controls across 20 NIST families |
| 3 — Templates | 12 | AGT-T-001 → AGT-T-012 |
| 4 — Assessment | 6 | AGT-A-001 → AGT-A-006 |
| Extras | 2 | Zero Trust Architecture, Control Traceability Matrix |

Generated from a shared builder (`_generator/agtdoc.py`) so the set is regenerable
and formatting is identical throughout.

### Framework coverage

`owasp_top10` 80% · `nist_800_53` 80% · `hipaa` 80% · `cwe_top25` 40% ·
`owasp_api` 30%

### Open compliance gaps

| Gap | Framework | Severity |
|---|---|---|
| No BAA with Anthropic or OpenAI | HIPAA 164.308(b)(1) | High |
| Audio sent to transcription provider unredacted | HIPAA 164.312(e)(1) | High |
| Audit log lacks tamper-evidence | NIST AU-9 | Moderate |
| `DATABASE_URL` not a Key Vault reference | NIST SC-12 | Moderate |
| Semgrep has never executed | NIST RA-5, SA-11 | Moderate |
| No independent penetration test | NIST CA-8 | Moderate |

---

## Tests

**27 passed · 0 failed · 5 skipped** (304s on the merged tree)

Skips are database-backed tests with no reachable test database, each skipping
with an explicit reason rather than failing.

Three design decisions worth recording:

1. **`TestClient` is constructed without the context-manager form.** Starlette
   only runs lifespan startup inside `with TestClient(app)`, and startup calls
   `create_all()` against the real database.
2. **Database detection authenticates rather than opening a socket.** A Postgres
   listening on the port with different credentials accepts the connection and
   then fails every query — which surfaces as a 500 and reads as an application
   bug rather than a missing test database.
3. **An autouse fixture clears the rate limiter between tests.** The limiter
   allows 10 requests per burst window per IP and every test shares the TestClient
   address, so without it tests fail with 429 in whatever order pytest runs them.
   Resetting rather than disabling keeps the middleware itself under test.

Two of my own assertions were wrong and were corrected rather than carried: a
`/health` leak check that matched the substring `api_key` (which `/health`
legitimately uses when naming `SAM_GOV_API_KEY` as unconfigured — a variable name
is not a credential), and `/bulletin/latest` asserted to return 200 when 404 is
correct before any briefing exists.

---

## Production status

| Environment | Result |
|---|--:|
| Production endpoints | **8/8** |
| Development endpoints | **6/6** (when paced within the documented rate limit) |
| Frontend | **3/3** |

Production scheduler running with 4 jobs. `/health` returned 200 at every check
throughout the session.

### Deployed

- Bulletin Phase 4 source registry + Phase 5 quality gate (dev + prod)
- FCC source catalogue packaging fix (dev + prod)
- Day 1–7 hardening: dependency upgrades, 13 guarded endpoints, PHI audit control
- Frontend accessibility work (both Static Web Apps)

### Not deployed, and why

- **Compliance documents** — documentation, nothing to deploy.
- **Test suite and CI changes** — take effect on the next pipeline run.
- **Azure scripts** — print-only by design; each touches production configuration
  and is executed deliberately.
- **`DATABASE_URL` → Key Vault** — script ready, not run. Requires network access
  to the Key Vault private endpoint.

---

## Git state

| Field | Value |
|---|---|
| Branch | `main` |
| Latest commit | `68b1595` |
| Unpushed | 0 |
| Branches deleted | 20 merged locals |
| Branches kept | `security/pre-azure-hardening`, `security/tefca-arc-hardening` |
| Stash | `stash@{0}` — 12 files, 486+/279−, **not popped** |

### Tags created

`platform-v1.0` · `v2.0-hardened` · `deploy-prod-2026-07-28`

The merge resolved one conflict — `docs/deployment/azure-deployment-guide.md`,
added on both sides. The branch version was taken after verifying it is a strict
superset by heading and change-record diff. The merge was rehearsed on a scratch
branch and the merged tree verified (274 paths / 285 operations, tests passing)
before it was made real.

---

## Things that went wrong

Recorded because the corrections are more useful than the successes.

1. **I took dev down** by applying `--clean` to an Oryx-build app, wiping
   `oryx-manifest.toml` and producing a 503 crash loop. My own guide was wrong;
   it now carries the environment-specific rule.
2. **I caused a spurious deployment failure** by re-running `az webapp deploy` to
   capture its error output. That started a second concurrent build which collided
   with the first. The CLI's `RemoteDisconnected` error had meant only that the
   client lost its polling connection.
3. **A regex meant to add an import consumed `import ` from `import io`**,
   breaking the bulletin download module. Caught because the route count dropped
   255 → 251.
4. **`git add -A` swept a 1.9 MB deployment zip into a commit.** Removed and
   gitignored in a follow-up.
5. **A scan from the wrong platform copy reported 44.5 / FAIL / 5 Criticals.**
   The repo copy has 0 suppressions instead of 65 and is missing four scanners.
   Nothing about the code had changed. Now documented at the top of the platform
   README.

---

## Remaining work

### Needs human input

| Item | Why |
|---|---|
| Execute BAAs with Anthropic and OpenAI | Contractual; blocks HIPAA compliance on the PHI path |
| Commission an independent penetration test | Cannot be self-performed |
| Decide on the 33 remaining unguarded endpoints | Product judgment about what should be world-readable |
| Resolve `stash@{0}` | 12 files of WIP predating this session |

### Manual, ready to run

| Item | Artifact |
|---|---|
| `DATABASE_URL` → Key Vault | `scripts/migrate-db-url-to-keyvault.sh` |
| Branch protection | `docs/deployment/BRANCH_PROTECTION_SETUP.md` |
| Monitoring alerts | `scripts/setup-monitoring-alerts.sh` |
| Dev Key Vault adoption | `docs/runbooks/dev-keyvault-setup.md` |

### Recommended next sprint

1. Run Semgrep once on Linux CI — every score currently understates coverage.
2. Migrate `DATABASE_URL` and `PERIGON_API_KEY` to Key Vault.
3. Add the 15 missing foreign-key indexes and fix the N+1 queries in the bulletin
   pipeline — highest-value low-risk performance work available.
4. Implement audit log hash chaining.
5. Activate Alembic before the next non-additive schema change.
