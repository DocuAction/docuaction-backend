# Design System Audit — Measured

> Framing: a strong system already exists (`src/platform/tokens.js` + `azure-tokens.css` + ~25 components + WCAG-tuned dark theme). This audit **measures adoption** and locates the leaks. **Read-only.**

## The headline number
| Metric | Design system defines | Actually used in `src/` | Verdict |
|---|---:|---:|---|
| **Unique colors** | ~12 PALETTE + ~16 STATES + 91 CSS vars | **191 unique hex** (2,362 occurrences) + 185 rgba + Tailwind color classes | **massive leakage** |
| **Font sizes** | 5 (`TYPE`: 11/12/14/16/24) | **14 inline px** + heavy Tailwind bracket sizes incl. **688× sub-11px** | leakage + a11y issue |
| **Spacing** | 6 (`SPACING`: 4/8/12/16/24/32) | **22 unique px** (10/6/2px off-scale, heavy) | scale not followed |
| **Font families** | 1 (Segoe UI stack) + 1 mono | **~6 declarations** (3 different Segoe stacks + Arial + var + inherit) | minor drift |

**Interpretation:** the *definitions* are ~70–90% complete and well-designed; **adoption/enforcement is ~40%.** The gap from "70%" to "100%" is overwhelmingly **removing hardcoded values and migrating the non-token pages**, not building new tokens.

## TYPOGRAPHY

**Inline `fontSize` px (14 unique):** 8, 9, 10, 11, 12, 13, 14, 15, 16, 20, 22, 24, 26, 28. Token pages use ~5 (11/12/14/16/24); the rest add ad-hoc sizes.

**Tailwind text sizing (the real problem):**
| Class | Uses | Note |
|---|---:|---|
| `text-[10px]` | 265 | below the platform's own 11px floor |
| `text-[9px]` | 213 | **too small** |
| `text-[8px]` | 210 | **too small** |
| `text-[7px]` | 31 | **far too small** |
| `text-sm` (14) | 169 | ok |
| `text-[11px]` | 107 | floor |
| `text-xs` (12) | 73 | ok |

⚠ **688+ usages of sub-11px text** — an **accessibility/legibility finding** (concentrated in Dialect-2 pages: `dashboard` 158, `case-management` 122, `trust` 100, `compare` 79). The platform's own A11Y-1.0 pass raised type to an 11px floor; the Tailwind pages regressed below it.

**Font weights:** 600 (315×), 700 (102×), 500 (6×), 400 (5×), 800 (2×) — effectively a 600/700 binary; reasonable.

**Font families (~6 declarations):** three slightly-different Segoe UI stacks + `Arial,Helvetica`, `var(--arc-font)`, `inherit`, and a Cascadia/Consolas mono. All Segoe-ish but should be **one** body stack + **one** mono.

## COLOR

**191 unique hex** across `src/`. Top offenders reveal **two overlapping palettes**:

| Hex | Uses | Role | Token exists? | Verdict |
|---|---:|---|---|---|
| `#107c10` | 139 | success green | Yes (`PALETTE.success`) | ⚠ used as literal hex, not `var(--)` |
| `#16a34a` | 127 | success green (Tailwind green-600) | **No** | ❌ **duplicate green — leak** |
| `#fff` | 123 | white | Yes (`PALETTE.card`) | ⚠ literal |
| `#0078d4` | 106 | accent blue (pre-A11Y) | **stale** (accent moved to `#006ec3`) | ❌ **stale accent — leak** |
| `#dc2626` | 105 | error red (Tailwind red-600) | **No** (`PALETTE.error` is `#a4262c`) | ❌ **duplicate red — leak** |
| `#2563eb` | 95 | blue-600 | **No** | ❌ **duplicate accent — leak** |
| `#d13438` | 68 | Fluent red | ~ | ⚠ third red |
| `#0f172a` | 54 | sidebar navy (slate-900) | ~ | ⚠ literal (nav shell) |
| `#835c00`,`#ffb900`,`#8764b8`,`#605e5c`,`#a19f9d` | 30–80 | Fluent warning/gold/purple/text | Yes (as tokens) | ⚠ literal hex |

**Competing semantic colors:** **2 greens** (success), **≥3 reds** (error), **≥2 blues** (accent) — Fluent set *and* a Tailwind set both in use.

**Status-color consistency:** the **token pages** use `STATES` (fail-closed, one green/amber/red) ✅; the **Tailwind pages** invent their own green/red/amber per component ❌.

**Estimated WCAG AA (key combos):** the token palette is contrast-tuned (`PALETTE`/`DARK_PALETTE` carry A11Y-1.0 fixes: accent `#006ec3` ≈ 4.5:1 on white; textTertiary `#757270` fixed; dark error `#e49699`). The **risk is in the leaks** — e.g., `#a19f9d` (Fluent "disabled" grey) used as body text ≈ ~2.3:1 (fail); sub-11px Tailwind text compounds it. **Formal per-combo contrast measurement is a Part-6 accessibility task**; here we flag the un-tokened greys/tints as the likely failures.

## SPACING

**22 unique px values.** Most-used: 8 (135×), 10 (129×), 6 (73×), 2 (72×), 4 (62×), 7 (25×), 16 (25×), 12 (19×) … up to 60. The **token scale is 4/8/12/16/24/32** — but **10px, 6px, 2px, 7px, 3px, 5px, 9px, 14px, 30px, 40px are off-scale** and heavily used (10px alone 129×). Token pages follow the scale; Tailwind/auth pages don't.

## VISUAL DIALECT MAP (every page classified)

**Dialect 1 — Platform Tokens (best; hardcoded-hex ≈ 0, tokens imported):** ~19 pages
`tefca-registry/{page, entities, entity, verification, issues}` · `tefca-arc/{page(Mission Control), import, qa, cycles, findings, priority, connectors, administration, analytics, reports, reviews, validation, audit}` · `admin/users`.

**Dialect 2 — Tailwind + Hardcoded Hex (dark mode breaks):** ~28 pages
`dashboard` (hex 165 / tw-px 158 — worst) · `case-management` (58/122) · `trust` (60/100) · `compare` (86/79) · `actions-inbox` (122/70) · `documents` (77/61) · `decisions` (82/58) · `intelligence`, `analytics`, `validation`, `healthcare` · and the commercial `ats-agent, bench-sales, deal, deal-tracker, opportunities, projects, rfqs, quotes, manage-*, finance, intel`.

**Dialect 3 — CSS-Class / non-token (auth + some CRM; likely light-only):** ~15–18 pages
`login, register/signup, forgot-password, reset-password, verify-email, auth/callback, contact, settings` · plus className-CSS `.tsx`: `agency-contacts, ai-analyze, staffing, proposal-library, invoices, pricing, bom, company-profile, support`.

## DARK MODE — where it concretely breaks
- **Works:** Dialect 1 pages — colors are `var(--…)` flipped by `data-theme="dark"`; `DARK_PALETTE` is AA-tuned. ✅ (verified on the registry).
- **Breaks:** **Dialect 2** — 165 hardcoded hex on `dashboard` (and 55–122 on case-mgmt/trust/etc.) **do not flip** → light colors on a dark shell. ❌
- **Breaks/absent:** **Dialect 3** — CSS-class auth pages sit outside the theme scope → **light-only**. ❌
- **Net:** dark mode is broken or absent on **~40+ pages** (Dialects 2+3); solid on the ~19 token pages.

## The 70% → 100% gap (quantified)
| To reach 100% | Work |
|---|---|
| **Eliminate ~180 of the 191 hex** → ~12 tokens (fix the 2 greens / 3 reds / 2 blues duplication) | large but mechanical |
| **Kill 688 sub-11px text usages** → token TYPE (11px floor) | a11y + mechanical |
| **Collapse 22 spacing values → 6-scale** | mechanical |
| **Migrate Dialect 2 (~28) + Dialect 3 (~18) pages onto tokens** (fixes their dark mode) | the bulk of the effort |
| **Converge duplicate components** (see `component_inventory.md`) | medium |
| **One font stack + one mono** | small |
| **Add a lint rule banning raw hex/sub-11px in `src/`** | small, prevents regression |
