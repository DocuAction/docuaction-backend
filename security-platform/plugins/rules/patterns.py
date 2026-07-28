"""Declarative regex rule packs.

Pure stdlib — these rules must run even when every external scanner is unavailable,
which is the guarantee that the platform always produces SOME evidence.

PRECISION OVER RECALL
    Phase 0's manual review found this codebase is genuinely good on SQL injection,
    command injection and path traversal (ORM-parameterised, list-arg subprocess,
    UUID storage + commonpath containment). A rule pack that floods the report with
    false positives on a clean codebase trains people to ignore it. So each rule
    carries `negative` patterns that suppress the known-safe idiom, and confidence is
    set honestly — regex cannot prove taint, and findings are labelled accordingly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from core.models import Category, ComplianceMapping, Confidence, Severity


@dataclass
class PatternRule:
    id: str
    title: str
    severity: Severity
    pattern: str
    description: str
    remediation: str
    compliance: ComplianceMapping
    extensions: List[str] = field(default_factory=lambda: [".py"])
    confidence: Confidence = Confidence.MEDIUM
    category: Category = Category.SAST
    #: If any of these match the same line, the hit is suppressed (known-safe idiom).
    negative: List[str] = field(default_factory=list)
    #: Only consider files whose path matches this (e.g. route modules).
    path_include: str = ""
    path_exclude: str = ""
    effort: str = ""

    _rx: Optional[re.Pattern] = None
    _neg: Optional[List[re.Pattern]] = None

    def compiled(self) -> re.Pattern:
        if self._rx is None:
            self._rx = re.compile(self.pattern)
        return self._rx

    def negatives(self) -> List[re.Pattern]:
        if self._neg is None:
            self._neg = [re.compile(n) for n in self.negative]
        return self._neg

    def suppressed(self, line: str) -> bool:
        return any(n.search(line) for n in self.negatives())


def _cm(**kw) -> ComplianceMapping:
    return ComplianceMapping(**kw)


# ── Pack: OWASP / API Security ────────────────────────────────────────────────

OWASP_RULES: List[PatternRule] = [
    PatternRule(
        id="AGT-SQL-001",
        title="Raw SQL built by string interpolation",
        severity=Severity.HIGH,
        # text(f"..."), text("..." % x), execute("..." + var)
        pattern=r"(?:text|execute|executemany)\s*\(\s*(?:f[\"']|[\"'][^\"']*[\"']\s*(?:%|\+|\.format\s*\())",
        description="SQL text is assembled with an f-string, % formatting, .format() or "
                    "concatenation. If any interpolated value is request-derived this is "
                    "SQL injection.",
        remediation="Use bound parameters: text('... :name').bindparams(name=value), or "
                    "the ORM query API. Never interpolate into SQL text.",
        compliance=_cm(cwe=["89"], owasp_top10=["A03:2021"],
                       owasp_api_top10=["API8:2023"], owasp_asvs=["V5.3.4"],
                       nist_800_53=["SI-10"], cwe_top25=True),
        confidence=Confidence.MEDIUM,
        # A literal-only text() call is the safe, dominant idiom in this codebase.
        negative=[r"bindparams", r"#\s*nosec", r"#\s*noqa"],
        effort="0.5d",
    ),
    PatternRule(
        id="AGT-CMD-001",
        title="subprocess invoked with shell=True",
        severity=Severity.HIGH,
        pattern=r"subprocess\.(?:run|call|check_call|check_output|Popen)\s*\([^)]*shell\s*=\s*True",
        description="shell=True passes the command through a shell, so any interpolated "
                    "value becomes shell syntax (command injection).",
        remediation="Pass the command as a list and leave shell=False (the default).",
        compliance=_cm(cwe=["78"], owasp_top10=["A03:2021"], owasp_asvs=["V5.3.8"],
                       nist_800_53=["SI-10"], cwe_top25=True),
        confidence=Confidence.HIGH,
        negative=[r"#\s*nosec"],
        effort="0.5d",
    ),
    PatternRule(
        id="AGT-XSS-001",
        title="HTML response built from an f-string without escaping",
        severity=Severity.MEDIUM,
        pattern=r"(?:HTMLResponse|Response)\s*\(\s*(?:content\s*=\s*)?f[\"']",
        description="HTML is interpolated without escaping. Any interpolated value that "
                    "reaches a browser is reflected XSS.",
        remediation="Escape with html.escape() on every interpolated value, or render via "
                    "a Jinja2 template with autoescape enabled.",
        compliance=_cm(cwe=["79"], owasp_top10=["A03:2021"], owasp_asvs=["V5.3.3"],
                       nist_800_53=["SI-10"], cwe_top25=True),
        confidence=Confidence.MEDIUM,
        negative=[r"html\.escape", r"\bescape\(", r"#\s*nosec"],
        effort="0.5d",
    ),
    PatternRule(
        id="AGT-XSS-002",
        title="dangerouslySetInnerHTML used in React",
        severity=Severity.MEDIUM,
        pattern=r"dangerouslySetInnerHTML",
        description="Bypasses React's automatic escaping. If the value is user- or "
                    "API-derived this is stored/reflected XSS.",
        remediation="Render as text, or sanitise with DOMPurify before injecting.",
        compliance=_cm(cwe=["79"], owasp_top10=["A03:2021"], owasp_asvs=["V5.3.3"],
                       nist_800_53=["SI-10"], cwe_top25=True),
        extensions=[".js", ".jsx", ".ts", ".tsx"],
        confidence=Confidence.MEDIUM,
        negative=[r"DOMPurify", r"sanitize"],
        effort="0.5d",
    ),
    PatternRule(
        id="AGT-SSRF-001",
        title="Outbound HTTP request to a non-literal URL",
        severity=Severity.MEDIUM,
        pattern=r"(?:requests|httpx|aiohttp)\.(?:get|post|put|delete|patch|request)\s*\(\s*(?!['\"])(?!self\.)[A-Za-z_][A-Za-z0-9_\.]*\s*[,)]",
        description="The request target is a variable. If it is request-derived and not "
                    "validated against an allow-list, this is SSRF.",
        remediation="Validate the URL against an allow-list of hosts/schemes before the "
                    "call; reject internal/link-local addresses.",
        compliance=_cm(cwe=["918"], owasp_top10=["A10:2021"],
                       owasp_api_top10=["API7:2023"], owasp_asvs=["V12.6.1"],
                       nist_800_53=["SC-7"], cwe_top25=True),
        confidence=Confidence.LOW,
        negative=[r"BASE_URL", r"_URL\b", r"settings\.", r"#\s*nosec"],
        effort="1d",
    ),
    PatternRule(
        id="AGT-PATH-001",
        title="open() called with a non-literal path",
        severity=Severity.MEDIUM,
        pattern=r"(?<![\w.])open\s*\(\s*(?!['\"])(?:f['\"]|[A-Za-z_][A-Za-z0-9_\.\[\]]*\s*[,)])",
        description="File path is a variable or f-string. If any component is "
                    "request-derived this is path traversal.",
        remediation="Resolve the path and assert containment with "
                    "os.path.commonpath([base, resolved]) == base; prefer opaque "
                    "server-generated names (UUIDs).",
        compliance=_cm(cwe=["22"], owasp_top10=["A01:2021"], owasp_asvs=["V12.3.1"],
                       nist_800_53=["AC-3"], cwe_top25=True),
        confidence=Confidence.LOW,
        negative=[r"commonpath", r"__file__", r"Path\(__file__", r"#\s*nosec",
                  r"encoding\s*=", r"\.parent"],
        effort="0.5d",
    ),
    PatternRule(
        id="AGT-DESER-001",
        title="Unsafe deserialization (pickle / yaml.load / eval / exec)",
        severity=Severity.CRITICAL,
        pattern=r"(?:pickle\.loads?|cPickle\.loads?|yaml\.load\s*\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)|(?<![\w.])eval\s*\(|(?<![\w.])exec\s*\()",
        description="Deserialising or evaluating untrusted input yields remote code "
                    "execution.",
        remediation="Use json for data, yaml.safe_load for YAML, and remove eval/exec "
                    "entirely; if dynamic dispatch is needed use an explicit mapping.",
        compliance=_cm(cwe=["502", "94"], owasp_top10=["A08:2021"],
                       owasp_asvs=["V5.5.1"], nist_800_53=["SI-10"], cwe_top25=True),
        confidence=Confidence.HIGH,
        negative=[r"#\s*nosec", r"ast\.literal_eval"],
        effort="1d",
    ),
    PatternRule(
        id="AGT-TLS-001",
        title="TLS certificate verification disabled",
        severity=Severity.HIGH,
        pattern=r"verify\s*=\s*False|CERT_NONE|rejectUnauthorized\s*:\s*false",
        description="Disabling certificate verification makes the connection trivially "
                    "interceptable.",
        remediation="Remove verify=False; supply the correct CA bundle instead.",
        compliance=_cm(cwe=["295"], owasp_top10=["A02:2021"], owasp_asvs=["V9.2.1"],
                       nist_800_53=["SC-8", "SC-13"]),
        extensions=[".py", ".js", ".ts"],
        confidence=Confidence.HIGH,
        effort="trivial",
    ),
    PatternRule(
        id="AGT-CRYPTO-001",
        title="Weak hash algorithm used",
        severity=Severity.MEDIUM,
        pattern=r"hashlib\.(?:md5|sha1)\s*\(",
        description="MD5/SHA-1 are broken for any security purpose.",
        remediation="Use SHA-256+ for integrity; for passwords use bcrypt/argon2. If the "
                    "use is non-security (e.g. a cache key), pass usedforsecurity=False.",
        compliance=_cm(cwe=["327"], owasp_top10=["A02:2021"], owasp_asvs=["V6.2.2"],
                       nist_800_53=["SC-13"]),
        confidence=Confidence.MEDIUM,
        negative=[r"usedforsecurity\s*=\s*False", r"#\s*nosec"],
        effort="0.5d",
    ),
]

# ── Pack: Authentication / Session ────────────────────────────────────────────

AUTH_RULES: List[PatternRule] = [
    PatternRule(
        id="AGT-JWT-001",
        title="JWT signature verification disabled",
        severity=Severity.CRITICAL,
        pattern=r"verify_signature[\"']?\s*:\s*False|verify\s*=\s*False\s*\)[^)]*jwt|jwt\.decode\s*\([^)]*options\s*=\s*\{[^}]*verify",
        description="A token whose signature is not verified can be forged by anyone.",
        remediation="Always verify the signature; pin the expected algorithm and validate "
                    "iss/aud/exp.",
        compliance=_cm(cwe=["347"], owasp_top10=["A07:2021"],
                       owasp_api_top10=["API2:2023"], owasp_asvs=["V3.5.3"],
                       nist_800_53=["IA-2", "SC-13"]),
        confidence=Confidence.HIGH,
        effort="1d",
    ),
    PatternRule(
        id="AGT-JWT-002",
        title="JWT 'none' algorithm accepted",
        severity=Severity.CRITICAL,
        pattern=r"algorithms\s*=\s*\[[^\]]*[\"']none[\"']|alg[\"']?\s*:\s*[\"']none[\"']",
        description="The 'none' algorithm means unsigned tokens are accepted.",
        remediation="Pin algorithms to an explicit allow-list that excludes 'none'.",
        compliance=_cm(cwe=["327", "347"], owasp_top10=["A02:2021"],
                       owasp_api_top10=["API2:2023"], owasp_asvs=["V3.5.3"],
                       nist_800_53=["SC-13"]),
        confidence=Confidence.HIGH,
        effort="trivial",
    ),
    PatternRule(
        id="AGT-JWT-003",
        title="JWT encoded without an expiration claim",
        severity=Severity.MEDIUM,
        pattern=r"jwt\.encode\s*\(",
        description="Tokens minted without an 'exp' claim never expire, so a leaked token "
                    "is valid forever. Verify the payload sets exp.",
        remediation="Always include exp (and iat); keep access-token lifetime short and "
                    "use refresh rotation.",
        compliance=_cm(cwe=["613"], owasp_top10=["A07:2021"], owasp_asvs=["V3.3.1"],
                       nist_800_53=["AC-12"]),
        confidence=Confidence.LOW,
        negative=[r"\bexp\b"],
        effort="0.5d",
    ),
    PatternRule(
        id="AGT-SECRET-001",
        title="Hardcoded credential or signing secret",
        severity=Severity.CRITICAL,
        pattern=r"(?i)(?:SECRET_KEY|JWT_SECRET|API_KEY|PASSWORD|CLIENT_SECRET|TOKEN)\s*=\s*[\"'][^\"'{}$][^\"']{7,}[\"']",
        description="A credential is embedded in source. Anyone with repository read "
                    "access holds the secret, and rotation requires a code change.",
        remediation="Load from the environment or Azure Key Vault; rotate any value that "
                    "was committed.",
        compliance=_cm(cwe=["798"], owasp_top10=["A07:2021"], owasp_asvs=["V2.10.4"],
                       nist_800_53=["IA-5", "SC-12"], cwe_top25=True),
        extensions=[".py", ".js", ".ts", ".jsx", ".tsx"],
        confidence=Confidence.MEDIUM,
        # os.getenv(...) defaults, placeholders and test fixtures are not real secrets.
        negative=[r"os\.getenv", r"os\.environ", r"getenv\(", r"process\.env",
                  r"(?i)your[-_]?", r"(?i)change[-_]?me", r"(?i)placeholder",
                  r"(?i)example", r"(?i)dummy", r"(?i)fake", r"\.\.\.", r"xxx",
                  r"(?i)test", r"#\s*nosec"],
        path_exclude=r"(?i)(test|spec|fixture|mock|\.example)",
        effort="0.5d+rotate",
    ),
]

# ── Pack: Healthcare (HIPAA / TEFCA) ──────────────────────────────────────────

_PHI_TERMS = (r"ssn|social_security|date_of_birth|dob|mrn|medical_record|patient_name|"
              r"first_name|last_name|diagnosis|icd10|npi|member_id|subscriber_id|"
              r"patient_id|phi|health_plan|beneficiary")

HEALTHCARE_RULES: List[PatternRule] = [
    PatternRule(
        id="AGT-PHI-001",
        title="Potential PHI written to application logs without masking",
        severity=Severity.HIGH,
        pattern=rf"(?i)log(?:ger)?\.(?:debug|info|warning|error|critical|exception)\s*\([^)]*\b(?:{_PHI_TERMS})\b",
        description="A log statement references a PHI field. Application logs are "
                    "routinely shipped to third-party sinks and retained outside the "
                    "HIPAA boundary; §164.312(b) requires audit controls over PHI, and "
                    "unmasked PHI in logs is a disclosure.",
        remediation="Mask or omit the identifier before logging (log an opaque internal "
                    "ID instead of the identifier itself).",
        compliance=_cm(cwe=["532"], owasp_top10=["A09:2021"], owasp_asvs=["V7.1.1"],
                       nist_800_53=["AU-3", "AU-9"],
                       hipaa=["164.312(b)", "164.514(b)(2)"]),
        confidence=Confidence.LOW,
        negative=[r"(?i)mask|redact|deidentif|de_identif|hash|sanitiz|scrub|\*{3,}"],
        effort="0.5d",
    ),
    PatternRule(
        id="AGT-PHI-002",
        title="Identifier echoed in an error response",
        severity=Severity.MEDIUM,
        pattern=rf"(?i)(?:HTTPException|JSONResponse|raise\s+\w*Error)\s*\([^)]*(?:detail|message|content)\s*=\s*f?[\"'][^\"']*\{{?\s*(?:{_PHI_TERMS})",
        description="An NPI/PHI identifier is interpolated into a client-visible error. "
                    "Error bodies are frequently logged and forwarded, widening exposure "
                    "beyond the authorised recipient.",
        remediation="Return a generic error to the client and log the correlating "
                    "identifier internally only.",
        compliance=_cm(cwe=["209"], owasp_top10=["A09:2021"],
                       owasp_api_top10=["API3:2023"], owasp_asvs=["V7.4.1"],
                       nist_800_53=["SI-11"], hipaa=["164.312(a)(1)", "164.502(b)"]),
        confidence=Confidence.LOW,
        negative=[r"(?i)mask|redact|not found|invalid|missing"],
        effort="0.5d",
    ),
    PatternRule(
        id="AGT-FHIR-001",
        title="FHIR resource route without an obvious access control check",
        severity=Severity.HIGH,
        # Must be an actual route DECORATOR, and resource names are matched
        # case-SENSITIVELY in FHIR's PascalCase. The earlier version matched any line
        # containing "/fhir/" or a resource word case-insensitively, which produced
        # 54/54 false positives here: it fired on the canonical identifier string
        # "http://hl7.org/fhir/sid/us-npi" in constants and on the ordinary English
        # word in the bulletin route "/coverage/{agency_id}". Neither is a FHIR
        # endpoint. Requiring the decorator plus PascalCase removes both classes.
        pattern=r"@(?:router|app)\.(?:get|post|put|patch|delete)\s*\(\s*[\"'][^\"']*"
                r"/(?:fhir|FHIR|Patient|Observation|Encounter|Condition|"
                r"MedicationRequest|DocumentReference|Coverage|Claim|AllergyIntolerance|"
                r"Immunization|DiagnosticReport)\b",
        description="A FHIR resource route is defined. TEFCA/HIPAA require that access "
                    "is authorised per-resource, not merely per-endpoint; verify an "
                    "ownership/purpose-of-use check exists.",
        remediation="Enforce authentication plus a per-resource authorisation check "
                    "(patient compartment / purpose of use) before returning data.",
        compliance=_cm(cwe=["285", "639"], owasp_top10=["A01:2021"],
                       owasp_api_top10=["API1:2023"], owasp_asvs=["V4.2.1"],
                       nist_800_53=["AC-3", "AC-4"],
                       hipaa=["164.312(a)(1)", "164.308(a)(4)"]),
        confidence=Confidence.LOW,
        negative=[r"(?i)Depends\(|require_role|get_current_user|authoriz|authent"],
        effort="2-3d",
    ),
    PatternRule(
        id="AGT-PHI-003",
        title="PHI identifier passed in a URL query string",
        severity=Severity.MEDIUM,
        pattern=rf"(?i)[\?&](?:{_PHI_TERMS})=",
        description="Query strings are recorded in server logs, proxies and browser "
                    "history, so PHI in a URL is disclosed well beyond the request.",
        remediation="Move the identifier into the request body or a path segment that is "
                    "an opaque server-generated ID.",
        compliance=_cm(cwe=["598"], owasp_top10=["A09:2021"], owasp_asvs=["V8.3.1"],
                       nist_800_53=["SC-8"], hipaa=["164.312(e)(1)"]),
        extensions=[".py", ".js", ".jsx", ".ts", ".tsx"],
        confidence=Confidence.LOW,
        effort="0.5d",
    ),
]

# ── Pack: Azure ───────────────────────────────────────────────────────────────

AZURE_RULES: List[PatternRule] = [
    PatternRule(
        id="AGT-AZ-001",
        title="Hardcoded Azure/DB connection string",
        severity=Severity.CRITICAL,
        pattern=r"(?i)(?:DefaultEndpointsProtocol\s*=|AccountKey\s*=|SharedAccessSignature\s*=|"
                r"Server\s*=\s*tcp:|postgres(?:ql)?://[^\s\"']*:[^\s\"'@]+@|"
                r"mongodb(?:\+srv)?://[^\s\"']*:[^\s\"'@]+@)",
        description="A connection string with an embedded credential appears in source.",
        remediation="Use a Key Vault reference resolved by the app's managed identity, or "
                    "Entra token auth; rotate any credential that was committed.",
        compliance=_cm(cwe=["798"], owasp_top10=["A05:2021"], owasp_asvs=["V2.10.4"],
                       nist_800_53=["IA-5", "SC-12", "SC-28"], cwe_top25=True),
        extensions=[".py", ".js", ".ts", ".json", ".bicep", ".yml", ".yaml"],
        confidence=Confidence.MEDIUM,
        negative=[r"(?i)<user>|<host>|<password>|\{\{|\$\{|your[-_]|example|placeholder|"
                  r"changeme|xxx|getenv|os\.environ|process\.env"],
        path_exclude=r"(?i)(\.example|test|spec|fixture|mock)",
        effort="0.5d+rotate",
    ),
    PatternRule(
        id="AGT-AZ-002",
        title="Unresolved Key Vault reference committed as a literal",
        severity=Severity.HIGH,
        pattern=r"@Microsoft\.KeyVault\s*\(",
        description="An @Microsoft.KeyVault(...) reference in source or config is only "
                    "resolved by App Service at runtime. If it fails to resolve, App "
                    "Service passes the literal string through to the application - which "
                    "is how a 71-character unresolved reference once satisfied a 64-char "
                    "SECRET_KEY length check (Phase 0 finding SEC-01).",
        remediation="Keep KV references in App Service settings only, and fail fast at "
                    "startup if a required secret still starts with '@Microsoft.KeyVault('.",
        compliance=_cm(cwe=["1188", "798"], owasp_top10=["A05:2021"],
                       owasp_asvs=["V2.10.4"], nist_800_53=["IA-5", "CM-6"]),
        extensions=[".py", ".json", ".bicep", ".yml", ".yaml", ".ts", ".js"],
        confidence=Confidence.MEDIUM,
        negative=[r"startswith|_KV_REFERENCE_PREFIX|_assert_resolved"],
        effort="0.5d",
    ),
    PatternRule(
        id="AGT-AZ-003",
        title="Azure SDK client constructed without Managed Identity",
        severity=Severity.MEDIUM,
        pattern=r"(?:BlobServiceClient|SecretClient|QueueClient|TableServiceClient|"
                r"CosmosClient)\s*\([^)]*(?:connection_string|credential\s*=\s*[\"'])",
        description="An Azure client is built from a connection string or literal "
                    "credential rather than DefaultAzureCredential/Managed Identity.",
        remediation="Use DefaultAzureCredential() with a system-assigned managed identity "
                    "so no secret exists to leak or rotate.",
        compliance=_cm(cwe=["798"], owasp_top10=["A07:2021"], owasp_asvs=["V2.10.4"],
                       nist_800_53=["IA-5", "AC-3"]),
        confidence=Confidence.MEDIUM,
        negative=[r"DefaultAzureCredential|ManagedIdentityCredential"],
        effort="1d",
    ),
]

RULE_PACKS = {
    "owasp": OWASP_RULES,
    "auth": AUTH_RULES,
    "healthcare": HEALTHCARE_RULES,
    "azure": AZURE_RULES,
}


def all_rules(packs: Optional[List[str]] = None) -> List[PatternRule]:
    names = packs or list(RULE_PACKS)
    out: List[PatternRule] = []
    for n in names:
        out.extend(RULE_PACKS.get(n, []))
    return out
