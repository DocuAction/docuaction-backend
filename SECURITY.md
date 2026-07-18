# Security Policy

**DocuAction AI** — Enterprise Document, Voice & Healthcare Intelligence Platform
Maintained by **Alliance Global Tech, Inc. ("AGT")**
Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.

Alliance Global Tech, Inc. treats the security of its customers, partners, and the
public with the seriousness expected of a federal contractor. AGT maintains a
CMMI Level 3, ISO 27001, and ISO 9001 certified engineering and quality program,
and aligns its security practices with NIST SP 800-53, the OWASP Top 10 / ASVS,
HIPAA, and Section 508 (WCAG 2.2 AA).

This document describes how to report security vulnerabilities in the DocuAction
backend and what you can expect from us in return.

---

## Supported Versions

Security updates are provided only for supported release lines. Older versions
must be upgraded to a supported line to receive fixes.

| Version   | Supported          | Notes                                        |
| --------- | ------------------ | -------------------------------------------- |
| 6.0.x     | :white_check_mark: | Current release line — actively supported.   |
| < 6.0     | :x:                | Unsupported. Please upgrade to 6.0.x.        |

The current platform version is **6.0.0**.

---

## How to Report a Vulnerability

**Please do not open public GitHub issues, pull requests, or discussions for
security vulnerabilities.** Public disclosure before a fix is available places
users at risk.

Report suspected vulnerabilities privately by email to:

> **security@agtbi.com**

To help us triage and remediate quickly, please include as much of the following
as you can:

- A clear description of the vulnerability and its potential impact.
- The affected component, endpoint, or module (for example, an API route,
  authentication flow, or TEFCA connector).
- The affected version or environment (for example, `api-prod.docuaction.io`).
- Step-by-step reproduction instructions, including any required preconditions.
- Proof-of-concept code, requests, or screenshots where applicable.
- Any relevant logs, stack traces, or observed behavior (with sensitive data
  redacted).
- Your assessment of severity (for example, using CVSS) if available.
- How you would like to be credited, if you wish to be acknowledged.

If you need to transmit sensitive details, request a secure channel in your
initial email and we will coordinate an appropriate method.

---

## Response SLA

AGT is committed to timely, transparent handling of all good-faith reports.

| Stage                      | Target                                                         |
| -------------------------- | ------------------------------------------------------------- |
| **Acknowledgement**        | Within **48 hours** of receipt.                               |
| **Initial triage & severity assessment** | Within **5 business days**.                     |
| **Status updates**         | At least every **7 calendar days** until resolution.          |

Remediation targets, measured from confirmation of a valid vulnerability, are
prioritized by severity:

| Severity      | Remediation Target       |
| ------------- | ------------------------ |
| **Critical**  | 7 calendar days          |
| **High**      | 30 calendar days         |
| **Medium**    | 90 calendar days         |
| **Low**       | Best effort / next release cycle |

These targets are goals, not contractual guarantees, and may be adjusted based on
complexity, active exploitation, or dependencies on upstream third parties. We
will communicate any material deviation from these targets.

---

## Coordinated Disclosure

AGT follows a coordinated (responsible) disclosure model:

- Please give us a reasonable opportunity to investigate and remediate before any
  public disclosure.
- **Do not disclose the vulnerability publicly until a patch has been released**
  and AGT has confirmed that coordinated disclosure may proceed.
- We will work with you to agree on a disclosure timeline and, where appropriate,
  publish an advisory once a fix is available.
- With your permission, we are happy to credit you for your responsible
  disclosure.

---

## Scope

### In Scope

- The DocuAction backend production API: **api-prod.docuaction.io**
- The DocuAction frontend application: **app.docuaction.io**
- The source code contained in this repository, including its authentication,
  authorization (RBAC), API, and TEFCA ARC components.

### Out of Scope

The following are **not** in scope for this program:

- Third-party services, platforms, and dependencies not operated by AGT
  (for example, Microsoft Azure infrastructure, upstream open-source libraries,
  NPPES, PECOS, OIG LEIE, SAM.gov, and other external data sources).
- Social engineering, phishing, or pretexting against AGT staff, customers, or
  contractors.
- Physical attacks against AGT facilities, personnel, or equipment.
- Denial-of-service (DoS/DDoS), volumetric, or resource-exhaustion attacks.
- Automated scanner output or noise submitted without a demonstrated,
  reproducible security impact.
- Reports of missing "best practice" configurations without a demonstrated
  vulnerability (for example, informational TLS or header findings with no impact).

---

## Safe Harbor

AGT supports safe, good-faith security research. If you make a good-faith effort
to comply with this policy during your research, AGT will consider your research
to be authorized, will work with you to understand and resolve the issue quickly,
and will not pursue or support legal action against you related to your research.

To qualify for safe harbor, you must:

- Act in good faith and comply with this policy and all applicable laws.
- Avoid privacy violations, data destruction, service degradation, and
  interruption of AGT's operations.
- Only interact with accounts you own or have explicit permission to access.
- Not access, modify, or retain data that does not belong to you; if you
  encounter sensitive data (including any protected health information), stop,
  do not further access or store it, and report it to us immediately.
- Give AGT a reasonable time to remediate before any disclosure.

This safe harbor applies only to the extent AGT is legally authorized to grant
it. It does not authorize actions against third-party systems or data, and it
does not waive the rights of third parties.

---

## Contact

- Security disclosures: **security@agtbi.com**
- General inquiries: **imran@agtbi.com**

Thank you for helping keep DocuAction and its users safe.
