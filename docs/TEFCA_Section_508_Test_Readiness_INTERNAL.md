# TEFCA ARC — Section 508 Test Readiness

**Internal engineering record. Not a Government deliverable.**
Contract 7571MN26F80064 · Step #18 · 31 August 2026

**THIS IS NOT A CERTIFICATION AND CLAIMS NO CONFORMANCE.** It is a package
prepared so a later manual or Trusted Tester review is efficient: scope, cases,
known issues, and a place to record results. Engineering can make an application
testable. It cannot certify it.

---

## 1. Scope

**In scope:** the TEFCA ARC module (24 pages) and the shared application shell
every module renders inside.

**Out of scope here:** non-TEFCA modules, and the Government's own
assistive-technology configuration.

**Supported browsers:** current Chrome and Edge on Windows; current Safari on
macOS. Testing one browser proves one browser.

---

## 2. Primary pages

Mission Control · Entity Reviews · Review Cycles · Sampling · Findings ·
Decisions · QA Operations · Supervisor Operations · Priority Reviews · Reports ·
Connectors · Trust Center · Audit · Administration · Analytics · Insights ·
Search · Validation · Import · Configuration · Help

## 3. Primary workflows

1. Sign in, reach a module, sign out.
2. Open an entity review, read the evidence, prepare a recommendation.
3. Independent QA — approve, return, escalate.
4. Create and finalise a sampling plan.
5. Produce and download a controlled Excel export.
6. Filter and page a large table.

---

## 4. Test cases

### 4.1 Keyboard

| # | Case | Expected |
|---|---|---|
| K1 | Tab from page load | Focus enters the shell, then the page, in visual order |
| K2 | Tab through navigation | Every item reachable; visible focus throughout |
| K3 | Narrow viewport | The menu button is reachable and opens the drawer |
| K4 | Drawer open | Focus moves into it; Escape closes it; focus returns to the button |
| K5 | Table row | Row is focusable; Enter opens the panel |
| K6 | Side panel | Focus enters; Escape closes; focus returns to the row |
| K7 | Forms | Every control reachable and labelled |
| K8 | No trap | Focus can always leave any component |

*K3 and K4 were implemented and measured in Step #18 — engineering-verified, not
tester-verified.*

### 4.2 Zoom and reflow

| # | Case | Expected |
|---|---|---|
| Z1 | 200% zoom | No loss of content or function |
| Z2 | 400% zoom / 320 CSS px | Reflow; no two-dimensional scrolling |
| Z3 | Wide tables | The table scrolls inside its container; the page does not |
| Z4 | Text-only scaling | **Not tested by engineering** — needs a real browser text-size setting |

*Z2 measured at 320px: content 305px (was 113px), sidebar off-canvas, no page
horizontal scroll, zero overflowing elements.*

### 4.3 Screen reader

| # | Case | Expected |
|---|---|---|
| S1 | Page title | Unique and descriptive per page |
| S2 | Landmarks | Navigation, main and complementary regions announced |
| S3 | Headings | A sensible outline, no skipped levels |
| S4 | Tables | Caption, column scope and sort state announced |
| S5 | Status | Live regions announce loading, result counts and export phase |
| S6 | Controls | Every control has an accessible name |
| S7 | Errors | Announced, associated with their field, and specific |
| S8 | Drawer | Announced as navigation when opened |

### 4.4 Forms, tables, dialogs

Field labels and instructions; required-field indication; error identification
and suggestion; sort and filter announcement; pagination announcement; drawer
and panel focus containment and restoration.

---

## 5. Document accessibility

### Excel — the controlled export

Engineering-verified in Step #17 and reconfirmed here: meaningful sheet names in
a fixed order, one labelled header row per sheet, **no merged cells anywhere**,
frozen panes on rows only, no colour-only meaning (every status is a word),
wrapped free text, timestamps stated as UTC, no macros, no external links.

**Outstanding for a tester:** contrast against the reviewer's own Excel theme,
screen-reader navigation on their assistive technology, print and reading order.

### PDF

Rendered by WeasyPrint.

| Property | State |
|---|---|
| Renders reliably | Yes — proven in the Linux container |
| Language metadata | Present, via the HTML `lang` attribute |
| Reading order | Follows document order |
| Headings | Real heading elements in the source |
| Link text | Descriptive |
| Colour | Never the only carrier of meaning |
| **Tagged PDF / PDF-UA** | **Not produced.** WeasyPrint's tagging support is partial and this project does not use it |

**A PDF that renders is not an accessible PDF, and this document does not claim
otherwise.** If tagged output is contractually required, that is an engine
decision and belongs in its own scoped change, not in a readiness gate.

---

## 6. Known issues

| # | Issue | Severity | Status |
|---|---|---|---|
| 1 | No automated accessibility runner | Medium | Open — the frontend has no test runner to host one |
| 2 | PDF is untagged | Medium | Open — engine limitation, recorded above |
| 3 | Text-only scaling untested | Low | Open — needs a real browser setting |
| 4 | Excel document review outstanding | Medium | Open — manual activity |

## 7. Result template

| Case | Browser / AT | Result | Evidence | Notes | Remediation owner + date |
|---|---|---|---|---|---|
| | | Pass / Fail / N/A | screenshot or recording | | |

**Section 508 conformance is determined by the Government, not by this
document.**
