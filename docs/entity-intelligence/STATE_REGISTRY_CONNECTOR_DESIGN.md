# State business registry — connector design (DESIGN ONLY)

STATE_ADAPTERS_IMPLEMENTED = 0. No scraping, no jurisdiction, no purchase. Flag `STATE_REGISTRY_INTELLIGENCE_ENABLED = False` with no code behind it.

## Purpose

Corroborate LEGAL organisation identity (registered legal name, trade names, registration status, formation date, registered address) from a state business-registration source, as `source_authority = STATE_REGISTRY` observations.

## Contract (Core, `ports.py`)

```
StateRegistryCapability(jurisdiction, legal_name, trade_name, entity_identifier, entity_status,
                        formation_date, registered_address, source_reference, source_timestamp)
StateBusinessRegistryConnector.capability() -> StateRegistryCapability
StateBusinessRegistryConnector.observations_for(canonical_entity_id, legal_name, jurisdiction, provenance) -> [EvidenceObservation]
```

Every capability field is optional and defaults to False: no jurisdiction is assumed to publish any of them. A connector reports what it can, and the comparison engine treats absent fields as INSUFFICIENT evidence, never as a negative.

## Observation mapping (when a jurisdiction is implemented)

| Registry field | ObservationType | role |
|---|---|---|
| legal name | NAME | LEGAL_BUSINESS_NAME |
| trade / DBA name (only if the registry labels it so) | NAME | TRADE_NAME |
| entity identifier | IDENTIFIER | `<JURISDICTION>_ENTITY_ID` |
| registered / principal office address | LOCATION | REGISTERED_LOCATION |
| status, formation date | carried in observed_value; effective_from = formation date |

## Acquisition options to evaluate later (none chosen)

Official bulk data downloads where a state publishes them; official APIs with terms of use; an aggregator under licence. Screen-scraping is excluded. Each jurisdiction is a separate adapter with its own descriptor, capability, terms review and provenance (source reference URL/document, retrieval timestamp, dataset date).

## Not in scope

EIN/TIN verification (explicitly excluded by the program). Fifty-state coverage. Any implementation in this sprint.
