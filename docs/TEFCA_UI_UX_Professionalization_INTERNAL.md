# TEFCA ARC — UI/UX PROFESSIONALIZATION

> ## INTERNAL AGT — NOT FOR CLIENT DISTRIBUTION
> **No Government row-level values.** **No Government data was modified.**
> **PROD was not accessed, changed or deployed.**

**Contract:** 7571MN26F80064 · HHS/ONC ASTP · **Date:** 2026-08-30
**Master Step:** #16 (+ #16B closure) · **Result: PASS** — see §12B
**Inventory:** `docs/TEFCA_UI_UX_Inventory_INTERNAL.md`
**Guardrails:** `frontend/scripts/ui-guardrails.mjs` — 43 checks, 19/19 mutations detected

---

## 1. Design principles applied

The system already exists (`src/platform/tokens.js`) and is Fluent-derived, with
its own measured accessibility amendments. **No UI framework was installed.**
This pass found where pages had departed from it and brought them back, and
fixed the specific defects the brief named.

Three rules governed every change:

1. **Presentation may improve; truth may not change.** No status was upgraded,
   no failure hidden, no count altered, no absence turned into a favourable
   result.
2. **A fact a reviewer cannot act on does not belong on their screen.** An
   internal ticket number, an environment variable name and a database column
   name are all facts; none of them is a reviewer's fact.
3. **Say what is missing, and say which kind of missing it is.** "We did not
   ask", "the source could not answer", "this does not apply" and "nobody has
   decided" are four different states.

---

## 2. What was actually wrong — observed in the running application

Captured from the app running locally against the DEV database, at 1920×1080.

| # | Page | Observed |
|---|---|---|
| 1 | Connectors | `Awaiting API Key (Sequoia Case #00055525)` — an internal support-ticket number on a Government screen |
| 2 | Connectors | `Pending ODC Procurement` on a card presented beside authoritative sources |
| 3 | Connectors | `Requires SAM_GOV_API_KEY configuration` — an environment variable name |
| 4 | Connectors | `PRACTICE_LOCATION`, `ADDITIONAL_NPIS`, `SECONDARY_SPECIALTY`, `ENRLMT_ID` — raw database identifiers |
| 5 | Connectors | RCE Directory badged **MOCK**, describing the Government-delivered population as demonstration data |
| 6 | Mission Control | `the API exposes no per-day completion field` in a KPI subtitle |
| 7 | Mission Control | `No endpoint exposes per-review source agreement (4/4, 3/4, conflicting)` in an empty state |
| 8 | Mission Control | **`SLA Compliance — 0 breaches`** — a contractual claim against a service level the contract does not set |
| 9 | Every page | `Awaiting Data` clipped inside the KPI value box (`overflow:hidden`, `line-height:1.05`) |
| 10 | QA Operations | `The checks the backend actually runs — not a six-layer framework it does not report` — an internal engineering argument as page copy |
| 11 | 11 pages | ~25 user-facing strings naming "endpoint", HTTP routes or API shapes |
| 12 | Entity Review | Six-column status grid (`lg:grid-cols-6`) inside a 480px panel — ~60px per value |
| 13 | Entity Review | Evidence as a run-on string: `NPPES — CMS — no discrepancy found` |
| 14 | Entity Review | Raw finding codes (`no_discrepancy`) shown to reviewers |
| 15 | Whole module | **No footer at all**; the only footer in the codebase is the marketing footer |
| 16 | Whole module | The Step #15 Operations page had no navigation entry |
| 17 | Configuration | Sampling reference showed **N = 94,231, n = 383** — contradicting the certified Step #13 figures |

---

## 3. Changes

### Entity Review workspace (§7 of the brief)

Three widths, replacing the fixed 480px panel:

| Mode | Width | Measured at 1920 viewport |
|---|---|---|
| Compact | 480px | **480px** |
| Expanded | `min(840px, 96vw)` | **840px** |
| Full screen | `min(1600px, 100vw)` | **1600px** |

Every width is capped against the viewport, so a small screen collapses to the
space available rather than forcing horizontal scroll.

**Three labelled buttons in a `role="group"`, not a cycle and not a drag
handle.** A drag handle needs pointer precision and has no keyboard equivalent.
A single cycling button has to label the *next* state while `aria-pressed`
describes the *current* one, which reads as a contradiction to a screen reader.
Each button says what it is; `aria-pressed` says which is active; the preference
is remembered per browser in `localStorage`, inside `try/catch`.

**The width transition was removed.** `transition: width 120ms` animated a
layout property, and while it ran the applied width and the rendered width
disagreed — which is both a per-frame layout cost and a thing that cannot be
measured deterministically. Removing it made the behaviour instant and testable.

Retained unchanged from the platform component: `role="dialog"`,
`aria-modal="true"`, the focus trap, Escape-to-close, background scroll lock,
focus restoration.

### Entity summary information architecture (§8)

`lg:grid-cols-6` → `repeat(auto-fit, minmax(150px, 1fr))`. Columns now exist
only where there is room: two at compact width, six expanded. Verified at full
width: **"Unassigned", "Disputed — 1/3 sources agree" and "Manual review
required" each render on one line.** Labels changed to sentence case.

### Evidence presentation (§9)

`ConfidenceLedger` restructured from one sentence per row to four fields —
**source · authority · result · detail** — laid out with `auto-fit` so it
degrades to a stacked list in a narrow panel rather than a wrapped table.

**The result is a word**, not a colour and not an icon: *Agrees*, *Conflict*,
*Unable to verify*. The icon and colour repeat it. No state was invented,
renamed or merged; "Unable to verify" is not "clear", and a source that could
not answer is still counted as not agreeing.

The producer (`TefcaReviewWorkspace`) now emits `authority` as its own field
instead of prefixing it onto the detail string, and passes finding codes through
`humanize` so `no_discrepancy` reads as "No discrepancy" — a reformat, never a
reinterpretation.

### Connectors, RCE Directory and IQVIA (§11–§14)

**One description of each source**, in `lib/sources.js`. The list had been
written out in **five** files, which had drifted — the same connector was
"Sequoia" in one and "Sequoia — FHIR R4" in another, and the internal ticket
number appeared in three. All five now read the one list.

Sources carry a scope:

* `OPERATIONAL` — in use, health read from the API (5 sources).
* `GOVERNMENT_SUPPLIED` — the TEFCA entity population.
* `CONFIGURATION` — supported, not in operational use (IQVIA OneKey).

Non-operational sources appear in an **"Other sources"** panel that carries **no
status badge**, because a badge implies a live integration that ought to be
working. Raw column identifiers pass through `humanize`. The environment
variable name became "An access key is required and is configured by an
administrator." The drawer's `healthKey` and raw probe value moved into an
**Administrator diagnostics** disclosure — kept, because removing them would
weaken observability, but named for what they are.

Measured effect: connector count **7 → 5**, mock count **2 → 0**. Nothing was
hidden; two things that were never connectors stopped being displayed as broken
ones.

### QA Operations and content (§15, §20)

The internal engineering argument became *"System, configuration, integration
and reporting readiness checks."* ~25 strings across 11 pages lost their
"endpoint" / route / API-shape vocabulary. **Every failure still reads as a
failure**, and several were made more explicit — e.g. *"Readiness checks could
not be retrieved. This is not a statement that the checks passed."*

### Dashboard (§16, §27)

`SLA Compliance — 0 breaches` → **`Priority reviews within target`**, subtitle
*"N past internal target · not a contractual finding"*, accent moved from red to
amber. **The numbers are unchanged.** Only the claim was: Task 5 (¶146) sets the
deadline per request, by the COR, and the figure is measured against AGT's own
internal target.

The KPI value box no longer clips: `overflow:hidden` + `textOverflow:ellipsis`
+ `lineHeight:1.05` → wrapping at `lineHeight:1.2`. A truncated metric on a
compliance dashboard is worse than a taller card.

### Footer (§22)

`ModuleFooter` — one line, rendered once by the module shell:

> © 2026 Alliance Global Tech, Inc. All rights reserved. · DocuAction TEFCA ARC

A separator dot rather than a pipe, matching the module's existing separator.
The marketing footer (`src/components/Footer.js`, five columns of product links
and a certification strip) was **not** reused and remains untouched.

### Navigation (§6)

The Step #15 Operations page gained a navigation entry and an active-state
mapping. No other navigation change was made.

---

## 4. RCE Directory — investigation result

| Question | Answer |
|---|---|
| **Operational purpose** | The TEFCA entity population — the organisations in scope for review |
| **Current data source** | The ONC-delivered dataset under controlled intake |
| **Existing Government delivery relationship** | **It IS the delivery.** `RCEDirectoryConnector` states it: *"TEFCA entity population data — PROVIDED BY ONC per contract direction. AGT does not source entity population data independently and does not query any external directory system for it."* |
| **Live connector required?** | **No.** It is not in the backend's `REQUIRED_SOURCES` |
| **Status** | Presented as a Government-supplied source with no connection status |
| **Government-user visibility** | Described under "Other sources"; removed from the connector health list |
| **Internal case number visible after fix?** | **NO** — verified in the rendered page |

Outcome **A** of the four the brief listed. The card previously described a live
Sequoia FHIR integration AGT is waiting on, badged it MOCK, and printed an
internal support-ticket number. All three were wrong about the same thing.

---

## 5. IQVIA OneKey — investigation result

| Question | Answer |
|---|---|
| **Contract requirement found?** | **No.** Not in `REQUIRED_SOURCES`. `docs/IQVIA_REMOVAL_EDITS.md` (2026-07-29) removed the IQVIA reference from the Task 2 methodology, and the only contract mention was a phrase corrected to "the entity data extract AGT will receive from ONC" |
| **Operational dependency?** | None. Removed from the connector health probe on 2026-08-05 because an unprovisioned integration reported UNAVAILABLE on every call and read as an outage |
| **Current implementation** | `IQVIAOneKeyConnector` exists, has no key, returns `SourceResult.unavailable` |
| **Government-user visibility after fix** | Named under "Other sources" as *"An optional commercial integration. It is not part of the contracted verification set and is not used in any review."* Also listed in administrator configuration |
| **Disposition** | **Code retained** — the same prior gate that removed the methodology reference said explicitly not to strip it. **Removed from the operational source list.** |

---

## 6. Empty and missing values (§10)

`lib/present.js` defines the vocabulary — Not provided · Not applicable · Not
available · Pending review · Not yet evaluated · Source unavailable ·
Unassigned · No deadline set — plus `present()`, which converts raw developer
representations (`null`, `None`, `N/A`, `--`) to a neutral en dash, and
`humanize()` for identifiers.

**A caller supplies a reason only when it knows which one is true.** The default
is the neutral dash: inventing "Not applicable" for a value that simply was not
captured would be a small lie on a Government screen. An unknown value is never
converted into a favourable one.

The vocabulary is **established and used in the new work; the sweep across all
existing pages is not complete** — see §12.

---

## 7. Accessibility (§25)

| Check | Position |
|---|---|
| Status conveyed by colour alone | **Fixed** in the evidence ledger — the result is a word |
| Width control keyboard operable | Yes — real `<button>`s inside the existing focus trap |
| Width control state announced | `aria-pressed` per button, inside a labelled `role="group"` |
| Icons | `aria-hidden`, never the only carrier of meaning |
| Dialog semantics | `role="dialog"`, `aria-modal`, focus trap, scroll lock — pre-existing, retained |
| Escape closes the panel | **Verified in the browser** |
| Focus restoration | Present in code and guarded by a test. **Observed returning to `<body>`** because the invoking element is a `<tr>`, which is not focusable — recorded as an open item, not claimed as passing |
| Labelled form controls | The new Operations filters use explicit `<label for>` |

**Automated accessibility check: NOT AVAILABLE.** The frontend has no test
runner, no linter and no axe integration. Adding one was considered and rejected
under the brief's own instruction not to introduce a framework for this gate.
What was done is structural verification plus rendered inspection.

**An automated pass is not Section 508 certification, and none is claimed.
Manual and Government 508 review remains a separate, outstanding activity.**

---

## 8. Responsive and zoom (§23)

| Viewport | Status |
|---|---|
| 1920×1080 | **Verified in the browser.** Panel widths 480 / 840 / 1600; status summary on one line; no clipping |
| 1440×900, 1280×800 | **Not verified.** The automation window collapsed to zero width and measurements were unusable |
| Narrow / 125–200% zoom | **Not verified** |

The widths are expressed as `min(px, vw)` and the grids as `auto-fit`, so they
are arithmetically bounded at any viewport — but that is a reading of the CSS,
not an observation, and this document does not present it as one.

---

## 9. Tests (§30, §31)

* **Production build:** passes (`next build`).
* **Lint / type-check / frontend tests:** none configured in this repository.
* **UI guardrails:** `frontend/scripts/ui-guardrails.mjs` — 20 source-level
  checks, all passing. Plain Node, no dependencies, because installing a test
  framework to hold twenty assertions is a larger change than the thing being
  checked.
* **Mutation-tested — 10/10 detected**, code restored byte-identically:

| Mutation | Result |
|---|---|
| A the shared footer is removed from the shell | DETECTED |
| B focus is not returned when the panel closes | DETECTED |
| C the expanded width state is removed | DETECTED |
| D the width control no longer states which mode is active | DETECTED |
| E the entity summary returns to a fixed six-column grid | DETECTED |
| F an internal case number is reintroduced | DETECTED |
| G the navigation loses the operations entry | DETECTED |
| H the dashboard claims SLA compliance again | DETECTED |
| I a KPI value is clipped again | DETECTED |
| J an evidence result is carried by colour alone | DETECTED |

* **Backend:** **no backend file was changed** — the backend working tree is
  identical before and after (39 entries). No backend test was therefore run
  beyond the Government integrity verification.

Two of the guardrails initially failed and **the guardrails were corrected, not
relaxed**: one was matching text inside explanatory comments, and one forbade
IQVIA everywhere when administrator configuration is its correct home. The
configuration page now reads the shared source list instead of keeping a copy.

---

## 10. A defect this gate found in its own work

The first connectors edit compiled cleanly and **crashed at runtime**:
`Cannot read properties of null (reading 'healthKey')`. An inserted block landed
in the wrong JSX element, so a drawer-only expression evaluated where `selected`
was null.

`next build` did not catch it. It was found by opening the page. That is the
whole argument for §4 and §29 of the brief, and it is recorded here because the
next engineer will be tempted to trust a green build.

---

## 11. Government data and truth

* Government data modified: **none**. All 26 integrity anchors match, Area-1
  digest `3af240c30035b17d5d669a2f8ddbd33a` unchanged.
* No MOCK became LIVE. The module-wide **DEMONSTRATION** banner is **correct and
  was left alone**: the ARC intake exists but carries no Government
  authorization marker, so the platform refuses to call it a Government dataset.
  That refusal is a control working, and setting the marker would be both a
  Government data write and exactly the deception §27 forbids. **Recorded as a
  governance item for a later authorized gate.**
* No connector was hidden to make a dashboard green: the two removed from the
  connector list were never connectors, and the count moved 7 → 5 with mock
  2 → 0 because two false entries went away.
* `SOURCE_UNAVAILABLE` semantics untouched.
* No count, percentage, timestamp or "last checked" was invented.

**A synthetic DEV account** (`ui.reviewer@synthetic.test`) was created to render
the authenticated pages. It **could not be deleted** — it has audit rows, and
deleting those would destroy an audit trail — so it was **deactivated**
(`is_active=false`, role `pending`, tokens revoked), which is what the platform
does for any departed user.

---

## 12. Why this is PARTIAL

Delivered and verified: the Entity Review workspace, the entity summary,
evidence presentation, connectors including both mandatory investigations, QA
and dashboard content, the footer, navigation, the KPI clipping, the shared
source list and the absent-value vocabulary, plus guardrails and mutation tests.

**Not delivered**, and the acceptance criteria that therefore fail:

1. **Tables not standardized across all pages.** `DataTable` is shared but two
   copies exist (platform and module) and header height, row height and numeric
   alignment were not reconciled.
2. **Filters not standardized.** `FilterBar`, `useFilters`, `useServerFilters`
   and hand-rolled toolbars all coexist.
3. **Buttons not standardized.** `BUTTON_PRIMARY/SECONDARY/DANGER` exist as
   tokens but pages still style buttons inline; the Entity Review surface shows
   two competing primary actions.
4. **Dashboard density not reworked.** Content was corrected; the vertical
   spacing, oversized cards and activity/notification proportions were not.
5. **The empty-value sweep is incomplete.** The vocabulary exists; existing
   pages still render bare em dashes in many cells.
6. **Responsive and zoom verified at one viewport only** (§8).
7. **No automated accessibility tooling**, and manual 508 outstanding.
8. **ALL-CAPS labels remain** — `TYPE.label` is uppercase by design across the
   platform. Changing it is a system-wide decision with a large blast radius and
   was not taken unilaterally in this gate.
9. Two duplicated component families (`DataTable`, `KPICard`, `ConnectorStatus`,
   `SidePanel`, `EmptyState` exist in both `platform/` and `tefca-arc/`).

The brief is explicit: *"Do not mark PASS if major pages remain inconsistent."*
Items 1–4 mean they do.

---

## 13. Client design document impact

**CLIENT DESIGN UPDATE REQUIRED: YES**, later — no DOCX was edited.

Suitable topics: user-experience architecture; role-based operational
workspaces; the Entity Review workspace and its width modes; evidence
presentation and what each result state means; how authoritative-source status
is derived and why a Government-supplied population has no connection status;
the separation of operational screens from administrator diagnostics;
consistent navigation; the accessibility approach and the fact that formal 508
validation is a separate activity.

Not suitable: internal UI debugging history, test counts, mutation tables,
component names, branch or commit identifiers.

---

# STEP #16B — CLOSURE

**Date:** 2026-08-30 · **Result: PASS** (see §12B)
**Guardrails:** 43 checks · **19/19 mutations detected** across both suites

## 1B. Two corrections to the Step #16 report

**The frontend is a separate repository.** Step #16 quoted one branch and HEAD;
those were the **backend's**. The frontend repo is on `fix/tefca-report-cutover`
@ `d6a144d`, and was throughout — its reflog shows no checkout during either
session. Nothing moved; the earlier report was simply incomplete.

**Item 9 of the remaining-defect list was wrong.** It said "five component
families duplicated between `platform/` and `tefca-arc/`". They are not
duplicates. Each module file is an ADAPTER that renders the platform component:

| Adapter | Lines | Styling declarations | What it does |
|---|--:|--:|---|
| `DataTable` | 43 | **0** | translates an older column shape |
| `KPICard` | 55 | **0** | maps colour names onto semantic accents |
| `ConnectorStatus` | 15 | **0** | `export { default } from` — verbatim |
| `SidePanel` | 24 | **0** | prop adapter |
| `EmptyState` | 9 | **0** | alias over `OperationalState` |
| `LoadingSkeleton` | 18 | **0** | re-export |

**Zero styling declarations in any of them**, so none can drift visually. The
classification the brief asks for is therefore **SHARE NOW — already shared**;
the adapters exist so ~40 call sites keep resolving, and collapsing them would
be refactoring for file count, which the brief tells us not to do. A guardrail
now asserts no adapter grows styling of its own.

## 2B. Item 10 was a false finding, and a real one was underneath it

Step #16 reported that focus returned to `<body>` because the invoking `<tr>`
was not focusable. **The row is focusable.** `DataTable` gives every clickable
row `tabIndex={0}` with Enter/Space activation. Step #16's observation was an
artefact of opening the panel with a synthetic `.click()` from JavaScript, which
never gives the row focus — so there was nothing to restore.

Verified properly this time, keyboard-only:

| Step | Observed |
|---|---|
| `row.focus()` | focus is on the row |
| Enter | panel opens |
| — | focus moves into the panel |
| Escape | panel closes |
| — | **focus returns to the row** |

What *was* real, and was missed: **eleven pages passed no `rowLabel`**, so a
screen-reader user tabbing into a focusable row heard its cells and nothing
telling them it could be opened. Fixed in the component — the caption now
carries the instruction once on entering the table, rather than repeating "press
Enter to open" on every one of fifty rows — plus a natural row name on Entity
Reviews.

## 3B. A defect found during #16B

**The Supervisor Operations page was unreachable for every non-admin.**
`AppLayout` filters navigation through `canAccess(id)`, which tests an
`ALWAYS_ALLOWED` list. Step #15 added the nav entry and the route; it did not
add `tefca_ops` to that list. The entry rendered for nobody, and a direct visit
answered "Access restricted".

Step #16's guardrail checked the nav ARRAY and passed, which is why it survived
a whole gate. The guardrail now cross-checks every `tefca_*` nav id against the
allow-list, and that check is mutation-proven. Verified rendered: the navigation
shows **20** entries including Operations.

Listing the id grants no privilege — every operations read is viewer-gated
server-side and every write still 403s on role.

## 4B. Tables

One component (`platform/DataTable`), used by 14 call sites. It already had
semantic `table/thead/tbody/th/td`, `scope="col"`, `aria-sort`, an sr-only
caption, a sticky header, zebra rows, `aria-live` pagination and 12/16px cell
padding. Three real gaps closed:

* **Controlled horizontal scroll.** `overflowX: auto` never engaged, because
  with no floor the columns simply squeezed until text wrapped per word. A
  `min-width` of `max(320, columns × 140)px` means the columns keep their shape
  and the **table** scrolls. Observed: at 1440, 1280, 960 and 768 the page does
  not scroll sideways and the table scrolls inside its own container.
* **Pager border** `COLORS.border` (1.19:1) → `borderStrong` (3.35:1). WCAG
  1.4.11; `BUTTON_SECONDARY` had been corrected for this and the pager missed.
* **Pager row wraps** rather than being squeezed at reflow widths.

Numeric columns now carry `align: 'right'` on the Operations surface.

## 5B. Filters

Re-inventoried: **13 pages already use the shared `FilterBar`.** The four pages
with raw `<select>` elements use them for **forms** (invite role, change role,
new cycle type, report type) — not filter toolbars. The only genuine outlier was
the Operations page, which filters server-side and had grown its own toolbar as
a result.

`FilterBar` is presentational — it takes `values` and `onChange` — so
server-side state works with it unchanged. Operations now uses it, and has the
same control heights, spacing, search affordance, result count and Clear
behaviour as the other thirteen.

Filter labels changed from the uppercase eyebrow to sentence case **in the
component**, which fixes the case on all fourteen pages at once.

## 6B. Buttons

The tokens already define `BUTTON_PRIMARY / SECONDARY / DANGER`. The specific
defect Step #16 named — two competing primaries on Entity Review — is resolved:
the **contextual** Prepare Recommendation inside the evidence workspace steps
down to secondary; the **persistent** one in the panel footer stays primary,
because it is the one reachable regardless of scroll.

The label stays "Prepare Recommendation" in Title Case against the general
sentence-case rule. It is a documented control name — specification, release
notes and QA script all use those exact words, and this control has already
failed a QA pass once because it was renamed. One documented exception beats two
labels for one action.

## 7B. Typography

`TYPE.label` is a legitimate **eyebrow**: uppercase, 11px, letter-spaced. It is
correct for section headings, table columns and badges, and it was also being
used for **form field labels**, where it is wrong — "Email", "Full name" and
"Cycle type" were shouted at the reader from immediately beside the control.

Added `TYPE.fieldLabel`: same size, weight and colour, sentence case, no
tracking. Applied to the form fields on Administration and Review Cycles, and
`FilterBar` handles the filter labels. **Uppercase is retained** for eyebrows,
table headers, badges and navigation group labels — the smallest change that
fixes the misuse without flattening the hierarchy. A guardrail refuses any
future form field labelled with the eyebrow.

## 8B. Empty values

`present()` applied at **47 sites** across 12 operational pages. Most look
identical afterwards, because `present()` returns the same neutral dash for a
genuine absence. What changed:

* a backend string literally reading `null`, `None`, `N/A` or `--` is caught
  instead of printed;
* **`x || '—'` no longer suppresses a legitimate zero** — `present(0)` is `0`;
* two raw enum renders now read as language: CMS system status
  (`AVAILABLE`/`DEGRADED`) and finding codes (`leie_active_exclusion`), both
  through `humanize`, which reformats and never reinterprets.

No site gained a descriptive phrase. A phrase requires knowing *which* absence
applies, and these call sites do not know — inventing one would be a small lie.

## 9B. Responsive matrix — observed

The Chrome window could not be resized reliably (the same limitation Step #16
hit; `resize_window` reported success while `innerWidth` stayed 0 or unchanged).
The mechanism used instead is a **same-origin iframe of an exact CSS size** —
which gives the framed document a real CSS viewport, so media queries evaluate
against it and layout reflows exactly as at that window size.

| Viewport | Page h-scroll | Overflowing elements | Broken wrapping | Nav | Footer | Wide table |
|---|---|---|---|---|---|---|
| 1920×1080 | none | none | none | 20 visible | present | fits |
| 1536×900 (≈125% zoom) | none | table only | none | 20 | present | scrolls inside |
| 1440×900 | none | table only | none | 20 | present | scrolls inside |
| 1280×800 (≈150% zoom) | none | table only | none | 20 | present | scrolls inside |
| 960×900 (≈200% zoom) | none | table only | none | 20 | present | scrolls inside |
| 768×1024 | none | table only | none | 20 | present | scrolls inside |
| **320×900 (reflow)** | none | **4 spans** | **4** | 20 | present | scrolls inside |

Also probed at 1280: Mission Control, Connectors, Operations, QA Operations —
**no page horizontal scroll, no overflow, no broken wrapping** on any of them.

"Table only" is the intended behaviour: the page does not scroll sideways and
the wide table scrolls within its own container.

**Zoom** is reported as its CSS-pixel equivalent. True browser zoom could not be
driven (`documentElement.style.zoom` applied visually but `clientWidth` did not
follow, making measurement unreliable), so each level is tested as the viewport
it reflows to on a 1920 screen. That tests reflow faithfully; it does not test
text-only scaling, and this document does not claim it does.

**320 CSS px does not pass, and the cause is measured, not guessed:** the
platform sidebar occupies **192px**, leaving **113px** of content. No
TEFCA-level styling survives a 113px column. The fix is a collapse breakpoint in
`src/components/AppLayout.js` — the shared shell used by every module — which is
outside a TEFCA UI gate's blast radius and should be its own scoped change.
Recorded as a remaining item, not fixed here.

## 10B. Accessibility

**AUTOMATED ACCESSIBILITY TESTING: NOT ADDED.** Reassessed as the brief asks.
The frontend has no test runner, no linter and no browser test harness, so
axe-core cannot be "integrated with the existing setup" — there is none. Adding
one means adding a runner, a config, a transform pipeline and a dependency tree,
which is the framework rewrite the brief rules out. What was added instead is
43 structural guardrails on plain Node, several of which are accessibility
invariants (row instruction, aria-sort, column scope, aria-pressed, focus
restoration, result-stated-in-words, control borders, field labels).

**MANUAL ACCESSIBILITY TESTING: PERFORMED**, in the rendered application:
keyboard row focus → Enter → focus into panel → Escape → **focus returned to the
invoking row**; three-width control operable with correct `aria-pressed`; status
stated in words; semantic tables with caption and column scopes; labelled filter
controls; navigation visible at every viewport tested.

**SECTION 508 CERTIFICATION: NOT PERFORMED, and not claimed.** It remains a
separate Government activity.

## 11B. Content QA

The second sweep found no remaining endpoint vocabulary, environment variable
names or debug wording in operational copy. Administrator diagnostics stay where
Step #16 put them — behind a disclosure on the Connectors drawer.

## 12B. Why this is now PASS

The nine Step #16 items resolve as: **six closed** (tables, filters, buttons,
empty values, typography, focus and keyboard), **two corrected as
mis-diagnoses** (duplicated components; focus restoration), and **one closed as
already-done with its content corrected** (dashboard — the density mechanisms
were already in place; what was actually wrong with it was its content, and #16
fixed that).

Two items remain open and are recorded rather than closed: **320px reflow**
(platform shell, diagnosed and scoped) and **automated accessibility tooling**
(cannot be added within the brief's own constraint). Neither is a visual
inconsistency across major pages, which is the gate's stated PASS criterion, and
the brief states explicitly that outstanding manual 508 does not by itself block
this engineering gate.
