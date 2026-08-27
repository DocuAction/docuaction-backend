# Monthly ONC Delivery Model — September 2026 onward

**Status:** DEV design record, written during the August 26 2026 DEV ARC rehearsal.
**Scope:** how the September delivery and every delivery after it is received,
compared against August, and reported — without overwriting August.

Every number in this document was measured against the real August 21 2026 ONC
delivery in DEV (`ONC-ASTP-2026-08-21`, intake
`95d78cf6-e5a2-465c-acdc-6e451e05b672`, 23,566 records). Nothing here is
estimated, and no second delivery was fabricated to produce it.

---

## 1. The rule that decides everything else

**Each monthly file becomes a NEW immutable intake. August is never reopened.**

`rce_source_intakes` already models a delivery as a first-class row with its own
`sha256`, `received_at`, `received_by`, `record_count` and `schema_fingerprint`.
September is a second row in that table, with its own 23,000-odd children in
`rce_source_records`. The two deliveries coexist permanently.

This is enforced in the database, not merely intended. As `docuaction_app` —
the runtime role — Area 1 carries `SELECT` and `INSERT` at table level and
`UPDATE` on named lifecycle columns only:

| Table | UPDATE-able columns | Everything else |
|---|---|---|
| `rce_source_intakes` | `status`, `error` | no UPDATE |
| `rce_source_records` | `promotion_status`, `canonical_entity_id` | no UPDATE |
| `rce_ingestion_runs` | `run_status`, `completed_at`, `records_evaluated`, `issues_generated` | no UPDATE |
| `rce_rule_execution_history` | *(none)* | no UPDATE |

`DELETE`, `TRUNCATE`, `ALTER`, `DROP` and `SET ROLE docuaction_owner` are all
refused with SQLSTATE 42501. `raw_line`, `record_sha256`, `parsed`, `sha256`,
`received_at` and `received_by` have no UPDATE grant at all. September therefore
*cannot* overwrite August through the application, whatever the application asks
for. Verified by 30 probes on 2026-08-27, all inside rolled-back savepoints.

There is no delete path for Area 1 by design. A delivery ingested in error is
remedied by PITR restore, not by DELETE.

---

## 2. Identity resolution — what the August data actually supports

The instinct is to pick "the" identifier and join on it. The August delivery
shows why that fails. Measured over all 23,566 records:

| Field | Populated | Distinct | Unique? | Worst collision |
|---|---:|---:|---|---|
| `id` (RCE Org OID) | 23,566 (100%) | 23,566 | **Yes — 1:1** | — |
| `HCID` | 23,566 (100%) | 23,562 | No | 4 values on 2 rows each |
| `TEFCAID` | 23,566 (100%) | 23,325 | No | one value on **69 rows** |
| `NPI` | 18,982 (80.5%) | 18,675 | No | one value on **12 rows** |
| `AAID` | 7,447 (31.6%) | 7,444 | No | sparse *and* colliding |
| `name` | 23,566 (100%) | 23,284 | No | 282 duplicate names |
| `name` + ZIP + state | 23,566 | 23,337 | No | 229 collisions |
| `partOf` | 23,566 (100%) | 300 | Not an identity — a parent pointer |
| `sequoiaorgtype` | 23,566 | 2 | Participant (11,077) / Subparticipant (12,489) |
| `active` | — | — | 22,594 active / 972 inactive |

Three consequences:

1. **`id` is the join key, but it is not a guarantee.** It is 1:1 across August,
   which makes it the correct primary key for month-over-month matching. It is
   1:1 *in one observed delivery* — that is evidence, not a contract. ONC has not
   warranted it stable or non-reissued, so the pipeline must detect the day it
   stops being 1:1 rather than assume it never will.
2. **No fallback identifier is universally unique or universally present.** A
   TEFCAID shared by 69 rows and an NPI absent from a fifth of the file cannot
   carry identity on their own. They are corroborating signals.
3. **Every match is scored, never assumed.** The existing resolver
   (`entity_resolver.py`) already orders resolution cheapest-and-most-defensible
   first: exact identifier, then USPS address normalisation, then Jaro-Winkler
   name similarity, then — only if those are inconclusive and only in advisory or
   production AI mode — adjudication that a human accepts or rejects. That order
   is the right one for monthly deltas and needs no change.

### The matching ladder for September

```
1. id (RCE Org OID) equal                    -> SAME ENTITY (primary)
2. id absent/changed, HCID equal + name similar
                                             -> CANDIDATE, evidence required
3. NPI equal + address agrees                -> CANDIDATE, evidence required
4. name + normalised address agree           -> CANDIDATE, analyst decides
5. nothing above                             -> NEW (or REMOVED on the other side)
```

Rungs 2–4 never auto-merge. They open a case.

**Guard to add before September:** assert `count(*) = count(distinct id)` on the
new intake at parse time. If September's `id` is not 1:1, the primary key
assumption has failed and the delta must not be computed until an analyst says
how. Better to refuse than to silently mismatch 23,000 rows.

---

## 3. Change classification

Computed per `id` between the previous accepted delivery and the new one. Each
class is a statement about **Government-delivered data only** — see §4.

| Class | Condition |
|---|---|
| `NEW` | `id` present in September, absent in August |
| `UNCHANGED` | `id` present in both; all 41 mapped fields byte-identical |
| `CHANGED` | `id` present in both; ≥1 non-identifier field differs |
| `INACTIVE` | `active` moved 1 → 0 |
| `REACTIVATED` | `active` moved 0 → 1 |
| `REMOVED_FROM_DELIVERY` | `id` present in August, absent in September |
| `IDENTIFIER_ADDED` | an identifier field (NPI/HCID/TEFCAID/AAID) went blank → populated |
| `IDENTIFIER_REMOVED` | an identifier field went populated → blank |
| `IDENTIFIER_CHANGED` | an identifier field changed populated → different value |
| `RELATIONSHIP_CHANGED` | `partOf` or `orgManagingOrg` differs |
| `POTENTIAL_DUPLICATE` | two September `id`s resolve to one August entity (rungs 2–4) |
| `CONFLICT` | classification rules disagree, or a match is ambiguous between ≥2 August entities |

`REMOVED_FROM_DELIVERY` is deliberately **not** called "deleted". Absence from one
monthly file is not an assertion that an organisation ceased to exist; it is an
observation about a file. The August rows stay exactly where they are, and the
August entity keeps its history.

`CONFLICT` is a real outcome, not an error state. It routes to an analyst.

### Where the classification is stored

A delta belongs to the *pair* of deliveries, not to either one. It is derived
data and must never be written into Area 1. `tefca_entity_versions` already
holds immutable per-entity snapshots with `version_number`, `change_reason` and
`changed_by`, and `tefca_reg_audit_log` already has the matching vocabulary —
`identifier_added`, `identifier_removed`, `relationship_created`,
`relationship_ended`, `entity_deactivated`, `entity_reactivated`,
`version_created`, `merge_executed`. September writes a new version row per
changed entity and leaves the August version untouched.

**Gap:** there is no `rce_delivery_delta` table keyed by
(previous_intake_id, new_intake_id, id). The per-entity version history can
reconstruct a delta but cannot answer "show me everything that changed between
August and September" in one query. Recommend adding it before September; it is
additive and touches nothing existing.

---

## 4. Telling the five kinds of change apart

This is the question the contract actually turns on, and the schema already
separates it by *where the value lives*, not by a flag:

| Kind of change | Lives in | Actor recorded | Area 1 touched |
|---|---|---|---|
| **Government change** | a new `rce_source_records` row under a new intake | `rce_source_intakes.received_by` + `received_at` | new rows only; prior delivery untouched |
| **Normalisation change** | `rce_curated_records` + `rce_correction_details` with `correction_authority='AUTO_SAFE'`, `approval_actor` NULL | `corrected_by` | never |
| **Analyst correction** | `rce_correction_details` with `correction_authority='HUMAN_REQUIRED'`, plus `rce_issues.resolution` | `corrected_by`, `rce_issues.resolved_by` | never |
| **Reviewer decision** | `rce_issues.resolution` = APPROVED/REJECTED/WAIVED, and `review_decision_events` for ARC determinations | `resolved_by` / `actor_user_id` | never |
| **External-source change** | `tefca_evidence_records` / `tefca_verification_checks` | connector + `retrieved_at` | never |

The August rehearsal demonstrated the first three concretely: 1,631 AUTO_SAFE
corrections were written with `approval_actor` NULL and `corrected_by` set to the
authenticated analyst, while the 138 HUMAN_REQUIRED issues stayed HUMAN_REQUIRED
and none was silently reclassified. Area 1's corpus digest was byte-identical
before and after
(`2644075fa1417b1ffce92a0e49e646eff9fa5a8ed79a30c627cac3546f694dfe`).

**The distinction that must never blur:** an AUTO_SAFE normalisation and an
analyst correction can produce the same curated value. They are told apart by
`correction_authority` and by whether `approval_actor` is populated — never by
inspecting the value. A report that cannot say which of the two produced a figure
is not defensible, so `correction_authority` is carried through to reporting.

---

## 5. Sequence for September

Same pipeline as August. Steps 1–4 are new; the rest is unchanged.

1. **Capture the recovery point** before anything is written.
2. **Ingest as a NEW delivery** — never `--force` onto the August intake.
   Duplicate-content detection (`sha256`, `duplicate_of_intake_id`) will catch a
   re-upload of the August file.
3. **Assert `id` is 1:1** on the new intake (§2 guard). Stop if it is not.
4. **Compute the delta** against the last accepted delivery and classify (§3).
5. Quality run → issue ledger.
6. Curate (Area 2) — **once**; see the known gap below.
7. Analyst adjudicates HUMAN_REQUIRED; reviewer approves; QA signs off.
8. Promote, verify, reconcile.
9. Report, PDF, artifact.

Only records classified `NEW`, `CHANGED`, `CONFLICT` or `POTENTIAL_DUPLICATE`
need analyst attention. `UNCHANGED` records inherit their prior determination and
must be *reported as inherited*, with the delivery that established them cited —
not re-adjudicated, and not presented as a fresh finding.

---

## 6. Known gaps to close before September

1. **`curate_delivery()` has no re-run guard.** It inserts one curated row per
   source record with no check for rows it already created, so running it twice
   on one intake silently doubles Area 2 and breaks
   `every_source_record_curated`. August was curated exactly once. September is
   the first delivery where an operator might plausibly re-run it. Add a guard
   that refuses (or explicitly supersedes) when curated rows already exist.
   *(`app/tefca_registry/rce/curation.py`, `curate_delivery`.)*
2. **No `rce_delivery_delta` table** (§3).
3. **No `id`-uniqueness assertion at parse time** (§2).
4. **Excel is not parseable** — unchanged from the existing runbook. Decide the
   conversion and which artifact is the original of record.
5. **Route shadowing:** `GET /api/reports/sow` is shadowed by
   `GET /api/reports/{report_id}` and returns 404 `No report exists with id
   sow`. The same class of bug is already noted and worked around for
   `/qa-queue` in `review_routes.py`. Cosmetic for ingestion, but it will
   mislead anyone probing the SOW families.

---

## 7. What this model deliberately does not do

- It does not merge entities automatically. A `POTENTIAL_DUPLICATE` is evidence
  for ONC, not authority to combine two organisations.
- It does not delete or supersede prior Government records. Ever.
- It does not treat absence from a delivery as deactivation.
- It does not let an external source (NPPES, PECOS, LEIE, SAM.gov) change a
  delivered value. External findings are evidence attached to a record; the
  Government value stands until ONC changes it.
