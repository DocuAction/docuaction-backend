# Data Flow (Section 2B)

Every connection with direction, protocol, auth, sensitivity, and encryption-in-transit. (Mermaid version in `diagrams/data_flow.md`.)

```
  Browser (User)
       │  HTTPS · JWT bearer · [PII/PHI/CUI in/out] · TLS ✅
       ▼
  Azure Static Web App (Frontend, static export)         [PUBLIC assets; no server-side secrets]
       │  HTTPS · JWT bearer · TLS ✅
       ▼
  Azure App Service (Backend FastAPI)
       │
       ├──▶ Azure PostgreSQL (docuaction-db-geo)   IN/OUT · SSL · connection-string · [ALL classes] · TLS ✅
       ├──▶ Azure Key Vault (private endpoint)      OUT  · Managed Identity · [SECRETS] · TLS ✅ (private)
       ├──▶ App Insights / Log Analytics            OUT  · instrumentation key/MI · [telemetry, may include PII] · TLS ✅
       ├──▶ Anthropic Claude API                    OUT  · API key · [Documents/entities/claims — POTENTIAL PHI] · TLS ✅
       ├──▶ OpenAI Whisper / chat                   OUT  · API key · [Audio — POTENTIAL PHI] · TLS ✅
       ├──▶ SendGrid                                OUT  · API key · [PII: emails+content] · TLS ✅
       ├──▶ NPPES (npiregistry.cms.hhs.gov)         OUT  · keyless · [PHI: NPI] · TLS ✅
       ├──▶ OIG LEIE (oig.hhs.gov)                   OUT  · keyless · [PHI: NPI/name] · TLS ✅
       ├──▶ PECOS / CMS data (data.cms.gov)          OUT  · keyless · [PHI: provider] · TLS ✅
       ├──▶ SAM.gov (api.sam.gov)                    OUT  · API key · [CONFIDENTIAL: entity/UEI] · TLS ✅
       ├──▶ RCE/Sequoia FHIR (rce.sequoiaproject.org)OUT  · API key · [CUI/PHI: FHIR] · TLS ✅
       ├──▶ ONC Box (box.com)                        IN   · OAuth2 · [CUI/PHI: FHIR bundles] · TLS ✅
       ├──▶ News APIs (GDELT/NewsAPI.ai/.org/eventregistry) OUT · API key/keyless · [PUBLIC: queries] · TLS ✅
       └──▶ Local disk /home/site/wwwroot/uploads    IN   · filesystem · [PHI-capable uploads] · at-rest (Azure) ✅ · ⚠ not Blob, not shared
```

## Sensitivity legend
PUBLIC · INTERNAL · CONFIDENTIAL · PII · FINANCIAL · CUI · **PHI** · AUTHENTICATION · SECRETS

## Flow-level findings
1. **All external hops are HTTPS/TLS** ✅ (in-transit encryption is consistent).
2. **PHI leaves the boundary to AI providers** (Anthropic, OpenAI) and to `.gov` verifiers (NPI) — the `.gov` flows are expected; **the AI flows need minimization + BAA** (Part 2J/10).
3. **Uploads land on local disk**, not Blob — durability + multi-instance + PHI-at-rest-governance concern.
4. **Key Vault is the only external hop over a private endpoint**; everything else is public-internet TLS.
5. **Telemetry to App Insights** may carry PII/PHI if requests/exceptions are logged verbatim — verify scrubbing (Part 8).

## Trust-zone view
- **Untrusted:** Browser, all external APIs (incl. AI), inbound ONC Box files.
- **Semi-trusted:** SWA (public static), Kudu SCM (AAD).
- **Trusted:** App Service process, PostgreSQL, Key Vault (private).
