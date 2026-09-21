# IQVIA Release 1 — observation-layer schema PROPOSAL (not implemented)

**Status:** PROPOSAL. Blocked on (a) the licensed IQVIA OneKey file
specifications, (b) sample files, (c) a data-use approval naming the
programme. None of the three is in the repository or in DocuAction's
possession as of 2026-09-20. Nothing below is created by any migration;
the generic layer it plugs into (`source_snapshot`, `entity_source_match`,
`arc_assessment_run`) IS created by `20260921_september_snapshot`.
Governing decisions: ADR-006.

## Five-layer model

| Layer | Table | Exists? | Purpose |
|---|---|---|---|
| 1 | `source_snapshot` | yes (20260921) | One row per received file of any source; approval is an append-only successor row |
| 2 | `tefca_entity_observation` | proposed | What the ONC delivery said about an entity in one snapshot — today served by `rce_curated_records` + `rce_entity_presence`; a dedicated table is deferred until a second TEFCA source exists |
| 3 | `iqvia_hco_observation`, `iqvia_hcp_observation`, `iqvia_affiliation_observation` | proposed | Verbatim licensed rows keyed by snapshot; never joined to registry tables directly |
| 4 | `entity_source_match` | yes (20260921) | The analyst/QA-determined link between an entity and a source record key |
| 5 | `arc_assessment_run` | yes (20260921) | Which snapshot ids and model/rule versions an assessment used |

## Proposed observation tables (subject to the licensed spec)

Column names below are placeholders for the licensed field names and will be
replaced by the specification's names verbatim; nothing is renamed or
derived at ingestion.

```
iqvia_hco_observation
  id uuid pk
  source_snapshot_id uuid fk source_snapshot(id) on delete restrict  -- APPROVED only
  source_record_key text not null            -- the licensed HCO identifier, verbatim
  record_sha256 char(64) not null            -- of the delivered row bytes
  payload jsonb not null                     -- the delivered fields, verbatim
  npi text, ccn text                          -- lifted for indexing only, verbatim
  observed_at timestamptz not null default now()
  correlation_id varchar(64) not null
  unique (source_snapshot_id, source_record_key)

iqvia_hcp_observation        -- same shape; HCP key; NPI (Type 1 expected)
iqvia_affiliation_observation
  id, source_snapshot_id, hcp_record_key, hco_record_key, affiliation_type text,
  payload jsonb, record_sha256, observed_at, correlation_id
  unique (source_snapshot_id, hcp_record_key, hco_record_key, affiliation_type)
```

Rules that will bind these tables (already in code, `source_matching.py`):

* Ingestion refuses a snapshot that is not `APPROVED`.
* An affiliation row never becomes a `tefca_entity_relationships` row.
* HCP rows are Type-1 NPIs by nature: they can never auto-match an
  organisation entity (`EXCEPTION`).
* Grants: SELECT+INSERT to the app role; no UPDATE/DELETE; served only above
  the reviewer floor with `ENABLE_IQVIA_SOURCES` on.

## Matching model (Release 1)

| Method | Automatic? | Result |
|---|---|---|
| NPI exact, NPPES Type 2, Luhn-valid, unique in registry | yes | `AUTO_APPROVED` |
| NPI exact but Type 1 / ambiguous / invalid | no | `EXCEPTION` |
| CCN | no | `CANDIDATE` |
| Exact name + address + phone | no | `CANDIDATE` |
| Fuzzy discovery | no | `CANDIDATE` |
| Analyst determination | human | `ANALYST_APPROVED` |
| Independent QA | human (≠ analyst) | `QA_APPROVED` (reportable) |

`matching_model_version = r1-foundation-1.0.0`.

## Tests to add when the spec arrives

* Ingest refuses RECEIVED snapshots; accepts APPROVED; idempotent per
  (snapshot, key).
* Payload round-trip: `record_sha256` reproduces from the stored payload.
* An affiliation between an HCP and an HCO creates no registry relationship.
* A licensed value never appears in a log line (extend
  `tests/test_release1_foundation.py::test_log_redaction_covers_licensed_keys`).
* Access: viewer/contributor/manager get `requires_role:reviewer`; flag off
  gets `not_configured`.
