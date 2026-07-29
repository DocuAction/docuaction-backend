# Diagram 3 — Trust Boundaries

```mermaid
flowchart TB
    subgraph B1["① Untrusted — Internet"]
        U[Browser]
        EXT[External APIs / AI / ONC Box]
    end
    subgraph B2["② Semi-trusted — Azure edge"]
        SWA[Static Web App]
        SCM[Kudu SCM · AAD-only]
    end
    subgraph B3["③ Trusted — App tier"]
        API[FastAPI App Service]
    end
    subgraph B4["④ Trusted — Data/secret tier (private)"]
        PG[(PostgreSQL)]
        KV[Key Vault · private endpoint]
    end

    U -->|"TLS1.2 · CORS · TrustedHost · rate-limit · (no WAF)"| SWA
    SWA -->|"JWT validate · Pydantic · scanner"| API
    API -->|"parameterized SQL · SSL · (149 raw text() to audit)"| PG
    API -->|"Managed Identity"| KV
    API <-->|"HTTPS · API keys · (SSRF + PHI-to-AI risk)"| EXT
    SCM -.->|"AAD RBAC · (manual, ungated deploy)"| API

    classDef weak stroke:#a00,stroke-width:2px;
    class EXT weak;
```

**Boundary posture:** ⑤ App→Azure = Strong (MI + private KV) · ① edge = Good (needs WAF) · ③ DB = Moderate (raw SQL + credential) · ④ External = Moderate/Weak (PHI-to-AI, SSRF) · Management = Moderate (AAD-only but manual deploy).
