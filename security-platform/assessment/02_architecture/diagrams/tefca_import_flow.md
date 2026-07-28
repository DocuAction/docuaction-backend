# Diagram 7 — TEFCA Entity Import Flow

```mermaid
flowchart TB
    START([FHIR Bundle / CSV upload]) --> AUTH{require_role reviewer}
    AUTH -->|403| DENY[Reject]
    AUTH -->|ok| SCAN[file_scanner<br/>magic bytes · dangerous content<br/>size · JSON/CSV structure · SHA-256]
    SCAN -->|fail| R422[422 generic reject + audit]
    SCAN -->|pass, checksum| PARSE[Parse Bundle/CSV]

    PARSE --> BATCH[Create tefca_import_batches<br/>status=processing]
    BATCH --> P1[PASS 1: per Organization<br/>detect level from meta.profile<br/>extract identifiers/address/purposes]
    P1 --> DUP{TEFCAID/HCID<br/>already exists?}
    DUP -->|yes| SKIP[skip · skipped_count++]
    DUP -->|no| SAVEPOINT[begin_nested savepoint]
    SAVEPOINT --> ENT[Insert entity -> flush -> identifiers -> endpoints -> audit entity_created]
    ENT -->|error| ERR[rollback savepoint · error_count++]
    ENT -->|ok| MAP[record fhir_id/tefcaid -> uuid]

    MAP --> P2[PASS 2: resolve partOf/ParentTEFCAID<br/>in-bundle map or DB lookup]
    P2 --> REL[Insert relationships<br/>belongs_to / sub_participant_of / member_of<br/>+ audit]
    REL --> VER[Insert initial version snapshot v1<br/>change_reason=initial_import]
    VER --> FIN[Finalize batch<br/>completed/partial/failed · counts · errors · duration]
    FIN --> DONE([Return batch summary])

    FIN -.later.-> VERIFY[POST /verify -> jobs/checks/findings]
    VERIFY -.-> REVIEW[Analyst review queue]

    classDef ok fill:#dfd,stroke:#080;
    class DONE ok;
```

**Design strengths:** two-pass (ordering-independent) · idempotent skip · per-entity savepoint fault isolation · full audit + versioning · reuses the platform upload scanner. *(ONC Box → download step is a planned source; current engine ingests uploaded Bundles/CSV.)*
