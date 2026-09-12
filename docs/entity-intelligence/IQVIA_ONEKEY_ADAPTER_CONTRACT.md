# IQVIA OneKey — adapter contract (RCE-provided delivery)

STATUS = AWAITING_SCHEMA. IQVIA_API_CALLED = NO. IQVIA_CREDENTIAL_REQUIRED = NO. IQVIA_SCHEMA_INVENTED = NO. Data rights: COMMERCIAL_LICENSED / AWAITING_DELIVERY_TERMS / TRANSIENT_ONLY. Source authority when delivered: `RCE_PROVIDED_THIRD_PARTY` (program to confirm; see checklist Q28).

## Program context

ONC has indicated that an IQVIA-related data file is expected from the RCE/vendor. The anticipated source is therefore an **RCE-provided IQVIA / OneKey evidence delivery**, not an IQVIA API. AGT does not purchase, license or query OneKey for this capability. The pre-existing `app/Tefca/connectors.py::IQVIAOneKeyConnector` (a keyed API client "pending federal contract ODC") is a different, older path and is not used by this adapter.

## Public research (capability vocabulary only)

From IQVIA's public OneKey Reference Data material (fact sheet, 2025; iqvia.com OneKey pages): a persistent OneKey ID assigned to every HCP and HCO; coverage figures (746,211 HCOs, 11.3M HCPs, 6.2M HCP-to-HCO affiliations, 26,557 corporate parents, 1,116 IDNs); attributes described as names, addresses, organisation classifications/types, affiliations and corporate hierarchies, other identifiers; delivery "with APIs, portals, and integrated data services". **These are potential capabilities. None is an assumed RCE-delivered column.**

## Public research re-verified 2026-09-12

IQVIA's public OneKey page (iqvia.com, read 2026-09-12) describes: "unique identifiers" linking to other IQVIA datasets; HCP and HCO reference data with a "best address" capability; "Integrated Delivery Networks (IDNs), hospitals, clinics, group purchasing organizations (GPOs)"; "B2B and B2P affiliations"; "physician employment indicators"; "over 1,000 attributes"; "More than 1.5M updates are made globally each month"; delivery via "a variety of delivery options" including API and portal. Nothing on the page describes a file layout. `POTENTIAL_CONCEPTS` remains a vocabulary, not a schema.

## Receiving pipeline (tested with generic synthetic headers only)

```
RECEIVE → PRESERVE → HASH → PROVENANCE → FINGERPRINT → INVENTORY → PROFILE → UNKNOWN-FIELD REPORT → PROPOSE → HUMAN REVIEW → APPROVED → OBSERVATIONS
```

| Stage | Implemented | Notes |
|---|---|---|
| PRESERVE / HASH | `preserve(bytes)` | SHA-256 + size of the bytes as received |
| FINGERPRINT / INVENTORY | `inventory(text)` | header hash, field list, record count, ≤ 3 sample values per field |
| PROFILE | `profile(text)` | per-column fill rate, capped distinct count, max length, all-numeric flag; no values echoed |
| UNKNOWN-FIELD REPORT | `unknown_field_report(inventory, approved_mapping)` | every field without an approved concept; with no mapping, every field — the truthful state |
| PROPOSE | `propose_mapping(inventory)` | empty by design |
| HUMAN REVIEW / APPROVED | outside the system | recorded mapping becomes `approved_mapping` |
| OBSERVATIONS | `observations_for` | refuses (`SchemaUnknown`) until approved; still refuses after, until the real layout is implemented |

All stages are behind `ENTITY_INTELLIGENCE_ENABLED` + `IQVIA_EVIDENCE_ENABLED` where they touch observations; preserve/inventory/profile are pure functions usable for the intake review.

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
