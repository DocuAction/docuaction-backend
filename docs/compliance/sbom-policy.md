# DocuAction AI — Software Bill of Materials (SBOM) Policy

**Product:** DocuAction AI (Version 6.0.0)
**Owner:** Alliance Global Tech, Inc. ("AGT")
**Document Classification:** Internal — Compliance Reference
**Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.**
**Security Contact:** security@agtbi.com (48-hour acknowledgment SLA)

---

## 1. Purpose and Scope

This policy defines how AGT generates, maintains, distributes, and monitors a Software Bill of Materials (SBOM) for the DocuAction AI backend. An SBOM provides transparency into third-party and open-source components, supporting supply-chain risk management and federal review. Scope covers the backend Python application and its declared dependencies.

This policy references **Executive Order 14028** ("Improving the Nation's Cybersecurity") and the **NTIA minimum elements for an SBOM** as the guiding baseline.

---

## 2. SBOM Formats

AGT produces SBOMs in machine-readable, industry-standard formats:
- **CycloneDX** (primary) — well-suited to vulnerability and dependency analysis.
- **SPDX** — produced additionally where a consumer requires SPDX.

Both formats satisfy the NTIA minimum data-field expectations (component name, supplier, version, unique identifiers, dependency relationships, author of the SBOM, and timestamp).

---

## 3. Tooling

Dependencies are managed via **`requirements.txt`** (pip). SBOM and vulnerability tooling includes:

| Tool | Role |
|---|---|
| **cyclonedx-py** | Generate CycloneDX SBOM from the pip environment / `requirements.txt`. |
| **Syft** | Alternative/cross-check SBOM generation (CycloneDX and SPDX output). |
| **pip-audit** | Audit dependencies against known vulnerability advisories. |
| **Dependabot** | Weekly automated dependency update and vulnerability alerts. |
| **Microsoft Defender for Cloud** | Runtime/posture vulnerability monitoring at the platform layer. |

---

## 4. Generation Cadence

An SBOM is generated:
- **Per release** of the DocuAction backend (release artifact includes the SBOM).
- **On dependency change** (any modification to `requirements.txt` or resolved dependency set).
- **On demand** for federal reviewers or prime-contractor requests.

Each generated SBOM is timestamped and associated with the specific build/commit it describes.

---

## 5. Storage and Distribution

- SBOMs are stored as versioned build artifacts associated with their corresponding release/commit.
- SBOMs are made available to authorized federal reviewers and primes on request through a controlled channel.
- Distribution preserves integrity (the SBOM corresponds exactly to the delivered build).

---

## 6. Vulnerability Monitoring and Response

- **Dependabot (weekly)** surfaces vulnerable and outdated dependencies; **pip-audit** provides on-demand and CI-time checks.
- **Microsoft Defender for Cloud** contributes platform-level vulnerability posture.
- Confirmed vulnerabilities are triaged by severity; remediation targets align with the Vulnerability Disclosure Policy (`docs/security/vulnerability-disclosure-policy.md`) and are handled under the Incident Response Plan where exploitation is suspected.
- Remediation typically means upgrading, patching, or replacing the affected component and regenerating the SBOM.

---

## 7. Consumption by Federal Reviewers

The SBOM enables federal customers and primes to:
- Verify the component inventory and versions of the delivered software.
- Cross-reference components against vulnerability databases.
- Assess supply-chain risk consistent with EO 14028 expectations.

AGT provides the SBOM in CycloneDX (and SPDX on request) to support these reviews.

---

## 8. Alignment References

- **Executive Order 14028** — federal software supply-chain security direction.
- **NTIA Minimum Elements for a Software Bill of Materials** — baseline data fields and practices.
- **CycloneDX / SPDX** — SBOM format standards.

---

## 9. Document Control

| Field | Value |
|---|---|
| Version | 1.0 |
| Status | Baseline |
| Review cadence | Per release or on tooling/process change |
| Approver | AGT Security (security@agtbi.com) |
