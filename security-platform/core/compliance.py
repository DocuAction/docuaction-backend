"""PHASE 1E - compliance mapping.

Aggregates the per-finding ComplianceMapping objects into framework matrices and
writes them out. Findings already carry their control references (each plugin sets
them); this module's job is the roll-up, the gap analysis, and the honest arithmetic.

TWO DIFFERENT THINGS CALLED "COVERAGE"
    These are routinely conflated, and conflating them produces a number that sounds
    like assurance but is not:

    detection coverage  - what fraction of a framework this platform is CAPABLE of
                          detecting, i.e. controls that at least one loaded rule maps
                          to. This is an assurance statement about the tooling, and
                          it is what the release gate consumes.

    finding coverage    - what fraction of a framework currently HAS findings. This
                          is a statement about the code, and higher is WORSE.

    A report that shows "OWASP coverage 40%" without saying which one is meaningless.
    Both are computed, both are labelled, and the gate is explicitly wired to the
    first.

A control with no findings is reported as "no findings" - never as "compliant".
Absence of a finding from an automated scan is not evidence of a satisfied control.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.models import Finding, Scan, Severity

# ── Framework catalogues ──────────────────────────────────────────────────────

OWASP_TOP10_2021 = {
    "A01:2021": "Broken Access Control",
    "A02:2021": "Cryptographic Failures",
    "A03:2021": "Injection",
    "A04:2021": "Insecure Design",
    "A05:2021": "Security Misconfiguration",
    "A06:2021": "Vulnerable and Outdated Components",
    "A07:2021": "Identification and Authentication Failures",
    "A08:2021": "Software and Data Integrity Failures",
    "A09:2021": "Security Logging and Monitoring Failures",
    "A10:2021": "Server-Side Request Forgery (SSRF)",
}

OWASP_API_TOP10_2023 = {
    "API1:2023": "Broken Object Level Authorization",
    "API2:2023": "Broken Authentication",
    "API3:2023": "Broken Object Property Level Authorization",
    "API4:2023": "Unrestricted Resource Consumption",
    "API5:2023": "Broken Function Level Authorization",
    "API6:2023": "Unrestricted Access to Sensitive Business Flows",
    "API7:2023": "Server Side Request Forgery",
    "API8:2023": "Security Misconfiguration",
    "API9:2023": "Improper Inventory Management",
    "API10:2023": "Unsafe Consumption of APIs",
}

# NIST SP 800-53 Rev. 5 - the controls this platform can speak to.
NIST_CONTROLS = {
    "AC-3": ("Access Control", "Access Enforcement"),
    "AC-4": ("Access Control", "Information Flow Enforcement"),
    "AC-7": ("Access Control", "Unsuccessful Logon Attempts"),
    "AC-12": ("Access Control", "Session Termination"),
    "AU-2": ("Audit and Accountability", "Event Logging"),
    "AU-3": ("Audit and Accountability", "Content of Audit Records"),
    "AU-9": ("Audit and Accountability", "Protection of Audit Information"),
    "AU-12": ("Audit and Accountability", "Audit Record Generation"),
    "IA-2": ("Identification and Authentication", "Identification and Authentication"),
    "IA-5": ("Identification and Authentication", "Authenticator Management"),
    "CM-6": ("Configuration Management", "Configuration Settings"),
    "CM-7": ("Configuration Management", "Least Functionality"),
    "CM-8": ("Configuration Management", "System Component Inventory"),
    "RA-5": ("Risk Assessment", "Vulnerability Monitoring and Scanning"),
    "SC-5": ("System and Communications Protection", "Denial-of-Service Protection"),
    "SC-7": ("System and Communications Protection", "Boundary Protection"),
    "SC-8": ("System and Communications Protection", "Transmission Confidentiality"),
    "SC-12": ("System and Communications Protection", "Cryptographic Key Establishment"),
    "SC-13": ("System and Communications Protection", "Cryptographic Protection"),
    "SC-18": ("System and Communications Protection", "Mobile Code"),
    "SC-28": ("System and Communications Protection", "Protection of Information at Rest"),
    "SI-2": ("System and Information Integrity", "Flaw Remediation"),
    "SI-3": ("System and Information Integrity", "Malicious Code Protection"),
    "SI-10": ("System and Information Integrity", "Information Input Validation"),
    "SI-11": ("System and Information Integrity", "Error Handling"),
}

NIST_FAMILIES = {
    "AC": "Access Control", "AU": "Audit and Accountability",
    "IA": "Identification and Authentication", "CM": "Configuration Management",
    "RA": "Risk Assessment", "SC": "System and Communications Protection",
    "SI": "System and Information Integrity",
}

HIPAA_SAFEGUARDS = {
    "164.312(a)": ("Access Control", "required",
                   "Unique user identification, emergency access, automatic logoff, "
                   "encryption/decryption"),
    "164.312(b)": ("Audit Controls", "required",
                   "Record and examine activity in systems containing ePHI"),
    "164.312(c)": ("Integrity", "required",
                   "Protect ePHI from improper alteration or destruction"),
    "164.312(d)": ("Person or Entity Authentication", "required",
                   "Verify that a person seeking access is the one claimed"),
    "164.312(e)": ("Transmission Security", "required",
                   "Guard against unauthorised access to ePHI transmitted over a network"),
}

CWE_TOP25_2024 = {
    "79": "Cross-site Scripting", "787": "Out-of-bounds Write",
    "89": "SQL Injection", "352": "Cross-Site Request Forgery",
    "22": "Path Traversal", "125": "Out-of-bounds Read",
    "78": "OS Command Injection", "416": "Use After Free",
    "862": "Missing Authorization", "434": "Unrestricted Upload",
    "94": "Code Injection", "20": "Improper Input Validation",
    "77": "Command Injection", "287": "Improper Authentication",
    "269": "Improper Privilege Management", "502": "Deserialization of Untrusted Data",
    "200": "Exposure of Sensitive Information", "863": "Incorrect Authorization",
    "918": "Server-Side Request Forgery", "119": "Improper Restriction of Buffer",
    "476": "NULL Pointer Dereference", "798": "Use of Hard-coded Credentials",
    "190": "Integer Overflow", "400": "Uncontrolled Resource Consumption",
    "306": "Missing Authentication for Critical Function",
}

SEV_ORDER = ["critical", "high", "medium", "low", "info"]


def _norm_hipaa(ref: str) -> str:
    """Collapse §164.312(a)(2)(i) -> 164.312(a) so it lands in a safeguard bucket."""
    r = (ref or "").strip().lstrip("§")
    for key in HIPAA_SAFEGUARDS:
        if r.startswith(key):
            return key
    return ""


class ComplianceMapper:
    """Rolls findings up into framework matrices and writes the report files."""

    def __init__(self, platform_root: Path):
        self.root = Path(platform_root)

    # ── public API ───────────────────────────────────────────────────────────

    def build(self, scan: Scan, project) -> Dict[str, Any]:
        active = [f for f in scan.findings if not f.suppressed]
        out_dir = self.root / "reports" / project.name
        out_dir.mkdir(parents=True, exist_ok=True)

        matrices = {
            "owasp_top10": self._owasp(active, OWASP_TOP10_2021, "owasp_top10"),
            "owasp_api_top10": self._owasp(active, OWASP_API_TOP10_2023, "owasp_api_top10"),
            "nist_800_53": self._nist(active),
            "hipaa": self._hipaa(active),
            "cwe_top25": self._cwe(active),
            "asvs": self._asvs(active),
        }
        coverage = {k: v["detection_coverage_pct"] for k, v in matrices.items()
                    if "detection_coverage_pct" in v}

        unmapped = [f for f in active if f.compliance.is_empty()]
        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scan_id": scan.scan_id,
            "project": project.name,
            "total_findings": len(active),
            "findings_with_any_mapping": len(active) - len(unmapped),
            "findings_unmapped": len(unmapped),
            "coverage_definitions": {
                "detection_coverage_pct":
                    "Share of the framework's controls that at least one loaded rule "
                    "maps to. An assurance statement about the TOOLING. Consumed by "
                    "the release gate.",
                "controls_with_findings":
                    "Controls that currently have at least one finding. A statement "
                    "about the CODE - higher is worse.",
                "no_findings":
                    "No finding maps here. This is NOT an assertion of compliance: an "
                    "automated scan not finding something is not evidence the control "
                    "is satisfied.",
            },
            "detection_coverage": coverage,
            "matrices": matrices,
        }

        self._write_json(out_dir / "compliance_summary.json", summary)
        self._write_owasp_md(out_dir / "compliance_owasp_top10.md", scan,
                             matrices["owasp_top10"], OWASP_TOP10_2021,
                             "OWASP Top 10 (2021)")
        self._write_owasp_md(out_dir / "compliance_owasp_api.md", scan,
                             matrices["owasp_api_top10"], OWASP_API_TOP10_2023,
                             "OWASP API Security Top 10 (2023)")
        self._write_nist_md(out_dir / "compliance_nist_800_53.md", scan,
                            matrices["nist_800_53"])
        self._write_hipaa_md(out_dir / "compliance_hipaa.md", scan, matrices["hipaa"])

        return {
            "available": True,
            "coverage": coverage,
            "matrices": matrices,
            "summary": summary,
            "files": [str(p) for p in sorted(out_dir.glob("compliance_*"))],
        }

    # ── matrix builders ──────────────────────────────────────────────────────

    @staticmethod
    def _bucket(active: List[Finding], attr: str) -> Dict[str, List[Finding]]:
        b: Dict[str, List[Finding]] = defaultdict(list)
        for f in active:
            for ref in getattr(f.compliance, attr, []) or []:
                b[str(ref).strip()].append(f)
        return b

    @staticmethod
    def _counts(items: List[Finding]) -> Dict[str, int]:
        c = {s: 0 for s in SEV_ORDER}
        for f in items:
            c[f.severity.value] += 1
        return c

    def _owasp(self, active: List[Finding], catalogue: Dict[str, str],
               attr: str) -> Dict[str, Any]:
        buckets = self._bucket(active, attr)
        rows = []
        for cid, name in catalogue.items():
            items = buckets.get(cid, [])
            rows.append({
                "id": cid, "name": name,
                "finding_count": len(items),
                "by_severity": self._counts(items),
                "status": "findings" if items else "no findings",
                "top_rules": sorted({f.rule_id for f in items})[:5],
            })
        with_findings = sum(1 for r in rows if r["finding_count"])
        return {
            "rows": rows,
            "controls_total": len(catalogue),
            "controls_with_findings": with_findings,
            # Detection coverage = how much of the framework our RULESET can reach.
            "detection_coverage_pct": round(100.0 * len(
                [c for c in catalogue if c in buckets]) / len(catalogue), 1),
            "findings_coverage_pct": round(100.0 * with_findings / len(catalogue), 1),
        }

    def _nist(self, active: List[Finding]) -> Dict[str, Any]:
        buckets = self._bucket(active, "nist_800_53")
        rows = []
        for cid, (family, title) in sorted(NIST_CONTROLS.items()):
            items = buckets.get(cid, [])
            rows.append({
                "id": cid, "family": family, "title": title,
                "finding_count": len(items),
                "by_severity": self._counts(items),
                "status": "findings" if items else "no findings",
            })
        # Controls seen on findings but absent from our catalogue - keeps the matrix
        # honest instead of silently dropping them.
        extra = sorted(set(buckets) - set(NIST_CONTROLS))
        by_family: Dict[str, int] = defaultdict(int)
        for r in rows:
            by_family[r["id"].split("-")[0]] += r["finding_count"]
        return {
            "rows": rows,
            "controls_total": len(NIST_CONTROLS),
            "controls_with_findings": sum(1 for r in rows if r["finding_count"]),
            "detection_coverage_pct": round(100.0 * len(
                [c for c in NIST_CONTROLS if c in buckets]) / len(NIST_CONTROLS), 1),
            "by_family": {NIST_FAMILIES.get(k, k): v for k, v in sorted(by_family.items())},
            "uncatalogued_controls": extra,
            "total_mapped_findings": len({id(f) for items in buckets.values() for f in items}),
        }

    def _hipaa(self, active: List[Finding]) -> Dict[str, Any]:
        buckets: Dict[str, List[Finding]] = defaultdict(list)
        for f in active:
            for ref in f.compliance.hipaa or []:
                key = _norm_hipaa(ref)
                if key:
                    buckets[key].append(f)
        rows = []
        for sid, (name, req, desc) in HIPAA_SAFEGUARDS.items():
            items = buckets.get(sid, [])
            rows.append({
                "id": sid, "name": name, "requirement": req, "description": desc,
                "finding_count": len(items),
                "by_severity": self._counts(items),
                "status": "findings" if items else "no findings",
            })
        return {
            "rows": rows,
            "safeguards_total": len(HIPAA_SAFEGUARDS),
            "safeguards_with_findings": sum(1 for r in rows if r["finding_count"]),
            "detection_coverage_pct": round(100.0 * len(
                [s for s in HIPAA_SAFEGUARDS if s in buckets]) / len(HIPAA_SAFEGUARDS), 1),
        }

    def _cwe(self, active: List[Finding]) -> Dict[str, Any]:
        buckets = self._bucket(active, "cwe")
        rows = []
        for cwe, name in CWE_TOP25_2024.items():
            items = buckets.get(cwe, [])
            if items:
                rows.append({"cwe": f"CWE-{cwe}", "name": name,
                             "finding_count": len(items),
                             "by_severity": self._counts(items)})
        flagged = sum(1 for f in active if f.compliance.cwe_top25)
        return {
            "rows": sorted(rows, key=lambda r: -r["finding_count"]),
            "top25_categories_present": len(rows),
            "top25_total": len(CWE_TOP25_2024),
            "findings_flagged_top25": flagged,
            "distinct_cwes_seen": len(buckets),
            "detection_coverage_pct": round(
                100.0 * len(rows) / len(CWE_TOP25_2024), 1),
        }

    def _asvs(self, active: List[Finding]) -> Dict[str, Any]:
        buckets = self._bucket(active, "owasp_asvs")
        chapters: Dict[str, int] = defaultdict(int)
        for ref, items in buckets.items():
            chapters[str(ref).split(".")[0]] += len(items)
        return {
            "requirements_referenced": len(buckets),
            "by_chapter": dict(sorted(chapters.items())),
            "findings_with_asvs": sum(1 for f in active if f.compliance.owasp_asvs),
        }

    # ── writers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    @staticmethod
    def _hdr(scan: Scan, title: str) -> List[str]:
        return [
            f"# {title}", "",
            f"**Project:** {scan.project_name}  ",
            f"**Scan:** `{scan.scan_id}`  ",
            f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}  ",
            "",
            "> A control with **no findings** is reported as such. That is *not* an "
            "assertion of compliance - an automated scan finding nothing is not "
            "evidence that a control is satisfied. Controls requiring procedural or "
            "physical evidence cannot be assessed by this platform at all.",
            "",
        ]

    def _write_owasp_md(self, path: Path, scan: Scan, matrix: Dict[str, Any],
                        catalogue: Dict[str, str], title: str) -> None:
        L = self._hdr(scan, title)
        L += [
            f"**Detection coverage:** {matrix['detection_coverage_pct']:.0f}% "
            f"- the share of the {matrix['controls_total']} categories that at least "
            f"one loaded rule can detect.  ",
            f"**Categories with findings:** {matrix['controls_with_findings']}"
            f"/{matrix['controls_total']}", "",
            "| ID | Category | Findings | C | H | M | L | Status |",
            "|---|---|--:|--:|--:|--:|--:|---|",
        ]
        for r in matrix["rows"]:
            s = r["by_severity"]
            L.append(f"| **{r['id']}** | {r['name']} | {r['finding_count']} | "
                     f"{s['critical']} | {s['high']} | {s['medium']} | {s['low']} | "
                     f"{r['status']} |")
        L += ["", "## Categories with findings", ""]
        for r in matrix["rows"]:
            if r["finding_count"]:
                L.append(f"- **{r['id']} {r['name']}** - {r['finding_count']} finding(s); "
                         f"rules: {', '.join(r['top_rules']) or 'n/a'}")
        path.write_text("\n".join(L) + "\n", encoding="utf-8")

    def _write_nist_md(self, path: Path, scan: Scan, matrix: Dict[str, Any]) -> None:
        L = self._hdr(scan, "NIST SP 800-53 Rev. 5 - Control Mapping")
        L += [
            f"**Controls in catalogue:** {matrix['controls_total']}  ",
            f"**Controls with findings:** {matrix['controls_with_findings']}  ",
            f"**Detection coverage:** {matrix['detection_coverage_pct']:.0f}%", "",
            "## Findings by control family", "",
            "| Family | Findings |", "|---|--:|",
        ]
        for fam, n in matrix["by_family"].items():
            L.append(f"| {fam} | {n} |")
        L += ["", "## Control detail", "",
              "| Control | Family | Title | Findings | C | H | M | L |",
              "|---|---|---|--:|--:|--:|--:|--:|"]
        for r in matrix["rows"]:
            s = r["by_severity"]
            L.append(f"| **{r['id']}** | {r['family']} | {r['title']} | "
                     f"{r['finding_count']} | {s['critical']} | {s['high']} | "
                     f"{s['medium']} | {s['low']} |")
        if matrix.get("uncatalogued_controls"):
            L += ["", "## Controls referenced by findings but not in this catalogue", "",
                  ", ".join(matrix["uncatalogued_controls"])]
        path.write_text("\n".join(L) + "\n", encoding="utf-8")

    def _write_hipaa_md(self, path: Path, scan: Scan, matrix: Dict[str, Any]) -> None:
        L = self._hdr(scan, "HIPAA Technical Safeguards - 45 CFR §164.312")
        L += [
            f"**Safeguards with findings:** {matrix['safeguards_with_findings']}"
            f"/{matrix['safeguards_total']}", "",
            "| Safeguard | Name | Req. | Findings | C | H | M | L | Status |",
            "|---|---|---|--:|--:|--:|--:|--:|---|",
        ]
        for r in matrix["rows"]:
            s = r["by_severity"]
            L.append(f"| **§{r['id']}** | {r['name']} | {r['requirement']} | "
                     f"{r['finding_count']} | {s['critical']} | {s['high']} | "
                     f"{s['medium']} | {s['low']} | {r['status']} |")
        L += ["", "## Safeguard detail", ""]
        for r in matrix["rows"]:
            L += [f"### §{r['id']} - {r['name']}", "",
                  f"{r['description']}", "",
                  f"**Findings:** {r['finding_count']}", ""]
        L += ["---", "",
              "**Scope limitation.** This maps only the *Technical* Safeguards of the "
              "Security Rule. Administrative (§164.308) and Physical (§164.310) "
              "safeguards require procedural and organisational evidence and are out "
              "of scope for static analysis. A BAA with any AI subprocessor remains a "
              "contractual control that no scanner can verify.", ""]
        path.write_text("\n".join(L) + "\n", encoding="utf-8")
