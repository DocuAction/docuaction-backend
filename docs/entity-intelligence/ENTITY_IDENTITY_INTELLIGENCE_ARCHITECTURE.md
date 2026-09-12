# Entity Identity & Location Intelligence — Architecture (isolated foundation)

Status: DEV engineering foundation. Feature OFF. Not deployed to the shared QA environment. Not Task 7; a reusable evidence-intelligence capability that may later support Tasks 2–5.

## The question it answers

Not "is this address valid?" but: *is this the organisation represented in the delivered data, operating under the represented name, at the represented location, with relationships and independent evidence that explain or challenge the delivered information — and what changed since the prior review?*

## Where the engine stops

```
SOURCE DATA (program delivery: the SUBJECT)
   ↓
INDEPENDENT EVIDENCE (NPPES V2 today; RCE-provided IQVIA later; state registries by design)
   ↓
SYSTEM EVIDENCE ASSESSMENT (this capability)   ← STOPS HERE
   ↓
ANALYST REVIEW → ANALYST DETERMINATION → INDEPENDENT QA → CONTRACTUAL CLASSIFICATION → REPORT
```

The engine never emits COMPLIANT / NON_COMPLIANT / APPROVED / REJECTED / PASS / FAIL / VERDICT. `assessment.py` asserts that at import time and tests assert it on payloads.

## Placement in the locked architecture

| Layer | What lives there | Package |
|---|---|---|
| CORE | observation model, normalisation, comparison rules, historical delta, system evidence assessment, ports, isolated persistence, feature gates | `app/core/entity_intelligence/` |
| Source adapters (program-agnostic, source-specific) | NPPES V2 (implemented, file-based), IQVIA OneKey (awaiting schema), state registry (interface only) | `app/evidence_sources/` |
| PROGRAM CONFIGURATION / TEFCA module | relationship kinds (QHIN→Participant, Participant→Subparticipant), applicability decisions, when to run, what the workbench shows | not built in this sprint |

Core imports nothing from `app/Tefca`, `app/tefca_registry` or the adapters. Adapters import Core. A test walks `app/` and fails if anything outside these two packages (other than the settings declaration) references them.

## Existing components reused vs new

| Concern | EXISTING_REUSABLE_COMPONENT | EXTEND_EXISTING | NEW_COMPONENT_REQUIRED |
|---|---|---|---|
| Source version / hashing / provenance | `app.core.evidence_provenance` (`SourceVersionRef`, `observation_hash`, `file_sha256`) — reused as-is | no | `Provenance` wrapper adds source owner, delivery path, received-by |
| Ingestion contracts | `app.core.ingestion.contracts` (`SourceDescriptor`, `AcquisitionResult`, `ParsedBatch`) — the future NPPES *acquisition* step will register through it | later | not now (adapter is file-in, observations-out) |
| Canonical entity + identifiers + relationships | `tefca_registry.models` (`TefcaRegEntity`, `TefcaEntityIdentifier`, `TefcaEntityRelationship`, `TefcaEntityVersion`) — the program owns these; observations reference `canonical_entity_id` only | no (shared schema frozen) | none |
| Delivered source record | `RceSourceRecord`, `TefcaEntityContact` — future TEFCA adapter maps them to PROGRAM_DELIVERY observations | no | none |
| Evidence store | `TEFCADimensionEvidence` is the program's Layer-1 store (dimension → disposition). Identity observations need role, effective dates and multi-valued names/locations it does not model | no (frozen) | `ei_evidence_observations` etc. on a separate base |
| Address/name normalisation | `app.Tefca.address_comparison` is program-owned; Core cannot import it | no | `core/entity_intelligence/normalize.py` (same rules, Core-owned) |
| Applicability | `app.Tefca.source_applicability` (program); `evidence_dimensions.Applicability` | no | `EvidenceApplicability` enum in Core; program decides values |
| Feature flags | `Settings` (env-backed pydantic) — reused; `PlatformFeature` DB rows exist but would require shared-DB writes | yes: five boolean fields, default False | no library |
| Connectors | `app.Tefca.connectors.NPPESConnector` (live API, per-NPI) and `IQVIAOneKeyConnector` (keyed API stub) — different purpose; not touched | no | bulk-file adapter (`evidence_sources/nppes_v2`) |
| Migrations | Alembic chain (head `20260903_delivery_grants`) — untouched | no | pending script outside `alembic/versions`, separate declarative base |

PARALLEL_ARCHITECTURE_CREATED = only where the frozen shared schema could not be extended (observation store) and where Core could not import program code (normalisation). Both are documented as deliberate.

## Five identity dimensions

| Dimension | Delivered (subject) | Evidence | Result |
|---|---|---|---|
| A Organisation identity | delivered NPI | NPPES NPI record (Type 2) | `IdentifierSignal` |
| B Name identity | delivered name | NPPES legal name, Other Names by type code | `NameSignal` |
| C Location identity | delivered address | NPPES primary, additional (reference file), mailing | `LocationSignal` |
| D Relationship identity | delivered program relationship (kind code) | same-kind observations only | `RelationshipSignal` |
| E Historical identity | prior vs current observations | comparison signals | `HistoricalDelta` + `VariationSignal` |

## Feature isolation

`Settings`: `ENTITY_INTELLIGENCE_ENABLED` (master), `NPPES_IDENTITY_CORROBORATION_ENABLED`, `IQVIA_EVIDENCE_ENABLED`, `GOOGLE_ADDRESS_INTELLIGENCE_ENABLED`, `STATE_REGISTRY_INTELLIGENCE_ENABLED` — all False. Gates are enforced at `EntityIntelligenceService.evaluate` (service boundary) and every adapter's `observations_for` (connector boundary). No route or job exists to gate. Flags are read at call time.

Feature OFF means: no external call (no network code exists), no processing (service raises `FeatureDisabled`), no job, no evidence, no assessment, no API change (OpenAPI byte-identical), no report change, no UI change.

## Persistence isolation

`EntityIntelligenceBase` is a separate `DeclarativeBase`. The app's startup `create_all()` and Alembic `target_metadata` bind to `app.core.database.Base`, so the five `ei_*` tables cannot be created on the shared QA database by deployment. Local/CI creation: `python -m app.core.entity_intelligence.migrations.apply --database-url sqlite:///ei_local.db` (refuses non-local URLs).

## Future UI (documented only)

An entity 360 view: ONC/RCE delivered values · independent evidence per source · system observations · prior review · what changed · analyst determination · QA · audit history. Not built.
