# DocuAction AI — Incident Response Plan

**Product:** DocuAction AI (Version 6.0.0)
**Owner:** Alliance Global Tech, Inc. ("AGT")
**Document Classification:** Internal — Security Reference
**Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.**
**Security Contact:** security@agtbi.com (48-hour external acknowledgment SLA)

---

## 1. Purpose and Scope

This plan defines how AGT prepares for, detects, responds to, and recovers from security incidents affecting DocuAction AI and its Azure infrastructure. It covers confidentiality, integrity, and availability incidents, including suspected or confirmed exposure of PHI, PII, or CUI. It aligns with the NIST incident-handling lifecycle and supports HIPAA breach-notification obligations where PHI is involved.

---

## 2. Definitions and Severity Classification

An **incident** is an event that actually or imminently jeopardizes the confidentiality, integrity, or availability of the Platform or the data it processes.

| Severity | Description | Example | Target Response |
|---|---|---|---|
| **SEV-1 — Critical** | Confirmed compromise or exposure of PHI/PII/CUI, or full loss of a production service. | Confirmed unauthorized data access; production outage. | Immediate; incident commander engaged at once. |
| **SEV-2 — High** | Probable compromise, privilege escalation, or significant control failure without confirmed data exposure. | Exploited vulnerability contained pre-exfiltration. | Rapid; same business day. |
| **SEV-3 — Moderate** | Limited-impact security event or degraded control. | Isolated malicious request pattern blocked by rate limiting. | Prioritized within the response queue. |
| **SEV-4 — Low** | Minor policy deviation or informational alert requiring review. | Benign anomalous log entry. | Routine review. |

---

## 3. Roles and Responsibilities

| Role | Responsibility |
|---|---|
| **Incident Commander (IC)** | Owns the response, declares severity, coordinates all phases, authorizes containment. |
| **Security Lead** | Technical analysis, evidence preservation, Defender/audit-log review, remediation direction. |
| **Engineering On-Call** | Executes containment/eradication/recovery actions in the Platform and Azure. |
| **Compliance/Privacy Officer** | Assesses HIPAA breach-notification obligations and regulatory impact. |
| **Communications Lead** | Manages internal and external communications, including the security@agtbi.com channel. |

The reporting/acknowledgment channel for externally reported issues is **security@agtbi.com**, with a **48-hour acknowledgment SLA**.

---

## 4. Incident Response Lifecycle

### 4.1 Preparation
- Maintain this plan, contact roster, and severity matrix.
- Ensure Microsoft Defender for Cloud (Standard tier) alerting, enterprise audit logging, and health monitoring are active.
- Maintain least-privilege access, secrets hygiene (`docs/security/secrets-management.md`), and up-to-date dependency posture (Dependabot weekly).
- Periodic tabletop review of response procedures.

### 4.2 Detection & Analysis
Detection inputs include:
- **Microsoft Defender for Cloud** alerts (App Services, SQL/PostgreSQL, Key Vaults).
- **Enterprise audit logs** (authentication, authorization, admin actions, data access).
- **Health monitoring** signals (availability, error-rate anomalies).
- **Rate-limiting / upload-safety** telemetry indicating abuse.
- External reports received at **security@agtbi.com**.

Analysis establishes scope, affected data classification (PHI/PII/CUI), entry vector, and severity. Request IDs from centralized error handling are used to correlate events across logs.

### 4.3 Containment
- Short-term: revoke affected JWT sessions/tokens, disable or restrict affected accounts, tighten host/CORS allowlists, apply rate-limit or network restrictions.
- Long-term: isolate affected components while preserving evidence; rotate any potentially exposed secrets.

### 4.4 Eradication
- Remove the root cause: patch vulnerable code/dependencies, remove unauthorized access, remediate misconfiguration.
- Validate that the vector is closed before restoration.

### 4.5 Recovery
- Restore services from known-good state, monitor closely for recurrence, and confirm control effectiveness.
- Re-enable accounts/sessions only after verification.

### 4.6 Post-Incident Review
- Conduct a blameless post-incident review documenting timeline, root cause, impact, and corrective actions.
- Track corrective actions to closure and feed lessons learned back into Preparation.

---

## 5. Evidence Preservation

- Preserve relevant audit logs, Defender alerts, application logs, and system state before remediation where feasible.
- Maintain chain-of-custody notes and timestamps for materials that may support regulatory notification or legal review.
- Avoid destructive remediation until evidence is captured, consistent with severity and containment urgency.

---

## 6. Communication Plan

- **Internal:** IC coordinates updates to stakeholders on a cadence set by severity.
- **External reporters:** Acknowledged within **48 hours** via **security@agtbi.com**.
- **Customers/agencies:** Notifications follow contractual and regulatory requirements, coordinated by the Communications and Compliance leads.

---

## 7. HIPAA Breach-Notification Considerations

Where an incident involves protected health information (PHI):
- The Compliance/Privacy Officer performs a breach risk assessment under the HIPAA Breach Notification Rule.
- If a reportable breach is confirmed, required notifications are made **without unreasonable delay and no later than 60 calendar days** from discovery, to affected individuals and, as applicable, to HHS and other required parties.
- Business Associate Agreement (BAA) obligations with Microsoft/Azure and with covered-entity customers are honored, including timely notification up the chain.
- See `docs/compliance/hipaa-safeguards.md` for the safeguards context.

---

## 8. Document Control

| Field | Value |
|---|---|
| Version | 1.0 |
| Status | Baseline |
| Review cadence | Annually and after any SEV-1/SEV-2 incident |
| Approver | AGT Security (security@agtbi.com) |
