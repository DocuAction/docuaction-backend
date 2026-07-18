# DocuAction Backend — Environment Topology & Configuration Reference

**Product:** DocuAction AI / DocuAction TEFCA ARC
**Version:** 6.0.0
**Owner:** Alliance Global Tech, Inc.
**Audience:** Platform Operations / Security / Release Engineering
**Classification:** Internal — Operations
**Contacts:** security@agtbi.com · imran@agtbi.com

---

## 1. Purpose

This document defines the **dev/prod environment topology** for the DocuAction backend and
provides the **authoritative Environment Variables Reference** for the repository. Section 5 is
the single source of truth for backend configuration.

> **Note:** The committed `.env.example` in the repository is a **minimal legacy stub**. It is
> retained for historical/local-bootstrap convenience only. **This document supersedes it.**
> When the two disagree, this table governs.

---

## 2. Environment Isolation Principles

DocuAction operates **fully isolated dev and prod environments**. The isolation is
non-negotiable and forms part of the security control baseline:

- **Separate App Service apps** — dev and prod run as distinct Azure App Service (Linux)
  applications. No shared compute.
- **Separate PostgreSQL servers/databases** — each environment has its own Azure Database for
  PostgreSQL Flexible Server and database. Production data never resides on a dev server.
- **Separate Microsoft Entra ID settings** — distinct Entra app registrations / client
  credentials and redirect URIs per environment.
- **No shared secrets** — every secret (`SECRET_KEY`, database credentials, API keys, OAuth
  client secrets) is unique per environment. A secret is never copied between dev and prod.
- **Separate ALLOWED_HOSTS / ALLOWED_ORIGINS** — each app trusts only its own hostnames and
  frontend origin.

---

## 3. Topology Overview

| Attribute | Production | Development |
|-----------|-----------|-------------|
| Cloud subscription | `AGT-DocuAction` | `AGT-DocuAction` |
| Resource group | `rg-docuaction-prod` (East US 2 family) | dev resource group (isolated) |
| App Service (Linux) | **Docuaction** | separate dev App Service app |
| Default host | `docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net` | dev default host |
| Custom domain | `api-prod.docuaction.io` | dev custom domain (if configured) |
| PostgreSQL Flexible Server | **docuaction-db** (SSL required, 42-table schema) | separate dev server |
| Frontend origin (CORS) | `https://app.docuaction.io` | dev frontend origin |
| Identity | JWT + Microsoft Entra ID SSO (prod app registration) | JWT + Entra (dev app registration) |
| Security monitoring | Microsoft Defender for Cloud (Standard) | Defender for Cloud (Standard) |

---

## 4. Network, TLS & Trust Boundaries

```
                     Internet (HTTPS only)
                             │
              ┌──────────────┴───────────────┐
              │                              │
   app.docuaction.io (SWA)         api-prod.docuaction.io
   Azure Static Web Apps                    │  (managed TLS, SNI)
   "docuaction-frontend"                    ▼
              │                   ┌─────────────────────────┐
              │  CORS:            │  Azure App Service (Linux)│
              └── ALLOWED_ORIGINS │  app "Docuaction"        │
                 = app.docuaction │  TrustedHost middleware  │
                                  │  (ALLOWED_HOSTS gate)    │
                                  │  gunicorn + uvicorn      │
                                  └───────────┬─────────────┘
                                              │  TLS, sslmode=require
                                              ▼
                                  ┌─────────────────────────┐
                                  │ Azure DB for PostgreSQL  │
                                  │ Flexible Server          │
                                  │ "docuaction-db"          │
                                  │ 42-table schema          │
                                  └─────────────────────────┘

   Cross-cutting: Microsoft Defender for Cloud (Standard) monitors App Service,
   SQL/PostgreSQL, and Key Vault. Microsoft Entra ID provides SSO.

   ─────────────────  PROD (rg-docuaction-prod)  ─────────────────
   ══════ isolated, no shared compute/DB/secrets ══════
   ─────────────────  DEV (separate RG/app/DB)   ─────────────────
```

**Trust boundary notes:**

- **Ingress** — HTTPS-only. TLS terminates at App Service with a managed certificate bound to
  `api-prod.docuaction.io` (SNI). The default `*.azurewebsites.net` host is also TLS-secured.
- **Host trust** — TrustedHost middleware rejects any `Host` header not in `ALLOWED_HOSTS`
  with **HTTP 400** (including `/health`).
- **Origin trust** — CORS admits only `ALLOWED_ORIGINS` (`https://app.docuaction.io` in prod).
- **Database** — App Service to PostgreSQL requires TLS (`sslmode=require`). Credentials are
  environment-unique.
- **Secrets** — supplied as App Service application settings, ideally as Key Vault references.

---

## 5. Environment Variables Reference (Authoritative)

Every variable below must be set with an environment-appropriate value. **Placeholders shown
are illustrative only — never commit or paste a real secret.** Prefer Key Vault references for
all secret-bearing settings.

### 5.1 Core

| Variable | Purpose | Placeholder |
|----------|---------|-------------|
| `DATABASE_URL` | PostgreSQL connection string (SSL required). Required at boot. | `postgresql://<user>:<pw>@docuaction-db.postgres.database.azure.com:5432/<db>?sslmode=require` |
| `SECRET_KEY` | Application signing/crypto secret (JWT, sessions). Required at boot. | `<64-char-random-hex>` |
| `ENVIRONMENT` | Deployment tier selector. | `production` / `development` |
| `ALLOWED_HOSTS` | Comma-separated trusted `Host` values (TrustedHost). Missing entry ⇒ 400. | `docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net,api-prod.docuaction.io` |
| `ALLOWED_ORIGINS` (a.k.a. `CORS_ORIGINS`) | Comma-separated CORS-allowed origins. | `https://app.docuaction.io` |
| `ENABLE_DOCS` | Gates Swagger/OpenAPI UI exposure. | `false` (prod) |
| `ENABLE_SCHEDULER` | Gates APScheduler daily jobs. | `true` |
| `STORAGE_PROVIDER` | Selects the file/object storage backend. | `azure` / `local` |
| `UPLOAD_DIR` | Filesystem path for uploaded files. | `/home/site/uploads` |
| `DATA_RETENTION_DAYS` | Retention window for retained data/records. | `365` |
| `REQUIRE_ADMIN_APPROVAL` | Requires admin approval for new accounts. | `true` |
| `BLOCK_DISPOSABLE_EMAILS` | Rejects disposable/temporary email domains at signup. | `true` |

### 5.2 AI Providers

| Variable | Purpose | Placeholder |
|----------|---------|-------------|
| `AI_PROVIDER` | Selects the active AI provider. | `anthropic` |
| `ANTHROPIC_API_KEY` | Anthropic API credential. | `<anthropic-key>` |
| `ANTHROPIC_MODEL` | Default Anthropic model id. | `<model-id>` |
| `ANTHROPIC_SONNET_MODEL` | Secondary/Sonnet model id. | `<model-id>` |
| `OPENAI_API_KEY` | OpenAI API credential (optional provider). | `<openai-key>` |
| `GEMINI_API_KEY` | Google Gemini API credential (optional). | `<gemini-key>` |
| `PERPLEXITY_API_KEY` | Perplexity API credential (optional). | `<perplexity-key>` |
| `TAVILY_API_KEY` | Tavily search API credential (optional). | `<tavily-key>` |
| `WHISPER_MODEL` | Whisper transcription model id/size. | `<whisper-model>` |

### 5.3 Identity / SSO

| Variable | Purpose | Placeholder |
|----------|---------|-------------|
| `AZURE_AD_CLIENT_ID` | Entra ID app (client) ID for SSO. | `<guid>` |
| `AZURE_AD_CLIENT_SECRET` | Entra ID app client secret. | `<secret>` |
| `AZURE_AD_TENANT_ID` | Entra ID tenant (directory) ID. | `<guid>` |
| `AZURE_AD_DEFAULT_ROLE` | Default role assigned to SSO users. | `viewer` |
| `AZURE_AD_POST_LOGIN_REDIRECT` | Post-login redirect target. | `https://app.docuaction.io/auth/callback` |
| `MICROSOFT_CLIENT_ID` | Microsoft OAuth client ID (alt/legacy identity path). | `<guid>` |
| `MICROSOFT_CLIENT_SECRET` | Microsoft OAuth client secret. | `<secret>` |
| `MICROSOFT_TENANT_ID` | Microsoft OAuth tenant ID. | `<guid>` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID. | `<google-client-id>` |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret. | `<secret>` |
| `ZOOM_CLIENT_ID` | Zoom OAuth client ID (integration). | `<zoom-client-id>` |
| `ZOOM_CLIENT_SECRET` | Zoom OAuth client secret. | `<secret>` |

### 5.4 Email

| Variable | Purpose | Placeholder |
|----------|---------|-------------|
| `SENDGRID_API_KEY` | SendGrid API credential for transactional email. | `<sendgrid-key>` |
| `EMAIL_FROM` | Default From address. | `no-reply@docuaction.io` |
| `EMAIL_FROM_NAME` | Default From display name. | `DocuAction` |
| `MAIL_FROM` | Alternate/legacy From address key. | `no-reply@docuaction.io` |
| `MAIL_FROM_NAME` | Alternate/legacy From display name key. | `DocuAction` |

### 5.5 TEFCA / Connectors

| Variable | Purpose | Placeholder |
|----------|---------|-------------|
| `SAM_GOV_API_KEY` | SAM.gov entity/exclusion connector key (registered key required). | `<sam-key>` |
| `RCE_DIRECTORY_API_KEY` | RCE Directory connector API key. | `<rce-key>` |
| `IQVIA_ONEKEY_API_KEY` | IQVIA OneKey reference-data API key. | `<iqvia-key>` |
| `TEFCA_ALERT_FROM` | From address for TEFCA operational alerts. | `alerts@docuaction.io` |
| `TEFCA_ALERT_RECIPIENTS` | Comma-separated TEFCA alert recipients. | `security@agtbi.com` |
| `ENABLE_QA_MONITOR` | Enables the TEFCA QA monitor job. | `true` |
| `QA_MONITOR_INTERVAL_MIN` | QA monitor interval in minutes. | `60` |
| `QA_BASE_URL` | Base URL the QA monitor probes. | `https://api-prod.docuaction.io` |

### 5.6 Bulletin & External Data

| Variable | Purpose | Placeholder |
|----------|---------|-------------|
| `NEWSAPI_KEY` | NewsAPI credential (bulletin feeds). | `<newsapi-key>` |
| `NEWSAPI_AI_KEY` | NewsAPI.ai credential (bulletin feeds). | `<newsapi-ai-key>` |
| `CONGRESS_API_KEY` | Congress.gov data API key. | `<congress-key>` |
| `GOVINFO_API_KEY` | GovInfo data API key. | `<govinfo-key>` |
| `YOUTUBE_API_KEY` | YouTube Data API key. | `<youtube-key>` |
| `REDDIT_CLIENT_ID` | Reddit API client ID. | `<reddit-client-id>` |
| `REDDIT_CLIENT_SECRET` | Reddit API client secret. | `<secret>` |
| `BULLETIN_*` (group) | Feature-flag and configuration group gating the **Bulletin Intelligence module** — includes `BULLETIN_AUTH_ENABLED`, `BULLETIN_RATE_LIMIT_ENABLED`, `BULLETIN_AUDIT_ENABLED`, `BULLETIN_PUBLIC_BASE_URL`, `BULLETIN_SEND_FROM`, `BULLETIN_ALERT_EMAIL`, and related flags. Default to OFF unless the module is being activated. | flags: `false`/`true`; URLs/addresses as appropriate |

### 5.7 URLs

| Variable | Purpose | Placeholder |
|----------|---------|-------------|
| `APP_URL` | Primary application URL (frontend). | `https://app.docuaction.io` |
| `APP_BASE_URL` | Base URL used for building app links. | `https://app.docuaction.io` |
| `API_PUBLIC_URL` | Public base URL of the backend API. | `https://api-prod.docuaction.io` |
| `PUBLIC_BASE_URL` | Public base URL for generated/shared links. | `https://api-prod.docuaction.io` |

---

## 6. Configuration Governance

- Secret-bearing settings SHOULD be delivered via **Azure Key Vault references** so rotation
  is decoupled from redeployment.
- Any change to `ALLOWED_HOSTS`, `ALLOWED_ORIGINS`, `SECRET_KEY`, `DATABASE_URL`, or SSO
  settings is a **controlled change** requiring review and post-change `/health` verification.
- Dev and prod configuration sets are maintained separately. Never promote a secret value from
  dev to prod.

---

## 7. Change Record

| Field | Value |
|-------|-------|
| Document owner | Platform Operations, Alliance Global Tech, Inc. |
| Applies to | DocuAction backend v6.0.0 |
| Supersedes | `.env.example` (legacy stub) for configuration documentation |
| Review cadence | Each release or quarterly |
| Security contact | security@agtbi.com |
