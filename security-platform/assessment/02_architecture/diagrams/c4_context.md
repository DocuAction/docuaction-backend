# C4 Level 1 — System Context

```mermaid
graph TB
    Reviewer["TEFCA Reviewer / Analyst<br/>(ONC / RCE)"]
    Admin["Platform Admin"]
    ClientUser["Commercial user<br/>(GovCon/ATS — dormant)"]

    DA["DocuAction Platform<br/>Enterprise Intelligence + TEFCA"]

    ONC["ONC / RCE<br/>(FHIR directory, Box)"]
    GOV["Federal APIs<br/>NPPES/LEIE/PECOS/SAM"]
    AIV["AI Providers<br/>Anthropic / OpenAI"]
    MAIL["SendGrid"]
    NEWS["News APIs"]
    AZ["Azure Platform<br/>KV / PG / App Insights"]

    Reviewer -->|review, verify, import entities| DA
    Admin -->|manage users/config| DA
    ClientUser -.->|procurement/staffing| DA
    DA -->|verify identifiers| GOV
    DA -->|FHIR sync / file drop| ONC
    DA -->|classify/summarize/transcribe| AIV
    DA -->|email| MAIL
    DA -->|news aggregation| NEWS
    DA -->|secrets/data/telemetry| AZ
```
