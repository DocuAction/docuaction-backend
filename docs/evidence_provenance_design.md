# MINIMUM EVIDENCE PROVENANCE — DESIGN

**Date:** 2026-08-22 · **Branch:** `fix/tefca-stabilization` · **Status:** DESIGN ONLY — nothing here is implemented.

**Scope.** The minimum provenance required *before* PPEF ingestion or an
Observation Store is built. **This is not the Observation Store.** It is the set of
facts without which an Observation Store would record more observations, each
equally unverifiable.

---

## 0. THE QUESTION THIS DESIGN MUST MAKE ANSWERABLE

> *"Why was Entity X classified B2 on 22 August 2026?"*

The answer must be reconstructable six months later:

```
Because:
  RCE said        [value]  (source record [id], delivery SHA-256 [hash])
  NPPES observed  [value]  on [date], dataset version [version]
  PECOS observed  [value]  from the Q3 2026 extract, file SHA-256 [hash],
                           enrollment [ENRLMT_ID], via [relationship]
  D1 = PASS · D2 = PASS · D3 = UNAVAILABLE · D4 = REVIEW · D5 = PASS · D6 = PASS
  RULE-003 matched (version 2)
  B2 determined by DocuAction
  Analyst [email] determined [result] on [date]
  QA [email] approved on [date]
```

**Today three links in that chain cannot be produced.** This design closes the
provenance links. Links 6 and 7 (analyst, QA) are closed by
`docs/qa_gate_design.md`.

---

## 1. GAP ANALYSIS AGAINST THE EXISTING TABLE

`tefca_dimension_evidence` has 29 columns and 1,984 rows. Measured coverage below
is the share of rows carrying a non-empty value, from the 43-entity run.

### 1.1 The seven core requirements

| # | Required | Existing column | Status | Measured reality |
|---|---|---|---|---|
| 1 | `source` | `source` | **COVERED** | 100%, and constrained — a canonical-source assertion refuses the ambiguous legacy `pecos` key |
| 2 | `source_dataset_version` | `dataset_version_anchor` | **PARTIAL — and misleading where populated** | CMS: the dataset UUID, correct. **NPPES: `"2.1"` — the API version, not a data version. OIG LEIE: the literal string `"CSV-UPDATED"`.** SAM: empty. Two of the three live sources record no data version at all |
| 3 | `source_as_of_date` | *(none)* | **MISSING** | `http_last_modified` exists but is explicitly labelled transport metadata — a CDN artefact, not the source's as-of date. Present on 70% of CMS rows, 0% elsewhere |
| 4 | `source_file_hash` | *(none)* | **MISSING ENTIRELY** | The table has **no hash column of any kind**. `original_values` is a shaped projection, not the raw response |
| 5 | `retrieved_at` | `retrieved_at` | **COVERED** | 100% across every source |
| 6 | `identifier_searched` | `query_identifier` | **PARTIAL** | CMS Enrollment 100%, OIG 100%, CMS Revocation 53%, **NPPES 0%, SAM 0%** |
| 7 | `observation_result` | `disposition` | **CONFLATED — see §1.3** | 100% populated, but with the wrong vocabulary |

**Two of seven covered. Three missing. Two partial.**

### 1.2 Extended fields

| Field | Existing | Status |
|---|---|---|
| `evidence_id` | `id` | COVERED |
| `entity_id` | `entity_id` | **PARTIAL — `VARCHAR(255)` with no foreign key.** Nothing prevents evidence orphaned from any entity |
| `dimension` | `evidence_dimension` | COVERED |
| `raw_observation_ref` | *(none)* | MISSING |
| `observation_hash` | *(none)* | MISSING |
| `match_method` | *(none)* | MISSING |
| `match_level` | *(none)* | MISSING |
| `match_version` | *(none)* | MISSING |
| `rule_version` | `rule_applied` | **MISSING.** `rule_applied` is a rule *name* string such as `NPPES_PRIMARY_IDENTITY_AUTHORITY`, not a version |
| `disposition` | `disposition` | COVERED |
| `correlation_id` | *(none)* | MISSING |

### 1.3 The conflation that must be fixed here, not later

`disposition` currently carries `PASS`, `NOT_FOUND`, `CORROBORATED`, `UNAVAILABLE`
— a **mixture of two vocabularies**. `PASS` is a judgement about a requirement;
`NOT_FOUND` is a fact about a lookup. Persisting them in one column means a future
Observation Store inherits the ambiguity in its source data.

**This design adds `observation_result` as a separate column** carrying only the
Layer-1 vocabulary defined in `docs/evidence_vocabulary_design.md`. `disposition`
keeps its current meaning and its 1,984 existing rows are untouched.

**This is the single most important structural point in this document.** Recording
what a source *said* separately from what DocuAction *concluded* is what makes the
same evidence re-interpretable under a revised methodology — which is exactly what
D1–D7 will require once answered.

---

## 2. EXTEND THE EXISTING TABLE, OR CREATE A NEW ONE?

### Option A — extend `tefca_dimension_evidence`

| Pros | Cons |
|---|---|
| The table is already append-only by contract and generation-stamped | Grows to ~40 columns, mixing dimension interpretation with source provenance |
| 1,984 rows keep their meaning; every new column is nullable, so historical rows stay valid and visibly incomplete | Source-version facts are repeated on every row that used that source — for one NPPES lookup feeding D1 and D4, the same version is written twice |
| No join for the common read | Cannot represent a source version that was retrieved once and used by many observations |
| Zero code churn for existing readers | |

### Option B — a new `evidence_provenance` table, 1:1 with each evidence row

| Pros | Cons |
|---|---|
| Clean separation | A 1:1 table is a column group with extra steps. It solves nothing that nullable columns do not |
| | Every read joins; the reconciliation gate gets slower for no gain |

### Option C — extend the evidence table **and** add a source-version table (**RECOMMENDED**)

Two changes, each doing one thing:

1. **`source_version_snapshots`** — one row per (source, version) actually
   consulted. Holds the version, the as-of date, the file hash, the row count and
   where the bytes live. Written once per retrieval, referenced by many
   observations.
2. **Additive columns on `tefca_dimension_evidence`** — a foreign key to the
   version row, the observation vocabulary, the match provenance, and the
   correlation id.

| Pros | Cons |
|---|---|
| A source version is stated **once** and cannot drift between the rows that cite it | Two migrations rather than one |
| Directly models the reality: PPEF Q3 2026 is one artefact consulted by thousands of observations | Requires a write path at retrieval time, not only at assembly time |
| Mirrors the proven `tefca_ppef_snapshots` pattern, which already records file name, resource version, SHA-256, schema fields and record count | |
| Makes "which entities were judged against the superseded LEIE edition?" a query | |

---

## 3. PROPOSED SCHEMA

### 3.1 `source_version_snapshots` — NEW

```
source_version_snapshots                      APPEND-ONLY
  id                    UUID PK
  source                VARCHAR(40)  NOT NULL   -- NPPES | CMS_PPEF_ENROLLMENT |
                                                -- CMS_PPEF_PRACTICE_LOCATION |
                                                -- CMS_PPEF_REASSIGNMENT |
                                                -- CMS_PPEF_SECONDARY_SPECIALTY |
                                                -- CMS_PPEF_ADDITIONAL_NPIS |
                                                -- CMS_REVOCATION | OIG_LEIE |
                                                -- SAM_GOV | USPS | ONC_RCE
  version_label         VARCHAR(120) NOT NULL   -- 'PPEF Q3 2026 v2026.07.17'
                                                -- 'LEIE 2026-08-01'
  source_as_of          DATE                    -- the SOURCE's own as-of date
  source_file_hash      CHAR(64)                -- SHA-256 of the retrieved artefact
  dataset_identifier    VARCHAR(120)            -- CMS dataset UUID, file_uuid, etc.
  record_count          BIGINT
  retrieved_at          TIMESTAMPTZ  NOT NULL
  http_last_modified    VARCHAR(64)             -- transport metadata, labelled
  storage_uri           TEXT                    -- where the bytes are preserved
  retrieval_method      VARCHAR(20)  NOT NULL   -- API | DOWNLOAD | LOCAL_SNAPSHOT
  is_point_in_time      BOOLEAN      NOT NULL   -- FALSE for a live API with no
                                                -- preserved copy: an honest flag
  note                  TEXT
  created_at            TIMESTAMPTZ  NOT NULL DEFAULT now()

  UNIQUE (source, version_label, source_file_hash)
  INDEX  (source, retrieved_at DESC)
```

**`is_point_in_time` is deliberate.** A live NPPES lookup with no preserved copy is
**not** reproducible, and the row must say so rather than implying otherwise by
carrying a version string. It is the field that distinguishes "we know exactly what
we read" from "we know when we read it and nothing more".

### 3.2 `tefca_dimension_evidence` — ADDITIVE COLUMNS

```
  source_version_id        UUID  FK -> source_version_snapshots.id   -- CORE 2,3,4
  observation_result       VARCHAR(24)     -- CORE 7, Layer-1 vocabulary (B5)
  identifier_searched      VARCHAR(200)    -- CORE 6, lifted out of query_identifier
  identifier_type          VARCHAR(24)     -- npi | tefcaid | hcid | uei | org_name |
                                           -- enrollment_id | pac_id | address
  raw_observation_ref      TEXT            -- URI of the preserved raw response
  observation_hash         CHAR(64)        -- SHA-256 of the canonicalised raw response
  match_method             VARCHAR(20)     -- exact | structured | fuzzy | none
  match_level              SMALLINT        -- 1..4
  match_version            VARCHAR(20)     -- matcher algorithm version
  rule_version             VARCHAR(20)     -- version of the rule in `rule_applied`
  correlation_id           UUID            -- links every observation of one run
```

All nullable. Existing rows stay valid and are visibly incomplete — which is the
correct representation of history, not a defect to be backfilled. **Backfilling
`source_version_id` for the 1,984 existing rows is not possible and must not be
attempted:** the NPPES and LEIE editions consulted on 2026-08-21 were not recorded
and cannot be recovered.

### 3.3 PPEF relational provenance — `evidence_relationship_path` — NEW

**The requirement: PPEF evidence must not be flattened to "NPI → enrolled / not
enrolled."**

Today the PPEF identifiers exist **only inside `original_values` JSONB**, unindexed
and unqueryable. `PECOS_ASCT_CNTL_ID` has no column anywhere. The traversal that
produced a piece of evidence cannot be stated.

```
evidence_relationship_path                    APPEND-ONLY
  id                       UUID PK
  evidence_id              UUID NOT NULL FK -> tefca_dimension_evidence.id
  hop_sequence             SMALLINT NOT NULL   -- 1,2,3… the traversal, ordered

  from_identifier_type     VARCHAR(30) NOT NULL  -- npi | pac_id | enrollment_id
  from_identifier_value    VARCHAR(60) NOT NULL
  relationship_type        VARCHAR(40) NOT NULL  -- enrolled_as
                                                 -- has_practice_location
                                                 -- has_secondary_specialty
                                                 -- has_additional_npi
                                                 -- reassigns_benefits_to
  to_identifier_type       VARCHAR(30)
  to_identifier_value      VARCHAR(60)

  ppef_component           VARCHAR(40)         -- which sub-file supplied this hop
  source_row_key           VARCHAR(120)        -- the key of the row that supplied it
  source_version_id        UUID FK -> source_version_snapshots.id

  UNIQUE (evidence_id, hop_sequence)
  INDEX  (from_identifier_type, from_identifier_value)
  INDEX  (to_identifier_type, to_identifier_value)
```

**The traversal this preserves:**

```
TEFCA Entity
   | NPI
   v
PPEF ENROLLMENT ------ PAC_ID identifies the enrolling provider and may be
   | ENRLMT_ID         associated with MULTIPLE enrollment ids. One row per
   |                   enrollment; the entity is not collapsed to one.
   +-- has_practice_location   --> ADDRESS      (Practice Location sub-file)
   +-- has_secondary_specialty --> TAXONOMY     (Secondary Specialty sub-file)
   +-- has_additional_npi      --> NPI          (Additional NPIs sub-file)
   +-- reassigns_benefits_to   --> ENRLMT_ID    (Reassignment sub-file)
            |                     from = REASGN_BNFT_ENRLMT_ID  (the practitioner)
            v                     to   = RCV_BNFT_ENRLMT_ID     (the receiver)
       RECEIVING ENROLLMENT
```

**Why hops rather than columns.** A fixed column set forces one enrollment, one
location, one specialty. The delivered reality is one-to-many at every level:
a provider may hold several enrollments, an enrollment several practice locations.
An ordered hop list represents all of them without flattening, and makes the
reconstruction sentence *"via enrollment I20040309000221, reassignment to
I20051212000388, from the Q3 2026 Reassignment extract, file SHA-256 abc…"*
a query rather than a JSONB scan.

**Why `source_version_id` is on every hop.** Different PPEF components are
different files with different hashes. A single evidence item can legitimately
traverse two components, and each hop must name the artefact that supplied it.

**CMS publishes PPEF as current enrollment information only** — it contains no
historical enrollment data. When the next quarter publishes, the rows behind a
determination are gone from the source. **The version, the hash and the retrieval
timestamp are therefore not metadata; they are the only thing that will remain.**

### 3.4 What is deliberately NOT in this design

| Excluded | Why |
|---|---|
| A general observation graph with temporal validity intervals | That is the Observation Store. This design records provenance for the observations already being made |
| Bitemporal `valid_from` / `valid_to` | Most sources publish no such date. Adding the axis before a source supplies it invents data |
| A matching service | Only the *provenance* of a match is recorded here (`match_method`, `match_level`, `match_version`). Producing those values is separate work |
| Backfill of historical rows | Not possible for NPPES or LEIE, and fabricating a version would be worse than a null |

---

## 4. WHAT EACH CORE REQUIREMENT COSTS

| # | Requirement | Where it lands | Producer change |
|---|---|---|---|
| 1 | source | already covered | none |
| 2 | source_dataset_version | `source_version_snapshots.version_label` | connector records the version at retrieval |
| 3 | source_as_of_date | `source_version_snapshots.source_as_of` | connector; null where the source publishes none |
| 4 | source_file_hash | `source_version_snapshots.source_file_hash` | **LEIE is the cheapest and highest-value win — the downloaded bytes are already in memory and are currently discarded unhashed** |
| 5 | retrieved_at | already covered | none |
| 6 | identifier_searched | `identifier_searched` + `identifier_type` | populate on all branches, **including the UNAVAILABLE branch**, which today writes none |
| 7 | observation_result | `observation_result` | evidence assembly emits the Layer-1 value alongside the disposition |

---

## 5. MIGRATION COMPLEXITY

| Change | Complexity | Reason |
|---|---|---|
| `source_version_snapshots` (new table) | **LOW** | Additive; nothing reads it until written |
| 11 additive nullable columns on `tefca_dimension_evidence` | **LOW** | No default, no rewrite, no lock of consequence on 1,984 rows |
| `evidence_relationship_path` (new table) | **LOW** | Additive; populated only when PPEF is ingested |
| Connector changes to record a version at retrieval | **MEDIUM** | Six connectors; each records its own version differently, and two publish none |
| Evidence assembly emits `observation_result` | **MEDIUM** | Touches all six dimension assemblers; must not change any existing `disposition` |
| Populating provenance on the UNAVAILABLE branch | **LOW** | ~6 lines, one place |
| Backfill | **NOT ATTEMPTED** | Historical source versions are unrecoverable. Existing rows keep null and are honestly incomplete |

**No existing column changes type or meaning. No data is rewritten.**

### 5.1 LOC estimate

| Work item | Production | Test |
|---|---|---|
| Migration: `source_version_snapshots` | 70 | 40 |
| Migration: 11 additive columns | 55 | 30 |
| Migration: `evidence_relationship_path` | 75 | 45 |
| Version capture in 6 connectors | 160 | 170 |
| LEIE edition hashing + move cache out of the per-process dict | 65 | 60 |
| `observation_result` emission across 6 assemblers | 120 | 140 |
| `identifier_searched` / `identifier_type`, all branches incl. UNAVAILABLE | 45 | 55 |
| `correlation_id` threading, intake → report | 80 | 70 |
| PPEF hop recording (inert until PPEF is ingested) | 95 | 90 |
| Reconstruction endpoint — the query that answers the six-month question | 90 | 85 |
| **TOTAL** | **~855** | **~785** |

**The first four rows (~350 production LOC) close the reproducibility break on
their own.** The rest is completeness.

---

## 6. PRIORITY

| Priority | Item | Rationale |
|---|---|---|
| **1** | `source_version_snapshots` + LEIE hashing | Closes the break for the one source where the bytes are already in hand and being thrown away |
| **2** | `observation_hash` + `raw_observation_ref` | Turns "the source said X" from an assertion into a verifiable fact |
| **3** | Provenance on the UNAVAILABLE branch | 172 SAM rows carry no timestamp and no attempted identifier. Six lines |
| **4** | `observation_result` column | Must exist **before** an Observation Store, or the ambiguity is inherited at scale |
| **5** | `evidence_relationship_path` | Inert until PPEF is ingested — but must exist **before**, or the first ingestion flattens |
| **6** | `correlation_id` | Joins the chain by key rather than by inference |

---

## 7. DEPENDENCIES AND RISKS

**Dependencies**

- **Independent of D1–D7.** Provenance records what was observed; it does not
  decide what an observation means.
- **Depends on `docs/evidence_vocabulary_design.md`** for the `observation_result`
  vocabulary. That vocabulary must be settled first or the column is filled with
  ad-hoc values.
- **Should precede** PPEF ingestion, NPPES bulk loading, and the Observation Store.
- **Complements** `docs/area1_immutability_design.md` — Layer 1 there and
  `source_version_snapshots` here are the same pattern applied to inbound and
  outbound evidence respectively, and should share a storage backend.

**Risks**

| Risk | Severity | Mitigation |
|---|---|---|
| Version capture is added to connectors but two sources publish no version | HIGH | `is_point_in_time = FALSE` states it explicitly. **Do not synthesise a version from a retrieval date** — that would imply reproducibility that does not exist |
| Raw-response storage grows without bound | MEDIUM | Store raw responses in Blob keyed by `observation_hash`; identical responses deduplicate naturally. The database holds the hash and the URI |
| `observation_result` and `disposition` drift | MEDIUM | A contract test asserts the mapping is total and one-directional (see B5 §C) |
| PPEF hop table is built and PPEF is never ingested | LOW | It is additive and inert. The cost of having it unused is far below the cost of ingesting 200 MB of relational sub-files with nowhere to record the traversal |
| Effort is spent on provenance while the reproducibility chain still breaks at links 6 and 7 | MEDIUM | Sequence alongside `docs/qa_gate_design.md`, which closes those two links and is also unblocked |
