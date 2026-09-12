# State business registry — connector design (DESIGN ONLY)

STATE_ADAPTERS_IMPLEMENTED = 0. No scraping, no jurisdiction, no purchase. Flag `STATE_REGISTRY_INTELLIGENCE_ENABLED = False` with no code behind it. Data rights per jurisdiction; default `TRANSIENT_ONLY` until a jurisdiction's terms are reviewed.

## Purpose

Corroborate LEGAL organisation identity (registered legal name, trade names, registration status, formation date, registered address) from a state business-registration source, as `source_authority = STATE_REGISTRY` observations.

## Contract (Core, `ports.py`)

```
CapabilityAvailability = SUPPORTED | NOT_SUPPORTED | NOT_AVAILABLE | AUTH_REQUIRED | RATE_LIMITED | MANUAL_ONLY

StateRegistryCapability(jurisdiction,
    legal_name, trade_name, entity_identifier, entity_status, formation_date,
    registered_address, source_reference, source_timestamp,    # each a CapabilityAvailability
    acquisition)                                                # the connector as a whole

StateBusinessRegistryConnector.capability() -> StateRegistryCapability
StateBusinessRegistryConnector.observations_for(canonical_entity_id, legal_name, jurisdiction, provenance) -> [EvidenceObservation]
```

Every field defaults to NOT_SUPPORTED. A connector reports what it can obtain **and how**:

| Value | Meaning | Engine behaviour |
|---|---|---|
| SUPPORTED | published and obtainable by the connector under reviewed terms | observation produced |
| NOT_SUPPORTED | the jurisdiction does not publish it | INSUFFICIENT for that field, never negative |
| NOT_AVAILABLE | published but not obtainable now (outage, dataset not yet released) | SOURCE_UNAVAILABLE for that field |
| AUTH_REQUIRED | requires an account/credential the program has not approved | not consulted; recorded as not consulted |
| RATE_LIMITED | obtainable only within published limits | connector must budget; over-budget = NOT_AVAILABLE, never a silent skip |
| MANUAL_ONLY | exists only via a human lookup or counter request | not consulted by the system; surfaced as an open question for the analyst |

`supported_fields()` lists only SUPPORTED fields; tests cover defaults and MANUAL_ONLY.

## Observation mapping (when a jurisdiction is implemented)

| Registry field | ObservationType | role |
|---|---|---|
| legal name | NAME | LEGAL_BUSINESS_NAME |
| trade / DBA name (only if the registry labels it so) | NAME | TRADE_NAME |
| entity identifier | IDENTIFIER | `<JURISDICTION>_ENTITY_ID` |
| registered / principal office address | LOCATION | REGISTERED_LOCATION |
| status, formation date | carried in observed_value; effective_from = formation date |

A REGISTERED_LOCATION that differs from a delivered practice address is **not** a conflict by itself — a registered agent's address is routinely not a service location. The comparison engine already separates roles; the explanation must say which role matched or differed.

## Acquisition options to evaluate later (none chosen)

Official bulk data downloads where a state publishes them; official APIs with terms of use; an aggregator under licence. Screen-scraping is excluded. Each jurisdiction is a separate adapter with its own descriptor, capability, data-rights review and provenance (source reference URL/document, retrieval timestamp, dataset date).

## Not in scope

EIN/TIN verification (explicitly excluded by the program). Fifty-state coverage. Any implementation in this sprint.
