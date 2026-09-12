# IQVIA OneKey — adapter contract (RCE-provided delivery)

STATUS = AWAITING_SCHEMA. IQVIA_API_CALLED = NO. IQVIA_CREDENTIAL_REQUIRED = NO. IQVIA_SCHEMA_INVENTED = NO.

## Program context

ONC has indicated that an IQVIA-related data file is expected from the RCE/vendor. The anticipated source is therefore an **RCE-provided IQVIA / OneKey evidence delivery**, not an IQVIA API. AGT does not purchase, license or query OneKey for this capability. The pre-existing `app/Tefca/connectors.py::IQVIAOneKeyConnector` (a keyed API client "pending federal contract ODC") is a different, older path and is not used by this adapter.

## Public research (capability vocabulary only)

From IQVIA's public OneKey Reference Data material (fact sheet, 2025; iqvia.com OneKey pages): a persistent OneKey ID assigned to every HCP and HCO; coverage figures (746,211 HCOs, 11.3M HCPs, 6.2M HCP-to-HCO affiliations, 26,557 corporate parents, 1,116 IDNs); attributes described as names, addresses, organisation classifications/types, affiliations and corporate hierarchies, other identifiers; delivery "with APIs, portals, and integrated data services". **These are potential capabilities. None is an assumed RCE-delivered column.**

## Adapter responsibilities

Implemented now (`app/evidence_sources/iqvia_onekey/adapter.py`):
1. `preserve(bytes)` — SHA-256 and size of the file exactly as received.
2. `inventory(text)` — schema fingerprint (hash of the header), field list, record count, sample values.
3. `propose_mapping(inventory)` — an **empty** proposal: every delivered field listed, no concept assigned. Automatic mapping is deliberately not implemented.
4. `observations_for(...)` — gated by `ENTITY_INTELLIGENCE_ENABLED` + `IQVIA_EVIDENCE_ENABLED`; raises `SchemaUnknown` until a human-approved mapping exists, and still raises after that because observation production is not implemented until the real layout is known.

Later (after the RCE file arrives and a mapping is approved by a person):
- map approved fields → `EvidenceObservation`s with `source_authority = COMMERCIAL_REFERENCE`;
- preserve the OneKey identifier as an IDENTIFIER observation with role `ONEKEY_ID` — never as DocuAction's canonical entity id;
- names → NAME with kinds the delivered layout supports (legal / DBA / other) — only if the layout states the kind;
- addresses → LOCATION with role `UNKNOWN` unless the layout states a role;
- affiliations → RELATIONSHIP with IQVIA-specific kind codes (e.g. `CORPORATE_PARENT_HCO`), which the comparison engine never equates with program relationships (QHIN→Participant).

## Provenance

| Field | Value |
|---|---|
| source_owner | IQVIA OneKey |
| delivery_path | THIRD_PARTY_DELIVERY (RCE-provided) |
| received_by | AGT / DocuAction |
| source_version | RCE delivery reference + file hash; IQVIA's own dataset date if stated |

This is distinct from "DocuAction queried IQVIA" and the two are never blurred.

## Stop rule

When the actual RCE file arrives: preserve → inventory → schema/mapping proposal for human review. No automatic mapping. No observation production until approval.
