# Phase 8 — Learning Center and operational transparency

2026-08-24 · Backend `fix/tefca-stabilization` · Frontend `fix/tefca-report-cutover`

> ## DEVELOPMENT / TEST DATA — NOT FOR GOVERNMENT DELIVERY — NOT ONC FINDINGS
>
> `is_running_mock()` is **TRUE**. The Government entity CSV has not been
> imported. Phase 8 is presentation and guidance only: no evidence, no
> determination and no QA event was created, altered or deleted.

---

## 1. What was already there, and what was missing

The inventory found a real framework rather than a blank page — and one gap that
mattered more than the rest.

| | Before | After |
| --- | --- | --- |
| Core framework | 247 lines: modules, lessons, glossary, contextual help, prohibited conclusions | extended |
| TEFCA content | 7 modules, 8 help topics, 26 glossary terms | 8 modules |
| **API** | **none — nothing could reach any of it** | **13 endpoints** |
| **Content classification** | **none** | 5-value vocabulary, enforced |
| **Search** | **none** | role-filtered |
| **Programme key** | **none** | `ProgramRegistry` |
| **Deep links** | **none** | validated at construction |
| Last-updated metadata | none | present |
| **Discrepancy / methodology module** | **missing** | Module 6 |

The Learning Center was a data structure with no way to read it. That is the
bulk of what Phase 8 fixed.

---

## 2. Classification — the reason this phase exists

Operators read guidance and act on it. If they cannot tell an agency requirement
from the contractor's own choice, the contractor's choice quietly becomes
policy — first internally, then in something sent to the agency.

Every substantive assertion now carries one of five labels:

| Classification | Meaning |
| --- | --- |
| `GOVERNMENT_REQUIREMENT` | Stated by the agency. **Must cite a source** — enforced in `__post_init__`. |
| `AGT_IMPLEMENTATION` | How AGT built it. Binding on staff, not on the agency. |
| `AGT_RECOMMENDATION` | AGT's proposed answer to an open question. |
| `PROGRAM_GUIDANCE_REQUESTED` | Genuinely unanswered. |
| `SOURCE_LIMITATION` | What a source cannot establish. Never a fact about the entity. |

A requirement nobody can trace to a document is indistinguishable from an
assumption, which is why the source is mandatory rather than encouraged.

---

## 3. Module 6 — the one that was missing

`app/Tefca/learning_methodology.py`. It is the module where mislabelling has
contractual consequences, so it lives beside the decision register it must stay
in step with.

| # | Government category (contract wording) | AGT shorthand | Methodology dependency |
| --- | --- | --- | --- |
| 1 | No discrepancies identified | B1 | — |
| 2 | Minor or administrative discrepancies | B2 | D4_ADDRESS_MATERIALITY |
| 3 | Inexplicable discrepancies | B3 | D5 |
| 4 | Non-compliant discrepancies | B4 | D7 |

Category labels are **imported from the same constants the reports use**, not
retyped. A lesson that spells a contractual term differently from the
deliverable is worse than no lesson, and a test asserts they agree.

Each category records its contractual meaning, the evidence that feeds it, AGT's
implementation, what the analyst must verify and what QA must verify.

---

## 4. D1–D9 — reported, not resolved

`GET /api/tefca/methodology/status`. All ten entries (D1–D9 plus
D4_ADDRESS_MATERIALITY) are **`PROGRAM_GUIDANCE_REQUESTED`**. None is marked
DECIDED, because no written COR response exists for any of them, and inventing
one would be fabricating a Government decision.

Each states its operational consequence and the workflows it affects. A test
asserts the payload contains no table name, class name, migration reference or
SQL fragment — a programme manager should see what is undecided, not the schema.

---

## 5. Unsupported policy wording found and removed

The static help page declared it contained "no invented data". It contained
three inventions.

| Found | Problem | Corrected to |
| --- | --- | --- |
| "B2 = 30 days, B3 = 21 days, B4 = 10 days" | **No contractual basis.** ¶146 sets the priority deadline *per request*; the contract sets no standing per-category turnaround at all. Stating fixed deadlines invents a requirement and then trains people to it. | The per-request rule, and an explicit note that the removed deadlines had no basis |
| "No Discrepancy (B1), Minor/Administrative (B2)…" | Presented B1–B4 as *the* classification with no Government/AGT distinction | Contract wording first, with B1–B4 identified as AGT shorthand not to be used externally |
| "drawn with a fixed, auditable seed" | No sample has been drawn; the parameters are AGT's proposal | The 95% contractual floor, AGT's ±5%/383 proposal, and "no sample has been drawn yet" |

Tests now guard all three.

---

## 6. Contextual help

Eight topics, each answering the same five questions, each **deep-linking to the
lesson that answers it** rather than to the Learning Center home. Deep links are
validated when the registry is constructed: a link to a module or lesson that
does not exist raises rather than shipping. A link that goes nowhere is worse
than no link — the reader follows it, hits an error, and stops trusting the help.

`LearningHelp` (frontend) fetches from the API rather than embedding copy, so
the screen inherits the backend's anti-drift guarantee instead of quietly
contradicting it. Wired into the reports screen against
`report.release_status`.

**Screens not yet wired — enumerated rather than implied.** The API and the
component are ready; these need the one-line insertion:

| Screen | Help key |
| --- | --- |
| Evidence view | `evidence.observation` |
| Discrepancy view | `evidence.address_conflict` |
| Source / provenance display | `evidence.source_unavailable`, `source.limitation` |
| Analyst queue and determination | `exception.queue_item` |
| QA workspace | `qa.decision` |
| Methodology-pending indicators | `methodology.pending` |

---

## 7. Reusability — the architecture test

**Could another programme use the framework without importing TEFCA? YES.**

Proven by test, not asserted:

- A second programme is constructed from core imports alone and gets
  navigation, search, role filtering, classification and contextual help.
- The core framework and the core API are parsed, docstrings and comments
  stripped, and checked for programme vocabulary — word-boundary matched,
  because `RCE` is a substring of `SOURCE_UNAVAILABLE`.
- The core API's import list is inspected: nothing from `app.Tefca`.

No hypothetical programme module was built. The throwaway fixture exists only to
prove the property.

---

## 8. Source limitations are never collapsed

The three prohibited collapses, each guarded:

- `SOURCE_UNAVAILABLE` → "no issue"
- `INSUFFICIENT_EVIDENCE` → "PASS"
- `METHODOLOGY_PENDING` → "FAIL"

Guidance exists for each distinct state, and the prohibited-conclusion list is
served on its own endpoint — the conclusions people reach wrongly are exactly
the ones nobody wrote down as forbidden.

---

## 9. Accessibility

| | |
| --- | --- |
| Semantic headings | ✅ `h3`/`h4` hierarchy in the help panel |
| Announced region | ✅ `role="region"` with `aria-label` |
| Disclosure state | ✅ `aria-expanded`, `aria-controls` |
| Labelled controls | ✅ close button has `aria-label` |
| Decorative icons hidden | ✅ `aria-hidden="true"` |
| Meaning not colour-only | ✅ every classification renders its words |
| Link purpose | ✅ "Read the full lesson on this topic" |
| Failure states stated | ✅ a failed load says so rather than showing an empty panel |
| **Automated frontend a11y suite** | ❌ **none exists in the repo** — `package.json` has only `build` |
| **Manual Section 508 review** | ❌ **not performed** |

**No Section 508 conformance is claimed.** The structural properties above are
asserted by test; conformance needs a manual review that has not happened.

---

## 10. Integrity

Baseline captured before Stage A, re-run after Phase 8. **Byte-identical.**

Area 1 · both Phase-6 evidence versions · all relationship hops · 43 review
records with 0 reportable · 0 decision events · Government CSV absent ·
`is_running_mock()` TRUE.

Phase 8 added no table and wrote no row.

---

## 11. Tests

| | |
| --- | --- |
| Stage A close | 1,937 passed · 56 skipped · 0 failed |
| After Phase 8 | **2,020 passed · 56 skipped · 0 failed** |
| New | **+83** |

Three existing tests updated rather than weakened: the vocabulary drift guard
now knows the Government categories are module constants rather than enum
values, and the navigation and module counts moved 18→19 and 7→8.

Frontend: `next build` compiles successfully. No test runner exists in that
repository.
