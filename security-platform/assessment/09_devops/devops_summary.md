# DevOps Review — Summary (Part 9)

> CI/CD, Azure operations, and operational gaps. From `.github/`, `backend/infra/` Bicep, and ops docs. Read-only.

## Headline
**Strong governance and documentation; weak automation and resilience.** The project has real Bicep IaC, comprehensive ops runbooks, and a solid security-scanning CI layer — but **no build/test/deploy pipeline**, **no HA**, and **public data-plane exposure**. The gap is execution/automation, not knowledge.

## CI/CD maturity level: **Level 1–2 of 5 ("Scanned, not automated")**
- **Present:** CodeQL (py+js), Bandit, `pip-audit`, `npm audit`, ESLint, CycloneDX SBOM, Dependabot, Dependency Review — on push + weekly. Strong PR template + CODEOWNERS + issue templates.
- **Absent:** any **build, test, or deploy** workflow. Deploy is **manual** zip/`az` (backend) + SWA CLI (frontend). No tests in CI (`tests/` empty, 1 test file). Security scans are **report-only** (`|| true`) except PR dependency-review. No deployment slot → **no slot-swap rollback**. IaC not applied by any pipeline → **drift risk**. Single CODEOWNER = **segregation-of-duties gap**.

## Azure operations snapshot
| Area | State |
|---|---|
| App Service | P0v3, alwaysOn, httpsOnly, minTLS1.2, health `/health` — **but capacity 1, no autoscale, no zone redundancy** |
| PostgreSQL 16 | **Burstable B1ms, HA Disabled, geo-backup Disabled, 7-day retention, publicly reachable, password-only** |
| Static Web App | Free tier, SWA-CLI deploy (no repo link); **no CSP/HSTS** |
| Key Vault | RBAC + MI + purge protection — **but publicNetworkAccess Enabled; private endpoint authored-not-deployed** `(*)` |
| Monitoring | Log Analytics + App Insights + 4 alerts — **but App Insights not code-instrumented, 3-day HTTP log retention, single-recipient email, no diagnostic settings** |
| Networking | VNet/PE authored **but not deployed**; **no NSGs, no App Service IP restrictions**; **Postgres + KV public** |
| Defender | Standard, **6 plans** `(*)` |
| IaC | Real Bicep — **but not pipeline-enforced (drift)** |

## Operational gaps (top)
- **Resilience:** single App Service instance + Burstable Postgres with HA off + no geo-backup → documented **RTO≤4h / RPO≤15min are aspirational**, not achievable under regional/instance failure.
- **Observability:** App Insights not code-instrumented; missing latency/memory/DB-saturation/cert-expiry alerts; logs stdout with 3-day retention.
- **Exposure:** public Postgres + public Key Vault, no IP restrictions; private endpoints authored but not deployed.
- **Key-person risk:** single CODEOWNER, single alert recipient, single escalation sponsor.
- **Rotation:** no secret-rotation automation; `DATABASE_URL` not vaulted.
- **Strengths to keep:** thorough IR/backup/on-call runbooks, real Bicep, active dependency governance.

## Requested report fields
- **CI/CD maturity level:** **Level 1–2 / 5** — security-scanned, but no build/test/deploy automation.
- **Deploy pipeline:** **manual/click-ops** (zip + `az` / SWA CLI); no CD; no slot rollback.
- **Biggest DevOps risks:** (1) no HA/DR (single instance + Burstable-no-HA Postgres + no geo-backup); (2) no CD/tests-in-CI; (3) public data-plane (Postgres + KV) with no network hardening deployed; (4) observability gaps (App Insights not instrumented, alert coverage thin); (5) key-person/bus-factor of one.

## Top DevOps priorities
1. **Enable Postgres HA + geo-redundant backup; add a second App Service instance/autoscale** (resilience — the #1 gap for a healthcare prod system).
2. **Deploy the authored network hardening** (Postgres + KV private endpoints, App Service IP restrictions).
3. **Add a CD pipeline** (test → build artifact → deploy to a **staging slot** → swap) — fixes no-tests-in-CI, no-artifact-registry, and no-slot-rollback together.
4. **Code-instrument App Insights + expand alerts + add a second alert recipient/channel.**
5. **Implement secret rotation; vault `DATABASE_URL`; run Bicep in a pipeline (drift control).**

## DevOps score: **5.0 / 10**
High-maturity documentation and security-scanning, dragged down by absent CD/tests, no HA/DR, public data-plane exposure, and single-person operations.
