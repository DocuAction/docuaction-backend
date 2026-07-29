# TEFCA Compliance Assessment

> Alignment of the DocuAction registry with the TEFCA / RCE IG model. Static review of `app/tefca_registry/` (+ legacy `app/Tefca/`). Read-only.

## 1. Entity hierarchy (QHIN / Participant / Sub-Participant) — **Compliant**
`tefca_registry/models.py:30-70` — `TefcaRegEntity` (`tefca_reg_entities`). `entity_level` (VARCHAR enum, `models.py:38`): `qhin, participant, sub_participant, child`; `entity_type` (`:42`): health_information_network, hospital_system, etc.

Hierarchy is modeled as **explicit directed edges** (not a self-referential FK) in `TefcaEntityRelationship` (`models.py:111-141`): `parent_entity_id`/`child_entity_id` both FK to `tefca_reg_entities.id`, `relationship_type` (`belongs_to, sub_participant_of, …`), a self-loop guard `CheckConstraint("parent_entity_id <> child_entity_id")` (`:132`), and a unique-edge constraint (`:134`). Hierarchy types centralized at `queries.py:18` `HIERARCHY_TYPES`. Expected parent-level rules enforced in `verification.py:275` (`participant→qhin`, `sub_participant→participant`, `child→sub_participant`). **Sound, spec-aligned modeling.**

## 2. RCE IG v1.14.0 mandatory identifiers (TEFCAID + HCID) — **Partial**
- Identifiers live in `TefcaEntityIdentifier` (`models.py:75-106`); `identifier_value` is `nullable=False` (`:87`), **but there is NO DB-level requirement that every entity carry a TEFCAID or HCID** — an entity with zero identifiers is structurally allowed.
- Mandatory presence is enforced **only at the verification layer** as `critical` findings: `verification.py:173-178` ("Missing mandatory TEFCAID", "Missing mandatory HCID"). **Detective, not preventive.**
- Import engines **do** require both: CSV requires TEFCAID+HCID columns (`csv_import.py:49-52`); FHIR idempotent-skip keys on tefcaid/hcid (`fhir_import.py:122-125`).
- **Gap:** a manually-inserted or partial entity can persist without mandatory IDs (caught only on the next verification run). **Recommend** a create-time service/DB guard. **Partial.**

## 3. Verification engine coverage — **Compliant (two engines)**
- **Registry engine** `tefca_registry/verification.py`: identity checks (`_check_identity`, l.161) — mandatory TEFCAID/HCID, retired-TEFCAID-on-active, **NPI Luhn** validity (`_npi_valid`, l.34-47), duplicate NPI, duplicate HCID/TEFCAID, expired CCN; hierarchy checks (`_check_hierarchy`, l.232) — circular (**Tarjan SCC**, l.100), orphan, inactive-parent-with-active-children, wrong parent level, multiple parents. External NPPES/LEIE/SAM/PECOS are **plumbed but gated** (`verification.py:341-349`, `include_external`, synthetic seed).
- **Legacy connector engine** `Tefca/validation_engine.py`: 4-bucket classification, confidence scoring, NPI active-status check (l.210-223), fail-closed INDETERMINATE routing (l.377-406).
- **`ACTIVE_NPPES_STATUSES` fix confirmed:** single shared source at `Tefca/connectors.py:57` `= ("ACTIVE", "A", "")`, imported by `validation_engine.py:18`, used at `:221` and `connectors.py:1017`. The status-"A"→inactive bug is fixed via this shared tuple. **Compliant.**

## 4. Import engine (FHIR Bundle + CSV) — **Compliant**
`tefca_registry/fhir_import.py`:
- **Ordering / reference resolution:** explicit **two-pass** (`:133-201`) — pass 1 creates entities+identifiers+endpoints, pass 2 resolves `partOf` into relationships; docstring (l.10-12) states it "handles any ordering in the Bundle". Parent resolved via in-batch map or DB lookup (`_resolve_fhir_parent` :240, `_resolve_tefcaid_parent` :247).
- **Identifier mapping:** `_extract_identifiers` (:297-317) maps by type-code (HCID/TEFCAID) and system URI (`_SYSTEM_TO_TYPE` :46-51); unrecognized identifiers **skipped, never guessed** (:315).
- Idempotent skip on existing TEFCAID/HCID (:122-139); fault-tolerant per-entity **savepoints** (`begin_nested` :141); batch tracking + audit rows (:116-119, 226-230); per-entity version snapshot (:204-216). CSV importer reuses the same `persist_import`. **Robust and spec-aligned.**

## 5. Common Agreement obligations — **Gap (docs only)**
No operational code addressing Common Agreement flow-down/attestation/exchange-purpose enforcement. Only a seed catalog string (`platform_config/seed.py:183`). `exchange_purposes` JSONB exists on the entity (`models.py:55`) but no enforcement logic. **Gap.**

## TEFCA readiness verdict
The **structural TEFCA model is strong and largely spec-aligned** — hierarchy, identifiers, a genuine verification engine (Luhn + Tarjan SCC + level rules), and a robust two-pass FHIR/CSV importer with the NPPES-status bug fixed. The two real gaps: **(1) mandatory TEFCAID/HCID is detective, not preventive** (add a create-time guard), and **(2) Common Agreement obligations are documented but not enforced in code.** Neither is a modeling flaw — both are enforcement additions.

| Item | Status |
|---|---|
| Entity hierarchy | ✅ Compliant |
| Mandatory TEFCAID/HCID | ◐ Partial (detective only) |
| Verification engine (+ NPPES-status fix) | ✅ Compliant |
| Import engine (ordering/refs/mapping) | ✅ Compliant |
| Common Agreement obligations | ❌ Gap (docs only) |
