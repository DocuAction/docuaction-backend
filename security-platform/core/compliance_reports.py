"""PHASE 4 - framework matrices and evidence packages.

Built entirely from findings already in the database. No new scanning, no code or
cloud changes.

THE HONESTY RULE THAT GOVERNS EVERY MATRIX
    A control with no automated finding is reported as **NOT ASSESSED**, never as
    "compliant". Static and dynamic analysis can demonstrate that a control is
    BROKEN; it can almost never demonstrate that a control is SATISFIED. Most of
    HIPAA's Administrative safeguards, all of the Physical safeguards, and whole NIST
    families (AT, PS, PE, MA, MP) cannot be evidenced by a scanner at all.

    Anything that says "compliant" in these documents is therefore backed by a
    specific passing test or observed configuration, and everything else is labelled
    as requiring manual assessment. An evidence package that overstates coverage is
    worse than no package, because someone will rely on it.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


HEADER_NOTE = (
    "> **How to read this.** A control is marked **GAP** only where a finding "
    "demonstrates a deficiency, and **EVIDENCED** only where a passing test or an "
    "observed configuration supports it. Everything else is **NOT ASSESSED** - an "
    "automated scan that finds nothing is not evidence that a control is satisfied. "
    "This document supports an assessment; it is not a certification.\n")

# ── HIPAA ─────────────────────────────────────────────────────────────────────

HIPAA_TECH = [
    ("164.312(a)(1)", "Access Control", "Required",
     "Unique user identification, emergency access, automatic logoff, encryption"),
    ("164.312(a)(2)(i)", "Unique User Identification", "Required",
     "Assign a unique name/number for tracking user identity"),
    ("164.312(a)(2)(ii)", "Emergency Access Procedure", "Required",
     "Obtain ePHI during an emergency"),
    ("164.312(a)(2)(iii)", "Automatic Logoff", "Addressable",
     "Terminate a session after a predetermined time of inactivity"),
    ("164.312(a)(2)(iv)", "Encryption and Decryption", "Addressable",
     "Encrypt and decrypt ePHI"),
    ("164.312(b)", "Audit Controls", "Required",
     "Record and examine activity in systems containing ePHI"),
    ("164.312(c)(1)", "Integrity", "Required",
     "Protect ePHI from improper alteration or destruction"),
    ("164.312(c)(2)", "Mechanism to Authenticate ePHI", "Addressable",
     "Corroborate that ePHI has not been altered or destroyed"),
    ("164.312(d)", "Person or Entity Authentication", "Required",
     "Verify that a person seeking access is the one claimed"),
    ("164.312(e)(1)", "Transmission Security", "Required",
     "Guard against unauthorised access to ePHI transmitted over a network"),
    ("164.312(e)(2)(i)", "Integrity Controls", "Addressable",
     "Ensure transmitted ePHI is not improperly modified"),
    ("164.312(e)(2)(ii)", "Encryption", "Addressable",
     "Encrypt ePHI whenever deemed appropriate"),
]

HIPAA_ADMIN_PHYS = [
    ("164.308(a)(1)", "Security Management Process", "Required",
     "PARTIAL - this platform IS the risk-analysis capability (§164.308(a)(1)(ii)(A)). "
     "Risk management, sanction policy and information-system activity review remain "
     "organisational."),
    ("164.308(a)(2)", "Assigned Security Responsibility", "Required",
     "NOT ASSESSABLE by scanner - name a Security Official."),
    ("164.308(a)(3)", "Workforce Security", "Required",
     "PARTIAL - RBAC roles exist in code; authorisation/clearance/termination "
     "procedures are organisational."),
    ("164.308(a)(4)", "Information Access Management", "Required",
     "PARTIAL - role-based access is implemented and tested (AUTHZ suite); the access "
     "authorisation POLICY is organisational."),
    ("164.308(a)(5)", "Security Awareness and Training", "Addressable",
     "GAP - no evidence. Training records are organisational and none were provided."),
    ("164.308(a)(6)", "Security Incident Procedures", "Required",
     "PARTIAL - Azure alerts + action group exist (AZ-MON-004/005); a documented "
     "incident response plan was not provided."),
    ("164.308(a)(7)", "Contingency Plan", "Required",
     "PARTIAL - database backups exist with retention; geo-redundancy is disabled on "
     "one prod server and there is no tested DR plan or HA."),
    ("164.308(b)(1)", "Business Associate Contracts", "Required",
     "**GAP - BLOCKING.** Clinical narrative is transmitted to Anthropic. Phase 0 "
     "DP-02 established this is closable only by a signed BAA plus zero-retention. No "
     "BAA evidence exists. This is the single largest HIPAA exposure and no code "
     "change resolves it."),
    ("164.310", "Physical Safeguards", "Required",
     "INHERITED - workloads run in Azure; facility, workstation and device controls "
     "inherit from Microsoft's FedRAMP/HITRUST attestations. Obtain the current Azure "
     "SOC 2 / HITRUST report as evidence."),
    ("164.316(b)(2)", "Documentation Retention (6 years)", "Required",
     "PARTIAL - audit rows are retained and pseudonymised rather than deleted "
     "(Sprint 1 AUDIT-MUT). App Insights telemetry retention is far shorter, but "
     "telemetry is not the audit record."),
]

# ── NIST 800-53 families ──────────────────────────────────────────────────────

NIST_FAMILIES = {
    "AC": "Access Control", "AT": "Awareness and Training",
    "AU": "Audit and Accountability", "CA": "Assessment, Authorization, Monitoring",
    "CM": "Configuration Management", "CP": "Contingency Planning",
    "IA": "Identification and Authentication", "IR": "Incident Response",
    "MA": "Maintenance", "MP": "Media Protection", "PE": "Physical and Environmental",
    "PL": "Planning", "PM": "Program Management", "PS": "Personnel Security",
    "RA": "Risk Assessment", "SA": "System and Services Acquisition",
    "SC": "System and Communications Protection",
    "SI": "System and Information Integrity", "SR": "Supply Chain Risk Management",
}

SCANNER_BLIND = {"AT", "PS", "PE", "MA", "MP", "PL", "PM"}

# ── NIST 800-171 ──────────────────────────────────────────────────────────────

NIST_171 = [
    ("3.1", "Access Control", ["AC"]),
    ("3.2", "Awareness and Training", ["AT"]),
    ("3.3", "Audit and Accountability", ["AU"]),
    ("3.4", "Configuration Management", ["CM"]),
    ("3.5", "Identification and Authentication", ["IA"]),
    ("3.6", "Incident Response", ["IR"]),
    ("3.7", "Maintenance", ["MA"]),
    ("3.8", "Media Protection", ["MP"]),
    ("3.9", "Personnel Security", ["PS"]),
    ("3.10", "Physical Protection", ["PE"]),
    ("3.11", "Risk Assessment", ["RA"]),
    ("3.12", "Security Assessment", ["CA"]),
    ("3.13", "System and Communications Protection", ["SC"]),
    ("3.14", "System and Information Integrity", ["SI"]),
]

# ── ASVS ──────────────────────────────────────────────────────────────────────

ASVS_CHAPTERS = [
    ("V1", "Architecture, Design and Threat Modeling"),
    ("V2", "Authentication"), ("V3", "Session Management"),
    ("V4", "Access Control"), ("V5", "Validation, Sanitization and Encoding"),
    ("V6", "Stored Cryptography"), ("V7", "Error Handling and Logging"),
    ("V8", "Data Protection"), ("V9", "Communication"),
    ("V10", "Malicious Code"), ("V11", "Business Logic"),
    ("V12", "Files and Resources"), ("V13", "API and Web Service"),
    ("V14", "Configuration"),
]

# ── TEFCA ─────────────────────────────────────────────────────────────────────

TEFCA_ROWS = [
    ("CA-1", "Identity proofing", "NOT ASSESSED",
     "No IAL2 identity-proofing flow exists in the application; signup is "
     "self-service email/password.", "Organisational + engineering"),
    ("CA-2", "Authentication", "EVIDENCED (partial)",
     "JWT with pinned HS256, bcrypt password hashing, Entra SSO route. All 13 JWT "
     "forgery attacks rejected (Phase 2A).", "No MFA on the local password path."),
    ("CA-3", "Authorization", "GAP",
     "RBAC exists, but Phase 1 found 72 endpoints with no auth dependency and Phase 2 "
     "found no allowed-transition map (TEFCA-WF-002).", "Sprint 2"),
    ("CA-4", "Audit logging", "EVIDENCED (partial)",
     "audit_logs table with actor/action/timestamp; pseudonymisation on delete. No "
     "hash chain (TEFCA-AUD-003b); pgaudit off at the database tier.", "Sprint 2"),
    ("CA-5", "Encryption", "EVIDENCED",
     "TLS 1.2 floor and HTTPS-only on both App Services; Key Vault for secrets; "
     "database TLS required.", "DB public network access still enabled."),
    ("CA-6", "Breach notification", "NOT ASSESSED",
     "Procedural control; Azure alerting exists but no documented notification "
     "workflow was provided.", "Organisational"),
    ("CA-7", "Minimum necessary", "GAP",
     "Phase 0 DP-05: no role-based PHI masking on read responses; full clinical "
     "narrative is sent to the AI provider.", "Sprint 2 + BAA"),
    ("IG-1", "FHIR R4 compliance", "PARTIAL",
     "FHIR import exists (fhir_import.py) and canonical NPI system URIs are used. "
     "Resource-level validation not exercised - registry not deployed to a test "
     "target.", "Deploy registry to dev to test"),
    ("IG-2", "Organization hierarchy (QHIN/Participant/Sub)", "PARTIAL",
     "A parent reference is modelled, but hierarchy tier rules and circular-reference "
     "prevention are untested (TEFCA-ENT-008/009).", "Deploy registry to dev"),
    ("IG-3", "Mandatory identifiers (TEFCAID + HCID)", "GAP",
     "FHIR-ID-006: identifiers are not enforced non-nullable in the schema.", "Sprint 2"),
    ("IG-4", "NPI handling", "GAP",
     "FHIR-ID-002: no check-digit validation anywhere; 6 of 8 bundled sample NPIs are "
     "themselves invalid (FHIR-ID-002b).", "Sprint 2"),
    ("IG-5", "Directory participation", "NOT ASSESSED",
     "Requires a live registry and RCE connectivity.", "Deploy + RCE key"),
    ("IG-6", "Endpoint management", "NOT ASSESSED",
     "tefca_entity_endpoints table exists; behaviour untested.", "Deploy registry"),
    ("SP-1", "Purpose limitation", "NOT ASSESSED", "Policy control.", "Organisational"),
    ("SP-2", "Data minimization", "GAP",
     "Full clinical narrative egresses to the AI provider (DP-02).", "BAA + redesign"),
    ("SP-3", "Individual access", "NOT ASSESSED", "Policy/feature.", "Organisational"),
    ("SP-4", "Correction", "NOT ASSESSED", "Policy/feature.", "Organisational"),
    ("SP-5", "Disclosure limitation", "PARTIAL",
     "Direct identifiers are stripped at the AI egress chokepoint (Sprint 1 DP-02); "
     "narrative is not.", "BAA"),
    ("SP-6", "Safeguards", "EVIDENCED (partial)",
     "309 findings across SAST/DAST/infra; TLS, Key Vault, MI, RBAC in place.",
     "See remediation roadmap"),
    ("SP-7", "Accountability", "EVIDENCED (partial)",
     "Audit trail + this assessment programme.", "Hash chain outstanding"),
]


class ComplianceReporter:
    def __init__(self, root: Path, findings: List[Any], scan: Any, project: Any):
        self.root = Path(root)
        self.findings = [f for f in findings if not getattr(f, "suppressed", False)]
        self.scan = scan
        self.project = project
        self.out = self.root / "compliance"
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "evidence").mkdir(parents=True, exist_ok=True)

    # ── helpers ──────────────────────────────────────────────────────────────

    def by_ref(self, attr: str) -> Dict[str, List[Any]]:
        b: Dict[str, List[Any]] = defaultdict(list)
        for f in self.findings:
            for r in getattr(f.compliance, attr, []) or []:
                b[str(r).strip().lstrip("§")].append(f)
        return b

    @staticmethod
    def _sev_line(items: List[Any]) -> str:
        c = Counter(f.severity.value for f in items)
        return " ".join(f"{k[0].upper()}{v}" for k, v in
                        sorted(c.items(), key=lambda kv: SEV_ORDER.get(kv[0], 9))) or "-"

    @staticmethod
    def _worst(items: List[Any]) -> str:
        if not items:
            return ""
        return min((f.severity.value for f in items), key=lambda s: SEV_ORDER.get(s, 9))

    def _hdr(self, title: str) -> List[str]:
        return [f"# {title}", "",
                f"**Project:** {getattr(self.project, 'display_name', 'DocuAction')}  ",
                f"**Scan:** `{getattr(self.scan, 'scan_id', 'n/a')}`  ",
                f"**Findings analysed:** {len(self.findings)}  ",
                f"**Generated:** {_now()}", "", HEADER_NOTE, ""]

    # ── 4A HIPAA ─────────────────────────────────────────────────────────────

    def hipaa(self) -> Tuple[str, Dict[str, Any]]:
        hb = self.by_ref("hipaa")
        L = self._hdr("HIPAA Security Rule - Control Matrix")
        L += ["## §164.312 Technical Safeguards", "",
              "| Section | Requirement | Type | Status | Findings | Evidence / Gap |",
              "|---|---|---|---|---|---|"]
        evidenced = gaps = 0
        for sec, name, typ, desc in HIPAA_TECH:
            items = []
            for k, v in hb.items():
                if k.startswith(sec) or sec.startswith(k):
                    items.extend(v)
            items = list({id(f): f for f in items}.values())
            if items:
                status = "**GAP**"
                gaps += 1
                ev = f"{len(items)} finding(s): {self._sev_line(items)}"
            else:
                status = "NOT ASSESSED"
                ev = "No automated finding maps here - manual assessment required"
            L.append(f"| {sec} | {name} | {typ} | {status} | {len(items)} | {ev} |")
        L += ["", "### Evidence detail by safeguard", ""]
        for sec, name, _t, _d in HIPAA_TECH:
            items = list({id(f): f for k, v in hb.items() if k.startswith(sec)
                          for f in v}.values())
            if not items:
                continue
            L += [f"**§{sec} {name}** - {len(items)} finding(s)", ""]
            for f in sorted(items, key=lambda x: SEV_ORDER.get(x.severity.value, 9))[:8]:
                L.append(f"- `{f.rule_id}` [{f.severity.value}] {f.title[:90]} "
                         f"({f.location})")
            L.append("")
        L += ["## §164.308 Administrative and §164.310 Physical Safeguards", "",
              "These are largely NOT machine-verifiable. Recorded so the gaps are "
              "explicit rather than absent.", "",
              "| Section | Safeguard | Type | Assessment |", "|---|---|---|---|"]
        for sec, name, typ, note in HIPAA_ADMIN_PHYS:
            L.append(f"| {sec} | {name} | {typ} | {note} |")
        L += ["", "## Summary", "",
              f"- Technical safeguards with findings (**GAP**): **{gaps} of "
              f"{len(HIPAA_TECH)}**",
              f"- Technical safeguards with no automated finding (NOT ASSESSED): "
              f"{len(HIPAA_TECH) - gaps}",
              "- **Blocking organisational gap: no Business Associate Agreement "
              "evidence for the AI subprocessor (§164.308(b)(1)).**", ""]
        path = self.out / "hipaa_security_rule_matrix.md"
        path.write_text("\n".join(L), encoding="utf-8")
        return str(path), {"gaps": gaps, "total": len(HIPAA_TECH)}

    # ── 4B TEFCA ─────────────────────────────────────────────────────────────

    def tefca(self) -> str:
        L = self._hdr("TEFCA Compliance Matrix")
        L += ["Mapped against Common Agreement obligations, RCE Implementation Guide "
              "v1.14.0, and the Security & Privacy Principles.", "",
              "| Ref | Requirement | Status | Evidence | Owner / next step |",
              "|---|---|---|---|---|"]
        for ref, req, status, ev, owner in TEFCA_ROWS:
            L.append(f"| {ref} | {req} | **{status}** | {ev} | {owner} |")
        counts = Counter(r[2].split()[0] for r in TEFCA_ROWS)
        L += ["", "## Summary", ""]
        for k, v in counts.most_common():
            L.append(f"- {k}: {v}")
        L += ["",
              "**Principal constraint:** the TEFCA registry is not deployed to any "
              "test environment, so roughly half of the Implementation Guide rows "
              "cannot be exercised. They are marked NOT ASSESSED rather than assumed "
              "compliant.", ""]
        path = self.out / "tefca_compliance_matrix.md"
        path.write_text("\n".join(L), encoding="utf-8")
        return str(path)

    # ── 4C NIST 800-53 ───────────────────────────────────────────────────────

    def nist_800_53(self) -> Tuple[str, Dict[str, Any]]:
        nb = self.by_ref("nist_800_53")
        fam_findings: Dict[str, List[Any]] = defaultdict(list)
        for ctrl, items in nb.items():
            fam_findings[ctrl.split("-")[0]].extend(items)

        L = self._hdr("NIST SP 800-53 Rev. 5 - Control Matrix")
        L += ["## Coverage by control family", "",
              "| Family | Name | Controls with findings | Findings | Severity | Status |",
              "|---|---|--:|--:|---|---|"]
        assessed = 0
        for fam, name in NIST_FAMILIES.items():
            ctrls = sorted({c for c in nb if c.startswith(fam + "-")})
            items = list({id(f): f for f in fam_findings.get(fam, [])}.values())
            if items:
                assessed += 1
                status = "**GAP**"
            elif fam in SCANNER_BLIND:
                status = "NOT ASSESSABLE by scanner"
            else:
                status = "NOT ASSESSED"
            L.append(f"| {fam} | {name} | {len(ctrls)} | {len(items)} | "
                     f"{self._sev_line(items)} | {status} |")

        L += ["", "## Controls with findings", "",
              "| Control | Findings | Severity | Example |", "|---|--:|---|---|"]
        for ctrl in sorted(nb):
            items = list({id(f): f for f in nb[ctrl]}.values())
            ex = sorted(items, key=lambda x: SEV_ORDER.get(x.severity.value, 9))[0]
            L.append(f"| **{ctrl}** | {len(items)} | {self._sev_line(items)} | "
                     f"`{ex.rule_id}` {ex.title[:60]} |")

        L += ["", "## Families a scanner cannot assess", "",
              "These require documentary or procedural evidence and no automated tool "
              "can substitute for it:", ""]
        for fam in sorted(SCANNER_BLIND):
            L.append(f"- **{fam}** {NIST_FAMILIES[fam]}")
        L += ["", f"**{assessed} of {len(NIST_FAMILIES)} families have automated "
                  f"findings.** Families with none are NOT ASSESSED, not compliant.", ""]
        path = self.out / "nist_800_53_matrix.md"
        path.write_text("\n".join(L), encoding="utf-8")
        return str(path), {"families_with_findings": assessed,
                           "families_total": len(NIST_FAMILIES),
                           "controls": len(nb)}

    # ── 4D NIST 800-171 ──────────────────────────────────────────────────────

    def nist_800_171(self) -> str:
        nb = self.by_ref("nist_800_53")
        fam: Dict[str, List[Any]] = defaultdict(list)
        for ctrl, items in nb.items():
            fam[ctrl.split("-")[0]].extend(items)

        L = self._hdr("NIST SP 800-171 Rev. 3 - CUI Control Family Mapping")
        L += ["800-171 requirements are derived from the 800-53 moderate baseline, so "
              "findings are mapped through their 800-53 control families.", "",
              "| Req | Family | Mapped 800-53 | Findings | Severity | Status |",
              "|---|---|---|--:|---|---|"]
        for ref, name, fams in NIST_171:
            items = list({id(f): f for x in fams for f in fam.get(x, [])}.values())
            if items:
                status = "**GAP**"
            elif any(x in SCANNER_BLIND for x in fams):
                status = "NOT ASSESSABLE by scanner"
            else:
                status = "NOT ASSESSED"
            L.append(f"| {ref} | {name} | {', '.join(fams)} | {len(items)} | "
                     f"{self._sev_line(items)} | {status} |")
        L += ["", "**Scope note.** DocuAction handles ePHI. Whether it also handles "
                  "CUI depends on the contract vehicle; this matrix is provided for "
                  "readiness, not as an assertion that CUI is in scope.", ""]
        path = self.out / "nist_800_171_matrix.md"
        path.write_text("\n".join(L), encoding="utf-8")
        return str(path)

    # ── 4E ASVS ──────────────────────────────────────────────────────────────

    def asvs(self) -> str:
        ab = self.by_ref("owasp_asvs")
        chap: Dict[str, List[Any]] = defaultdict(list)
        for req, items in ab.items():
            chap[req.split(".")[0].upper()].extend(items)

        L = self._hdr("OWASP ASVS v4.0 - Verification Matrix")
        L += ["| Chapter | Area | Requirements referenced | Findings | Severity | Status |",
              "|---|---|--:|--:|---|---|"]
        met = 0
        for cid, name in ASVS_CHAPTERS:
            reqs = sorted({r for r in ab if r.upper().startswith(cid + ".")})
            items = list({id(f): f for f in chap.get(cid, [])}.values())
            if items:
                status = "**NOT MET** (findings present)"
            elif reqs:
                status = "PARTIALLY ASSESSED"
                met += 1
            else:
                status = "NOT ASSESSED"
            L.append(f"| {cid} | {name} | {len(reqs)} | {len(items)} | "
                     f"{self._sev_line(items)} | {status} |")
        L += ["", "## Requirements referenced by findings", "",
              "| ASVS requirement | Findings | Example |", "|---|--:|---|"]
        for req in sorted(ab):
            items = list({id(f): f for f in ab[req]}.values())
            ex = sorted(items, key=lambda x: SEV_ORDER.get(x.severity.value, 9))[0]
            L.append(f"| {req} | {len(items)} | `{ex.rule_id}` {ex.title[:60]} |")
        L += ["", "**ASVS level.** No target level (L1/L2/L3) has been agreed. For a "
                  "healthcare application processing ePHI, **L2** is the normal "
                  "baseline; this matrix reports what the current ruleset touches, not "
                  "conformance to a level.", ""]
        path = self.out / "owasp_asvs_matrix.md"
        path.write_text("\n".join(L), encoding="utf-8")
        return str(path)

    # ── 4F PHI data flow ─────────────────────────────────────────────────────

    def phi_flow(self) -> str:
        phi = [f for f in self.findings
               if f.compliance.hipaa or "PHI" in (f.title or "").upper()
               or "phi" in (f.rule_id or "").lower()]
        logs = [f for f in self.findings if "532" in (f.compliance.cwe or [])
                or "log" in (f.title or "").lower()]
        L = self._hdr("PHI / PII Data Flow Report")
        L += [
            "## Where PHI enters", "",
            "| Entry point | Control | Status |", "|---|---|---|",
            "| `POST /api/v1/case-management/*` | Router-level auth (Sprint 1 AUTHZ-01) "
            "| **Verified 403 anonymously on dev** |",
            "| FHIR Bundle / CSV import (`tefca_registry`) | reviewer+ RBAC | Not "
            "testable - registry not deployed |",
            "| Document upload | magic-byte + macro/PE scan | Heuristic only (Phase 0 "
            "FU-02) |", "",
            "## Where PHI is stored", "",
            "- PostgreSQL flexible servers (`docuaction-db`, `-geo`). Encrypted at rest "
            "by Azure platform.",
            "- **All three servers accept public network access** (AZ-DB-006, HIGH) - "
            "the largest infrastructure exposure for stored PHI.",
            "- `cm_*` case-management tables are **not deployed**; Phase 0 established "
            "the exposure is PHI ingress/egress, not PHI at rest.", "",
            "## Where PHI is transmitted", "",
            "- Client to API: TLS 1.2 floor, HTTPS-only enforced (AZ-APP-001/002 PASS).",
            "- API to database: TLS required at the server; but over a public endpoint "
            "because the App Service is not VNet-integrated (AZ-NET-007).", "",
            "## Where PHI is logged (it should not be)", "",
            f"{len(logs)} finding(s) relate to logging of sensitive values:", ""]
        for f in sorted(logs, key=lambda x: SEV_ORDER.get(x.severity.value, 9))[:10]:
            L.append(f"- `{f.rule_id}` [{f.severity.value}] {f.title[:80]} "
                     f"({f.location})")
        L += ["", "Phase 0 DP-01 named `password_reset.py:182` and "
                  "`connectors.py:300,611`; the Phase 1 custom rule AGT-PHI-001 "
                  "independently reproduced `connectors.py:300`.", "",
              "## Where PHI exits to external APIs", "",
              "| Destination | Data | De-identification | Residual risk |",
              "|---|---|---|---|",
              "| Anthropic API | Clinical narrative + structured fields | Direct "
              "identifiers stripped at the `_call_claude` chokepoint (Sprint 1 DP-02, "
              "11 of 12 egress sites) | **Narrative is still PHI. Closable only by a "
              "signed BAA + zero retention.** |",
              "| NPPES / PECOS / LEIE | NPI, organisation name | Not PHI (provider "
              "data) | Low |",
              "| SendGrid | Recipient email | Not clinical PHI | Medium - no BAA "
              "evidence |", "",
              "## De-identification status (Sprint 1)", "",
              "`phi_deidentify.py` performs exact-value replacement of name / MRN / "
              "DOB / SSN / phone before AI egress, verified by intercepting real "
              "outbound payloads. Known and accepted limitations:", "",
              "- `generate_government_case_document` takes `case_facts`, not "
              "`patient_context`, and is the 1 of 12 sites not covered.",
              "- Over-redaction is possible (a patient surnamed *Stone* turns "
              "\"kidney stone\" into \"kidney [PATIENT_LAST]\"). Accepted: "
              "over-redaction is visible at the `requires_review` gate; a leak is not.",
              "- The clinical narrative itself is not redacted and remains PHI.", "",
              f"## PHI-tagged findings: {len(phi)}", ""]
        for f in sorted(phi, key=lambda x: SEV_ORDER.get(x.severity.value, 9))[:25]:
            L.append(f"- `{f.rule_id}` [{f.severity.value}] {f.title[:85]}")
        path = self.out / "phi_data_flow_report.md"
        path.write_text("\n".join(L), encoding="utf-8")
        return str(path)

    # ── 4G evidence packages ─────────────────────────────────────────────────

    def evidence_packages(self, matrices: Dict[str, str],
                          gov_readiness: Optional[Path] = None) -> Dict[str, int]:
        base = self.out / "evidence"
        packs = {
            "hipaa_evidence_package": ["hipaa_security_rule_matrix.md",
                                       "phi_data_flow_report.md"],
            "tefca_evidence_package": ["tefca_compliance_matrix.md"],
            "nist_evidence_package": ["nist_800_53_matrix.md",
                                      "nist_800_171_matrix.md"],
            "owasp_evidence_package": ["owasp_asvs_matrix.md"],
            "fedramp_preparation": [],
        }
        written: Dict[str, int] = {}
        for pack, docs in packs.items():
            d = base / pack
            (d / "finding_evidence").mkdir(parents=True, exist_ok=True)
            n = 0
            for doc in docs:
                src = self.out / doc
                if src.exists():
                    shutil.copy2(src, d / doc)
                    n += 1
            # attach the findings relevant to this framework
            attr = {"hipaa_evidence_package": "hipaa",
                    "tefca_evidence_package": "hipaa",
                    "nist_evidence_package": "nist_800_53",
                    "owasp_evidence_package": "owasp_asvs",
                    "fedramp_preparation": "nist_800_53"}[pack]
            rel = [f for f in self.findings if getattr(f.compliance, attr, [])]
            payload = [f.to_dict() for f in sorted(
                rel, key=lambda x: SEV_ORDER.get(x.severity.value, 9))[:200]]
            (d / "finding_evidence" / "findings.json").write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8")
            n += 1
            written[pack] = n

        # FedRAMP-specific docs
        fr = base / "fedramp_preparation"
        (fr / "control_inheritance.md").write_text("\n".join([
            "# FedRAMP Control Inheritance", "", HEADER_NOTE, "",
            "Running on Azure (commercial today, Government later) lets the system "
            "inherit a substantial part of the infrastructure control layer. It does "
            "NOT inherit application controls.", "",
            "| Layer | Inherited from Azure | Retained by DocuAction |", "|---|---|---|",
            "| Physical & environmental (PE) | Yes - fully | None |",
            "| Media protection (MP) | Largely | Application-level export controls |",
            "| Maintenance (MA) | Platform | Application dependency patching (SI-2) |",
            "| SC - transport/at-rest crypto | Platform primitives | Correct USE of "
            "them (TLS config, KV references) |",
            "| CP - backup infrastructure | Platform | Retention, geo-redundancy and "
            "DR TESTING |",
            "| AC / AU / IA / SI | **No** | **Entirely the application's** |", "",
            "Azure Government carries a FedRAMP High P-ATO. Everything Phases 1-3 "
            "assess (AC-3, AU-2/3/9/12, IA-2/5, SI-10/11) stays with the application "
            "and is where the current 309 findings sit.", ""]), encoding="utf-8")
        if gov_readiness and Path(gov_readiness).exists():
            shutil.copy2(gov_readiness, fr / "azure_gov_readiness.md")
        (fr / "gap_analysis.md").write_text("\n".join([
            "# FedRAMP Readiness - Gap Analysis", "", HEADER_NOTE, "",
            "| Gap | Impact | Owner |", "|---|---|---|",
            "| **No BAA with the AI subprocessor** | Blocking for HIPAA and for any "
            "ATO that includes the AI features | Organisational / legal |",
            "| **Anthropic API is outside an Azure Gov boundary** | Blocking for Gov "
            "migration | Architecture decision |",
            "| Databases accept public network access | High - AC-4 / SC-7 | "
            "Engineering (VNet integration first) |",
            "| No database-tier audit logging (pgaudit off) | AU-2 / AU-12 | "
            "Engineering |",
            "| No diagnostic settings on PostgreSQL or Key Vault | AU-6 | Engineering |",
            "| No hash chain on audit log | AU-9 tamper evidence | Sprint 2 |",
            "| Static Web Apps unavailable in Azure Gov | Frontend replatform | "
            "Engineering (large) |",
            "| No security awareness training evidence | AT family | Organisational |",
            "| No documented IR plan or tested DR | IR / CP | Organisational |", "",
            "**Assessment.** The application-layer gaps are tractable engineering "
            "work. The two blocking items are not engineering problems - they are a "
            "contract (BAA) and a boundary decision (where the model runs).", ""]),
            encoding="utf-8")
        written["fedramp_preparation"] = written.get("fedramp_preparation", 0) + 3
        return written

    # ── executive summary ────────────────────────────────────────────────────

    def executive(self, stats: Dict[str, Any], coverage: Dict[str, float]) -> str:
        c = Counter(f.severity.value for f in self.findings)
        L = ["# Compliance Executive Summary", "",
             f"**{getattr(self.project, 'display_name', 'DocuAction')}** · "
             f"scan `{getattr(self.scan, 'scan_id', 'n/a')}` · {_now()[:10]}", "",
             "---", "",
             "## Posture at a glance", "",
             "| Framework | Automated coverage | Position |", "|---|--:|---|",
             f"| HIPAA Technical Safeguards | {coverage.get('hipaa', 0):.0f}% | "
             f"{stats.get('hipaa_gaps', 0)} of {stats.get('hipaa_total', 12)} "
             f"safeguards have findings |",
             f"| NIST SP 800-53 Rev. 5 | {coverage.get('nist_800_53', 0):.0f}% | "
             f"{stats.get('families_with_findings', 0)} of "
             f"{stats.get('families_total', 19)} families exercised |",
             f"| NIST SP 800-171 Rev. 3 | derived | 14 requirement families mapped |",
             f"| OWASP Top 10 (2021) | {coverage.get('owasp_top10', 0):.0f}% | "
             f"detection coverage |",
             f"| OWASP API Top 10 (2023) | {coverage.get('owasp_api_top10', 0):.0f}% | "
             f"detection coverage |",
             "| OWASP ASVS v4.0 | partial | no target level agreed (L2 recommended) |",
             "| TEFCA | partial | registry not deployed - ~half untestable |", "",
             "## Findings", "",
             f"**{len(self.findings)} total** - Critical {c['critical']}, High "
             f"{c['high']}, Medium {c['medium']}, Low {c['low']}", "",
             "## The three things that actually block", "",
             "1. **No Business Associate Agreement with the AI subprocessor.** "
             "Clinical narrative leaves the boundary. No code change closes this; it "
             "is a contract. Blocks HIPAA §164.308(b)(1) and any ATO covering the AI "
             "features.",
             "2. **All three databases accept public network access.** Fix ordering "
             "matters: App Service VNet integration must land first, then private "
             "access.",
             "3. **Six Critical findings** - a live database credential and an "
             "OpenAI key in the working tree, plus unsafe-deserialization patterns.", "",
             "## Recommended sequencing", "",
             "| When | Action | Owner |", "|---|---|---|",
             "| Immediate | Delete/rotate the working-tree credentials; rotate the "
             "Perigon key | Engineering |",
             "| Sprint 2 | TEFCA state machine, NPI check digit, audit hash chain, "
             "mandatory identifiers | Engineering |",
             "| Sprint 2 | Dependency upgrades (26 of 27 advisories have a fix) | "
             "Engineering |",
             "| Sprint 3 | VNet integration then private DB access; pgaudit; "
             "diagnostic settings | Engineering |",
             "| In parallel | **BAA negotiation** and the AI boundary decision | "
             "Legal / leadership |",
             "| Before ATO | IR plan, DR test, training records | Organisational |", "",
             "---", "",
             "**This is not a certification.** It is an evidence package produced by "
             "automated analysis. Controls with no automated finding are reported as "
             "NOT ASSESSED, and a large share of the Administrative and Physical "
             "safeguards cannot be evidenced by any scanner.", ""]
        path = self.out / "COMPLIANCE_EXECUTIVE_SUMMARY.md"
        path.write_text("\n".join(L), encoding="utf-8")
        return str(path)

    # ── run all ──────────────────────────────────────────────────────────────

    def run_all(self, coverage: Dict[str, float],
                gov_readiness: Optional[Path] = None) -> Dict[str, Any]:
        hipaa_path, hstats = self.hipaa()
        tefca_path = self.tefca()
        n53_path, nstats = self.nist_800_53()
        n171_path = self.nist_800_171()
        asvs_path = self.asvs()
        phi_path = self.phi_flow()
        packs = self.evidence_packages({}, gov_readiness)
        stats = {"hipaa_gaps": hstats["gaps"], "hipaa_total": hstats["total"], **nstats}
        exec_path = self.executive(stats, coverage)
        return {"files": [hipaa_path, tefca_path, n53_path, n171_path, asvs_path,
                          phi_path, exec_path],
                "packages": packs, "stats": stats}
