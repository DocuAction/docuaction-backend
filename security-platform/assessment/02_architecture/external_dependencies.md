# External Dependency Risk Matrix (Section 2G)

Verified against source (`grep` of hosts + client code). "Business Critical" = to the module that uses it, not the whole platform.

| Integration | Used by | Business Critical | Security Risk | Data Sent | Auth | Fallback | Timeout/Retry |
|---|---|:--:|:--:|---|---|---|---|
| **Anthropic Claude** (Sonnet-4, Haiku-4.5) | Bulletin, Case Mgmt, Migration, Documents, Meetings, Security, Decisions | High | **Medium** | Documents, entity/claim text, prompts (**potential PHI**) | API key | some paths → gpt-4o-mini | httpx timeouts (20–180s); tenacity in connectors |
| **OpenAI** (Whisper-1, gpt-4o-mini) | Audio/Meetings, one AI path | Medium | **Medium** | **Audio files (potential PHI)**, chat text | API key | None (transcription has no fallback) | timeouts set |
| **SendGrid** | Auth (invites/reset), admin, bulletin, QA alerts | High | Low | Email addresses + content | API key | None observed (no SMTP fallback) | timeouts set |
| **NPPES** (npiregistry.cms.hhs.gov) | TEFCA verification | Medium | Low | NPI numbers | Keyless | `tefca_source_cache` | tenacity retry |
| **OIG LEIE** (oig.hhs.gov/exclusions) | TEFCA verification | Medium | Low | NPI/name (CSV download) | Keyless | cache | retry |
| **PECOS / CMS data** (data.cms.gov) | TEFCA verification | Medium | Low | provider queries | Keyless | cache | retry |
| **SAM.gov** (api.sam.gov) | TEFCA verify + GovCon opportunities | Medium | **Medium** | entity/UEI queries | **API key (registered)** | cache | retry |
| **RCE / Sequoia** (rce.sequoiaproject.org/fhir) | TEFCA registry directory + identifier system | Medium | Medium | FHIR queries | API key (per config) | manual/import | — |
| **News APIs** (GDELT, NewsAPI.ai, NewsAPI.org, eventregistry/Perigon) | Bulletin Intelligence | High (to bulletin) | Medium | search queries | API keys / keyless (GDELT) | multi-source failover (GDELT ↔ NewsAPI ↔ eventregistry) | timeouts |
| **ONC Box** (box.com) | TEFCA (FHIR file drop — referenced, 3 files) | Medium | Medium | FHIR bundles | OAuth2 (per data-source config) | manual upload / CSV | — |
| **Azure Key Vault** | Secrets delivery | **Critical** | Low | Secret reads | **Managed Identity** (private endpoint) | app-setting env fallback | Azure SDK |
| **Azure PostgreSQL** | All persistence | **Critical** | Low | All data (SSL) | connection string | None | pool (5+10, pre-ping) |

> Note: **no Azure Blob Storage** integration (uploads write to local disk `/home/site/wwwroot/uploads`) — a durability/scale concern, not a dependency.

## 24-hour outage impact per dependency

| Dependency down 24h | Effect |
|---|---|
| **Azure PostgreSQL** | **Total outage** — app cannot serve. No fallback. (Geo-redundant *backups* aid recovery, not availability.) |
| **Azure Key Vault** | Secrets resolve at startup; running instance may survive, but restarts fail. **High** risk on any restart/deploy. |
| **Anthropic** | AI features (classification, extraction, briefings, case engines) degrade/fail; core CRUD + TEFCA read/verify (internal checks) unaffected. Partial fallback to OpenAI on some paths. |
| **OpenAI** | Audio transcription unavailable (no fallback); minor chat path. |
| **SendGrid** | No emails (invites, password resets, alerts) — **blocks new-user onboarding & password recovery**. No SMTP fallback. |
| **NPPES/LEIE/PECOS/SAM** | TEFCA **external** verification degraded (already gated off for synthetic data); **internal** identity+hierarchy checks unaffected; cache mitigates. Registry browse/import unaffected. |
| **RCE/Sequoia / ONC Box** | TEFCA directory sync + FHIR file ingestion pause; manual/CSV import still works. |
| **News APIs** | Bulletin briefings stale; multi-source failover reduces impact; no effect on other modules. |

## Concentration & single-points
- **Two hard single-points with no fallback:** PostgreSQL and Key Vault (both Azure-managed, 99.99% SLA — acceptable, but no cross-region failover configured).
- **SendGrid has no fallback** → password-reset/onboarding availability depends on one vendor. *Recommendation (documented): add an SMTP fallback or a second provider.*
- **Whisper has no fallback** for audio.
- **AI (Anthropic)** is broadly depended-on but non-blocking for the federal TEFCA read/verify core.
