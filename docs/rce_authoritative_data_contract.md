# RCE Authoritative Data Contract

**Status:** DESIGN / VALIDATION ONLY — no ingestion performed, no database mutation.
**Prepared:** 2026-08-22
**Contract:** ONC 7571MN26F80064 (Alliance Global Tech)
**Field map version under review:** `FIELD_MAP_VERSION = "1.0.0"`
**Branch:** `fix/tefca-stabilization`

---

## 0. Governing correction (read first)

The authoritative ONC/RCE CSV **has not been provided to this workflow and has not
been ingested.**

One artifact exists in `uploads/rce_deliveries/`. Its provenance is **not proven.**
Throughout this document it is called the **PROFILED ARTIFACT** and never "the ONC
delivery", "the RCE delivery", "production", or "real".

| Property | Value |
| --- | --- |
| Path | `uploads/rce_deliveries/689472073480b1cc_onc-snapshot-20260720.csv` |
| SHA-256 | `689472073480b1cc4faf604527eda47e4e59928f7a6128d84b2f28bb6e9e9e8d` (independently recomputed — **matches** the recorded hash) |
| Size | 10,042,400 bytes |
| Rows | 23,566 data rows (independently counted) |
| Columns | 41, pipe-delimited, UTF-8, CRLF |
| Intake id | `9a32a35d-e6e3-40d3-90c6-2d1e43e0bab3` |
| Received | 2026-08-21 18:05:19 by imran@agtbi.com |
| Operator label | `source_metadata.origin = "ONC/RCE delivery"` — **typed at intake, not evidence** |

**Authenticity classification: UNKNOWN — UNVERIFIED FOR COR USE.**

The pipeline is provably honest (artifact preserved read-only, hash reconciles, row
count reconciles) and the file is provably **not** the output of the known synthetic
generator `app/Tefca/rce_mock_enrichment.py` (0 occurrences of its markers). But
there is **no chain of custody**: no transmittal record, no ONC-issued control
total, no download provenance. It fails the fourth authenticity element
(independently reconciled control count).

Consequences, binding on this document:

- 23,566 / 23,562 are **not** an RCE population and **not** a COR denominator.
- Every "observed" statistic below is a property of the PROFILED ARTIFACT only.
- `TEFCA_ENTITY_DATA_KEY` remains unset. MOCK/demo labelling is unchanged.

### The single most important architectural finding

`app/tefca_registry/rce/field_map.py:59-62`

```python
PROFILED_FILE = "onc-snapshot-20260720.csv"
PROFILED_RECORD_COUNT = 23_566
PROFILED_AT = "2026-08-21"
```

**CORRECTED 2026-08-22.** The origin of the field map splits cleanly in two, and the
distinction matters:

| Part of the map | Origin | Status |
| --- | --- | --- |
| The 41 column **names and their order** — i.e. `RCE_FIELDS`, and therefore `EXPECTED_SCHEMA_FINGERPRINT` | **Supplied by the user**, 2026-08-21T17:03:13Z, before ingestion | **VERIFIED** — fingerprint match, zero positional differences |
| Relationship model, DQ issue catalogue, `sequoiaorgtype`/`organizationNodeType` distinction | **Supplied by the user**, same turn | VERIFIED by attestation |
| Every **quantitative** claim — coverage %, distinct counts, min/max lengths, necessity classifications, all `OBSERVED_*` vocabularies, the 11 QHIN OIDs | **Profiled from the 23,566-row artifact** | Conditional on that artifact |
| Meaning of 6 fields (`domains`, `initiatoronly`, `stateofoperation`, `transaction`, `delegationRole`, `address_text`) | Neither — explicitly `_UNDOCUMENTED` | Correctly not inferred |

So the **structure** of the contract rests on user-supplied authority, while its
**statistics** rest on an artifact of unresolved contractual authority. If that
artifact is not the intended delivery, the schema stays correct and the numbers do
not. This narrowed risk is **GAP-C1**.

---

## 1. Source information currently available

### 1.1 Genuine user-provided source information: **FOUND — schema, not rows**

> **CORRECTED 2026-08-22.** An earlier revision of this document stated that no
> user-provided source information existed. That was wrong. Forensic review of the
> session transcripts found that the user supplied the **complete authoritative
> 41-column header, in exact delivered order**, at **2026-08-21T17:03:13Z** — one
> hour before any file was ingested.

**COMPLETE 41-COLUMN HEADER PROVIDED BY USER: YES**
**HEADER FINGERPRINT VERIFIED: YES**
**SCHEMA ORDER VERIFIED: YES**

Location: `~/.claude/projects/C--Imran-Coding-projects-DocuAction-backend/`
`1eebe1d9-ddac-4fa0-911b-33209e4b6b37.jsonl`, user turn 2026-08-21T17:03:13Z.

Proof: hashing the user's 41 names with `field_map.schema_fingerprint()` yields
`1cd655e9120dc9d0d6a52697ea470519b138fe0f9334af6f69467f3485ade3d0`, which equals
`EXPECTED_SCHEMA_FINGERPRINT` exactly — ordered match, zero positional differences.

The user additionally supplied the relationship model (`orgManagingOrg`→QHIN,
`partOf`→parent, `sequoiaorgtype` = Participant/Subparticipant), the
`sequoiaorgtype` vs `organizationNodeType` semantic distinction, a data-quality
issue catalogue, and real observed values (`ELLKAY-DOA-TEST`; ZIP `94761` for
Hawaii, should be `96761`; okina mojibake `â€˜`; embedded tabs in `address_text`).

**Sample data ROWS: 0.** No RCE data row was ever pasted. Verified across all six
session transcripts (user-authored turns only, tool results excluded).

An exhaustive search of the repository and working tree found no standalone
user-provided RCE sample file. Searched: all `*.csv|tsv|psv|json|xlsx|txt`
matching `rce|tefca|onc|snapshot|deliver|sample|fixture|seed`, all of `tests/`,
all git history including deleted/renamed blobs, stash, and dangling objects.

What exists instead:

| Candidate | Records | Verdict |
| --- | --- | --- |
| PROFILED ARTIFACT | 23,566 | Provenance unproven — **UNKNOWN** |
| `app/Tefca/rce_mock_enrichment.py` | 30 + 11 | **SYNTHETIC** — self-declared; deliberately impossible identifiers (`urn:uuid:00000000-test-NNNN-mock-…` is non-hex, `.9999` OID arc, `@example.com`, `555-0NN-NNNN`) |
| `tests/test_rce_pipeline.py`, `test_rce_enrichment.py`, `test_qa_entity_import.py` | inline | **SYNTHETIC** test fixtures authored in-repo |
| DB: 183 entities created 2026-07-25 | 183 | **SEED/DEMO** — invented names (`Cypress Imaging Center`, `Pinnacle Urgent Care`, `Test Import QHIN Alpha`) |
| DB: 11 entities created 2026-08-21 | 11 | **DERIVED** — set-equal to the artifact's 11 distinct `orgManagingOrg` values (symmetric difference = ∅); synthesised by `promotion.py`, fully audited |

If genuine sample records were supplied earlier, they were supplied **in
conversation and never persisted to disk.** They cannot be inventoried.

### 1.2 Format observed (in the unverified artifact)

Pipe-delimited, UTF-8, CRLF, 41 columns, header row present, no quoting observed,
no embedded newlines. Detected automatically by `reader.detect_delimiter`.

### 1.3 Field population — OBSERVED IN THE PROFILED ARTIFACT ONLY

**Always populated (100% — 15 columns):** `id`, `domains`, `orgManagingOrg`, `HCID`,
`TEFCAID`, `active`, `sequoiaorgtype`, `name`, `address_text`, `address_line`,
`address_city`, `address_state`, `address_postalCode`, `address_country`, `partOf`

**Sometimes populated (20 columns):** `purposesofuse` 98.25%, `NPI` 80.55%,
`contact_name` 77.40%, `contact_email` 75.44%, `contact_phone` 75.43%,
`contact_purpose` 72.97%, `AAID` 31.60%, `contact_address_{line,city,state,postalCode}`
30.51%, `contact_address_country` 30.48%, `contact_address_text` 0.69%, `doa` 0.45%,
`phone` 0.36%, `hl7orgrole` 0.25%, `stateofoperation` 0.03%, `initiatoronly` 0.02%,
`delegationRole` 0.01%, `organizationNodeType` 0.01%

**Never populated (0% — 6 columns):** `transaction`, `NAIC`, `CCN`, `alias`, `email`,
`contact_company`

> **A blank column in the profiled artifact does not prove the column is optional in
> the authoritative dataset.** These 6 columns carry `SCH-002` and are the highest-risk
> region of the contract: their semantics, necessity, format, and cardinality are
> **entirely unproven**, and their mapping paths (`NAIC`/`CCN` → identifier rows,
> `alias` → `display_name`) have **never once been exercised by data.**

### 1.4 Documentation status

`field_map.py` distinguishes what DocuAction can cite from what it cannot, via an
explicit sentinel:

> `_UNDOCUMENTED = "No RCE/TEFCA specification text for this field is in DocuAction's
> possession. Meaning is NOT inferred from the column name."`

- **Fields with a specification DocuAction holds: 35**
- **Fields with no specification at all: 6** — `domains`, `initiatoronly`,
  `stateofoperation`, `transaction`, `delegationRole`, `address_text`

### 1.5 Verdict

> ## SOURCE SCHEMA: CONFIRMED. DATASET AUTHORITY: NOT CONFIRMED.
>
> **CORRECTED 2026-08-22.** The 41-column header, including column order, was
> supplied by the user and verified against `EXPECTED_SCHEMA_FINGERPRINT`. The
> schema is a **CONFIRMED SOURCE SCHEMA**, not merely observed.
>
> What remains unconfirmed is the **dataset**, not the schema: no data dictionary
> or written specification was supplied, and the contractual authority of the
> 23,566-row artifact is unresolved. Schema conformance proves structure; it does
> not prove that this particular file is the authoritative delivery.

---

## 2. Minimum source information required from the user

**Do not send the full CSV yet.** Requested, in priority order:

| # | Item | Why it is needed | Blocks |
| --- | --- | --- | --- |
| ~~A~~ | ~~The exact header row~~ — **ALREADY SUPPLIED AND VERIFIED 2026-08-21T17:03:13Z.** Do not request again. | Fingerprint match confirmed; see §1.1. | *closed* |
| **A′** | **Reconcile ~2,300 vs 23,566** — is the Box artifact the intended delivery, or does a ~2,300-record delivery exist elsewhere? | The user stated ~2,300 records four times, including after ingestion. No column in the artifact has ~2,300 distinct values, so the gap is not structural. This is the sole blocker to dataset classification. | GAP-C1 |
| **B** | **10–20 representative rows**, redacted as needed — deliberately including rows with `NAIC`, `CCN`, `alias`, `email`, `contact_company`, or `transaction` populated | The only way to learn the semantics of the 6 never-populated columns. Blank samples cannot teach a mapping. | GAP-C2 |
| **C** | **Data dictionary / RCE specification**, if one exists | Would replace inference for the 6 undocumented fields and confirm necessity classifications. | GAP-H1 |
| **D** | **A control total** — the record count ONC states the delivery contains, and any transmittal/manifest | The missing fourth authenticity element. Without it no ingestion can ever be classified `VERIFIED_AUTHORITATIVE_SOURCE`. | GAP-C3 |
| **E** | Delivery cadence and whether files are full snapshots or deltas | Determines whether ingestion is replace-by-version or merge. Currently unproven. | GAP-H2 |

**A + B are sufficient to validate the contract before bulk ingestion.** D is
required before any COR-facing use of the resulting population.

---

## 3. Backend model inventory

### 3.1 Modules

| Module | Role |
| --- | --- |
| `rce/field_map.py` | The contract itself — 41 `FieldSpec`, necessity/role/target, schema fingerprint |
| `rce/reader.py` | Encoding, delimiter, line splitting, parsing — produces `DeliveryRead` / `ParsedLine` |
| `rce/intake.py` | Area-1 write: artifact storage, hashing, drift check, one row per line |
| `rce/models.py` | Area-1/Area-2 SQLAlchemy models (8 tables) |
| `rce/quality_rules.py` | ~40 DQ rules with severity + correction authority |
| `rce/quality_engine.py` | Rule execution, issue generation, drift finding SCH-003 |
| `rce/curation.py` | Area-2 correction application, staleness guard |
| `rce/promotion.py` | Area-2 → Area-3 canonical registry promotion, QHIN synthesis |
| `rce/repository.py`, `profiler.py`, `reconciliation.py`, `arc_pipeline.py`, `routes.py` | Persistence, profiling, count reconciliation, orchestration, API |
| `tefca_registry/models.py` | Area-3 canonical registry (10 tables) |
| `Tefca/rce_fields.py` | Read accessors over promoted RCE attributes |
| `Tefca/source_applicability.py` | Which enrichment source applies to which entity |
| `core/ingestion/*` | Reusable Phase-5 framework — owns no tables |

### 3.2 Database tables — 24 reviewed

**Area 1 — immutable delivery (write-once, never updated)**

| Table | Purpose | PK | FKs | Key columns | Writer | Immutability |
| --- | --- | --- | --- | --- | --- | --- |
| `rce_source_intakes` | One delivery event | `id` uuid | self-ref `duplicate_of_intake_id` | `sha256`, `headers` jsonb, `schema_fingerprint`, `record_count`, `storage_path`, `source_metadata` jsonb, `status` | `intake.py` | **IMMUTABLE** |
| `rce_source_records` | One row per delivered line | `id` uuid | → intakes | `line_number`, **`raw_line` (verbatim)**, **`parsed` jsonb**, `record_sha256`, `parse_status`, `promotion_status` | `intake.py` | **IMMUTABLE** except `promotion_status` / `canonical_entity_id` |

**Processing / issue ledger**

| Table | Purpose | PK | FKs | Immutability |
| --- | --- | --- | --- | --- |
| `rce_ingestion_runs` | One rule-execution run | `id` | → intakes | append-only |
| `rce_rule_execution_history` | Per-rule outcome | `id` | → runs | append-only |
| `rce_issues` | One DQ finding | `id` (`issue_code` unique) | → intakes, records, runs | append-only; resolution fields mutable |

**Area 2 — curated**

| Table | Purpose | PK | FKs | Immutability |
| --- | --- | --- | --- | --- |
| `rce_curated_records` | One normalized row per source row (`UNIQUE(source_record_id)`) | `id` | → intakes, records | mutable under correction authority |
| `rce_correction_details` | One field-level change + authority + `original_value_hash` staleness guard | `id` | → curated, records, issues | append-only |
| `tefca_entity_contacts` | Promoted contact rows | `id` | → entities | mutable |

**Area 3 — canonical registry**

`tefca_reg_entities`, `tefca_entity_identifiers`, `tefca_entity_relationships`,
`tefca_entity_versions`, `tefca_entity_endpoints`, `tefca_verification_jobs`,
`tefca_verification_checks`, `tefca_entity_findings`, `tefca_import_batches`,
`tefca_reg_audit_log`

**Enrichment / evidence (never source)**

`tefca_dimension_evidence`, `tefca_evidence_records`, `source_version_snapshots`,
`evidence_relationship_path`, `tefca_ppef_snapshots`, `tefca_ppef_records`

---

## 4. Source → backend → database crosswalk

Ordinals are the delivered order. Coverage is **OBSERVED IN THE PROFILED ARTIFACT**.

### 4.1 Mapping and storage

| # | Source column | Raw Area-1 | Parsed | Area-2 (`rce_curated_records`) | Area-3 | Type | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `id` | `raw_line` | `parsed.id` | `rce_org_oid` | `tefca_entity_identifiers` type `rce_org_oid` + `tefca_reg_entities.rce_org_oid` | String(200) | MAPPED |
| 2 | `domains` | ✓ | ✓ | — | **NOT_PROMOTED** | — | RAW_ONLY |
| 3 | `initiatoronly` | ✓ | ✓ | `rce_attributes` | `rce_attributes` jsonb | jsonb | MAPPED |
| 4 | `orgManagingOrg` | ✓ | ✓ | `org_managing_org` | `tefca_entity_relationships` (`managed_by_qhin`) | String(200) | MAPPED |
| 5 | `purposesofuse` | ✓ | ✓ | `exchange_purposes` jsonb | `exchange_purposes` jsonb | jsonb | MAPPED |
| 6 | `stateofoperation` | ✓ | ✓ | `rce_attributes` | `rce_attributes` | jsonb | MAPPED |
| 7 | `doa` | ✓ | ✓ | `rce_attributes` | `rce_attributes` | jsonb | MAPPED |
| 8 | `transaction` | ✓ | ✓ | — | **NOT_PROMOTED** | — | **RAW_ONLY / AMBIGUOUS** |
| 9 | `delegationRole` | ✓ | ✓ | `rce_attributes` | `rce_attributes` | jsonb | MAPPED |
| 10 | `organizationNodeType` | ✓ | ✓ | `org_node_type` | `org_node_type` | String(100) | MAPPED |
| 11 | `NPI` | ✓ | ✓ | `npi` | identifier row `npi` | String(40) | **PARTIALLY_MAPPED** — multi-valued cells |
| 12 | `NAIC` | ✓ | ✓ | *(identifier)* | identifier row `naic` | String(500) | **SOURCE_SCHEMA_NOT_CONFIRMED** |
| 13 | `CCN` | ✓ | ✓ | *(identifier)* | identifier row `ccn` | String(500) | **SOURCE_SCHEMA_NOT_CONFIRMED** |
| 14 | `HCID` | ✓ | ✓ | `hcid` | identifier row `hcid` + `rce_hcid` | String(100) | MAPPED |
| 15 | `AAID` | ✓ | ✓ | `aaid` | identifier row `aaid` + `rce_aaid` | String(100) | MAPPED |
| 16 | `TEFCAID` | ✓ | ✓ | `tefcaid` | identifier row `tefcaid` + `rce_tefcaid` | String(100) | MAPPED |
| 17 | `active` | ✓ | ✓ | `operational_status`, `is_active` | `operational_status`, `is_active` | String(50)/bool | MAPPED |
| 18 | `sequoiaorgtype` | ✓ | ✓ | `entity_level` | `entity_level`, `sequoia_org_type` | String(50) | MAPPED |
| 19 | `hl7orgrole` | ✓ | ✓ | `hl7_org_role` | `hl7_org_role` | String(100) | MAPPED |
| 20 | `name` | ✓ | ✓ | `name` | `name` | String(500) | MAPPED |
| 21 | `alias` | ✓ | ✓ | — | `display_name` | String(500) | **SOURCE_SCHEMA_NOT_CONFIRMED** |
| 22 | `phone` | ✓ | ✓ | `rce_attributes` | `rce_attributes` | jsonb | MAPPED |
| 23 | `email` | ✓ | ✓ | — | **NOT_PROMOTED** | — | **SOURCE_SCHEMA_NOT_CONFIRMED** |
| 24 | `address_text` | ✓ | ✓ | `rce_attributes` | `rce_attributes` | jsonb | MAPPED |
| 25 | `address_line` | ✓ | ✓ | `address_line` | `address` | Text | MAPPED |
| 26 | `address_city` | ✓ | ✓ | `address_city` | `city` | String(200) | MAPPED |
| 27 | `address_state` | ✓ | ✓ | `address_state` String(10) | `state` **String(2)** | String(2) | **MAPPED — zero margin** |
| 28 | `address_postalCode` | ✓ | ✓ | `address_postal_code` String(20) | `zip` **String(10)** | String(10) | **MAPPED — zero margin** |
| 29 | `address_country` | ✓ | ✓ | `address_country` | `rce_attributes` | jsonb | MAPPED |
| 30 | `partOf` | ✓ | ✓ | `part_of` | `tefca_entity_relationships` (`sub_participant_of`) | String(200) | MAPPED |
| 31 | `contact_company` | ✓ | ✓ | `contact` jsonb | `tefca_entity_contacts.company` | jsonb | **SOURCE_SCHEMA_NOT_CONFIRMED** |
| 32–41 | `contact_purpose`, `contact_name`, `contact_phone`, `contact_email`, `contact_address_{text,line,city,state,postalCode,country}` | ✓ | ✓ | `contact` jsonb | `tefca_entity_contacts.*` | jsonb / varchar | MAPPED |

### 4.2 Semantics and usage

| # | Column | Business meaning | Necessity | Key role | Validation | Dimensions | Documented |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `id` | RCE organisation OID — **the only unique key** | REQUIRED | **BUSINESS KEY** | SCH-001, ID-001 | D1, D5 | yes |
| 2 | `domains` | Constant `"RCE"` (1 distinct) | OPTIONAL | — | CON-001 | — | **no** |
| 3 | `initiatoronly` | unknown | LEGIT. NULLABLE | — | — | — | **no** |
| 4 | `orgManagingOrg` | QHIN OID (11 distinct) | REQUIRED | **FK → QHIN** | INT-001 | D5, D6 | yes |
| 5 | `purposesofuse` | Exchange purpose tokens (16 distinct) | LEGIT. NULLABLE | — | CON-002, BUS-001 | D5 | yes |
| 6 | `stateofoperation` | unknown (7 distinct) | LEGIT. NULLABLE | — | — | — | **no** |
| 7 | `doa` | Designated org authority (32 distinct) | LEGIT. NULLABLE | — | — | — | yes |
| 8 | `transaction` | unknown, never populated | LEGIT. NULLABLE | — | SCH-002 | — | **no** |
| 9 | `delegationRole` | `principal` | LEGIT. NULLABLE | — | — | — | **no** |
| 10 | `organizationNodeType` | `initiating-node` | LEGIT. NULLABLE | — | — | — | yes |
| 11 | `NPI` | National Provider Identifier | LEGIT. NULLABLE | **join key → NPPES/PECOS/OIG** | NPI-001/2/3 | D1, D2 | yes |
| 12 | `NAIC` | Insurance code | LEGIT. NULLABLE | identifier | SCH-002 | — | yes |
| 13 | `CCN` | CMS Certification Number | LEGIT. NULLABLE | identifier | SCH-002 | — | yes |
| 14 | `HCID` | Home community ID | CONDITIONAL | near-unique | ID-002, ID-003 | D5 | yes |
| 15 | `AAID` | Assigning authority ID | LEGIT. NULLABLE | identifier | ID-004 | D5 | yes |
| 16 | `TEFCAID` | TEFCA identifier — **family, not unique** | REQUIRED | grouping key | ID-005, ID-006 | D5 | yes |
| 17 | `active` | `0`/`1` | REQUIRED | — | CON-003 | D5 | yes |
| 18 | `sequoiaorgtype` | `Participant` / `Subparticipant` | REQUIRED | **hierarchy level** | REQ-001, CON-004 | D5 | yes |
| 19 | `hl7orgrole` | HL7 org role (5 distinct) | LEGIT. NULLABLE | — | — | D2 | yes |
| 20 | `name` | Legal/registered name | REQUIRED | matching input | REQ-002, BUS-002 | D1, D3 | yes |
| 21 | `alias` | Alternate name | LEGIT. NULLABLE | — | SCH-002 | — | yes |
| 22–23 | `phone`, `email` | Org contact | OPTIONAL / LEGIT. NULLABLE | — | SCH-002 (email) | — | yes |
| 24–29 | `address_*` | Address of record | REQUIRED (line/city/state/zip) | D4 input | FMT-001/2/3/4, REQ-003, CON-005 | D4 | yes |
| 30 | `partOf` | Parent org OID | REQUIRED | **FK → parent** | INT-002, INT-003, BUS-003 | D5, D6 | yes |
| 31–41 | `contact_*` | Administrative contact (PII) | OPTIONAL / LEGIT. NULLABLE | — | FMT-005, FMT-006 | — | yes |

---

## 5. Relationship model — PROVEN, not assumed

Every relationship below was proven by measuring the artifact, not inferred from
column names.

### 5.1 `partOf` — organisational hierarchy

```
distinct partOf values ......... 300
  resolve to a row `id` in file . 289   → intra-file parent
  dangling ......................  11   → EXACTLY the 11 QHIN OIDs
rows where partOf == own id ....   0   → no self-parenting
```

| | |
| --- | --- |
| **Parent** | `rce_source_records.parsed.id` (289 cases) **or** a QHIN OID not present as a row (11 cases) |
| **Child** | the row itself |
| **Cardinality** | many-to-one; 23,566 children → 300 parents |
| **Join key** | `partOf` → `id` (string OID) |
| **Nullability** | 100% populated in the artifact; declared REQUIRED |
| **Source authority** | RCE |
| **DB enforcement** | `tefca_entity_relationships` FK to `tefca_reg_entities`, `CHECK(parent <> child)`, `UNIQUE(parent, child, relationship_type)` |
| **App enforcement** | `INT-002 PART_OF_UNRESOLVED`, `INT-003 SUBPARTICIPANT_PARENTED_TO_QHIN` |
| **Type** | **hierarchical, one-to-many** |

The 11 dangling values are not a defect — they are the design. A QHIN is referenced
but never delivered as a record, so `promotion.py` **synthesises** a QHIN entity per
OID and writes an audit row explaining it:

> `"QHIN referenced by orgManagingOrg but not present as a record in the delivery.
> Synthesised so the hierarchy has a root."`

This is the proven origin of the 11 `QHIN <OID>` entities in the current database.

### 5.2 `orgManagingOrg` — QHIN attribution

Many-to-one, 23,566 → 11, 100% populated, always dangling (QHIN is never a row).
Distribution: 10,481 / 6,977 / 4,483 / 477 / 461 / 366 / 102 / 88 / 84 / 44 / 3.
**Reference-only** at source; materialised as a `managed_by_qhin` edge.

### 5.3 `TEFCAID` — family grouping, NOT identity

```
23,566 rows → 23,325 distinct TEFCAID
43 TEFCAIDs appear more than once; largest family = 69 rows
```

`TEFCAID` is **not** a primary key. `tefca_reg_entities.rce_tefcaid` correctly
carries the comment *"family identifier — NOT unique"* and no unique constraint.
Rule `ID-006 SHARED_TEFCAID` reports the condition at INFO.

> **Any report that counts entities by TEFCAID will undercount by 241.**

### 5.4 `HCID` / `AAID`

`HCID` 100% populated, 23,562 distinct — 4 duplicated (max 2). Near-unique but
**not** unique; must not be used as a key. `AAID` 31.6% populated, 7,444 distinct.

### 5.5 `sequoiaorgtype`

`Participant` 11,077 / `Subparticipant` 12,489. Neither is self-parented.

### 5.6 Contact block — **unresolved**

| Column | Populated | Distinct |
| --- | --- | --- |
| `contact_purpose` | 17,196 | **1** |
| `contact_phone` | 17,775 | **34** |
| `contact_email` | 17,779 | **37** |
| `contact_address_line` | 7,191 | **64** |
| `contact_name` | 18,240 | 10,876 |

Thousands of organisations share 34 phone numbers and 37 email addresses. The
contact block is **parent/QHIN-level boilerplate**, not per-entity contact data.

**This relationship is UNRESOLVED.** DocuAction currently promotes one
`tefca_entity_contacts` row per entity, which asserts a per-entity contact fact the
source does not support. Whether contacts should be de-duplicated into shared
contact entities is a **design decision requiring the authoritative specification** —
not implemented here (GAP-H3).

### 5.7 Summary

| Relationship | Type | Status |
| --- | --- | --- |
| `partOf` → parent org | hierarchical many-to-one | **VERIFIED** |
| `orgManagingOrg` → QHIN | many-to-one, reference-only | **VERIFIED** |
| `TEFCAID` → family | many-to-one grouping | **VERIFIED** |
| `id` → entity | one-to-one | **VERIFIED** |
| `NPI` → NPPES/PECOS/OIG | one-to-one *(assumed)* | **AMBIGUOUS** — multi-valued cells exist |
| contact block → entity | one-to-one *(as built)* | **UNRESOLVED** |
| `HCID`/`AAID` → entity | one-to-one | **VERIFIED** (not unique) |
| `NAIC`, `CCN` → entity | unknown | **MISSING** — never populated |

**VERIFIED 6 · AMBIGUOUS 1 · UNRESOLVED 1 · MISSING 2**

---

## 6. Key strategy

| Key | Column | Uniqueness in artifact | Use |
| --- | --- | --- | --- |
| **Business key** | `id` | 23,566 / 23,566 — **unique** | The only safe natural key. Promoted to `tefca_entity_identifiers(rce_org_oid)`. |
| Grouping | `TEFCAID` | 23,325 — not unique | Family grouping only |
| Near-unique | `HCID` | 23,562 — not unique | Never a key |
| Surrogate | `id` uuid4 | — | All PKs |
| Content key | `record_sha256` | per line | Change detection |
| Delivery key | `sha256` | per file | Duplicate detection (linked, never rejected) |

**Rule: `id` is the only column that may be used as a business key.**

---

## 7. Null / blank / missing semantics

CSV has no NULL. The following distinctions hold today:

| Distinction | Preserved? | How |
| --- | --- | --- |
| Blank vs. populated | **Yes** | `parsed[col] == ""` |
| Column absent from header vs. blank | **Yes** | key missing from `parsed` vs. present-and-empty |
| Delivered-blank vs. row-too-short | **Partially** | short rows get `""` for absent trailing columns, conflating the two — but `parse_status = field_count_mismatch` flags the row and `raw_line` permits exact reconstruction |
| Missing vs. invalid | **Yes** | separate rules: `NPI-001 NPI_NOT_SUPPLIED` (INFO) vs. `NPI-002 NPI_MALFORMED` (HIGH) |
| Source value vs. enrichment value | **Yes** | different tables entirely (§10) |
| Source value vs. corrected value | **Yes** | `rce_correction_details.original_value` + `original_value_hash` |

`SCH-002` records column-level emptiness **once per delivery**, not once per record.

---

## 8. Raw preservation rules — Area 1

Verified by reading `reader.read_delivery` and `intake.py`:

1. **The artifact is stored byte-for-byte** at `storage_path`, read-only, SHA-256 recorded. Confirmed on disk: `-r--r--r--`, hash recomputes correctly.
2. **`raw_line` holds every delivered line verbatim.** Exact reconstruction is always possible.
3. **`headers` come from the file, not from the map** — `headers = header_line.split(delimiter)`, then `parsed = dict(zip(headers, values))`. Therefore **an unknown column is captured under its own name and is never silently dropped.**
4. **A malformed row is preserved, not rejected.** On field-count mismatch the reader refuses positional mapping, with the reasoning recorded in `parse_note`:
   > *"values are NOT mapped positionally, because a shifted mapping is worse than none."*
5. **No line is ever discarded.** `read_delivery` raises only `DelimiterUndecidable`, before any line is read.
6. **Row count is reconciled** — `LineCountMismatch` is raised if stored rows ≠ read rows.
7. **Duplicate deliveries are linked, never rejected** (`duplicate_of_intake_id`).

**Area-1 preservation: PASS.** This layer is sound and needs no change.

---

## 9. Normalization rules — Area 2

Normalization writes to `rce_curated_records`; it never touches Area 1.

| Rule | Behaviour | Authority |
| --- | --- | --- |
| `FMT-001 ZIP_LEADING_ZERO_STRIPPED` | zero-pad to 5 | **AUTO_SAFE** |
| `FMT-002 STATE_CASE_NOT_CANONICAL` | upper-case | **AUTO_SAFE** |
| `FMT-002 STATE_NOT_USPS_CODE` | report only | HUMAN_REQUIRED |
| `FMT-003 ZIP_STATE_MISMATCH` | **report only, never correct** | HUMAN_REQUIRED |
| `NPI-002/003` | report only | HUMAN_REQUIRED |
| `SCH-003 SCHEMA_DRIFT` | report, hold promotion | QA_REQUIRED |

`AUTO_SAFE_RULES = {FMT-001, FMT-002, FMT-004}` — only deterministic,
non-substantive transformations may self-apply.

The design discipline is explicit and correct — `FMT-003`:

> *"REPORTS a disagreement. It does NOT correct one: a ZIP-to-state table would let a
> typo in a ZIP rewrite the state, or vice versa, and there is no basis in the data
> for deciding which of the two is wrong."*

And `SUSPECTED_PURPOSE_VARIANTS` are reported, never merged:

> *"asserting T-TREAT means T-TRTMNT would put a claim in the audit trail that the
> RCE never made."*

Every correction writes `rce_correction_details` with `original_value`,
`original_value_hash`, reason, rule id, authority, actor. The hash is a **staleness
guard**: if the underlying value changed between approval and application, the
approval is invalidated rather than applied to a value the reviewer never saw.

**Normalization: PASS.**

---

## 10. Enrichment boundary

**Verified structurally: no enrichment path writes to any `rce_*` column or to
`tefca_reg_entities.rce_*`.** A grep for enrichment writes into source columns
returns nothing.

| Layer | Table | May be written by |
| --- | --- | --- |
| RCE source | `rce_source_records` | intake only |
| RCE curated | `rce_curated_records` | curation only, under authority |
| Canonical | `tefca_reg_entities`, identifiers, relationships | promotion only |
| **Enrichment** | `tefca_dimension_evidence`, `tefca_evidence_records`, `source_version_snapshots`, `tefca_ppef_records`, `tefca_ppef_snapshots` | ingestion framework |
| Human | `rce_correction_details`, `tefca_reviews`, `tefca_findings` | analyst/QA |

Each reference source links to the entity **by key, in its own table, with its own
`SourceVersionRef`** — never by overwriting:

| Source | Join key | Lands in | Cannot overwrite |
| --- | --- | --- | --- |
| NPPES | `NPI` | `tefca_dimension_evidence` (D1) | RCE `name`, `address_*` |
| PECOS/PPEF | `NPI` / `CCN` | `tefca_ppef_records` | RCE identifiers |
| OIG LEIE | `NPI` + name | dimension evidence (D3) | RCE `name` |
| SAM | name / UEI | dimension evidence | anything — currently `UNKNOWN_PENDING_METHODOLOGY`, blocked by D4 |

The chain **RCE source ≠ NPPES ≠ PECOS ≠ OIG ≠ SAM ≠ system interpretation ≠ human
determination** is enforced by table separation, not by convention.

**Enrichment boundary: PASS.**

---

## 11. Schema-drift handling

| Capability | Status | Mechanism |
| --- | --- | --- |
| Expected header set | ✓ | `RCE_FIELDS` (41) |
| Received header set | ✓ | `rce_source_intakes.headers` jsonb — from the file |
| Schema fingerprint | ✓ | SHA-256 over ordered, lower-cased headers |
| Order sensitivity | ✓ | deliberate — *"treating them as the same would silently transpose every value"* |
| Detect missing / added / renamed / reordered | **Partial** | detected **in aggregate** (fingerprint differs) but **not itemised** |
| Unknown column preserved | ✓ | captured in `parsed` under its real name |
| Drift blocks promotion | ✓ | `promotion.py:145` **raises `ValueError`** |
| Drift raises a CRITICAL finding | ✓ | `quality_engine.py:118` → `SCH-003` |
| Datatype change detection | ✗ | not implemented |
| Source-file version | ✓ | `sha256` + `field_map_version` per intake |

Drift behaviour today: **preserve, record, raise CRITICAL, refuse promotion.** That
matches the requested principle — *raw preserved + drift detected + ingestion paused
when material* — with one shortfall: **the operator is told the fingerprint differs,
but not which columns changed.**

**SCHEMA-DRIFT PROTECTION: PARTIAL** (protection is real; diagnostics are not).

---

## 12. Provenance requirements

| Requirement | Status |
| --- | --- |
| Artifact retained byte-for-byte | ✓ |
| SHA-256 recorded and verifiable | ✓ |
| Receiver identity + timestamp | ✓ |
| Field-map version recorded per intake and per curated record | ✓ |
| Rule set version + config hash per run | ✓ |
| Every correction attributed to an actor and authority | ✓ |
| QHIN synthesis audited with rationale | ✓ |
| **Chain of custody from ONC to the artifact** | ✗ **ABSENT** |
| **Independently reconciled control count** | ✗ **ABSENT** |

The last two are why the current artifact cannot be classified better than UNKNOWN.
`source_metadata.origin` is a free-text operator label with no evidentiary weight,
and nothing in the schema distinguishes an asserted origin from a proven one.

---

## 13. Reporting field lineage

`app/reports/data/rce_report_data.py` reads `schema_fingerprint`, `schema_drift`,
`record_count`, and issue aggregates from the intake and issue ledger — so a report
can always state which delivery, which field map, and which rule set produced it.

**Gap:** no report field currently carries an *authenticity* classification. A
report generated today would present counts from the PROFILED ARTIFACT with no
indication that its origin is unproven. See GAP-C3.

---

## 14. Gap analysis

### CRITICAL — could lose or misrepresent authoritative source data

**GAP-C1 — The map's *statistics* rest on an artifact of unresolved authority.**
*(Narrowed 2026-08-22: the schema itself is no longer in doubt — see §0.)*
*Component:* `field_map.py` — the quantitative fields of all 41 specs
(`populated`, `distinct`, `coverage_pct`) and all `OBSERVED_*` vocabularies.
**Not** `RCE_FIELDS` or `EXPECTED_SCHEMA_FINGERPRINT`, which are user-derived and
verified.
*Impact:* Every necessity classification and observed vocabulary is attributed to
"the RCE" but was learned from the 23,566-row Box artifact, whose contractual
authority is unresolved. The user separately stated the delivery contains ~2,300
records. If the artifact is not the intended delivery, the necessity
classifications and vocabularies describe the wrong dataset — while the schema
stays correct.
*Correction:* Rename profiling constants to state their true basis
(`PROFILED_ARTIFACT_SHA256` rather than an implied ONC origin); record in
`field_map` that the schema is user-attested while the statistics are artifact-
derived.
*Test:* assert the field map distinguishes user-attested structure from
artifact-derived statistics, and that no docstring claims ONC authorship without
evidence.

**GAP-C2 — Six columns have never been exercised by data.**
*Columns:* `transaction`, `NAIC`, `CCN`, `alias`, `email`, `contact_company`.
*Impact:* `NAIC`/`CCN` → identifier rows and `alias` → `display_name` are
**untested mapping paths**. If the authoritative CSV populates them, values flow
into columns whose length, format, and cardinality assumptions were never
validated. `NAIC`/`CCN` map to `identifier_value String(500)` with no format rule
at all.
*Correction:* Do not finalise these mappings until sample rows with these fields
populated are supplied (request item B).
*Test:* a fixture per column exercising the full intake → promotion path.

**GAP-C3 — No authenticity classification is recorded or surfaced.**
*Impact:* `source_metadata.origin` is operator free text. Nothing distinguishes an
asserted origin from a proven one, and no report field carries the distinction. A
COR-facing report could present unverified counts as authoritative.
*Correction:* Add an explicit intake-level authenticity classification
(`VERIFIED_AUTHORITATIVE_SOURCE` requiring all four elements: artifact + matching
hash + ingestion provenance + independently reconciled control count) defaulting to
`UNKNOWN`; propagate it to every report header.
*Test:* an intake without a control total can never reach
`VERIFIED_AUTHORITATIVE_SOURCE`; report rendering fails closed if classification is
absent.

### HIGH — relationship, provenance or reporting could be wrong

**GAP-H1 — Six fields have no specification.** `domains`, `initiatoronly`,
`stateofoperation`, `transaction`, `delegationRole`, `address_text`. Meaning is
correctly *not* inferred, but three of them are promoted into `rce_attributes`
under names that imply meaning. *Correction:* request item C; until then keep them
`rce_attributes`-only and never use them in a determination.

**GAP-H2 — Snapshot vs. delta is unproven.** Nothing establishes whether a delivery
is a full snapshot or an increment. Ingesting a delta as a snapshot would imply
mass deactivation; the reverse would strand stale records. *Correction:* request
item E before a second delivery is ever ingested. *Test:* two-delivery fixture
asserting the chosen semantics.

**GAP-H3 — Contact block cardinality is unresolved.** 17,775 populated
`contact_phone` values across **34 distinct** numbers. Promoting one contact row
per entity asserts a per-entity fact the source does not support. *Correction:*
design decision required — deferred pending specification.

**GAP-H4 — Multi-valued NPI cells.** `'1780787176, 1770559767'` occurs in the
artifact (1 row; 3 further malformed: 9, 9, and 6 digits). `NPI-002
MULTIPLE_NPI_IN_ONE_FIELD` correctly detects it, but the crosswalk to a single
`identifier_value` has no defined resolution, and `NPI` is the join key for NPPES,
PECOS and OIG. *Correction:* define whether a multi-valued cell yields two
identifier rows or is held for review. *Test:* fixture with a multi-NPI cell
asserting no silent truncation and no bad join.

### MEDIUM

**GAP-M1 — Zero width margin on two Area-3 columns.** Observed maxima exactly equal
declared widths: `address_state` max 2 → `state String(2)`; `address_postalCode`
max 10 → `zip String(10)`. Area 2 is wider (10 and 20), so **Area 2 → Area 3 is the
narrowing step.** Any longer value in the authoritative file raises `DataError`
mid-promotion or truncates. *Correction:* widen `state` to String(10) and `zip` to
String(20) to match Area 2, or add an explicit pre-promotion length check that
raises a named issue. *Test:* promotion fixture with an over-long state and ZIP.

**GAP-M2 — Drift is not itemised.** The operator learns the fingerprint differs but
not which columns were added, removed, renamed, or reordered. *Correction:* compute
and record the header set difference alongside the fingerprint. *Test:* assert
added/removed/reordered columns are enumerated in `source_metadata`.

**GAP-M3 — `expected_fields` is a dead parameter.** In `reader.read_delivery`,
`fields = expected_fields or RCE_FIELDS` is assigned and never used; headers always
come from the file. Runtime behaviour is *correct*, but a caller passing
`expected_fields` gets a silent no-op. *Correction:* remove the parameter, or use it
to produce the itemised diff of GAP-M2. *Test:* assert the parameter either affects
behaviour or does not exist.

### LOW

**GAP-L1 — Hardcoded profile statistics in user-facing rule text.** `FMT-001`'s
message states *"1,627 records in the profiled delivery show the same pattern"*.
Accurate for the artifact; becomes false the moment a different file is ingested.
*Correction:* compute per-run or drop the figure.

**GAP-L2 — One test-named row is unflagged.** `ELLKAY-DOA-TEST` is not marked
`is_test_record`, while 8 similarly named rows are. *Correction:* review the
test-detection predicate.

---

## 15. Required corrections before authoritative ingestion

| # | Gap | Correction | Blocking? |
| --- | --- | --- | --- |
| 1 | C3 | Intake-level authenticity classification, defaulting to `UNKNOWN`, propagated to reports | **YES** |
| 2 | C1 | Field-map provenance declaration; stop implying ONC origin | **YES** |
| 3 | M1 | Widen `state`/`zip` in Area 3, or add a pre-promotion length gate | **YES** |
| 4 | H4 | Define multi-valued NPI resolution | **YES** |
| 5 | C2 | Finalise `NAIC`/`CCN`/`alias`/`email`/`contact_company`/`transaction` mappings once samples arrive | **YES** |
| 6 | M2 | Itemised header diff | No |
| 7 | H2 | Snapshot-vs-delta semantics | Before **second** delivery |
| 8 | H3 | Contact cardinality design | No |
| 9 | M3 | Remove or use `expected_fields` | No |
| 10 | L1, L2 | Message hygiene; test-flag predicate | No |

**Nothing in the existing architecture needs redesign.** Area-1 preservation,
normalization authority, drift gating, and the enrichment boundary are all sound and
should be left alone (§12 of the request).

---

## 16. Required tests

1. Authoritative header vs. `EXPECTED_SCHEMA_FINGERPRINT` — exact comparison.
2. Unknown column survives intake and appears in `parsed` under its own name.
3. Drift raises `SCH-003` **and** `promotion.promote_delivery` raises.
4. Over-long `state` / `zip` is caught before promotion, not by `DataError`.
5. Multi-valued NPI produces a defined, non-truncating outcome.
6. Each of the 6 never-populated columns, populated, traverses intake → promotion.
7. Row count reconciles: file lines = `rce_source_records` = `record_count`.
8. `raw_line` reconstructs the delivered line byte-for-byte.
9. `FMT-001` pads without mutating Area 1, and writes a correction row.
10. Intake without a control total cannot reach `VERIFIED_AUTHORITATIVE_SOURCE`.
11. No enrichment writes to any `rce_*` column (structural assertion).
12. Short row conflating blank and absent is flagged `field_count_mismatch`.

---

## 17. Authoritative-ingestion prerequisites

1. Authoritative **header row** supplied and fingerprint-compared. *(user)*
2. **10–20 sample rows** including the 6 unpopulated columns. *(user)*
3. **Control total** from ONC. *(user)*
4. Snapshot-vs-delta confirmed. *(user)*
5. Corrections 1–5 of §15 implemented and tested. *(DocuAction)*
6. Authenticity classification set from evidence, not assertion. *(both)*
7. Ingestion executed through Phase-5 intake with drift gating active.
8. Post-ingestion reconciliation: file lines = stored rows = control total.

---

## 18. Final readiness checklist

| # | Item | Status |
| --- | --- | --- |
| 1 | Area-1 raw preservation | ✅ PASS |
| 2 | Unknown columns preserved | ✅ PASS |
| 3 | Malformed rows preserved, not positionally mapped | ✅ PASS |
| 4 | Row-count reconciliation | ✅ PASS |
| 5 | Schema fingerprint computed and stored | ✅ PASS |
| 6 | Drift blocks promotion | ✅ PASS |
| 7 | Drift itemised for the operator | ⚠️ PARTIAL |
| 8 | Correction authority + staleness guard | ✅ PASS |
| 9 | Enrichment boundary enforced structurally | ✅ PASS |
| 10 | Provenance: artifact, hash, actor, versions | ✅ PASS |
| 11 | Provenance: chain of custody | ❌ ABSENT |
| 12 | Provenance: independent control count | ❌ ABSENT |
| 13 | Authenticity classification recorded | ❌ ABSENT |
| 14 | Business key identified and unique | ✅ PASS (`id`) |
| 15 | Hierarchy proven | ✅ PASS |
| 16 | Contact cardinality resolved | ❌ UNRESOLVED |
| 17 | All 41 columns mapped | ⚠️ 35 of 41 |
| 18 | Column widths safe end-to-end | ⚠️ zero margin ×2 |
| 19 | Snapshot-vs-delta defined | ❌ UNKNOWN |
| 20 | Authoritative source **schema** confirmed | ✅ **VERIFIED** (user-supplied header, fingerprint + order match) |
| 21 | Dataset **lineage** file→database proven | ✅ PASS (byte-identical, hash match) |
| 22 | **Contractual authority** of the 23,566-row dataset | ❌ **UNKNOWN** — ~2,300 vs 23,566 unreconciled |

---

## 19. Stop gate

```
SOURCE SAMPLE RECORDS FOUND ............ 0 data ROWS pasted by the user, but the
                                         complete 41-column HEADER was supplied
                                         2026-08-21T17:03:13Z and verified.
                                         (Also: 1 Box artifact of 23,566 rows,
                                          contractual authority unresolved;
                                          41 provably synthetic mock entities;
                                          183 seed/demo DB entities)

SOURCE COLUMNS OBSERVED ................ 41  (user-supplied header, verified;
                                              artifact conforms to it)

COMPLETE AUTHORITATIVE HEADER AVAILABLE  YES   [corrected 2026-08-22]
  HEADER FINGERPRINT VERIFIED .......... YES   1cd655e9120dc9d0…
  SCHEMA ORDER VERIFIED ................ YES   zero positional differences

BACKEND FIELDS REVIEWED ................ 41 source specs
                                         + 32 rce_curated_records columns
                                         + 35 tefca_reg_entities columns
                                         + 41 supporting columns
                                         = 149

DATABASE TABLES REVIEWED ............... 24
                                         (2 Area-1, 3 processing, 3 Area-2,
                                          10 registry, 6 evidence/enrichment)

SOURCE COLUMNS
  MAPPED ............................... 33
  PARTIALLY MAPPED ..................... 1   (NPI — multi-valued cells)
  RAW ONLY ............................. 1   (domains)
  UNMAPPED ............................. 0
  AMBIGUOUS ............................ 1   (transaction)
  SOURCE_SCHEMA_NOT_CONFIRMED .......... 5   (NAIC, CCN, alias, email,
                                              contact_company)

RELATIONSHIPS
  VERIFIED ............................. 6
  AMBIGUOUS ............................ 1   (NPI → reference sources)
  UNRESOLVED ........................... 1   (contact block cardinality)
  MISSING .............................. 2   (NAIC, CCN)

INFORMATION-LOSS RISKS ................. 6
  1. Area2→Area3 narrowing, state String(10)→String(2), zero margin
  2. Area2→Area3 narrowing, zip String(20)→String(10), zero margin
  3. Multi-valued NPI cell → single identifier_value
  4. Short rows conflate "blank" with "absent" (mitigated: parse_status + raw_line)
  5. Five never-exercised mapping paths (NAIC, CCN, alias, email, contact_company)
  6. Upstream leading-zero loss in 1,627 entity ZIPs (6.90%) and 6,978 contact
     ZIPs (97.04%) — present IN the source, correctly detected by FMT-001,
     corrected AUTO_SAFE with the original preserved. Not a DocuAction defect.

SCHEMA-DRIFT PROTECTION ................ PARTIAL
    Detected, recorded, CRITICAL finding raised, promotion hard-blocked
    (promotion.py:145 raises). NOT itemised: the operator is told the header
    changed but not which columns.

READY TO ACCEPT FULL AUTHORITATIVE CSV . NO

EXACTLY WHAT IS NEEDED FROM USER
  A. The exact header row ................................... CLOSED — supplied
                                                              and verified
                                                              2026-08-21T17:03:13Z
  A'. Reconcile ~2,300 vs 23,566: is the Box artifact
     (sha256 689472073480b1cc…) the intended delivery, or
     does a ~2,300-record delivery exist elsewhere? ........ REQUIRED — sole
                                                              blocker to dataset
                                                              classification
  B. 10-20 representative rows, redacted as needed,
     deliberately including rows where NAIC, CCN, alias,
     email, contact_company or transaction are POPULATED ... REQUIRED
  C. Data dictionary / RCE specification, if one exists .... STRONGLY PREFERRED
  D. A control total (record count ONC states the file
     contains) + any transmittal or manifest ............... REQUIRED for COR use
  E. Whether deliveries are full snapshots or deltas ....... REQUIRED before a
                                                              second delivery

  A' alone may resolve dataset classification.
  DO NOT SEND THE FULL FILE YET.

DATA-CONTRACT DOCUMENT ................. docs/rce_authoritative_data_contract.md
```
