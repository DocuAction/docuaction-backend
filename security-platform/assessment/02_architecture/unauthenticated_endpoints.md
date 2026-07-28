# Unauthenticated Endpoint Classification (Section 2F)

> **Method:** static analysis of auth dependencies across all 52 endpoint-defining modules. Auth patterns recognized: `require_role`, `get_current_user`, `require_permission`, `guard(role)` (bulletin), `require_admin`, plus **router-level** `dependencies=[...]`. This is best-effort static classification; the Category 3 list should be confirmed by a live probe in Phase 2.

## Auth coverage summary

| Auth pattern | Usages |
|---|---:|
| `get_current_user` | 268 |
| `require_role` | 96 |
| `require_admin` | 15 |
| `require_permission` (migration) | 13 |
| `guard(role)` (bulletin writes) | 15 |
| Router-level gate | 2 routers (TEFCA legacy `/api/v1/tefca`, registry `/api/tefca/registry`) |

**Correction to Part 1's rough "~106":** Part 1 counted only `get_current_user`+`require_role`. Adding `require_permission`, `guard`, `require_admin`, and router-level gates, **the vast majority of the 411 endpoints are authenticated.** The genuinely unauthenticated set is smaller and concentrated in specific modules.

## Category 1 — Intentionally Public (OK)

| Endpoint(s) | Module | Why public |
|---|---|---|
| `GET /health` | main.py | liveness (App Service health probe) |
| `POST /api/auth/login`, token issue | api/auth_endpoints.py | pre-auth |
| password forgot/reset (2) | api/password_reset.py | pre-auth (throttled in-memory) |
| Entra SSO login/callback (2) | api/azure_auth_routes.py | pre-auth OAuth |
| `/docs`, `/redoc`, `/openapi.json` | FastAPI | **disabled in prod** unless `ENABLE_DOCS`/`ENABLE_OPENAPI` |
| Bulletin public **reads** (~21 of 36: latest/today/history/archive/agencies…) | bulletin_intelligence/routes.py | public news product; writes are `guard()`-gated; whole module optionally gated by `BULLETIN_AUTH_ENABLED` |

**Count ≈ 28** (7 explicit + ~21 bulletin reads).

## Category 2 — Public but should be verified (info-disclosure / enumeration)

| Endpoint(s) | Module | Concern |
|---|---|---|
| `GET /api/security/residency`, `GET /api/security/status` | api/security.py | may disclose data-residency / security posture to anonymous callers |
| `GET /.../download/{agency_id}`, `/download-options`, `/download-excel`, `/briefings/{briefing_id}/excel` (4) | bulletin_download_routes.py | anonymous download by `agency_id`/`briefing_id` → **IDOR/enumeration** risk if briefings are not intended fully public |

**Count ≈ 6 — verify in Phase 2.** (Likely acceptable for a public bulletin, but the `security/*` info endpoints deserve review.)

## Category 3 — ACTUALLY MISSING AUTH (security findings)

These endpoints take only `db: Depends(get_db)` with **no auth dependency and no router-level gate**. They belong to the **dormant commercial GovCon/ATS stack** (tables not deployed), which **mitigates but does not eliminate** the risk.

### 3a. Confirmed — zero auth in the whole file (GovCon/procurement)

| Module | Endpoints | Examples |
|---|---:|---|
| `routers/suppliers.py` | **12** | `POST /suppliers` (create), list, `GET /suppliers/{id}`, update, `POST /suppliers/seed` |
| `routers/quotes.py` | **6** | `POST /quotes` (create), versions, `GET /quotes/{id}` |
| `routers/rfq.py` | **4** | `POST /rfq` (create), list, `GET /rfq/{id}` |
| `routers/products.py` | **3** | `POST /products` (create), list, get |
| `routers/bom.py` | **3** | BOM CRUD |
| `routers/deal_regs.py` | **3** | deal registration CRUD |
| `routers/pricing.py` | **1** | pricing |
| **Subtotal** | **32** | |

### 3b. Partial — file has *some* auth but a subset of endpoints are unauthenticated (verify per-endpoint)

| Module | Endpoints | Auth deps present | Likely unauthenticated subset |
|---|---:|---:|---:|
| `routers/ats.py` | 26 | 3 | ~23 |
| `routers/ats_agent.py` | 14 | 2 | ~12 |
| `routers/bench.py` | 11 | 2 | ~9 |
| **Subtotal (to confirm)** | | | **~44** |

### Finding
- **~32 confirmed** unauthenticated write/read endpoints (GovCon), **plus ~44 to confirm** (ATS).
- **Severity: HIGH by design, downgraded to MEDIUM in the current deployment** because the backing tables (`suppliers`, `rfqs`, `quotes`, `candidates`, …) are **not present in the database** — calling these endpoints would raise a DB error (500), not leak data. Risk becomes HIGH the moment those tables are created, or if error responses disclose internals.

### Recommendation (documented only)
1. **Do not mount** the dormant commercial routers in the federal deployment (conditional `safe_load` behind a feature flag), **or**
2. apply a **router-level `dependencies=[Depends(require_role("contributor"))]`** to every commercial router (one line each), **or**
3. move the commercial stack to a **separate service/app** entirely (aligns with the two-Base split).

Also review `api/security.py` info endpoints and the bulletin download-by-id endpoints for IDOR.

## Positive note
The **CRITICAL** modules (TEFCA registry + legacy, Auth, Admin, Healthcare, Case Mgmt, Migration) are **consistently authenticated** — the two TEFCA routers use router-level `require_role("reviewer")`, migration uses fine-grained `require_permission`, and admin/healthcare/CM use `get_current_user`/`require_role`. The auth gap is isolated to the **dormant commercial modules.**
