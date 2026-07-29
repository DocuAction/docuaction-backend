# UX Recommendations (Prioritized)

Documented only — no changes made. IDs `UX-###`. Effort in rough eng-days.

## CRITICAL (before HHS demo / production)
| ID | Recommendation | Why | Effort |
|---|---|---|---|
| UX-01 | **Confirmation dialogs on destructive actions** (disable/delete user, cycle runs, send briefing) — audit every mutating action for a confirm step | data-integrity / accidental-action risk on a compliance tool | 2–3d |
| UX-02 | **Audit Trail filtering + export + pagination** (who/what/when/date-range) | auditors can't work a large log without filters; also a HIPAA/compliance need (Part 10) | 2–3d |
| UX-03 | **Fix `formatDate` date-only off-by-one** (UTC-parse) | designation/effective dates display a day early in western TZ — wrong data shown | 0.5d |

## HIGH (Sprint 1)
| ID | Recommendation | Why | Effort |
|---|---|---|---|
| UX-04 | **Bring auth pages onto the token system** (AuthCard/Field components) | fixes the biggest consistency gap + dark-mode breakage on login/signup/reset | 2–3d |
| UX-05 | **Converge duplicate components** → one header (retire CommandBar/PageHeader split), one `StatusBadge` (drop `StatusPill`), one `Panel` | cross-module consistency; less maintenance | 3–4d |
| UX-06 | **Unify the 3 API clients** into one zero-trust fetch (base on `src/lib/session.ts`) | remove drift/dup; single 401/403/expiry behavior | 2d |
| UX-07 | **Shared Toast + surface run/save results consistently** (verification banner → toast) | consistent feedback pattern | 1–2d |
| UX-08 | **Responsive pass on dense pages** (import 655, dashboard 814, Mission Control) — collapse KPI grids, horizontal-scroll containment, tablet breakpoints | desktop-first tool is cramped on smaller screens | 3–5d |
| UX-09 | **SSO button on login** ("Sign in with Microsoft") | Entra SSO exists but has no UI entry point | 0.5d |

## MEDIUM (next month)
| ID | Recommendation | Effort |
|---|---|---|
| UX-10 | **Finish hardcoded-hex → token migration** on `/dashboard` + legacy commercial pages (fix their dark mode) | 3–5d |
| UX-11 | **Entity-detail in-page tabs/anchors** (Summary/Identifiers/Hierarchy/Findings/FHIR/Versions) instead of long scroll | 1–2d |
| UX-12 | **Issue-resolution workflow** on Registry Issues (acknowledge/resolve — model already supports `status`/`resolved_by`) + finding-type filter | 2–3d |
| UX-13 | **Async progress for bulk verification / large imports** (determinate progress or job polling) | 3d |
| UX-14 | **Consistent breadcrumbs + back nav** across all modules | 1–2d |
| UX-15 | **Post-signup expectation messaging** (pending/verify email) | 0.5d |

## LOW (future)
| ID | Recommendation |
|---|---|
| UX-16 | Cursor pagination + CSV export on registry lists |
| UX-17 | Connector uptime trend/sparkline + manual re-probe |
| UX-18 | Saved views / persistent filters for reviewers |
| UX-19 | Retire single vs dual dashboard ambiguity (`/dashboard` vs `/tefca-arc`) |
| UX-20 | Tooltip/help coverage + first-run onboarding |

## Design-system track (parallel)
| ID | Recommendation | Effort |
|---|---|---|
| UX-DS1 | **Formalize the existing platform system** ("DocuAction DS v1"): promote duplicates into `src/platform`, add a component gallery | 5–8d |
| UX-DS2 | **Lint rule banning raw hex** in `src/` (enforce tokens) | 1d |

## Highest-leverage 3 (if only three)
1. **UX-04/05/06** — converge auth styling + duplicate components + API clients (kills the consistency debt).
2. **UX-01/02** — confirmations + audit filtering/export (compliance + safety).
3. **UX-08/10** — responsive + finish hardcoded-hex migration (dark mode + smaller screens).
