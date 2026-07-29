# Diagram 1 — High-Level System Architecture

```mermaid
graph TB
    subgraph Internet
        User[Browser / Reviewer]
    end
    subgraph "Azure — rg-docuaction-prod"
        SWA[Static Web App<br/>docuaction-frontend<br/>app.docuaction.io]
        API[App Service — FastAPI<br/>Docuaction P0v3<br/>api-prod.docuaction.io]
        DB[(PostgreSQL 16<br/>docuaction-db-geo<br/>geo-redundant)]
        KV[Key Vault<br/>docuaction-kv-prod<br/>private endpoint]
        AI2[App Insights +<br/>Log Analytics + Alerts]
    end
    subgraph "External APIs"
        LLM[Anthropic Claude<br/>OpenAI Whisper]
        GOV[NPPES / LEIE /<br/>PECOS / SAM.gov]
        RCE[RCE-Sequoia FHIR /<br/>ONC Box]
        NEWS[GDELT / NewsAPI /<br/>eventregistry]
        MAIL[SendGrid]
    end

    User -->|HTTPS| SWA
    SWA -->|HTTPS + JWT| API
    API -->|SSL| DB
    API -->|Managed Identity| KV
    API --> AI2
    API -->|API key, PHI risk| LLM
    API -->|keyless/key| GOV
    API -->|OAuth2/key| RCE
    API -->|key| NEWS
    API -->|key| MAIL

    classDef crit fill:#fde,stroke:#a00;
    classDef ext fill:#eef,stroke:#06c;
    class DB,KV crit;
    class LLM,GOV,RCE,NEWS,MAIL ext;
```

**Notes:** single App Service instance (no HA); Key Vault is the only privately-networked dependency; PHI can flow to the LLM providers (compliance item).
