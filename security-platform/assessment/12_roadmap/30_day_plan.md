# 30-Day Plan

> Grouped by **shared root-cause cluster** — each cluster's fixes move multiple scores together. Read-only recommendations. Effort in engineer-days.

## Cluster A — PHI Protection → Security + Healthcare + Compliance
The epicenter cluster. Completes the containment started in `immediate_actions.md`.

| Item | Action | Effort |
|---|---|---|
| IMP-005 | Full auth + module-gate on all Case Management endpoints (persist the immediate fix properly) | 1d |
| IMP-006 | Expand `mask_pii` to redact **names + street addresses + unlabeled dates**; run before **every** AI egress (both pipelines) | 2–3d |
| IMP-007 | **BAA enforcement** — sign Anthropic BAA + zero-retention; add a code-level "PHI egress allowed" gate + documentation | 1d + process |
| IMP-008 | **Audit-log immutability** — remove delete/update paths (`compliance.py:129-134`, `admin_users.py:433`); anonymize via tombstone rows; enforce append-only | 2–3d |
| IMP-009 | **Pin DB TLS** in `connect_args` on both engines (consolidate to one) | 0.5d |
| IMP-010 | Fix healthcare-claims **IDOR** (ownership check) + move **PHI out of query strings** | 1d |
| IMP-011 | **Log PHI reads** (document + registry GETs: who/what/when) | 2–3d |

**Cluster A effort: ~9–12 days. Scores: Security 6.0→7.5, Healthcare 6.0→7.5, Compliance posture materially improved.**

## Cluster B — Accessibility + Design System → Accessibility + UX + Dark Mode
Convergence, not construction (the system is ~70% built).

| Item | Action | Effort |
|---|---|---|
| IMP-012 | **Page titles** (`metadata`) on all ~72 routes | 0.5–1d |
| IMP-013 | **Shared `Field` component** (label+htmlFor+aria-invalid+describedby+autocomplete); migrate forms | 4–6d |
| IMP-014 | **Hex → token migration** (kill 191→~12; retire duplicate greens/reds/blues) + fixes dark mode on Dialect-2 | 4–6d |
| IMP-015 | **11px type floor** + **lint rule** banning raw hex / sub-11px / off-scale spacing | 3–4d |
| IMP-016 | Keyboard-enable ~7 custom controls + adopt one `PageHeader` (`<h1>`); restore focus indicators | 2–3d |
| IMP-017 | Converge duplicate components (StatusBadge, Panel, DataTable, KPICard) + drop dead `@tanstack/react-table` | 3–4d |

**Cluster B effort: ~17–24 days. Scores: Accessibility 6.4→8.0, UI/UX 7.2→8.0, Dark-mode coverage ~19→all token pages.** (Full WCAG AA + full DS 100% continues into 90-day.)

## Cluster C — Infrastructure → DevOps + Performance
One Redis layer + HA + a pipeline fix three score areas.

| Item | Action | Effort |
|---|---|---|
| IMP-018 | **Redis layer** — cache (hierarchy, dashboard aggregates, reference data) + back distributed lockout/rate-limit + scheduler dedup | 3–5d |
| IMP-019 | **Enable Postgres HA (zone-redundant)** + geo-redundant backup (at cutover) | config + 1d |
| IMP-020 | **Add a second App Service instance / autoscale** (min 2 for HA) | config |
| IMP-021 | **CD pipeline** — test → build artifact → deploy to a **staging slot** → swap; provision the slot | 3–5d |
| IMP-022 | Fix the **hierarchy N+1** (recursive CTE) + the count anti-pattern (`func.count()`) | 2–3d |
| IMP-023 | Deploy the **authored network hardening** (Postgres + KV private endpoints, App Service IP restrictions) | 1–2d |

**Cluster C effort: ~11–17 days. Scores: DevOps 5.0→6.5, Performance 5.5→7.0.**

## 30-day totals
- **Effort:** ~37–53 engineer-days (≈ **6–8 weeks for 1 engineer**, or ~3–4 weeks for a 2-person team).
- **Scores moved:** Security 6.0→7.5 · Healthcare 6.0→7.5 · Accessibility 6.4→8.0 · UX 7.2→8.0 · DevOps 5.0→6.5 · Performance 5.5→7.0.
- **Not yet moved (60/90-day):** Test Coverage (still ~1.4 until Cluster D), full compliance hardening, scale prep.

## Priority order within 30 days
**Cluster A first** (closes the Critical/High + HIPAA blockers) → **Cluster C infra** (HA + pipeline de-risk everything else) → **Cluster B** (parallelizable on the frontend, independent of A/C). If staffed by one engineer, do A→C→B; with two, run B in parallel with A/C.
