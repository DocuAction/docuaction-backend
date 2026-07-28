# WCAG 2.2 Level AA Compliance Matrix

> Criterion-by-criterion determination for the DocuAction reviewer web app, from static source review. Section 508 (2017 Revised, 36 CFR 1194) incorporates WCAG 2.0 A/AA by reference; this matrix uses the current **WCAG 2.2** A + AA set (the applicable 508 baseline plus the 2.1/2.2 additions).
>
> **Verdict key:** ✅ **Pass** (no failure found) · ⚠️ **Partial** (passes on token pages / fails on Dialect 2-3) · ❌ **Fail** (failure found affecting many pages) · ➖ N/A.
> Because the app is **bimodal**, most criteria are ⚠️ **Partial** — the determination is "pass on Dialect 1, fail on Dialect 2/3" unless noted.

## Perceivable

| SC | Title | Lvl | Verdict | Evidence |
|---|---|:--:|:--:|---|
| 1.1.1 | Non-text Content | A | ✅ | 0 `<img>`; icons are SVG/text; 117 `aria-hidden`, 79 `aria-label`. Spot-verify icon-only buttons. |
| 1.2.x | Time-based Media | A/AA | ➖ | No audio/video content. |
| 1.3.1 | Info & Relationships | A | ❌ | Labels not associated (`htmlFor`=10 vs 362 inputs); `<h1>` on only 24/75 pages; placeholder-as-label. |
| 1.3.2 | Meaningful Sequence | A | ⚠️ | DOM order sane on token pages; dense Dialect-2 grids unverified. |
| 1.3.3 | Sensory Characteristics | A | ✅ | No "click the round green one" style instructions found. |
| 1.3.4 | Orientation | AA | ✅ | No orientation lock. |
| 1.3.5 | Identify Input Purpose | AA | ⚠️ | `autoComplete` present on auth pages only; absent on most Dialect-2 forms. |
| 1.4.1 | Use of Color | A | ⚠️ | StatusBadge pairs color+text/icon (good); some Dialect-2 status uses color alone (green/red hex) — verify. |
| 1.4.3 | **Contrast (Minimum)** | AA | ❌ | `#a19f9d` 2.64, `#ffb900` 1.72, `#cbd5e1` 1.48 (text/placeholder); `#16a34a` 3.30 & `#d97706` 3.19 at normal size. Token palette passes. |
| 1.4.4 | Resize Text | AA | ⚠️ | Fixed-px sizing (688 Tailwind bracket px + inline px) may not scale with zoom on Dialect-2. |
| 1.4.5 | Images of Text | AA | ✅ | Text is real text; no image-of-text. |
| 1.4.10 | Reflow | AA | ⚠️ | Dense data tables likely require horizontal scroll at 320px; not verified. |
| 1.4.11 | **Non-text Contrast** | AA | ❌ | `#cbd5e1` (1.48) used as control border; some focus indicators thin. |
| 1.4.12 | Text Spacing | AA | ⚠️ | Fixed px line-heights may clip on user overrides (Dialect-2). |
| 1.4.13 | Content on Hover/Focus | AA | ⚠️ | Tooltips/popovers not audited for dismiss/hover-persist. |

## Operable

| SC | Title | Lvl | Verdict | Evidence |
|---|---|:--:|:--:|---|
| 2.1.1 | **Keyboard** | A | ❌ | ~7 interactive `<div>` controls with no keyboard path (ats:146, ats-agent:442, deal-tracker:127, decisions:126, opportunities:459, projects:122, validation:149). |
| 2.1.2 | No Keyboard Trap | A | ✅ | Only trap is SidePanel, which is Escape-/close-able. |
| 2.1.4 | Character Key Shortcuts | A | ✅ | `KeyboardShortcuts` component present; no single-char global shortcut hazard found. |
| 2.2.1 | Timing Adjustable | A | ✅ | No time limits found (session expiry is server-side, not a content timer). |
| 2.2.2 | Pause, Stop, Hide | A | ✅ | Only skeleton shimmer (reduced-motion aware); no auto-advancing content. |
| 2.3.1 | Three Flashes | A | ✅ | No flashing content. |
| 2.4.1 | Bypass Blocks | A | ⚠️ | Persistent nav + landmarks on token pages; **no explicit skip-link** found. |
| 2.4.2 | **Page Titled** | A | ❌ | Only 3 routes set `metadata`/`<title>`; ~72 share the inherited root title. |
| 2.4.3 | Focus Order | A | ✅ | No positive `tabIndex`; order follows DOM. |
| 2.4.4 | Link Purpose (In Context) | A | ⚠️ | Most links contextual; some icon-only actions rely on `aria-label` (present) — spot-verify. |
| 2.4.5 | Multiple Ways | AA | ✅ | Global search + nav + (some) breadcrumbs. |
| 2.4.6 | Headings & Labels | AA | ❌ | ~51 pages lack `<h1>`; label text often unassociated. |
| 2.4.7 | **Focus Visible** | AA | ❌ | Bare `focus:outline-none` with no replacement: actions-inbox:339, analytics:157, decisions:202, bulletin/lib/constants.js:94. |
| 2.4.11 | Focus Not Obscured (Min) | AA | ⚠️ | Sticky headers + SidePanel could obscure focus on scroll; not verified. |
| 2.5.1 | Pointer Gestures | A | ✅ | No path/multipoint gestures. |
| 2.5.2 | Pointer Cancellation | A | ✅ | Actions fire on click/up, not down. |
| 2.5.3 | Label in Name | A | ⚠️ | Visible label vs accessible name match not fully verified on icon buttons. |
| 2.5.4 | Motion Actuation | A | ✅ | No motion-actuated features. |
| 2.5.7 | Dragging Movements | AA | ✅ | No drag-only interactions found. |
| 2.5.8 | **Target Size (Minimum, 24px)** | AA | ⚠️ | Platform rows 44px (pass, exceeds AAA); dense sub-11px chips/pills on Dialect-2 are a risk — spot-check. |

## Understandable

| SC | Title | Lvl | Verdict | Evidence |
|---|---|:--:|:--:|---|
| 3.1.1 | Language of Page | A | ⚠️ | Verify `<html lang="en">` in root layout (not confirmed in this pass). |
| 3.1.2 | Language of Parts | AA | ✅ | Single-language content. |
| 3.2.1 | On Focus | A | ✅ | No context change on focus. |
| 3.2.2 | On Input | A | ✅ | No auto-submit on input change found (selects trigger explicit fetches, not navigation). |
| 3.2.3 | Consistent Navigation | AA | ✅ | Shared `AppLayout`/`AppShell` nav is consistent. |
| 3.2.4 | Consistent Identification | AA | ⚠️ | Duplicate components (StatusBadge vs StatusPill, 2 headers) → same function, different presentation (Part 4). |
| 3.2.6 | Consistent Help | A | ➖/⚠️ | No consistent help mechanism (support page exists; not a persistent help affordance). |
| 3.3.1 | Error Identification | A | ⚠️ | Errors shown visually; only 5 `aria-invalid`/`aria-describedby` → not associated for SR. |
| 3.3.2 | **Labels or Instructions** | A | ❌ | Programmatic labels nearly absent (`htmlFor`=10); placeholder-as-label on ~40 form pages. |
| 3.3.3 | Error Suggestion | AA | ⚠️ | Auth pages suggest fixes; Dialect-2 forms minimal. |
| 3.3.4 | Error Prevention (Legal/Financial/Data) | AA | ⚠️ | Confirmation dialogs inconsistent/missing (Part 5, AL-01) → destructive actions lack confirm. |
| 3.3.7 | Redundant Entry | A | ✅ | No obvious redundant re-entry patterns. |
| 3.3.8 | Accessible Authentication (Min) | AA | ✅ | Password auth with paste + show/hide + `autoComplete`; no cognitive-test CAPTCHA in the login path found. |

## Robust

| SC | Title | Lvl | Verdict | Evidence |
|---|---|:--:|:--:|---|
| 4.1.1 | Parsing | A | ✅ | Obsolete in WCAG 2.2; React enforces well-formed markup. |
| 4.1.2 | Name, Role, Value | A | ❌ | Custom `<div>` controls lack role/name/state (K-01…K-07); form controls lack associated names. |
| 4.1.3 | Status Messages | AA | ⚠️ | `role="status"` on skeletons (good); async result/error toasts on Dialect-2 likely not announced. |

## Tally

| Verdict | Count | Criteria |
|---|:--:|---|
| ✅ **Pass** | **22** | 1.1.1, 1.2.x(N/A-adjacent), 1.3.3, 1.3.4, 1.4.5, 2.1.2, 2.1.4, 2.2.1, 2.2.2, 2.3.1, 2.4.3, 2.4.5, 2.5.1, 2.5.2, 2.5.4, 2.5.7, 3.1.2, 3.2.1, 3.2.2, 3.2.3, 3.3.7, 3.3.8, 4.1.1 |
| ❌ **Fail** | **8** | **1.3.1, 1.4.3, 1.4.11, 2.1.1, 2.4.2, 2.4.6, 2.4.7, 3.3.2, 4.1.2** *(9 incl. 4.1.2)* |
| ⚠️ **Partial** | **18** | 1.3.2, 1.3.5, 1.4.1, 1.4.4, 1.4.10, 1.4.12, 1.4.13, 2.4.1, 2.4.4, 2.4.11, 2.5.3, 2.5.8, 3.1.1, 3.2.4, 3.2.6, 3.3.1, 3.3.3, 3.3.4, 4.1.3 |
| ➖ **N/A** | **~3** | 1.2.x time-based media |

**Counting 4.1.2 among the fails → 9 outright AA failures, 18 partial, 22 pass.**

## The 9 outright failures, ranked by reach
1. **2.4.2 Page Titled (A)** — ~72 pages, trivial fix (add `metadata`).
2. **3.3.2 Labels or Instructions (A)** — ~40 form pages / 352 unassociated inputs.
3. **1.3.1 Info & Relationships (A)** — labels + missing `<h1>` (~51 pages).
4. **2.4.6 Headings & Labels (AA)** — overlaps 1.3.1; ~51 pages.
5. **4.1.2 Name, Role, Value (A)** — custom controls + unnamed inputs.
6. **1.4.3 Contrast Minimum (AA)** — 5 hex colors fail; concentrated on ~28 Dialect-2 pages.
7. **2.1.1 Keyboard (A)** — ~7 interactive divs.
8. **2.4.7 Focus Visible (AA)** — 4 bare `outline:none` sites.
9. **1.4.11 Non-text Contrast (AA)** — `#cbd5e1` control borders.

**Note the concentration:** fixing the **forms/labels** cluster (2.4.2 + 3.3.2 + 1.3.1 + 2.4.6 + 4.1.2 — five of the nine) via a shared `Field` component + per-route `metadata` clears the majority of Level-A failures in a single, mechanical converge-not-build effort.
