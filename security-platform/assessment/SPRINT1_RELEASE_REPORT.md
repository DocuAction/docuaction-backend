# Sprint 1 — Release Report

**Critical & High Security Remediation** · DocuAction platform
**Date:** 2026-07-26 · **Status: REVIEW ONLY — nothing merged, pushed, or deployed**

Per-branch reviews: `sprint1_review/` · Merge plan: `sprint1_review/merge_plan.md` ·
Validation: `sprint1_review/validation_checklist.md`

---

## 1. Executive summary

Sprint 1 remediated the platform's one Critical and two High security findings plus one
Medium, closing an unauthenticated PHI-handling API surface, minimising PHI sent to a
third-party AI provider, hardening secrets validation, and stopping the destruction of
audit records — across **12 files, +1,401 / −26 lines**, with **no database schema change,
no migration, no infrastructure change, and no dependency added**. Equally important, the
sprint corrected **eight material inaccuracies in the Phase 0 assessment**, including two
cases where implementing the prescribed remediation as written would have produced a
control that appeared to work but did nothing, and one where it would have caused a
production outage. Three of the four findings are genuinely closed at the code level; **DP-02
and SEC-01 remain partially open by their nature** — their residual risk is contractual
(a HIPAA BAA with Anthropic) and operational (manual key rotation), not fixable in code, and
is documented rather than downgraded.

---

## 2. Findings remediated

| ID | Severity | Finding | Status | Commit |
|---|:--:|---|---|---|
| **AUTHZ-01** | **Critical** | Entire Case Management PHI router unauthenticated | **FIXED** | `4879e3e` + frontend `7c76208` |
| **DP-02** | **High** | PHI sent to Anthropic unmasked | **PARTIALLY FIXED** — direct identifiers stripped; clinical narrative requires a BAA | `da9ae7c` |
| **SEC-01** | **High** | Secrets management / live keys in working-tree `.env` | **CODE HARDENED + DOCUMENTED** — rotation still manual and open | `9e041df` |
| **AUDIT-MUT** | **Medium** | `audit_logs` mutable; no WORM/hash-chain | **APP LAYER FIXED** — hash-chain and WORM still open | `4893f1f` |

Two status corrections against the brief's table: **SEC-01 is not "Guarded + Documented"
complete** — no key was rotated, which is the finding's primary remediation. And
**AUDIT-MUT is not fully "Fixed"** — the application layer is, but tamper detection and
prevention (the AU-9 substance) remain absent.

Requested as "AU-01 (High)"; the register's actual row is **`AUDIT-MUT`, Medium**. `AU-9` is
the NIST control, the likely source of the confusion.

---

## 3. Assessment corrections

Every case where Phase 0 was found inaccurate during remediation. **Two of these would have
produced a placebo control; one would have caused an outage.**

| # | Original claim | Actual finding | Impact |
|--:|---|---|---|
| 1 | AUTHZ-01: "12 endpoints" | **22 endpoints** (`:132–:711`; the cited range omitted 4) | Larger exposure than reported |
| 2 | AUTHZ-01 implied **PHI at rest** | **No `cm_*` table deployed** — `models.py` never imported, no migration, live query returned none of 51 tables; all GETs return hardcoded stubs | **No stored data was at risk.** Real exposure was unauthenticated PHI *ingress* → Anthropic egress |
| 3 | AUTHZ-01: "no consumers" (implied) | **The frontend `/case-management` page was a consumer** and sent no `Authorization` header | Backend-only fix would have shipped a visible regression |
| 4 | DP-02 remediation: "run (expanded) `mask_pii` before egress" | **Provable no-op** — redacts **0 items** from the real prompt: no name pattern at all (Safe Harbor #1); DOB pattern needs a keyword prefix + `MM/DD/YYYY` so it misses bare ISO dates (#3) | **Would have produced a `pii_count` log line that reads like a working control while doing nothing.** Different fix required (exact-value replacement) |
| 5 | DP-02 cited `ai_engine.py:251` + `pii_masking.py` as locations | `ai_engine.py:251` **is** the only `mask_pii` call site in the application; the case-management engines **bypass it entirely** with their own `_call_claude` | Fix belonged in the bypassing engines, not in the masking module |
| 6 | SEC-01: "gitignored, not in history" | **Confirmed** — full `git rev-list --all --objects` scan finds only `.env.example` | **No history rewrite needed.** Rotation is hygiene, not breach response |
| 7 | SEC-01: "source from Key Vault" (implying code work) | **Key Vault already works** at the platform layer — App Service references + system-assigned MI, all 4 `Resolved`, vault public access disabled | **No SDK needed.** Adding one would duplicate a working mechanism and break local dev |
| 8 | SEC-01 scope: "Anthropic/OpenAI keys in `.env`" | **~18 secret-bearing variables, only 4 Key Vault backed.** `DATABASE_URL` plaintext in prod *and* dev; **dev entirely un-vaulted**; `OPENAI_API_KEY` only local | Materially larger scope |
| 9 | Requested inventory row `JWT_SECRET` | **Does not exist** — JWTs signed with `SECRET_KEY` | Row should be struck, not migrated |
| 10 | AUDIT-MUT: audit rows "deleted by compliance flow" | Code confirmed but **`compliance.py` is NOT MOUNTED** — `/api/user/hard-delete` returns 404 | Destructive path was **dead code**; live exposure was loss of *attribution* only |
| 11 | AUDIT-MUT: audit rows "updated by admin flow" (implied careless) | **Structurally required** — `audit_logs_user_id_fkey` is `NO ACTION`; proven by `ForeignKeyViolationError` | Not a defect to remove; a constraint to document |
| 12 | AUDIT-MUT remediation: "add append-only" (i.e. DB trigger) | **A trigger would have broken production** — the live admin delete path *must* `UPDATE audit_logs` for that FK | **App-layer fix first**, as prerequisite; trigger deferred with reasons |
| 13 | Finding ID "AU-01" | Correct ID is **`AUDIT-MUT`** (Medium, not High) | Tracking correction |

**Also discovered, not in Phase 0 at all:**

- **`app/api/security.py` publishes false compliance attestations** (`"pii_masking_active": True`,
  `"Zero retention"`, stale "Railway.app on GCP") — **not mounted**, so dead code, now guarded
  with a DO-NOT-MOUNT header.
- **Unresolved Key Vault reference accepted as `SECRET_KEY`** — App Service passes the literal
  `@Microsoft.KeyVault(...)` string through; at 71 chars it **passed** the 64-char entropy
  floor, so the app would boot signing every JWT with a publicly derivable constant.
- **`StateAuditLog` documented "immutable append-only"** with nothing enforcing it.
- **`security-platform/` is not a git repo** — it resolves to a stray zero-commit repo at
  `C:\.git` covering the whole drive. These documents are therefore untracked on disk by
  design.

---

## 4. Security improvements

| Area | Before Sprint 1 | After Sprint 1 |
|---|---|---|
| Unauthenticated PHI-handling endpoints | **22 live** | **0** |
| PHI masking before AI egress | **none** on case-management paths | Patient name, MRN, DOB, SSN, phone stripped at the egress chokepoint; verified on real outbound payloads |
| Unresolved Key Vault reference | **silently accepted** as the secret (passed the entropy floor) | **Hard startup failure** on required settings; ERROR log on optional |
| Audit records on GDPR erasure | **deleted** | **retained + pseudonymised** (identity, IP, personal keys erased) |
| Audit records on admin user delete | attribution nulled, undocumented | attribution nulled (FK-required), documented, and **`audit_rows_detached` recorded in the trail** |
| GDPR erasure disclosure | promised audit deletion that also happened to breach HIPAA retention | discloses retention + legal basis |
| False compliance attestations | live in an unmounted module, no warning | DO-NOT-MOUNT guard enumerating each unverified claim |
| Secrets inventory | none | ~18 variables inventoried with per-environment provenance |

**Not improved, and stated plainly:** clinical narrative still egresses to Anthropic; no
audit hash-chain; no server-side `allowed_modules` enforcement; no key rotated.

---

## 5. Files changed — complete inventory

**12 unique files · +1,401 / −26.** Of the additions, **875 lines are documentation** and
**41 a pure comment block**, leaving **~485 lines of functional code**.

| File | Commit | + | − | Purpose |
|---|---|--:|--:|---|
| `app/case_management/routes.py` | `4879e3e` | 15 | 2 | Router-level `Depends(get_current_user)` |
| `src/app/case-management/page.js` *(frontend)* | `7c76208` | 10 | 3 | `authHeaders()` at 3 call sites |
| `app/case_management/services/phi_deidentify.py` | `da9ae7c` | 188 | 0 | **NEW** — exact-value redact/restore |
| `app/case_management/services/discharge_engine.py` | `da9ae7c` | 54 | 6 | `phi_map` chokepoint + 4 call sites |
| `app/case_management/services/ccm_engine.py` | `da9ae7c` | 53 | 8 | `phi_map` chokepoint + 6 call sites |
| `app/api/security.py` | `da9ae7c` | 41 | 0 | **Comment only** — DO-NOT-MOUNT guard |
| `app/case_management/routes.py` | `da9ae7c` | 7 | 1 | `phi_map` for meeting minutes |
| `docs/compliance/AI_EGRESS_PHI.md` | `da9ae7c` | 281 | 0 | **NEW** — DP-02 control record |
| `app/core/config.py` | `9e041df` | 60 | 0 | Unresolved-KV-reference guard |
| `docs/compliance/SECRETS_MANAGEMENT.md` | `9e041df` | 310 | 0 | **NEW** — inventory + rotation checklist |
| `app/api/compliance.py` | `4893f1f` | 82 | 4 | Pseudonymise instead of delete; corrected contract |
| `app/api/admin_users.py` | `4893f1f` | 16 | 2 | Document detach; record `audit_rows_detached` |
| `docs/compliance/AUDIT_LOG_INTEGRITY.md` | `4893f1f` | 284 | 0 | **NEW** — AU-9 control record |

Per-commit: `4879e3e` 1 file +15/−2 · `da9ae7c` 6 files +624/−15 · `9e041df` 2 files +370/−0
· `4893f1f` 3 files +382/−6 · frontend `7c76208` 1 file +10/−3.

---

## 6. API impact

| Endpoint | Change | Breaking? |
|---|---|---|
| `/api/v1/case-management/*` (**22** endpoints) | Now require a bearer token: **403** without, **401** on malformed, **200** with | **No for backend consumers** (none exist) · **Yes for the frontend** — fixed in `7c76208`, which must ship with or before the backend |
| 9 of those that call an AI engine | Outbound Anthropic payloads no longer carry patient identifiers; responses unchanged | No — no contract change |
| `DELETE /api/user/hard-delete` | Audit rows pseudonymised not deleted; response gains `data_to_be_retained`, `retention_basis`, `audit_logs_pseudonymised`; `audit_logs_deleted` retained, now `0` | No — **route is unmounted (404)** |
| `DELETE /api/admin/users/{user_id}` | Unchanged behaviour; audit entry gains `audit_rows_detached` | No — additive |
| `/api/security/*` | Unchanged (still unmounted, 404) | No |
| **All other endpoints** | **No change** | N/A |

Response-code note: unauthenticated case-management requests return **403**, not 401,
because `HTTPBearer(auto_error=True)` rejects a missing credential before any handler runs.

---

## 7. Database impact

| | |
|---|---|
| Schema changes | **NONE** — no table, column, index, trigger, constraint, or RLS change |
| Migrations required | **NONE** — no Alembic revision added |
| Data changes on deploy | **NONE** |
| Data changes at runtime | Only via the **unmounted** GDPR route (pseudonymisation), and the pre-existing admin detach — both were already possible |
| Verified | `audit_logs` count unchanged (30 → 30) across a user deletion in integration test; no `cm_*` table exists to affect |

---

## 8. Infrastructure impact

| | |
|---|---|
| Azure resource changes required | **NONE** |
| Key Vault changes required | **NONE** to deploy. All 4 references verified `Resolved` 2026-07-26 |
| Environment variable changes required | **NONE** |
| Dependency changes | **NONE** — no package added or upgraded |
| Managed identity / RBAC changes | **NONE** |
| **New operational requirement** | **Yes** — verify Key Vault reference resolution **before** restarting the site. SEC-01 now fails startup on an unresolved reference (by design). |

Nothing in Azure was created, modified, or rotated during Sprint 1; all Azure interaction
was read-only verification.

---

## 9. Deployment steps

Full detail in `sprint1_review/merge_plan.md`. Two corrections to the brief:

**(a) The four branches are ONE LINEAR STACK, not independent.** Merging
`sprint1/audit-mut-log-integrity` merges all four findings. There is no way to merge SEC-01
without AUTHZ-01 and DP-02 short of cherry-picking. Recommended: one `--no-ff` merge of the
stack tip; per-commit `git revert` preserves granular rollback.

**(b) Kudu VFS is not the documented deploy path.** `docs/deployment/azure-deployment-guide.md`
§7 uses `az webapp deploy --type zip` with dependencies vendored into `pydeps/` and exposed
via `PYTHONPATH` (and an explicit warning not to name it `antenv`). Prod app settings
(`SCM_DO_BUILD_DURING_DEPLOYMENT=false`, `ENABLE_ORYX_BUILD=false`) are consistent with the
zip recipe. Reconcile before deploying, not during.

**Sequence:** frontend merge + deploy first (or simultaneously) → pre-deploy Key Vault
resolution gate → backend zip deploy → restart → validation checklist.

**Post-deploy verification:** `/health` 200 · 22/22 modules, zero `Skipped` · case-management
403 without auth / 200 with · TEFCA Registry pages load · login works · dashboard loads ·
bulletin scheduler running. Full list in `validation_checklist.md`.

**Estimated total: ~60–80 minutes** including validation, excluding rollback.

---

## 10. Rollback steps

Revert **in reverse stack order** — reverting a lower commit while higher ones are present
risks conflicts in `routes.py` (touched by both AUTHZ-01 and DP-02).

| Priority | Symptom | Action |
|--:|---|---|
| **1** | Site won't start, `UNRESOLVED Azure Key Vault reference` | **Do NOT revert `9e041df`.** Fix the reference or set the app setting to a real value. Reverting reopens the exact defect the guard prevents. |
| **2** | `/case-management` 403s for logged-in users | Deploy frontend `7c76208`. Only if impossible: `git revert 4879e3e`. |
| **3** | Notes contain `[PATIENT_LAST]` or read incorrectly | `git revert da9ae7c`, or one-line disable: `build_phi_map()` → `{}`. |
| **4** | Admin user deletion fails | `git revert 4893f1f` (cleanest — stack tip). |
| **5** | Anything else | `git revert -m 1 <merge-commit>`. |

No schema, migration, trigger, data, config, infrastructure, or dependency change exists in
any commit — every rollback is code-only with no state to unwind.

---

## 11. Post-deployment validation checklist

See `sprint1_review/validation_checklist.md` for the executable version with **[GATE]** /
**[CHECK]** severity, exact commands, and a sign-off block. Summary:

- [ ] `/health` → 200
- [ ] 22/22 modules loaded, **zero `Skipped`** in logs
- [ ] No startup exception (esp. no `UNRESOLVED ... Key Vault reference`)
- [ ] Login valid → success · invalid → 401 · Entra SSO works
- [ ] Case management without auth → 403 · with auth → 200 · malformed token → 401
- [ ] `/case-management` page loads in browser while logged in *(frontend co-dependency)*
- [ ] Generated note shows the **real** patient name, **no** `[PATIENT*]` tokens
- [ ] Over-redaction spot-check if a test surname collides with clinical vocabulary
- [ ] TEFCA Registry: Overview, search, verification all work; **QHIN count matches the
      pre-deploy baseline** — the brief's "11 QHINs" could **not** be verified (no
      `tefca_reg_qhins` table locally; `tefca_reg_entities` has no `qhin` type), so record
      the baseline first rather than asserting 11
- [ ] Dashboard loads · Admin user list loads · Bulletin scheduler running
- [ ] `audit_logs` row count **unchanged** by a test user deletion
- [ ] `/api/user/hard-delete` → 404 · `/api/security/status` → 404
- [ ] No new exception types in App Insights (a **rise in 403s on case-management is
      expected and correct**)

---

## 12. Remaining open findings

### Carried over from Sprint 1 work — contractual / organisational

1. **BAA with Anthropic — not executed.** The controlling safeguard for clinical-narrative
   egress. **DP-02 cannot close without it.**
2. **Zero-retention addendum — not obtained.** There is **no per-request zero-retention
   header**; it is an organisation-level account setting. Contractual only.
3. **Clinical narrative still sent to Anthropic** — symptoms, diagnoses, medications, raw
   transcripts. PHI under HIPAA even with the name removed. Unmaskable without destroying
   the product.
4. **Third-party names still egress** — relatives, providers, facilities in transcripts.
   Not in `patient_context`, so exact-value replacement cannot see them.
5. **Key rotation not performed** — `ANTHROPIC_API_KEY` (up to **3 distinct values** in
   circulation), `OPENAI_API_KEY` (local only; decide if Whisper is used at all).
   Checklist: `SECRETS_MANAGEMENT.md` §4.
6. **`DATABASE_URL` plaintext** app setting with the DB password, **prod and dev**.
7. **Dev environment entirely un-vaulted** — 6 plaintext secrets despite `docuaction-kv-dev`.
8. **`AZURE_AD_CLIENT_SECRET` expiry** — an expired secret still *resolves*, so the SEC-01
   guard cannot detect it. SSO outage risk; check expiry and calendar renewal.
9. **No audit hash-chain** — a row can still be edited or removed with **no trace**. The
   main remaining AU-9 gap.
10. **No audit tamper prevention** — no trigger, RLS, or privilege separation.
11. **Attribution still lost on user hard-delete** while the FK is `NO ACTION`.
12. **`users.allowed_modules` never enforced server-side** — anywhere. Any authenticated
    user reaches any module regardless of granted areas. *Deferred by you.*
13. **No case-management role tiering** — a `viewer` can generate and sign clinical notes.
    *Deferred by you.*
14. **No audit logging of PHI egress** — nothing records that PHI went to a third party.
15. **`generate_government_case_document` uncovered** by de-identification (takes
    `case_facts`, no known values).
16. **`api/meeting_routes.py:145`** sends raw transcripts to Anthropic unmasked — same
    class as DP-02, outside its scope.
17. **Stray `C:\.git`** repo covering the whole drive. *Deferred by you.*

### Phase 0 findings not addressed in Sprint 1

| ID | Sev | Finding |
|---|:--:|---|
| AUTH-03 | Medium | Entra `id_token` signature not verified (no JWKS, no nonce) |
| AUTHZ-02 | Medium | IDOR — no ownership check on healthcare-claims handlers |
| DP-03 | Medium | PHI in URL query strings |
| CRYPTO-DBTLS | Medium | DB TLS not pinned in code |
| AUDIT-READ | Medium | PHI **read** access not logged |
| SH-03 | Medium | No CSP on the SWA frontend |
| AUTH-01 | Low | Admin token TTL 24h |
| AUTH-02 | Low | Account lockout in-memory / per-process |
| SH-01 | Low | Rate limiting in-memory / per-process |
| AUTHZ-03 | Low | Untyped-dict admin user update |
| DP-01 | Low | Email/NPI in some info logs |
| SEC-03 | Low | `DATABASE_URL` not vaulted *(overlaps item 6)* |
| DP-05 | Low | No role-based PHI masking on read responses |
| SH-04 | Low | No HSTS on SWA frontend |
| FU-02 | Low | No true anti-malware (heuristic scanner only) |

**Also unaddressed (non-security, from the wider assessment):** Test Coverage **1.4/10**
(one test file) — the single biggest lever on the overall grade; Scalability 4.5;
DevOps/Operations 5.0 (no CD, no HA/DR).

---

## 13. Sprint 2 recommendations

Ordered by risk-reduction per unit of effort, informed by what Sprint 1 actually cost.

**Tier 1 — do first (highest value, and two are unblocking)**

1. **Execute the Anthropic BAA + zero-retention addendum.** Not code, but it is the only
   thing that closes DP-02 and moves Healthcare Compliance meaningfully. Start it now
   because it has external lead time. *(Owner: Imran / legal)*
2. **Rotate the keys** per `SECRETS_MANAGEMENT.md` §4, and vault `DATABASE_URL` + the dev
   environment. Closes the rest of SEC-01. *(~1 day, mostly ops)*
3. **Add a minimal test suite around Sprint 1's changes.** Coverage is 1.4/10 and every
   validation this sprint was manual. Even 15–20 tests over the auth gate, the
   de-identification round-trip, and the config guard would convert the checklist into
   something repeatable. **Highest long-term leverage on the overall grade.** *(~2 days)*
4. **`require_module()` dependency enforcing `users.allowed_modules` server-side.** One
   reusable dependency closes a platform-wide authorization gap on *every* module router,
   not just case management. *(~1 day)*

**Tier 2 — the audit and authorization substance**

5. **Audit hash-chain** — nullable `prev_hash`/`row_hash` via Alembic (no backfill), computed
   in the single write helper, plus an admin verify endpoint. Makes tampering *detectable*.
   *(2–3 days)*
6. **Case-management role tiering** — `require_role("contributor")` on write/sign paths once
   role assignments are audited. *(0.5 day)*
7. **AUTH-03** — verify the Entra `id_token` against Microsoft JWKS with nonce/iss/exp.
   A genuine auth bypass class. *(1–2 days)*
8. **AUTHZ-02** — ownership checks on healthcare-claims handlers (IDOR). *(0.5 day)*

**Tier 3 — hardening sweep**

9. **AUDIT-READ** + PHI-egress audit logging (items 9, 14) — one coherent piece of work.
10. **DP-03** (PHI out of query strings), **CRYPTO-DBTLS** (pin DB TLS), **SH-03/SH-04**
    (CSP + HSTS on the SWA) — all small and independent.
11. **Redis-backing** for rate limiting and account lockout (AUTH-02, SH-01) — one change
    covering both.
12. Decide the fate of `app/api/security.py` (correct its claims and mount with auth, or
    delete) and `app/api/compliance.py` (mount the GDPR flow, or delete).

**Process lessons to carry forward**

- **The register was inaccurate on every row verified — 13 corrections across 4 findings.**
  Keep verifying before implementing; two prescribed fixes were placebos and one would have
  caused an outage.
- **Check for frontend consumers before adding auth.** AUTHZ-01 nearly shipped a regression.
- **Do not stack branches** unless intended. Sprint 1's four branches accidentally formed a
  linear chain, removing the option of independent merges.

---

## 14. Score projections (post-merge)

**These are my estimates, not a re-run of the Phase 0 methodology.** Baselines from
`EXECUTIVE_REPORT.md` §2.

| Category | Before | After (est.) | Reasoning |
|---|:--:|:--:|---|
| **Security** | 6.0 | **7.0** | The assessment itself projected "~7.5–8" for AUTHZ-01 + DP-02 + SEC-01. AUTHZ-01 is fully closed; DP-02 and SEC-01 are **partial** (BAA and rotation outstanding), so the upper half of that range is not earned. The audit fix and the newly found Key Vault defect add a little. **Cannot exceed ~7.5 until the BAA and rotation land.** |
| **Healthcare Compliance** | 6.0 | **6.5** | Real gains: no unauthenticated PHI surface, identifiers minimised before egress, audit retention now HIPAA-aligned with honest disclosure. But the Phase 0 verdict was "**not PHI-ready**", and **without a BAA it still is not** — clinical PHI goes to a third party under no agreement. That caps this score. |
| **Overall product** | 5.8 | **5.9** | Deliberately modest. Security is 1 of 17 categories; +1.0 in one and +0.5 in another moves a 17-category mean by roughly +0.09. **Sprint 1 barely moves the overall grade, and that is the honest answer** — the biggest levers are untouched: Test Coverage **1.4**, Scalability 4.5, DevOps 5.0. Anyone expecting a large overall jump from a security sprint should recalibrate. |

**What would actually move the overall grade:** the test suite (1.4 → 5.0 alone is worth
about +0.2 overall, more than all of Sprint 1), then DevOps/CD and scalability. Sprint 1 was
the right *risk* priority; it is not the right *grade* priority, and those are different
questions.
