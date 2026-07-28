# C4 Level 4 — Component Detail: TEFCA Registry

```mermaid
graph LR
    subgraph "TEFCA Registry module (app/tefca_registry)"
        ROUTES["routes.py<br/>19 endpoints<br/>router-gate require_role(reviewer)"]
        QUERIES["queries.py<br/>reads: list/detail/qhins/<br/>hierarchy/search/stats"]
        VERIF["verification.py<br/>internal identity+hierarchy checks<br/>(deterministic; external gated off)"]
        FHIR["fhir_import.py<br/>two-pass Bundle importer<br/>+ shared persist_import"]
        CSV["csv_import.py<br/>CSV importer -> persist_import"]
        SCHEMAS["schemas.py<br/>Pydantic"]
        MODELS["models.py<br/>10 tables"]
    end
    SCAN["file_scanner (shared)"]
    SEC["core.security require_role"]
    DB[(PostgreSQL<br/>tefca_reg_*/tefca_entity_*/<br/>tefca_verification_*/import_batches)]

    ROUTES --> SEC
    ROUTES --> QUERIES --> MODELS --> DB
    ROUTES --> VERIF --> MODELS
    ROUTES --> FHIR --> MODELS
    ROUTES --> CSV --> FHIR
    ROUTES -->|import upload| SCAN
    ROUTES --> SCHEMAS

    classDef good fill:#dfd,stroke:#080;
    class ROUTES,QUERIES,VERIF,FHIR,CSV,MODELS good;
```

**Data path — Reads:** Routes → Queries → Models → DB.
**Data path — Verify:** Routes → Verification (deterministic checks) → Models (jobs/checks/findings/audit) → DB.
**Data path — Import:** Routes → Scanner → FHIR/CSV Import → shared `persist_import` → Models (entities/identifiers/relationships/versions/audit/batch) → DB.

This module is the **reference implementation** for the codebase: isolated, RBAC-gated, indexed, audited, idempotent, fault-isolated.
