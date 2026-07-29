# Diagram 4 — Module Dependency Graph

Which modules depend on which shared services.

```mermaid
graph LR
    subgraph "Shared services (core)"
        AUTH[core.security<br/>RBAC/JWT]
        DBX[core.database<br/>2 engines/Bases]
        AUD[Audit logs]
        AIENG[AI clients<br/>Claude/Whisper]
        SCAN[file_scanner]
        MAIL[core.email]
    end

    subgraph "Federal stack (deployed)"
        REG[TEFCA Registry]
        ARC[TEFCA ARC legacy]
        PLAT[Platform Config]
        ADM[Admin/Users]
    end
    subgraph "Medium modules"
        BUL[Bulletin]
        HC[Healthcare Claims]
        CM[Case Mgmt]
        DOC[Documents]
        AUDIO[Audio/Meetings]
        MIG[Migration]
    end
    subgraph "Dormant commercial (undeployed)"
        GOV[GovCon/ERP]
        ATS[ATS/Staffing]
    end

    REG --> AUTH & DBX & AUD
    ARC --> AUTH & DBX & AUD & AIENG
    PLAT --> DBX
    ADM --> AUTH & DBX & AUD & MAIL
    BUL --> DBX & AIENG & MAIL
    HC --> AUTH & DBX & AIENG
    CM --> AUTH & DBX & AIENG
    DOC --> AUTH & DBX & AIENG & SCAN
    AUDIO --> AUTH & DBX & AIENG
    MIG --> AUTH & DBX & SCAN
    REG --> SCAN
    GOV -.->|"no auth"| DBX
    ATS -.->|"partial auth"| DBX

    classDef dormant stroke-dasharray: 5 5,stroke:#a00;
    class GOV,ATS dormant;
```

**Observations:** `core.database` (two engines/Bases) and `core.security` are the universal shared services. The **dormant commercial modules depend on the DB but bypass auth** (dashed red). AI clients are a widely-shared dependency (Part 2J).
