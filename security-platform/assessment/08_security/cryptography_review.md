# Cryptography Review

> Manual review of hashing, signing, randomness, TLS, and encryption-at-rest. Read-only.

## Password hashing — GOOD (Info)
`core/security.py:52-56` — **bcrypt** (`bcrypt.hashpw` with per-hash `gensalt()`, `checkpw`). No MD5/SHA1/plaintext for credentials. **CWE-916: satisfied.** (SHA-256 appears only for file checksums and verification-token fingerprints — appropriate uses.)

## JWT signing — GOOD, one caveat (Info / Medium)
- HS256 with `settings.SECRET_KEY` (config floor ≥64 chars, `config.py:97`), algorithm **pinned on decode** (`algorithms=["HS256"]`) → no alg-confusion (CWE-347). See `authentication_review.md`.
- **Caveat (cross-ref AUTH-03):** the **Entra id_token** is decoded **unverified** (`azure_auth_routes.py:182`) — a real CWE-347 gap on that one token (not the app's own JWTs). Medium.
- HS256 (symmetric) is fine for a single-issuer app; if the JWT is ever consumed by a separate service, prefer RS256/asymmetric so the verifier needs only the public key.

## Random number generation — GOOD (Info)
Security-sensitive tokens use the **`secrets`** module: `admin_users.py:123` (`secrets.choice`), `azure_auth_routes.py:23,90` (`secrets.token_urlsafe`), password-reset tokens. The only `random.*` uses are **non-security** (a mock relevance score `bulletin_intelligence/routes.py:769`; seed/QA/review-engine sample data). **CWE-330: not present.**

## TLS verification — GOOD (Info)
- **No `verify=False`, no `ssl.CERT_NONE`, no unverified SSL contexts** anywhere. All outbound httpx/requests calls verify certificates by default.
- **Outbound is HTTPS:** all connectors (`connectors.py:243,350,459,558,638,760`) and the AI endpoint (`ai_engine.py:469`) use `https://`.

## Transport / encryption-in-transit gaps (cross-ref Part 10)
- **No in-app inbound TLS enforcement** — only `TrustedHostMiddleware`; no `HTTPSRedirectMiddleware`/`X-Forwarded-Proto` check (HSTS header *is* set, and App Service enforces `httpsOnly` at the platform, so this is defense-in-depth, not an open hole). **Low.**
- **No explicit DB TLS** — both `create_async_engine` calls (`core/database.py:32`, `app/database.py:6`) pass **no `connect_args`/`ssl`/`sslmode`**. `sslmode=require` is applied via the connection string in deployment (per ops docs), but it is **not enforced in code** — if a connection string omits it, PHI-bearing DB traffic could go cleartext. **Medium (CWE-319).** Fix: pin `ssl` in `connect_args`.

## Encryption at rest — platform-level only (Low)
- No application-level column/field encryption on stored PHI (documents, `fhir_resource`, case-management data). Data-at-rest protection relies entirely on **Azure Postgres/Storage transparent encryption** (platform default) — acceptable for many HIPAA postures but not field-level. **Consider** field-level encryption for the highest-sensitivity columns (MRN, diagnoses) if the threat model requires it. **Low/Info.**

## Verdict
Cryptography is **solid**: bcrypt, pinned-HS256, `secrets`-based randomness, and universal TLS verification. Two real gaps to close: **verify the Entra id_token signature** (Medium) and **pin DB TLS in code** (Medium). Encryption-at-rest is platform-default (acceptable, not field-level). OWASP **A02 (Cryptographic Failures): Medium** — driven by the id_token and DB-TLS items plus the `.env` key exposure in `secrets_review.md`.
