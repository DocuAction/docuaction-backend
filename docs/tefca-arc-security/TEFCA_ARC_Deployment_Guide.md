# TEFCA ARC — Security Hardening Deployment Guide (RC2)

Scope: deploy the security-hardening release (`security/tefca-arc-hardening` → `main`).
Nature: **configuration + standard redeploy only.** No database migration, no schema change, no data backfill.

---

## 1. Prerequisites

- Merge of the RC PR into `main` (see `PULL_REQUEST.md` / `TEFCA_ARC_RC_Checklist.md`).
- Access to the deployment platform's environment-variable configuration (e.g. Railway service variables).
- The application already runs in production; this release changes no runtime contract by default.

## 2. Environment variables

Required (already set today — do not remove):
```
SECRET_KEY=<64+ random chars>
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>:5432/<db>
```

Recommended for this release (set at deploy):
```
DATABASE_SSL=require              # or verify-full after validating the DB cert
ALLOWED_ORIGINS=https://app.docuaction.io
ALLOWED_HOSTS=api.docuaction.io,api-prod.docuaction.io
```

Optional (leave unset to preserve current behavior):
```
REDIS_URL=redis://<host>:6379/0   # only if running >1 instance
REVOCATION_FAIL_CLOSED=false      # true only for high-assurance deployments
RATE_LIMIT_AUTH_PER_MINUTE=10
RATE_LIMIT_AUTH_BURST=5
ADMIN_TOKEN_HOURS=24              # consider a shorter value for tightened baselines
STANDARDIZED_ERROR_ENVELOPE=false # enable only after confirming client compatibility
```

Unchanged operational flags (existing policy): `ENABLE_SCHEDULER`, `ENABLE_QA_MONITOR` — set on exactly one instance.

## 3. Deploy order

1. **Merge** the PR to `main`.
2. **Set env vars** (§2) on the production service.
3. **Redeploy** `main` via the existing pipeline (no build/schema step differs).
4. **Watch startup logs** for:
   - `Loaded: tefca-review-protocol + dashboard (REQUIRED …)`
   - `Creating DB engine: … (SSL=require)` if `DATABASE_SSL` set
   - `Token revocation: using Redis backend` if `REDIS_URL` set (else in-memory)
5. Run the **post-deploy smoke checks** (§4).

> Multi-instance note: without `REDIS_URL`, rate-limit counters and token revocation are per-instance. Set `REDIS_URL` before scaling beyond one instance so logout/role-change revocation is cluster-wide.

## 4. Post-deploy smoke checks

```
# Health
GET /health                          → 200; modules.tefca_review_protocol == "active"

# Security headers present on any response
curl -sI https://<host>/health | grep -iE "strict-transport|content-security|x-frame|x-content-type|referrer-policy|permissions-policy|x-request-id"

# Auth required on operational endpoint
GET /api/v1/tefca/reviews            → 401/403 without a valid token
GET /api/v1/tefca/reviews (reviewer) → 200

# Brute-force throttle
6x rapid POST /api/auth/login (bad)  → 429 after the burst ceiling

# Logout revocation
POST /api/auth/logout (Bearer T)     → 200; reuse T → 401

# Error hygiene
force a 500                          → generic JSON body, no stack trace, carries X-Request-ID
```

## 5. Verifying the two RC2 controls in production

- **Role/permission change revocation:** change a test user's role via `PATCH /api/admin/users/{id}/role`; that user's existing token should now return 401 on the next request.
- **Fail-closed (only if `REVOCATION_FAIL_CLOSED=true` with Redis):** simulate a Redis outage in a staging environment → authenticated requests return `401 "Authorization temporarily unavailable"` and a `SECURITY:` log line with `request_id`/`correlation_id` appears. Do **not** test by disrupting production Redis.

## 6. Rollback

| Situation | Action |
|---|---|
| A single control misbehaves | Unset its env var (e.g. `DATABASE_SSL`, `REVOCATION_FAIL_CLOSED`, `RATE_LIMIT_ENABLED=false`) and redeploy |
| Broader issue | `git revert <phase sha>` (phases are independent) and redeploy, **or** redeploy the prior `main` build |
| Data | None required — no schema or data changes |

Rollback is immediate and non-destructive; no migration to reverse.

## 7. Contacts / escalation

- Security review sign-off: per `TEFCA_ARC_RC_Checklist.md` §H.
- Post-freeze: security changes require a documented defect or approved change request.
