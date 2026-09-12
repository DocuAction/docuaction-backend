# Evidence source data-rights model

Every evidence source carries a `DataRights` descriptor (`app/core/entity_intelligence/ports.py`) and every observation carries a `value_handling` mode (`observations.py`). The defaults are the most restrictive values; a right exists only when a reviewed term is recorded in `basis`. This model records capabilities and obligations; it is not legal advice and each `basis` must be confirmed by the program before a source is enabled.

## Classes

| Class | Meaning |
|---|---|
| PUBLIC | Published for unrestricted download by its owner (e.g. CMS NPPES dissemination) |
| GOVERNMENT_PROVIDED | Delivered by ONC/RCE under the contract; handled under the contract's data terms |
| COMMERCIAL_LICENSED | Proprietary data under licence (e.g. IQVIA OneKey) |
| RESTRICTED | Terms restrict storage/display/derivation (e.g. Google Address Validation content) |
| TRANSIENT_PROCESSING_ONLY | May be processed in memory for a comparison; nothing persisted, nothing displayed |

## Fields

`storage_allowed` · `raw_storage_allowed` · `display_allowed` · `redistribution_allowed` · `external_call_allowed` · `credential_required` · `attribution_required` · `retention_rule` · `value_handling` · `status` (DOCUMENTED / NOT_YET_APPROVED / AWAITING_DELIVERY_TERMS) · `basis`.

## Value handling modes (per observation)

| Mode | Persisted | Displayed | Use |
|---|---|---|---|
| RAW_PERMITTED | value | value | public sources |
| HASHED_REFERENCE_ONLY | SHA-256 of the value + kind | hash | licensed sources where only "a matching value exists" may be kept |
| TRANSIENT_ONLY | nothing (`{"withheld": "TRANSIENT_ONLY"}`) | nothing | terms unknown or storage prohibited |
| RESTRICTED_DISPLAY | value | only to authorised roles (consumer enforces) | licensed sources with display limits |

`EvidenceObservation.to_dict()` applies the mode, so a serialised observation can never leak a value its source forbids. Tested.

## Current register

| Source | Class | Status | storage | raw | display | redistribute | ext call | cred | attribution | retention | handling | Basis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| NPPES V2 (CMS) | PUBLIC | DOCUMENTED | yes | yes | yes | yes | no (file intake) | no | no | retain each preserved edition | RAW_PERMITTED | CMS NPPES Data Dissemination public download; Federal Register CMS-6060-N (May 30, 2007) cited in readme v.2 as the FOIA-disclosure basis |
| Program delivery (ONC/RCE) | GOVERNMENT_PROVIDED | DOCUMENTED (contract) | yes | yes | yes (authorised roles) | no | n/a | n/a | n/a | per contract | RAW_PERMITTED | existing platform handling; unchanged by this capability |
| IQVIA OneKey (RCE-provided) | COMMERCIAL_LICENSED | **AWAITING_DELIVERY_TERMS** | no | no | no | no | no | no | unknown | unknown | TRANSIENT_ONLY | no terms received; see RCE_IQVIA_DATA_INTAKE_CHECKLIST.md |
| Google Address Validation | RESTRICTED | **NOT_YET_APPROVED** | Place ID only (if approved) | no | attribution rules | no | would be yes | yes (API key) | yes | ≤ policy term | TRANSIENT_ONLY | Google "Policies and attributions for Address Validation API" (updated 2026-09-10): "Content pre-fetching, caching, or storage is generally restricted"; Place ID exempt |
| State business registries | PUBLIC or per-state terms | DESIGN_ONLY | per jurisdiction | per jurisdiction | per jurisdiction | no | per connector | per jurisdiction | per jurisdiction | per jurisdiction | default TRANSIENT_ONLY | none reviewed |
| DocuAction historical observation | (derived) | DOCUMENTED | yes | inherits source mode | inherits | no | n/a | n/a | inherits | same as source | inherits | a prior observation keeps the rights of its source |
| Prior human determination | GOVERNMENT_PROVIDED (program record) | DOCUMENTED | reference only | no | reference | no | n/a | n/a | n/a | per contract | RAW_PERMITTED for the reference id | echoed, never read by rules |

## Rules

1. A source with status other than DOCUMENTED cannot be enabled (its flag may exist; enabling is a program decision recorded against this register).
2. `external_call_allowed = false` for every source today; no adapter contains network code (tested).
3. A source's `retention_rule` is enforced by a purge job that does not exist yet; until it exists, no source with a finite retention may be enabled.
4. Attribution obligations belong to the display layer; the observation carries `attribution_required` so a future UI cannot omit it by oversight.
5. Synthetic test data is never sent to any external service (Google's USPS "artificially created address" clause is one reason; the program's data-handling terms are the other).
