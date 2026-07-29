# Design System Recommendations

Documented only. Goal: take the existing ~70% system to a **formalized, enforced 100%**. IDs `DS-###`, effort in eng-days.

## Should a formal design system be created?
**No new system — formalize the existing one.** The tokens, ~25 accessible components, and a WCAG-tuned dark theme already exist. The work is **measure → converge → migrate → enforce**.

## Recommendations (prioritized)

### P1 — Foundation cleanup (enforce what exists)
| ID | Action | Why | Effort |
|---|---|---|---|
| DS-01 | **Kill the color leaks** — replace the 191 hex with ~12 tokens; resolve the **2 greens / ≥3 reds / ≥2 blues** duplication (retire `#16a34a`, `#dc2626`, `#2563eb`, stale `#0078d4`) | one semantic color each; fixes dark mode | 4–6d |
| DS-02 | **Eliminate 688 sub-11px text usages** → token `TYPE` (11px floor) | accessibility/legibility | 3–4d |
| DS-03 | **Collapse 22 spacing values → the 6-value scale** (4/8/12/16/24/32) | consistency | 2–3d |
| DS-04 | **One body font stack + one mono** (retire the 3 Segoe variants + Arial) | consistency | 0.5d |
| DS-05 | **Lint rule: ban raw hex + sub-11px + off-scale spacing in `src/`** (eslint/stylelint custom rule) | prevents regression — the multiplier | 1–2d |

### P1 — Dark-mode coverage
| ID | Action | Effort |
|---|---|---|
| DS-06 | Migrate **Dialect-2 pages** (~28: dashboard, case-mgmt, trust, compare, actions-inbox, documents, decisions, analytics, healthcare, validation, GovCon/ATS) off hardcoded hex → tokens (this is what fixes their broken dark mode) | 8–12d |
| DS-07 | Migrate **Dialect-3 auth pages** (~18) onto tokens via new `AuthCard`/`Field` | 3–4d |

### P2 — Component convergence
| ID | Action | Effort |
|---|---|---|
| DS-08 | One `PageHeader`, one `StatusBadge` (drop `StatusPill`), one `Panel`, platform `SidePanel` everywhere | 4–6d |
| DS-09 | Migrate tefca-arc DataTable adapter → platform `DataTable`; one `KPICard`; one `LoadingSkeleton` | 3–4d |
| DS-10 | **One API client** on `lib/session.ts` (retire 3) + promote `Toast` + `PermissionBoundary` to platform | 4d |
| DS-11 | Remove unused `@tanstack/react-table`; retire obsolete `tefca-arc/dashboard` + `tefca-dashboard` | 0.5d |

### P3 — Formalize & document
| ID | Action | Effort |
|---|---|---|
| DS-12 | **"DocuAction Design System v1" doc + component gallery** (Storybook or a `/design` route) | 5–8d |
| DS-13 | Per-combo **WCAG AA contrast matrix** (feeds Part 6) + published contrast rules | 2–3d |
| DS-14 | Responsive tokens/breakpoints doc + a responsive pass on dense pages | (tracked in UX-08) |

## What "100%" looks like
- **~12 color tokens** (no hex in `src/`), **5 type sizes** (11px floor), **6 spacing steps**, **1 font stack** — enforced by lint.
- **One** of each component (header/badge/panel/table/KPI/skeleton/client).
- **Every page** on tokens → dark mode works everywhere.
- A published DS doc + gallery + contrast matrix.

## Effort summary
| Track | Effort |
|---|---|
| Foundation cleanup (DS-01–05) | ~11–16d |
| Dark-mode coverage (DS-06–07) | ~11–16d |
| Component convergence (DS-08–11) | ~11–15d |
| Formalize/docs (DS-12–14) | ~7–11d |
| **Total to 100%** | **~40–58d** (~8–12 weeks, 1 FE eng) |

## The one-line strategy
**Don't build a design system — enforce the one you have.** The tokens and accessible components are done; ~180 stray hex, 688 tiny-text usages, ~46 non-token pages, and 2–3× duplicate components are the entire gap. Lint + migration close it.
