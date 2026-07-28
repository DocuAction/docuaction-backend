# Security Maturity Model

> 13-area maturity read across the platform, from Parts 8/9/10. Scale (low→high): **Initial → Developing → Moderate → Good → Mature.** Read-only.

| Area | Maturity | Basis |
|---|:--:|---|
| **Authentication** | **Good** | bcrypt + JWT (HS256 pinned on decode) + refresh rotation + revocation epoch + account lockout + login timing-attack mitigation + Entra SSO. Short of *Mature* on HS256 (symmetric) + in-memory lockout |
| **Authorization** | **Moderate** | Strong RBAC ladder + `allowed_modules` + per-user filtering — **but** the `case_management` unauthenticated PHI router (Critical) + healthcare-claims IDOR pull it down |
| **Input Validation** | **Good** | ORM-parameterized (no SQLi), Pydantic models, list-arg subprocess (no cmd injection), UUID upload paths (no traversal), multi-layer file scanner |
| **Cryptography** | **Good** | bcrypt, pinned HS256, `secrets` randomness, universal TLS verify. Gaps: DB TLS unpinned in code, Entra id_token unverified, no field-level at-rest encryption |
| **Logging & Audit** | **Moderate** | Good write/auth/403 coverage + append-only registry log — **but PHI reads unlogged, canonical `audit_logs` mutable, App Insights not code-instrumented** |
| **Secrets Management** | **Moderate** | Key Vault + Managed Identity for 4 secrets, purge protection — **but live keys in working-tree `.env`, `DATABASE_URL` not vaulted, no rotation policy** |
| **DevSecOps** | **Moderate** | CodeQL + Bandit + pip-audit + npm-audit + SBOM + Dependabot — **but scans report-only (`|| true`), no test gate, no CD** |
| **Infrastructure Security** | **Moderate** | httpsOnly + TLS1.2 + Defender (6 plans) + RBAC Key Vault + MI + purge protection — **but public Postgres + public Key Vault, no App Service IP restrictions, private endpoints authored-not-deployed** |
| **Data Protection** | **Developing** | PHI masking exists on AI egress **but regex-only (misses names)**, **unauthenticated + unmasked** PHI egress on case-management, no read-time role masking, no field encryption |
| **Incident Response** | **Good** | Comprehensive IR plan + runbooks + on-call guide + VDP. Short of *Mature* on single-recipient/single-channel alerting + off-repo rotation schedule |
| **Compliance** | **Moderate** | Strong docs (ATO/SSP/HIPAA mappings), TEFCA/FHIR compliant — **but HIPAA gaps (transmission, audit immutability) + 508 failures (6.4)** |
| **Supply Chain Security** | **Good** | Dependabot + dependency-review (fail-on-high) + CycloneDX SBOM + pip/npm audit + CodeQL. Short of *Mature* on report-only gating + no runtime SCA on deploy |
| **AI Security** | **Developing** | Masking pipeline exists **but** misses names/addresses; **unauthenticated PHI → Anthropic** on case-management; **no BAA gate** in code; no prompt-injection defenses reviewed |

## Maturity distribution
- **Good (5):** Authentication, Input Validation, Cryptography, Incident Response, Supply Chain.
- **Moderate (6):** Authorization, Logging & Audit, Secrets Management, DevSecOps, Infrastructure Security, Compliance.
- **Developing (2):** Data Protection, AI Security.
- **Initial (0), Mature (0).**

## Read
The security *engineering fundamentals* are **Good** — this is a team that knows how to build securely (the Good column is the classic AppSec core). The weaknesses cluster in **two themes**: **(1) data protection / AI security** (the `case_management` PHI egress epicenter drags both to *Developing*), and **(2) operational security governance** (audit immutability, secrets rotation, infra exposure, DevSecOps gating — all *Moderate*, i.e. present-but-not-enforced).

**The fastest maturity gains:** close the `case_management` PHI path (moves **Data Protection** and **AI Security** Developing→Good and **Authorization** Moderate→Good), and make audit append-only + pin DB TLS (moves **Logging & Audit** and **Compliance** up). Two clusters lift five areas.

## Target maturity (post-90-day roadmap)
Authentication → **Mature**; Authorization, Data Protection, AI Security, Logging & Audit → **Good**; Secrets Mgmt, DevSecOps, Infra Security → **Good**; Compliance → **Good**. No area should remain *Developing*.
