# Diagram 2 — Data Flow (with sensitivity labels)

```mermaid
flowchart TB
    U[Browser] -->|"HTTPS · JWT · PII/PHI/CUI"| FE[Static Web App]
    FE -->|"HTTPS · JWT bearer"| BE[FastAPI App Service]

    BE -->|"SSL · ALL classes"| PG[(PostgreSQL)]
    BE -->|"MI · SECRETS · private"| KV[Key Vault]
    BE -->|"key · telemetry(may incl PII)"| AI[App Insights]

    BE -->|"key · PHI-risk (docs/claims)"| CL[Anthropic Claude]
    BE -->|"key · PHI-risk (audio)"| WH[OpenAI Whisper]
    BE -->|"key · PII (emails)"| SG[SendGrid]
    BE -->|"keyless · PHI (NPI)"| NP[NPPES]
    BE -->|"keyless · PHI (NPI)"| LE[OIG LEIE]
    BE -->|"key · CONFIDENTIAL (UEI)"| SAM[SAM.gov]
    BE -->|"OAuth2 · CUI/PHI (FHIR)"| BOX[ONC Box]
    BE -->|"key · CUI (FHIR)"| RCE[RCE-Sequoia]
    BE -->|"file · PHI-capable"| DISK[/Local disk uploads/]

    classDef phi fill:#fdd,stroke:#a00;
    classDef sec fill:#ffd,stroke:#aa0;
    class CL,WH,NP,LE,BOX,RCE,DISK phi;
    class KV,PG sec;
```

**Legend:** red = PHI/PHI-capable path · yellow = secrets/all-data. All hops TLS-encrypted. **AI paths (Claude/Whisper) carry unminimized PHI risk — top data-flow finding.**
