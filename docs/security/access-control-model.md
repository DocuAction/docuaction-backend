# DocuAction AI — Access Control Model (RBAC)

**Product:** DocuAction AI (Version 6.0.0) — Healthcare/TEFCA suite: DocuAction TEFCA ARC
**Owner:** Alliance Global Tech, Inc. ("AGT")
**Document Classification:** Internal — Security Reference
**Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.**
**Security Contact:** security@agtbi.com (48-hour acknowledgment SLA)

---

## 1. Purpose and Scope

This document defines the role-based access control (RBAC) model for DocuAction AI, the least-privilege defaults applied to new accounts, the integration of Microsoft Entra ID Single Sign-On (SSO), and the separation-of-duties structure used within the TEFCA ARC review/QA/program-management workflow. TEFCA role definitions are grounded in federal contract clauses referenced in Section 7.

---

## 2. RBAC Overview

Authorization is enforced by an **8-level role hierarchy**. Each role is assigned a numeric level (1–8); higher levels inherit the read/collaboration capabilities appropriate to their function while adding governance and administrative authority. Authorization decisions are enforced server-side on every protected endpoint; a valid, non-revoked JWT is required, and the caller's role level is evaluated against the resource's minimum required level.

### 2.1 Role Table

| Role | Level | Primary Capabilities |
|---|---|---|
| **viewer** | 1 | Read-only access to permitted content and dashboards. Default level for newly provisioned accounts. No create/edit/delete. |
| **contributor** | 2 | Create and edit own draft content and submissions; cannot approve, finalize, or manage others' work. |
| **manager** | 3 | Manage team content and workflow assignments; coordinate contributors; limited configuration within assigned area. |
| **reviewer** | 4 | Review and provide dispositions on submitted work items; part of TEFCA review separation of duties. |
| **senior_analyst** | 5 | Advanced analytical functions, cross-item analysis, and elevated read across permitted datasets. |
| **qalead** | 6 | Quality-assurance leadership; owns QA gates and validation sign-off; part of TEFCA QA separation of duties. |
| **program_manager** | 7 | Program-level oversight, cross-team coordination, and program acceptance; part of TEFCA PM separation of duties. |
| **admin** | 8 | Full administrative authority: user management, role assignment, area/module access, and platform configuration. |

Area/module-level access is further constrained per user (for example, via an allowed-modules attribute), so that role level and functional area are evaluated together (a user must satisfy both the role-level requirement and be granted the relevant area).

---

## 3. Least-Privilege Defaults

- **Default role on provisioning:** `viewer` (level 1). No user is created with elevated privileges by default.
- **Explicit elevation:** Any privilege above viewer is granted only by an **admin** through user management.
- **Registration gating:** New registrations are subject to **admin approval** and **disposable-email blocking**, so account activation is a deliberate administrative action rather than automatic self-service.
- **Deny-by-default:** Endpoints require an explicit minimum role level and area grant; absence of a grant results in denial.

---

## 4. Microsoft Entra ID SSO Integration

- **First-login provisioning:** When a user authenticates through Entra ID SSO for the first time, the account is provisioned at the **least-privileged** role (`viewer`, level 1). Subsequent elevation is performed by an admin.
- **Unified session model:** Entra SSO issues the same JWT-based session as password login, so downstream RBAC enforcement is identical regardless of the authentication method.
- **Role mapping from Entra roles claim:** Where an Entra ID `roles` claim is present, it may be mapped to DocuAction roles to align enterprise directory groups with Platform role levels. Mappings are administratively governed and default to the least-privileged interpretation when a claim is absent or unrecognized.

---

## 5. Separation of Duties (TEFCA ARC)

To preserve integrity of TEFCA deliverables, the workflow enforces separation among distinct duties:

| Duty | Role(s) | Control Intent |
|---|---|---|
| **Review** | reviewer (4) | Independent review of submitted work items. |
| **Quality Assurance** | qalead (6) | QA gate ownership and validation sign-off, distinct from the reviewer of record. |
| **Program Acceptance** | program_manager (7) | Program-level acceptance, distinct from review and QA. |

A single individual should not simultaneously perform the reviewer, QA-lead, and program-acceptance functions for the same work item. This structure supports the four-eyes principle and reduces the risk of unilateral, unverified deliverable release.

---

## 6. Administrative User Management

Admins (level 8) are responsible for:
- Approving or rejecting new registrations.
- Assigning and adjusting role levels.
- Granting and revoking area/module access.
- Deactivating accounts on offboarding or incident containment.

All administrative actions are recorded through the enterprise audit logging subsystem (see `docs/security/security-overview.md` and `docs/compliance/nist-800-53-mapping.md`, control family AU).

---

## 7. Federal Role Basis (TEFCA)

TEFCA ARC roles and duties are aligned to applicable federal contract requirements, including **HHSAR 352.204-71** (privacy/information-security clauses in HHS acquisitions) and **FAR 52.212-4** (contract terms and conditions for commercial products/services). These references establish the governance basis for the review/QA/program-management separation-of-duties model above.

---

## 8. Document Control

| Field | Value |
|---|---|
| Version | 1.0 |
| Status | Baseline |
| Review cadence | Per release or on role-model change |
| Approver | AGT Security (security@agtbi.com) |
