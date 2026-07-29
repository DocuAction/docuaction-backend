# Federal/Enterprise UX Alignment Recommendations

Documented only. Focus on the **accidental** divergences worth closing; keep the **intentional** Fluent-not-USWDS positioning. IDs `AL-###`.

## Keep (intentional, correct)
- **Stay Fluent 2 / Azure-Portal-styled** for the internal reviewer tool. Do **not** convert to USWDS unless a public-facing or "recognized federal system" requirement appears.
- Keep the **fail-closed `STATES` status vocabulary**, **"Awaiting Data" honesty**, **audit trail**, and **44px targets** — these are best-practice and above baseline.

## Close (accidental gaps) — prioritized

### High — safety, feedback, accessibility (below all six references)
| ID | Recommendation | Reference precedent | Effort |
|---|---|---|---|
| AL-01 | **Confirmation dialogs on every destructive action** (Fluent `Dialog`) | Fluent/USWDS/SF/ServiceNow all require | 2–3d |
| AL-02 | **Consistent toast/notification** for action results (adopt the existing `Toast` app-wide) | Fluent Toast / SF toast | 1–2d |
| AL-03 | **Fix accessibility regressions** — eliminate sub-11px text (688 uses) + hardcoded-hex contrast leaks on ~46 pages | USWDS/508, Fluent contrast | (DS-02/06) |
| AL-04 | **Table export (CSV/XLSX)** + verify filter/paginate parity | SF/ServiceNow list tooling | 2–3d |

### Medium — enterprise polish
| ID | Recommendation | Reference | Effort |
|---|---|---|---|
| AL-05 | **Consistent breadcrumbs** across all modules (one header component) | Azure Portal | (DS-08) |
| AL-06 | **Saved views / persistent filters** for reviewer queues | ServiceNow/SF | 3–4d |
| AL-07 | **Stage/path progression** component for the review-cycle lifecycle (Planned→In Progress→Complete→Report) | Salesforce **Path** | 2–3d |
| AL-08 | **SSO button on login** ("Sign in with Microsoft") | enterprise SSO norm | 0.5d |
| AL-09 | **Command bar consistency** (actions row) across list pages | Fluent CommandBar / M365 | (DS-08) |

### Optional — only if scope changes
| ID | Recommendation | Trigger |
|---|---|---|
| AL-10 | **USWDS alignment** (official-gov banner, Public Sans, USWDS components, full 508) | only if the app becomes **public-facing** or must be a *recognized* federal service |
| AL-11 | **Adopt Fluent UI React** (`@fluentui/react-components`) to replace hand-rolled components | if maintenance of the bespoke library becomes costly — gains theming/a11y for free, at bundle/migration cost |
| AL-12 | **Setup/onboarding assistant** for first-run admins | M365 setup-assistant pattern; if new-tenant onboarding grows |

## Alignment scorecard (DocuAction vs reference intent)
| Reference | Alignment | Note |
|---|:--:|---|
| **Fluent 2 / Azure Portal** | **Strong (8/10)** | intended idiom, well-executed on the token stack; gaps = adoption leaks + missing Dialog/Toast/CommandBar consistency |
| **M365 Admin Center** | Good (7/10) | admin/dashboard family; add bulk actions + command bar |
| **ServiceNow** | Good (7/10) | queue/record-workspace pattern present; add saved views |
| **Salesforce Lightning** | Moderate (6/10) | record-detail present; add Path + utility bar + export |
| **Material Design 3** | Low (intentional) | not the chosen system — no action |
| **USWDS** | Low (intentional) | not applicable to an internal enterprise tool unless scope changes |

## Bottom line
DocuAction's **enterprise idiom is correct and mostly well-executed**. To reach a polished federal-enterprise bar, close the **accidental** gaps — **confirmations, toasts, export, saved views, a Path-style lifecycle, SSO entry, breadcrumb/command-bar consistency** — and fix the **accessibility regressions** (tiny text, contrast leaks). USWDS conversion is **out of scope** unless the product becomes public-facing.
