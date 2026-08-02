# Sprint Report — SAM.gov + All Remaining Items

**Contract:** 7571MN26F80064 · **Date:** 2026-08-02

All figures from actual execution. Anything not run is recorded as **Not Executed** with its reason.

## Readiness matrix

| Capability | Status | Evidence |
|---|---|---|
| SAM.gov connector (v3 entities + v4 exclusions) | **Built — NOT operational (no API key)** | `docs/SAM_GOV_API_KEY_SETUP.md` |
| SAM.gov key provisioning | **Blocked — requires interactive SAM.gov login** | steps documented |
| Classification rules v2 (SAM wired in) | **Active** — v1 retired, v2 live on dev | `bucket_classifier.py`, 8 new tests |
| NewsData.io connector | **Built — inert (no API key)** | `newsdata_source.py` |
| Railway → Azure DNS cutover | **Plan documented — manual (DNS access)** | `docs/RAILWAY_DNS_CUTOVER_PLAN.md` |
| ZAP DAST in CI | **Pipeline created — Not Executed** | `.github/workflows/zap-scan.yml` |
| StackHawk DAST in CI | **Pipeline created — Not Executed (needs HAWK_API_KEY)** | `.github/workflows/stackhawk-scan.yml` |
| Risk acceptance register | **6 entries, unsigned** | `docs/RISK_ACCEPTANCE_REGISTER.md` |
| Bulletin source investigation | **Complete — 431 feeds, two passes** | `docs/audit/SOURCE_HEALTH_INVESTIGATION.md` |
| Dead source deactivation | **78 deactivated** | `app/bulletin_intelligence/dead_feeds.py` |
| Entity soft-delete endpoint | **Live and verified** | `DELETE /api/tefca/registry/entities/{id}` |
| N+1 query fixes | **None needed on the path inspected** | see below |
| Dev password rotation | **6/6 rotated and verified** | `docs/SESSION_STATE.md` (gitignored) |
| Backend tests | **274 passed, 22 skipped, 0 failed** | `test-results.xml` |
| Deployment | dev + Azure prod verified | below |

## SAM.gov — built, not operational

No `SAM_GOV_API_KEY` exists on dev, prod, or in `.env`. Not expired — never provisioned.

Both endpoints probed directly: **v3/entities and v4/exclusions each return HTTP 404 with `DEMO_KEY` and with no key.** SAM returns 404 rather than 401/403 for an unauthorised key, which is why this read as a wrong-URL problem in earlier sprints. The URLs are correct.

Implemented: UEI exact match, legal-name fallback, `ambiguous` flag when a name matches more than one entity, and an **independent** v4 exclusions query. The v3 `exclusionStatusFlag` is a summary on the registration — an entity with no SAM registration can still appear on the exclusions list, so trusting v3 alone would report "not found, therefore fine" about a debarred party.

**A key alone is necessary but not sufficient:** SAM is keyed on UEI, which the registry does not capture.

## Rules v2 — and a real defect it fixes

v1 retired (5 rules, `retired_date` set), v2 active (5 rules).

SAM is wired in as a **disqualifier, never a requirement.** Requiring `sam_gov: verified` for B1 would drop every entity out of B1 while SAM has no key — reclassifying the whole registry on deploy. Every v2 SAM condition fires only on a positive finding, so with no key classification is byte-identical to v1 (`test_v2_is_identical_to_v1_when_sam_is_silent`).

v2 also fixes a latent v1 bug: **RULE-005 matched only status `debarred`, but the connector emits `excluded`** — so a SAM-excluded entity with clean NPPES/PECOS was classified **B1 "No Discrepancy"**. Now B4.

## Bulletin sources — the first measurement was wrong

431 unique feed URLs, probed twice.

| Category | Count | Share |
|---|---|---|
| ACTIVE | 161 | 37.4% |
| TRANSIENT_RECOVERED (working) | 78 | 18.1% |
| DEAD_URL (404/410 twice) | 78 | 18.1% |
| ACCESS_BLOCKED (401/403) | 58 | 13.5% |
| STALE | 38 | 8.8% |
| UNREACHABLE | 15 | 3.5% |
| SERVER_ERROR / RATE_LIMITED | 3 | 0.7% |

The fast sweep (concurrency 24) reported 232 failures. A gentle re-probe (concurrency 4, 30 s) found **78 of them — 34% — working perfectly.** Acting on the first sweep alone would have deactivated 78 healthy feeds while producing a report that looked like diligent cleanup.

**Only twice-confirmed 404/410 feeds were deactivated (78).** The 58 access-blocked feeds were deliberately left active: the feed exists, our client is refused, most likely bot protection reacting to the User-Agent. That is a fixable headers bug, and deleting them would convert it into permanent lost coverage. **Fixing those 58 is the largest recoverable block of coverage available.**

## N+1 — premise did not hold

The TEFCA registry list path already batches identifiers explicitly (`_attach_identifiers`, one query per page) and uses no lazy-loaded ORM relationships, so there is no N+1 to fix there. **Query-count instrumentation was Not Executed** — it needs a local database, and the local Postgres rejects authentication.

## Password rotation

All 6 dev accounts rotated via the admin API (direct DB access is blocked by the Azure Postgres firewall). Verified: all 6 new passwords authenticate; the old admin password now returns 401.

`admin@docuaction.io` was rotated **last** by design — the endpoint stamps `tokens_revoked_at`, so rotating it first would have invalidated the token driving the rotation.

Credentials are in `docs/SESSION_STATE.md`. **That file was tracked in git**; it has been untracked and gitignored, because writing credentials to a tracked file and then committing would have put live passwords in history permanently.

## Deployment

| Target | Result |
|---|---|
| dev (`docuaction-dev`) | Deployed, restarted, `/health` 200, `environment=development` |
| Azure prod (`Docuaction`) | Deployed `--clean true`, restarted, `/health` 200, `environment=production`, guarded endpoint 401 |
| Rollback artifact | `prod-deploy.prev.zip` preserved |

**`api.docuaction.io` still did not receive this deploy.** It CNAMEs to `thzu1ngo.up.railway.app` and answers `Server: railway-hikari`. Azure production answers on `api-prod.docuaction.io`. Cutover plan documented; it needs registrar access.

## Open items

- [ ] Obtain SAM.gov API key (interactive login required)
- [ ] Obtain NewsData.io API key
- [ ] Execute the Railway → Azure DNS cutover
- [ ] Set `HAWK_API_KEY` and run the first StackHawk scan
- [ ] Fix the 58 access-blocked feeds (User-Agent/headers)
- [ ] Sign the risk acceptance register
- [ ] Rehearse a database restore and measure RTO
- [ ] Populate UEI on registry entities so SAM can match exactly
