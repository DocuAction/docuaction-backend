# Cross-Cutting UX Consistency Analysis

## 1. Consistency gaps

### Old vs new / auth vs app — TWO styling systems
| System | Where | How it styles |
|---|---|---|
| **Platform tokens** (inline styles + `azure-tokens.css` CSS vars + `src/platform/tokens.js`) | tefca-arc, tefca-registry, admin(via UsersAdmin), most module pages | color/type/spacing as JS token objects; dark mode via `data-theme` |
| **Global CSS classes** | **auth pages** (login, register/signup, forgot/reset) | `login-card`, `form-group`, `btn-primary`, `alert-error` — a classic stylesheet |
| **Tailwind + hardcoded hex** | `/dashboard` (814 LOC), older GovCon/ATS pages | some literal hex (`#64748B`, `#0F172A`) not from tokens |

➡️ **Three visual dialects.** The auth screens and some legacy pages don't inherit the token theme → **dark mode breaks** there, and colors drift.

### Duplicate components for the same concept
- **Page header:** `tefca-arc` uses **`CommandBar`**; `tefca-registry` uses **`PageHeader`**; auth pages use bare markup. → *same concept, 3 implementations.*
- **Status pill:** platform **`StatusBadge`** vs tefca-arc **`StatusPill`** (two badge components).
- **DataTable:** platform **`DataTable`** (hand-rolled) is the standard, but a legacy tefca-arc **`components/DataTable.js` adapter** exists for old call sites; `@tanstack/react-table` is installed but unused.
- **API client:** `src/lib/api.ts` (`api()`, `govcon_token`) vs `tefca-arc/lib/api.js` (`tefcaFetch`) vs `tefca-registry/lib/api.js` (`registryFetch`) — three clients with overlapping logic (all re-implement token read + 401/403).
- **Panel/Card:** platform `CARD` token + tefca-arc `Panel` + tefca-registry `Panel` (module-local copies).

### Color / spacing / date
- Colors: mostly tokenized (`COLORS`, `TINTS`, `STATES`) ✅, but hardcoded hex leaks in dashboard/admin/GovCon.
- Spacing: consistent scale where tokens used (`SPACING` 4/8/12/16/24/32) ✅; ad hoc padding in class-styled pages.
- Dates: **one canonical `formatDate` (MM/DD/YYYY)** ✅ — but a **UTC-parse off-by-one** for date-only values affects everywhere.

## 2. Missing / inconsistent patterns
| Pattern | Status |
|---|---|
| **Breadcrumbs** | Present but **inconsistent** — tefca-arc `CommandBar` breadcrumb; registry `PageHeader` breadcrumb string; auth/dashboard none. |
| **Back navigation** | Registry entity detail has a Back button; not standardized elsewhere. |
| **Confirmation dialogs** (destructive) | **Not consistently present** — verify for user disable/delete (admin), cycle runs, sends. *Gap.* |
| **Toast notifications** | **No shared toast** used broadly — registry verification uses a transient text banner; tefca-arc has a `Toast` component but usage is uneven. |
| **Help / tooltips** | tefca-arc has a `/help` page + some inline help; tooltips sparse. |
| **Onboarding / first-run** | None observed. |
| **Loading/Empty/Error** | **Consistently good** on token pages (the strongest cross-cutting pattern). |

## 3. The core consistency verdict
**Within** each module, DocuAction is consistent and polished. **Across** modules it carries **historical strata**: a token-based federal stack (best), a hardcoded/Tailwind commercial stack, and a CSS-class auth layer. The single highest-leverage consistency action is to **bring auth pages and the dashboard onto the platform token system** and to **converge the 3 header + 3 API-client + 2 badge implementations** to one each.
