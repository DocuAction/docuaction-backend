# Runbook: enable App Service health check

**Status:** open on both apps.

## What it changes

App Service polls a path you nominate. Instances that fail repeatedly are taken
out of rotation and, on a multi-instance plan, replaced. Without it a wedged
worker keeps receiving traffic until someone notices.

## Why `/health` is the right path here, with one caveat

`/health` already reports scheduler state and probes TEFCA connectors, and it is
built to never raise. The caveat: it calls `_probe_tefca()`, which hits external
services (NPPES, PECOS, LEIE) behind a cache. If those degrade, `/health` still
returns 200 with `"status": "degraded"` — good, because a third-party outage must
not cause Azure to recycle your instances.

## Procedure

```bash
az webapp config set -n Docuaction -g rg-docuaction-prod --generic-configurations '{"healthCheckPath": "/health"}'
az webapp config set -n docuaction-dev -g rg-docuaction-dev --generic-configurations '{"healthCheckPath": "/health"}'
```

Verify:
```bash
az webapp config show -n Docuaction -g rg-docuaction-prod --query "healthCheckPath" -o tsv
```

## Before enabling

Health check only removes instances when the plan has more than one. Prod runs
S1; confirm the instance count, or this buys monitoring signal rather than
self-healing:
```bash
az appservice plan list -g rg-docuaction-prod --query "[].{name:name,sku:sku.name,workers:numberOfWorkers}" -o tsv
```

`ALLOWED_HOSTS` must permit the probe. TrustedHostMiddleware returns **400 on
every path including `/health`** for an unlisted host, which would make the
health check fail permanently and pull every instance out of rotation. Confirm
the azurewebsites.net hostname is in `ALLOWED_HOSTS` before you enable this.
