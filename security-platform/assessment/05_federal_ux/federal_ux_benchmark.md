# Federal & Enterprise UX Benchmark

> Compares DocuAction's **UX patterns** (from source review) against six reference systems. **Not** a copy exercise — a pattern alignment map. Read-only.

## Reference systems
USWDS (federal public-service standard) · Microsoft **Fluent 2 / Azure Portal** · **M365 Admin Center** · **Salesforce Lightning** · **ServiceNow** · **Material Design 3**.

## The core positioning finding
DocuAction is **deliberately Fluent-2 / Azure-Portal-styled** (Segoe UI, `azure-tokens`, the Fluent palette `#107c10/#0078d4/#d13438`, dark left rail, `STATES` status vocabulary). It is an **internal enterprise reviewer tool**, so an **enterprise (Azure/Fluent) idiom is the right choice** — **not** USWDS, which targets *public-facing citizen services*. The alignment strength/weakness therefore differs sharply by reference.

## Pattern-by-pattern alignment

| Pattern | DocuAction | USWDS | Fluent 2 / Azure | M365 Admin | Salesforce | ServiceNow | Material 3 |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|
| **Navigation model** | persistent dark left rail, sectioned, collapsible, `aria-current` | ◐ (USWDS side-nav is light) | ✅ strong | ✅ | ✅ | ✅ | ◐ (rail/drawer) |
| **Breadcrumbs** | present but inconsistent (CommandBar only) | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ |
| **Dashboard (KPIs/cards/charts)** | KPI row + panels + tables + "Awaiting Data" | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Data table (sort/filter/paginate/export)** | hand-rolled DataTable: sort, client-paginate, sticky, zebra, 44px, keyboard, aria-sort. **No export; offset paginate; filters via FilterBar** | ✅ (508 tables) | ✅ (DetailsList) | ✅ | ✅ (has export) | ✅ (heavy filters/saved views) | ✅ |
| **Forms (validation/inline errors/required)** | labels+autocomplete+inline errors+disabled; **no shared field component** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Status/state indicators** | fail-closed `STATES` vocabulary, dot+badge | ◐ (alert styles) | ✅ (Badge/Presence) | ✅ | ✅ | ✅ | ✅ (chips) |
| **Empty states** | `EmptyState` + honest "Awaiting Data" | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Search** | debounced + race-guarded global identifier search | ◐ | ✅ | ✅ | ✅ (global) | ✅ | ✅ |
| **Notifications/toasts** | `Toast` exists but uneven; transient banners | ◐ | ✅ | ✅ | ✅ (toast) | ✅ | ✅ (snackbar) |
| **Settings / administration** | `UsersAdmin` (roles/access) | ◐ | ✅ | ✅ (M365-like) | ✅ | ✅ | ◐ |
| **Onboarding / first-run** | none | ◐ | ◐ | ◐ | ✅ (setup assistant) | ◐ | ◐ |
| **Confirmation before destructive** | **inconsistent/missing** | ✅ | ✅ (Dialog) | ✅ | ✅ | ✅ | ✅ |
| **Accessibility implementation** | platform components strong (focus trap, aria-sort, 44px, role=status); **but sub-11px text + hex-contrast leaks on Dialect-2/3 pages** | ✅ (508-first) | ✅ | ✅ | ✅ | ✅ | ✅ |

✅ aligned · ◐ partial/divergent

## Where DocuAction aligns well (and it's intentional)
- **Fluent 2 / Azure Portal:** the token pages match Azure Portal idioms closely — left rail, KPI cards, DetailsList-style tables, status badges, dark theme. **Intentional and well-executed.**
- **M365 Admin Center:** the user-admin + dashboard pattern is squarely in the M365 admin family.
- **ServiceNow / Salesforce:** the **queue → record-detail → review-action** workflow (TEFCA reviews/priority) mirrors ITSM/CRM record workspaces.
- **Accessibility (component level):** focus traps, `aria-sort`, 44px targets, `role="status"` skeletons are on par with Fluent/USWDS component a11y.

## Where DocuAction diverges (and whether it matters)
| Divergence | Intentional? | Matters? |
|---|---|---|
| **Not USWDS** (no "official government website" banner, no Public Sans, no USWDS components) | **Intentional** — internal enterprise tool, not public citizen service | **Only if** it becomes public-facing or must be a "recognized" federal system → then USWDS becomes mandatory |
| **Hand-rolled components** instead of Fluent UI React (`@fluentui/react-components`) | Mixed (bundle/control reasons) | Medium — bespoke = maintenance + the hex/type leaks; Fluent UI would give a11y/theming for free |
| **No export on tables / offset pagination / no saved views** | Accidental (not yet built) | Medium — Salesforce/ServiceNow-grade list tooling expected by power reviewers |
| **Missing confirmation dialogs / uneven toasts / no onboarding** | Accidental | **High** — safety + feedback gaps below all six references |
| **Sub-11px text + hardcoded-hex contrast on ~46 pages** | Accidental (Tailwind/legacy) | **High** — falls below USWDS/508 and Fluent contrast bars |
| **Auth pages off-system** | Accidental (legacy CSS) | Medium — inconsistent with the enterprise idiom |

## Enterprise-grade vs consumer-grade?
- **Federal/token stack (Registry, TEFCA ARC, Mission Control): enterprise-grade.** Dense, data-first, status-vocabulary, audit trail, honest empty states — appropriate for an ONC reviewer tool and comparable to Azure Portal / ServiceNow.
- **Auth pages + some legacy/commercial pages: generic/consumer-grade** (off-token, tiny text). These pull the overall impression down.

**Verdict:** the app's **chosen idiom (Fluent 2 / Azure enterprise) is correct** for its users and is executed well on the federal stack. The divergences that *matter* are the **accidental** ones — missing confirmations/toasts/export, the accessibility regressions (tiny text, contrast leaks), and the off-system auth/legacy pages — not the (appropriate) decision to be Fluent rather than USWDS.
