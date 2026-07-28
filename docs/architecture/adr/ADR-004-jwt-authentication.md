# ADR-004: JWT-Based Authentication

**Alliance Global Tech, Inc. (AGT)** — Copyright © 2024–2026
**Status:** Accepted
**Deciders:** DocuAction Engineering (@imran-agt)
**Applies to:** DocuAction AI backend (Version 6.0.0)

---

## Context

The DocuAction AI backend is a stateless, horizontally scalable API serving ~261
endpoints under an 8-level RBAC model (viewer through admin) and TEFCA
contract-defined roles. It requires an authentication mechanism that avoids
server-side session affinity, carries authorization context efficiently, and
supports both interactive users and integrations. Credentials and tokens protect
access to PHI/PII/CUI and must meet NIST SP 800-53 and HIPAA expectations.

## Decision

We adopted **JSON Web Tokens (JWT)** with the **HS256** algorithm, issued as an
**access + refresh** token pair:

- **Library:** python-jose 3.4.0 for signing/verification.
- **Password storage:** passlib with bcrypt 4.0.1 for credential hashing.
- **Access token:** short-lived, presented as `Authorization: Bearer <token>`.
- **Refresh token:** longer-lived, exchanged for new access tokens.
- **Claims:** `sub` (subject/user), `role` (RBAC level), `email`, and `exp`.
- **Revocation:** tokens can be invalidated to terminate compromised sessions.
- The same JWT format is issued by password login **and** Entra ID SSO
  (see ADR-005), unifying downstream authorization.

## Consequences

### Positive

- **Stateless & scalable** — no server-side session store required; any worker can
  validate a token.
- **Embedded authorization** — role/email claims drive RBAC without extra lookups.
- **Short access-token lifetime** limits the window of a leaked token; refresh
  tokens preserve usability.
- **Bcrypt hashing** protects stored credentials against offline attacks.
- **Unified model** — identical JWTs from password and SSO simplify the codebase.

### Negative

- **Symmetric key management** — HS256 relies on a shared `SECRET_KEY` that must be
  protected and rotated (sourced from environment / Key Vault, never committed).
- **Revocation complexity** — statelessness requires an explicit revocation
  mechanism to invalidate tokens before natural expiry.
- **Token hygiene** — clients must store and transmit tokens securely.

## Alternatives Considered

- **Server-side sessions** — rejected: introduces session affinity/shared store and
  works against stateless horizontal scaling.
- **RS256 / asymmetric JWT** — reasonable for multi-party verification, but HS256 is
  sufficient and simpler for a single trusted issuer; may be revisited if external
  verifiers are introduced.
- **Opaque tokens with introspection** — rejected: adds a per-request introspection
  round-trip without a compelling benefit for this architecture.
