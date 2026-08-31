# TEFCA ARC — MONTHLY / RECURRING DELIVERY DELTA MODEL

> ## INTERNAL AGT — NOT FOR CLIENT DISTRIBUTION
> **No Government row-level values.** Aggregate figures only.

**Contract:** 7571MN26F80064 · HHS/ONC ASTP · **Date:** 2026-08-29
**Master Step:** #12 · **Delta version:** 1.0.0
**Implementation:** `app/tefca_registry/rce/delivery_delta.py`
**Certification:** `tests/test_delivery_delta.py` — 24 synthetic tests

**Supersedes in part:** `docs/monthly_delivery_model.md` (the August 26 design
record). That document specified the model; this one records what was built,
where the build differs from the specification, and what was proven.

---

## 1. The rule that decides everything else

**A new delivery is a NEW immutable snapshot. It never updates the old one.**

```
Delivery N      immutable source snapshot, kept forever
Delivery N+1    a new immutable snapshot
                     |
                     v
                delta engine (read-only)
                     |
    NEW - CHANGED - UNCHANGED - NOT_PRESENT_IN_CURRENT_DELIVERY
                     +  HELD reported alongside
```

Area 1 is append-only and has no delete path, so Delivery N cannot be
overwritten by anything the application does.

## 2. Persistence decision: DERIVED

**No new table. No migration.**

The delta's only inputs are `rce_source_intakes` (receipt time, schema
fingerprint, status) and `rce_source_records` (`source_rce_id`,
`record_sha256`, `parsed`) — both append-only. Immutable inputs plus a
deterministic function give a reconstructable answer at any future time, so a
stored delta could only ever be right or stale. A delta table would be a cache
of two immutable tables.

This confirms the August assessment's suspicion that no monthly table was
required — now verified against current code rather than held as an
expectation. `monthly_delivery_model.md` §3 listed "no `rce_delivery_delta`
table" as a gap to close; the finding of this gate is that it is not a gap.

## 3. Comparison identity

| | |
|---|---|
| Key | `rce_source_records.source_rce_id` — the delivered `id`, lifted out of `parsed` and indexed |
| Normalisation | none; compared as delivered |
| Raw or curated | **raw source** — the delta is a statement about what ONC sent |
| Collision behaviour | comparison **refused** — `NON_COMPARABLE_DUPLICATE_IDENTITY` |

Deliberately **not** used as identity: organisation name, NPI alone, TEFCAID
alone, address, row position, or the database surrogate UUID.

**Stated limitation.** `id` was measured 1:1 across the one delivery on record
(23,566 distinct of 23,566). That is evidence from a single delivery, not a
warranty of cross-delivery stability — ONC has not warranted it stable or
non-reissued. The engine therefore refuses on duplicate identity rather than
assuming uniqueness continues to hold.

## 4. Which delivery is "previous"

Reuses the selection already in `rce_report_data.get_delta_from_previous`
rather than inventing a second rule: strictly earlier `received_at`, excluding
`FAILED` intakes, newest first, with `id` breaking a same-instant tie so the
order is total. Database insertion order is never used — a backfilled delivery
must not become "previous" to something older than itself.

An explicit `previous_intake_id` may be supplied to compare a named pair, which
is how an out-of-order or backfilled delivery is handled.

## 5. Hash strategy

`record_sha256` is SHA-256 over `raw_line`, and the reader strips the line
terminator before storing it. Therefore:

| Difference | False CHANGED? | Why |
|---|---|---|
| CRLF vs LF | **No** | terminator stripped before hashing |
| Row order in the file | **No** | line number is not in the hash — proven by test |
| Delivery-level metadata | **No** | not part of the record hash |
| JSON key order in `parsed` | **No** | the hash is over `raw_line`, not `parsed` |
| **Field (column) order** | would be | **guarded** — comparison refused unless schema fingerprints match |

Historical hashes are unchanged and no new fingerprint was introduced.

**Hash first, diff second:** equal hash means UNCHANGED with no further work;
only a differing hash is diffed field by field over the 41 authoritative fields.

## 6. Classification semantics

| State | Meaning |
|---|---|
| `NEW` | identity in the current delivery, not in the previous one |
| `CHANGED` | identity in both; at least one of the 41 source fields differs |
| `UNCHANGED` | identity in both; the delivered line is byte-identical |
| `NOT_PRESENT_IN_CURRENT_DELIVERY` | identity was in the previous delivery, absent from this one |
| `BASELINE_DELIVERY` | first controlled delivery; nothing to compare against |
| `NON_COMPARABLE_SCHEMA_CHANGE` | schema fingerprints differ — refused |
| `NON_COMPARABLE_DUPLICATE_IDENTITY` | an identity appears twice — refused |

**Absence is not removal.** `NOT_PRESENT_IN_CURRENT_DELIVERY` is an observation
about a *file*. It is never deletion, termination, deactivation, revocation or
an adverse finding, and nothing downstream is changed by it. What absence should
trigger operationally is an ONC question this engine does not answer.

*Naming note.* `rce_report_data.get_delta_from_previous` counts the same set
under the key `removed_ids`. Same population, older name; the rendered template
already labels it "Identifiers absent from this delivery", so no incorrect
output reaches a reader. The published key was left alone rather than broken,
and new code uses the neutral term.

**HELD is orthogonal.** It is a processing state of a record in one delivery,
not a fifth delta class. A record can be NEW and HELD, or UNCHANGED and HELD.
It is reported alongside the classification, never instead of it.

**The first delivery is not "all NEW."** It is `BASELINE_DELIVERY` with
`comparable: false` and a stated reason — "nothing to compare against" and
"nothing changed" are different facts, and only one is true of a first delivery.

**Reappearance** is explanatory metadata (`reappearance_context`), not a fifth
state. A record absent in N and back in N+1 is `NEW` relative to N — but the
history says it is not first-ever seen, so it is never reported as such.

## 7. Source change vs curated equivalence

Certified: a Month-1 postal code that FMT-001 would zero-pad, delivered in
Month 2 already padded, is **CHANGED**. The curated values would agree; ONC sent
different bytes, and a delta that hid that would misreport the delivery. The
delta speaks about the Government source; curation is a separate representation
and is not consulted.

## 8. Reprocessing scope

`reprocessing_scope()` is **advisory and conservative**. It reports which areas a
change touches — identity verification, address verification, relationship
interpretation, contact-only — using the groupings from the approved 41-field
matrix. It invalidates nothing and re-runs nothing: how stale external evidence
becomes on a source change is not contractually established and was not
invented here.

UNCHANGED records generate no new DQ work, no new review case and no new entity
version. Verification-refresh policy is deliberately kept separate from "source
unchanged" and remains an open operational policy question.

## 9. Rule-set evolution

A delivery's issues stay bound to the run and `rule_set_version` that produced
them. The delivered population remains at 1.0.0; a future delivery may run
1.1.0. **Delta classification is based on source change alone** and is unaffected
by which rule set ran — structurally guaranteed, because the comparison reads
only `rce_source_records` and never touches `rce_issues`.

## 10. Idempotency, concurrency, replay

The same pair rerun gives identical counts, classifications and changed-field
lists, and writes nothing. Three simultaneous comparisons on independent
connections agree exactly (certified in a throwaway schema in a separate
database). There is no persisted delta, so there is nothing to duplicate and no
lock to take.

## 11. Performance

Two indexed passes — one per delivery — over `(source_intake_id)`, building a
dict keyed by `source_rce_id`. Set operations give NEW / common / absent. The
field diff runs only on hash-differing identities, fetched in a single `IN`
query.

**Complexity O(N_current + N_previous)** in rows, plus one query proportional to
the number of changed records. No pairwise comparison, no per-row query, no
external API call. For 23,566 records that is two scans and one small fetch —
no O(N^2) behaviour and no per-row API fan-out.

## 12. Audit reconstruction

For any classification a reviewer can recover: current intake, previous intake,
delta version, stable identity, previous hash, current hash, the exact changed
fields with before and after values, the Area 1 source record id, and the HELD
state in the current delivery. Certified: the hashes the delta cites are the
ones Area 1 actually stores.

## 13. Test evidence — 24 synthetic tests

First delivery · exact Month-2 classification · exact changed fields per record
(none missing, none false) · multiple changes as one record · HELD orthogonality
· HELD-to-processable and processable-to-HELD · absence preserves history ·
reappearance · idempotent rerun · concurrent comparison · duplicate identity
refused · schema mismatch refused · out-of-order refused · self-comparison
refused · source change vs curated equivalence · row-order insensitivity ·
advisory scope · audit reconstruction · writes nothing · synthetic fixtures only.

**Mutation-tested:** disabling changed-field detection fails 7 tests; weakening
duplicate-identity protection fails its test; breaking the hash short-circuit
fails 8. Code restored byte-identically after each.

## 14. Government readiness — architecture only

| | |
|---|---|
| Current delivery usable as baseline | **YES** |
| Stable comparison identity | **YES** — 23,566 distinct of 23,566, 0 null |
| Record fingerprint | **YES** — 23,566 of 23,566 hashed, 23,566 distinct |
| Schema fingerprint | **YES** — `1cd655e9120dc9d0...` |
| History preservation | **YES** — append-only Area 1 |
| **Ready for a controlled future delivery** | **YES** |

The engine currently reports the sole delivery as `BASELINE_DELIVERY`, which is
correct. **This is architecture readiness only and is not authorization to
ingest Government data.**

## 15. What this gate deliberately did not do

No sampling or periodic resampling (Master Step #13). No Excel export. No report
redesign. No client DOCX edit. No Government delivery was re-run, re-ingested or
modified, and no second Government delivery was fabricated.
