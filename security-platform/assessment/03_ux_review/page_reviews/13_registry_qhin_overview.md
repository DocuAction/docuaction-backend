# Page Review 13 — TEFCA Registry: QHIN Overview

- **Route:** `/tefca-registry`
- **Component File:** `src/app/tefca-registry/page.js`
- **Lines of Code:** 293

## Scores (1–10)
| Dimension | Score |
|---|:--:|
| Layout | 8 |
| Navigation | 8 |
| Visual Hierarchy | 8 |
| Info Density | 7 |
| User Workflow | 8 |
| Consistency | 7 |
| Loading States | 9 |
| Empty States | 8 |
| Error States | 7 |
| Table Usability | 7 |
| Form Usability | N/A |
| Dark Mode | 9 |
| Responsive | 7 |
| Accessibility | 8 |
| **OVERALL** | **7.8** |

## Strengths (top 3)
1. **Lazy-loading hierarchy** — QHIN cards → participants (on select) → sub-participants (on expand); only one level fetched at a time (never dumps the 177-entity tree). KPI row + card grid + expandable tree with `SkeletonPage`/`SkeletonTable` and a spinner during lazy fetch.
2. **Fully token-themed** (dark mode flips cleanly) + `formatDate` MM/DD/YYYY + 44px min row height (WCAG target size) + `role="tree"/"treeitem"` + `aria-expanded`.
3. **Verified live** in prod — renders real data (177 entities / 11 QHINs / 45 participants).

## Improvements Needed (top 3)
1. **Header inconsistency across TEFCA modules** — registry uses `PageHeader` while `tefca-arc` uses `CommandBar` for the same concept → two header components. *[Priority: Medium]*
2. **`formatDate` date-only off-by-one** in western timezones (shared util UTC-parses `"2023-12-12"`) → designation dates can display one day early. *[Priority: Medium]* (util-level, affects all pages).
3. **Card-grid responsiveness** — expandable tree rows have many fixed-width meta columns that crowd on tablet. *[Priority: Low]*
