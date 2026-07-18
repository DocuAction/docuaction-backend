# ADR-005: Microsoft Entra ID Single Sign-On

**Alliance Global Tech, Inc. (AGT)** — Copyright © 2024–2026
**Status:** Accepted
**Deciders:** DocuAction Engineering (@imran-agt)
**Applies to:** DocuAction AI backend (Version 6.0.0)

---

## Context

As DocuAction AI moved to Microsoft Azure (see ADR-001) and expanded into
enterprise and federal healthcare engagements, customers require centralized
identity, single sign-on, and organizational lifecycle controls (provisioning,
deprovisioning, conditional access). The existing password-based login (ADR-004)
remains necessary for certain users and integrations, so the two mechanisms must
coexist and produce a consistent authorization context for the ~261-endpoint API.

## Decision

We added **Microsoft Entra ID SSO** using the **OAuth2 authorization-code flow**
with a **confidential client**, operating **alongside** password login:

- **Initiation:** `GET /api/auth/login/azure` issues a `307` redirect to the
  Microsoft authorization endpoint.
- **Callback:** `GET /api/auth/callback/azure` receives the authorization code and
  performs the confidential-client token exchange.
- **App token:** on success the backend issues its **own application JWT** — the
  same HS256 access/refresh format used by password login (ADR-004) — so all
  downstream RBAC and endpoints treat both login paths identically.
- **Handoff:** the application JWT is delivered to the frontend via URL fragment to
  the `/auth/callback` route.
- **Provisioning:** the first SSO login provisions a local user, linked by email, at
  **least privilege (viewer)**; elevation is a deliberate administrative action.

## Consequences

### Positive

- **Enterprise SSO** — centralized authentication, MFA, and conditional access via
  Entra ID.
- **Least-privilege by default** — auto-provisioned users start as `viewer`,
  reducing over-permissioning risk (NIST SP 800-53 AC-6).
- **Unified authorization** — SSO and password logins yield the same JWT, so no
  divergent authorization paths exist.
- **Lifecycle alignment** — organizational identity governance flows into
  application access.

### Negative

- **Configuration complexity** — client ID/secret, redirect URIs, and tenant
  settings must be managed and kept in sync across environments.
- **External dependency** — authentication availability depends on Entra ID.
- **Secret management** — the confidential-client secret must be protected
  (environment / Key Vault) and rotated.
- **Account linking care** — email-based linking requires verified, unique email
  addresses to prevent account collision.

## Alternatives Considered

- **Password-only authentication** — rejected: does not meet enterprise SSO,
  centralized MFA, or lifecycle-governance requirements.
- **Third-party IdP (Okta, Auth0)** — capable, but Entra ID is native to the chosen
  Azure platform (ADR-001) and included in the customers' Microsoft tenants.
- **SAML federation** — viable, but OAuth2/OIDC authorization-code flow integrates
  more directly with the FastAPI/JWT architecture.
