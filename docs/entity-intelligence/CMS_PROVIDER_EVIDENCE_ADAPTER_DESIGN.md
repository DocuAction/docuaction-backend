# CMS provider evidence adapter — design (NOT BUILT)

## Starting point: what already exists and is frozen

The Task 3 pipeline already downloads, hashes, schema-validates and stores the five PPEF components (`app/Tefca/ppef_resources.py`, `ppef_ingest.py`, `ppef_jobs.py`, `ppef_store.py`; tables `tefca_ppef_snapshots`, `tefca_ppef_records`), records PPEF lineage as hop lists (`app/core/evidence_provenance.py::build_ppef_lineage`), and compares delivered addresses with PPEF localities (`app/Tefca/address_comparison.py`). It answers only from one COMPLETE snapshot per component. None of that changes.

Therefore the entity-intelligence adapter for CMS must **read preserved snapshots, not download**. A second downloader would duplicate a working, audited job and create two editions of "the current PPEF".

## Design

```
CMSPPEFAdapter (app/evidence_sources/cms_ppef/, flag: CMS_ENROLLMENT_EVIDENCE_ENABLED — new sub-flag, default False)
    describe() -> AdapterDescriptor(source_id="CMS_PPEF", source_owner="CMS PECOS public extract",
                                    status=DESIGN_ONLY today, makes_external_calls=False, requires_credential=False,
                                    data_rights=PUBLIC / DOCUMENTED (CMS public use; fact sheet))
    observations_for(canonical_entity_id, identifier=NPI, provenance)
        input:  the latest COMPLETE snapshot per component, supplied by a read-only port
                (SnapshotReader protocol; the platform implementation wraps ppef_store.latest_snapshot —
                 wired only in the integration sprint; tonight the port has an in-memory test double)
        output: EvidenceObservations with source_authority=FEDERAL_PROGRAM_ENROLLMENT:
            IDENTIFIER   role "NPI"            CMS_NPI_OBSERVED (value, ENRLMT_ID(s), PAC ID as opaque link key)
            IDENTIFIER   role "NPI"            CMS_ADDITIONAL_NPI_OBSERVED (from Additional NPIs, per enrollment)
            NAME         LEGAL_BUSINESS_NAME   CMS_ORG_NAME_OBSERVED (ORG_NAME; kind stated as "enrollment organisation name")
            LOCATION     CMS_ENROLLMENT_LOCATION  CMS_PRACTICE_LOCATION_OBSERVED (city/state/ZIP only; line1 absent → the
                                              engine treats it as locality-only, i.e. never a street match)
            RELATIONSHIP "MEDICARE:REASSIGNS_BENEFITS_TO" CMS_REASSIGNMENT_RELATIONSHIP_OBSERVED
            PROGRAM_PARTICIPATION "MEDICARE:ENROLLMENT"  MEDICARE_ENROLLMENT_OBSERVED (one per enrollment; PROVIDER_TYPE, STATE_CD)
            absence()    PROGRAM_PARTICIPATION "MEDICARE:ENROLLMENT" with AbsenceReason and applicability when no row matches
        provenance: SourceVersionRef(source="CMS_PPEF", dataset_version=<resource_version e.g. 2026.07.17>,
                    retrieval_method=DOWNLOAD, retrieved_at=<snapshot.retrieved_at>, source_file_hash=<snapshot.sha256>,
                    dataset_identifier="<component> <cms_title>"); source_record_ref = deterministic row key;
                    schema_version = the component's EXPECTED_FIELDS tuple hash
```

Applicability is decided before the read, exactly as the methodology draft §5 does: NPI absent → IDENTIFIER_NOT_AVAILABLE absence; taxonomy not Medicare-relevant → NOT_APPLICABLE absence; otherwise CONDITIONALLY_APPLICABLE.

### Provider-type adapters (Hospital / FQHC / RHC / Hospice Enrollments)

Same shape, one adapter per dataset, only if PPEF proves insufficient for a need the program states. Their distinct value: **DBA** and a **street-level enrollment address** for those provider types, and monthly cadence for hospitals. They would need their own preserved-snapshot job (none exists); that is a new acquisition design, not tonight's work. Recommendation: do not build until the program confirms DBA/street corroboration from CMS is wanted for those populations.

## Why not a PECOS connector

There is no public PECOS API for enrollment lookup; the existing `PECOSConnector` class is an NPPES proxy and the vocabulary already warns that a "PECOS non-match" from it must never feed a discrepancy category. The public evidence is the PPEF extract, which is already preserved. Building a "PECOS connector" would either duplicate that or mislabel NPPES.

## Comparison behaviour with a CMS source present

| Dimension | Behaviour |
|---|---|
| Identifier | corroborated when exactly one enrollment set carries the NPI; MULTIPLE_CANDIDATE_ENTITIES only if the NPI maps to different PAC IDs; additional-NPI observations explain a delivered NPI that differs from an enrollment's primary NPI |
| Name | ORG_NAME vs delivered: DIRECT/NORMALIZED/AMBIGUOUS/CONFLICT; DBA never inferred from PPEF (it has none) |
| Location | locality-only: AMBIGUOUS_LOCATION at best; the explanation says the file publishes no street line |
| Relationship | MEDICARE:REASSIGNS_BENEFITS_TO compared only with a delivered relationship of the same kind (there is none in the TEFCA delivery) → NOT_COMPARABLE; recorded as context |
| Participation | MEDICARE:ENROLLMENT observed / not found with reason; never compared with TEFCA:PARTICIPANT |

## Tests to write when built

Snapshot double with two enrollments for one NPI; additional NPI; no practice location for one enrollment; ORG_NAME formatting variant; absent NPI; taxonomy not relevant; edition change with unchanged values (PROGRAM_ENROLLMENT UNCHANGED delta); PARTIAL snapshot never read.

## Flags and isolation

New sub-flag under the master; adapter behind `flags.require_enabled`; no import of `app.Tefca` from the adapter (the port is injected), so the isolation test's "no platform references" rule holds until the integration sprint wires the port.
