"""
DocuAction TEFCA Review Protocol
Validation Engine + Evidence Record Generator

Implements:
  - Field mapping and name normalization
  - 4-bucket discrepancy classification
  - Confidence scoring (0.00 - 1.00)
  - Tier routing (Tier 1 auto / Tier 2 analyst / Tier 3 SME)
  - 5-element evidence record generation
"""

import re
import uuid
import hashlib
from datetime import datetime
from typing import Any
from .connectors import SourceResult


# ─── Finding Codes ────────────────────────────────────────────────────────────

class FindingCode:
    # Bucket 4 — Non-Compliant
    NPI_NOT_FOUND              = "NPI_NOT_FOUND"
    NPI_INACTIVE               = "NPI_INACTIVE"
    NPI_DEACTIVATED            = "NPI_DEACTIVATED"
    LEIE_ACTIVE_EXCLUSION      = "LEIE_ACTIVE_EXCLUSION"
    SAM_ACTIVE_DEBARMENT       = "SAM_ACTIVE_DEBARMENT"
    PECOS_PAYMENT_SUSPENSION   = "PECOS_PAYMENT_SUSPENSION"
    NAME_UNRESOLVABLE          = "NAME_UNRESOLVABLE"

    # Bucket 3 — Inexplicable
    NAME_COMPLETELY_DIFFERENT  = "NAME_COMPLETELY_DIFFERENT"
    ADDRESS_STATE_CONFLICT     = "ADDRESS_STATE_CONFLICT"
    ENTITY_TYPE_MISMATCH       = "ENTITY_TYPE_MISMATCH"
    NPI_MISSING                = "NPI_MISSING"
    SAM_REGISTRATION_LAPSED    = "SAM_REGISTRATION_LAPSED"
    SOURCE_CONFLICT            = "SOURCE_CONFLICT"
    HIERARCHY_MISMATCH         = "HIERARCHY_MISMATCH"

    # Bucket 2 — Minor/Administrative
    NAME_ABBREVIATION_DIFF     = "NAME_ABBREVIATION_DIFF"
    NAME_PUNCTUATION_DIFF      = "NAME_PUNCTUATION_DIFF"
    NAME_DBA_VS_LEGAL          = "NAME_DBA_VS_LEGAL"
    ADDRESS_UNIT_DIFF          = "ADDRESS_UNIT_DIFF"
    ADDRESS_FORMAT_DIFF        = "ADDRESS_FORMAT_DIFF"
    PHONE_DISCREPANCY          = "PHONE_DISCREPANCY"
    ZIP_FORMAT_DIFF            = "ZIP_FORMAT_DIFF"
    LEIE_HISTORICAL_RESOLVED   = "LEIE_HISTORICAL_RESOLVED"
    MINOR_CORP_SUFFIX_DIFF     = "MINOR_CORP_SUFFIX_DIFF"

    # Bucket 1 — No Discrepancy
    NO_DISCREPANCY             = "NO_DISCREPANCY"


FINDING_DESCRIPTIONS = {
    FindingCode.NPI_NOT_FOUND: "NPI submitted does not exist in NPPES registry",
    FindingCode.NPI_INACTIVE: "NPI found in NPPES but status is inactive or deactivated",
    FindingCode.NPI_DEACTIVATED: "NPI was deactivated in NPPES — organization no longer enrolled",
    FindingCode.LEIE_ACTIVE_EXCLUSION: "Entity has active OIG LEIE exclusion with no reinstatement",
    FindingCode.SAM_ACTIVE_DEBARMENT: "Entity has active SAM.gov debarment or suspension",
    FindingCode.PECOS_PAYMENT_SUSPENSION: "CMS PECOS active payment suspension flag present",
    FindingCode.NAME_UNRESOLVABLE: "Legal name cannot be matched to any authoritative source record",
    FindingCode.NAME_COMPLETELY_DIFFERENT: "NPI found but under completely different organization name",
    FindingCode.ADDRESS_STATE_CONFLICT: "Different state across two or more authoritative sources",
    FindingCode.ENTITY_TYPE_MISMATCH: "Submitted entity type does not match NPPES taxonomy classification",
    FindingCode.NPI_MISSING: "No NPI provided in RCE Directory submission",
    FindingCode.SAM_REGISTRATION_LAPSED: "SAM.gov registration expired without renewal on record",
    FindingCode.SOURCE_CONFLICT: "Conflicting legal names across three or more authoritative sources",
    FindingCode.HIERARCHY_MISMATCH: "Organizational hierarchy in submission conflicts with OneKey data",
    FindingCode.NAME_ABBREVIATION_DIFF: "Name difference attributable to abbreviation (St./Saint, Corp./Corporation)",
    FindingCode.NAME_PUNCTUATION_DIFF: "Name difference attributable to punctuation only",
    FindingCode.NAME_DBA_VS_LEGAL: "DBA name submitted vs legal name in NPPES — trade name variation",
    FindingCode.ADDRESS_UNIT_DIFF: "Address difference attributable to suite/floor/unit number only",
    FindingCode.ADDRESS_FORMAT_DIFF: "Address formatting difference — same location, different format",
    FindingCode.PHONE_DISCREPANCY: "Phone number differs from NPPES — likely data entry error",
    FindingCode.ZIP_FORMAT_DIFF: "ZIP code format difference (5-digit vs ZIP+4)",
    FindingCode.LEIE_HISTORICAL_RESOLVED: "Historical LEIE exclusion found but reinstatement confirmed",
    FindingCode.MINOR_CORP_SUFFIX_DIFF: "Minor corporate suffix difference (LLC vs Group LLC)",
    FindingCode.NO_DISCREPANCY: "All validation checks passed within tolerance thresholds",
}


# ─── Name Normalizer ─────────────────────────────────────────────────────────

class NameNormalizer:
    """Normalizes healthcare organization names for comparison."""

    ABBREVIATIONS = {
        "st.": "saint", "st ": "saint ", "dr.": "doctor", "mt.": "mount",
        "corp.": "corporation", "corp ": "corporation ", "inc.": "incorporated",
        "inc ": "incorporated ", "llc": "limited liability company",
        "l.l.c.": "limited liability company", "llp": "limited liability partnership",
        "p.c.": "professional corporation", "p.a.": "professional association",
        "healthcare": "health care", "med.": "medical", "hosp.": "hospital",
        "ctr.": "center", "ctr ": "center ", "mgmt.": "management",
    }

    REMOVALS = re.compile(
        r"\b(the|a|an|of|and|&|for|in|at|by|to|dba|aka)\b", re.IGNORECASE
    )
    PUNCTUATION = re.compile(r"[^\w\s]")
    SPACES = re.compile(r"\s+")

    def normalize(self, name: str) -> str:
        """Full normalization pipeline."""
        if not name:
            return ""
        n = name.lower().strip()
        for abbr, full in self.ABBREVIATIONS.items():
            n = n.replace(abbr, full)
        n = self.PUNCTUATION.sub(" ", n)
        n = self.REMOVALS.sub(" ", n)
        n = self.SPACES.sub(" ", n).strip()
        return n

    def levenshtein(self, a: str, b: str) -> int:
        """Compute Levenshtein edit distance between two strings."""
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            curr = [i]
            for j, cb in enumerate(b, 1):
                curr.append(min(
                    prev[j] + 1,
                    curr[j - 1] + 1,
                    prev[j - 1] + (0 if ca == cb else 1)
                ))
            prev = curr
        return prev[-1]

    def similarity_score(self, name1: str, name2: str) -> float:
        """Return 0.0 (no similarity) to 1.0 (identical) after normalization."""
        n1, n2 = self.normalize(name1), self.normalize(name2)
        if not n1 and not n2:
            return 1.0
        if not n1 or not n2:
            return 0.0
        max_len = max(len(n1), len(n2))
        dist = self.levenshtein(n1, n2)
        return max(0.0, 1.0 - dist / max_len)


# ─── Validation Engine ───────────────────────────────────────────────────────

class ValidationEngine:
    """
    Core validation logic.
    Takes entity + all source results → produces classification + findings.
    """

    def __init__(self):
        self.normalizer = NameNormalizer()

    def validate(
        self,
        entity: dict,
        source_results: dict[str, SourceResult]
    ) -> dict:
        """
        Run full validation for an entity.
        Returns: {bucket, bucket_label, confidence, finding_codes, field_comparisons}
        """
        findings: list[str] = []
        field_comparisons: list[dict] = []
        deductions = 0.0

        npi = self._extract_npi(entity)
        submitted_name = entity.get("name", "")
        submitted_address = (entity.get("address") or [{}])[0]
        submitted_phone = self._extract_phone(entity)

        # ── NPPES Validation ──────────────────────────────────────────────────
        nppes = source_results.get("nppes")
        if nppes and nppes.success:
            nppes_data = nppes.data
            if not npi:
                findings.append(FindingCode.NPI_MISSING)
                deductions += 0.25
                field_comparisons.append({
                    "field": "npi",
                    "submitted": None,
                    "nppes": None,
                    "result": "MISSING",
                    "finding": FindingCode.NPI_MISSING
                })
            elif not nppes_data.get("found"):
                findings.append(FindingCode.NPI_NOT_FOUND)
                deductions += 0.40
                field_comparisons.append({
                    "field": "npi",
                    "submitted": npi,
                    "nppes": "NOT_FOUND",
                    "result": "NOT_FOUND",
                    "finding": FindingCode.NPI_NOT_FOUND
                })
            else:
                # NPI status check
                npi_status = nppes_data.get("status", "ACTIVE").upper()
                if npi_status in ("DEACTIVATED",):
                    findings.append(FindingCode.NPI_DEACTIVATED)
                    deductions += 0.40
                    field_comparisons.append({
                        "field": "npi_status",
                        "submitted": "ACTIVE (claimed)",
                        "nppes": npi_status,
                        "result": "NON_COMPLIANT",
                        "finding": FindingCode.NPI_DEACTIVATED
                    })
                elif npi_status not in ("ACTIVE", ""):
                    findings.append(FindingCode.NPI_INACTIVE)
                    deductions += 0.40

                # Name comparison
                nppes_name = nppes_data.get("legal_name", "")
                if nppes_name:
                    sim = self.normalizer.similarity_score(submitted_name, nppes_name)
                    name_finding, name_deduction = self._classify_name_similarity(
                        sim, submitted_name, nppes_name
                    )
                    if name_finding:
                        findings.append(name_finding)
                        deductions += name_deduction
                    field_comparisons.append({
                        "field": "legal_name",
                        "submitted": submitted_name,
                        "nppes": nppes_name,
                        "similarity": round(sim, 3),
                        "result": "MATCH" if not name_finding else "MISMATCH",
                        "finding": name_finding
                    })

                # Address comparison
                nppes_addrs = nppes_data.get("addresses", [])
                if nppes_addrs:
                    primary = next(
                        (a for a in nppes_addrs if a.get("address_purpose") == "LOCATION"),
                        nppes_addrs[0]
                    )
                    addr_finding, addr_deduction = self._classify_address(
                        submitted_address, primary
                    )
                    if addr_finding:
                        findings.append(addr_finding)
                        deductions += addr_deduction
                    field_comparisons.append({
                        "field": "address",
                        "submitted": submitted_address,
                        "nppes": primary,
                        "result": "MATCH" if not addr_finding else "MISMATCH",
                        "finding": addr_finding
                    })

                # Entity type check
                nppes_enum = nppes_data.get("enumeration_type", "")
                entity_type = self._extract_entity_type(entity)
                if nppes_enum == "NPI-1" and entity_type in ("PARTICIPANT", "SUBPARTICIPANT"):
                    findings.append(FindingCode.ENTITY_TYPE_MISMATCH)
                    deductions += 0.25
                    field_comparisons.append({
                        "field": "entity_type",
                        "submitted": entity_type,
                        "nppes": f"Individual Provider (NPI-1)",
                        "result": "MISMATCH",
                        "finding": FindingCode.ENTITY_TYPE_MISMATCH
                    })
        elif nppes and not nppes.success:
            deductions += 0.05  # Source unavailable penalty

        # ── LEIE Validation ───────────────────────────────────────────────────
        leie_npi = source_results.get("leie_npi")
        if leie_npi and leie_npi.success:
            if leie_npi.data.get("excluded"):
                findings.append(FindingCode.LEIE_ACTIVE_EXCLUSION)
                deductions += 0.40
                field_comparisons.append({
                    "field": "oi_leie_status",
                    "submitted": "NOT_EXCLUDED (claimed)",
                    "leie": "ACTIVELY_EXCLUDED",
                    "exclusions": leie_npi.data.get("active_exclusions", []),
                    "result": "NON_COMPLIANT",
                    "finding": FindingCode.LEIE_ACTIVE_EXCLUSION
                })
            elif leie_npi.data.get("historical_exclusions"):
                findings.append(FindingCode.LEIE_HISTORICAL_RESOLVED)
                deductions += 0.10
                field_comparisons.append({
                    "field": "oi_leie_status",
                    "submitted": "NOT_EXCLUDED",
                    "leie": "HISTORICAL_EXCLUSION_REINSTATED",
                    "result": "MINOR",
                    "finding": FindingCode.LEIE_HISTORICAL_RESOLVED
                })
            else:
                field_comparisons.append({
                    "field": "oi_leie_status",
                    "submitted": "NOT_EXCLUDED",
                    "leie": "NO_RECORD",
                    "result": "MATCH",
                    "finding": None
                })

        # ── SAM.gov Validation ────────────────────────────────────────────────
        sam_entity = source_results.get("sam_entity")
        sam_excl = source_results.get("sam_exclusion")

        if sam_excl and sam_excl.success and sam_excl.data.get("excluded"):
            findings.append(FindingCode.SAM_ACTIVE_DEBARMENT)
            deductions += 0.40
            field_comparisons.append({
                "field": "sam_exclusion_status",
                "submitted": "NOT_DEBARRED (claimed)",
                "sam_gov": "ACTIVELY_DEBARRED",
                "result": "NON_COMPLIANT",
                "finding": FindingCode.SAM_ACTIVE_DEBARMENT
            })

        if sam_entity and sam_entity.success and sam_entity.data.get("found"):
            if not sam_entity.data.get("registration_current"):
                findings.append(FindingCode.SAM_REGISTRATION_LAPSED)
                deductions += 0.25
                field_comparisons.append({
                    "field": "sam_registration",
                    "submitted": "ACTIVE (claimed)",
                    "sam_gov": f"EXPIRED: {sam_entity.data.get('registration_expiry')}",
                    "result": "INEXPLICABLE",
                    "finding": FindingCode.SAM_REGISTRATION_LAPSED
                })

        # ── PECOS Validation ─────────────────────────────────────────────────
        pecos = source_results.get("pecos")
        if pecos and pecos.success and pecos.data.get("payment_suspension"):
            findings.append(FindingCode.PECOS_PAYMENT_SUSPENSION)
            deductions += 0.40
            field_comparisons.append({
                "field": "pecos_payment_status",
                "submitted": "ACTIVE (claimed)",
                "pecos": "PAYMENT_SUSPENDED",
                "result": "NON_COMPLIANT",
                "finding": FindingCode.PECOS_PAYMENT_SUSPENSION
            })

        # ── Source Availability Penalty ───────────────────────────────────────
        for key, result in source_results.items():
            if result and not result.success:
                deductions += 0.05

        # ── Calculate Confidence Score ────────────────────────────────────────
        confidence = max(0.0, round(1.0 - deductions, 3))

        # ── Apply Bucket Classification ───────────────────────────────────────
        bucket, label = self._determine_bucket(findings)

        if not findings:
            findings.append(FindingCode.NO_DISCREPANCY)

        return {
            "bucket": bucket,
            "bucket_label": label,
            "confidence": confidence,
            "finding_codes": findings,
            "field_comparisons": field_comparisons,
            "tier": self._determine_tier(bucket, confidence),
            "auto_classify": confidence >= 0.95 and bucket == 1,
        }

    def _classify_name_similarity(
        self, sim: float, submitted: str, nppes: str
    ) -> tuple[str | None, float]:
        """Classify name similarity into finding code + confidence deduction."""
        if sim >= 0.90:
            return None, 0.0
        if sim >= 0.70:
            return FindingCode.NAME_ABBREVIATION_DIFF, 0.10
        if sim >= 0.50:
            sub_norm = self.normalizer.normalize(submitted)
            nppes_norm = self.normalizer.normalize(nppes)
            # Check for DBA/alias patterns
            if any(word in sub_norm for word in nppes_norm.split()[:2]):
                return FindingCode.NAME_DBA_VS_LEGAL, 0.10
            return FindingCode.NAME_PUNCTUATION_DIFF, 0.10
        if sim >= 0.30:
            return FindingCode.NAME_COMPLETELY_DIFFERENT, 0.25
        return FindingCode.NAME_UNRESOLVABLE, 0.40

    def _classify_address(
        self, submitted: dict, nppes: dict
    ) -> tuple[str | None, float]:
        """Compare submitted address against NPPES address."""
        sub_state = (submitted.get("state") or "").strip().upper()
        nppes_state = (nppes.get("state") or "").strip().upper()
        if sub_state and nppes_state and sub_state != nppes_state:
            return FindingCode.ADDRESS_STATE_CONFLICT, 0.25

        sub_zip = (submitted.get("postalCode") or "")[:5]
        nppes_zip = (nppes.get("postal_code") or "")[:5]
        if sub_zip and nppes_zip and sub_zip != nppes_zip:
            return FindingCode.ADDRESS_FORMAT_DIFF, 0.10

        sub_line = " ".join(submitted.get("line") or []).lower()
        nppes_line = (nppes.get("address_1") or "").lower()
        if sub_line and nppes_line:
            # Check if core street number + name match
            sub_core = re.sub(r"\b(suite|ste|floor|fl|unit|apt|#)\b.*$", "", sub_line).strip()
            nppes_core = re.sub(r"\b(suite|ste|floor|fl|unit|apt|#)\b.*$", "", nppes_line).strip()
            sim = self.normalizer.similarity_score(sub_core, nppes_core)
            if sim < 0.70:
                return FindingCode.ADDRESS_FORMAT_DIFF, 0.10
        return None, 0.0

    def _determine_bucket(self, findings: list[str]) -> tuple[int, str]:
        """Determine final bucket — worst finding wins."""
        bucket4_codes = {
            FindingCode.NPI_NOT_FOUND, FindingCode.NPI_INACTIVE, FindingCode.NPI_DEACTIVATED,
            FindingCode.LEIE_ACTIVE_EXCLUSION, FindingCode.SAM_ACTIVE_DEBARMENT,
            FindingCode.PECOS_PAYMENT_SUSPENSION, FindingCode.NAME_UNRESOLVABLE,
        }
        bucket3_codes = {
            FindingCode.NAME_COMPLETELY_DIFFERENT, FindingCode.ADDRESS_STATE_CONFLICT,
            FindingCode.ENTITY_TYPE_MISMATCH, FindingCode.NPI_MISSING,
            FindingCode.SAM_REGISTRATION_LAPSED, FindingCode.SOURCE_CONFLICT,
            FindingCode.HIERARCHY_MISMATCH,
        }
        bucket2_codes = {
            FindingCode.NAME_ABBREVIATION_DIFF, FindingCode.NAME_PUNCTUATION_DIFF,
            FindingCode.NAME_DBA_VS_LEGAL, FindingCode.ADDRESS_UNIT_DIFF,
            FindingCode.ADDRESS_FORMAT_DIFF, FindingCode.PHONE_DISCREPANCY,
            FindingCode.ZIP_FORMAT_DIFF, FindingCode.LEIE_HISTORICAL_RESOLVED,
            FindingCode.MINOR_CORP_SUFFIX_DIFF,
        }

        if any(f in bucket4_codes for f in findings):
            return 4, "Non-Compliant"
        if any(f in bucket3_codes for f in findings):
            return 3, "Inexplicable"
        if any(f in bucket2_codes for f in findings):
            return 2, "Minor or Administrative"
        return 1, "No Discrepancy"

    def _determine_tier(self, bucket: int, confidence: float) -> int:
        """Route to correct review tier."""
        if bucket == 4:
            return 3
        if bucket == 3:
            return 2  # Tier 2 with escalation flag
        if bucket == 2:
            return 2
        if bucket == 1 and confidence >= 0.95:
            return 1  # Auto-complete
        return 2  # Tier 2 spot check

    def _extract_npi(self, entity: dict) -> str:
        for ident in entity.get("identifier", []):
            if ident.get("system") == "http://hl7.org/fhir/sid/us-npi":
                return ident.get("value", "")
        return ""

    def _extract_phone(self, entity: dict) -> str:
        for t in entity.get("telecom", []):
            if t.get("system") == "phone":
                return t.get("value", "")
        return ""

    def _extract_entity_type(self, entity: dict) -> str:
        for t in entity.get("type", []):
            for coding in t.get("coding", []):
                return coding.get("code", "")
        return ""


# ─── Evidence Record Generator ───────────────────────────────────────────────

class EvidenceRecordGenerator:
    """
    Generates the complete 5-element evidence record
    for each reviewed TEFCA entity.
    """

    DISPOSITION_RULES = {
        1: "NO_ACTION_REQUIRED",
        2: "QHIN_NOTIFICATION_MINOR",
        3: "QHIN_CORRECTIVE_ACTION_REQUIRED",
        4: "QHIN_CORRECTIVE_ACTION_REQUIRED",
    }

    ESCALATE_CODES = {
        FindingCode.LEIE_ACTIVE_EXCLUSION,
        FindingCode.SAM_ACTIVE_DEBARMENT,
        FindingCode.PECOS_PAYMENT_SUSPENSION,
    }

    def generate(
        self,
        entity: dict,
        cycle_id: str,
        validation_result: dict,
        source_results: dict[str, SourceResult],
        reviewer_id: str = "SYSTEM_TIER1",
    ) -> dict:
        """Generate complete 5-element evidence record."""
        record_id = str(uuid.uuid4())
        findings = validation_result.get("finding_codes", [])
        bucket = validation_result.get("bucket", 1)

        record = {
            "record_id": record_id,
            "cycle_id": cycle_id,
            "entity_rce_id": entity.get("id"),
            "generated_at": datetime.utcnow().isoformat(),
            "element_1": self._element_1(entity, cycle_id, record_id),
            "element_2": self._element_2(validation_result),
            "element_3": self._element_3(entity, validation_result, source_results),
            "element_4": self._element_4(source_results),
            "element_5": self._element_5(bucket, findings, reviewer_id),
        }
        return record

    def _element_1(self, entity: dict, cycle_id: str, record_id: str) -> dict:
        """Element 1 — Entity Identification."""
        npi = ""
        rce_id = entity.get("id", "")
        for ident in entity.get("identifier", []):
            if ident.get("system") == "http://hl7.org/fhir/sid/us-npi":
                npi = ident.get("value", "")
                break
        qhin = entity.get("_qhin", "Unknown QHIN")
        entity_type = ""
        for t in entity.get("type", []):
            for coding in t.get("coding", []):
                entity_type = coding.get("code", "")

        return {
            "qhin_name": qhin,
            "entity_type": entity_type,
            "entity_legal_name": entity.get("name", ""),
            "entity_aliases": entity.get("alias", []),
            "entity_npi": npi,
            "entity_rce_id": rce_id,
            "submission_date": entity.get("date_submitted", datetime.utcnow().date().isoformat()),
            "review_cycle_id": cycle_id,
            "evidence_record_id": record_id,
            "review_date": datetime.utcnow().date().isoformat(),
            "addresses_submitted": entity.get("address", []),
            "telecom_submitted": entity.get("telecom", []),
        }

    def _element_2(self, validation_result: dict) -> dict:
        """Element 2 — Finding Classification."""
        bucket = validation_result.get("bucket", 1)
        return {
            "bucket_classification": str(bucket),
            "bucket_label": validation_result.get("bucket_label", ""),
            "confidence_score": validation_result.get("confidence", 1.0),
            "finding_codes": validation_result.get("finding_codes", []),
            "finding_descriptions": [
                FINDING_DESCRIPTIONS.get(code, code)
                for code in validation_result.get("finding_codes", [])
            ],
            "tier_assigned": validation_result.get("tier", 1),
            "auto_classified": validation_result.get("auto_classify", False),
            "supervisor_review_required": bucket == 4,
        }

    def _element_3(
        self,
        entity: dict,
        validation_result: dict,
        source_results: dict[str, SourceResult]
    ) -> dict:
        """Element 3 — Source Comparison."""
        comparisons = validation_result.get("field_comparisons", [])

        nppes_data = source_results.get("nppes", SourceResult("NPPES", False)).data
        leie_data = source_results.get("leie_npi", SourceResult("OIG_LEIE", False)).data
        sam_data = source_results.get("sam_entity", SourceResult("SAM_GOV", False)).data
        pecos_data = source_results.get("pecos", SourceResult("PECOS", False)).data

        return {
            "field_comparisons": comparisons,
            "submitted_summary": {
                "legal_name": entity.get("name"),
                "npi": next(
                    (i.get("value") for i in entity.get("identifier", [])
                     if i.get("system") == "http://hl7.org/fhir/sid/us-npi"), None
                ),
                "primary_address": (entity.get("address") or [{}])[0],
                "phone": next(
                    (t.get("value") for t in entity.get("telecom", [])
                     if t.get("system") == "phone"), None
                ),
            },
            "nppes_summary": {
                "found": nppes_data.get("found", False),
                "status": nppes_data.get("status"),
                "legal_name": nppes_data.get("legal_name"),
                "enumeration_type": nppes_data.get("enumeration_type"),
            },
            "leie_summary": {
                "excluded": leie_data.get("excluded", False),
                "active_exclusion_count": len(leie_data.get("active_exclusions", [])),
            },
            "sam_summary": {
                "found": sam_data.get("found", False),
                "registration_current": sam_data.get("registration_current"),
                "active_exclusion": sam_data.get("active_exclusion", False),
            },
            "pecos_summary": {
                "found": pecos_data.get("found", False),
                "payment_suspension": pecos_data.get("payment_suspension", False),
                "provider_type": pecos_data.get("provider_type"),
            },
        }

    def _element_4(self, source_results: dict[str, SourceResult]) -> dict:
        """Element 4 — Supporting Citations."""
        citations = []
        for key, result in source_results.items():
            if result:
                citations.append({
                    "source_name": result.source_name,
                    "query_timestamp": result.query_timestamp,
                    "query_parameters": result.query_params,
                    "response_hash": result.response_hash,
                    "query_success": result.success,
                    "api_version": result.api_version,
                    "error": result.error,
                })
        return {"citations": citations, "total_sources_queried": len(citations)}

    def _element_5(
        self, bucket: int, findings: list[str], reviewer_id: str
    ) -> dict:
        """Element 5 — Disposition Recommendation."""
        # Escalate to ONC for the most serious violations
        needs_escalation = any(f in self.ESCALATE_CODES for f in findings)
        if needs_escalation:
            recommendation = "ESCALATE_TO_ONC_REVIEW"
        else:
            recommendation = self.DISPOSITION_RULES.get(bucket, "QHIN_NOTIFICATION_MINOR")

        action_detail = self._get_action_detail(bucket, findings)
        prevention = self._get_prevention(bucket, findings)
        deadline_days = {1: None, 2: 30, 3: 21, 4: 10}.get(bucket)
        deadline = None
        if deadline_days:
            from datetime import timedelta
            deadline = (datetime.utcnow() + timedelta(days=deadline_days)).date().isoformat()

        return {
            "recommendation": recommendation,
            "recommended_action_detail": action_detail,
            "recommended_deadline": deadline,
            "prevention_recommendation": prevention,
            "reviewer_id": reviewer_id,
            "review_notes": "",
            "agt_does_not_adjudicate": (
                "AGT produces this evidence record and disposition recommendation. "
                "The ONC COR makes all final determinations."
            ),
        }

    def _get_action_detail(self, bucket: int, findings: list[str]) -> str:
        if FindingCode.LEIE_ACTIVE_EXCLUSION in findings:
            return ("Active OIG LEIE exclusion confirmed. Recommend immediate suspension "
                   "of this entity's TEFCA Participant status pending COR determination.")
        if FindingCode.SAM_ACTIVE_DEBARMENT in findings:
            return ("Active SAM.gov debarment confirmed. Recommend immediate suspension "
                   "of TEFCA participation pending COR and ONC Legal determination.")
        if FindingCode.PECOS_PAYMENT_SUSPENSION in findings:
            return ("PECOS payment suspension flag active. CMS Program Integrity concern. "
                   "Recommend escalation to COR with notification to CMS CPI.")
        if FindingCode.NPI_NOT_FOUND in findings or FindingCode.NPI_DEACTIVATED in findings:
            return ("NPI invalid or deactivated. QHIN should require entity to submit "
                   "valid, active NPI from NPPES or submit corrected enrollment documentation.")
        if bucket == 3:
            return ("Inexplicable discrepancy requires QHIN investigation. "
                   "QHIN should contact Participant/Subparticipant for clarifying documentation.")
        if bucket == 2:
            return ("Administrative discrepancy. QHIN should notify entity and request "
                   "updated submission to correct minor data quality issues.")
        return "No action required. Entity validated successfully."

    def _get_prevention(self, bucket: int, findings: list[str]) -> str:
        if bucket >= 3:
            return ("QHIN should implement pre-submission validation against NPPES, "
                   "OIG LEIE, and SAM.gov before onboarding new Participants and Subparticipants.")
        if bucket == 2:
            return ("QHIN should provide submission guidance to entities on legal name "
                   "format requirements and address standardization.")
        return "Continue current onboarding processes — no issues identified."
