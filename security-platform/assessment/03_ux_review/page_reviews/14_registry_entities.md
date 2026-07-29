# Page Review 14 — TEFCA Registry: Entities

- **Route:** `/tefca-registry/entities`
- **Component File:** `src/app/tefca-registry/entities/page.js`
- **Lines of Code:** 145

## Scores (1–10)
| Dimension | Score |
|---|:--:|
| Layout | 8 | Navigation | 8 | Visual Hierarchy | 7 | Info Density | 7 |
| User Workflow | 8 | Consistency | 8 | Loading States | 8 | Empty States | 8 |
| Error States | 7 | Table Usability | 8 | Form Usability | 7 | Dark Mode | 9 |
| Responsive | 7 | Accessibility | 8 | **OVERALL** | **7.8** |

## Strengths (top 3)
1. **Global search across name/NPI/TEFCAID/HCID** with a **250ms debounce + monotonic request-ordering guard** (fixed a real race where stale partial-query results overwrote the correct set) — robust search UX.
2. **Platform DataTable** with sort, client pagination (25/page), keyboard-activatable rows, 44px height, sr-only caption; StatusBadge for verification status; row-click → detail.
3. Clean filter dropdowns (level, verification status) disabled during active search; token-themed, MM/DD/YYYY.

## Improvements Needed (top 3)
1. **Offset pagination** — fine at 177 but degrades on deep pages at 10× scale; cursor pagination later. *[Priority: Low]*
2. **No column-level filtering / saved views** — power reviewers may want persistent filters. *[Priority: Low]*
3. **Export** (CSV of filtered results) not present — common reviewer need. *[Priority: Low]*
