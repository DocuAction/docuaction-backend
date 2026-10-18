"""SAM.gov verification contract — connector-to-evidence correctness.

2026-10-02 fix. Root cause traced end to end: `SourceConnectorManager.
query_all_sources()` queried only `lookup_by_uei()` and reused that single
registration probe for both the `sam_entity` and `sam_exclusion` slots, and
`evidence_assembly.py`'s D3 exclusion dimension read `debarred`/`exclusions`
keys the connector never set (the real key is `excluded`) — so a confirmed,
active SAM debarment was silently assembled as `Disposition.PASS`, "No
debarment record found." These tests pin the corrected behavior:

  * the independent v4 exclusions check is actually invoked (`verify()`,
    not a bare `lookup_by_uei()`), not re-inferred from the v3 summary flag
  * an unperformed/failed exclusions leg never reads as "clear"
  * an ambiguous name match never becomes a confirmed clearance OR a
    confirmed debarment/lapse
  * `registration_current = None` (ambiguous identity) is never treated the
    same as a confirmed `False` (actually looked up and expired)

Deliberately NOT asserted here (documented, not fixed, in this pass — see
arc_pipeline.py's `_DISPOSITION_TO_STATE` comment): whether a REVIEW
disposition reaches the dashboard's outcome buckets, and whether
`BucketClassifier` RULE-005 can disqualify a SAM-debarred RCE entity from
B1. Both are confirmed separate, open gaps.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest import mock

import pytest

from app.Tefca.applicability import build_profile
from app.Tefca.connectors import SAMGovConnector, SourceConnectorManager, SourceResult
from app.Tefca.evidence_assembly import assemble_dimensions
from app.Tefca.evidence_dimensions import Dimension, Disposition
from app.Tefca.validation_engine import FindingCode, ValidationEngine

pytestmark = pytest.mark.regression


# ── fixtures ─────────────────────────────────────────────────────────────────

def onc_entity(**over) -> dict:
    entity = {
        "resourceType": "Organization",
        "id": "rce-org-sam-test-001",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1003879883"},
            {"system": "urn:docuaction:tefca/identifier/uei", "value": "ABC123XYZ789"},
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type",
                              "code": "PARTICIPANT"}]}],
        "name": "Riverside Community Health Network",
        "address": [{"use": "work", "line": ["1200 Health Center Drive"], "city": "Baltimore",
                     "state": "MD", "postalCode": "21201", "country": "US"}],
        "uei": "ABC123XYZ789",
    }
    entity.update(over)
    return entity


def nppes_ok() -> SourceResult:
    return SourceResult.ok("NPPES", {"found": True, "legal_name": "Riverside Community Health Network",
                                     "enumeration_type": "NPI-2", "status": "A", "addresses": []},
                           {"npi": "1003879883"})


def sam(data: Dict[str, Any], success: bool = True) -> SourceResult:
    return (SourceResult.ok("SAM_GOV", data, {"uei": "ABC123XYZ789"})
            if success else
            SourceResult.unavailable("SAM_GOV", "simulated outage", {"uei": "ABC123XYZ789"}))


def clean_sources(**over) -> Dict[str, Any]:
    base = {
        "nppes": nppes_ok(),
        "leie_npi": SourceResult.ok("OIG_LEIE", {"excluded": False}, {"npi": "1003879883"}),
        "sam_entity": sam({"found": True, "matched_by": "uei", "registration_current": True,
                           "excluded": False, "excluded_known": True, "identity_ambiguous": False}),
        "sam_exclusion": sam({"found": True, "matched_by": "uei", "registration_current": True,
                              "excluded": False, "excluded_known": True, "identity_ambiguous": False}),
    }
    base.update(over)
    return base


def d3(entity, sources):
    profile = build_profile(entity, nppes_data=sources["nppes"].data, pecos_found=None)
    results = assemble_dimensions(entity, profile, sources)
    return next(r for r in results if r.dimension == Dimension.D3_EXCLUSION_REVOCATION.value)


def sam_item(dim_result):
    return next(i for i in dim_result.items if i.source == "SAM_GOV")


# ── connector-level: the independent-check wiring itself ────────────────────

class TestConnectorWiring:
    async def test_query_all_sources_calls_verify_not_bare_lookup(self):
        """The headline fix: both legs must actually be queried, independently."""
        mgr = SourceConnectorManager()
        verify_calls = []

        async def fake_verify(uei="", legal_name=""):
            verify_calls.append({"uei": uei, "legal_name": legal_name})
            return sam({"found": True, "matched_by": "uei", "registration_current": True,
                       "excluded": False, "excluded_known": True, "identity_ambiguous": False})

        with mock.patch.object(mgr.sam, "verify", side_effect=fake_verify), \
             mock.patch.object(mgr.nppes, "lookup_by_npi",
                              return_value=nppes_ok()) as _n, \
             mock.patch.object(mgr.leie, "lookup_by_npi",
                              return_value=SourceResult.ok("OIG_LEIE", {"excluded": False}, {})):
            result = await mgr.query_all_sources(onc_entity())

        assert len(verify_calls) == 1
        assert verify_calls[0]["uei"] == "ABC123XYZ789"
        # Both slots are backed by the one merged, independently-checked result.
        assert result["sam_entity"] is result["sam_exclusion"]

    async def test_check_exclusions_by_name_flags_ambiguity(self):
        conn = SAMGovConnector()
        conn.api_key = "test-key"

        class FakeResp:
            status_code = 200
            def json(self):
                return {"totalRecords": 2, "excludedEntity": [
                    {"exclusionIdentification": {"exclusionName": "A"}},
                    {"exclusionIdentification": {"exclusionName": "B"}}]}

        with mock.patch("app.Tefca.connectors._get_with_retry", return_value=FakeResp()):
            result = await conn.check_exclusions(legal_name="Acme Health")
        assert result.success
        assert result.get("ambiguous") is True
        assert result.get("match_count") == 2

    async def test_check_exclusions_by_uei_never_flagged_ambiguous(self):
        conn = SAMGovConnector()
        conn.api_key = "test-key"

        class FakeResp:
            status_code = 200
            def json(self):
                return {"totalRecords": 1, "excludedEntity": [
                    {"exclusionIdentification": {"exclusionName": "A"}}]}

        with mock.patch("app.Tefca.connectors._get_with_retry", return_value=FakeResp()):
            result = await conn.check_exclusions(uei="ABC123XYZ789")
        assert result.get("ambiguous") is False

    async def test_verify_merges_independent_legs(self):
        conn = SAMGovConnector()
        reg_result = SourceResult.ok("SAM_GOV", {"found": True, "matched_by": "uei",
                                                  "registration_current": True}, {"uei": "X"})
        exc_result = SourceResult.ok("SAM_GOV_EXCLUSIONS", {"excluded": True, "match_count": 1,
                                                            "ambiguous": False}, {"uei": "X"})
        with mock.patch.object(conn, "lookup_by_uei", return_value=reg_result), \
             mock.patch.object(conn, "check_exclusions", return_value=exc_result):
            merged = await conn.verify(uei="X")
        assert merged.success
        assert merged.get("excluded") is True
        assert merged.get("excluded_known") is True
        assert merged.get("registration_available") is True
        assert merged.get("exclusions_available") is True
        assert merged.get("identity_ambiguous") is False

    async def test_verify_unavailable_exclusions_leg_does_not_become_clear(self):
        """Registration succeeds, the independent exclusions check fails:
        the merged result must say the exclusion question is UNKNOWN, not
        answer it False on the registration leg's strength alone."""
        conn = SAMGovConnector()
        reg_result = SourceResult.ok("SAM_GOV", {"found": True, "matched_by": "uei"}, {"uei": "X"})
        exc_result = SourceResult.unavailable("SAM_GOV_EXCLUSIONS", "HTTP 503", {"uei": "X"})
        with mock.patch.object(conn, "lookup_by_uei", return_value=reg_result), \
             mock.patch.object(conn, "check_exclusions", return_value=exc_result):
            merged = await conn.verify(uei="X")
        assert merged.success  # registration leg alone still answers *something*
        assert merged.get("excluded") is False       # the safe default value
        assert merged.get("excluded_known") is False  # — but marked NOT confirmed clear
        assert merged.get("exclusions_available") is False

    async def test_verify_ambiguous_name_match_propagates(self):
        conn = SAMGovConnector()
        reg_result = SourceResult.ok("SAM_GOV", {"found": True, "matched_by": "name",
                                                  "ambiguous": True, "registration_current": None},
                                     {"legal_name": "Acme"})
        exc_result = SourceResult.ok("SAM_GOV_EXCLUSIONS", {"excluded": False, "ambiguous": False},
                                     {"legal_name": "Acme"})
        with mock.patch.object(conn, "lookup_by_name", return_value=reg_result), \
             mock.patch.object(conn, "check_exclusions", return_value=exc_result):
            merged = await conn.verify(legal_name="Acme")
        assert merged.get("identity_ambiguous") is True


# ── evidence_assembly.py: the D3 dimension itself ────────────────────────────

class TestEvidenceAssemblyExclusionDimension:
    def test_confirmed_exclusion_is_review_with_truthful_evidence(self):
        """The headline bug, pinned: a real `excluded: True` must surface as
        REVIEW, never silently collapse to PASS."""
        entity = onc_entity()
        sources = clean_sources(
            sam_exclusion=sam({"found": True, "matched_by": "uei", "excluded": True,
                              "excluded_known": True, "identity_ambiguous": False}),
        )
        d = d3(entity, sources)
        item = sam_item(d)
        assert item.disposition == Disposition.REVIEW.value
        assert item.disposition != Disposition.PASS.value
        assert item.original_values.get("excluded") is True
        assert d.requires_analyst is True

    def test_clean_known_result_is_pass(self):
        """Regression guard: a genuinely clear, fully-checked result stays PASS."""
        entity = onc_entity()
        d = d3(entity, clean_sources())
        item = sam_item(d)
        assert item.disposition == Disposition.PASS.value

    def test_unperformed_exclusion_check_is_unavailable_not_pass(self):
        """`excluded_known=False` — registration answered, exclusion did not.
        Must never read as a clean bill."""
        entity = onc_entity()
        sources = clean_sources(
            sam_exclusion=sam({"found": True, "matched_by": "uei", "excluded": False,
                              "excluded_known": False, "identity_ambiguous": False}),
        )
        d = d3(entity, sources)
        item = sam_item(d)
        assert item.disposition == Disposition.UNAVAILABLE.value
        assert item.disposition != Disposition.PASS.value

    def test_ambiguous_identity_is_review_even_when_excluded_false(self):
        """An unconfirmed identity must not be reported clear just because the
        `excluded` flag defaulted False."""
        entity = onc_entity()
        sources = clean_sources(
            sam_exclusion=sam({"found": True, "matched_by": "name", "excluded": False,
                              "excluded_known": True, "identity_ambiguous": True}),
        )
        d = d3(entity, sources)
        item = sam_item(d)
        assert item.disposition == Disposition.REVIEW.value
        assert "unconfirmed" in item.note.lower() or "ambiguous" in item.note.lower() \
            or "more than one" in item.note.lower()

    def test_name_fallback_clean_result_is_not_found_not_pass(self):
        """Pre-existing, unchanged behavior: a name-only clean search stays
        NOT_FOUND, weaker evidence than a UEI match — not newly broken by
        this fix."""
        entity = onc_entity(identifier=[{"system": "http://hl7.org/fhir/sid/us-npi",
                                         "value": "1003879883"}])  # no UEI identifier
        sources = clean_sources()
        del sources["sam_entity"], sources["sam_exclusion"]
        sources["sam_name"] = sam({"found": True, "matched_by": "name", "excluded": False,
                                   "excluded_known": True, "identity_ambiguous": False})
        d = d3(entity, sources)
        item = sam_item(d)
        assert item.disposition == Disposition.NOT_FOUND.value


# ── validation_engine.py: the legacy FindingCode path ────────────────────────

class TestValidationEngineSAM:
    def _validate(self, sources):
        return ValidationEngine().validate(onc_entity(), sources)

    def test_confirmed_debarment_files_finding(self):
        sources = clean_sources(
            sam_exclusion=sam({"found": True, "excluded": True, "excluded_known": True,
                              "identity_ambiguous": False}),
        )
        result = self._validate(sources)
        assert FindingCode.SAM_ACTIVE_DEBARMENT in result["finding_codes"]

    def test_ambiguous_match_files_no_finding_and_forces_indeterminate(self):
        sources = clean_sources(
            sam_entity=sam({"found": True, "matched_by": "name", "registration_current": None,
                           "identity_ambiguous": True}),
            sam_exclusion=sam({"found": True, "matched_by": "name", "excluded": False,
                              "excluded_known": True, "identity_ambiguous": True}),
        )
        result = self._validate(sources)
        assert FindingCode.SAM_ACTIVE_DEBARMENT not in result["finding_codes"]
        assert FindingCode.SAM_REGISTRATION_LAPSED not in result["finding_codes"]
        assert result["indeterminate"] is True
        assert "SAM_GOV" in result["indeterminate_reason"]

    def test_registration_current_none_never_reads_as_lapsed(self):
        """The `if not ...` -> `is False` idiom fix, pinned directly."""
        sources = clean_sources(
            sam_entity=sam({"found": True, "matched_by": "name", "registration_current": None,
                           "identity_ambiguous": True}),
        )
        result = self._validate(sources)
        assert FindingCode.SAM_REGISTRATION_LAPSED not in result["finding_codes"]

    def test_confirmed_lapsed_registration_still_files(self):
        """Regression guard: a real, confirmed lapse must still fire."""
        sources = clean_sources(
            sam_entity=sam({"found": True, "matched_by": "uei", "registration_current": False,
                           "registration_expiry": "2025-01-01", "identity_ambiguous": False}),
        )
        result = self._validate(sources)
        assert FindingCode.SAM_REGISTRATION_LAPSED in result["finding_codes"]

    def test_unknown_exclusion_check_files_no_finding_and_forces_indeterminate(self):
        sources = clean_sources(
            sam_exclusion=sam({"found": True, "excluded": False, "excluded_known": False,
                              "identity_ambiguous": False}),
        )
        result = self._validate(sources)
        assert FindingCode.SAM_ACTIVE_DEBARMENT not in result["finding_codes"]
        assert result["indeterminate"] is True

    def test_clean_fully_checked_result_is_not_indeterminate_on_sam_alone(self):
        """Regression guard: a genuinely clean, fully-checked SAM result must
        not itself force indeterminate (other required sources still apply
        independently)."""
        result = self._validate(clean_sources())
        assert "SAM_GOV" not in (result["indeterminate_reason"] or "")
