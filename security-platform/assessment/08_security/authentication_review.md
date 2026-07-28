# Authentication Review

> Manual code review of the live authentication stack (`app/core/security.py`, `app/api/routes.py`, `app/api/auth_endpoints.py`, `app/api/azure_auth_routes.py`, `app/api/password_reset.py`). Read-only. The live ASGI app is `app.main:app`.

## Summary
The **core authentication is strong** — bcrypt, HS256 with the algorithm pinned on decode, short access-token lifetime, refresh rotation, a server-side revocation epoch, account-state enforcement on every request, timing-attack mitigation, and account lockout. The gaps are: **an admin token that lives 24h**, **in-memory (per-process) lockout**, and an **Entra id_token whose signature is not verified**.

## Findings

### AUTH-GOOD — JWT implementation (Info)
`core/security.py:20,58-73,112-116`. `ALGORITHM="HS256"`, secret from `settings.SECRET_KEY` (config enforces ≥64 chars, `config.py:97`). Access 15 min, refresh 7 days, with `jti`/`iat`. **Decode pins `algorithms=[ALGORITHM]`** → no algorithm-confusion (CWE-347 avoided). Multiple modules issue JWTs consistently (routes.py, password_reset.py, azure_auth_routes.py) all via the same secret/alg.

### AUTH-01 — Admin access token TTL = 24h (Low, CWE-613)
`core/security.py:25` `ACCESS_EXPIRE_ADMIN` = 24h for the **highest-privilege** principals. A stolen admin token is valid for a day (mitigated by the revocation epoch on explicit logout/rotation). **Fix:** reduce to ≤1h; rely on refresh. Effort: trivial.

### AUTH-GOOD — Password hashing (Info)
`core/security.py:52-56` bcrypt (`hashpw`/`checkpw`, per-hash salt). No MD5/SHA1/plaintext for passwords anywhere (SHA-256 only for file checksums + verification-token fingerprints). The dead-code `services/auth.py:15` uses passlib bcrypt (only relevant if ever wired).

### AUTH-02 — Account lockout is in-memory / per-process (Low, CWE-307)
`api/routes.py:90-160,347-352`. Per-account lockout (**5 fails / 15 min**) + per-IP throttle (**20 / 15 min**) + signup throttle — a genuine control. **Caveat:** state is an in-process dict, so it **resets on restart and is not shared across workers/instances** (`--workers 4` per the gunicorn startup, or any scale-out, weakens it). **Fix:** back with Redis. Effort: 1–2d.

### AUTH-GOOD — Token refresh, rotation, revocation (Info)
`api/auth_endpoints.py:18-22` → `refresh_access_token` (`core/security.py:178-196`) rotates **both** tokens and re-checks account state. Revocation denylist via `tokens_revoked_at` epoch vs token `iat` (`core/security.py:76-95`), enforced on every request and on logout (`routes.py:391-400`). This is a real hybrid (stateless JWT + server-side kill switch).

### AUTH-GOOD — Login timing-attack / enumeration mitigation (Info)
`api/routes.py:101-102,358` — a pre-computed bcrypt hash equalizes response time when the email doesn't exist (always one bcrypt op), defeating the user-enumeration timing oracle. A mature touch.

### AUTH-03 — Entra id_token signature NOT verified (Medium, CWE-347, OWASP A07)
`api/azure_auth_routes.py:182` uses `jwt.get_unverified_claims(id_token)` — only `tid`/`aud` are checked, and only `if present`. **No JWKS signature validation and no OIDC `nonce` binding** on the id_token; trust rests solely on the TLS channel to Microsoft. The OAuth **state** parameter *is* protected (signed/expiring JWT + nonce, l.86-104) — good — but the id_token itself is consumed unverified. **Fix:** validate the id_token signature against Microsoft's JWKS and enforce `nonce`/`iss`/`exp`. Effort: 1–2d.

### AUTH-GOOD — SSO token handoff via URL fragment (Info)
`azure_auth_routes.py:228-234` returns the app JWT in the URL **fragment** (`#…`), which is not sent to servers/logs/Referer — the correct place for a bearer token in a redirect. Issues the same `create_token_pair` → consistent authorization; first-login role from Entra roles claim, least-privilege default.

## Session management
Stateless JWT (bearer header, no cookies → no cookie-flag surface) + server-side revocation epoch = a sound hybrid. Account state (disabled/revoked) is enforced on **every** request via `_enforce_account_state` (`core/security.py:118-127`).

## NIST 800-53 mapping
IA-2 (identification/auth) ✅, IA-5 (authenticator mgmt — bcrypt) ✅, AC-7 (unsuccessful login attempts — present but in-memory) ◐, AC-12 (session termination — revocation epoch) ✅, IA-8/IA-2(1) (Entra SSO — signature-verification gap) ◐.
