# Accessibility Audit — Section 508 / WCAG 2.2 AA

> **Read-only static audit** of `frontend/src` (Next.js 16 / React 18). Method: source-code analysis (no automated scanner, no live AT run), plus **computed WCAG contrast ratios** (relative-luminance formula) for the measured palette. Every finding carries a `file:line` anchor and a WCAG 2.2 success criterion. Scope: the reviewer/registry web app; the FastAPI backend is out of scope for WCAG.

## Executive summary

DocuAction's accessibility is a **split verdict along the three-dialect fault line** established in Parts 3–5:

- **Dialect 1 — platform-token pages (~19):** genuinely accessible. The shared `src/platform/components/*` were built with a11y in mind — `aria-sort`, keyboard-activatable rows, focus traps, `role="status"` skeletons, 44px targets, an AA-tuned token palette (light **and** dark). These pages would substantially pass 508/AA.
- **Dialect 2 — Tailwind + hardcoded-hex pages (~28):** where failures concentrate. Sub-11px text (688 uses), hardcoded-hex **contrast failures** (measured below), non-semantic clickables, placeholder-as-label, and no keyboard path on custom rows.
- **Dialect 3 — CSS-class auth/legacy pages (~18):** mixed. The **auth pages are actually a bright spot** for forms (they carry the only real `htmlFor` label associations and `autoComplete`), but they sit off the token system (contrast unverified per-page) and some CRM/legacy pages repeat the Dialect-2 problems.

**The token *system* is AA-compliant; the *leaks* are where AA breaks.** This is the same root cause as the design-system debt (Part 4): the gap is adoption, not capability.

**Overall accessibility score: 6.4 / 10** (weighted: strong component foundation dragged down by ~46 non-conforming pages).

---

## 1. Keyboard operability (WCAG 2.1.1, 2.1.2, 2.4.3, 2.4.7)

### 1.1 Keyboard access — the good
The platform components model correct keyboard interaction:
- `platform/components/DataTable.js` — rows are `tabIndex={0}` with `onKeyDown` handling **Enter/Space**, `aria-sort` on sortable headers, sticky header, sr-only `<caption>`.
- `platform/components/SidePanel.js` — **focus trap**, **Escape** to close, focus **restored** to the trigger on close, `role="dialog"` + `aria-modal`, background scroll-lock.
- `platform/components/KPICard.js` — keyboard-activatable when clickable.
- `app/tefca-arc/decisions/page.js:68` — a **correct pattern**: `role="option"` + `tabIndex={0}` + `aria-selected` + `onClick` on a `<div>` (this is how the failing ones below *should* look).
- `components/AppLayout.js` sidebar — `aria-current`, `aria-expanded`, `aria-controls`, `aria-label`.

Signal totals: **23 `tabIndex`**, **15 `onKeyDown`**, **86 `role=`**, **258 `aria-*`** attributes across `src/`.

### 1.2 Keyboard access — the failures (WCAG 2.1.1, Level A)
**14** non-semantic `<div>/<span>/<li>` click handlers exist. Excluding the ones that are decorative backdrops (redundant to an Escape/close button — acceptable), **~7 are genuinely interactive controls with no keyboard path**:

| # | Location | Element | Issue | WCAG |
|---|---|---|---|---|
| K-01 | `app/ats/page.tsx:146` | pipeline `<div>` card | click-to-filter, no `tabIndex`/`onKeyDown`/`role` | 2.1.1 (A) |
| K-02 | `app/ats-agent/page.tsx:442` | expander `<div>` | `cursor:pointer` toggle, keyboard-inaccessible | 2.1.1 (A) |
| K-03 | `app/deal-tracker/page.tsx:127` | filter card `<div>` | click-to-filter, no keyboard | 2.1.1 (A) |
| K-04 | `app/decisions/page.js:126` | list-row `<div>` | selects detail, no keyboard | 2.1.1 (A) |
| K-05 | `app/opportunities/page.tsx:459` | opportunity card `<div>` | `matchOpportunity` on click only | 2.1.1 (A) |
| K-06 | `app/projects/page.tsx:122` | stage card `<div>` | click-to-filter, no keyboard | 2.1.1 (A) |
| K-07 | `app/validation/page.js:149` | list-row `<div>` | selects detail, no keyboard | 2.1.1 (A) |
| — | `app/bulletin/components/DailyBriefingTab.js:293` | TOC `<div>` | scroll-to on click; content also reachable → minor | 2.1.1 (A, minor) |

**Acceptable (not counted as failures):** `app/dashboard/page.js:779,795`, `components/UsersAdmin.js:271` — modal backdrops with `aria-hidden="true"`; dismissal is redundant to a keyboard-reachable close/Escape. `components/AppShell.tsx:57` — nav overlay, same pattern.

### 1.3 Focus visibility (WCAG 2.4.7, Level AA)
**8** `outline:none` / `focus:outline-none` usages. Two sub-classes:
- **Partial-pass** — outline removed but **replaced** by a visible `focus:border-[#2563EB]` color change: `app/compare/page.js:143,349`, `app/dashboard/page.js:415`, `app/decisions/page.js:118`, `app/contact/page.js:39–48`. A border-color swap is a visible focus indicator (meets 2.4.7 minimally) but is **thin and low-salience**.
- **True failure** — outline removed with **no replacement** indicator: `app/actions-inbox/page.js:339`, `app/analytics/page.js:157`, `app/decisions/page.js:202`, `app/bulletin/lib/constants.js:94` (`inputStyle` shared across bulletin inputs). These are **2.4.7 (AA) failures** — the focused control shows no focus ring at all.

**No global `:focus-visible` reset** was found that would break focus app-wide; the risk is localized to the listed controls. Native focus rings survive on the platform/auth pages.

### 1.4 Focus order & traps (2.4.3, 2.1.2)
No positive `tabIndex` (>0) misuse found — all `tabIndex` values are `0` or `-1` (correct). SidePanel is the only focus trap and it is **escapable** (Escape + close), so **no keyboard trap (2.1.2) risk**.

---

## 2. Contrast (WCAG 1.4.3 text, 1.4.11 non-text — Level AA)

Ratios computed with the WCAG relative-luminance formula. AA thresholds: **4.5:1** normal text, **3.0:1** large text (≥18.66px bold / ≥24px) and UI components/graphics.

### 2.1 Token palette on white `#ffffff` — **PASS**
| Token | Hex | Ratio | Normal | Verdict |
|---|---|---:|:--:|---|
| textPrimary | `#323130` | 12.98 | ✅ | PASS |
| textSecondary | `#605e5c` | 6.46 | ✅ | PASS |
| textTertiary (A11Y-fixed) | `#757270` | 4.78 | ✅ | PASS |
| accent (A11Y-fixed) | `#006ec3` | 5.23 | ✅ | PASS |
| success | `#107c10` | 5.37 | ✅ | PASS |
| error | `#a4262c` | 7.26 | ✅ | PASS |
| warning | `#835c00` | 6.01 | ✅ | PASS |
| purple | `#8764b8` | 4.62 | ✅ | PASS |

The **A11Y-1.0 pass worked**: the accent moved `#0078d4` (4.53, marginal) → `#006ec3` (5.23), and textTertiary was fixed to `#757270` (4.78). Every semantic token clears AA on white.

### 2.2 Dark theme on `#323130` — **PASS (well-tuned)**
| Token | Hex | Ratio | Verdict |
|---|---|---:|---|
| dark accent | `#65b2eb` | 5.64 | PASS |
| dark success | `#67be67` | 5.65 | PASS |
| dark error | `#e49699` | 5.64 | PASS |
| dark text | `#c8c6c4` | 7.62 | PASS |
| dark textSecondary | `#a19f9d` | 4.92 | PASS |
| dark surface text | `#faf9f8` | 12.35 | PASS |

`DARK_PALETTE` is genuinely AA-tuned — **but it only *applies* on the ~19 Dialect-1 pages**. On Dialect 2/3 the hardcoded light hex does not flip, so dark mode is broken there (Part 4, DS-06), which is *also* a contrast problem (light text tokens on a dark shell, or vice-versa).

### 2.3 Hardcoded-hex leaks — **FAIL** (this is where AA breaks)
| Hex | Role / leak | Ratio on white | Normal 4.5 | Large 3.0 | Verdict | WCAG |
|---|---|---:|:--:|:--:|---|---|
| `#a19f9d` | Fluent "disabled" grey used as **body text** | **2.64** | ❌ | ❌ | **FAIL (text)** | 1.4.3 |
| `#ffb900` | Fluent gold used as text | **1.72** | ❌ | ❌ | **FAIL (text)** | 1.4.3 |
| `#cbd5e1` | slate-300 as **control border / placeholder text** | **1.48** | ❌ | ❌ | **FAIL if bounding a control** | 1.4.11 / 1.4.3 |
| `#16a34a` | Tailwind green-600 (duplicate green) as text | **3.30** | ❌ | ✅ | **FAIL normal / pass large** | 1.4.3 |
| `#d97706` | Tailwind amber-600 as text | **3.19** | ❌ | ✅ | **FAIL normal / pass large** | 1.4.3 |
| `#0078d4` | stale accent (pre-A11Y) | 4.53 | ✅ | ✅ | marginal pass (leak, not contrast fail) | — |
| `#dc2626` | Tailwind red-600 (duplicate red) | 4.83 | ✅ | ✅ | pass (consistency leak only) | — |
| `#2563eb` | Tailwind blue-600 (duplicate blue) | 5.17 | ✅ | ✅ | pass (consistency leak only) | — |

**Interpretation:** Not every leak is a contrast failure — the duplicate Tailwind red/blue actually pass. The **hard contrast failures are the greys and the green/amber**: `#a19f9d` (body text, 2.64), `#ffb900` (gold text, 1.72), `#cbd5e1` (borders/placeholder, 1.48), and `#16a34a`/`#d97706` when used at normal size. `#cbd5e1` appears as `placeholder:text-[#CBD5E1]` (e.g. `app/dashboard/page.js:415`, `app/decisions/page.js:118`) — **placeholder text at 1.48:1 is invisible**; if placeholder is doing label duty (see §5) that is a compound A-level failure.

**Contrast is compounded by size.** Many of these hex colors are applied to `text-[9px]`/`text-[10px]` runs on the same Dialect-2 pages — small *and* low-contrast at once.

---

## 3. Non-text content & images (WCAG 1.1.1, Level A)
**0 `<img>` / `<Image>` tags** in `src/`. Iconography is inline SVG / icon components and text; there is **no missing-`alt` exposure** on raster images. Verify that decorative inline SVGs carry `aria-hidden="true"` and that meaningful icon-only buttons carry `aria-label` (79 `aria-label` present; 117 `aria-hidden` present — coverage looks reasonable but is not exhaustively verified per-icon).

---

## 4. ARIA & screen-reader support (WCAG 4.1.2, 1.3.1, 4.1.3)
- **258 `aria-*`** attributes, **86 `role=`**, **13 `sr-only`** usages — non-trivial SR investment, **concentrated in Dialect-1 platform components** (DataTable, SidePanel, LoadingSkeleton `role="status"`, StatusBadge, AppLayout nav).
- **Live regions:** `LoadingSkeleton` uses `role="status"`/`aria-busy`; confirm that async result toasts/errors are announced (only **5** `aria-invalid`/`aria-describedby` total — see §5). Status changes on Dialect-2 pages likely **do not announce** (4.1.3 Status Messages, AA — partial).
- **Landmarks/headings** feed SR navigation — see §6.
- **Risk:** the SR quality is as bimodal as everything else — excellent on the token pages, thin on Dialect 2/3.

---

## 5. Forms (WCAG 1.3.1, 3.3.2, 3.3.1, 4.1.2)
This is the **single largest AA gap by volume**.

| Signal | Count |
|---|---:|
| `<input>` / `<select>` / `<textarea>` | **362** |
| `<label>` elements | 259 |
| **`htmlFor` associations** | **10** |
| `aria-invalid` / `aria-describedby` (error association) | **5** |
| pages using `htmlFor` at all | **5** |

- **Programmatic label association is nearly absent.** Only **5 pages** (`app/login/page.tsx`, `app/register/page.tsx`, `app/reset-password/page.js`, `app/forgot-password/page.js`, `app/tefca-arc/decisions/page.js`) use `htmlFor`. Across the other ~40 form-bearing pages, `<label>` text sits *near* an input without a programmatic association, or the input relies on a **`placeholder` as its only label** — a **3.3.2 (A)** and **1.3.1 (A)** failure, made worse because the placeholder color (`#cbd5e1`, 1.48:1) also fails contrast.
- **Error identification/association is minimal** (5 total `aria-invalid`/`aria-describedby`). Inline validation errors on the token/auth pages are shown visually but rarely associated to the field for SR users — **3.3.1 (A)** partial.
- **Bright spot:** the **auth pages are the model to copy** — `login/page.tsx` has `htmlFor`, `autoComplete`, `required`, `autoFocus`, `aria-label` on the show/hide-password toggle, `noValidate`, and disabled-while-submitting. Generalizing this into a shared `Field` component (Part 4, DS-07) fixes most of the 362-input gap at once.

---

## 6. Headings, titles & structure (WCAG 2.4.2, 1.3.1, 2.4.6)
| Signal | Count | WCAG |
|---|---:|---|
| Pages with an `<h1>` | **24 / ~75** | 1.3.1 / 2.4.6 |
| `export const metadata` / `<title>` (page titles) | **3** | **2.4.2 (A)** |
| `<h1>` total | 30 · `<h2>` 33 · `<h3>` 10 | 1.3.1 |

- **Page titles (2.4.2, A) — systemic failure.** Only **3** route files set a document `<title>`/metadata. In a Next.js App Router app, pages without `metadata` inherit the root title, so **most routes share one generic browser-tab/SR title** — users can't distinguish pages by title. This is a **broad Level-A failure** (low effort to fix: add `metadata` per route).
- **Missing `<h1>` on ~51 pages (1.3.1 / 2.4.6).** Roughly a third of pages have a top-level heading; the rest open with a styled `<div>` header (the bare-markup headers noted in Part 4's component inventory). SR users lose the primary landmark/heading.
- Heading nesting where present (30 h1 / 33 h2 / 10 h3) looks broadly sensible; the problem is **absence**, not mis-nesting.

---

## 7. Target size (WCAG 2.5.8 Minimum, Level AA = 24px)
- **Platform tables/rows: 44px** (Part 3/5) — exceeds the 24px AA minimum and even meets 2.5.5 AAA (44px). ✅ on Dialect-1.
- **Risk on Dialect-2:** dense `text-[8px]`–`text-[10px]` interactive chips/links (688 sub-11px runs) imply **small hit targets**; inline icon buttons and tag/filter pills in `dashboard`, `case-management`, `trust`, `compare` should be spot-checked against 24px. Not individually measured here, but the density pattern is a **2.5.8 risk** on the same pages that fail everything else.

---

## 8. Motion, reflow, resize, other AA criteria (spot findings)
- **2.3.1 (Three Flashes):** no flashing content found. PASS.
- **2.3.3 / prefers-reduced-motion:** `LoadingSkeleton` honors reduced-motion; other animated transitions (`transition-colors`) are subtle. Largely OK.
- **1.4.4 Resize text / 1.4.10 Reflow:** heavy fixed-px inline sizing (14 inline `fontSize` px, 688 Tailwind bracket sizes) means text may **not scale with browser zoom** on Dialect-2 pages (px, not rem) — **1.4.4 (AA) risk**. Reflow at 320px not verified (dense tables likely require horizontal scroll → 1.4.10 risk on data pages).
- **1.4.12 Text spacing:** fixed px line-heights may clip on user text-spacing overrides — minor risk on dense pages.

---

## 9. Per-page accessibility scores

Scored 0–10 across keyboard, contrast, forms/labels, headings/title, ARIA/SR. Grouped by dialect (the score tracks the dialect almost perfectly).

### Dialect 1 — platform-token pages (best)
| Page | Score | Notes |
|---|:--:|---|
| `tefca-registry/{page,entities,entity,verification,issues}` | **8.5** | DataTable a11y, 44px, tokens, dark mode; minor: per-route title |
| `tefca-arc/page` (Mission Control) | **8.5** | KPICard keyboard, StatusBadge, tokens |
| `tefca-arc/{import,qa,cycles,findings,reviews,validation,audit,connectors,administration,analytics,reports}` | **8.0–8.5** | strong shared components |
| `tefca-arc/decisions` | **8.5** | exemplary `role="option"`+`tabIndex`+`aria-selected` keyboard row |
| `tefca-arc/priority` | **8.0** | SidePanel focus trap |
| `admin/users` (`UsersAdmin.js`) | **7.5** | good aria; drawer backdrop `aria-hidden` correct |
| **Login** (`app/login/page.tsx`) | **8.0** | best form a11y (htmlFor, autoComplete, aria-label); off-token styling only |

### Dialect 3 — auth/legacy
| Page | Score | Notes |
|---|:--:|---|
| `register`, `reset-password`, `forgot-password` | **7.0** | carry `htmlFor`; off-token contrast unverified per-field |
| `verify-email`, `auth/callback` | **6.0** | mostly status text; titles missing |
| `contact` | **5.0** | `focus:outline-none`+border only; placeholder-as-label risk |
| `settings`, CRM legacy (`invoices,pricing,bom,company-profile,support,staffing,…`) | **4.5–5.5** | off-token, label association gaps |

### Dialect 2 — Tailwind + hardcoded-hex (worst)
| Page | Score | Notes |
|---|:--:|---|
| `dashboard` | **3.5** | 158 sub-11px, `#cbd5e1` placeholder (1.48), div-onClick backdrops OK but drawer content dense; no `<h1>`/title |
| `case-management` | **3.5** | 122 sub-11px, hex contrast leaks |
| `trust` | **3.5** | 100 sub-11px, hex leaks |
| `compare` | **4.0** | 79 sub-11px, `outline-none`+border (partial focus) |
| `actions-inbox` | **3.5** | **bare `focus:outline-none`** (339), `text-[9px]` |
| `decisions` | **4.0** | **K-04 keyboard fail** (126), bare `outline-none` select (202), `#cbd5e1` placeholder |
| `validation` | **4.0** | **K-07 keyboard fail** (149) |
| `documents` | **4.0** | sub-11px, hex leaks |
| `analytics` | **4.0** | **bare `focus:outline-none`** (157) |
| `ats`, `ats-agent`, `deal-tracker`, `opportunities`, `projects` | **4.0** | **K-01/02/03/05/06 keyboard fails**; commercial pages |
| `healthcare`, `intelligence` | **4.5** | sub-11px |

**Best pages:** the `tefca-registry` and `tefca-arc` token pages + `login` (8.0–8.5).
**Worst pages:** `dashboard`, `case-management`, `trust`, `actions-inbox` (3.5).

---

## 10. Overall score & verdict

**Overall accessibility score: 6.4 / 10.**

Rationale: a genuinely accessible shared component core and an AA-tuned token palette (would score ~8.5 alone) is dragged down by **~46 pages** (Dialects 2+3) carrying keyboard, contrast, label, and title failures — plus **two systemic Level-A gaps** (page titles on ~72 pages; label association on ~40 form pages) that touch nearly the whole app.

**The remediation is convergence, not construction** — the same finding as Parts 4–5. Adopting the existing `Field`/`DataTable`/token stack app-wide, adding per-route `metadata`, and killing the hex/tiny-text leaks would move the whole app from ~6.4 to ~8.5 without inventing anything new.

*See `wcag_compliance_matrix.md` for the criterion-by-criterion determination and `accessibility_issues.md` for the enumerated issues and remediation priorities.*
