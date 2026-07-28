# Page Review 15 — TEFCA Registry: Entity Detail

- **Route:** `/tefca-registry/entity?id=<uuid>` (query-param — no dynamic routes in this static-export app)
- **Component File:** `src/app/tefca-registry/entity/page.js`
- **Lines of Code:** 278

## Scores (1–10)
| Dimension | Score |
|---|:--:|
| Layout | 7 | Navigation | 7 | Visual Hierarchy | 8 | Info Density | 6 |
| User Workflow | 8 | Consistency | 8 | Loading States | 8 | Empty States | 8 |
| Error States | 8 | Table Usability | 7 | Form Usability | N/A | Dark Mode | 9 |
| Responsive | 6 | Accessibility | 7 | **OVERALL** | **7.5** |

## Strengths (top 3)
1. **Comprehensive, well-sectioned detail** — summary, parent chain → root QHIN + children, identifiers table, verification findings (severity/status badges), **pretty-printed FHIR R4 JSON viewer** (scrollable, monospace), and version info; plus an inline **Run verification** action.
2. **Suspense-wrapped `useSearchParams`** (correct for static export) + fail-closed data loading + "Entity not found" empty state.
3. Findings surfaced with severity color mapping (e.g., Duplicate NPI shows as HIGH/OPEN) — makes defects legible.

## Improvements Needed (top 3)
1. **Long single-scroll page** — 6 stacked panels; add in-page anchors/tabs (Summary · Identifiers · Hierarchy · Findings · FHIR · Versions). *[Priority: Medium]*
2. **Parent-chain builder does iterative fetches** (one per ancestor) — bounded but a mini N+1 on the client; a single ancestry endpoint would be cleaner. *[Priority: Low]*
3. **Query-param routing** (`?id=`) is a static-export workaround — less shareable/semantic than `/entity/{id}`; document the constraint. *[Priority: Low]*
