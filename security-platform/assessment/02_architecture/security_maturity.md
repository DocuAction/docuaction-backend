# Security Maturity Model (Section 2Q)

Levels: **Mature** > **Good** > **Moderate** > **Developing** > **Initial**. Read-only assessment (no scanning).

| Area | Maturity | Status / evidence |
|---|---|---|
| **Authentication** | **Good** | bcrypt hashing, JWT (access/refresh), token-epoch revocation (`tokens_revoked_at`), Entra ID SSO, account lockout + IP/signup throttling, constant-time login equalizer. *Gaps:* HS256 (symmetric secret) not RS256; lockout/throttle is **in-memory/per-process**. |
| **Authorization** | **Good** | 8-level linear RBAC (`ROLE_HIERARCHY`), router-level gates on TEFCA, fine-grained `require_permission` in migration, account-state enforcement in `require_role`. *Gaps:* **dormant commercial routers unauthenticated** (see findings); RBAC is coarse/linear (no per-resource ownership checks verified). |
| **Input Validation** | **Moderate** | Pydantic v2 schemas on modeled endpoints; upload scanner (magic bytes, dangerous content, CSV/JSON structure). *Gaps:* **149 raw `text()`** usages to review for injection; validation coverage uneven across older modules. |
| **Cryptography** | **Good** | TLS 1.2 min enforced, HTTPS-only, FtpsOnly, bcrypt. *Gaps:* HS256 JWT; DB encryption-at-rest is Azure-managed (adequate) but app-level field encryption for PHI not observed. |
| **Logging & Monitoring** | **Good** | App Insights + Log Analytics + 4 metric alerts + action group + Smart Detection; app audit tables (`audit_logs`, `tefca_reg_audit_log` append-only). *Gaps:* no evidence of centralized security-event/SIEM correlation; audit completeness for PHI access not yet verified (Part 10). |
| **Secrets Management** | **Good** | Managed Identity + Key Vault **with private endpoint**; `SECRET_KEY` etc. as KV references. *Gaps:* **`DATABASE_URL` is a direct credential string** (inline user/pass) rather than a KV ref/passwordless MI-to-Postgres. |
| **DevSecOps Pipeline** | **Developing** | CI has **CodeQL + dependency-review + security-scan** workflows. *Gaps:* **no automated tests** (1 test file total), **no deploy pipeline** (manual Kudu VFS + SWA CLI), **no gated release / no security scan blocking deploy** (deploy is out-of-band from CI). |
| **Infrastructure Security** | **Good/Mature** | HTTPS-only, TLS 1.2, FtpsOnly, remote-debug off, always-on, health check `/health`, KV private endpoint + private DNS + VNet, **Defender for Cloud (Standard) on AppServices/SqlServers/StorageAccounts/KeyVaults/OpenSourceRelationalDatabases/Containers**, SCM basic-auth disabled (AAD only). *Gaps:* single instance (no HA), Postgres public-vs-private access not fully confirmed, HTTP/2 disabled. |
| **Data Protection** | **Moderate** | TLS in transit + Azure at-rest; PII/PHI-bearing tables identified. *Gaps:* no observed **field-level PHI masking by role**, no app-level PHI encryption; PHI-in-logs/error-messages not yet verified (Part 8/10). |
| **Incident Response** | **Developing** | Alerts + action group exist. *Gaps:* no documented IR runbook, on-call, or tamper-evident audit export observed; rollback is manual. |
| **Compliance** | **Developing** | Extensive compliance documentation exists (SSP/ATO docs, TEFCA reports); code comments cite NIST 800-53 controls. *Gaps:* controls are **documented more than enforced/tested**; HIPAA technical-safeguard verification pending (Part 10). |
| **Supply Chain Security** | **Moderate** | dependency-review in CI, lockfile on frontend. *Gaps:* **unpinned** `weasyprint`/`gunicorn`, `xlsx` from a **CDN tarball**, suspicious `lucide-react ^1.23.0`, no reproducible transitive pin for backend (`pydeps` build-time resolution). |

## Overall security maturity: **GOOD (leaning Moderate on process/DevSecOps)**

The **infrastructure and authentication** posture is genuinely strong (private KV endpoint, Defender Standard, MI, TLS enforcement, audit tables). The **process** side (automated testing, deploy pipeline, IR runbooks) and a few **hardening items** (JWT algorithm, DB credential, dormant unauthenticated routers, PHI masking) are where maturity is lowest and where Phase 1+ should focus.

### Top maturity-lifting actions (documented only)
1. Gate/unmount the unauthenticated commercial routers.
2. Move `DATABASE_URL` to a KV ref or passwordless MI→Postgres.
3. Add a deploy pipeline that runs the existing CI scans **as a gate**, plus a smoke-test suite.
4. Pin `weasyprint`/`gunicorn`; resolve `xlsx`/`lucide-react` supply-chain flags.
5. Verify PHI masking-by-role + PHI-not-in-logs (feeds Part 8/10).
