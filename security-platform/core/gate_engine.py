"""Release gate and security scoring.

The gate answers one question - may this build ship? - and must always explain
itself. Every rule that fires produces a human-readable justification line, so a
FAIL is actionable and a PASS is auditable evidence rather than a bare assertion.

POLICY IS DATA, NOT CODE
    Thresholds come from the project's gate_policy block (or config/gate_policy.json).
    A stricter programme changes JSON, not Python.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.models import Category, Finding, GateResult, Scan, Severity

# Penalty weights per finding, in "score points". Chosen so a single Critical
# (25) drops a clean project below a 70 threshold on its own, one High (10) does
# not, and Low findings can only ever account for a modest share.
SEVERITY_WEIGHTS: Dict[str, float] = {
    "critical": 25.0,
    "high": 10.0,
    "medium": 3.0,
    "low": 0.5,
    "info": 0.0,
}

DEFAULT_POLICY: Dict[str, Any] = {
    "block_on_critical": True,
    "block_on_high": True,
    "min_security_score": 70,
    "require_owasp_coverage": 80,
    "max_critical_cves": 0,
    "max_high_cves": 5,
    "block_on_secrets": True,
    "warn_only": False,
    "fail_on_tool_error": False,
    "required_tools": [],
    # A scan in which nothing executed produces no evidence. Without this rule a CI
    # job whose tool installation failed would report score 100 / PASS and certify a
    # build nobody scanned. Absence of findings is not evidence of absence.
    "require_at_least_one_scanner": True,
    # An SBOM is a supply-chain deliverable in its own right (EO 14028 / NIST SSDF).
    # Verified by artefact existence, not by the plugin claiming success.
    "require_sbom": False,
}


# Penalty density (points per 1,000 lines) at which the score is exactly 50.
# Calibration: a codebase carrying one High finding per KLOC scores 50. That is a
# deliberately demanding midpoint for a security-sensitive healthcare application.
DENSITY_HALF_POINT = 5.0

SCORE_MODEL_VERSION = "2.0-density"


def raw_penalty(findings: List[Finding]) -> float:
    return sum(SEVERITY_WEIGHTS.get(f.severity.value, 0.0)
               for f in findings if not f.suppressed)


def compute_security_score(findings: List[Finding], kloc: Optional[float] = None) -> float:
    """0-100, higher is better. Suppressed findings excluded.

    v2.0 - DENSITY NORMALISED
        The v1 model was `100 - penalty`, which floored at 0 once penalties passed
        100 points. On the first DocuAction baseline that was 1,446 penalty points:
        the score read 0.0 and would have read 0.0 for 90 findings or 900. A metric
        that cannot distinguish those is not a metric.

        v2 divides penalty by size and applies a hyperbola:

            density = penalty_points / KLOC
            score   = 100 / (1 + density / 5.0)

        Properties that matter:
          * approaches 0 but never reaches it, so it always discriminates;
          * strictly monotonic - fixing anything always raises the score;
          * size-normalised, so a 160 KLOC application is not punished for being
            larger than a 10 KLOC one;
          * two findings of the same severity always move it by the same amount.

        Without a KLOC figure it falls back to the v1 linear model, because inventing
        a denominator would be worse than being explicit about the limitation.
    """
    penalty = raw_penalty(findings)
    if not kloc or kloc <= 0:
        return round(max(0.0, 100.0 - penalty), 1)
    density = penalty / float(kloc)
    return round(100.0 / (1.0 + density / DENSITY_HALF_POINT), 1)


def score_formula_text(kloc: Optional[float] = None) -> str:
    """ASCII only: printed to a Windows console whose cp1252 codec cannot encode
    typographic characters and would raise UnicodeEncodeError."""
    parts = ", ".join(f"{k} x{v:g}" for k, v in SEVERITY_WEIGHTS.items() if v)
    if kloc and kloc > 0:
        return (f"score = 100 / (1 + density/{DENSITY_HALF_POINT:g}) where "
                f"density = penalty_points / {kloc:.1f} KLOC; "
                f"penalties: {parts} (suppressed excluded) [model {SCORE_MODEL_VERSION}]")
    return (f"score = max(0, 100 - sum of penalties), penalties: {parts} "
            f"(suppressed excluded) [model 1.0-linear, no KLOC available]")


class GateEngine:
    """Evaluates a scan against a release policy."""

    def __init__(self, policy: Optional[Dict[str, Any]] = None):
        self.policy = dict(DEFAULT_POLICY)
        if policy:
            # Keys beginning with "_" are documentation (rationale, comments) that
            # lives alongside the policy in JSON. They are not rules and must not be
            # loaded as such, or they surface in every printed policy.
            self.policy.update({k: v for k, v in policy.items()
                                if v is not None and not k.startswith("_")})

    @classmethod
    def from_project(cls, project, platform_root: Optional[Path] = None) -> "GateEngine":
        """Project policy wins; falls back to config/gate_policy.json, then defaults."""
        policy: Dict[str, Any] = {}
        if platform_root:
            shared = Path(platform_root) / "config" / "gate_policy.json"
            if shared.exists():
                try:
                    policy.update(json.loads(shared.read_text(encoding="utf-8")))
                except Exception:
                    pass
        if getattr(project, "gate_policy", None):
            policy.update(project.gate_policy)
        return cls(policy)

    def evaluate(self, scan: Scan,
                 compliance_coverage: Optional[Dict[str, float]] = None,
                 sbom_paths: Optional[Dict[str, str]] = None,
                 ) -> tuple[GateResult, List[str]]:
        """Return (result, justifications). Justifications explain PASS too."""
        active = [f for f in scan.findings if not f.suppressed]
        counts = scan.counts_by_severity()
        reasons: List[str] = []
        failures = 0
        warnings = 0

        # Evidence check FIRST. A clean result from a scan that ran nothing is not a
        # clean result, and must never be reported as one.
        ran_tools = [t for t in scan.tools if t.ran]
        if self.policy.get("require_at_least_one_scanner") and not ran_tools:
            failures += 1
            reasons.append(
                "FAIL: no scanner executed - this scan produced no evidence, so a "
                "score of 100 and an empty finding list are meaningless. "
                "(policy: require_at_least_one_scanner=true)")

        # Severity gates
        if self.policy.get("block_on_critical") and counts.get("critical", 0) > 0:
            failures += 1
            reasons.append(
                f"FAIL: {counts['critical']} Critical finding(s) present "
                f"(policy: block_on_critical=true)")
        if self.policy.get("block_on_high") and counts.get("high", 0) > 0:
            failures += 1
            reasons.append(
                f"FAIL: {counts['high']} High finding(s) present "
                f"(policy: block_on_high=true)")

        # Score gate
        min_score = float(self.policy.get("min_security_score", 0) or 0)
        if min_score and scan.security_score < min_score:
            failures += 1
            reasons.append(
                f"FAIL: security score {scan.security_score} is below the minimum "
                f"{min_score:g}")
        elif min_score:
            reasons.append(
                f"PASS: security score {scan.security_score} meets the minimum {min_score:g}")

        # Vulnerable-dependency gates
        cve_findings = [f for f in active if f.cve or f.category in
                        (Category.SCA, Category.CONTAINER)]
        crit_cves = len([f for f in cve_findings if f.severity == Severity.CRITICAL])
        high_cves = len([f for f in cve_findings if f.severity == Severity.HIGH])
        max_crit = self.policy.get("max_critical_cves")
        if max_crit is not None and crit_cves > int(max_crit):
            failures += 1
            reasons.append(
                f"FAIL: {crit_cves} Critical dependency CVE(s) exceed the limit of {max_crit}")
        max_high = self.policy.get("max_high_cves")
        if max_high is not None and high_cves > int(max_high):
            failures += 1
            reasons.append(
                f"FAIL: {high_cves} High dependency CVE(s) exceed the limit of {max_high}")

        # Secrets are treated as release-blocking regardless of severity mapping.
        if self.policy.get("block_on_secrets"):
            secret_hits = [f for f in active if f.category == Category.SECRETS]
            if secret_hits:
                failures += 1
                reasons.append(
                    f"FAIL: {len(secret_hits)} secret(s) detected in the repository "
                    f"(policy: block_on_secrets=true)")

        # SBOM. Checked by looking for the artefact on disk, because a plugin that
        # silently no-ops still reports "ran" - which is exactly what cyclonedx did
        # when its v4 CLI flags were rejected by v7.
        if self.policy.get("require_sbom"):
            produced = [p for p in (sbom_paths or {}).values()
                        if p and Path(p).exists()]
            if produced:
                reasons.append(f"PASS: SBOM present ({len(produced)} artefact(s))")
            else:
                failures += 1
                reasons.append(
                    "FAIL: policy requires an SBOM but no CycloneDX artefact was "
                    "produced (policy: require_sbom=true)")

        # Compliance coverage
        req_cov = self.policy.get("require_owasp_coverage")
        if req_cov and compliance_coverage:
            actual = compliance_coverage.get("owasp_top10")
            if actual is None:
                warnings += 1
                reasons.append(
                    "WARN: OWASP Top 10 coverage required by policy but not computed")
            elif actual < float(req_cov):
                warnings += 1
                reasons.append(
                    f"WARN: OWASP Top 10 coverage {actual:.0f}% is below the required "
                    f"{float(req_cov):.0f}%")
            else:
                reasons.append(
                    f"PASS: OWASP Top 10 coverage {actual:.0f}% meets the required "
                    f"{float(req_cov):.0f}%")

        # Tool availability. A skipped scanner is a coverage gap, and the report must
        # say so - but by policy it does not fail the build unless explicitly required.
        skipped = [t for t in scan.tools if not t.ran]
        if skipped:
            names = ", ".join(sorted(t.name for t in skipped))
            warnings += 1
            reasons.append(f"WARN: reduced coverage - scanner(s) did not run: {names}")

        required = set(self.policy.get("required_tools") or [])
        if required:
            ran = {t.name for t in scan.tools if t.ran}
            missing = sorted(required - ran)
            if missing:
                failures += 1
                reasons.append(
                    f"FAIL: policy-required scanner(s) did not run: {', '.join(missing)}")

        if self.policy.get("fail_on_tool_error"):
            errored = [t.name for t in scan.tools if t.error]
            if errored:
                failures += 1
                reasons.append(
                    f"FAIL: scanner error(s) with fail_on_tool_error=true: "
                    f"{', '.join(sorted(errored))}")

        # Verdict
        if failures and not self.policy.get("warn_only"):
            result = GateResult.FAIL
        elif failures and self.policy.get("warn_only"):
            result = GateResult.WARN
            reasons.append("NOTE: warn_only=true - failures downgraded to warnings")
        elif warnings:
            result = GateResult.WARN
        else:
            result = GateResult.PASS

        if result == GateResult.PASS and not reasons:
            reasons.append("PASS: no policy rule was violated")

        header = (f"Gate {result.value.upper()} - {len(active)} active finding(s), "
                  f"score {scan.security_score}/100 "
                  f"[C:{counts.get('critical',0)} H:{counts.get('high',0)} "
                  f"M:{counts.get('medium',0)} L:{counts.get('low',0)}]")
        return result, [header] + reasons

    def describe(self) -> Dict[str, Any]:
        return dict(self.policy)
