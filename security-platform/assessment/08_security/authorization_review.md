# Authorization Review

> Manual review of the **live** authorization surface. Read-only. The live ASGI app is `app.main:app`; router registration verified in `app/main.py`.

## Correction to prior parts: the unauthenticated surface is NOT GovCon/ATS

Parts 1–2 flagged "~32 unauthenticated GovCon CRUD endpoints" and "~44 ATS endpoints." **Direct verification of `app/main.py` shows the `app/routers/*` GovCon and ATS modules are NOT registered** — `grep` for `app.routers` / `include_router(ats…)` in `main.py` returns nothing. They are **dead/unwired code** in the live entrypoint (they *would* be unauthenticated if wired, since they use the weaker `services/auth.py`). **They are not a live exposure.** This corrects the earlier conditional-HIGH.

**The real live unauthenticated surface is the Case Management PHI module**, registered at `main.py:321` via `safe_load("app.case_management", "case-management")`.

## Findings

### AUTHZ-01 — CRITICAL: entire Case Management PHI router is unauthenticated (Critical, CWE-306, OWASP A01)
`app/case_management/routes.py:34-37` — `cm_router = APIRouter(prefix="/api/v1/case-management")` defined with **no `dependencies=`**, and **no endpoint carries any `Depends`**. It is live (verified wired at `main.py:321`). Unauthenticated, state-changing, PHI-accepting endpoints (**12**):

| Method | Path | Line | Accepts |
|---|---|---|---|
| POST | `/patients` | :189 | name, MRN, DOB, diagnoses |
| POST | `/notes/voice-to-note` | :219 | **unauth file upload** + transcript |
| POST | `/notes/generate` | :260 | clinical note text |
| POST | `/notes/tcm` | :296 | clinical text |
| PATCH | `/notes/{id}/approve` | :338 | note id |
| POST | `/care-plans/generate` | :359 | patient clinical data |
| POST | `/discharge/generate` | :409 | discharge clinical data |
| POST | `/education/generate` | :445 | clinical data |
| POST | `/sdoh/assess` | :514 | social determinants |
| POST | `/government/cases/generate` | :534 | case data |
| POST | `/billing/determine-code` | :580 | clinical/billing data |
| POST | `/meetings/generate-minutes` | :652 | meeting content |

Plus unauthenticated **GET** reads (`/patients`, `/patients/{id}`, `/notes`, …).

**Honest scoping nuance (verified directly):** the patient-record CRUD endpoints are currently **non-persisting stubs** — `create_patient` echoes `patient.dict()` back with `"note": "Wire to database for production use."`; `get_patient` returns the same placeholder. So this is **not yet an unauthenticated PHI *database*.** **However**, the AI generation endpoints **do** accept real PHI in request bodies and forward it to Anthropic (see `data_protection_review.md` DP-02) — a live unauthenticated PHI→third-party path — and the `voice-to-note` upload bypasses the file scanner (`file_upload_review.md`). **Severity remains Critical** on the AI/upload paths; the stub CRUD is Critical-by-design-intent (it is clearly meant to persist PHI and ships with no auth).

**Fix:** add `dependencies=[Depends(require_role("...")), <module gate>]` to `cm_router`; route the upload through `FileScanner`; mask PHI before any external call. Effort: 0.5–1d for the auth gate.

### AUTHZ-02 — IDOR on healthcare claims (Medium, CWE-639, OWASP A01)
`api/healthcare_claims_routes.py` — `generate_appeal` (:171), `get_fwa_report` (:233), `get_revenue_impact` (:243), `validate_claim_codes` (:212) fetch `_claims_store[claim_id]` with **no `user_id` ownership check**. Any authenticated user can read/act on another user's claim by id. The sibling `get_claim`/`list_claims` (:133,146) **do** check `user_id` — so the gate exists and is simply missing on four handlers. **Fix:** apply the same `claim.user_id == user.id or admin` check. Effort: 0.5d.

### AUTHZ-GOOD — Privilege escalation / mass assignment on signup (Info)
Public `signup` hard-codes `role="pending"`, `is_active=False`, `plan="free"` server-side (`routes.py:248-261`); `SignupRequest` cannot carry `role`. Admin role/permission changes enforce super-admin gating (`admin_users.py:165,234,265,362`). Email-based auto-admin escalation was explicitly removed (`core/security.py:142-145`). Solid.

### AUTHZ-03 — Untyped-dict admin update (Low, CWE-915)
`admin_users.py:349-388` `PATCH /api/admin/users/{id}` takes a raw `payload: dict`. It is admin-gated and whitelists keys (role/is_active/permissions), so **not directly exploitable**, but the untyped pattern is fragile. **Fix:** typed Pydantic model. Effort: 0.5d.

## Positive authorization posture (the rest of the live app)
- **TEFCA registry** router globally gated `dependencies=[Depends(require_role("reviewer"))]` (`tefca_registry/routes.py:24-28`); writes require higher roles (`verify_bulk` → `senior_analyst`).
- **Documents/outputs** always filter by `user_id` (`routes.py:595,599,627,691`) — proper tenant/owner isolation.
- **Bulletin** endpoints role-gated via `guard(role)`.
- **RBAC ladder:** numeric `ROLE_HIERARCHY` (viewer=1 … admin=8) `core/security.py:33-43`, plus per-user `allowed_modules` allowlist.

## NIST mapping
AC-3 (access enforcement) — strong except AUTHZ-01/02; AC-6 (least privilege) ✅ on signup/admin; AC-4 (info flow) ◐ (case-mgmt PHI egress).
