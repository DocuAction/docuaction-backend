# Azure DEV Readiness Checklist — review only, no deployment

**Classification:** INTERNAL ENGINEERING · 2026-08-23
**Nothing in this document was executed.** No deployment, no installation, no
configuration change.

| Label | Meaning |
| --- | --- |
| **READY** | Verified by inspection of committed configuration |
| **ACTION** | Someone must do something before deploying |
| **UNVERIFIED** | Cannot be confirmed without deploying |

| # | Item | Status | Detail |
| --- | --- | --- | --- |
| 1 | Backend App Service | **READY** | `infra/modules/appService.bicep` |
| 2 | Frontend | **UNVERIFIED** | Separate repository, not tracked here |
| 3 | Database connection | **READY** | `DATABASE_URL` app setting |
| 4 | Key Vault | **READY** | `infra/modules/keyVault.bicep` |
| 5 | Managed identity | **READY** | Key Vault references resolve via MI |
| 6 | `SECRET_KEY` | **READY** deployed / **ACTION** local | `@Microsoft.KeyVault(...SecretName=SECRET-KEY)`. The 42-char local `.env` value is a workstation issue only |
| 7 | RBAC | **READY** | Four-role ladder |
| 8 | Migration command | **ACTION** | `alembic upgrade head` from `20260828_area1_grants`; single head confirmed |
| 9 | Area-1 privileges | **ACTION** | See the owner-role runbook |
| 10 | `docuaction_owner` | **ACTION** | **Production prerequisite** |
| 11 | Storage for retained artefacts | **ACTION** | ~1.7 GB per cycle; depends on D8 |
| 12 | Logging | **READY** | App Insights instrumentation key wired |
| 13 | Monitoring / alerts | **ACTION** | `scripts/setup-monitoring-alerts.sh` exists; routing unconfirmed for this programme |
| 14 | Backup | **ACTION** | Procedure documented; not exercised against 706 MB |
| 15 | Restore | **ACTION** | Not rehearsed |
| 16 | Health endpoint | **READY** | Exposed |
| 17 | Rollback | **ACTION** | App Service slot swap available; migration rollback path not rehearsed |
| 18 | Section 508 | **READY** | Automated checks pass; VPAT/ACR per deliverable is a separate contract obligation |

---

## PDF generation on Azure Linux — the specific answer

Phase 9 recorded PDF as "container-only". Precisely:

**Why it fails on the workstation.** WeasyPrint is installed, but its rendering
stack is native, not Python. The import raises
`cannot load library 'libgobject-2.0-0'`. WeasyPrint requires the
Pango / Cairo / GObject stack, which on Windows means a separate GTK3 runtime.

**What Azure Linux requires.** The container image must provide, at minimum:

```
libpango-1.0-0      libpangoft2-1.0-0    libcairo2
libgdk-pixbuf-2.0-0 libffi              shared-mime-info
fonts-dejavu-core   (or another font package — no fonts, no glyphs)
```

On a Debian/Ubuntu base these come from `libpango-1.0-0 libpangoft2-1.0-0
libcairo2 libgdk-pixbuf2.0-0 shared-mime-info fonts-dejavu-core`.

**Actions before relying on PDF:**

1. Confirm the deployed image actually installs them — `docs/DEPLOYMENT_GUIDE.md`
   should be checked against the running image, not assumed.
2. Add a startup assertion that imports WeasyPrint and renders a one-page
   document, so a missing library fails at deploy time rather than when a COR
   deliverable is requested.
3. Include at least one font package. A PDF that renders with no glyphs passes a
   naive smoke test and is unusable.
4. If PDF is the format of record (**D9**), pin generation to the container and
   never to a developer workstation.

**Deployment recommendation: NOT YET.** Items 8–11, 13–15 and 17 are open, and
PDF must be proven in the image first.
