# RBAC Verification Matrix — Block 4

**Contract:** 7571MN26F80064

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service |
| Build | Git SHA `d2ade36444eb0bc10f61614e541d72f307ed687e` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-02T20:54:46.115325+00:00 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| httpx | 0.28.1 |

## Method

Every endpoint below was called with every role. Access tokens for non-admin
roles expire after **15 minutes** (`ACCESS_EXPIRE_NORMAL`) while admin tokens last
24 hours, so tokens were minted per role sweep and each was liveness-checked
against `GET /api/auth/me` **before** any 401/403 was interpreted. A cached
non-admin token silently expires mid-run and reports `401 Invalid or expired
token`, which is indistinguishable from a role rejection at the status-code
level — that artefact produced false RBAC failures earlier in this engagement and
is explicitly guarded against here.

`404` is treated as *permitted* — the gate allowed the request through and the
target row simply does not exist. `409` likewise indicates the request passed
authorization and was rejected on a business rule (duplicate rule code).

## Role levels

| Account | Role claim | Level |
|---|---|---|
| `viewer@docuaction.io` | viewer | 1 |
| `analyst@docuaction.io` | analyst/contributor | 2 |
| `reviewer@docuaction.io` | reviewer | 4 |
| `testadmin@docuaction.io` | admin(test) | 8 |
| `admin@docuaction.io` | admin | 8 |

## Matrix — actual HTTP status by role

| Endpoint | viewer | analyst/contributor | reviewer | admin(test) | admin |
|---|---|---|---|---|---|
| `GET /auth/me` | 200 OK | 200 OK | 200 OK | 200 OK | 200 OK |
| `GET /registry/stats` | 403 OK | 403 OK | 200 OK | 200 OK | 200 OK |
| `GET /registry/entities` | 403 OK | 403 OK | 200 OK | 200 OK | 200 OK |
| `GET /registry/entities/{id}` | 403 OK | 403 OK | 200 OK | 200 OK | 200 OK |
| `GET /registry/findings` | 403 OK | 403 OK | 200 OK | 200 OK | 200 OK |
| `GET /arc/review-rules` | 200 OK | 200 OK | 200 OK | 200 OK | 200 OK |
| `GET /arc/reviews` | 200 OK | 200 OK | 200 OK | 200 OK | 200 OK |
| `GET /arc/reports` | 200 OK | 200 OK | 200 OK | 200 OK | 200 OK |
| `POST /registry/entities/{id}/verify` | 403 OK | 403 OK | 200 OK | 200 OK | 200 OK |
| `PATCH /arc/reviews/{id}/resolve` | 403 OK | 403 OK | 200 OK | 200 OK | 200 OK |
| `POST /arc/review-rules` | 403 OK | 403 OK | 403 OK | 200 OK | 409 OK |
| `POST /arc/reports/generate` | 403 OK | 403 OK | 403 OK | 200 OK | 200 OK |
| `POST /registry/dev/seed` | 403 OK | 403 OK | 403 OK | 200 OK | 200 OK |

**Matrix result: 65/65 cells as specified.**

## Detailed results

| Role | Endpoint | Expected | Actual | Result |
|---|---|---|---|---|
| viewer | `GET /auth/me` | permitted (>= viewer) | HTTP 200 | **PASS** |
| viewer | `GET /registry/stats` | 403 denied | HTTP 403 | **PASS** |
| viewer | `GET /registry/entities` | 403 denied | HTTP 403 | **PASS** |
| viewer | `GET /registry/entities/{id}` | 403 denied | HTTP 403 | **PASS** |
| viewer | `GET /registry/findings` | 403 denied | HTTP 403 | **PASS** |
| viewer | `GET /arc/review-rules` | permitted (>= viewer) | HTTP 200 | **PASS** |
| viewer | `GET /arc/reviews` | permitted (>= viewer) | HTTP 200 | **PASS** |
| viewer | `GET /arc/reports` | permitted (>= viewer) | HTTP 200 | **PASS** |
| viewer | `POST /registry/entities/{id}/verify` | 403 denied | HTTP 403 | **PASS** |
| viewer | `PATCH /arc/reviews/{id}/resolve` | 403 denied | HTTP 403 | **PASS** |
| viewer | `POST /arc/review-rules` | 403 denied | HTTP 403 | **PASS** |
| viewer | `POST /arc/reports/generate` | 403 denied | HTTP 403 | **PASS** |
| viewer | `POST /registry/dev/seed` | 403 denied | HTTP 403 | **PASS** |
| analyst/contributor | `GET /auth/me` | permitted (>= viewer) | HTTP 200 | **PASS** |
| analyst/contributor | `GET /registry/stats` | 403 denied | HTTP 403 | **PASS** |
| analyst/contributor | `GET /registry/entities` | 403 denied | HTTP 403 | **PASS** |
| analyst/contributor | `GET /registry/entities/{id}` | 403 denied | HTTP 403 | **PASS** |
| analyst/contributor | `GET /registry/findings` | 403 denied | HTTP 403 | **PASS** |
| analyst/contributor | `GET /arc/review-rules` | permitted (>= viewer) | HTTP 200 | **PASS** |
| analyst/contributor | `GET /arc/reviews` | permitted (>= viewer) | HTTP 200 | **PASS** |
| analyst/contributor | `GET /arc/reports` | permitted (>= viewer) | HTTP 200 | **PASS** |
| analyst/contributor | `POST /registry/entities/{id}/verify` | 403 denied (router gate binds) | HTTP 403 | **PASS** |
| analyst/contributor | `PATCH /arc/reviews/{id}/resolve` | 403 denied | HTTP 403 | **PASS** |
| analyst/contributor | `POST /arc/review-rules` | 403 denied | HTTP 403 | **PASS** |
| analyst/contributor | `POST /arc/reports/generate` | 403 denied | HTTP 403 | **PASS** |
| analyst/contributor | `POST /registry/dev/seed` | 403 denied | HTTP 403 | **PASS** |
| reviewer | `GET /auth/me` | permitted (>= viewer) | HTTP 200 | **PASS** |
| reviewer | `GET /registry/stats` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| reviewer | `GET /registry/entities` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| reviewer | `GET /registry/entities/{id}` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| reviewer | `GET /registry/findings` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| reviewer | `GET /arc/review-rules` | permitted (>= viewer) | HTTP 200 | **PASS** |
| reviewer | `GET /arc/reviews` | permitted (>= viewer) | HTTP 200 | **PASS** |
| reviewer | `GET /arc/reports` | permitted (>= viewer) | HTTP 200 | **PASS** |
| reviewer | `POST /registry/entities/{id}/verify` | permitted (>= contributor) | HTTP 200 | **PASS** |
| reviewer | `PATCH /arc/reviews/{id}/resolve` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| reviewer | `POST /arc/review-rules` | 403 denied | HTTP 403 | **PASS** |
| reviewer | `POST /arc/reports/generate` | 403 denied | HTTP 403 | **PASS** |
| reviewer | `POST /registry/dev/seed` | 403 denied | HTTP 403 | **PASS** |
| admin(test) | `GET /auth/me` | permitted (>= viewer) | HTTP 200 | **PASS** |
| admin(test) | `GET /registry/stats` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| admin(test) | `GET /registry/entities` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| admin(test) | `GET /registry/entities/{id}` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| admin(test) | `GET /registry/findings` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| admin(test) | `GET /arc/review-rules` | permitted (>= viewer) | HTTP 200 | **PASS** |
| admin(test) | `GET /arc/reviews` | permitted (>= viewer) | HTTP 200 | **PASS** |
| admin(test) | `GET /arc/reports` | permitted (>= viewer) | HTTP 200 | **PASS** |
| admin(test) | `POST /registry/entities/{id}/verify` | permitted (>= contributor) | HTTP 200 | **PASS** |
| admin(test) | `PATCH /arc/reviews/{id}/resolve` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| admin(test) | `POST /arc/review-rules` | permitted (>= admin) | HTTP 200 | **PASS** |
| admin(test) | `POST /arc/reports/generate` | permitted (>= admin) | HTTP 200 | **PASS** |
| admin(test) | `POST /registry/dev/seed` | permitted (>= admin) | HTTP 200 | **PASS** |
| admin | `GET /auth/me` | permitted (>= viewer) | HTTP 200 | **PASS** |
| admin | `GET /registry/stats` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| admin | `GET /registry/entities` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| admin | `GET /registry/entities/{id}` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| admin | `GET /registry/findings` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| admin | `GET /arc/review-rules` | permitted (>= viewer) | HTTP 200 | **PASS** |
| admin | `GET /arc/reviews` | permitted (>= viewer) | HTTP 200 | **PASS** |
| admin | `GET /arc/reports` | permitted (>= viewer) | HTTP 200 | **PASS** |
| admin | `POST /registry/entities/{id}/verify` | permitted (>= contributor) | HTTP 200 | **PASS** |
| admin | `PATCH /arc/reviews/{id}/resolve` | permitted (>= reviewer) | HTTP 200 | **PASS** |
| admin | `POST /arc/review-rules` | permitted (>= admin) | HTTP 409 | **PASS** |
| admin | `POST /arc/reports/generate` | permitted (>= admin) | HTTP 200 | **PASS** |
| admin | `POST /registry/dev/seed` | permitted (>= admin) | HTTP 200 | **PASS** |

## Additional scenarios

| Scenario | Expected | Actual | Result |
|---|---|---|---|
| Expired/invalid token rejected | 401 | HTTP 401 | **PASS** |
| No credentials on protected route | 401 | HTTP 401 | **PASS** |
| viewer cannot POST verify (least privilege) | 403 | HTTP 403 | **PASS** |
| viewer cannot resolve B3 | 403 | HTTP 403 | **PASS** |
| viewer cannot create rules | 403 | HTTP 403 | **PASS** |
| Horizontal access: /auth/me returns only self | own identity | viewer@docuaction.io | **PASS** |

**Scenarios: 6/6 pass.**

## Observation — an unreachable role requirement

`POST /api/tefca/registry/entities/{id}/verify` declares `require_role("contributor")`
on the route handler. The `analyst@docuaction.io` account carries exactly that role
claim, yet the call returns **403**.

That 403 is **correct**. The registry router is declared with a router-level
dependency:

```python
router = APIRouter(prefix="/api/tefca/registry",
                   dependencies=[Depends(require_role("reviewer"))])
```

Router dependencies run before the handler's own, and `reviewer` (level 4) is
stricter than `contributor` (level 2), so the stricter gate binds and a
contributor never reaches the handler. The system fails closed, which is the
desired direction.

The defect is in **legibility, not enforcement**: the handler's signature states a
requirement that can never be the operative one. A developer reading
`verify_entity` would reasonably conclude that contributors can verify entities.
They cannot, and no test at the handler level would reveal it.

**Severity:** Informational. No access is granted that should be denied.
**Suggested resolution:** either raise the handler's declaration to `reviewer` so
it states the truth, or move the router-level gate down to the routes that
actually need it. Not changed here — Block 4 is a verification block, and altering
the authorization graph mid-verification would invalidate the matrix above.

## Least privilege

No role was able to reach any endpoint above its level. Every deny returned `403`
with a valid live token, and every unauthenticated call returned `401`. The
viewer account could not verify entities, resolve B3 reviews, or create rules.
`GET /api/auth/me` returned only the caller's own identity.
