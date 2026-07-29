# DocuAction Deployment Checklist

## Pre-Deployment
- [ ] All tests pass (`python -m pytest tests/ -v`)
- [ ] Security gate passes (`cd security-platform && python cli.py gate`)
- [ ] No .env files in artifact
- [ ] gunicorn in pydeps (prod only)
- [ ] No duplicate dist-info in pydeps
- [ ] All KV references verified (`./scripts/verify-keyvault-references.sh`)
- [ ] PR approved with 1+ reviewer
- [ ] CHANGELOG updated

## Dev Deployment
- [ ] Build from tag or branch tip (NOT working tree)
- [ ] Deploy **WITHOUT** `--clean` (Oryx build)
- [ ] Wait 90 seconds for startup
- [ ] `/health` → 200
- [ ] **Restart the app** — dev serves the previous build until it recycles
- [ ] Verify new feature works on dev
- [ ] If RemoteDisconnected error: check deployment status, do NOT re-run deploy

## Production Deployment
- [ ] Dev deployment verified first
- [ ] Build prod zip with pydeps + gunicorn
- [ ] Deploy **WITH** `--clean true`
- [ ] Wait 90 seconds for startup
- [ ] `/health` → 200
- [ ] `/api/v1/bulletin/health` → 200
- [ ] `/api/tefca/status` → 200
- [ ] `/api/v1/case-management/patients` → 401/403
- [ ] Scheduler running (check `/health`)
- [ ] Tag the release

## Post-Deployment
- [ ] Monitor for 30 minutes
- [ ] Check Application Insights for errors
- [ ] Verify no spike in 5xx responses
- [ ] Notify team of successful deployment

## Rollback
- [ ] If issues: redeploy previous tag
- [ ] `git revert` if a code change caused the issue
- [ ] If `--clean` broke something: redeploy without `--clean`
- [ ] Document the incident

---

## The three things that have actually gone wrong

Everything above is routine. These are the failure modes this project has hit,
and each one cost real time.

### 1. `--clean` is environment-specific, not a preference

| Environment | Build mode | `--clean true`? |
|---|---|---|
| **PROD** (`Docuaction`) | Pre-built `pydeps`, no Oryx | **YES — required** |
| **DEV** (`docuaction-dev`) | `SCM_DO_BUILD_DURING_DEPLOYMENT=true`, Oryx | **NO — never** |

On prod, omitting it overlays instead of replacing, leaving orphaned package
metadata that corrupts dependency scanning and the SBOM. On dev, using it deletes
`oryx-manifest.toml` — the file the startup script needs to locate the built
application — producing a crash loop and 503 on everything. Dev was taken down
this way on 2026-07-26.

### 2. A CLI error is not a failed deployment

`az webapp deploy` regularly ends with:

```
Raw Error : ('Connection aborted.', RemoteDisconnected(...))
```

**The deployment is usually still succeeding.** The CLI lost its polling
connection; the server keeps building for minutes afterwards.

Re-running the command to get a cleaner error starts a **second concurrent build
on the same app**, and the two collide. Observed twice on 2026-07-28: each time
the first deploy succeeded and went active, and the duplicate was recorded
failed. The retry *was* the failure.

Query the server instead:

```bash
az webapp log deployment list -n <app> -g <rg> \
  --query "[0:3].{t:received_time,s:status,a:active}" -o tsv
```

`0`=pending `1`=building `3`=failed `4`=success. Only `active: True` on a `4`
means the code is live.

### 3. `/health` does not prove a deployment landed

It answers 200 from the **old** code the entire time. Verify with an endpoint or
behaviour that exists only in the new build — a 404 turning into a 200 is proof;
a 200 staying 200 is not. On dev, also restart: the container serves the previous
build until it recycles, which is why a correct deployment can look like it did
nothing.

---

## Checks worth adding to the tick-list

- [ ] Confirm `ALLOWED_HOSTS` includes every hostname the deployment will be
      reached on. TrustedHost middleware returns **400 on every path including
      `/health`** for an unlisted host, so a missing entry fails the health check
      and looks like a total outage.
- [ ] After a dependency upgrade, re-check anything asserting on HTTP status
      codes. FastAPI 0.140 returns **401** where 0.115 returned **403** for a
      missing bearer credential.
- [ ] Confirm the deployment artifact contains data files the application reads
      at runtime, not just Python modules. The source catalogue lived under
      `docs/` and was silently absent from every deployed environment because the
      zip only ships `app/`, `alembic/`, `alembic.ini`, `requirements.txt` and
      `Procfile`.

Related: `docs/deployment/azure-deployment-guide.md`,
`docs/deployment/BREAKING_401_vs_403.md`, `docs/runbooks/`.
