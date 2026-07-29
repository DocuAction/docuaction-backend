# Sprint 1 — Merge & Deployment Plan

**Status: NOT MERGED, NOT PUSHED, NOT DEPLOYED.** Awaiting approval.
**Date:** 2026-07-26

---

## 1. ⚠️ Actual branch topology — correction to a previous statement

I earlier told you that `sec-01` and `audit-mut` "branch independently off
`security/pre-azure-hardening`". **That was wrong.** Verified:

```
* 4893f1f  (sprint1/audit-mut-log-integrity)        AUDIT-MUT
* 9e041df  (sprint1/sec-01-secrets-management)      SEC-01
* da9ae7c  (sprint1/dp-02-phi-egress-minimization)  DP-02
* 4879e3e  (sprint1/authz-01-case-management-auth)  AUTHZ-01
* e3f9e5b  (security/pre-azure-hardening)           ← base
```

| Branch | commits ahead of base |
|---|--:|
| `sprint1/authz-01-case-management-auth` | 1 |
| `sprint1/dp-02-phi-egress-minimization` | 2 |
| `sprint1/sec-01-secrets-management` | 3 |
| `sprint1/audit-mut-log-integrity` | **4** |

**All four are one linear stack.** Each branch contains every commit below it. This
changes the merge plan materially:

- **Merging `sprint1/audit-mut-log-integrity` merges all four findings at once.**
- There is **no way to merge SEC-01 without also merging AUTHZ-01 and DP-02**, short of
  cherry-picking.
- The four-step "merge order" in the brief describes four independent merges. That is not
  the shape of what exists.

### Consequence — pick one of two strategies

| | **Strategy A — single merge (RECOMMENDED)** | **Strategy B — staged via cherry-pick** |
|---|---|---|
| How | Merge `sprint1/audit-mut-log-integrity` once; it carries all four commits | Cherry-pick each commit onto its own branch off base, merge one at a time |
| Pros | Matches reality; zero conflict risk; already validated as a whole (every test run this sprint executed against the full stack) | Independent rollback per finding; smaller deploys |
| Cons | All-or-nothing rollback granularity at merge level (per-commit `git revert` still works afterwards) | Re-validation needed per branch; `routes.py` conflicts between AUTHZ-01 and DP-02; ~2 extra hours |
| **Recommendation** | **Use this.** Per-commit `git revert` preserves granular rollback without the cherry-pick cost. | Only if a stakeholder requires literally separate merge commits per finding. |

The rest of this plan assumes **Strategy A**.

---

## 2. Merge sequence

### Backend — one merge

```bash
cd "C:/Imran_Coding projects/DocuAction/backend"
git status --porcelain            # MUST be clean of the 4 sprint files; other WIP is fine
git checkout security/pre-azure-hardening
git merge --no-ff sprint1/audit-mut-log-integrity
```

`--no-ff` keeps a merge commit so the whole sprint can be reverted as a unit if needed,
while individual findings remain revertable by commit SHA.

Resulting commits, in order: `4879e3e` → `da9ae7c` → `9e041df` → `4893f1f`.

> **Target-branch note.** The stack is based on `security/pre-azure-hardening`, **not
> `main`**. That branch itself is unmerged and carries substantial uncommitted work in the
> tree. Merging Sprint 1 into `main` directly is **not** a fast-forward and is out of
> scope here — decide separately whether `security/pre-azure-hardening` goes to `main`
> first.

### Frontend — one merge, and it is NOT optional

```bash
cd "C:/Imran_Coding projects/DocuAction/frontend"
git checkout feature/tefca-registry-ui
git merge --no-ff sprint1/authz-01-case-management-auth
```

**The frontend commit must ship with or before the backend goes live.** Backend AUTHZ-01
without frontend `7c76208` = the `/case-management` page returns 403 on all three of its
calls. This is the single highest-consequence sequencing constraint in Sprint 1.

---

## 3. Deployment

### ⚠️ Correction: Kudu VFS is not the documented deploy path

The brief specifies *"Backend via Kudu VFS (changed files only)."* The project's own guide
(`docs/deployment/azure-deployment-guide.md` §7) uses the **one-deploy zip endpoint**:

```bash
az webapp deploy --resource-group rg-docuaction-prod \
                 --name Docuaction --src-path deploy.zip --type zip
```

with dependencies vendored into `pydeps/` and exposed via `PYTHONPATH`, and an explicit
warning **not to name that directory `antenv`** (it collides with the App Service startup
optimiser). `SCM_DO_BUILD_DURING_DEPLOYMENT=false` and `ENABLE_ORYX_BUILD=false` are set
in prod app settings, consistent with the zip recipe.

**Use the documented zip recipe.** A VFS file-push may appear to work but bypasses the
vendored-dependency layout the app relies on. If you have been deploying via VFS
successfully in practice, reconcile the guide before this deploy rather than during it.

### Order

1. **Frontend first, or simultaneously.** Sending an unused `Authorization` header to a
   backend that does not yet require it is harmless; the reverse is an outage. Deploying
   frontend first makes the window safe in both directions.
2. **Backend** via the zip recipe.
3. Frontend targets: **both** Static Web Apps if both serve users (`docuaction-frontend`;
   confirm whether the Vercel host is still live — the `app.docuaction.io` CNAME cutover
   was pending as of the last environment note).

### Pre-deploy gate — run this BEFORE restarting the site

SEC-01 introduces a fail-fast on unresolved Key Vault references. Confirm all four still
resolve, or the site will not start:

```bash
SUB=$(az account show --query id -o tsv)
az rest --method get --uri \
 "/subscriptions/$SUB/resourceGroups/rg-docuaction-prod/providers/Microsoft.Web/sites/Docuaction/config/configreferences/appsettings?api-version=2022-03-01" \
 --query "value[].{name:name,status:properties.status}" -o table
```

All four rows must read `Resolved`. Verified `Resolved` on 2026-07-26.

---

## 4. Estimated deployment time

| Phase | Estimate |
|---|--:|
| Backend merge + push | 5 min |
| Frontend merge + push | 5 min |
| Pre-deploy Key Vault resolution check | 2 min |
| Frontend build + SWA deploy | 10–15 min |
| Backend zip build (`pydeps/`, cp312 wheels) + `az webapp deploy` | 15–20 min |
| App Service restart + warm-up (`WEBSITES_CONTAINER_START_TIME_LIMIT=300`) | 5 min |
| Post-deploy validation (`validation_checklist.md`) | 20–30 min |
| **Total** | **~60–80 min**, plus contingency |

Assumes no rollback. Add ~20 min if the backend zip must be rebuilt.

---

## 5. Rollback — priority order if problems appear

Rollback **in reverse stack order**. Reverting a lower commit while higher ones are present
risks conflicts (notably `routes.py`, touched by both AUTHZ-01 and DP-02).

| Priority | Symptom | Revert | Notes |
|--:|---|---|---|
| **1** | **Site will not start; logs show `UNRESOLVED Azure Key Vault reference`** | *Do not revert.* Fix the reference, or set the app setting to a real value temporarily. | Reverting `9e041df` would let the app boot on the literal reference string — the exact defect the guard exists to prevent. |
| **2** | `/case-management` page 403s for logged-in users | Confirm frontend `7c76208` deployed. Only if that cannot be fixed quickly: `git revert 4879e3e` | Frontend fix is preferable to reopening the Critical. |
| **3** | Generated clinical notes contain `[PATIENT_LAST]` or read incorrectly | `git revert da9ae7c`, or set `build_phi_map()` to return `{}` | The over-redaction trade-off (surname colliding with clinical vocabulary). The one-line disable keeps the code. |
| **4** | Admin user deletion fails | `git revert 4893f1f` | Cleanest revert — tip of stack, files untouched by others. |
| **5** | Anything else | `git revert -m 1 <merge-commit>` | Reverts the whole sprint. |

Full whole-sprint reversal:

```bash
git revert 4893f1f && git revert 9e041df && git revert da9ae7c && git revert 4879e3e
# frontend, only if backend AUTHZ-01 was reverted:
cd ../frontend && git revert 7c76208
```

No schema, migration, trigger, data, config, infrastructure, or dependency change exists
in any of the four commits — every rollback is code-only with no state to unwind.

---

## 6. Concerns to weigh before approving

1. **The stack is not four independent branches** (§1). If your process requires per-finding
   merges, that is Strategy B and costs ~2 extra hours.
2. **The frontend is a hard co-dependency** of backend AUTHZ-01. Highest-consequence
   sequencing item.
3. **Kudu VFS vs the documented zip recipe** (§3) — reconcile before deploying.
4. **SEC-01 converts a silent security failure into a loud availability failure.** Correct,
   but the pre-deploy gate is now mandatory, and any future Key Vault work can take the
   site down.
5. **DP-02 can alter generated clinical content.** If the patient population plausibly
   includes surnames like Stone, Rash, Long, Gray, spot-check one generated note first.
6. **Target branch is `security/pre-azure-hardening`, not `main`** — that branch is itself
   unmerged with heavy uncommitted work in the tree.
7. **Neither `security-platform/` doc set nor the assessment updates are committable** —
   that directory resolves to a stray zero-commit repo at `C:\.git` covering the whole
   drive. Docs live on disk, untracked, deliberately. The three
   `backend/docs/compliance/*.md` records **are** committed inside the stack.
8. **No automated test suite exercises any of this.** Every validation this sprint was
   manual (test coverage is 1.4/10 per Phase 0). The checklist in
   `validation_checklist.md` is the only safety net — treat it as mandatory, not optional.
