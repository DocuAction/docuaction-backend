# Breaking change: unauthenticated requests now return 401, not 403

**Introduced:** 2026-07-28, by the FastAPI upgrade in the hardening sprint
(`fastapi 0.115.0 -> 0.140.13`). **Deployed to prod** the same day.

## What changed

Every endpoint protected by `HTTPBearer` returns **401 Unauthorized** for a
request with no credentials. It previously returned **403 Forbidden**.

Measured directly, same minimal app, only the framework differs:

```
fastapi 0.115.0 : HTTPBearer with no credentials -> 403
fastapi 0.140.13: HTTPBearer with no credentials -> 401
```

This is FastAPI's behaviour, not an application change. No auth logic in this
codebase was modified to produce it.

## Why it is correct

401 is what RFC 7235 specifies for a missing or invalid credential; 403 means the
credential was understood and refused. The old 403 was wrong, and clients that
distinguish "log in" from "you may not do that" were being told the wrong thing.

## Who this breaks

Anything asserting on 403 for an unauthenticated call:

- Smoke tests. `.github/workflows/deploy-backend.yml` checks
  `/api/v1/case-management/patients` for 403 and will now log
  `WARNING: expected 403`. The step does not fail the build, so this is noise
  rather than breakage - but it should be corrected to 401.
- The DAST suite's auth-gate assertions.
- Any external consumer branching on the status code.

Endpoints that return 403 for an *authenticated* principal lacking the required
role are unaffected - that is still 403, and correctly so.

## Verified on prod after deploy

```
/api/v1/case-management/patients   401   (was 403)
/api/v1/bulletin/costs             401   (was 200 - newly guarded)
/api/tefca/dashboard/summary       401   (was 200 - newly guarded)
/health                            200
```
