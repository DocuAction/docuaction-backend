# Attack Surface Inventory (Section 2E)

## Network attack surface

**API endpoints: 411 total**
| Bucket | Count | Notes |
|---|---:|---|
| Authenticated (any of require_role/get_current_user/require_permission/guard/admin or router-gate) | **~340** | majority |
| Intentionally public (Cat 1) | **~28** | /health, auth login/reset, SSO, bulletin reads |
| Public-verify (Cat 2) | **~6** | security/status, bulletin downloads-by-id |
| **Unauthenticated findings (Cat 3)** | **~32 confirmed + ~44 to verify** | dormant GovCon/ATS CRUD (tables not deployed) |
| **File upload endpoints** | 17 modules | scanned by `file_scanner.py`; incl. TEFCA import (fhir/csv), documents, audio, migration, bulletin |
| **File download/export endpoints** | multiple | bulletin downloads (unauth), report/PDF/DOCX/CSV exports, `/export`, TEFCA reports |
| Admin-only | ~15 (`require_admin`) + admin_users | user/access management |

**UI routes:** 75 (Next.js App Router; static export on SWA CDN). 44 reference the auth gate.

**Authentication endpoints:** login, signup, token refresh, password forgot/reset, Entra SSO login/callback, verify-email.

**Health/status/info endpoints:** `/health` (returns version + scheduler state — minor info disclosure, acceptable), `/api/security/residency`+`/status` (unauth — verify), `/docs`+`/openapi.json` (disabled in prod).

**Management endpoint:** Kudu SCM (`*.scm.azurewebsites.net`) — **AAD-only** (basic auth disabled); App Service SSH via tunnel (AAD).

## Application attack surface
- **User input:** every form/search/filter across 75 UI routes; TEFCA search (`q`), entity filters, import file contents.
- **Query parameters:** pagination (`limit`/`offset`), filter params, `agency_id`/`briefing_id`/`{id}` path params (IDOR surface on unauth bulletin downloads).
- **Request headers:** `Authorization` (bearer), `Host` (TrustedHost-checked), `Origin` (CORS), `Content-Type`.
- **Cookies:** none for app auth (JWT in localStorage) → smaller CSRF surface, larger XSS-exfiltration surface.
- **WebSockets:** none observed.
- **File parsers as attack surface:** PDF (`pdfplumber`), DOCX (`python-docx`), XLSX (`openpyxl`), JSON/CSV (import), audio (Whisper) — untrusted-input parsers; mitigated by the pre-scan but parser CVEs remain a surface.
- **Scheduler/background triggers:** bulletin daily job (internal), plus manual `POST /collect`/`/send` (guarded).
- **AI prompt surface:** user text → LLM prompts (prompt-injection surface, Part 8/2J).

## Infrastructure attack surface
| Surface | Exposure | Assessment |
|---|---|---|
| App Service (Docuaction) | Public HTTPS (api-prod.docuaction.io) | TLS 1.2, HTTPS-only ✅ |
| Static Web Apps (prod+dev) | Public HTTPS | CDN static ✅ |
| PostgreSQL (docuaction-db-geo) | private/public **to confirm** (Part 9) | ⚠ verify firewall; my local seeding used a public path historically — confirm prod |
| Key Vault (docuaction-kv-prod) | **Private endpoint** (VNet) | ✅ not publicly reachable |
| Kudu SCM | Public host, **AAD-only auth** | ✅ basic auth disabled |
| Second Postgres (docuaction-db) | unknown | ⚠ orphaned server = extra surface |

## Prioritized attack-surface reductions (documented only)
1. Gate/unmount **~32–76 unauthenticated commercial endpoints**.
2. Put a **WAF/Front Door** in front of the App Service.
3. Confirm **Postgres is private-access only**; decommission the orphaned DB server.
4. Review the **IDOR** on bulletin downloads-by-id and the unauth `security/*` info endpoints.
5. Reduce **parser** surface (already scanned) + add **SSRF** allowlists on any user-influenced outbound URL.
