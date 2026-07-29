# FCC Bulletin v1.0 — Defect / Findings Log

**Prepared:** 2026-07-07

Findings from the pre-production UAT. Most items are **readiness gaps or design limitations**, not code failures; each is labeled by type. Severity reflects impact on a production go-live for a daily-before-8-AM-ET Government briefing. **No code defect that breaks the default build was found** — the module builds, imports, and renders with flags OFF.

**Type:** Defect (code fault) · Limitation (by-design gap) · Process (validation/env gap) · Representation (claim risk).

---

### DEF-001 — Hydration-mismatch console warning (environmental)
- **Severity:** Low · **Type:** Defect (external/environmental) · **Priority:** Low
- **Description:** A React "tree hydrated but attributes didn't match" error appears once in the dev console.
- **Steps to reproduce:** Load `localhost:3000/bulletin` (dev) with Grammarly/QuillBot installed; read console.
- **Expected:** No console errors.
- **Actual:** One hydration warning; the diff shows the mismatch is on `<html>`/`<body>` attributes injected by browser extensions (`data-gr-ext-installed`, `data-qb-installed`) at the shared root `layout.tsx`, not in bulletin code. Dev-overlay only.
- **Recommendation:** Not a bulletin defect. If desired platform-wide, add `suppressHydrationWarning` on `<body>` in the shared root layout — **out of FCC Bulletin scope; do not change here.** Re-verify in a production build without extensions.

### DEF-002 — Endpoints unauthenticated in default configuration
- **Severity:** High · **Type:** Limitation (by design) · **Priority:** High (before production exposure)
- **Description:** Auth is flag-gated and OFF by default, so all bulletin endpoints are unauthenticated (unchanged from pre-v1.0).
- **Steps to reproduce:** Deploy with `BULLETIN_AUTH_ENABLED` unset; call any state-changing endpoint.
- **Expected (for production):** State-changing/costly endpoints require a valid role.
- **Actual:** No auth enforced until the flag is enabled.
- **Recommendation:** Before production exposure, enable `BULLETIN_AUTH_ENABLED`, verify the deployed UI attaches a valid token for a logged-in user, and confirm 401/403 on unauthorized calls in staging.

### DEF-003 — Delivery log has no writer
- **Severity:** Medium · **Type:** Limitation · **Priority:** Medium
- **Description:** `bulletin_delivery_log` table exists but is never written; the Delivery Dashboard reflects run history (`delivered_at`), not a per-recipient log.
- **Expected:** Per-recipient delivery records (recipient, SendGrid/provider id, result).
- **Actual:** No delivery-log rows; dashboard shows history-derived delivery state.
- **Recommendation:** Implement the C3 delivery-log writer before representing the Delivery Dashboard as a delivery audit.

### DEF-004 — QA per-item actions not implemented
- **Severity:** Medium · **Type:** Limitation · **Priority:** Medium
- **Description:** UAT expects QA approval/rejection/notes and per-item missing-field flags; v1.0 QA is coverage-level only.
- **Expected:** Article-level approve/reject/notes; missing summary/URL/date flags.
- **Actual:** QA Dashboard surfaces coverage-level checks (missing categories, dupes, subscription); no per-item workflow.
- **Recommendation:** Scope per-item QA as a future enhancement; do not advertise per-item QA in v1.0.

### DEF-005 — "SendGrid integration" is an httpx HTTP call, not a SendGrid SDK
- **Severity:** Low · **Type:** Representation/Limitation · **Priority:** Low
- **Description:** Delivery uses a direct `httpx` request; the `sendgrid` library is not installed.
- **Expected (per UAT wording):** SendGrid integration.
- **Actual:** No SendGrid SDK; HTTP call via `httpx`.
- **Recommendation:** Correct the wording in user-facing docs, or add the SendGrid SDK if a supported integration is required.

### DEF-006 — "Section 508: Compliant" banner is unaudited
- **Severity:** Medium · **Type:** Representation · **Priority:** High (claim-risk)
- **Description:** The hero credential bar displays a static "Section 508: Compliant" chip; no 508 audit has been performed.
- **Expected:** Compliance claims backed by an audit.
- **Actual:** Unverified compliance assertion (chip predates this work).
- **Recommendation:** Remove/replace the chip until a formal 508 audit is completed, to avoid a contract misrepresentation. *(UI text change — outside this validation task's no-code scope; flagged for a follow-up.)*

### DEF-007 — Coverage % unavailable by default (honest, expected)
- **Severity:** Informational · **Type:** Limitation (intended) · **Priority:** Low
- **Description:** `/coverage-assurance` returns `pending_instrumentation` until the source registry is seeded and per-source outcomes exist.
- **Actual:** `coverage_pct = null` by design (never estimated).
- **Recommendation:** Seed the registry + add per-source failure capture to reach "measured". Working as intended until then.

### DEF-008 — Functional UAT not executable locally
- **Severity:** High · **Type:** Process · **Priority:** High
- **Description:** The local frontend calls the production API (CORS-blocked for data) and the v1.0 backend is not deployed, so collection/briefing/export/delivery/analytics with real data cannot be validated locally.
- **Recommendation:** Stand up a **staging** environment (deployed v1.0 backend + test DB + same-origin/allowlisted frontend) and run the data-populated categories there before production.

### DEF-009 — Per-source failure/timing not captured
- **Severity:** Medium · **Type:** Limitation · **Priority:** Medium
- **Description:** Instrumentation records only succeeded sources (from the coverage report); HTTP status/error/response-time/retries are not populated.
- **Recommendation:** Wrap each ingest source to capture outcomes; required before Coverage % can be trusted as completeness.

### DEF-010 — Full backend boot with database unverified
- **Severity:** Medium · **Type:** Process · **Priority:** High
- **Description:** Only import-level startup was verified; a full uvicorn boot with `init_store` table creation and a live `/health` against a database was not run (no isolated test DB; production DB not used for testing).
- **Recommendation:** Verify full boot + table auto-creation + `/health` in staging before production.

---

## Severity summary

| Severity | Count | IDs |
|---|---|---|
| High | 3 | DEF-002, DEF-008, DEF-010 (+ DEF-006 high-priority) |
| Medium | 4 | DEF-003, DEF-004, DEF-009, DEF-006 |
| Low | 3 | DEF-001, DEF-005, DEF-007 |

**No blocking code defect in the default (flags-OFF) build.** The high-priority items are production-enablement and validation gaps, not faults in the committed code.
