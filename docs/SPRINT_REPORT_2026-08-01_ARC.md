# TEFCA ARC — Tasks 3–5 Operational Engine

**Contract:** 7571MN26F80064 · **Date:** 2026-08-01 · **Gate:** tests pass + /health 200 — met.

---

## Delivered

| | |
|---|---|
| Backend tests | 188 → **245** (0 failed, 9 skipped) |
| New tables | **6** — review_rules, review_records, tefca_verifications, review_samples, sample_entities, review_reports |
| New endpoints | **17** under `/api/tefca/arc/*` |
| Existing `tefca_reg_entities` schema | **unmodified**, per instruction |
| Deployments | dev + prod, both verified |

Full Monday workflow proven end to end on dev: seed → NPI validation → verify
(5-state) → classify B1–B4 → review ID → sample → weekly report → archived HTML
→ B3 resolution → priority review.

---

## Three defects found by running it, not by reading it

### 1. Every entity was being classified B4 "excluded" — the worst error available

`SourceResult.success` means **the query completed**, not that the entity was
found or excluded. OIG LEIE returns `ok()` whenever the exclusions CSV was
readable; the actual verdict is `data["excluded"]`. Reading `success` as the
answer marked every entity whose lookup merely succeeded as **excluded → B4**,
which is disqualifying.

The same applies to NPPES and PECOS: both return `ok()` for found *and*
not-found, distinguished only by `data["found"]`.

Fixed to read the payload. Confirmed on dev:

```
Inova (real NPI)      nppes verified · pecos verified · oig_leie clear  → B1
Richmond (synthetic)  nppes not_found · pecos not_found · oig_leie clear → B3
```

Regression tests added for both semantics.

### 2. `RULE-005` at priority 50 let a debarred entity classify B1

Its own description says "disqualifying regardless of what other sources say",
but evaluating it *last* meant an OIG-excluded or SAM-debarred entity with clean
NPPES/PECOS matched `RULE-001` first and was reported as **no discrepancy**.

Moved to **priority 5** so disqualifying conditions are checked first. A
provider reported clean when they are excluded is the most consequential error
this engine could make.

### 3. `RULE-002` swallowed real `not_found` findings

The partial-pass rule fired whenever *any* listed source was merely unqueried —
so a genuine PECOS `not_found` was reported as a clean B1. Added `none_of`
guards: a source that answered "no record" **answered**, and that is a finding,
not an outage. This preserves the rule's stated intent while closing the hole.

---

## Design decisions worth recording

**Five verification states, kept distinct end to end.**
`verified | not_found | not_checked | unavailable | failed`. `unavailable`
(source unreachable) never counts against an entity; `not_found` (source
reached, no record) does. A two-state model silently converts a third party's
outage into a finding against a provider.

**Sources with no connector are disclosed, not omitted.** `sam_gov`,
`state_registry` and `irs` are reported as `not_checked` **with a reason**. A
source missing from the response reads as an oversight; "not_checked — no
connector" is a stated gap.

**No match defaults to B3, never B1.** If the rule set does not describe a case,
that is precisely "inexplicable" and needs a human — not a silent pass.

**Rules are versioned rows, never edited.** `PUT` retires the current version
and inserts version+1; there is no `DELETE`. Every classification stores
`rule_code` **and** `rule_version`, so a review from Q3 stays explainable after
ONC changes guidance in Q4.

**Reports are archived, not regenerated.** Data *and* rendered HTML are stored.
Re-running a period mints a suffixed ID rather than overwriting what was
delivered. Regenerating on read would quietly rewrite history.

**Limitations are mandatory.** Always present, minimum "None identified." A
report that omits what could not be checked invites the reader to assume full
coverage.

**Wilson interval, not normal approximation.** At these sample sizes and rates
the normal interval routinely runs below zero, which is not a printable figure
in a federal deliverable.

**Seeds are refused on production.** `ENVIRONMENT=production` returns 403 with a
reason. The registry is the population every sample and report is drawn from;
demo entities would corrupt every downstream figure and that is not correctable
after the fact. Verified: prod `dev/seed` is gated.

---

## Deviations from the brief — flagged for your call

**1. Router moved to `/api/tefca/arc/*`, not `/api/tefca/*`.**
The legacy TEFCA module already owns `/api/tefca/reviews` and
`/api/tefca/reports`. Mounting there **silently shadowed** the new endpoints
behind the older ones, which return a completely different payload — caught
during the workflow run when `/reviews` returned legacy records with no
`review_id`. Verified the legacy routes still respond.

**2. Classification is stored on `review_records`, not on `tefca_reg_entities`.**
Task 1.6 asked for `discrepancy_bucket` etc. on the entity; your later
instruction said not to modify that schema. The later instruction wins. Nothing
is lost — the review record is the addressable, snapshot-bearing home for a
classification, and `latest_review_id` can be derived by query.

**3. Three of the five "real" NPIs fail the CMS check digit.**

```
Johns Hopkins  1316966918  INVALID
Mayo Clinic    1063626960  INVALID
Cleveland      1649278978  INVALID
Mass General   1982604013  valid
Inova Fairfax  1205839487  valid
```

They were seeded as given, and the three invalid ones are flagged by the
validator exactly as designed — so they still serve as fixtures. But they cannot
demonstrate the B1 path. **Inova and Mass General are the two that prove it**,
and Inova returned a genuine B1 on dev. Worth re-sourcing the other three if
they are meant to be real reference records.

---

## What is NOT built

* **`multiple_source_conflict`** is always `False` — a placeholder. Only the
  NPPES/PECOS pairwise conflict is computed.
* **State and IRS connectors** do not exist; both report `not_checked`. IRS is
  blocked on the registry holding no EIN (see the previous analysis).
* **Quarterly trend-over-weeks** aggregates the same structure; per-week trend
  series is not implemented.
* **Excel report export** — the endpoint list included
  `/reports/weekly/{date}/excel`; HTML and JSON are delivered, Excel is not.

---

## Verified on production

```
/api/config                       production
/api/tefca/arc/review-rules       401 (mounted, guarded)
/api/tefca/arc/samples            401
/api/tefca/arc/reviews            401
/api/tefca/arc/reports            401
/api/tefca/arc/priority-review    401
/api/tefca/registry/dev/seed      401  (and 403 by environment guard)
legacy /api/tefca/reports         401  (not shadowed)
bulletin /latest                  200  (public, unaffected)
bulletin /costs                   401  (guarded, unaffected)
```

Production carries the engine and **no seeded data**, as required.
