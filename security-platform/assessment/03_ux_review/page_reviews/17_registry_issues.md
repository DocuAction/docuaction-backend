# Page Review 17 — TEFCA Registry: Issues

- **Route:** `/tefca-registry/issues`
- **Component File:** `src/app/tefca-registry/issues/page.js`
- **Lines of Code:** 97

## Scores (1–10)
| Dimension | Score |
|---|:--:|
| Layout | 8 | Navigation | 8 | Visual Hierarchy | 8 | Info Density | 7 |
| User Workflow | 8 | Consistency | 8 | Loading States | 8 | Empty States | 8 |
| Error States | 7 | Table Usability | 8 | Form Usability | N/A | Dark Mode | 9 |
| Responsive | 7 | Accessibility | 8 | **OVERALL** | **7.9** |

## Strengths (top 3)
1. **Clean findings list** — DataTable of all findings with severity + status badges, sortable, row-click → the affected entity; **filter by severity and status** (verified: "Critical" narrows to exactly 4).
2. Most compact registry page (97 LOC) yet complete: loading/empty/error, MM/DD/YYYY, token-themed dark mode.
3. Severity color mapping consistent with the entity-detail findings view.

## Improvements Needed (top 3)
1. **No finding-type filter** — severity + status only; type (npi_duplicate, orphan_entity, …) would help triage. *[Priority: Low]*
2. **No bulk actions / resolution workflow** — findings are read-only here; acknowledging/resolving (the model supports `status`, `resolved_by`) isn't exposed. *[Priority: Medium]*
3. **Export** of the issues list for offline review/reporting. *[Priority: Low]*
