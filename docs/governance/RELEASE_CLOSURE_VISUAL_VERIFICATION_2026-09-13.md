# Release-Closure Visual Verification of Frontend PR #42 (d4e29f1) on a Local Synthetic Runtime, 2026-09-13

**Scope and method.** Every exported route of the PR #42 build (79 routes) was loaded in headless Microsoft Edge (Playwright, channel msedge) against a LOCAL backend (PR #59 tree at 78fd28c on an ephemeral Postgres migrated to head, one synthetic user, no Government data) with the PR #42 static export built for that API. Each authenticated route was loaded with an injected local session in light and dark themes at desktop (1366), tablet (834) and mobile (390) widths; 640 and 320 widths stand in for 200% and 400% zoom reflow; axe-core 4.x (wcag2a/aa/21aa/22aa) and an eight-stop keyboard walk ran at desktop. This is DEV-style verification on a local runtime, not the Azure DEV site and not a Section 508 certification. `/login`, `/signup` and `/register` were re-tested without a session (they redirect to the dashboard when a session exists).

Result rules: FAIL = redirect/HTTP error, theme attribute missing, light island in dark (>=15,000 px2 element with luminance >0.85), horizontal page overflow at any width, or any serious/critical axe violation; PARTIAL = only structural notes (missing h1 or main landmark, unlabeled inputs, tables without th); PASS = none of these.

| Route | Kind | Result | Light | Dark | Desktop | Tablet | Mobile | Zoom 200 | Zoom 400 | Keyboard | Focus | axe serious L/D | Contrast L/D | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| / | public | FAIL | PASS | PASS | PASS | PASS | FAIL | FAIL | FAIL | PASS | PASS | 34/36 | 33/35 | h1=0; 1 unlabeled inputs; 1 dark blocks in light |
| /actions-inbox/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 25/23 | 24/22 | h1=0; 1 dark blocks in light |
| /admin/users/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 2/8 | 0/6 | no main landmark; 3 unlabeled inputs |
| /agency-contacts/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 0/8 | 0/8 | h1=0; 1 unlabeled inputs |
| /ai-analyze/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/8 | 0/7 | h1=0; 2 unlabeled inputs |
| /analytics/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 20/13 | 19/12 | h1=0; 1 unlabeled inputs; 2 dark blocks in light |
| /ats-agent/ | auth | FAIL | PASS | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 | no main landmark; page error boundary shown |
| /ats/ | auth | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/0 | 0/0 | h1=0 |
| /auth/callback/ | public | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/1 | 0/1 | h1=0; no main landmark; 1 dark blocks in light |
| /bench-sales/ | auth | FAIL | PASS | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 2/2 | 0/0 | no main landmark; page error boundary shown |
| /bom/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 | h1=0 |
| /bulletin/ | auth | FAIL | PASS | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 25/18 | 22/15 | no main landmark; 3 unlabeled inputs; 5 light islands in dark |
| /case-management/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 33/16 | 26/9 | no main landmark; 10 unlabeled inputs; 2 dark blocks in light |
| /company-profile/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 6/15 | 0/9 | h1=0; 22 unlabeled inputs |
| /compare/ | auth | FAIL | PASS | PASS | FAIL | FAIL | FAIL | FAIL | FAIL | PASS | PASS | 2/1 | 1/0 | h1=0; no main landmark; 1 unlabeled inputs |
| /contact/ | public | FAIL | PASS | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 22/16 | 22/16 | 4 unlabeled inputs; 1 light islands in dark; 1 dark blocks in light |
| /dashboard/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | FAIL | FAIL | PASS | PASS | 34/36 | 33/35 | h1=0; 1 unlabeled inputs; 1 dark blocks in light |
| /deal-tracker/ | auth | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/0 | 0/0 | h1=0 |
| /deal/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 2/1 | 1/0 | h1=0 |
| /decisions/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 7/4 | 7/4 | h1=0; 1 unlabeled inputs; 1 dark blocks in light |
| /documents/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 6/4 | 6/4 | h1=0; 1 unlabeled inputs; 1 dark blocks in light |
| /finance/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 0/0 | 0/0 |  |
| /forgot-password/ | public | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 3/3 | 3/3 | no main landmark |
| /healthcare/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 2/0 | 2/0 | h1=0; no main landmark |
| /intel/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 0/0 | 0/0 |  |
| /intelligence/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 6/4 | 6/4 | h1=0; 1 dark blocks in light |
| /invoices/ | auth | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/0 | 0/0 | h1=0 |
| /login/ | public | FAIL | PASS | PASS | PASS | PASS | FAIL | FAIL | FAIL | PASS | PASS | 34/36 | 33/35 | h1=0; 1 unlabeled inputs; 1 dark blocks in light; with-session run redirects to dashboard; see no-session re-test below |
| /manage-customers/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 1/2 | 0/1 | 2 unlabeled inputs |
| /manage-products/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 0/8 | 0/8 | h1=0 |
| /manage-suppliers/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 1/2 | 0/1 | 2 unlabeled inputs |
| /manage-users/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/0 | 1/0 | h1=0 |
| /opportunities/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | FAIL | PASS | PASS | 2/4 | 0/2 | h1=0; 5 unlabeled inputs |
| /pricing/ | public | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 | h1=0 |
| /product/ | public | FAIL | PASS | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 26/20 | 26/20 | 1 light islands in dark |
| /projects/ | auth | FAIL | PASS | PASS | PASS | FAIL | FAIL | FAIL | FAIL | PASS | PASS | 0/3 | 0/3 | 1 unlabeled inputs |
| /proposal-library/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 0/9 | 0/9 | h1=0; 1 unlabeled inputs |
| /quotes/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 0/1 | 0/1 |  |
| /register/ | public | FAIL | PASS | PASS | PASS | PASS | FAIL | FAIL | FAIL | PASS | PASS | 34/36 | 33/35 | h1=0; 1 unlabeled inputs; 1 dark blocks in light; with-session run redirects to dashboard; see no-session re-test below |
| /reset-password/ | public | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 2/2 | 2/2 | no main landmark |
| /rfqs/ | auth | FAIL | PASS | PASS | PASS | FAIL | FAIL | PASS | FAIL | PASS | PASS | 0/3 | 0/3 | 1 unlabeled inputs |
| /settings/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 3/7 | 3/7 |  |
| /signup/ | public | FAIL | PASS | PASS | PASS | PASS | FAIL | FAIL | FAIL | PASS | PASS | 34/36 | 33/35 | h1=0; 1 unlabeled inputs; 1 dark blocks in light; with-session run redirects to dashboard; see no-session re-test below |
| /staffing/ | auth | FAIL | PASS | PASS | PASS | PASS | FAIL | PASS | FAIL | PASS | PASS | 0/5 | 0/5 | h1=0 |
| /support/ | public | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/1 | 0/1 |  |
| /tefca-arc/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 6/1 | 5/0 |  |
| /tefca-arc/administration/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/analytics/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 2/1 | 1/0 |  |
| /tefca-arc/assignment/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/audit/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 18/1 | 17/0 |  |
| /tefca-arc/configuration/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 4/1 | 3/0 |  |
| /tefca-arc/connectors/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 2/1 | 1/0 |  |
| /tefca-arc/cycles/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/dashboard/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 13/1 | 12/0 |  |
| /tefca-arc/decisions/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 2/1 | 1/0 |  |
| /tefca-arc/deliveries/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/findings/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/help/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 2/1 | 1/0 |  |
| /tefca-arc/import/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 | 1 unlabeled inputs |
| /tefca-arc/insights/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 10/1 | 9/0 |  |
| /tefca-arc/my-reviews/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/operations/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/priority/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/qa/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 3/1 | 2/0 |  |
| /tefca-arc/reports/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/reviews/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/search/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/2 | 0/1 |  |
| /tefca-arc/trust-center/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 14/9 | 13/8 |  |
| /tefca-arc/validation/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-arc/workspace/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 1/1 | 0/0 |  |
| /tefca-dashboard/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 28/1 | 27/0 |  |
| /tefca-registry/ | auth | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/0 | 0/0 | no main landmark |
| /tefca-registry/entities/ | auth | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/0 | 0/0 | no main landmark |
| /tefca-registry/entity/ | auth | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/0 | 0/0 | no main landmark |
| /tefca-registry/issues/ | auth | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/0 | 0/0 | no main landmark |
| /tefca-registry/verification/ | auth | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/0 | 0/0 | no main landmark |
| /trust/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 20/0 | 20/0 | h1=0 |
| /validation/ | auth | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 10/4 | 10/4 | h1=0; 1 dark blocks in light |
| /verify-email/ | public | FAIL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | 0/1 | 0/1 | no main landmark |

Totals: {'FAIL': 71, 'PARTIAL': 8} of 79 routes.

## No-session re-test of the public authentication pages

| Route | Light | Dark | h1 | main | Overflow (390 / 320) | axe serious (light / dark) |
|---|---|---|---|---|---|---|
| /login/ | PASS | PASS | 1 | 1 | none / none | 0 / 1 (colour-contrast, 1 node) |
| /signup/ | PASS | PASS | 1 | 0 | none / none | 0 / 0 |
| /register/ | PASS | PASS | 1 | 0 | none / none | 0 / 0 |

## Learning Center (Program Manager role, the only path with all 16 modules)

Rendered in light and dark at desktop and mobile: all 16 module titles present; five role-path steppers (Administrator, Program Manager, BA/Analyst, Independent QA, Viewer/COR read-only) with 51 steps; the evidence-pipeline figure renders as a captioned figure with a seven-item accessible list and the labelled absolute human decision boundary; labelled search with a live status region; two knowledge checks; five progress indicators; one h1, one main, two nav landmarks; no horizontal overflow at 390px; no vague link text. axe: one critical `aria-allowed-attr` node (shared shell component, see defects) and one colour-contrast node in light. Previous/next and related-training controls live inside module views and were not exercised on the landing view.

## Defect classes found (for builder remediation, not fixed here)

1. Shared shell: one `aria-allowed-attr` critical node on 26 pages (`.bg-transparent` element and an `input` in the TEFCA command bar / search) and `button-name` on six `.h-7` buttons; `select-name` on 13 selects; `label` on 12 inputs; `link-in-text-block` on three links.
2. Colour contrast (axe serious): 458 nodes in light and 372 in dark across the legacy DocuAction core and GovCon pages (dashboard 33/35, tefca-dashboard 27/0, trust 20/0, analytics 19/12, case-management 26/9, bulletin 22/15, product 26/20, contact 22/16, audit 17/0); TEFCA ARC module pages total 65 light / 9 dark nodes.
3. Horizontal overflow at 390px and 320px on 20 routes (dashboard, actions-inbox, documents, compare at every width, projects and rfqs from tablet, and 13 GovCon pages); none on TEFCA ARC module pages.
4. Heading and landmark structure: 25 routes without an h1 (core and GovCon pages, pricing, callback); 15 routes without a main landmark (admin/users, bulletin, case-management, healthcare, forgot/reset password, signup, register, verify-email, callback, ats-agent, bench-sales, five tefca-registry pages).
5. Dark theme leakage: three routes only (bulletin: five light panels; contact and product: one light section each). Light theme: dark status strips and cards on dashboard, analytics, case-management, decisions, validation, intelligence (bg-[#1E293B] / bg-[#0F172A] Tailwind literals).
6. `/ats-agent/` and `/bench-sales/` render the client error boundary ("This page couldn't load") when their GovCon API endpoints answer 404 on this runtime, and the error boundary renders outside the theme provider (no data-theme attribute).

Every route: keyboard walk reached focusable elements with a visible focus indicator (79/79); the first Tab lands on the skip link on 62 routes.