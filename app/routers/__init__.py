"""DORMANT commercial modules - NOT mounted, NOT reachable at runtime.

SECURITY: Disabled - dormant, no tables, no auth.
Re-enable after adding auth + deploying schema.

Nothing in this package is wired into the application. `app/main.py` is the only
FastAPI entrypoint and it mounts routers by explicit `safe_load(...)` calls; none
of them name `app.routers`. A repo-wide search for `app.routers` finds zero
importers outside this directory. These endpoints therefore do not exist at
runtime - not as 401s or 403s, but as nothing at all.

That matters for how the findings here should be read. Static analysis flags 36
High AGT-AUTHZ-001 (missing authorization) plus 12 Low across these files, and
every one of them is accurate about the source: these handlers genuinely have no
auth dependency. They are not exploitable only because the router is never
included. That is one deployment accident away from being real.

Those findings are suppressed with a 90-day expiry (to 2026-10-26) rather than
permanently, precisely so they come back if this code is still here and still
unauthenticated. Suppression keys on a fingerprint that excludes line numbers, so
it would survive the module being mounted - the expiry is what stops a
mounted-but-unauthenticated router from staying invisible.

Before mounting ANY module in this package:
  1. Add an auth dependency to every route (see the live modules in app/api/ for
     the `Depends(...)` pattern).
  2. Deploy the schema - these modules reference tables that exist in no deployed
     database.
  3. Drop the corresponding suppressions so the scanner re-evaluates the code as
     reachable.
"""
