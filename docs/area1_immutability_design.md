# AREA 1 DURABLE IMMUTABLE STORAGE — DESIGN

**Date:** 2026-08-22 · **Branch:** `fix/tefca-stabilization` · **Status:** DESIGN ONLY — nothing here is implemented.

---

## 0. THE THREE PROBLEMS THIS DESIGN ADDRESSES

| # | Problem | Evidence |
|---|---|---|
| 1 | Area 1 database immutability reports `enforced: false` | Reconciliation check, run 2026-08-22: the application role holds UPDATE, DELETE and TRUNCATE on both Area 1 tables |
| 2 | The emitted REVOKE would break promotion | `promotion.py:338-341` legitimately UPDATEs `rce_source_records.promotion_status`. A blanket revoke fails mid-transaction, after entities, identifiers and contacts are already committed |
| 3 | The ONC delivery exists only on local disk | 10,042,400 bytes at an absolute Windows path recorded in `rce_source_intakes.storage_path`. Not in version control, not in Blob Storage. A redeploy or a lost workstation makes the recorded SHA-256 unverifiable |

**A fourth problem, discovered during this design and not previously reported:**
`docuaction` is the **table owner** of both Area 1 tables. A PostgreSQL owner may
re-`GRANT` to itself at any time, and `ALTER`/`DROP`/ownership transfer are
inherent to ownership and cannot be revoked. **A REVOKE against the owner is a
guard against accident, not a control against intent.** Ownership must move off
the application role or none of the options below is a real control.

---

## 1. THREE STORAGE LAYERS

```
LAYER 1 — ONC DELIVERY OBJECT                                 immutable
  the original bytes, byte-for-byte
  SHA-256 · original filename · content type · received_at · received_by
  durable object storage, legal hold / immutability policy
        |
        |  1 : N
        v
LAYER 2 — AREA 1 SOURCE RECORD                                immutable
  raw_line · parsed · record_sha256 · line_number · field_count · parse_status
  lifted identifiers (rce_id, tefcaid, hcid, npi) for indexing
  no UPDATE, no DELETE by the application role
        |
        |  1 : 1
        v
LAYER 3 — WORKFLOW STATE                                      mutable
  promotion_status · canonical_entity_id · processing_state
  reviewed_by · reviewed_at
  application UPDATEs freely; no DELETE
```

Layer 1 does not exist today as a first-class object — it is a filesystem path in
a column. Layers 2 and 3 exist but are **mixed in one table**.

---

## 2. PART A — SHOULD IMMUTABLE EVIDENCE AND MUTABLE STATE BE SPLIT?

### 2.1 What is actually mixed

`rce_source_records` has 16 columns. Fourteen are immutable evidence; **two** are
mutable workflow state.

| Class | Columns | Written when |
|---|---|---|
| **IMMUTABLE (14)** | `id`, `source_intake_id`, `line_number`, `raw_line`, `parsed`, `record_sha256`, `source_rce_id`, `tefcaid`, `hcid`, `npi`, `field_count`, `parse_status`, `parse_note`, `created_at` | INSERT only |
| **MUTABLE (2)** | `promotion_status`, `canonical_entity_id` | INSERT, then UPDATE once at promotion |

`rce_source_intakes` is **not** mixed — all 22 columns are set at INSERT.
`status` is assigned in the constructor (`intake.py:120,168`); the declared value
`PROFILED` is never reached by any code path. It needs no change.

### 2.2 Blast radius of a split

Seven production modules reference the two mutable columns:

| File | Role |
|---|---|
| `rce/models.py` | column definitions, index `idx_rce_record_intake_status` |
| `rce/intake.py` | sets `promotion_status="pending"` at INSERT |
| `rce/promotion.py` | the only writer of both, in `BATCH_SIZE=1000` chunks |
| `rce/repository.py` | `list_source_records(promotion_status=...)` filter |
| `rce/reconciliation.py` | two of the eighteen checks |
| `rce/routes.py` | query parameter + response serialisation |
| `rce/curation.py` | reads `canonical_entity_id` |

Plus `tests/test_rce_pipeline.py`. Each read becomes a join; each write moves to
the state table.

### 2.3 Assessment

**Is the split architecturally justified?** *In principle yes; in practice the
justification is weaker than it first appears.*

The argument for splitting is that two columns out of sixteen are the sole reason
the strongest guarantee in the system cannot be enforced at the database. That is
a real asymmetry.

The argument against is that **PostgreSQL column-level privileges already provide
exactly the required enforcement without moving anything.** `GRANT UPDATE
(promotion_status, canonical_entity_id)` permits precisely the two writes that
occur and refuses every other column. The privilege check is performed on the
columns named in the UPDATE statement, so a statement touching `raw_line` is
rejected regardless of intent.

**Does it simplify or complicate?** It **complicates**, on current evidence:

- 7 production modules and 1 test module change, for a control already
  obtainable with two SQL statements and no code change.
- Every read of promotion state becomes a join, including inside the
  reconciliation gate — the one place where query simplicity has audit value.
- The 1:1 relationship must be maintained by application code or by a trigger.
  A missing state row is a new failure mode that does not exist today.
- 23,566 rows must be migrated, and reconciliation must be re-proven afterwards.

**Is the current mixed design ACCEPTABLE with column-level controls?**

**Yes — with three conditions**, all of which must hold or the control is nominal:

| # | Condition | Why |
|---|---|---|
| 1 | **Table ownership moves off the application role** | An owner can re-grant to itself. Without this, no revoke is a control |
| 2 | **`verify_immutable()` is extended to probe column privileges** | It currently calls `has_table_privilege(role, table, 'UPDATE')`, which returns **true** when *any* column is grantable. It would report the correct configuration as unenforced, and cannot distinguish it from no enforcement at all |
| 3 | **A test pins the ORM flush shape** | SQLAlchemy emits an UPDATE naming only dirty attributes, so `record.promotion_status = "promoted"` names two columns and passes. Any future code path that loads and re-flushes a whole object would name all sixteen and be refused. This must fail in CI, not in production |

### 2.4 RECOMMENDATION

**Adopt column-level grants now. Defer the table split.**

Record the split as the target architecture for the point at which workflow state
grows beyond two columns — the QA gate (B2) and the analyst queue will add
`processing_state`, `reviewed_by`, `reviewed_at` and possibly a claim lock. When
mutable state reaches five or six columns the balance reverses, and the split
should be revisited at that point rather than pre-emptively.

**Trigger condition for revisiting:** any change that adds a third mutable column
to `rce_source_records`.

---

## 3. PART B — GRANT / REVOKE DESIGN

### 3.1 Roles

| Role | Purpose | Login | Used by |
|---|---|---|---|
| `docuaction_owner` | Owns Area 1 objects. DDL only | **no** | migrations, run by a human or CI with an explicit credential |
| `docuaction` | The application role | yes | the running application. Named in `DATABASE_URL` |
| `docuaction_breakglass` | Emergency Area 1 correction | yes | **never the application** — no Key Vault reference reachable from App Service, no App Service setting |
| `docuaction_readonly` | Reporting / investigation | yes | analysts, this kind of investigation |

### 3.2 Recommended architecture (column-level, current tables)

```sql
-- Prerequisite. Without this the revoke below is self-reversible.
ALTER TABLE rce_source_intakes OWNER TO docuaction_owner;
ALTER TABLE rce_source_records OWNER TO docuaction_owner;

-- LAYER 1 metadata: fully immutable. Nothing updates it.
REVOKE UPDATE, DELETE, TRUNCATE ON rce_source_intakes FROM docuaction;
GRANT  SELECT, INSERT            ON rce_source_intakes TO   docuaction;

-- LAYER 2 + 3 mixed table: table-wide UPDATE revoked, then re-granted on
-- exactly the two workflow columns. `raw_line`, `parsed`, `record_sha256` and
-- every other evidence column become unwritable by the application.
REVOKE UPDATE, DELETE, TRUNCATE  ON rce_source_records FROM docuaction;
GRANT  SELECT, INSERT            ON rce_source_records TO   docuaction;
GRANT  UPDATE (promotion_status, canonical_entity_id)
                                 ON rce_source_records TO   docuaction;
```

**Effect:** `promotion.py:338-341` continues to work byte-for-byte unchanged. Any
statement naming an evidence column is refused by the database.

### 3.3 Target architecture (after a future split)

```sql
ALTER TABLE rce_source_evidence   OWNER TO docuaction_owner;
ALTER TABLE rce_source_processing OWNER TO docuaction_owner;

REVOKE UPDATE, DELETE, TRUNCATE ON rce_source_evidence   FROM docuaction;
GRANT  SELECT, INSERT           ON rce_source_evidence   TO   docuaction;

GRANT  SELECT, INSERT, UPDATE   ON rce_source_processing TO   docuaction;
REVOKE DELETE, TRUNCATE         ON rce_source_processing FROM docuaction;
```

Simpler to state and to audit — which is the split's real benefit, and why it
remains the target rather than being discarded.

### 3.4 Break-glass role

```sql
CREATE ROLE docuaction_breakglass LOGIN
  PASSWORD :'breakglass_pw'
  CONNECTION LIMIT 2
  VALID UNTIL 'infinity';

GRANT SELECT, INSERT, UPDATE, DELETE
  ON rce_source_intakes, rce_source_records TO docuaction_breakglass;

-- Mandatory audit. pgaudit is the ONLY mechanism that captures a direct psql
-- session; an application-layer log cannot see one.
ALTER ROLE docuaction_breakglass SET pgaudit.log = 'write,ddl';
ALTER ROLE docuaction_breakglass SET pgaudit.log_level = 'log';
ALTER ROLE docuaction_breakglass SET log_statement = 'all';
ALTER ROLE docuaction_breakglass SET log_min_duration_statement = 0;
ALTER ROLE docuaction_breakglass SET application_name = 'AREA1-BREAKGLASS';

-- The application must never be able to become this role.
REVOKE docuaction_breakglass FROM docuaction;
```

**Azure prerequisite:** `pgaudit` must be added to `azure.extensions` and
`shared_preload_libraries` in `infra/modules/postgresql.bicep`. **Without pgaudit
the break-glass path is unaudited and the control is nominal.**

### 3.5 Audit trigger — defence in depth

pgaudit records that a mutation happened, in the server log. A trigger records
*what changed*, in the database, where the reconciliation gate can read it.

```sql
CREATE TABLE area1_mutation_log (
  id              BIGSERIAL PRIMARY KEY,
  occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  db_role         TEXT        NOT NULL DEFAULT current_user,
  application     TEXT                 DEFAULT current_setting('application_name', true),
  table_name      TEXT        NOT NULL,
  operation       TEXT        NOT NULL,          -- UPDATE | DELETE
  row_id          UUID,
  before_image    JSONB       NOT NULL,
  after_image     JSONB,
  justification   TEXT                            -- set via SET LOCAL area1.justification
);

CREATE FUNCTION area1_log_mutation() RETURNS trigger AS $$
BEGIN
  INSERT INTO area1_mutation_log
    (table_name, operation, row_id, before_image, after_image, justification)
  VALUES (TG_TABLE_NAME, TG_OP, OLD.id, to_jsonb(OLD),
          CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END,
          current_setting('area1.justification', true));
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_area1_intake_mutation
  BEFORE UPDATE OR DELETE ON rce_source_intakes
  FOR EACH ROW EXECUTE FUNCTION area1_log_mutation();

CREATE TRIGGER trg_area1_record_mutation
  BEFORE UPDATE OR DELETE ON rce_source_records
  FOR EACH ROW EXECUTE FUNCTION area1_log_mutation();
```

**Design note.** The trigger fires on the promotion UPDATE too. That is deliberate
— two writes per delivery against 23,562 rows is a known, bounded cost, and a
trigger with an exception for the application role would be a trigger that stops
recording exactly when the application misbehaves. If the volume proves
unacceptable, exclude by *column list* (`WHEN (OLD.raw_line IS DISTINCT FROM
NEW.raw_line OR ...)`) rather than by role.

`area1_mutation_log` must itself be owned by `docuaction_owner` with only INSERT
granted to everyone, and no UPDATE or DELETE granted to anyone.

---

## 4. PART C — DURABLE STORAGE FOR THE ONC DELIVERY

### Option A — PostgreSQL, bytes in the database

Store the delivery as `BYTEA` (or a large object) in a dedicated table alongside
its metadata.

| Pros | Cons |
|---|---|
| One system, one backup, one restore, one set of permissions | 10 MB per delivery in a **32 GB Burstable B1ms** instance already holding 258 MB |
| Transactional — bytes and metadata commit together | TOAST compression makes byte-for-byte retrieval a decompression round-trip; the SHA-256 must be re-verified on every read to be meaningful |
| Immutability enforced by the same grants as everything else | Every backup and every restore carries the full delivery corpus. Quarterly deliveries make this grow without bound on a tier that cannot absorb it |
| No new service, no new credential | pg_dump size grows linearly; restore time grows with it |

### Option B — Azure Blob Storage with immutability policy

Dedicated container in a dedicated storage account, **time-based retention with
legal-hold capability (WORM)**, metadata in PostgreSQL with the URI.

| Pros | Cons |
|---|---|
| **Genuine WORM.** An immutability policy prevents deletion or modification *including by the storage account owner* for the retention period — the one control PostgreSQL ownership cannot provide | Bytes and metadata commit separately; a failure between the two must be reconciled |
| Purpose-built for large immutable objects; cost per GB roughly two orders of magnitude below Premium SSD database storage | New service, new managed-identity grant, new failure mode (Blob unreachable at retrieval) |
| Independent lifecycle from the database — a database restore to an earlier point does not resurrect or destroy deliveries | Requires `infra/modules/` changes and a storage account that does not yet exist |
| Versioning + soft delete give a second layer beneath the immutability policy | Local development needs Azurite or a filesystem fallback |

### Option C — Hybrid: bytes in Blob, metadata and parsed records in PostgreSQL

| Pros | Cons |
|---|---|
| Each store holds what it is good at: Blob for immutable bulk, PostgreSQL for indexed query | Two-phase write |
| The 23,566 parsed rows stay queryable and joinable; only the 10 MB original moves | Retrieval path must handle Blob being unavailable |
| The SHA-256 already recorded in `rce_source_intakes` becomes the integrity link across the two stores | Two systems to permission and audit |

### 4.1 RECOMMENDATION — **Option C**

Option C *is* Option B plus the explicit statement that parsed records stay in
PostgreSQL. It is recommended because:

1. **Only Option B/C provides WORM.** PostgreSQL immutability rests on privilege
   revocation, and privileges can be re-granted by an owner or a superuser. An
   Azure immutability policy with legal hold cannot be lifted by the storage
   account owner during the retention period. For evidence supporting federal
   determinations, that difference is the whole point.
2. **The B1ms tier cannot absorb Option A.** 32 GB total, 258 MB used, quarterly
   10 MB deliveries plus their backups — survivable for a year, then not, on an
   instance that already cannot hold bulk NPPES.
3. **The pattern is already proven in this codebase.** `tefca_ppef_snapshots`
   records file name, resource version, SHA-256, schema fields and record count
   for downloaded CMS files. Layer 1 is the same shape applied to the ONC
   delivery.
4. **It preserves the reconciliation gate unchanged.** The gate reads parsed rows
   and hashes; both stay in PostgreSQL.

### 4.2 Layer 1 object contract

| Requirement | How it is satisfied |
|---|---|
| Original bytes preserved byte-for-byte | Blob block upload of the exact request body, before any decode or parse — the ordering `intake.py` already uses |
| SHA-256 verified on retrieval | Computed on download and compared to `rce_source_intakes.sha256`; a mismatch raises rather than returning bytes |
| Original filename | `rce_source_intakes.original_filename` (exists) |
| Content type | **NEW column** `content_type` — the multipart part's declared type |
| Received timestamp | `rce_source_intakes.received_at` (exists) |
| Storage path / URI | `storage_path` repurposed to a `https://…/{container}/{blob}` URI, plus **NEW** `storage_backend`, `storage_etag`, `storage_immutability_policy_until` |
| Retrievable by `intake_id` | `GET /api/tefca/rce/deliveries/{intake_id}/original` → read URI from the intake row → download → verify → stream |

### 4.3 Proposed Layer 1 columns (additive; no existing column changes meaning except `storage_path`)

```
rce_source_intakes  (additions)
  storage_backend                    VARCHAR(20)   -- 'local' | 'azure_blob'
  content_type                       VARCHAR(120)
  storage_etag                       VARCHAR(80)
  storage_immutability_policy_until  TIMESTAMPTZ
  storage_verified_at                TIMESTAMPTZ   -- last successful hash re-verification
```

`storage_backend` is what makes the migration safe: existing rows stay `'local'`
and continue to resolve through the filesystem path, while new deliveries are
`'azure_blob'`. No historical row is rewritten — which matters, because rewriting
`storage_path` on an existing intake is itself an Area 1 write.

### 4.4 Infrastructure

| Resource | Setting |
|---|---|
| Storage account | dedicated, e.g. `stdocuactionevidence{env}` — not shared with application uploads |
| Redundancy | GRS minimum; the delivery is not reproducible from any other source |
| Container | `onc-deliveries`, private, no anonymous access |
| Immutability | time-based retention policy, **locked**, duration per the contract's records-retention requirement (AGT to confirm — `AGT-DRP-007_Data_Retention_Policy` governs) |
| Versioning + soft delete | enabled |
| Access | App Service managed identity, **Storage Blob Data Contributor scoped to the container** — write and read, never delete |
| Key Vault | no account key stored; managed identity only |

---

## 5. FUTURE IMPLEMENTATION ACCEPTANCE CRITERIA — HOW EACH WILL BE TESTED

**Defined here. Not executed. No criterion below has been run.**

| # | Criterion | Test design | Type |
|---|---|---|---|
| 1 | Area 1 DB immutability enforced: TRUE | Extend `verify_immutable()` to call `has_column_privilege(role, table, column, 'UPDATE')` for **every** column, then assert: UPDATE granted on exactly `{promotion_status, canonical_entity_id}` and on no other column; DELETE and TRUNCATE granted on neither table; `enforced` true. Assert the reconciliation gate reports it | integration, DB required |
| 2 | ONC delivery in durable controlled storage | Assert `storage_backend = 'azure_blob'` and `storage_immutability_policy_until` is in the future for every intake created after cutover | integration |
| 3 | SHA-256 linked to intake record | Assert `sha256` non-null, 64 hex characters, on every intake — already true; pin it | unit |
| 4 | Retrieval by `intake_id` demonstrated | `GET /deliveries/{intake_id}/original` returns 200, `Content-Length` equal to `file_size_bytes`, `Content-Type` equal to `content_type` | API integration |
| 5 | Retrieved bytes reproduce the original SHA-256 | Download through the retrieval path, hash the received bytes, assert equality with `rce_source_intakes.sha256`. **Hash the bytes the client receives, not the bytes the server read** — that is what proves the whole path | integration |
| 6 | Application role cannot UPDATE/DELETE immutable evidence | Connect **as `docuaction`** and attempt `UPDATE rce_source_records SET raw_line = ...`; assert `InsufficientPrivilege`. Repeat for `parsed`, `record_sha256`, and for `DELETE`. Then assert `UPDATE … SET promotion_status` **succeeds** — a test that only proves refusal would pass on a broken database | integration, DB required |
| 7 | Application role can update workflow state | Run `promote_delivery` end-to-end as `docuaction` against a seeded intake; assert the promotion markers are written and reconciliation check *"Area 1 promotion markers agree with Area 2"* passes | integration, DB required |
| 8 | Break-glass mutation generates an audit event | Connect as `docuaction_breakglass`, `SET LOCAL area1.justification`, UPDATE one row, assert exactly one `area1_mutation_log` row with correct `db_role`, `before_image`, `after_image` and justification. Then assert the same UPDATE as `docuaction` is refused | integration, DB required |

**One additional criterion AGT recommends adding**, because it is the criterion
that catches the failure mode most likely to occur in practice:

| 9 | ORM flush shape is pinned | Capture the SQL emitted by the promotion write and assert the UPDATE statement names **only** the two permitted columns. This fails in CI if a future refactor causes a whole-object flush, rather than failing in production against a hardened database | unit, SQL capture |

---

## 6. LOC ESTIMATE

| Work item | Production | Test | Infra |
|---|---|---|---|
| Extend `verify_immutable()` to column-level | 45 | 60 | — |
| Grants + ownership migration (SQL, reviewed by DBA) | 30 | 40 | — |
| `area1_mutation_log` + trigger + function | 60 | 55 | — |
| Break-glass role + pgaudit enablement | — | 35 | 40 (Bicep) |
| Blob storage account, container, immutability policy, MI grant | — | — | 120 (Bicep) |
| Layer 1: 5 additive columns + migration | 55 | 30 | — |
| Blob write path in `intake.preserve_original`, with `storage_backend` branch | 90 | 80 | — |
| Retrieval endpoint with hash verification | 70 | 85 | — |
| ORM flush-shape test | — | 40 | — |
| **TOTAL** | **~350** | **~425** | **~160** |

Deliberately excluded: the table split (a further ~400 production LOC across 7
modules plus a 23,566-row data migration), deferred per §2.4.

---

## 7. RISKS

| Risk | Severity | Mitigation |
|---|---|---|
| Ownership transfer is applied but the app role is later re-granted UPDATE by a well-meaning DBA | HIGH | Criterion 1 runs in CI against the deployed database, not only at migration time |
| pgaudit unavailable or not enabled on Azure Flexible Server | HIGH | Verify extension availability **before** creating the break-glass role. Without it, do not create the role — an unaudited break-glass path is worse than none |
| Blob immutability policy locked with the wrong retention period | HIGH | A **locked** time-based policy cannot be shortened. Confirm the retention requirement against `AGT-DRP-007` and the contract before locking; use an unlocked policy in dev |
| Two-phase write leaves bytes in Blob with no intake row, or an intake row with no bytes | MEDIUM | Write bytes first, then the intake row — an orphan blob is recoverable and harmless; an intake row pointing at nothing is not. A reconciliation check should count orphan blobs |
| A future third mutable column silently defeats the column grant | MEDIUM | The grant enumerates columns explicitly, so a new column is *not* writable by default and fails loudly on first use — the correct direction to fail |
| Existing `storage_path` rows point at a workstation path that no longer exists | MEDIUM | `storage_backend='local'` makes this explicit and queryable rather than discovered at retrieval. The current delivery should be uploaded to Blob and a **new** intake row is not created — instead the additive columns are populated, which is itself an Area 1 write and must be done **before** the grants are applied |

**Sequencing consequence of the last risk:** the Blob migration of the existing
delivery must precede the revoke. Once Area 1 is hardened, populating
`storage_backend` on the historical row requires the break-glass path.

---

## 8. DEPENDENCIES

- **Independent of D1–D7.** No methodology decision affects this design.
- **Depends on:** confirmation of the records-retention period before an
  immutability policy is locked; pgaudit availability on Azure PostgreSQL
  Flexible Server; a DBA to execute ownership transfer and grants.
- **Blocks:** nothing. Should precede PPEF and NPPES ingestion, so that the
  storage and provenance pattern is established once rather than three times.
