# Technical Debt Prioritization (Section 2O)

Priority = f(Impact, Effort). P1 = do first (high impact, low/med effort). Effort in rough eng-days.

| ID | Debt Item | Impact | Effort | Priority | Module |
|---|---|:--:|:--:|:--:|---|
| TD-01 | **No automated tests** (1 test file in entire repo) | High | High (ongoing) | **P1** (start now) | All |
| TD-02 | **`DATABASE_URL` as direct credential** (not KV/passwordless MI) | High | Low (~0.5d) | **P1** | Infra |
| TD-03 | **Unauthenticated commercial routers** mounted (GovCon/ATS) | High | Low (~1d: gate/unmount) | **P1** | GovCon/ATS |
| TD-04 | **Manual deployment, no pipeline / no gated CI-to-deploy** | High | Med (~3–5d) | **P1** | All |
| TD-05 | **Unpinned deps** (`weasyprint`, `gunicorn`) + supply-chain flags (`xlsx` CDN, `lucide-react ^1.23.0`) | Med | Low (~1d) | **P1** | All |
| TD-06 | **Prod schema via `create_all`, not Alembic** (no migration governance/rollback) | High | Med (~3–5d to adopt Alembic on prod) | **P2** | Backend/Infra |
| TD-07 | **Second Postgres `docuaction-db` (legacy) not decommissioned** | Low | Low (~0.5d, after confirm) | **P1** | Infra |
| TD-08 | **113 models / 51 tables drift; two `Base` classes** | Med | High (separate services or reconcile) | **P2** | Backend |
| TD-09 | **HS256 JWT + in-memory lockout/rate-limit** (single-process) | Med | Med (RS256 + shared store/Redis) | **P2** | Auth |
| TD-10 | **15 FK columns without a leading index** + JSONB without GIN | Med | Low (~1d) | **P2** | DB |
| TD-11 | **Monolithic legacy TEFCA routes (2,852 LOC)** + 786-LOC mock_data | Med | High (refactor) | **P3** | TEFCA ARC |
| TD-12 | **Bulletin in-memory storage** (3,618-LOC engine; volatile state) | Med | Med | **P3** | Bulletin |
| TD-13 | **Dockerfile diverges from prod runtime** (doc/repro gap) | Low | Low | **P2** | Infra |
| TD-14 | **No HA** (App Service capacity 1, PG HA disabled) | High | Med (cost + config) | **P2** (before scale) | Infra |
| TD-15 | **149 raw `text()` SQL usages** to audit for injection | Med | Med (audit) | **P2** | Backend |
| TD-16 | **Unused/odd frontend deps** (`@tanstack/react-table` unused; supply-chain flags) | Low | Low | **P2** | Frontend |
| TD-17 | **No IR runbook / rollback procedure documented** | Med | Low (docs) | **P2** | Ops |

## Debt by category (rough effort)
| Category | Items | Est. effort |
|---|:--:|---|
| Security / auth | TD-02, TD-03, TD-09, TD-15 | ~7–9d |
| DevSecOps / testing | TD-01, TD-04, TD-05 | high/ongoing |
| Infra / cloud | TD-07, TD-13, TD-14 | ~4–6d |
| Data / migrations | TD-06, TD-08, TD-10 | ~7–11d |
| Architecture / refactor | TD-11, TD-12, TD-16 | high |

## Top 5 technical-debt items (for the report)
1. **TD-01 — No automated tests** (highest long-term risk; blocks safe change).
2. **TD-02 — `DATABASE_URL` direct credential** (quick, high-value security fix).
3. **TD-03 — Unauthenticated commercial routers** (quick, closes real attack surface).
4. **TD-04 — Manual deploy / no gated pipeline** (release risk; CI scans not enforced at deploy).
5. **TD-06 — No Alembic on prod / `create_all` schema management** (migration & rollback governance).
