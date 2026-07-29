# AUTHZ-01 — Remediation Record

**Finding:** Entire Case Management PHI router unauthenticated
**Severity:** Critical · **OWASP** A01 · **CWE** 306 · **NIST** AC-3
**Status:** REMEDIATED (pending approval to merge)
**Date:** 2026-07-26
**Sprint:** Sprint 1 — Critical & High Security Remediation
**Branches:** `sprint1/authz-01-case-management-auth` (backend **and** frontend)

---

## 1. Verification of the original finding

The finding was confirmed as a real Critical exposure, with two corrections to its
description.

### Confirmed

| Claim | Evidence |
|---|---|
| Router is live in the running app | `app/main.py:321` → `safe_load("app.case_management", "case-management")`, which calls `app.include_router(mod.router)` (main.py:255-263). Module imports cleanly — verified at runtime: `Loaded: case-management`, 22 routes mounted on the app. |
| No authentication anywhere in the module | `APIRouter(...)` declared with no `dependencies=` (routes.py:34-37 pre-fix). `grep -rn "Depends\|current_user\|require_\|get_current" app/case_management/` → **no matches**. No import from `app.core.security`. |
| PHI accepted anonymously | Live server, no token: `POST /api/v1/case-management/notes/voice-to-note` with a patient name + MRN + clinical transcript was accepted and processed. |

### Correction 1 — endpoint count is 22, not 12

The finding cites "12 endpoints" over the line range `:189–:652`. The router
actually exposes **22** endpoints (`:132–:711`); the cited range omits
`/dashboard/stats` (:132), `/patients` GET (:167), `/patients/{id}` (:208) and
`/info` (:711).

### Correction 2 — no PHI at rest; the exposure is PHI *egress*

The finding implies stored PHI is exposed. It is not:

- `app/case_management/models.py` defines 6 tables (`cm_patients`, `cm_notes`,
  `cm_care_plans`, `cm_discharge_records`, `cm_government_cases`,
  `cm_billing_summaries`) but **`models.py` is never imported** — `__init__.py`
  imports only `.routes`, so the metadata is never registered.
- No Alembic migration references any `cm_*` table; no DDL in `main.py`.
- Live DB query: `select table_name from information_schema.tables where
  table_schema='public' and table_name like 'cm%'` → **NONE** (51 public tables).
- All 9 GET handlers return hardcoded literals with
  `"note": "Wire to database for production use."`

**The actual Critical exposure is unauthenticated PHI ingress → third-party
egress.** 8 endpoints accept a PHI request body and forward it to the Anthropic
API (overlaps finding DP-02); `POST /patients` additionally accepts name / MRN /
DOB. This is unauthenticated PHI disclosure to a third party under HIPAA
§164.502, plus an unmetered billable-LLM abuse vector on a public endpoint.
Severity remains **Critical**.

### Blast radius

No module imports `case_management`. The only external references are the string
module id used for area-access UI: `app/api/admin_users.py:40` (module catalog)
and `:51` (legacy alias `"casemanagement" → "case_management"`).

---

## 2. Root cause

`routes.py` was authored as a standalone drop-in module — its own docstring reads
*"Add to main.py: `safe_load(...)`"* — and was mounted without being retrofitted
with the platform's auth convention. Every other router imports
`get_current_user` / `require_role` from `app.core.security`; this one has no
import from that module at all.

The `users.allowed_modules` area-access mechanism that should have been the
second line of defence is **stored in the DB and rendered by the frontend nav,
but never enforced server-side** (`grep -rn allowed_modules` shows reads/writes
in `admin_users.py` and the column in `models/database.py` — no authorization
check anywhere). So there was no backstop to catch the omission.

---

## 3. Fix

Router-level authentication dependency — one edit covering all 22 endpoints, and
any endpoint added later.

**Backend** — `app/case_management/routes.py` (+13 / −2, comment included):

```python
from fastapi import APIRouter, ..., Depends
from app.core.security import get_current_user

cm_router = APIRouter(
    prefix="/api/v1/case-management",
    tags=["Case Management"],
    dependencies=[Depends(get_current_user)],
)
```

**Frontend** — `src/app/case-management/page.js` (+7 / −3). The page sent **no**
`Authorization` header on any of its 3 live calls, so backend-only auth would
have produced a 403 regression. Added an `authHeaders()` helper (token key
`'token'`, the dominant convention — 16 uses vs 3 for legacy `govcon_token`) and
applied it to `notes/voice-to-note` (:82), `billing/determine-code` (:347) and
`info` (:443).

### Decisions and rejected alternatives

| Option | Decision |
|---|---|
| Router-level `dependencies=[Depends(get_current_user)]` | **Chosen.** Cannot be forgotten on a future route. `get_current_user` also enforces account-disabled / pending-approval / session-revocation state on every request. |
| Per-endpoint decorators on all 22 routes | Rejected — 22 edits, and the next added route silently ships unauthenticated. |
| `require_role("contributor")` or higher | Rejected for this fix — risks locking out existing viewer-role accounts. Any authenticated user closes the Critical; role tiering is a separate authorization decision. |
| New `allowed_modules` enforcement dependency | Rejected as out of scope — net-new infrastructure. **Logged as follow-up (see §6).** |

### Import-risk note

`safe_load` swallows `ImportError` and would silently turn the module into 404s.
The fix adds `app.core.security` → `app.core.config` + `app.core.database` to
this module's import chain. Both are already imported by `app/main.py:11` at
startup, so a config failure aborts the whole app before `safe_load` runs — no
new silent-404 failure mode. Verified: `Loaded: case-management`, 22 routes.

---

## 4. Validation evidence

Local uvicorn on `127.0.0.1:8899`, `ALLOWED_HOSTS=localhost`, local Postgres.

### Auth boundary — negative

| Request | Result |
|---|:--:|
| `GET /info` (no token) | **403** |
| `GET /dashboard/stats` (no token) | **403** |
| `GET /patients` (no token) | **403** |
| `POST /patients` (no token, name+MRN+DOB body) | **403** |
| `POST /notes/voice-to-note` (no token, PHI transcript) | **403** |
| `POST /discharge/generate` (no token) | **403** |
| `POST /sdoh/assess` (no token) | **403** |
| `POST /billing/determine-code` (no token) | **403** |
| `PATCH /notes/{id}/approve` (no token) | **403** |
| `GET /billing/cpt-reference` (no token) | **403** |
| `GET /info` with `Bearer garbage.token.here` | **401** |

Static check over the mounted router — router-level + per-route dependencies
resolved for all 22 routes: **`UNPROTECTED: NONE`**.

### Auth boundary — positive (no regression for authenticated users)

Token minted for a real active local user (`role=contributor`):

| Request | Result |
|---|:--:|
| `GET /info` | 200 |
| `GET /dashboard/stats` | 200 |
| `GET /billing/cpt-reference` | 200 |
| `GET /patients` | 200 |
| `GET /notes` | 200 |
| `GET /care-plans` | 200 |
| `GET /education/topics` | 200 |
| `GET /government/cases` | 200 |
| `GET /billing/monthly-summary?month=2026-07` | 200 |
| `POST /billing/determine-code` | 200 — returned `99490`, `$66.13` |

### Platform regression sweep

| Endpoint | Result |
|---|:--:|
| `GET /health` | **200**, reports `case_management: active` |
| `GET /api/tefca/dashboard/summary` | 200 |
| `GET /api/tefca/registry/entities` | 200 |
| `GET /api/v1/tefca/connectors/status` | 200 |
| `GET /api/tefca/qa/connector-health` | 200 |
| `GET /api/admin/users` | 200 |
| `POST /api/auth/login` (bad creds) | 401 — login path healthy |
| App import | OK — 278 routes total; 22 case-management; **92 TEFCA routes intact** |
| All 24 `safe_load` modules | all logged `Loaded:` — none skipped |

### Pre-existing issues observed (NOT caused by this change)

1. `GET /api/documents` → **500**, `column documents.tenant_id does not exist`.
   Local-DB schema drift; unrelated to `case_management`.
2. Local `.env` `SECRET_KEY` is **42 chars**, below the enforced 64-char floor, so
   the app cannot boot locally from `.env` alone (`app/main.py:11` →
   `app/core/config.py:98` raises). Validation used an exported throwaway key;
   `.env` was not modified. Local-only — prod key was rotated to 65 chars.
3. `GET /api/v1/bulletin/today/fcc` hangs >2 min (known-slow collect path).
4. In-memory burst rate limiter returned 429 after 11 rapid unauthenticated
   calls during testing — expected control behaviour, not a defect.

---

## 5. Rollback

Fully reversible; no data or schema change, no migration, no config change.

**Backend**
```bash
cd "C:/Imran_Coding projects/DocuAction/backend"
git revert <backend-commit-sha>       # or:
git checkout <prev-sha> -- app/case_management/routes.py
```

**Frontend**
```bash
cd "C:/Imran_Coding projects/DocuAction/frontend"
git revert <frontend-commit-sha>
```

Reverting the backend alone restores the previous (vulnerable) behaviour and the
frontend keeps working, since sending an unused `Authorization` header is
harmless. **Reverting the frontend alone re-breaks the page** — revert backend
first, or both together.

---

## 6. Residual risk / follow-ups

| Item | Note |
|---|---|
| **DP-02 still open** | Auth now required, but PHI is still sent to Anthropic **unmasked**. `mask_pii` is not applied on this path. Requires signed BAA + zero-retention confirmation. Next item in Sprint 1. |
| **No module-level authorization** | `users.allowed_modules` remains unenforced server-side. Any authenticated user — including one without the `case_management` area granted in Admin — can reach these endpoints. Recommend a reusable `require_module("case_management")` dependency as a Sprint 1/2 item; it would also close the same gap on every other module router. |
| **No role tiering** | A `viewer` can generate notes and sign via `PATCH /notes/{id}/approve`. Consider `require_role("contributor")` on write paths once role assignments are audited. |
| **No audit logging** | PHI-touching calls in this module are not written to `audit_logs` (relates to AUDIT-READ). |
| **`models.py` dead code** | 6 ORM models defined, never imported, no tables. Either wire them up (with tenant scoping + migration) or delete. Note that wiring them up would turn this module into a PHI-at-rest store and require re-review. |
| **Stub handlers** | 9 GETs return hardcoded zeros/empties. Not a security issue, but the module is not functionally complete — relevant to any go-live claim about Case Management. |

---

## 7. Finding register update

`security_findings.md` row AUTHZ-01 should read: endpoint count **22** (not 12);
exposure is **unauthenticated PHI ingress → Anthropic egress**, not PHI at rest;
status **REMEDIATED** (auth gate) with the module-authorization portion of the
original remediation text ("+ module gate") **still open** and tracked in §6.
