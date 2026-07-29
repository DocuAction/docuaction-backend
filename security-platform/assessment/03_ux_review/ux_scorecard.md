# UX Aggregate Scorecard

Scope: the 17 assessed pages (federal/TEFCA + auth + admin). The dormant commercial pages (GovCon/ATS/ERP dashboards) were **not** individually scored; spot-checks indicate they trend lower (hardcoded hex, weaker state handling) and would pull a whole-app average down.

```
┌──────────────────────────────────┬────────┐
│ Page                             │ Score  │
├──────────────────────────────────┼────────┤
│ Login                            │ 6.5/10 │
│ Signup / Register                │ 6.3/10 │
│ Mission Control (TEFCA ARC)      │ 7.8/10 │
│ Entity Import                    │ 7.3/10 │
│ Entity Queue / Reviews           │ 7.4/10 │
│ Decision Workspace               │ 7.0/10 │
│ Priority Reviews                 │ 7.0/10 │
│ Review Cycles                    │ 7.2/10 │
│ Connector Health                 │ 7.2/10 │
│ QA Sweep / Operations            │ 7.1/10 │
│ Audit Trail                      │ 6.8/10 │
│ User Administration              │ 6.5/10 │
│ Registry — QHIN Overview         │ 7.8/10 │
│ Registry — Entities              │ 7.8/10 │
│ Registry — Entity Detail         │ 7.5/10 │
│ Registry — Verification          │ 7.9/10 │
│ Registry — Issues                │ 7.9/10 │
├──────────────────────────────────┼────────┤
│ TEFCA ARC Module Average         │ 7.4/10 │
│ TEFCA Registry Module Average    │ 7.8/10 │
│ Auth pages (login/signup)        │ 6.4/10 │
│ OVERALL APPLICATION UX (17 pgs)  │ 7.2/10 │
└──────────────────────────────────┴────────┘
```

## By-dimension pattern (across the 17)
| Dimension | Trend | Notes |
|---|---|---|
| Dark Mode | **Strong (8–9)** on token pages; **Weak (4–6)** on auth/admin | token pages flip via `data-theme`; auth pages use CSS classes |
| Loading States | **Strong (7–9)** | `SkeletonPage`/`SkeletonTable`/spinners nearly everywhere |
| Empty States | **Strong (6–9)** | "Awaiting Data" honesty is a differentiator |
| Error States | **Good (6–8)** | fail-closed client + `PermissionBoundary` (denial-as-denial) |
| Table Usability | **Good (7–8)** | shared DataTable: sort, paginate, 44px rows, keyboard |
| Accessibility | **Good (7–8)** | platform components carry a11y (aria-sort, focus trap, sr-only) |
| Consistency | **Split** | strong *within* each module; **cross-module** header + auth-styling gaps |
| Responsive | **Weakest (6–7)** | desktop-first federal tool; dense tables/grids on small screens |
| Info Density | **6–7** | large composed screens (import 655, dashboard 814) |

## Takeaways
- **Registry (7.8) is the strongest module; auth pages (6.4) the weakest** — inverse of typical apps (usually login is the most polished). The gap is **styling-system divergence**, not effort.
- **The federal stack UX is genuinely good (7.2–7.8)** and enterprise-grade in its state-handling and honesty.
- **Responsiveness** is the most consistent weak spot; **cross-module consistency** (two header components, two styling systems) is the most actionable.
