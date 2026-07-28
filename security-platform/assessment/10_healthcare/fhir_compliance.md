# FHIR Compliance Assessment

> FHIR R4 storage, identifier systems, profiles, and Bundle import — against the RCE IG. Static review of `app/tefca_registry/`. Read-only. Verdict: **Compliant** across all four checks.

## 1. FHIR R4 storage — **Compliant**
`fhir_resource = Column(JSONB)` at `tefca_registry/models.py:54` on `TefcaRegEntity`. Full R4 **Organization** JSON stored on import (`fhir_import.py:373`); version snapshots include it (`fhir_import.py:211`). (Performance note: **no GIN index** on this column — Part 7 `database_performance.md`; a `fhir_resource->>'id'` lookup at `fhir_import.py:244` seq-scans.)

## 2. FHIR identifier system URIs — **Compliant**
Canonical URIs defined consistently in three places (`fhir_import.py:36-43`, `seed.py:60-67`, referenced in `validation_engine.py`/`connectors.py`) and stored per-identifier in `system_uri` (`models.py:88`):

| Type | System URI |
|---|---|
| NPI | `http://hl7.org/fhir/sid/us-npi` |
| HCID | `urn:ietf:rfc:3986` |
| CCN | `urn:oid:2.16.840.1.113883.4.336` |
| CLIA | `urn:oid:2.16.840.1.113883.4.7` |
| NAIC | `urn:oid:2.16.840.1.113883.6.300` |
| TEFCAID | `https://rce.sequoiaproject.org/fhir/identifier/tefcaid` |

These match the expected RCE IG / US Core identifier systems. **Compliant.**

## 3. FHIR profile references (`meta.profile`) — **Compliant**
Level detection reads `meta.profile` **first**: `_detect_level` (`fhir_import.py:257-267`) checks profile suffixes `/qhin`, `subparticipant`, `/participant`, `/child`, falling back to `type.coding.code`. Seed sets the QHIN profile (`seed.py:68`). Profile-aware ingestion is the correct RCE-IG behavior. **Compliant.**

## 4. FHIR Bundle import (ordering / references) — **Compliant**
Two-pass import (`fhir_import.py:133-201`) handles **arbitrary Bundle ordering** and resolves `partOf` references in pass 2 (see `tefca_compliance.md` §4 for detail). Idempotent skip, per-entity savepoints, batch audit, version snapshots. **Robust.**

## FHIR verdict
**FHIR R4 handling is genuinely spec-compliant** — correct identifier system URIs, profile-first level detection, and a robust ordering-tolerant two-pass Bundle importer with proper reference resolution. The only caveat is **operational, not conformance**: the `fhir_resource` JSONB needs a **GIN index** (+ an expression index on `fhir_resource->>'id'`) so FHIR-id lookups and containment queries don't seq-scan at scale.

| Check | Status |
|---|---|
| FHIR R4 storage (`fhir_resource` JSONB) | ✅ Compliant (needs GIN — perf) |
| Identifier system URIs | ✅ Compliant |
| `meta.profile` references | ✅ Compliant |
| Bundle import (ordering/refs) | ✅ Compliant |
