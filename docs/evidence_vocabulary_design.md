# CANONICAL EVIDENCE VOCABULARY — DESIGN

**Date:** 2026-08-22 · **Branch:** `fix/tefca-stabilization` · **Status:** DESIGN ONLY — no vocabulary is changed, no column is added.

**Why now.** The stabilization gate found that the evidence layer produced a
field-level observation under one name while the classifier looked for another,
and nothing detected it for a full production run. That was one instance of a
general condition: **DocuAction has four overlapping vocabularies and no rule
about which layer owns which.** This must be settled *before* an Observation Store
persists terminology at scale.

---

## A. CANONICAL OBSERVATION STATES

Eight states. **None of them independently equals B1, B2, B3 or B4.** They are
statements about a lookup, not determinations about an entity.

| State | Means | Does NOT mean |
|---|---|---|
| `MATCH_OBSERVED` | Source answered; exactly one record matched at a stated match level | PASS · compliant · verified |
| `NO_MATCH_OBSERVED` | **Source answered; no record matched.** A real, informative negative | FAIL · PASS · safe · invalid · any bucket |
| `MULTIPLE_MATCHES` | Source answered; more than one record matched. A **cardinality** fact | duplicate · fraud · conflict |
| `AMBIGUOUS` | Matched only on supporting evidence; no decisive identifier, no human resolution | no match · match |
| `SOURCE_UNAVAILABLE` | The source did not answer. **A fact about the world, not about the entity** | no match · fail · pass |
| `LOOKUP_NOT_APPLICABLE` | The lookup does not apply to this entity | pass · not checked by oversight |
| `INSUFFICIENT_IDENTIFIER` | We lacked the key required to perform the lookup | no match · not applicable |
| `ERROR` | **Our** code failed, not the source | source unavailable |

### Two distinctions that are load-bearing

**`SOURCE_UNAVAILABLE` ≠ `NO_MATCH_OBSERVED`.** One is an outage; the other is
evidence. Collapsing them converts a third party's downtime into a finding against
an entity that did nothing wrong.

**`SOURCE_UNAVAILABLE` ≠ `ERROR`.** This distinction has already cost this system
once: an organisation-level exclusion lookup was wired with a missing argument and
failed silently for every entity without an NPI, indistinguishable from an outage.
The codebase now logs the two at different levels for exactly this reason. The
vocabulary must carry the same distinction, or a bug in our code is recorded
forever as somebody else's outage.

### `NO_MATCH_OBSERVED` must be persisted as a positive fact

An absent row and an observed negative are different. Only one is evidence.

---

## B. VOCABULARY LAYERS

Each layer owns one vocabulary. **A term may not appear in two layers with
different meanings.**

```
LAYER 1  SOURCE OBSERVATION      what the external source returned
LAYER 2  EVIDENCE INTERPRETATION what it means for this entity, field by field
LAYER 3  DIMENSION DISPOSITION   what D1-D6 concluded
LAYER 4  VERIFICATION RESULT     the B1-B4 classification
LAYER 5  HUMAN DETERMINATION     the analyst / QA decision
```

### Layer ownership and current code placement

| Layer | Vocabulary | Owner | Components operating here |
|---|---|---|---|
| **1** | the 8 states above | connector / source adapter | `Tefca/connectors.py` (`SourceResult`), `Tefca/cms_ppef.py`, `tefca_registry/usps_client.py` |
| **2** | field-level signals: `name_mismatch`, `address_mismatch`, `enrollment_found`, `npi_validation` | evidence assembly | `Tefca/evidence_assembly.py`, `Tefca/address_evidence.py`, `Tefca/applicability.py` |
| **3** | `PASS`, `FAIL`, `REVIEW`, `NOT_APPLICABLE`, `UNAVAILABLE` + supplemental `CORROBORATED`, `CONFLICT`, `INSUFFICIENT_EVIDENCE`, `NOT_FOUND` | dimension engine | `Tefca/evidence_dimensions.py` — the vocabulary definition; `evidence_assembly._dimension_*` — the producers |
| **4** | `B1`, `B2`, `B3`, `B4`, `UNDETERMINED` | the single authoritative classifier | `tefca_registry/bucket_classifier.py` + `review_rules` |
| **5** | `ACCEPT`, `REJECT`, `MODIFY`, `ESCALATE`, `RETURN` | review workflow | `tefca_registry/review_routes.py`; extended by `docs/qa_gate_design.md` |

Legitimate **translators** sit *between* layers and are correct there:

| Translator | Boundary | Status |
|---|---|---|
| `arc_pipeline.dimensions_to_verification_results` | 2 and 3 → 4 | correct placement |
| `arc_pipeline._DISPOSITION_TO_STATE` | 3 → 4 | correct placement |
| `arc_pipeline._EVIDENCE_SOURCE_TO_RULE_SOURCE` | 1 → 4 source naming | correct placement |

### B.1 Boundary violations found

**Violation 1 — one column holds three vocabularies. MEASURED.**

`tefca_dimension_evidence.disposition` carries **9 distinct values across 1,984
rows**, drawn from three vocabularies:

| Value | Count | Belongs to |
|---|---|---|
| `PASS` | 716 | **Layer 3** |
| `NOT_FOUND` | 504 | **Layer 1** |
| `UNAVAILABLE` | 268 | **Layer 1** |
| `MATCH` | 176 | **address comparison — a fourth vocabulary** |
| `CORROBORATED` | 92 | **Layer 3** (supplemental) |
| `NOT_APPLICABLE` | 92 | **Layer 3** |
| `PARTIAL_MATCH` | 76 | **address comparison** |
| `REVIEW` | 48 | **Layer 3** |
| `CONFLICT` | 12 | **Layer 3** (supplemental) |

Severity: **HIGH.** This is the column a future Observation Store would inherit.
Recording *what a source said* and *what DocuAction concluded* in one field means
the same evidence cannot be re-interpreted under a revised methodology — which is
precisely what answering D1–D7 will require.

**Remedy (specified in `docs/evidence_provenance_design.md` §3.2):** add
`observation_result` carrying only Layer 1. Leave `disposition` and all 1,984
existing rows untouched.

**Violation 2 — a fourth, undeclared vocabulary.**

`address_evidence.AddressComparison` defines `MATCH`, `PARTIAL_MATCH`, `CONFLICT`,
`NOT_FOUND`, `UNAVAILABLE`. Three of the five collide by name with Layer 1 or
Layer 3 terms; `PARTIAL_MATCH` and `MATCH` exist in neither.

Severity: **MEDIUM.** The vocabulary is coherent and useful — it is a genuine
comparison result, which is a Layer 2 concept. It is unregistered and its terms
overlap.

**Remedy:** declare it as the Layer 2 *comparison* vocabulary, and rename the two
colliding terms at the point they are written into the shared column. Do not
rename them inside the address module, where they are correct and clear.

**Violation 3 — one function spans Layers 1 to 5.**

`Tefca/validation_engine.py:ValidationEngine.validate` reads raw `SourceResult`
objects (Layer 1), derives finding codes (Layer 2), applies bucket rules (Layer 4)
and returns a review tier (Layer 5) — in a single call, with no vocabulary boundary
anywhere.

Severity: **HIGH**, but already scheduled. This is the legacy classifier the
architecture resolution recommends retiring. **No action here** — recorded so the
retirement rationale includes it.

**Violation 4 — one source name, two meanings.**

The classifier source name `pecos` denotes the NPPES-proxy connector in the
registry review path and genuine CMS PPEF Enrollment in the RCE path. The codebase
already documents this and refuses to write the ambiguous key into new evidence.

Severity: **MEDIUM.** It is contained but not eliminated, and it is why two
otherwise-wireable classifier signals were deferred rather than wired during the
stabilization gate.

**Remedy:** on the next rule-set version, rename the RCE-path source to
`cms_ppef_enrollment`. **This is a rule change and therefore a methodology act** —
it must accompany a versioned rule set, not precede one.

---

## C. CONTRACT VALIDATION

A partial implementation of this already exists — `tests/test_classifier_signal_contract.py`,
added during the stabilization gate, pins the Layer 2 → Layer 4 seam. This section
generalises it to all five layers.

### C.1 The three invariants

**Invariant 1 — every signal the classifier expects is produced or declared.**

For each `field` condition in every active rule, the name must appear either in the
producer's emitted-signal registry or in an explicit unproduced declaration with a
reason. *Implemented today for Layer 2 → 4.*

**Invariant 2 — every signal produced is consumed or documented as unused.**

The reverse direction. An emitted signal no rule references is dead code that reads
as coverage. *Not yet implemented.*

**Invariant 3 — no term appears in two layers with different semantics.**

A registry of every vocabulary term and its owning layer; the check fails if a term
appears under two owners without an explicit, reasoned alias entry.

```
VOCABULARY_REGISTRY = {
  ("LAYER_1", "MATCH_OBSERVED"):     "source returned exactly one match",
  ("LAYER_1", "NO_MATCH_OBSERVED"):  "source answered, nothing matched",
  ...
  ("LAYER_3", "PASS"):               "dimension requirement satisfied",
  ...
}

ALLOWED_CROSS_LAYER_TERMS = {
  "NOT_APPLICABLE": ["LAYER_1", "LAYER_3"],   # same meaning in both; documented
  "UNAVAILABLE":    ["LAYER_1", "LAYER_3"],   # same meaning in both; documented
}
```

Terms in `ALLOWED_CROSS_LAYER_TERMS` must be identical in meaning and must carry a
justification. Everything else fails.

### C.2 Test versus startup assertion

| Check | Where | Why |
|---|---|---|
| Invariants 1–3 | **test** | Vocabulary is static. A CI failure is the right feedback; a startup crash in production over a naming issue is not |
| Producer / consumer field-name match | **test** | Same |
| Rule conditions reference known signals | **startup assertion** | Rules are **database rows** and can change without a deploy. A rule inserted referencing an unknown signal would silently never fire — the exact failure this design exists to prevent. Refuse to load the rule set |
| Duplicate DQ rule ids | **startup** — already implemented | Precedent from the stabilization gate |

The split turns on one question: *can this change without a code change?*
Code-defined vocabularies are tested. Database-defined rules are asserted at load.

### C.3 What the checks would have caught

| Defect | Caught by |
|---|---|
| `legal_name` vs `name` | Invariant 1 — the fix that closed it |
| `taxonomy_mismatch` referenced by a rule, produced by nothing | Invariant 1 |
| `npi_validation` = `flagged` vs `invalid` | **Invariant 3** — the same term with two value vocabularies across rules |
| `disposition` holding three vocabularies | **Invariant 3** |
| A rule inserted into the database naming a signal that does not exist | Startup assertion |

---

## D. VERSIONING

### D.1 Version identity

```
EVIDENCE_VOCABULARY_VERSION = "1.0.0"
```

Recorded on every persisted observation, following the pattern already used by
`rule_set_version`, `field_map_version`, `transformation_version`,
`REPORT_DATA_SERVICE_VERSION` and `template_version`. All five already exist in
this codebase, so the mechanism is proven and the convention is established.

### D.2 Change classification

| Change | Version bump | Historical rows |
|---|---|---|
| Add a new state or signal | **MINOR** | unaffected — old rows never carried it |
| Add a term to `ALLOWED_CROSS_LAYER_TERMS` | MINOR | unaffected |
| Clarify a definition without changing meaning | PATCH | unaffected |
| **Rename a term** | **MAJOR** | **preserved under the old name.** A mapping is recorded; rows are never rewritten |
| **Change what a term means** | **MAJOR** | preserved. This is the dangerous one — a silent semantic change re-means history |
| Remove a term | MAJOR | preserved; the term is marked retired, never deleted |

### D.3 The rule that governs all of it

**Historical rows are never rewritten.** This is the same principle already applied
to the ambiguous `pecos` source key, and the reasoning transfers directly: an audit
trail edited to look correct cannot be relied on at all. A stored value records what
the system believed when it wrote it; the registry supplies the meaning.

### D.4 Migration path for a MAJOR change

1. New version is defined; the old version's registry is retained in full.
2. A mapping is recorded: old term → new term, or old term → *no equivalent*.
3. New observations are written under the new version.
4. Readers resolve a term through the version stamped on the row.
5. Reports state the vocabulary version in their snapshot.

**No backfill. No rewrite.** A row written under 1.0.0 is read under 1.0.0 forever.

---

## E. RELATIONSHIP TO THE OTHER DESIGNS

| Document | Dependency |
|---|---|
| `docs/evidence_provenance_design.md` | **Depends on this.** Its `observation_result` column is filled with the Layer 1 vocabulary defined here. That vocabulary must be settled first or the column receives ad-hoc values |
| `docs/qa_gate_design.md` | Defines the Layer 5 vocabulary. Consistent with `ACCEPT / REJECT / MODIFY / ESCALATE / RETURN`; `APPROVE` and `RETURN` are the QA-side terms and must be registered as Layer 5 |
| `docs/methodology_decision_package.md` | **D2, D4 and D7 turn on Layer 1 / Layer 3 boundaries.** The vocabulary makes the questions precise; it does not answer them |
| `docs/report_consolidation_plan.md` | Reports render Layer 3 and Layer 4 terms. The canonical engine already separates indicator shape from colour per term; consolidating means one rendering of each term |

---

## F. LOC ESTIMATE

| Work item | Production | Test |
|---|---|---|
| `evidence_vocabulary.py` — the 8 Layer 1 states, the registry, layer ownership, version constant | 130 | 60 |
| Invariant 2 — produced-but-unconsumed check | 25 | 55 |
| Invariant 3 — cross-layer term registry check | 45 | 80 |
| Startup assertion: rule conditions reference known signals | 40 | 60 |
| Register the address comparison vocabulary as Layer 2 | 30 | 35 |
| `EVIDENCE_VOCABULARY_VERSION` stamped on observations and report snapshots | 35 | 40 |
| **TOTAL** | **~305** | **~330** |

Excludes emitting `observation_result` — costed in `evidence_provenance_design.md`.

---

## G. RISKS AND DEPENDENCIES

**Dependencies**

- **Independent of D1–D7.** Defining a vocabulary is not choosing a methodology.
  D2, D4 and D7 become *easier to state* once the terms are precise.
- **Blocks** `evidence_provenance_design.md` §3.2 and, transitively, the
  Observation Store.
- **Should precede** PPEF ingestion, NPPES bulk loading, and any new persisted
  evidence.

**Risks**

| Risk | Severity | Mitigation |
|---|---|---|
| The vocabulary is defined and the producers are never migrated to it | HIGH | Invariant 2 fails on a defined-but-unused term, so an unmigrated vocabulary fails CI rather than sitting inert |
| A MAJOR rename is applied with a backfill "for consistency" | **HIGH** | §D.3 is the controlling rule. A test should assert that no migration rewrites a vocabulary column on existing rows |
| The `pecos` rename is treated as an engineering cleanup | **HIGH** | It changes what a rule matches, so it is a methodology act. It must ship with a versioned rule set, never alone |
| Registry maintenance is forgotten as new signals are added | MEDIUM | Invariant 1 already fails on an unregistered signal — the enforcement precedes the discipline |
| Five layers is over-engineering for the current system | LOW | The layers describe components that already exist; this names boundaries rather than adding them. No component moves |
