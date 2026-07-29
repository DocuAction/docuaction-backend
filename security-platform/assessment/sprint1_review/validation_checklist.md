# Sprint 1 — Post-Deployment Validation Checklist

**Run every item. No automated test suite covers this work** (Phase 0 scored test
coverage 1.4/10 — one test file), so this checklist is the only safety net.

Legend: **[GATE]** = stop and roll back if it fails · **[CHECK]** = investigate, not
necessarily a rollback.

---

## 0. Pre-deploy gate — BEFORE restarting the App Service

- [ ] **[GATE]** All four Key Vault references resolve. SEC-01 now fails startup if any
      does not, so a broken reference means the site will not come back up.
      ```bash
      SUB=$(az account show --query id -o tsv)
      az rest --method get --uri \
       "/subscriptions/$SUB/resourceGroups/rg-docuaction-prod/providers/Microsoft.Web/sites/Docuaction/config/configreferences/appsettings?api-version=2022-03-01" \
       --query "value[].{name:name,status:properties.status}" -o table
      ```
      Expect 4 rows, all `Resolved` (`SECRET_KEY`, `ANTHROPIC_API_KEY`,
      `AZURE_AD_CLIENT_SECRET`, `SENDGRID_API_KEY`). Baseline verified 2026-07-26.
- [ ] **[GATE]** Frontend `7c76208` is deployed, or is deploying simultaneously. Backend
      AUTHZ-01 without it = `/case-management` 403s for every user.
- [ ] **[CHECK]** Record the pre-deploy `audit_logs` row count, so §7 can compare.

---

## 1. Startup and health

- [ ] **[GATE]** `curl -s -o /dev/null -w '%{http_code}' https://api.docuaction.io/health` → **200**
- [ ] **[GATE]** No startup exception in logs — specifically **no** `FATAL: SECRET_KEY is an
      UNRESOLVED Azure Key Vault reference`. If present, do **not** revert SEC-01; fix the
      reference (see `merge_plan.md` §5 priority 1).
- [ ] **[GATE]** **22/22 modules loaded.** `az webapp log tail -g rg-docuaction-prod -n Docuaction`
      → count `Loaded:` lines; **zero** `Skipped` lines. A `Skipped` line means a router
      silently became 404s.
- [ ] **[CHECK]** `/health` payload still reports `"case_management": "active"`.
- [ ] **[CHECK]** No `phi_identifiers_masked` line with an implausible count, and **no PHI
      values** anywhere in logs (the control logs counts only — a value in a log is a defect).

## 2. Authentication (unchanged by Sprint 1 — regression check)

- [ ] **[GATE]** Login with valid credentials → success, token issued
- [ ] **[GATE]** Login with invalid credentials → **401**
- [ ] **[CHECK]** Entra ID SSO login still works (`AZURE_AD_CLIENT_SECRET` resolves — note
      the guard cannot detect an *expired* secret, only an unresolved reference)
- [ ] **[CHECK]** A disabled/pending-approval account is still rejected

## 3. AUTHZ-01 — Case Management gate

- [ ] **[GATE]** `GET /api/v1/case-management/info` **without** a token → **403**
- [ ] **[GATE]** `GET /api/v1/case-management/info` **with** a valid token → **200**
- [ ] **[GATE]** `GET /api/v1/case-management/info` with a **malformed** token → **401**
- [ ] **[GATE]** Spot-check 3 more unauthenticated → all **403**:
      `/dashboard/stats`, `/patients`, `/billing/cpt-reference`
- [ ] **[GATE]** `/case-management` **page in the browser, logged in** → loads, module info
      panel populates, no 403 in the network tab. *This is the frontend co-dependency check.*
- [ ] **[CHECK]** Billing calculator on that page returns a CPT code (expect `99490` /
      `$66.13` for 25 min clinical staff)

## 4. DP-02 — PHI egress

- [ ] **[GATE]** Generate one CCM note via `/case-management` (voice → note) → returns a note
- [ ] **[GATE]** **The returned note shows the real patient name**, not `[PATIENT]`. A visible
      token means restoration failed.
- [ ] **[GATE]** **No `[PATIENT_LAST]` / `[MRN]` / `[DOB]` token anywhere in the note body**
- [ ] **[CHECK]** Log shows `phi_identifiers_masked: N` with N > 0 for that request
- [ ] **[CHECK]** **Over-redaction spot-check** — if any test patient's surname collides with
      clinical vocabulary (Stone, Rash, Long, Short, Gray, Bell, Cross, Marsh, Back, Head),
      generate a note for them and confirm the clinical narrative reads correctly. This is the
      known accepted trade-off; confirm its impact is tolerable in practice.
- [ ] **[CHECK]** `GET /api/security/status` → **404** (module must remain unmounted; its
      attestations are false)

## 5. TEFCA — must be untouched

- [ ] **[GATE]** TEFCA Registry pages all load (Overview, Entities, Issues, Verification)
- [ ] **[GATE]** `GET /api/tefca/registry/entities` → 200 with data
- [ ] **[GATE]** `GET /api/tefca/dashboard/summary` → 200
- [ ] **[CHECK]** `GET /api/v1/tefca/connectors/status` → 200
- [ ] **[CHECK]** Registry search returns results
- [ ] **[CHECK]** Registry verification run completes
- [ ] **[CHECK]** **QHIN overview count matches the pre-deploy baseline.** The brief cites
      "11 QHINs"; I could **not** verify that number — the local database has no
      `tefca_reg_qhins` table and `tefca_reg_entities` (183 rows locally) has no `qhin`
      entity type. **Record the count before deploying and compare after**, rather than
      asserting 11.
- [ ] **[CHECK]** Route count sanity: 92 TEFCA routes were mounted pre-change

## 6. Other modules — regression sweep

- [ ] **[GATE]** Dashboard loads
- [ ] **[GATE]** Admin → user list loads (`GET /api/admin/users` → 200)
- [ ] **[CHECK]** Bulletin Intelligence: scheduler running (`ENABLE_SCHEDULER=true` in prod);
      `/api/v1/bulletin/latest/{agency}` responds. *Note: the `today` endpoint is known-slow
      (>2 min) — not a Sprint 1 regression.*
- [ ] **[CHECK]** Document upload still works (file scanner path untouched)
- [ ] **[CHECK]** `GET /api/documents` — **known pre-existing 500** locally
      (`column documents.tenant_id does not exist`). Confirm whether prod shares this;
      **not** caused by Sprint 1 either way.

## 7. AUDIT-MUT — audit integrity

- [ ] **[GATE]** Audit Trail view loads with entries present
- [ ] **[GATE]** `DELETE /api/user/hard-delete` → **404** (must stay unmounted)
- [ ] **[CHECK]** Delete a disposable test user via Admin → succeeds
- [ ] **[GATE]** **`audit_logs` row count is UNCHANGED by that deletion.** Compare with the
      §0 baseline. Any decrease means audit records were destroyed — roll back immediately.
- [ ] **[CHECK]** The resulting `user_deleted` audit entry contains `audit_rows_detached: N`
- [ ] **[CHECK]** The deleted user's prior audit rows still exist with `user_id = NULL`

## 8. Observability

- [ ] **[GATE]** No new exception types in Application Insights vs the pre-deploy baseline
- [ ] **[CHECK]** No spike in 4xx (a rise in 403 on `/api/v1/case-management/*` is
      **expected and correct** — that is AUTHZ-01 working)
- [ ] **[CHECK]** No spike in 5xx
- [ ] **[CHECK]** Response-time profile unchanged on AI endpoints (redaction is a few regex
      substitutions; a material change is a red flag)

---

## Sign-off

| | |
|---|---|
| Deployed by | |
| Date / time (UTC) | |
| Backend merge commit | |
| Frontend merge commit | |
| All **[GATE]** items passed | ☐ Yes ☐ No |
| **[CHECK]** deviations recorded below | |
| Rollback performed? | ☐ No ☐ Yes → which commit(s): |

**Deviations / notes:**
