# Phase 6 evidence correction — internal engineering record

**Audience:** DocuAction engineering. Not a COR deliverable and not written for one.
**Branch:** `fix/tefca-stabilization`
**Phase 6 commit:** `44a8c77` · **Phase 6.5 commit:** `6ea5746`
**Date:** 2026-08-23

---

## 1. The original run

One execution of `scripts/phase6_population_enrichment.py` against the verified
23,566-record Area-1 delivery produced, under `rule_version = phase6-bulk-1.0.0`:

| | |
| --- | --- |
| Observations | 164,962 (7 sources × 23,566) |
| Relationship hops | 39,749 |
| Evidence digest | `84384bcd7aef04b137e30eb88848e2ee` |
| Hop digest | `1cb09a1152d9af70423e3ab5dc514756` |
| Correlation id | `9a32a35d-e6e3-40d3-90c6-2d1e43e0bab3` |

**Known deficiency, not retrofitted:** there is no execution identifier for the
enrichment run. `correlation_id` carries the *intake* id, which names the
delivery rather than the run, and `rce_ingestion_runs` describes the RCE
quality-rule run, not this one. The run is therefore identifiable only by its
`rule_version`. Adding an identifier now would mean writing to 164,962
historical rows, so it was left alone and recorded here instead.

## 2. Defects found in Phase 6.5

Both were introduced by the Phase-6 implementation. Neither was a data problem.

**D-1 — PPEF relationship hops used the wrong vocabulary.**
`relationship_type` held *component names* (`PRACTICE_LOCATION`,
`REASSIGNMENT`) rather than values from the approved `PpefRelationship` enum.
Both hop types recorded the same `NPI → ENRLMT_ID` traversal under two
different labels, so the table contained one traversal duplicated rather than
two distinct ones. `ppef_component` and `source_row_key` were NULL throughout.
The traversals PPEF actually publishes — enrolment to practice location, to
secondary specialty, to additional NPI, to the receiving enrolment of a
reassignment — were never recorded.

**D-2 — Address agreement was computed but never persisted.**
The Phase-6 report quoted "230 address mismatches". That figure was produced by
an ad-hoc script while writing the report. It classified a record as a mismatch
**only when the state differed**, so 8,331 street-line disagreements were
silently counted as matches. The number was both unreproducible and wrong.

## 3. Why the original evidence was preserved

The 1.0.0 rows were not updated and not deleted. That run happened; rewriting it
would destroy the answer to *"what did the system observe on the day it ran?"*,
which is the first question an audit asks and the one the append-only design
exists to answer. A correction is a new version that supersedes the old one by
being newer, never an edit that erases it.

Verified after correction: 1.0.0 still holds 164,962 observations, its evidence
digest is byte-identical, and all 39,749 original hops remain — including their
defective vocabulary, which is part of the historical record.

## 4. The corrected version

`rule_version = phase6-bulk-1.1.0`, produced by
`scripts/phase6_evidence_correction.py`.

| | 1.0.0 | 1.1.0 |
| --- | --- | --- |
| Observations | 164,962 | 188,528 |
| Relationship hops | 39,749 | 116,218 |
| Hops missing component / row key / version | all | **0 / 0 / 0** |
| Address comparisons persisted | 0 | 47,132 |

### 4.1 Relationship correction

Hops now use the approved `PpefRelationship` vocabulary, one hop per **source
row** rather than per component:

| relationship_type | ppef_component | hops |
| --- | --- | --- |
| `has_practice_location` | PRACTICE_LOCATION | 65,298 |
| `enrolled_as` | ENROLLMENT | 42,890 |
| `has_additional_npi` | ADDITIONAL_NPIS | 5,091 |
| `reassigns_benefits_to` | REASSIGNMENT | 2,308 |
| `has_secondary_specialty` | SECONDARY_SPECIALTY | 631 |

Secondary Specialty and Additional NPIs were acquired by Phase 6 and never
represented. They are added here as **components**, not as new authoritative
sources — both already exist in `PPEFComponent` and `PpefRelationship`, so this
is provenance, not a methodology change. The `Source` enum is untouched.

`source_row_key` is a deterministic SHA-256 prefix over the row's own content.
No PPEF component publishes a row identifier, so the key is derived, never
guessed; the same row always yields the same key.

### 4.2 Address correction

`app/Tefca/address_comparison.py`, `ADDRESS_RULE_VERSION = 1.0.0`. Pure
functions, USPS-style normalisation applied before comparison, six outcomes kept
distinct. A formatting difference is not a conflict; absent data is
`INSUFFICIENT_DATA`, never a conflict.

| | RCE → NPPES | RCE → PPEF |
| --- | --- | --- |
| EXACT_MATCH | 7,070 | 0 |
| NORMALIZED_MATCH | 3,299 | 14,807 |
| CONFLICT | 8,584 | 1,842 |
| INSUFFICIENT_DATA | 23 | 6,917 |
| SOURCE_UNAVAILABLE | 4,590 | 0 |

PPEF can never yield EXACT_MATCH: the practice-location extract publishes
`ENRLMT_ID, CITY_NAME, STATE_CD, ZIP_CD` and **no street line**, so asserting
full agreement would claim agreement on a field the source never supplied. The
comparison is scoped to city/state/ZIP and records `line` as not compared. Where
a provider publishes several practice locations, the best result across them is
taken — a third differing location is not a finding about the entity.

**The "230" figure is corrected to 8,584** (NPPES). The old number counted only
state-level disagreement; `NPPES:state` conflicts alone are 207, which is where
230 came from.

## 5. Canonical evidence selection

One rule, one place: `app/Tefca/evidence_version.py`.

```
CURRENT = the newest entry in APPROVED_RULE_VERSIONS
HISTORY = every earlier entry — queryable, never deleted
```

Applied in exactly one query path,
`ReportDataService._dimension_rows`. That method de-duplicates on
`(entity, dimension)` with a `generation_timestamp` tie-break which the
population runs leave NULL — so without the filter the tie-break compared `""`
to `""` and the surviving row was whichever the database returned first. That is
not double-counting; it is worse, because the number would change between
identical runs. Rows with no `rule_version` predate versioning and are left
alone.

## 6. Triage impact

`TRIAGE_VERSION` 1.0.0 → 1.1.0.

| Disposition | 1.0.0 | 1.1.0 | Δ |
| --- | --- | --- | --- |
| READY_FOR_ANALYST | 28 | **28** | 0 |
| METHODOLOGY_PENDING | 23,566 | 33,992 | +10,426 |
| INFORMATIONAL_ONLY | 141,359 | 154,499 | +13,140 |
| SOURCE_LIMITATION | 9 | 9 | 0 |
| DUPLICATE_CONSOLIDATED | 0 | 0 | 0 |

The +10,426 is exactly the address conflicts (8,584 + 1,842). They are
`METHODOLOGY_PENDING`, blocked on `D4_ADDRESS_MATERIALITY`, **not** analyst work:
the RCE delivers a *registered* address while NPPES and PPEF publish *practice
locations*. Those are different kinds of address, and no approved methodology
says how large a difference between them has to be before it means anything.
Queueing all 10,426 would set that threshold at "any difference at all";
suppressing them would set it at "never". Both are methodology decisions, so the
condition is named and counted instead.

One rule needed narrowing during this work: "NPPES returned NO_MATCH" had
signalled an identity anomaly, and NPPES now also contributes an *address*
observation where NO_MATCH means "no address to compare". Left unscoped it would
have queued roughly 4,600 entities as identity anomalies on the strength of a
missing address field. The rule is now scoped to the identity dimension.

## 7. What was not changed

Area 1 (23,566 records, digest `24524f70c370d6c42a2b03d5385295a5`, artefact hash
matching, file read-only). The 43 historical determinations — still 0
`reportable_at`, 0 `reviewer_resolution`, 0 decision events. No PASS and no FAIL
at either version. `app/bulletin_intelligence/` and `app/tefca_registry/ai/`
untouched.
