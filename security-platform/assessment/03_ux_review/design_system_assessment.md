# Design System Readiness Assessment

## Is there an implicit design system?
**Yes — a substantial one already exists** (`src/platform/`): a documented token file (`tokens.js`), an app-wide CSS-variable layer (`azure-tokens.css`), and ~25 reusable, accessibility-aware components. It is explicitly Fluent-2 / Azure-Portal-inspired and enforces a doctrine ("module maps status → platform state key; never invents a color"). This is **more mature than most apps at this stage** — it is a design system in all but name/formalization.

## CSS approach
- **Primary:** JS **design tokens** consumed as **inline `style={{}}`** (deliberate — Tailwind color classes are purged in prod, documented in `tokens.js`).
- **Theme delivery:** `azure-tokens.css` CSS variables flipped by `data-theme="dark"` on `<html>` (+ `.dark` alias).
- **Layout:** Tailwind (grid/flex/spacing/breakpoints) only.
- **Divergences:** auth pages use **global CSS classes**; `/dashboard` + older commercial pages use **Tailwind + hardcoded hex**.

## Token counts (approx.)
| Category | Defined tokens | Notes |
|---|---|---|
| Colors | `PALETTE` (12) + `DARK_PALETTE` (12) + `COLORS`(12 vars) + `TINTS`(5) + `STATES`(~16 state pairs) | single source ✅ |
| Typography | `TYPE` (5 roles: headline/sectionTitle/body/label/metadata) + `FONT_FAMILY` (Segoe UI stack) | consistent scale ✅ |
| Spacing | `SPACING` (6: 4/8/12/16/24/32) + `GRID` | consistent ✅ |
| Radius | `RADIUS` (card 12 / button 4 / badge 10) | documented conflict (DPC 4px vs DEDS 12px) resolved ✅ |
| Buttons/Inputs/Table/Badge | `BUTTON_PRIMARY/SECONDARY/DANGER`, `INPUT`, `TABLE`, `BADGE` | ✅ |

**Unique off-token values leaking in:** hardcoded hex in dashboard/admin/auth/GovCon (e.g. `#64748B`, `#0F172A`, auth `login-*` classes). Small in number but they're the dark-mode/consistency breakers.

## Dark mode implementation
- **Token pages:** CSS variables + `data-theme` → **works well and consistently** (verified on the registry).
- **Auth pages:** class-based → **likely light-only** (breaks in dark).
- **Dashboard/commercial:** hardcoded hex → **partial/broken** dark mode.
- WCAG: `DARK_PALETTE` was tuned for AA on dark (comments cite A11Y-1.0 contrast fixes) — a genuine, contrast-aware dark theme, not a naive invert.

## Recommendation: formalize the existing system (don't build new)
A formal Design System is **warranted and mostly already built** — the work is **consolidation + coverage**, not greenfield. A "DocuAction Design System v1" should contain:
1. **Foundations** (already present): color/type/spacing/radius tokens + light/dark + contrast rules.
2. **Components** (already present): promote the module-local duplicates (header, badge, panel, toast, confirm dialog, auth field/card) into `src/platform` so there is exactly one of each.
3. **Coverage mandate:** migrate **auth pages** and **`/dashboard` + legacy hardcoded-hex pages** onto tokens (finish the "Stage 2 hardcoded-hex migration" the code comments already reference).
4. **Docs + lint:** a token-usage lint rule (ban raw hex in `src/`) + a component gallery.
5. **Accessibility baked in** (already the pattern): keep the fail-closed status vocabulary, 44px targets, focus traps, `role="status"` skeletons.

**Readiness: ~70%** — the hard part (tokens + accessible components + dark theme) exists; the remaining 30% is convergence, coverage of the auth/legacy layers, and formal documentation/linting.
