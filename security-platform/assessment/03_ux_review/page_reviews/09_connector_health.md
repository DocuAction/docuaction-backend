# Page Review 09 — Connector Health (TEFCA ARC)

- **Route:** `/tefca-arc/connectors`
- **Component File:** `src/app/tefca-arc/connectors/page.js`
- **Lines of Code:** 317

## Scores (1–10)
| Dimension | Score |
|---|:--:|
| Layout | 8 | Navigation | 8 | Visual Hierarchy | 8 | Info Density | 7 |
| User Workflow | 7 | Consistency | 8 | Loading States | 7 | Empty States | 6 |
| Error States | 7 | Table Usability | 6 | Form Usability | 6 | Dark Mode | 8 |
| Responsive | 7 | Accessibility | 8 | **OVERALL** | **7.2** |

## Strengths (top 3)
1. **Purpose-built for status** — uses the platform `ConnectorStatus` component with `resolveStatus()` (fail-closed: unknown → "Unable to Verify", never a healthy default). Correct trust semantics for NPPES/LEIE/PECOS/SAM/RCE health.
2. Strong visual hierarchy — status tiles with clear color coding (green/amber/red), distinct from `error`/`unavailable`/`live`.
3. Consistent CommandBar + platform components.

## Improvements Needed (top 3)
1. **Empty state** (0) — if no connector data, show "awaiting probe" rather than blank. *[Priority: Low]*
2. **Historical trend** — connector uptime is stored (`tefca_connector_logs`); surface a trend/sparkline, not just current status. *[Priority: Low]*
3. **Manual re-probe affordance** + timestamp of last check for operator confidence. *[Priority: Low]*
