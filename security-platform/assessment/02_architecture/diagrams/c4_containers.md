# C4 Level 2 — Containers

```mermaid
graph TB
    subgraph "DocuAction Platform"
        FE["Frontend SPA<br/>Next.js 16 static export<br/>Azure Static Web Apps"]
        API["Backend API<br/>FastAPI (async) on<br/>Azure App Service (Linux, py3.12, gunicorn)"]
        DB[("Database<br/>PostgreSQL 16<br/>Flexible Server")]
        KV["Secrets<br/>Azure Key Vault (private endpoint)"]
        SCH["Scheduler<br/>APScheduler in-process"]
        DISK["Uploads<br/>local disk (⚠ not Blob)"]
        OBS["Observability<br/>App Insights + Log Analytics"]
    end
    EXT["External services<br/>Claude/Whisper/gov/SendGrid/news/Box"]

    FE -->|HTTPS + JWT| API
    API -->|SSL / SQLAlchemy async| DB
    API -->|Managed Identity| KV
    API --> DISK
    API --> OBS
    API --> EXT
    SCH --> API
    SCH --> EXT
```

**Note:** two logical data models coexist inside the one API container (federal Base — deployed; commercial Base — dormant). Scheduler and uploads are in-process/local (single-instance constraints).
