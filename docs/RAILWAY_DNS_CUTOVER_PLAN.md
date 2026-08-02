# Railway → Azure DNS Cutover Plan

**Contract:** 7571MN26F80064 · **Date:** 2026-08-02 · **Owner:** Imran (DNS access required)

## Current state — verified, not assumed

| Host | Resolves to | Platform | Evidence |
|------|-------------|----------|----------|
| `api.docuaction.io` | CNAME → `thzu1ngo.up.railway.app` (69.46.46.105) | **Railway** | `Server: railway-hikari` response header |
| `api-prod.docuaction.io` | CNAME → `docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net` (20.119.144.55) | **Azure App Service** | `/api/config` → `environment=production` |

Azure custom domains currently bound to the `Docuaction` app:

| Domain | SSL |
|--------|-----|
| `docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net` | (default) |
| `api-prod.docuaction.io` | **SniEnabled**, thumbprint `E6F7495F…81E1` |

**`api.docuaction.io` is NOT bound to the Azure app.** It cannot be repointed
until it is added, or Azure will reject the host.

## The problem

Anything deployed to Azure production does **not** reach `api.docuaction.io`.
Railway keeps serving whatever code it last built. Concretely, the following are
live on Azure and **absent from Railway**:

- the import-error-detail fix (batches report `error_count` with no `errors[]`)
- the TEFCA ARC v2 rule set (SAM-excluded entities classified B1 instead of B4)
- security hardening from the prior sprints
- `/api/config`, which is why that endpoint 404s on `api.docuaction.io`

Any user, document, or integration still pointing at `api.docuaction.io` is on
old code. This is the single highest-risk item outstanding.

## Options

| Option | Description | Verdict |
|--------|-------------|---------|
| **A** | Repoint `api.docuaction.io` DNS → Azure | **Recommended.** Fastest, no consumer changes, one reversible DNS edit. |
| **B** | Retire `api.docuaction.io`, move everything to `api-prod.docuaction.io` | Requires finding and updating every reference — frontend builds, docs, emails, any third-party integration. Anything missed breaks with no warning, and you cannot enumerate external consumers. |
| **C** | Keep both | **Not recommended.** Railway serves stale code indefinitely and the two hosts drift further apart every deploy. This is the current state by inertia, not by decision. |

**Recommendation: Option A.** It is one change, it is reversible by reverting the
CNAME, and it requires nothing from consumers.

## Steps — Option A

### 1. Bind the domain in Azure first

Ordering matters. Add the hostname **before** changing DNS, or requests arrive
at an App Service that does not recognise the host and are rejected outright.

Azure verifies ownership via a TXT record:

```
# TXT  asuid.api.docuaction.io  ->  <verification id>
az webapp show --name Docuaction --resource-group rg-docuaction-prod \
  --query customDomainVerificationId -o tsv
```

Add that TXT record at the registrar, then:

```
az webapp config hostname add --webapp-name Docuaction \
  --resource-group rg-docuaction-prod \
  --hostname api.docuaction.io
```

### 2. Add the host to `ALLOWED_HOSTS`

`TrustedHostMiddleware` returns **400 on every route including `/health`** for an
unlisted host. Do this before cutover or the new domain fails closed.

```
az webapp config appsettings list --name Docuaction \
  --resource-group rg-docuaction-prod \
  --query "[?name=='ALLOWED_HOSTS'].value" -o tsv
# append api.docuaction.io, then set it back, then restart
```

### 3. Provision TLS

```
az webapp config ssl create --resource-group rg-docuaction-prod \
  --name Docuaction --hostname api.docuaction.io
az webapp config ssl bind --resource-group rg-docuaction-prod \
  --name Docuaction --certificate-thumbprint <thumb> --ssl-type SNI
```

A free App Service Managed Certificate requires the CNAME to already point at
Azure — so this step may need to follow step 4, with a short window where the
domain is reachable over HTTP but not yet HTTPS. Plan the cutover for a low
traffic period for that reason.

### 4. Repoint DNS

Change the CNAME for `api.docuaction.io`:

```
from: thzu1ngo.up.railway.app
to:   docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net
```

**Lower the TTL to 300 s at least 24 hours beforehand.** At a default TTL the
rollback in step 6 takes as long as the original TTL to take effect, which turns
a two-minute revert into an hours-long outage.

### 5. Verify

```
nslookup api.docuaction.io                     # expect the Azure CNAME chain
curl -sI https://api.docuaction.io/health | grep -i server   # must NOT be railway-hikari
curl -s https://api.docuaction.io/api/config   # expect environment=production
curl -s -o /dev/null -w '%{http_code}' https://api.docuaction.io/api/tefca/registry/stats
                                               # expect 401, not 404 — guarded, and the route exists
```

The `/api/config` check is the meaningful one: that endpoint does not exist on
the Railway build, so a 200 with `environment=production` proves Azure is
serving, not a cached response.

### 6. Rollback

Revert the CNAME to `thzu1ngo.up.railway.app`. Keep the Railway service running
and unchanged until the verification period ends — a rollback target that has
been torn down is not a rollback target.

### 7. Decommission

Only after a **7-day** verification period with no regressions: shut down the
Railway service. Take a final export of anything Railway holds that Azure does
not before deleting.

## Risks

| Risk | Mitigation |
|------|------------|
| TLS gap between CNAME change and certificate binding | Schedule off-peak; pre-stage the certificate if the registrar allows CAA/validation ahead of time |
| `ALLOWED_HOSTS` not updated → 400 on everything | Step 2, before DNS |
| Railway holds data Azure does not | Verify before decommission, not after |
| Long TTL makes rollback slow | Lower TTL to 300 s a day ahead |
| Frontend has the old host baked in | `NEXT_PUBLIC_API_URL` is inlined at **build** time — rebuild and redeploy the SWAs if they reference `api.docuaction.io` |

## Not automatable

Steps 1 (TXT record) and 4 (CNAME) require registrar/Cloudflare access, which
this environment does not have. Everything else is scripted above.
