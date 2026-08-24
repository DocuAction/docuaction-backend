# DocuAction Database Domain Architecture

**Date:** 2026-08-22
**Branch:** `fix/tefca-stabilization` · **Commit:** `596a4f9`
**Status:** Analysis and recommendation. Nothing implemented. No database changed.
**Blocks:** Alembic version reconciliation on the TEFCA database.

---

## 0. Why this document exists

The migration chain is correct: 1501 tests pass, `alembic check` reports zero
operations on both a fresh build and a clone of the live schema, there is one
head, and the chain is reversible and idempotent.

Running it is a different question. `alembic upgrade head` against the current
TEFCA database would create **61 tables that do not exist there** — 45 of them
ERP/business objects like `quotes`, `suppliers`, `candidates` and `invoices`.

That is not a migration defect. The chain faithfully builds everything the ORM
declares, and the ORM declares five unrelated product domains. The question the
chain cannot answer is *which of them this database owns*.

---

## 1. Method

Domain was not inferred from table names. For all 148 PostgreSQL objects the
following was traced and recorded (Appendix A):

- the module declaring the mapped class (`cls.__module__`), not the file it is
  imported from
- the full foreign-key graph across both declarative Bases
- presence and row count on the live database
- the revision that creates it, including the two `create_all()` revisions that
  read their table list at runtime
- whether TEFCA code imports the mapped class, read from `ast` import statements
  rather than by matching names

That last point changed the answer. A first pass matching class names as words
reported fourteen TEFCA cross-domain dependencies. Reading imports cut it to
**two**:

| Reported | Reality |
|---|---|
| `documents` | `from docx import Document` — python-docx, not the ORM model |
| `decisions`, `tasks`, `actions`, `contracts` | ordinary English in TEFCA prose and unrelated identifiers |
| `audit_log` (singular) | class-name collision with `audit_logs` |

SQLite objects are excluded by construction: `articles` and `briefings` are
created by `app/bulletin_intelligence/story_repository.py`, which drives a local
SQLite store. They are not PostgreSQL objects and are not Alembic's concern.

---

## 2. Task 1 — table ownership inventory

148 PostgreSQL objects: 135 modelled, 11 created by raw startup SQL,
`area1_mutation_log` (trigger-written, no model), `alembic_version`.

| Domain | Tables | Live | In the 61 |
|---|---:|---:|---:|
| ERP_BUSINESS | 45 | 0 | 45 |
| TEFCA | 45 | 45 | 0 |
| CORE_SHARED | 24 | 24 | 0 |
| BULLETIN | 10 | 9 | 0 |
| MIGRATION_TOOLING | 9 | 0 | 9 |
| CASE_MANAGEMENT | 6 | 0 | 6 |
| AUDIT_SHARED | 3 | 2 | 1 |
| AUTH_SECURITY | 3 | 3 | 0 |
| REPORTING_SHARED | 2 | 2 | 0 |
| UNKNOWN (`alembic_version`) | 1 | 1 | 0 |
| **Total** | **148** | **86** | **61** |

Full row-by-row inventory: **Appendix A**.

Three classifications a name-based reading gets wrong:

**`app.models.enterprise_models` is not ERP.** `contexts → decisions → actions →
traceability`, with `process_jobs` and `execution_queue`, is the DocuAction
document-to-action engine. All seven are live, all seven empty, all seven
foreign-key to `tenants`. Core, not ERP.

**`app/models/__init__.py` is the ERP product.** 45 tables — quoting, suppliers,
purchase orders, invoicing, ATS/recruiting, contracts, expenses. None exists on
the live database and nothing creates them: `app/main.py` calls `create_all()` on
`app.core.database.Base` only, and these live on `app.database.Base`. They have
never been part of this deployment.

**Audit splits by owner, not by name.** `audit_logs`, `audit_log` and
`state_audit_log` are the shared audit framework's data. `tefca_reg_audit_log`,
`tefca_qa_audit` and `area1_mutation_log` are TEFCA's own and belong with TEFCA.

---

## 3. Task 2 — the 61 tables, verified exactly

The earlier estimate was "~46 ERP + ~15 `cm_*`/`migration_*`". The exact
composition is:

| Bucket | Count | Verdict |
|---|---:|---|
| ERP_BUSINESS | **45** | UNRELATED_TO_TEFCA |
| MIGRATION_TOOLING (`migration_*`) | **9** | UNRELATED_TO_TEFCA |
| CASE_MANAGEMENT (`cm_*`) | **6** | UNRELATED_TO_TEFCA |
| AUDIT_SHARED (`audit_log`, singular) | **1** | UNRELATED_TO_TEFCA |
| **Total** | **61** | |

`REQUIRED_FOR_TEFCA`: **0**. `REQUIRED_SHARED_CORE`: **0**. `UNCLEAR`: **0**.

Answering the seven questions for all 61:

| Question | Answer |
|---|---|
| Does TEFCA import or use it? | No — 0 of 61 |
| Does TEFCA have an FK dependency on it? | No — 0 of 61 |
| Does authentication/security depend on it? | No. `users`, `tenants`, `tenant_users` already exist and are not among the 61 |
| Does shared audit depend on it? | No. `audit_logs` already exists; the absent `audit_log` is the enterprise-core table, unused by TEFCA |
| Does TEFCA reporting depend on it? | No. `app/reports/` declares no tables at all |
| Would TEFCA runtime fail without it? | No. TEFCA has been running without all 61 |
| Why is the migration creating it? | Because `env.py` now targets both declarative Bases. That change was correct — it made 88 previously invisible tables visible — and these 61 are the part of that visibility belonging to other products |

### TEFCA's actual cross-domain surface

- **Foreign keys from TEFCA to any other domain: none.** The 45 TEFCA tables are
  a closed component. This is the single most important structural fact in this
  document: separation needs no foreign-key surgery.
- **Tables TEFCA code imports from other domains: two**, both read-only, both
  already live.

| Table | Domain | Used by | How |
|---|---|---|---|
| `users` | AUTH_SECURITY | `app/Tefca/routes.py`, `app/Tefca/ppef_scheduler.py` | resolve actor names for the audit trail; resolve `job.requested_by` by email |
| `audit_logs` | AUDIT_SHARED | `app/Tefca/routes.py` | the TEFCA audit-trail endpoint reads it and joins to `users` |

`app/tefca_registry/**` — registry, RCE pipeline, Area 1 — imports neither. The
dependency is confined to two read paths in the presentation layer.

---

## 4. Task 3 — architecture evaluation

### A — one database for every DocuAction module

The status quo by accident, not by decision. Running `upgrade head` would
formalise it.

ERP commercial data (supplier pricing, quotes, candidate records) would share a
database, a backup artifact and a blast radius with federal contract evidence
held under ONC 7571MN26F80064. A `pg_dump` taken for a TEFCA audit would contain
the company's commercial book. A point-in-time restore for an ERP incident would
roll back TEFCA evidence. One compromised application credential reaches both.
Under NIST SP 800-53 this fails **AC-6** (least privilege), **SC-4** (information
in shared resources) and **MP-6/CP-9** (media and backup handling for CUI).

No architectural reason for the sharing was ever offered — the ORM merely exposes
them together. **Rejected.**

### B — a database per domain, no shared Core

Clean isolation, but it forces identity and tenancy to be duplicated per program
or reached over a service boundary for every request. TEFCA needs `users` for two
read paths; standing up a service for that is disproportionate today.
**Rejected as the target**, though it is what the program modules converge to
under D.

### C — one PostgreSQL service, one schema per domain

`core.*`, `tefca.*`, `erp.*`, `bulletin.*`, each with an Alembic chain owning its
schema (`version_table_schema`). Cross-schema foreign keys remain possible,
`GRANT`/`REVOKE` works per schema, operational cost stays flat.

Weaker than D on the two things that matter most: a database-level backup still
spans every domain, and any role with instance access is one `search_path` from
another program's data. **Strong as a stage, not as the end state.**

### D — hybrid: shared Core, program-owned databases

- **Core** — identity, tenancy, program configuration, shared audit, the
  document/output engine. One owner, one chain, consumed by every program.
- **Program modules** — TEFCA, ERP, Bulletin, Case Management, Migration
  Tooling. Each owns its database and its Alembic chain, deploys on its own
  schedule, and is backed up and restored independently.

This is the locked platform model — CORE, PROGRAM_CONFIGURATION,
SHARED_CONNECTORS, PROGRAM_MODULES — expressed physically rather than only
logically.

| Criterion | D |
|---|---|
| NIST / federal security | AC-6 and SC-4 satisfied structurally, not by convention |
| Least privilege | The TEFCA role has no grant path to ERP objects; today it would have full access |
| CUI / PHI isolation | Federal contract evidence, commercial ERP data and future healthcare PHI stop sharing a blast radius. Healthcare programs make this compliance, not preference |
| Auditability | "Everything in the TEFCA evidence store" is a database, not a filtered query |
| Evidence integrity | Area 1 WORM retention applies to an evidence database, not to quoting data that must stay mutable |
| Program separation | Each future program gets a boundary by default rather than by review |
| Migration blast radius | A bad ERP migration cannot fail a TEFCA deployment. Today one chain fails them all |
| Independent deployment | Each program releases on its own cadence — the point of a reusable platform |
| Backup / DR | Per-program RPO, retention and restore rehearsal |
| Cost | Multiple databases on one Azure Flexible Server cost nothing extra |
| Azure complexity | One connection string per module; no new infrastructure |
| Cross-program reuse | Core is reused as code and as a schema. A shared table namespace was never the requirement |
| Maintainability | More chains, each far smaller. The 2,359-line coverage migration exists precisely because one chain had to cover five products |
| Agency onboarding | A new program is a new database plus a configuration row set, not a schema negotiation |

**Recommended: D**, reached in two stages so nothing is rebuilt speculatively.

- **Stage 1 — now, no database change.** Scope each Alembic chain to the domain
  it owns. This unblocks reconciliation and is the whole of §9.
- **Stage 2 — when a second program deploys independently.** Promote domains to
  separate databases. Because TEFCA has no outbound foreign keys this is a data
  move, not a redesign. Do not do it before a second program justifies it.

---

## 5. Task 4 — the reusable federal standard

```
                    DOCUACTION PLATFORM
                            │
                       SHARED CORE
         identity · RBAC · audit framework · evidence engine
         provenance · vocabulary · rules engine · QA framework
         reporting engine · connectors · jobs · learning framework
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
      TEFCA             PROGRAM B           PROGRAM C
        │                   │                   │
  configuration       configuration       configuration
   terminology         terminology         terminology
   methodology         methodology         methodology
   thresholds          thresholds          thresholds
        │                   │                   │
   program data        program data        program data
   evidence · issues · observations · QA events · reports
```

Four categories, and they are not interchangeable:

- **SHARED IMPLEMENTATION** — one codebase, used by every program
- **SHARED DATA** — one table, rows spanning programs
- **PROGRAM-ISOLATED DATA** — same shape everywhere, rows never mixed
- **PROGRAM CONFIGURATION** — rows that define how a program behaves

| Capability | Implementation | Data | Evidence in this repo |
|---|---|---|---|
| Authentication | SHARED | **SHARED DATA** — `users`, `tenants`, `tenant_users` | `app/core/security.py`, `app/models/database.py`. One identity across programs is the point of the platform |
| RBAC | SHARED | **SHARED DATA** (`users.role`) + **PROGRAM CONFIGURATION** (which roles a program exposes) | `role` is a column, not a table. TEFCA's analyst/qalead/viewer set is program configuration |
| Audit framework | SHARED | **SHARED DATA** for platform events (`audit_logs`); **PROGRAM-ISOLATED** for program events | `tefca_reg_audit_log` (23,812 rows), `tefca_qa_audit`, `area1_mutation_log` are TEFCA's |
| Evidence framework | **SHARED** — already | **PROGRAM-ISOLATED** | `app/core/evidence_vocabulary.py` is Core code today; `tefca_dimension_evidence` is TEFCA data |
| Source files / intake | TEFCA-resident today, **should become SHARED** | **PROGRAM-ISOLATED** | `app/tefca_registry/rce/intake.py`, `reader.py`; Area 1 `rce_source_intakes` / `rce_source_records` |
| Issue ledger | TEFCA-resident today, **should become SHARED** | **PROGRAM-ISOLATED** | `quality_engine.py`; `rce_issues` (36,916 rows) |
| Provenance | **SHARED** — already | **PROGRAM-ISOLATED** | `app/core/evidence_provenance.py`; `source_version_snapshots`, `evidence_relationship_path` |
| Observation vocabulary | **SHARED** — already | **PROGRAM CONFIGURATION** (which terms a program uses) + **PROGRAM-ISOLATED** (recorded observations) | `app/core/evidence_vocabulary.py`, `vocabulary_contract.py` |
| Rules engine | TEFCA-resident today, **should become SHARED** | **PROGRAM CONFIGURATION** (rule rows) + **PROGRAM-ISOLATED** (execution history) | `quality_rules.py`; `review_rules`, `rce_rule_execution_history` |
| QA / review framework | TEFCA-resident today, **should become SHARED** | **PROGRAM-ISOLATED** | `app/tefca_registry/qa_gate.py`; `review_decision_events`, `review_records` |
| Reporting framework | **SHARED** — already, and correctly **declares no tables** | templates = **PROGRAM CONFIGURATION**; generated reports = **PROGRAM-ISOLATED**; `outputs`/`output_templates` = **SHARED DATA** | `app/reports/` is code, styles, fonts and templates only |
| Connectors | per-module today, **should become SHARED** | **PROGRAM-ISOLATED** (logs and caches) | `tefca_connector_logs`, `tefca_source_cache`, `usps_client.py` |
| Program methodology | SHARED engine | **PROGRAM CONFIGURATION** | D1–D9 decisions, thresholds, applicability — `docs/methodology_decision_package.md` |
| Program configuration | SHARED | **SHARED DATA holding program-scoped rows** — the 13 `platform_*` tables | `platform_programs`, `platform_workspaces`, `platform_jurisdictions` (57 rows) |
| Learning content | **not built** | content = PROGRAM CONFIGURATION; learner progress = SHARED DATA | No module, no tables. Per the locked rule, nothing to build until Phase 8 |

**The conclusion that matters:** the code story is mixed — five frameworks are
already Core, five are TEFCA-resident and would need lifting when a second
program arrives — but **the data story is uniform. Every framework's operational
data is program-isolated.** Not one requires shared program data. So lifting the
engines is a code refactor scheduled by need, and it does not change the database
boundary this decision sets.

What must stay common: identity, tenancy, RBAC, platform audit, program
configuration, and every engine. What must stay isolated: evidence, source
files, issues, observations, QA events, generated reports, connector logs.

---

## 6. Task 5 — Alembic ownership

### Structure

```
alembic/
  core/        env.py  versions/    → identity, tenancy, platform config,
                                      shared audit, documents/outputs, enterprise
  tefca/       env.py  versions/    → the 45 TEFCA tables
  erp/         env.py  versions/    → 45 ERP tables (not deployed today)
  bulletin/    env.py  versions/    → 10 tables, currently raw startup SQL
  casemgmt/    env.py  versions/    → 6 cm_* tables (not deployed today)
  migrationtool/ env.py versions/   → 9 migration_* tables (not deployed today)
alembic.ini                          → one [<name>] section per chain
```

Each chain gets its own `version_table` (`alembic_version_core`,
`alembic_version_tefca`, …) so several chains can share one database during
Stage 1 without colliding. Invoked as `alembic -n tefca upgrade head`.

Rules that make this hold:

1. **Exactly one chain creates any given table.** A chain that reads a table it
   does not own declares it a dependency, never a target.
2. **Each `env.py` imports only its own model modules.** Metadata is populated by
   import; importing another domain's models is what produced the 61.
3. **Core runs first.** A program deployment is `core upgrade head` then
   `<program> upgrade head`.

### Definitions

**TEFCA_MANAGED — 45 tables**

- `app.Tefca.models` — review cycles, samples, records, reports, rules,
  dimension evidence, provenance, PPEF snapshots/records/jobs, verification,
  findings, entities, import history
- `app.tefca_registry.models` — `tefca_reg_*`, registry entities, identifiers,
  endpoints, relationships, versions, analyst queue, `review_decision_events`
- `app.tefca_registry.rce.models` — `rce_*` (Area 1 and Area 2),
  `tefca_entity_contacts`
- `tefca_reg_audit_log`, `tefca_qa_audit` (to be lifted out of raw SQL),
  `area1_mutation_log` (trigger-written, chain-owned, excluded from autogenerate)

**CORE_DEPENDENCIES — read, never created by the TEFCA chain**

| Table | Why | Owner |
|---|---|---|
| `users` | actor names in the audit trail; PPEF `requested_by` | Core chain |
| `audit_logs` | the TEFCA audit-trail endpoint reads it | Core chain |

**EXCLUDED_FROM_TEFCA — 103 tables**

| Excluded | Count | Reason |
|---|---:|---|
| ERP (`app.models`) | 45 | Different product. No FK, no import, no rows, never deployed here |
| Core shared (`app.models.database`, `app.api.*`, `enterprise_models`) | 11 | Core-owned. The apparent TEFCA use of `documents` was python-docx |
| Program configuration (`platform_*`) | 13 | Shared by every program module. Nothing under `app/Tefca` or `app/tefca_registry` imports it |
| Bulletin | 10 | Own subsystem, own chain |
| Migration tooling | 9 | Separate product capability |
| Case management | 6 | Distinct program module (CCM/TCM/PCM) |
| Auth / shared audit / reporting-shared | 8 | Core-owned; `users` and `audit_logs` are dependencies, not targets |
| `alembic_version` | 1 | One per chain, managed by Alembic |

**SEPARATE_MIGRATION_DOMAINS**

| Chain | Tables | Deployed today |
|---|---:|---|
| Core | 32 | yes |
| TEFCA | 45 | yes |
| ERP | 45 | no |
| Bulletin | 10 | yes, via raw startup SQL |
| Case Management | 6 | no |
| Migration Tooling | 9 | no |

Goal restated: `alembic -n tefca upgrade head` creates **all and only** the
TEFCA schema, on a database where the Core chain has already run.

---

## 7. Task 6 — startup DDL

Eleven PostgreSQL tables are created by `CREATE TABLE IF NOT EXISTS` at boot.

| Table | Rows | Owner domain | Migration coverage required | Remove startup DDL | Chain that should own it |
|---|---:|---|---|---|---|
| `tefca_qa_audit` | present | TEFCA | **Yes** | Yes | TEFCA |
| `bulletin_articles` | 542 | BULLETIN | Yes | Yes | Bulletin |
| `bulletin_source_registry` | 276 | BULLETIN | Yes | Yes | Bulletin |
| `bulletin_source_outcome` | 125 | BULLETIN | Yes | Yes | Bulletin |
| `bulletin_search_profiles` | 9 | BULLETIN | Yes | Yes | Bulletin |
| `bulletin_briefings` | 3 | BULLETIN | Yes | Yes | Bulletin |
| `bulletin_run_log` | 1 | BULLETIN | Yes | Yes | Bulletin |
| `bulletin_audit_log` | 1 | BULLETIN | Yes | Yes | Bulletin |
| `bulletin_cost_logs` | 0 | BULLETIN | Yes | Yes | Bulletin |
| `bulletin_delivery_log` | 0 | BULLETIN | Yes | Yes | Bulletin |
| `bulletin_recipients` | absent | BULLETIN | Yes | Yes | Bulletin |

None is an `INTENTIONAL_STARTUP_OBJECT`. Creating schema at boot is a bootstrap
shortcut, not a design: it is unversioned, unreviewable and invisible to
`alembic check`. All eleven are **LEGACY_TECHNICAL_DEBT** — but bounded debt, in
two files, already declared in `env.py`'s `UNMODELLED_TABLES` with the reason
written beside each, so `alembic check` stays clean and honest about them.

`tefca_qa_audit` is the one that matters for this program. It is the QA gate's
audit trail, TEFCA-owned, and belongs in the TEFCA chain — a self-contained
change in `app/Tefca/qa_engine.py` plus one revision.

`app/bulletin_intelligence/` is out of scope for modification, so its ten tables
are recorded and left alone.

**`articles` and `briefings` are SQLite objects**, created by
`app/bulletin_intelligence/story_repository.py` against a local store
(`PRAGMA journal_mode=WAL`). They never appear in PostgreSQL, before or after
boot. They are deliberately **not** listed as Alembic exclusions — naming them
would imply Alembic is ignoring tables that exist. Recorded here so the question
is not reopened.

---

## 8. Task 7 — Area 1 security boundary

The runtime role must not own the immutable evidence tables. Confirmed, and the
measured reason is stronger than the theoretical one.

| Privilege | `docuaction_owner` (non-login owner) | Application runtime role | Break-glass DBA |
|---|---|---|---|
| Raw evidence columns (14) | implicit via ownership | **none** — measured, 0 of 14 writable | UPDATE under change control |
| Workflow columns (`promotion_status`, `canonical_entity_id`) | implicit | **UPDATE**, column-level | UPDATE |
| SELECT | implicit | **granted** | granted |
| INSERT | implicit | **granted** | granted |
| UPDATE (table-level) | implicit | **revoked** | granted |
| DELETE | implicit | **revoked** | granted, audited |
| TRUNCATE | implicit | **revoked** | granted, audited |
| DDL (`ALTER`, `DROP`) | **owner only** — cannot be revoked from an owner | none | none |
| Break-glass | — | — | named role, separate from both, every UPDATE/DELETE recorded by `area1_mutation_log` |

`docuaction_owner` must have **no `LOGIN` attribute** and must not be a role the
application can `SET ROLE` to.

**Is the owner role REQUIRED? Yes — for two reasons, not one.**

1. *The revoke is otherwise self-imposed.* A PostgreSQL owner can `GRANT` back to
   itself at any time, and `ALTER`/`DROP` are inherent to ownership and cannot be
   revoked. While the application role owns Area 1, the control stops an
   accidental code path but not an intentional one.
2. *The one testing found.* `rce_source_records.source_intake_id` references
   `rce_source_intakes`, and PostgreSQL enforces that with `SELECT … FOR KEY
   SHARE` executed **as the owner of the referenced table**. A row lock requires
   `UPDATE` or `DELETE`. So an owning role with `UPDATE` revoked cannot **insert**
   Area 1 records — ingestion fails with `permission denied for table
   rce_source_intakes`. `20260828_area1_grants` compensates with a single
   column-level `UPDATE (status)` grant, issued *only while the application role
   still owns the tables*. Moving ownership removes the workaround rather than
   adding one.

Ownership transfer is therefore not hardening polish; it removes a compensating
grant that exists solely because ownership is in the wrong place.

**Production-ready after transfer: YES.** Before it: functional and
application-safe, but database enforcement is advisory and one extra column is
writable. No live privilege was changed by this work.

---

## 9. Task 8 — minimum change plan

`alembic upgrade head` on the TEFCA database is **not approved**: it would import
61 tables from four other product domains into a database holding federal
contract evidence. The chain is not wrong — its *scope* is undecided, and running
it would decide the scope by side effect.

Stage 1 only. No new Alembic environments yet, no database change, no refactor.

| # | File | Change |
|---|---|---|
| 1 | `alembic/env.py` | Build `target_metadata` from the TEFCA model modules plus the Core tables a TEFCA deployment needs, instead of from every importable model. Same merge mechanism, narrower input. Keep `UNMODELLED_TABLES` and `MIGRATION_OWNED_INDEXES` as they are |
| 2 | `alembic/versions/20260827_startup_table_coverage.py` | Remove the `create_table` blocks for the 60 ERP / migration-tooling / case-management tables and their enum types. **This revision has never been applied to any database** — it was written today and exists only on this branch — so revising it is not rewriting history |
| 3 | `tests/test_migration_chain.py` | Assert the scope boundary: the TEFCA chain must not create an ERP, case-management or migration-tooling table, and must not create `users` or `audit_logs`. ~4 assertions |

**Metadata definitions to change:** one — `_merged_metadata()` in `env.py`.

**Alembic environments to change or create:** none in Stage 1. The multi-chain
layout in §6 is Stage 2, deferred until a second program deploys.

**Migrations to add:** none.

**Migrations to preserve unchanged:** all twelve released revisions
`20260627_tefca_initial` … `20260826_area1_audit`, and `20260828_area1_grants`.

**Tests required:** the boundary assertions above; the full suite should stay at
1501 passed / 0 failed.

After the change, `upgrade head` against the live database adds `audit_log` — one
empty Core-enterprise audit table — and nothing else.

### Commit `596a4f9`

**KEEP + FOLLOW-UP COMMIT.** The drift-safe chain, the Base unification and the
Area 1 correction are each correct and independently valuable, and the commit
contains no schema decision that this analysis reverses. Making both Bases
visible was the right change; it is what surfaced the 61 in the first place.
Scoping is a narrowing applied on top, not a correction of what was done.

Estimated scope: **3 files, 0 new migrations, 1 revised (unreleased) migration,
~4 new tests.** Under a day.

---

## 10. Task 9 — impact on Phases 5–9

| Phase | Impact | Why |
|---|---|---|
| **5 — Intelligent ingestion / enrichment** | **NO IMPACT** | Reads and writes `rce_*` and `tefca_*`, all TEFCA-owned and all present. New tables, if any, go in the TEFCA chain — which is where they would have gone anyway |
| **6 — PPEF ingestion** | **NO IMPACT** | `tefca_ppef_snapshots`, `tefca_ppef_records`, `tefca_ppef_ingest_jobs` are TEFCA-owned and live. The scheduler's one Core dependency (`users`, for `requested_by`) is a read that already works |
| **7 — Report consolidation** | **SMALL ADJUSTMENT** | `app/reports/` declares no tables today, which is exactly right for a shared engine. If consolidation introduces persistence, those tables are TEFCA program data and belong in the TEFCA chain, not in Core. One rule to follow, no rework |
| **8 — Learning Center** | **MATERIAL CHANGE** | The first genuinely cross-program capability, and nothing exists yet. This decision determines its shape: the framework is Core code, learner progress is SHARED DATA keyed to `users`, and course content is PROGRAM CONFIGURATION. Deciding that before building is the difference between one Learning Center and one per contract |
| **9 — End-to-end validation** | **SMALL ADJUSTMENT** | Should validate against a TEFCA-scoped database. After Stage 1 the fixture is 45 tables plus Core rather than 148, which makes the run faster and the assertion "this is what TEFCA needs" meaningful |

No phase is blocked by the architecture decision. Phase 8 is the one that should
not start before it is approved.

---

## 11. Open items

| # | Item | Decision owner |
|---|---|---|
| 1 | Approve Option D and the staged path | COR / architecture |
| 2 | Scope the TEFCA chain (§9) — unblocks reconciliation | engineering, after 1 |
| 3 | Create `docuaction_owner`, transfer Area 1 ownership | DBA — superuser required |
| 4 | Move `tefca_qa_audit` from raw SQL into the TEFCA chain | engineering |
| 5 | Give Bulletin Intelligence its own chain for its 10 tables | out of current scope |
| 6 | Decide whether ERP, case management and migration tooling are deployed at all, and where | product |
| 7 | `20260725_tefca_registry` still reads `TEFCA_REG_TABLE_ORDER` at runtime | engineering — see the previous report |
| 8 | Lift intake, issue ledger, rules and QA engines from `app/tefca_registry/` to Core | when a second program needs them, not before |

---

## Appendix A — table ownership inventory

Machine-readable. One row per PostgreSQL object. `sqlite` objects are
excluded by construction — see §6.

Columns: `table | domain | declaring module | live | rows | created by | TEFCA FK | TEFCA import | in the 61 | verdict`

| table | domain | declaring module | live | rows | created by | TEFCA FK | TEFCA import | in 61 | verdict |
|---|---|---|:-:|--:|---|:-:|:-:|:-:|---|
| `audit_log` | AUDIT_SHARED | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `audit_logs` | AUDIT_SHARED | `app.models.database` | Y | 251 | 20260827_startup_table_coverage.py | n | Y | n | - |
| `state_audit_log` | AUDIT_SHARED | `app.models.enterprise_models` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `tenant_users` | AUTH_SECURITY | `app.models.enterprise_models` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `tenants` | AUTH_SECURITY | `app.models.enterprise_models` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `users` | AUTH_SECURITY | `app.models.database` | Y | 6 | 20260827_startup_table_coverage.py | n | Y | n | - |
| `bulletin_articles` | BULLETIN | `raw SQL (startup)` | Y | 542 | startup raw SQL | n | n | n | - |
| `bulletin_audit_log` | BULLETIN | `raw SQL (startup)` | Y | 1 | startup raw SQL | n | n | n | - |
| `bulletin_briefings` | BULLETIN | `raw SQL (startup)` | Y | 3 | startup raw SQL | n | n | n | - |
| `bulletin_cost_logs` | BULLETIN | `raw SQL (startup)` | Y | 0 | startup raw SQL | n | n | n | - |
| `bulletin_delivery_log` | BULLETIN | `raw SQL (startup)` | Y | 0 | startup raw SQL | n | n | n | - |
| `bulletin_recipients` | BULLETIN | `raw SQL (startup)` | n | - | startup raw SQL | n | n | n | - |
| `bulletin_run_log` | BULLETIN | `raw SQL (startup)` | Y | 1 | startup raw SQL | n | n | n | - |
| `bulletin_search_profiles` | BULLETIN | `raw SQL (startup)` | Y | 9 | startup raw SQL | n | n | n | - |
| `bulletin_source_outcome` | BULLETIN | `raw SQL (startup)` | Y | 125 | startup raw SQL | n | n | n | - |
| `bulletin_source_registry` | BULLETIN | `raw SQL (startup)` | Y | 276 | startup raw SQL | n | n | n | - |
| `cm_billing_summaries` | CASE_MANAGEMENT | `app.case_management.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `cm_care_plans` | CASE_MANAGEMENT | `app.case_management.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `cm_discharge_records` | CASE_MANAGEMENT | `app.case_management.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `cm_government_cases` | CASE_MANAGEMENT | `app.case_management.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `cm_notes` | CASE_MANAGEMENT | `app.case_management.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `cm_patients` | CASE_MANAGEMENT | `app.case_management.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `actions` | CORE_SHARED | `app.models.enterprise_models` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `audio_files` | CORE_SHARED | `app.models.database` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `contexts` | CORE_SHARED | `app.models.enterprise_models` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `decisions` | CORE_SHARED | `app.models.enterprise_models` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `documents` | CORE_SHARED | `app.models.database` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `execution_queue` | CORE_SHARED | `app.models.enterprise_models` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `platform_agencies` | CORE_SHARED | `app.platform_config.models` | Y | 13 | 20260725_platform_config.py | n | n | n | - |
| `platform_data_sources` | CORE_SHARED | `app.platform_config.models` | Y | 14 | 20260725_platform_config.py | n | n | n | - |
| `platform_features` | CORE_SHARED | `app.platform_config.models` | Y | 9 | 20260725_platform_config.py | n | n | n | - |
| `platform_identifier_types` | CORE_SHARED | `app.platform_config.models` | Y | 18 | 20260725_platform_config.py | n | n | n | - |
| `platform_import_formats` | CORE_SHARED | `app.platform_config.models` | Y | 11 | 20260725_platform_config.py | n | n | n | - |
| `platform_jurisdictions` | CORE_SHARED | `app.platform_config.models` | Y | 57 | 20260725_platform_config.py | n | n | n | - |
| `platform_modules` | CORE_SHARED | `app.platform_config.models` | Y | 16 | 20260725_platform_config.py | n | n | n | - |
| `platform_pages` | CORE_SHARED | `app.platform_config.models` | Y | 11 | 20260725_platform_config.py | n | n | n | - |
| `platform_programs` | CORE_SHARED | `app.platform_config.models` | Y | 14 | 20260725_platform_config.py | n | n | n | - |
| `platform_tenants` | CORE_SHARED | `app.platform_config.models` | Y | 1 | 20260725_platform_config.py | n | n | n | - |
| `platform_themes` | CORE_SHARED | `app.platform_config.models` | Y | 2 | 20260725_platform_config.py | n | n | n | - |
| `platform_workspace_features` | CORE_SHARED | `app.platform_config.models` | Y | 8 | 20260725_platform_config.py | n | n | n | - |
| `platform_workspaces` | CORE_SHARED | `app.platform_config.models` | Y | 4 | 20260725_platform_config.py | n | n | n | - |
| `policy_validations` | CORE_SHARED | `app.models.enterprise_models` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `process_jobs` | CORE_SHARED | `app.models.enterprise_models` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `traceability` | CORE_SHARED | `app.models.enterprise_models` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `transcripts` | CORE_SHARED | `app.models.database` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `validation_queue` | CORE_SHARED | `app.api.validation_routes` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `agency_contacts` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `agency_metrics` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `ai_memory` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `applications` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `ats_activities` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `bench_candidates` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `bom_items` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `candidates` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `communication_logs` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `company_profiles` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `contract_staffing` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `contracts` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `customers` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `deal_registrations` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `dev_projects` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `employees` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `expenses` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `financials` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `follow_up_queue` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `invoice_line_items` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `invoices` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `job_postings` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `opportunities` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `outreach_logs` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `placement_outcomes` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `price_history` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `product_catalog` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `products` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `proposal_library` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `purchase_orders` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `quote_line_items` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `quotes` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `rfqs` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `saved_searches` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `submissions` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `supplier_contacts` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `supplier_metrics` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `supplier_price_snapshots` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `supplier_quote_files` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `supplier_quote_requests` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `suppliers` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `support_tickets` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `tasks` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `tax_jurisdictions` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `technical_library` | ERP_BUSINESS | `app.models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `migration_fields` | MIGRATION_TOOLING | `app.models.migration_models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `migration_logic_artifacts` | MIGRATION_TOOLING | `app.models.migration_models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `migration_manifest_versions` | MIGRATION_TOOLING | `app.models.migration_models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `migration_mapping_versions` | MIGRATION_TOOLING | `app.models.migration_models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `migration_mappings` | MIGRATION_TOOLING | `app.models.migration_models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `migration_profiling_results` | MIGRATION_TOOLING | `app.models.migration_models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `migration_projects` | MIGRATION_TOOLING | `app.models.migration_models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `migration_schemas` | MIGRATION_TOOLING | `app.models.migration_models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `migration_validation_runs` | MIGRATION_TOOLING | `app.models.migration_models` | n | - | 20260827_startup_table_coverage.py | n | n | Y | UNRELATED_TO_TEFCA |
| `output_templates` | REPORTING_SHARED | `app.api.templates` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `outputs` | REPORTING_SHARED | `app.models.database` | Y | 0 | 20260827_startup_table_coverage.py | n | n | n | - |
| `area1_mutation_log` | TEFCA | `migration only` | Y | 0 | 20260826_area1_mutation_audit.py | n | n | n | - |
| `evidence_relationship_path` | TEFCA | `app.Tefca.models` | Y | 0 | 20260824_evidence_provenance.py | n | n | n | - |
| `rce_correction_details` | TEFCA | `app.tefca_registry.rce.models` | Y | 1631 | - | n | n | n | - |
| `rce_curated_records` | TEFCA | `app.tefca_registry.rce.models` | Y | 23566 | - | n | n | n | - |
| `rce_ingestion_runs` | TEFCA | `app.tefca_registry.rce.models` | Y | 1 | - | n | n | n | - |
| `rce_issues` | TEFCA | `app.tefca_registry.rce.models` | Y | 36916 | - | n | n | n | - |
| `rce_rule_execution_history` | TEFCA | `app.tefca_registry.rce.models` | Y | 31 | - | n | n | n | - |
| `rce_source_intakes` | TEFCA | `app.tefca_registry.rce.models` | Y | 1 | - | n | n | n | - |
| `rce_source_records` | TEFCA | `app.tefca_registry.rce.models` | Y | 23566 | - | n | n | n | - |
| `review_cycles` | TEFCA | `app.tefca_registry.models` | Y | 0 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `review_decision_events` | TEFCA | `app.tefca_registry.models` | Y | 0 | 20260825_qa_decision_events.py | n | n | n | - |
| `review_records` | TEFCA | `app.tefca_registry.models` | Y | 43 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `review_reports` | TEFCA | `app.tefca_registry.models` | Y | 5 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `review_rules` | TEFCA | `app.tefca_registry.models` | Y | 10 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `review_samples` | TEFCA | `app.tefca_registry.models` | Y | 0 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `sample_entities` | TEFCA | `app.tefca_registry.models` | Y | 0 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `source_version_snapshots` | TEFCA | `app.Tefca.models` | Y | 0 | 20260824_evidence_provenance.py | n | n | n | - |
| `tefca_analyst_queue` | TEFCA | `app.Tefca.models` | Y | 0 | - | n | n | n | - |
| `tefca_connector_logs` | TEFCA | `app.Tefca.models` | Y | 38 | - | n | n | n | - |
| `tefca_dimension_evidence` | TEFCA | `app.Tefca.models` | Y | 1984 | - | n | n | n | - |
| `tefca_entities` | TEFCA | `app.Tefca.models` | Y | 2 | - | n | n | n | - |
| `tefca_entity_contacts` | TEFCA | `app.tefca_registry.rce.models` | Y | 18238 | - | n | n | n | - |
| `tefca_entity_endpoints` | TEFCA | `app.tefca_registry.models` | Y | 22 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `tefca_entity_findings` | TEFCA | `app.tefca_registry.models` | Y | 42 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `tefca_entity_identifiers` | TEFCA | `app.tefca_registry.models` | Y | 96803 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `tefca_entity_relationships` | TEFCA | `app.tefca_registry.models` | Y | 36225 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `tefca_entity_versions` | TEFCA | `app.tefca_registry.models` | Y | 23745 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `tefca_evidence_records` | TEFCA | `app.Tefca.models` | Y | 0 | - | n | n | n | - |
| `tefca_findings` | TEFCA | `app.Tefca.models` | Y | 100 | - | n | n | n | - |
| `tefca_import_batches` | TEFCA | `app.tefca_registry.models` | Y | 7 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `tefca_import_history` | TEFCA | `app.Tefca.models` | Y | 5 | 20260827_startup_table_coverage.py | n | n | n | - |
| `tefca_ppef_ingest_jobs` | TEFCA | `app.Tefca.models` | Y | 0 | - | n | n | n | - |
| `tefca_ppef_records` | TEFCA | `app.Tefca.models` | Y | 0 | - | n | n | n | - |
| `tefca_ppef_snapshots` | TEFCA | `app.Tefca.models` | Y | 0 | - | n | n | n | - |
| `tefca_priority_cases` | TEFCA | `app.Tefca.models` | Y | 0 | - | n | n | n | - |
| `tefca_qa_audit` | TEFCA | `raw SQL (startup)` | Y | 75 | startup raw SQL | n | n | n | - |
| `tefca_reg_audit_log` | TEFCA | `app.tefca_registry.models` | Y | 23812 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `tefca_reg_entities` | TEFCA | `app.tefca_registry.models` | Y | 23756 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `tefca_reports` | TEFCA | `app.Tefca.models` | Y | 0 | - | n | n | n | - |
| `tefca_review_cycles` | TEFCA | `app.Tefca.models` | Y | 0 | - | n | n | n | - |
| `tefca_reviews` | TEFCA | `app.Tefca.models` | Y | 50 | - | n | n | n | - |
| `tefca_source_cache` | TEFCA | `app.Tefca.models` | Y | 0 | - | n | n | n | - |
| `tefca_verification_checks` | TEFCA | `app.tefca_registry.models` | Y | 177 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `tefca_verification_jobs` | TEFCA | `app.tefca_registry.models` | Y | 177 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `tefca_verifications` | TEFCA | `app.tefca_registry.models` | Y | 43 | 20260725_tefca_registry_tables.py | n | n | n | - |
| `alembic_version` | UNKNOWN | `-` | Y | 1 | - | n | n | n | - |

### Appendix A.1 — counts

| domain | tables | live | in the 61 |
|---|---:|---:|---:|
| ERP_BUSINESS | 45 | 0 | 45 |
| TEFCA | 45 | 45 | 0 |
| CORE_SHARED | 24 | 24 | 0 |
| BULLETIN | 10 | 9 | 0 |
| MIGRATION_TOOLING | 9 | 0 | 9 |
| CASE_MANAGEMENT | 6 | 0 | 6 |
| AUDIT_SHARED | 3 | 2 | 1 |
| AUTH_SECURITY | 3 | 3 | 0 |
| REPORTING_SHARED | 2 | 2 | 0 |
| UNKNOWN | 1 | 1 | 0 |
| **total** | **148** | **86** | **61** |

### Appendix A.2 — verdict for the 61

| verdict | count |
|---|---:|
| UNRELATED_TO_TEFCA | 61 |
