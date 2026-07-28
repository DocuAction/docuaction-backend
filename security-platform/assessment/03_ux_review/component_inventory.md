# Component Inventory (shared vs duplicated)

## Shared platform library — `src/platform/components/` (the good core)
Reusable, token-styled, accessibility-aware; imported via `@/platform`:

| Component | Purpose | A11y notes |
|---|---|---|
| `DataTable` | universal table | sort, client-pagination, sticky header, zebra, **44px rows**, keyboard-activatable rows, `aria-sort`, sr-only caption |
| `StatusBadge` | universal status pill | fail-closed (unknown → `indeterminate`), token colors |
| `KPICard` | metric card | accent by token key, keyboard-activatable if clickable |
| `SidePanel` | 480px detail drawer | **focus trap**, Escape, `role="dialog" aria-modal`, scroll-lock, focus restore |
| `EmptyState` | empty/awaiting-data | icon+title+message+action |
| `LoadingSkeleton` (`SkeletonPage/Table/Card`) | loading | `role="status" aria-busy`, sr-only text, reduced-motion aware |
| `FilterBar` + `useFilters` | filtering | |
| `ConnectorStatus` (`resolveStatus`) | connector health | fail-closed status |
| `ContextBar`, `ActionBar`, `WorkSurface`, `PlatformLayout` | layout scaffolding | |
| `DecisionWorkspace`, `ConfidenceLedger`, `RecommendationCard`, `AnomalyAlert`, `AuditTimeline`, `EvidenceDrawer`, `AccessDenied`, `NaturalLanguageSearch` | decision surface | |

**Design tokens:** `src/platform/tokens.js` — `COLORS/TINTS/TYPE/SPACING/RADIUS/CARD/BADGE/BUTTON_*/INPUT/TABLE/STATES` + `src/app/azure-tokens.css` CSS vars. Strong, single source of truth for the token pages.

## Module-local duplicates (should converge)
| Duplicate | Location(s) | Converge to |
|---|---|---|
| `CommandBar` (tefca-arc) vs `PageHeader` (tefca-registry) vs bare headers | per-module `components/` | **one shared `PageHeader/CommandBar`** in `src/platform` |
| `StatusPill` (tefca-arc) vs `StatusBadge` (platform) | 2 badge impls | **`StatusBadge`** |
| `Panel` (tefca-arc) + `Panel` (tefca-registry) | 2 copies | one shared `Panel` |
| `components/DataTable.js` adapter (tefca-arc legacy) | old call sites | migrate to platform `DataTable` |
| API clients: `api.ts` / `tefcaFetch` / `registryFetch` | 3 clients | **one shared zero-trust fetch** (session logic already centralized in `src/lib/session.ts`) |
| `Toast`, `MockDataBanner`, `PermissionBoundary`, `KeyboardShortcuts` (tefca-arc) | module-only | promote useful ones (Toast, PermissionBoundary) to platform |

## Auth-layer components (separate world)
Login/register/forgot/reset use **global CSS classes** (`login-card`, `form-group`, `btn-primary`) — **not** platform components. Should be re-skinned onto tokens (or a shared `AuthCard`/`Field` set).

## Extract-to-shared candidates (priority)
1. **Unified header** (retire CommandBar/PageHeader duplication) — *High*.
2. **Unified fetch client** (retire 3 clients; keep `session.ts` as the base) — *High*.
3. **Shared `Toast` + confirmation `Dialog`** (currently missing/uneven) — *High*.
4. **`AuthCard`/`Field`** token components to bring auth pages into the system — *High*.
5. Single `Panel`, single `StatusBadge` (drop `StatusPill`) — *Medium*.

## Reuse verdict
The **platform library is a real, well-built shared system** (~25 components + tokens) and the newer modules use it faithfully. The debt is **module-local re-implementations of things the platform already has (or should have)** — headers, badges, panels, fetch clients — plus the auth layer sitting entirely outside it.
