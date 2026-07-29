# Accessibility Issues Register & Remediation Priorities

> Enumerated findings with `file:line`, WCAG 2.2 criterion, severity, and remediation. **Documented only — no fixes applied.** IDs `A11Y-###`. Severity: **Critical** (Level-A, broad reach) · **High** (AA, broad, or A narrow) · **Medium** · **Low**.

## Summary counts

| Metric | Value |
|---|---|
| **Total accessibility issues** | **24 issue groups** (spanning ~120+ individual `file:line` sites) |
| WCAG criteria **failing** | **9** (1.3.1, 1.4.3, 1.4.11, 2.1.1, 2.4.2, 2.4.6, 2.4.7, 3.3.2, 4.1.2) |
| WCAG criteria **partial** | **18** |
| WCAG criteria **passing** | **22** |
| Critical (Level-A, broad) | 5 groups |
| High | 7 groups |
| Medium | 8 groups |
| Low | 4 groups |

---

## Critical (Level-A, broad reach)

### A11Y-01 — Missing page titles (2.4.2)
- **Where:** only 3 of ~75 routes set `metadata`/`<title>`; all others inherit the root title.
- **Impact:** SR users and tab-switchers cannot distinguish pages. Level-A failure across ~72 pages.
- **Fix:** add `export const metadata = { title: '…' }` (or `generateMetadata`) to each route file. Mechanical, ~0.5–1d for the whole app.

### A11Y-02 — Form inputs without programmatic labels (3.3.2, 1.3.1, 4.1.2)
- **Where:** 362 inputs, only **10 `htmlFor`** on **5 pages** (`app/login/page.tsx`, `app/register/page.tsx`, `app/reset-password/page.js`, `app/forgot-password/page.js`, `app/tefca-arc/decisions/page.js`). ~40 other form pages rely on adjacent text or `placeholder` as the label.
- **Impact:** SR users get unlabeled fields; the largest single AA/A gap by volume.
- **Fix:** build a shared `Field` component (label+`htmlFor`+input+error+`aria-describedby`) — Part 4 DS-07 — and migrate forms to it. Copy the auth-page pattern.

### A11Y-03 — Missing `<h1>` / heading structure (1.3.1, 2.4.6)
- **Where:** only **24 of ~75** pages have an `<h1>`; the rest open with styled `<div>` headers.
- **Impact:** SR heading navigation broken on ~51 pages.
- **Fix:** the shared `PageHeader` component (Part 4 DS-08) should render an `<h1>`; adopt app-wide.

### A11Y-04 — Interactive `<div>` controls with no keyboard path (2.1.1, 4.1.2)
- **Where (~7):** `app/ats/page.tsx:146`, `app/ats-agent/page.tsx:442`, `app/deal-tracker/page.tsx:127`, `app/decisions/page.js:126`, `app/opportunities/page.tsx:459`, `app/projects/page.tsx:122`, `app/validation/page.js:149`.
- **Impact:** keyboard-only users cannot activate these cards/rows.
- **Fix:** add `role="button"` (or `option`) + `tabIndex={0}` + `onKeyDown` (Enter/Space). **Reference implementation already exists** at `app/tefca-arc/decisions/page.js:68` — copy it, or route through platform `DataTable`/`KPICard`.

### A11Y-05 — Placeholder used as label + failing contrast (3.3.2, 1.4.3)
- **Where:** `placeholder:text-[#CBD5E1]` at `app/dashboard/page.js:415`, `app/decisions/page.js:118`, and other Dialect-2 inputs.
- **Impact:** compound A-level failure — placeholder is the only label **and** is invisible (1.48:1). When the field is filled, the "label" disappears entirely.
- **Fix:** real `<label>` (via A11Y-02) + drop `#cbd5e1` placeholder color for a token grey ≥4.5:1.

---

## High

### A11Y-06 — Body-text contrast failure `#a19f9d` (1.4.3)
- **Where:** `#a19f9d` (2.64:1) used as text across Dialect-2 pages (Part 4 lists 30–80 uses among the greys).
- **Fix:** replace with `--text-secondary` `#605e5c` (6.46) or `--text-tertiary` `#757270` (4.78).

### A11Y-07 — Duplicate green/amber fail at normal size (1.4.3)
- **Where:** `#16a34a` (3.30) 127 uses, `#d97706` (3.19) as status/text on Dialect-2.
- **Fix:** retire to token `--success` `#107c10` (5.37) / `--warning` `#835c00` (6.01). Ties into DS-01.

### A11Y-08 — Gold text `#ffb900` fails badly (1.4.3)
- **Where:** `#ffb900` (1.72:1) used as text.
- **Fix:** use gold only as a non-text accent on dark, or replace text uses with `--warning`.

### A11Y-09 — Bare `focus:outline-none` with no replacement (2.4.7)
- **Where:** `app/actions-inbox/page.js:339`, `app/analytics/page.js:157`, `app/decisions/page.js:202`, `app/bulletin/lib/constants.js:94` (shared `inputStyle`, affects all bulletin inputs).
- **Fix:** remove `outline:none`, or add a visible `:focus-visible` ring (2px, ≥3:1). Fixing `constants.js:94` fixes every bulletin input at once.

### A11Y-10 — Non-text contrast on control borders `#cbd5e1` (1.4.11)
- **Where:** `#cbd5e1` (1.48:1) used as input/card borders on Dialect-2.
- **Fix:** token border `--card-border` at ≥3:1 against adjacent surfaces.

### A11Y-11 — Sub-11px text (1.4.4, legibility; compounds 1.4.3)
- **Where:** **688** sub-11px Tailwind runs — `text-[10px]`×265, `[9px]`×213, `[8px]`×210, `[7px]`×31 — concentrated in `dashboard` (158), `case-management` (122), `trust` (100), `compare` (79).
- **Fix:** enforce the platform's own 11px floor via `TYPE` tokens (DS-02) + a lint rule banning sub-11px (DS-05).

### A11Y-12 — Errors not associated to fields (3.3.1, 4.1.3)
- **Where:** only 5 `aria-invalid`/`aria-describedby` across `src/`.
- **Fix:** the shared `Field` (A11Y-02) should wire `aria-invalid` + `aria-describedby` to the error node; announce async results via a `role="status"`/`aria-live` region.

---

## Medium

### A11Y-13 — Thin focus indicators (border-swap only) (2.4.7)
- **Where:** `focus:outline-none` **paired with** `focus:border-[#2563EB]`: `app/compare/page.js:143,349`, `app/dashboard/page.js:415`, `app/decisions/page.js:118`, `app/contact/page.js:39–48`.
- **Note:** minimally passes (visible border change) but low-salience. **Fix:** add a proper focus ring.

### A11Y-14 — `autoComplete` / input-purpose missing off-auth (1.3.5)
- **Where:** present on auth pages; absent on most Dialect-2 forms. **Fix:** add `autoComplete` tokens in `Field`.

### A11Y-15 — No skip-link / bypass block (2.4.1)
- **Where:** no skip-to-content link found in `AppLayout`/`AppShell`. **Fix:** add a visually-hidden skip link as the first focusable element.

### A11Y-16 — `<html lang>` unverified (3.1.1)
- **Where:** root layout not confirmed to set `lang="en"`. **Fix:** verify/add `lang` on the root `<html>`.

### A11Y-17 — Status changes may not announce on Dialect-2 (4.1.3)
- **Where:** async fetch results/toasts outside platform components. **Fix:** shared live-region (ties to A11Y-12).

### A11Y-18 — Target-size risk on dense chips (2.5.8)
- **Where:** sub-11px interactive pills/filters on `dashboard`, `case-management`, `trust`, `compare`. **Fix:** ensure ≥24px hit area (padding) even when text is small.

### A11Y-19 — Reflow / resize on dense tables (1.4.10, 1.4.4)
- **Where:** fixed-px data grids on Dialect-2. **Fix:** rem-based sizing + responsive pass (UX-08).

### A11Y-20 — Destructive actions lack confirmation (3.3.4)
- **Where:** inconsistent/missing confirm dialogs (Part 5 AL-01). **Fix:** shared Fluent `Dialog` confirm on destructive actions.

---

## Low

### A11Y-21 — Icon-button name verification (2.5.3, 2.4.4)
- Spot-verify visible-label-in-accessible-name for icon-only buttons (79 `aria-label` present).

### A11Y-22 — Consistent-identification drift (3.2.4)
- Duplicate StatusBadge/StatusPill, 2 headers (Part 4) — same function, different presentation. Converge (DS-08).

### A11Y-23 — Focus-not-obscured with sticky headers (2.4.11)
- Verify focused rows aren't hidden under sticky headers/SidePanel on scroll.

### A11Y-24 — Tooltip/hover dismissibility (1.4.13)
- Audit any hover popovers for dismiss + hover-persistence.

---

## Top 5 remediation priorities

Ranked by **(criteria cleared × pages reached) ÷ effort**. All are **converge-not-build** — they adopt components/tokens that already exist.

| # | Priority | Clears | Reach | Effort | Why first |
|---|---|---|---|:--:|---|
| **1** | **Per-route page titles** (A11Y-01) — add `metadata` to every route | **2.4.2 (A)** | ~72 pages | **0.5–1d** | Highest reach-per-hour; pure Level-A win; zero design risk. |
| **2** | **Shared `Field` component** (A11Y-02, 05, 12, 14) — label+`htmlFor`+`aria-invalid`+`aria-describedby`+`autoComplete`, migrate forms | **3.3.2, 1.3.1, 4.1.2, 3.3.1, 1.3.5, 4.1.3** | ~40 form pages / 352 inputs | **4–6d** | One component clears **5–6 criteria** including 3 Level-A; copies the working auth pattern. |
| **3** | **Kill contrast + tiny-text leaks** (A11Y-06,07,08,10,11) — hex→tokens, 11px floor, lint rule | **1.4.3, 1.4.11, 1.4.4** | ~28 Dialect-2 pages | **3–4d** (DS-01/02/05) | Fixes the measured AA contrast fails **and** fixes dark mode (DS-06) as a side effect. |
| **4** | **Keyboard-enable custom controls + `PageHeader` `<h1>`** (A11Y-03, 04) — copy `tefca-arc/decisions:68` pattern; adopt shared `PageHeader` | **2.1.1, 1.3.1, 2.4.6, 4.1.2** | ~7 controls + ~51 pages | **2–3d** | Restores keyboard + heading nav; reference implementation already in-repo. |
| **5** | **Restore focus indicators** (A11Y-09, 13) — remove bare `outline:none`, add `:focus-visible` ring (fix `constants.js:94` once) | **2.4.7** | bulletin + ~8 sites | **1d** | Cheap AA win; one shared-const fix cascades across bulletin inputs. |

**Combined effort ≈ 11–15 engineer-days** to move from **6.4 → ~8.5** and clear **all 9 outright failures**. This overlaps almost entirely with the Part 4 design-system remediation (DS-01/02/05/07/08) and Part 5 alignment items (AL-03) — **accessibility and design-system convergence are the same work.**
