# Component Inventory & Convergence Plan (measured)

## Duplicate implementations found (by `find`)

| Component type | Implementations | Count | Best version | Converge to |
|---|---|:--:|---|---|
| **Header** | `tefca-arc/components/CommandBar.js`, `tefca-registry/components/PageHeader.js` (+ bare markup on auth/dashboard) | **2 (+bare)** | tie — CommandBar richer (breadcrumb+actions) | one `@/platform/PageHeader` |
| **Status badge** | `platform/components/StatusBadge.js`, `tefca-arc/components/StatusPill.js`, `tefca-arc/components/SecurityBadgeBar.js` | **3** | `StatusBadge` (fail-closed, token STATES) | `StatusBadge` |
| **Panel (card)** | `tefca-arc/components/Panel.js`, `tefca-registry/components/Panel.js` | **2** | equivalent | one `@/platform/Panel` |
| **SidePanel (drawer)** | `platform/components/SidePanel.js`, `tefca-arc/components/SidePanel.js` | **2** | `platform/SidePanel` (focus trap, aria-modal) | `platform/SidePanel` |
| **DataTable** | `platform/components/DataTable.js`, `tefca-arc/components/DataTable.js` (adapter) | **2** | `platform/DataTable` | migrate adapter call-sites |
| **KPICard** | `platform/components/KPICard.js`, `tefca-arc/components/KPICard.js`, `tefca-arc/dashboard/widgets/KpiCardsWidget.js` | **3** | `platform/KPICard` | `platform/KPICard` |
| **Skeleton/Loading** | `platform/components/LoadingSkeleton.js`, `tefca-arc/components/LoadingSkeleton.js` | **2** | `platform/LoadingSkeleton` (role=status) | `platform` |
| **API client** | `lib/api.ts`, `tefca-arc/lib/api.js`, `tefca-registry/lib/api.js` | **3** | `tefcaFetch`/`registryFetch` (fail-closed) on `lib/session.ts` | one shared `apiFetch` on `lib/session.ts` |
| **Panel/Toast/Boundary extras** | `tefca-arc/components/{Toast, MockDataBanner, PermissionBoundary, KeyboardShortcuts, EntityContextBar, ThemeProvider, GlobalSearch}` | module-only | good candidates | promote Toast + PermissionBoundary to platform |

## The strong shared core (keep + expand) — `src/platform`
~25 token-styled, accessibility-aware components: `DataTable, StatusBadge, KPICard, SidePanel, EmptyState, LoadingSkeleton, FilterBar, ConnectorStatus, ContextBar, ActionBar, WorkSurface, PlatformLayout, DecisionWorkspace, ConfidenceLedger, RecommendationCard, AnomalyAlert, AuditTimeline, EvidenceDrawer, AccessDenied, NaturalLanguageSearch`. Plus tokens (`tokens.js`) + `azure-tokens.css`.

## Convergence effort estimate
| Action | Effort | Risk |
|---|---|---|
| One `PageHeader` (retire CommandBar/PageHeader split) | 2–3d | low |
| One `StatusBadge` (drop `StatusPill`; keep `SecurityBadgeBar` as composite) | 1–2d | low |
| One `Panel` + use platform `SidePanel` everywhere | 1–2d | low |
| Migrate `tefca-arc` DataTable adapter → platform DataTable | 2–3d | medium (call-site churn) |
| One `KPICard` (retire tefca-arc KPICard + KpiCardsWidget wrapper) | 1–2d | low |
| One `LoadingSkeleton` | 0.5d | low |
| **One API client** (base on `lib/session.ts`; keep the fail-closed 401/403 behavior) | 2d | medium |
| Promote `Toast` + `PermissionBoundary` to platform + adopt app-wide | 2d | low |
| Add `AuthCard`/`Field` token components (bring auth pages in) | 2–3d | low |
| **Total convergence** | **~15–22d** | — |

## Unused / dead component-adjacent items
- `@tanstack/react-table` installed but **unused** (platform DataTable is hand-rolled) → remove dependency.
- `tefca-dashboard` page = redirect stub; legacy `tefca-arc/dashboard` obsolete (Mission Control is canonical).

## Verdict
The shared library is real and good; the debt is **module-local re-implementations of things the platform already has** (headers, badges, panels, tables, KPIs, skeletons, clients — 2–3× each) plus the **auth layer outside the system**. Convergence is ~15–22d of mostly-mechanical work with low behavioral risk.
