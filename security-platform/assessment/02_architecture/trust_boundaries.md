# Trust Boundaries (Section 2D)

Six boundaries; each becomes a Phase-2 penetration-test scope.

## Boundary 1 — Internet → Azure edge
- **Crosses:** HTTP(S) requests, credentials, file uploads, JWTs.
- **Protection:** TLS 1.2+ enforced, **HTTPS-only**, TrustedHostMiddleware (`ALLOWED_HOSTS`), CORS allowlist (`ALLOWED_ORIGINS`, `allow_credentials=False`), 6 security headers (HSTS/CSP/X-Frame/X-Content-Type/Referrer/Permissions), global rate limiting.
- **Gaps:** **No WAF / Front Door** at the edge; rate-limit is in-memory (per-process).
- **Threats:** DDoS, credential stuffing, injection, header spoofing.

## Boundary 2 — Frontend (SWA) → Backend API
- **Crosses:** JWT bearer, user input, uploads.
- **Protection:** CORS, JWT validation (`decode_token` + account-state enforcement + token-epoch revocation), Pydantic validation, upload scanner.
- **Gaps:** JWT in **localStorage** (XSS-exfiltration risk vs httpOnly cookie); **no CSRF token** (mitigated by bearer-not-cookie auth); **HS256** shared secret.
- **Threats:** XSS→token theft, token replay, injection via inputs/uploads.

## Boundary 3 — Backend → Database
- **Crosses:** SQL, PHI/PII/CUI, secrets.
- **Protection:** SSL to Postgres, SQLAlchemy parameterization, pooling; Azure-managed encryption at rest.
- **Gaps:** **149 raw `text()`** call sites to audit; `DATABASE_URL` is a **direct credential**; DB public/private access to confirm.
- **Threats:** SQL injection (via any unparameterized `text()`), credential exposure, data exfiltration.

## Boundary 4 — Backend → External APIs
- **Crosses:** API keys, NPI/entity data, **document/audio/claim text (potential PHI) to AI**.
- **Protection:** HTTPS, per-integration API-key auth, timeouts + some retries, caching for gov sources.
- **Gaps:** **PHI sent to AI without minimization/BAA-confirmation**; keys as app settings (some KV-backed); no per-call egress audit; **SSRF surface** wherever a URL is user-influenced (e.g., FHIR endpoint fields, ONC Box, connectors) — must be reviewed.
- **Threats:** API-key exposure, data leakage to AI, **SSRF**, third-party compromise.

## Boundary 5 — Backend → Azure platform services
- **Crosses:** secrets (KV), telemetry (App Insights), config.
- **Protection:** **Managed Identity**, **Key Vault private endpoint** + private DNS + VNet, Defender Standard.
- **Gaps:** `DATABASE_URL` not KV-delivered; diagnostic-settings completeness to verify.
- **Threats:** secret compromise, misconfiguration, telemetry tampering.

## Boundary 6 — Management plane (deploy / SCM)
- **Crosses:** application code (Kudu VFS), config, restarts.
- **Protection:** **SCM basic-auth disabled → AAD-only**, Azure RBAC, KV private endpoint. (This assessment used AAD-token Kudu access.)
- **Gaps:** **Manual deployment** (no gated pipeline; CI scans not enforced pre-deploy); no deployment approval/audit trail beyond Azure activity log; App Service SSH reachable via tunnel (auth'd).
- **Threats:** unauthorized/unreviewed deployment, config tampering, supply-chain injection at deploy time.

## Summary
| Boundary | Posture |
|---|---|
| 1 Internet→Azure | **Good** (needs WAF) |
| 2 FE→API | **Good** (JWT-in-localStorage + HS256 caveats) |
| 3 API→DB | **Moderate** (raw SQL audit + credential) |
| 4 API→External | **Moderate/Weak** (PHI-to-AI, SSRF surface) |
| 5 API→Azure | **Strong** (MI + KV private endpoint) |
| 6 Management | **Moderate** (AAD-only SCM, but manual/ungated deploy) |
