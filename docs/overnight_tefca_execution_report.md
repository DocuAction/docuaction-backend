# OVERNIGHT TEFCA EXECUTION REPORT

**Date:** 2026-08-26 · **Branch:** `fix/tefca-stabilization` · **Scope:** Phases 1–4 only
**Contract:** 7571MN26F80064 (HHS/ONC ASTP)

---

## 1. EXECUTIVE SUMMARY

All four approved phases are **COMPLETE**. Four checkpoint commits, four migrations, 76 new tests, and **0 test failures** at every checkpoint.

| | |
|---|---|
| Tests | **1364 → 1440** (+76), 0 failures throughout |
| Reconciliation | **18/18** at every phase |
| Area 1 hashes | **23,566 / 23,566** valid, 0 mismatches |
| Historical evidence | **1,984 rows unchanged** — digest identical to baseline |
| Determinations | **43 unchanged** — digest identical, 0 resolutions fabricated |
| `main` | **unchanged** at `d76937f` |

**Four defects were found and fixed during implementation**, each caught by the work rather than by review: a value-union that hid an unreachable rule condition, a model edit that split a class, a route that would have been shadowed by an existing path, and an emitted GRANT that would have broken promotion in production.

**No D1–D9 methodology decision was made.** Nine dependencies were encountered; each is recorded with its blocking decision and left unresolved.

**Two things are pending on external input, neither of which blocks the work done:** the Azure `review_rules` inventory (no DB credential in this environment) and the Area 1 grant application (needs a superuser to transfer table ownership).

---

## 2. PHASE STATUS

| Phase | Status |
|---|---|
| **E1 / B5 — Canonical Evidence Vocabulary** | **COMPLETE** |
| **E2 / B3 — Evidence Provenance** | **COMPLETE** (capability; no data ingested, as instructed) |
| **B2 — QA Gate** | **COMPLETE** |
| **B1 — Area 1 Immutability / Evidence Controls** | **PARTIAL — by design.** Everything retention-independent is done. Two components are pending on external prerequisites: applying the GRANT/REVOKE (needs superuser for ownership transfer) and durable Blob storage (D8 retention unresolved). Both were explicitly out of scope for tonight. |

---

## 3. CHECKPOINT COMMITS

| Phase | Commit | Message | Files |
|---|---|---|---|
| 1 | **`3bba15c`** | `feat(tefca): implement canonical evidence vocabulary` | 11 (2 new modules, 1 migration, 1 new test file, 7 modified) |
| 2 | **`cf02fd2`** | `feat(tefca): add evidence provenance foundation` | 4 (1 new module, 1 migration, 1 new test file, 1 modified) |
| 3 | **`d5c36e6`** | `feat(tefca): implement immutable analyst QA decision events` | 6 (1 new module, 1 migration, 1 new test file, 3 modified) |
| 4 | **`3c9ab48`** | `feat(tefca): strengthen Area 1 evidence controls` | 4 (1 migration, 1 new test file, 2 modified) |

Prior on branch: `af4181a` (stabilization fixes), `80693da` (design docs), `130e623` (forensic baseline).

---

## 4. MIGRATIONS

All four are **additive**, **nullable where columns were added**, and **reversible**. Every `downgrade()` was written.

| ID | Change | Backfill? | Result |
|---|---|---|---|
| `20260823_vocab_version` | `tefca_dimension_evidence.vocabulary_version VARCHAR(10) NULL` + index | **NO** — 0 rows written | applied, verified |
| `20260824_evidence_prov` | `source_version_snapshots` (new), `evidence_relationship_path` (new), **11 additive nullable columns** on evidence | **NO** — verified 0 rows per column, all 11 | applied, verified |
| `20260825_qa_events` | `review_decision_events` (new, 7 CHECK constraints), `review_records.reportable_at`, SoD trigger, effective-determination view | **NO** — 0 events, 0 reportable | applied, verified |
| `20260826_area1_audit` | `area1_mutation_log` (new), 3 triggers, log function | **NO** — 0 rows | applied, verified |

**No `server_default` on any added column.** A default would have made PostgreSQL rewrite all 1,984 evidence rows and erase the NULL-means-LEGACY distinction.

**One thing to flag.** The migrations were applied by executing each migration's own `upgrade()` directly, **not** via `alembic upgrade head`. `alembic current` reports `20260627_tefca_dashboard` — the chain has been **pre-existingly out of sync** since the RCE tables were created by startup `create_all` rather than by migration. Running `upgrade head` would have executed seven intervening migrations including an **`audit_logs` backfill**, which is outside scope and a historical mutation. `alembic_version` was therefore **not advanced**. This is a pre-existing condition, not caused by this work, and it is a morning item.

---

## 5. TESTS

| Phase | New tests | File | Suite total | Failures |
|---|---|---|---|---|
| baseline | — | — | 1364 | 0 |
| 1 | 27 | `test_evidence_vocabulary.py` (+ 10 repointed) | **1391** | 0 |
| 2 | 26 | `test_evidence_provenance.py` | **1417** | 0 |
| 3 | 23 | `test_qa_gate.py` | 1431 → **1440** | 0 |
| 4 | 9 | `test_area1_controls.py` | **1440** | 0 |
| **TOTAL** | **+76** | 4 new files | **1440 passed, 40 skipped** | **0** |

Skips (40) are the suite's established pattern: no `DATABASE_URL` in the test environment, `BULLETIN_AUTH_ENABLED` off, WeasyPrint native libraries absent on Windows. **13 database-backed assertions were additionally verified directly against Postgres**, all inside rolled-back transactions.

### Two regressions were introduced and fixed — neither by weakening a test

1. **`test_immutability_grants_cover_both_area1_tables`** pinned the exact string `"REVOKE UPDATE, DELETE ON …"`; I had *strengthened* the revoke to include `TRUNCATE`. Rewrote the assertion to check the **privileges** rather than the string, and **added** `TRUNCATE` as a requirement — a strictly stronger test — plus a companion test that the column grant keeps promotion working.
2. **`test_no_tefca_read_endpoint_sits_above_the_viewer_floor`** failed on the new `qalead`-gated `GET /qa-queue`. Added to the file's existing `ALLOWED_ABOVE_VIEWER` list **with a written justification**, not by lowering the gate: the queue names the analyst behind each determination, and staff email addresses are the same category the `/audit-trail` exception already turns on (LOGIN-013). The read a viewer legitimately needs stays open at `/reviews/{review_id}/history`.

---

## 6. RECONCILIATION

**18 / 18 passing at every phase.** Populations unchanged throughout:

```
A source records received  23,566      D curated records      23,566
B rejected or held              4      E promoted to registry 23,562
C eligible                 23,562      F verification reviews     43
corrections 1,631 · unexplained 0 · rules executed 31 · under-evaluated 0
```

---

## 7. HISTORICAL INTEGRITY

Fingerprints captured **before any change** and re-taken after each phase.

| | Before | After | Changed |
|---|---|---|---|
| Evidence rows | 1,984 | **1,984** | **0** |
| Evidence digest | `eca047f9bdf4afb8567c43c83325fa92` | **identical** | — |
| Disposition counts | 9 values | **identical** | — |
| Determinations | 43 | **43** | **0** |
| Determination digest | `a6fa52f503f6cf35dbe9d85bfaaadf2f` | **identical** | — |
| Bucket distribution | B1=12 B2=10 B3=21 | **identical** | — |
| `reviewer_resolution` set | 0 | **0** | 0 |
| `reportable_at` set | n/a | **0** | — |
| Decision events | n/a | **0** | — |

No analyst decision, QA event, or resolution was fabricated for any historical record.

---

## 8. AREA 1

| | |
|---|---|
| Hashes checked | **23,566** |
| Valid | **23,566** |
| Invalid | **0** |
| Record digest | `d65e51cfbd424bab7ad1703d4a1fba98` — identical to baseline |
| Stored delivery file | SHA-256 matches; intact |

**Expected 23,566 / 23,566 — met.**

---

## 9. AZURE READ-ONLY INVENTORY

| | Result |
|---|---|
| **DEV** (`docuaction-db-dev`) | **NOT EXECUTED — ACCESS UNAVAILABLE** |
| **PROD** (`docuaction-db`, `docuaction-db-geo`) | **NOT EXECUTED — ACCESS UNAVAILABLE** |
| **CHECK 4 Stage A** | **ACTIVE** — CI fatal, startup report-only (the default) |
| **CHECK 4 Stage B** | **DISABLED**, pending the inventory |

`az` is authenticated (`imran@agtbi.com`, AGT-DocuAction) and the three servers are visible via the control plane. But **no database credential exists in this environment** — `DATABASE_URL` points at localhost, and no Azure DB secret is present. Obtaining one would mean retrieving a production secret from Key Vault and probably adding a firewall rule (a configuration modification, explicitly prohibited). Per the instruction, this was **recorded rather than worked around**, and no credential was requested or invented.

**Outstanding prerequisite, precisely:** run these three read-only queries against Azure dev and prod and confirm the signal set is a subset of the eight registered signals.

```sql
SELECT rule_code, version, bucket, priority, is_active, retired_date,
       jsonb_pretty(conditions) FROM review_rules ORDER BY priority, rule_code;

SELECT DISTINCT cond->>'field' AS signal
FROM   review_rules r, LATERAL jsonb_each(r.conditions) AS c(k,v),
       LATERAL jsonb_array_elements(
         CASE WHEN jsonb_typeof(v)='array' THEN v ELSE '[]'::jsonb END) AS cond
WHERE  cond ? 'field' ORDER BY 1;

SELECT DISTINCT cond->>'source' AS source
FROM   review_rules r, LATERAL jsonb_each(r.conditions) AS c(k,v),
       LATERAL jsonb_array_elements(
         CASE WHEN jsonb_typeof(v)='array' THEN v ELSE '[]'::jsonb END) AS cond
WHERE  cond ? 'source' ORDER BY 1;
```

Only after that returns clean should `VOCABULARY_CONTRACT_STARTUP_MODE=fatal` be set.

---

## 10. METHODOLOGY BOUNDARY

**No D1–D9 decision was guessed, inferred, or encoded.** Nine dependencies encountered:

| Dependency | Where | How it was handled |
|---|---|---|
| **D1** — uncorroborated NPI | `nppes_pecos_conflict`, `multiple_source_conflict`, Layer 3 `FAIL` | signals METHODOLOGY_BLOCKED; `FAIL` tagged METHODOLOGY_DEPENDENT |
| **D2** — no-rule-match default | `confidence_below`, Layer 3 `NOT_FOUND`, Layer 4 `UNDETERMINED` | `UNDETERMINED` registered as reserved, **not** added to the classifier's output domain |
| **D3** — B3 tier | QA gate | **no queue routing built.** `/qa-queue` assigns nothing and implies no tier |
| **D4** — source unavailable | Layer 3 `UNAVAILABLE` | tagged METHODOLOGY_DEPENDENT |
| **D5** — name-difference severity | `name_mismatch` | PRODUCIBLE with `value_domain=UNRECONCILED`; the hardcoded `minor` severity left untouched |
| **D6** — flagged vs invalid | `npi_validation` | **PRODUCIBLE**, `value_domain=UNRECONCILED`, `consequence_state=METHODOLOGY_PENDING`. No INVALID→B4 or FLAGGED→bucket mapping exists |
| **D7** — exclusion → B4 | `_DISPOSITION_TO_STATE` | untouched; no disposition maps to `excluded` |
| **D8** — records retention | Area 1 storage | **no WORM retention configured, no Blob container created** |
| **D9** — deliverable format | — | not reached; reports untouched |

**Enforced by test, not by intention:** `test_no_layer1_to_layer4_mapping_exists` scans the vocabulary module and fails if any structure maps a Layer 1 observation to a bucket. `_DISPOSITION_TO_STATE`, `BUCKET_TO_TIER` and every `review_rules` row are byte-for-byte unchanged.

---

## 11. BLOCKERS

Two, both external and neither blocking the work completed.

### BLOCKER 1 — Azure `review_rules` inventory
- **Impact:** CHECK 4 Stage B stays disabled. Stage A already detects and reports the condition on every boot, so the risk is mitigated, not open.
- **Dependency:** a DB credential for Azure dev/prod, or a DBA to run three SELECTs.
- **Safe next action:** run the queries in §9; if clean, set `VOCABULARY_CONTRACT_STARTUP_MODE=fatal`.

### BLOCKER 2 — Area 1 GRANT/REVOKE application
- **Impact:** `enforced=false` persists. Application-layer immutability holds (no update path, no mutating route) and any evidence write is now audited by trigger — but the database would still permit one.
- **Dependency:** a superuser to `CREATE ROLE docuaction_owner` and `ALTER TABLE … OWNER TO`. `docuaction` owns the tables, so a revoke against it is self-reversible; applying it without the transfer would be theatre.
- **Safe next action:** DBA runs `immutability_grants_sql()` output, then re-run `verify_immutable()` and confirm `evidence_columns_writable == []` and `workflow_writable_as_designed == true`.

**Also noted, pre-existing and not caused by this work:** the Alembic chain is out of sync (§4).

---

## 12. REMAINING WORK FOR MORNING

| Item | Status |
|---|---|
| **Intelligent Ingestion** (Phase 5) | NOT STARTED — held |
| **Real PPEF ingestion** (Phase 6) | NOT STARTED — held. Provenance capability is built and inert |
| **B4 report consolidation** (Phase 7) | NOT STARTED — held. `app/reports/` untouched |
| **Learning Center** (Phase 8) | NOT STARTED — held |
| **End-to-end validation** (Phase 9) | NOT STARTED — held |
| **COR D1–D9** | Awaiting program decision — `docs/cor_decision_brief_v2.md` is ready to send |
| Azure inventory → CHECK 4 Stage B | see Blocker 1 |
| Area 1 grants → `enforced=true` | see Blocker 2 |
| Alembic chain reconciliation | pre-existing; needs a decision on how to resync without running the `audit_logs` backfill |
| Wire `observation_result` emission into the six dimension assemblers | B3 follow-on; schema and vocabulary are ready |

---

## 13. REPOSITORY STATUS

| | |
|---|---|
| Branch | `fix/tefca-stabilization` |
| HEAD | **`3c9ab48`** — `feat(tefca): strengthen Area 1 evidence controls` |
| Working tree | **clean** |
| `main` | **`d76937f` — unchanged** |
| Pushed | No. Four checkpoint commits are local, per the checkpoint policy |
| Protected modules | `app/tefca_registry/ai/` and `app/bulletin_intelligence/` — **untouched** |
| `app/reports/` | **untouched** |

---

## 14. SAFETY CONFIRMATION

| | |
|---|---|
| Production deployed | **NO** |
| Main merged/modified | **NO** |
| Real PPEF ingested | **NO** (`tefca_ppef_snapshots` = 0, `tefca_ppef_records` = 0) |
| Bulk NPPES ingested | **NO** |
| D1–D9 guessed | **NO** |
| Original ONC evidence rewritten | **NO** (23,566/23,566 hashes valid) |
| Historical evidence rewritten | **NO** (digest identical to baseline) |
| Final WORM retention configured | **NO** |
| Report presentation changed | **NO** |
| Classifier priority changed to pass tests | **NO** |
| Tests weakened to pass | **NO** — one was strengthened; one gained a justified, documented exception |

---

## APPENDIX — DEFECTS FOUND AND FIXED DURING IMPLEMENTATION

Each was caught by the work itself, not by later review.

**1. A value-union hid an unreachable rule condition.** The first signal registry stored `observed_values` as a flat union across producers, which reported `RULE-005 npi_validation = invalid` as READY. The RCE path emits only `flagged`; `invalid` comes from the registry path. Restructured so producers record their path and emit values individually — the exact silent-never-fires failure the contract exists to catch.

**2. A model edit split a class.** The two new provenance tables were inserted mid-class, silently reattaching `TEFCADimensionEvidence`'s analyst-annotation columns and `__table_args__` to `EvidenceRelationshipPath`. SQLAlchemy caught it at import.

**3. A new route would have been shadowed.** `GET /reviews/qa-queue` would have been matched by the pre-existing `/reviews/{review_id}` and returned 404 for a review named "qa-queue". Moved to `/qa-queue`.

**4. The emitted grants would have broken production.** `immutability_grants_sql()` emitted a blanket `REVOKE UPDATE`, which stops `promote_delivery` writing promotion markers on 23,562 rows *after* the entities are committed. Replaced with a column-level grant, plus the ownership transfer without which any revoke is self-reversible.
