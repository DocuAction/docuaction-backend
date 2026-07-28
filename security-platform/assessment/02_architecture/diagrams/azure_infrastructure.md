# Diagram 5 — Azure Infrastructure

```mermaid
graph TB
    subgraph "rg-docuaction-prod"
        subgraph "VNet docuaction-vnet"
            PE[Private Endpoint<br/>docuaction-kv-pe]
            PDNS[Private DNS<br/>privatelink.vaultcore]
        end
        ASP[App Service Plan<br/>P0v3 · capacity 1]
        APP[App Service<br/>Docuaction<br/>TLS1.2 · HTTPS-only · FtpsOnly]
        PG1[(PostgreSQL 16<br/>docuaction-db-geo<br/>geo-redundant · HA off)]
        PG2[(PostgreSQL<br/>docuaction-db<br/>⚠ legacy?)]
        KV[Key Vault<br/>docuaction-kv-prod]
        SWA[Static Web App<br/>docuaction-frontend]
        AI[App Insights<br/>docuaction-appinsights]
        LOG[Log Analytics<br/>docuaction-logs]
        AL[4 Metric Alerts +<br/>Action Group]
        CERT[TLS cert<br/>api-prod.docuaction.io]
    end
    subgraph "rg-docuaction-dev"
        DAPP[App Service docuaction-dev]
        DPG[(docuaction-db-dev)]
        DKV[Key Vault docuaction-kv-dev]
        DSWA[SWA docuaction-frontend-dev]
    end

    ASP --> APP
    APP -->|MI| PE --> KV
    PE --- PDNS
    APP -->|SSL| PG1
    APP --> AI --> LOG
    AI --> AL
    APP --- CERT
    SWA -. calls .-> APP

    DEF[Defender for Cloud — Standard:<br/>AppServices · SqlServers · KeyVaults ·<br/>StorageAccounts · OSS RDBMS · Containers] -.protects.-> APP & PG1 & KV

    classDef warn fill:#ffd,stroke:#a80;
    class PG2 warn;
```

**Strengths:** KV private endpoint, Defender Standard broad coverage, geo-redundant backups, monitoring+alerts. **Gaps:** capacity 1 / PG HA off (no HA), possible legacy `docuaction-db`, no WAF/Front Door.
