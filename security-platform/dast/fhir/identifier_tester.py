"""FHIR-ID-001..010 - healthcare identifier validation.

The NPI check digit is not optional trivia: CMS defines it (Luhn over the number
prefixed with 80840), and it is the only purely local way to reject a mistyped or
fabricated NPI before it reaches NPPES. A registry that stores unvalidated NPIs will
happily accept 1234567890 and carry it into TEFCA exchange.

This module implements the algorithm as a REFERENCE, self-tests it against known-good
and known-bad values so the test itself is trustworthy, then checks whether the
backend implements an equivalent check at all.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from dast.results import Outcome
from dast.static_base import StaticTester

CAT = "fhir_identifier"
HIPAA_INT = ["164.312(c)(1)"]

# Real, publicly-documented NPIs used as positive controls (CMS test/example values).
# Verified against the canonical CMS worked example (1234567893). Two values that
# appear in the backend's own mock data (1023011403, 1750384995) were REMOVED from
# this list after testing showed they fail the check digit - see FHIR-ID-002b.
VALID_NPIS = ["1234567893", "1003000126", "1245319599", "1073514055"]
INVALID_NPIS = ["1234567890", "0003000126", "1003000127", "999999999", "12345678901"]


def npi_is_valid(npi: str) -> bool:
    """CMS NPI check-digit validation: Luhn over '80840' + first 9 digits."""
    n = (npi or "").strip()
    if not re.fullmatch(r"\d{10}", n):
        return False
    if n[0] not in "12":          # NPIs currently begin 1 or 2
        return False
    payload = "80840" + n[:9]
    total, double = 0, True       # rightmost payload digit is doubled
    for ch in reversed(payload):
        d = int(ch)
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return (10 - (total % 10)) % 10 == int(n[9])


class IdentifierTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        # FHIR-ID-001/002 - self-test the reference implementation first. A validator
        # that has not been shown correct cannot be used to judge someone else's.
        good = [n for n in VALID_NPIS if npi_is_valid(n)]
        bad_rejected = [n for n in INVALID_NPIS if not npi_is_valid(n)]
        self_ok = len(good) == len(VALID_NPIS) and len(bad_rejected) == len(INVALID_NPIS)
        self.s.record(
            "FHIR-ID-001", CAT, "Reference NPI check-digit implementation is correct",
            outcome=Outcome.PASS if self_ok else Outcome.ERROR,
            expected=f"{len(VALID_NPIS)} known-valid accepted, "
                     f"{len(INVALID_NPIS)} known-invalid rejected",
            observed=f"{len(good)}/{len(VALID_NPIS)} valid accepted; "
                     f"{len(bad_rejected)}/{len(INVALID_NPIS)} invalid rejected",
            finding="" if self_ok else
                    "The reference implementation is wrong; downstream conclusions "
                    "about the backend cannot be trusted.",
            severity="info", confidence="high", cwe=["345"], nist=["SI-10"],
            notes="Positive control for the test harness itself.")

        # FHIR-ID-002 - does the BACKEND validate at all?
        blob = ""
        for rel in ("app/tefca_registry/schemas.py", "app/tefca_registry/models.py",
                    "app/tefca_registry/csv_import.py",
                    "app/tefca_registry/fhir_import.py",
                    "app/Tefca/connectors.py", "app/Tefca/validation_engine.py"):
            blob += self.s.read(rel)
        has_luhn = bool(re.search(r"(?i)luhn|check_?digit|80840", blob))
        has_shape = bool(re.search(r"(?i)\\d\{10\}|len\(\s*npi\s*\)\s*==\s*10|"
                                   r"regex\s*=\s*r?['\"]\^\\\\d\{10\}", blob))
        self.s.record(
            "FHIR-ID-002", CAT, "Backend validates the NPI check digit (Luhn/80840)",
            outcome=Outcome.PASS if has_luhn else Outcome.FAIL,
            expected="A Luhn/check-digit routine applied to NPI values on ingest",
            observed=f"check-digit logic {'found' if has_luhn else 'NOT found'}; "
                     f"10-digit shape check {'found' if has_shape else 'not found'}",
            finding="" if has_luhn else
                    "No NPI check-digit validation exists anywhere in the registry or "
                    "TEFCA modules. A mistyped or fabricated NPI such as 1234567890 "
                    "would be stored and carried into TEFCA exchange; only a live NPPES "
                    "lookup would catch it, and that is a network call that can be "
                    "skipped, rate-limited, or unavailable.",
            severity="medium" if not has_luhn else "info", confidence="high",
            source="app/tefca_registry/*, app/Tefca/*",
            owasp=["A04:2021"], owasp_api=["API6:2023"], cwe=["345", "20"],
            nist=["SI-10"], hipaa=HIPAA_INT, asvs=["V5.1.4"],
            remediation="Validate the check digit locally on ingest (Luhn over "
                        "'80840' + the first 9 digits) before any NPPES call, and "
                        "reject with 422 on failure.")

        # FHIR-ID-006 - mandatory identifiers enforced by the schema
        mandatory = bool(re.search(r"(?i)(tefca_?id|hcid)[^\n]{0,80}(Field\(\s*\.\.\.|"
                                   r"nullable\s*=\s*False)", blob))
        self.s.record(
            "FHIR-ID-006", CAT, "Mandatory TEFCA identifiers are non-nullable",
            outcome=Outcome.PASS if mandatory else Outcome.WARN,
            expected="TEFCAID / HCID declared required (Field(...) or nullable=False)",
            observed=f"mandatory declaration {'found' if mandatory else 'not found'}",
            finding="" if mandatory else
                    "Mandatory TEFCA identifiers do not appear to be enforced as "
                    "required, so an entity could be created without them.",
            severity="medium", source="app/tefca_registry/schemas.py",
            owasp=["A04:2021"], cwe=["20"], nist=["SI-10"], hipaa=HIPAA_INT,
            remediation="Declare the identifiers required in both the Pydantic schema "
                        "and the database model.")

        # FHIR-ID-007 - leading zeros must survive (a classic string/int bug)
        zero_led = "0000000006"
        preserved = not npi_is_valid(zero_led)     # also must not crash
        int_coerced = bool(re.search(r"(?i)int\(\s*npi|npi\s*:\s*int", blob))
        self.s.record(
            "FHIR-ID-007", CAT, "Identifiers handled as strings (leading zeros safe)",
            outcome=Outcome.FAIL if int_coerced else Outcome.PASS,
            expected="NPI/HCID typed as str, never coerced to int",
            observed=f"integer coercion {'FOUND' if int_coerced else 'not found'}",
            finding="Identifier appears to be coerced to an integer, which silently "
                    "destroys leading zeros and changes the value." if int_coerced else "",
            severity="medium" if int_coerced else "info",
            source="app/tefca_registry/schemas.py", cwe=["704"], nist=["SI-10"],
            hipaa=HIPAA_INT,
            remediation="Type all healthcare identifiers as strings end to end.")

        # FHIR-ID-008 - identifier system URIs validated
        sys_uri = bool(re.search(r"hl7\.org/fhir/sid/us-npi", blob))
        self.s.record(
            "FHIR-ID-008", CAT, "Canonical identifier system URIs used",
            outcome=Outcome.PASS if sys_uri else Outcome.WARN,
            expected="http://hl7.org/fhir/sid/us-npi referenced for NPI identifiers",
            observed=f"canonical NPI system URI {'found' if sys_uri else 'not found'}",
            finding="" if sys_uri else
                    "The canonical NPI system URI was not found; identifiers may be "
                    "stored without a system, making them ambiguous across namespaces.",
            severity="low", source="app/Tefca/connectors.py", cwe=["20"],
            nist=["SI-10"], remediation="Always pair an identifier value with its "
                                        "canonical system URI.")

        # FHIR-ID-002b - are the identifiers the system ships with even valid?
        import re as _re
        mock = self.s.read("app/Tefca/mock_data.py")
        npis = sorted(set(_re.findall(r'"([12]\d{9})"', mock)))
        bad = [n for n in npis if not npi_is_valid(n)]
        self.s.record(
            "FHIR-ID-002b", CAT,
            "Bundled TEFCA sample/mock NPIs carry valid check digits",
            outcome=Outcome.PASS if (npis and not bad) else
                    (Outcome.SKIP if not npis else Outcome.FAIL),
            expected="Every NPI in bundled sample data passes the CMS check digit",
            observed=f"{len(bad)} of {len(npis)} sampled NPIs fail the check digit",
            finding="" if (not bad or not npis) else
                    f"{len(bad)} of {len(npis)} NPIs in app/Tefca/mock_data.py have an "
                    f"INVALID check digit (e.g. {', '.join(bad[:3])}). Combined with "
                    f"FHIR-ID-002 (no check-digit validation anywhere), this is "
                    f"self-reinforcing: the system neither validates NPIs nor ships "
                    f"valid ones, so any regression in NPI handling would be invisible "
                    f"in demos and tests.",
            severity="low" if bad else "info", confidence="high",
            source="app/Tefca/mock_data.py", owasp=["A04:2021"], cwe=["345", "1188"],
            nist=["SI-10"], hipaa=HIPAA_INT,
            remediation="Regenerate sample NPIs with valid check digits so test data "
                        "exercises the same validation path production data will.")

        for tid, nm in (
            ("FHIR-ID-003", "Valid TEFCAID format accepted"),
            ("FHIR-ID-004", "Valid HCID format accepted"),
            ("FHIR-ID-005", "Duplicate NPI across entities detected"),
            ("FHIR-ID-009", "Cross-reference validation (NPI vs NPPES)"),
            ("FHIR-ID-010", "CCN format validation"),
        ):
            self.s.stub(tid, CAT, nm,
                        "requires the TEFCA registry backend on a live target "
                        "(routes 404 on dev)",
                        cwe=["20"], nist=["SI-10"], hipaa=HIPAA_INT)
