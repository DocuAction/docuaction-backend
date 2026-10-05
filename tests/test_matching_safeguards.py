"""The eight matching/verification safeguards, each pinned to the code that
enforces it (2026-10-03 audit; see SAFEGUARDS_AUDIT.md for the file:line map).

Every test here is a GUARD: it fails if a safeguard is loosened. None of
them require a database; the two that touch persistence use a fake session
that only records what would have been added.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.Tefca.connectors import SourceResult
from app.Tefca.validation_engine import FindingCode, ValidationEngine
from app.tefca_registry.bucket_classifier import (
    FAILED, NOT_CHECKED, NOT_FOUND, UNAVAILABLE, VERIFIED, BucketClassifier,
    SEED_RULES_V3)
from app.tefca_registry.entity_resolver import EntityResolver
from app.tefca_registry.rce import source_matching as sx

pytestmark = pytest.mark.regression

_NPI_T2 = "1003879883"       # Luhn-valid, used by the existing SAM contract tests
_OTHER_NPI = "1234567893"    # Luhn-valid, different


def _onc_entity(entity_type: str = "PARTICIPANT", npi: str = _NPI_T2) -> dict:
    return {
        "resourceType": "Organization", "id": "rce-org-safeguard-001",
        "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": npi}],
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type",
                              "code": entity_type}]}],
        "name": "Riverside Community Health Network",
        "address": [{"line": ["1200 Health Center Drive"], "city": "Baltimore",
                     "state": "MD", "postalCode": "21201"}],
    }


def _nppes(**data) -> SourceResult:
    base = {"found": True, "legal_name": "Riverside Community Health Network",
            "enumeration_type": "NPI-2", "status": "A", "addresses": []}
    base.update(data)
    return SourceResult.ok("NPPES", base, {"npi": _NPI_T2})


def _sources(**over) -> dict:
    base = {
        "nppes": _nppes(),
        "leie_npi": SourceResult.ok("OIG_LEIE", {"excluded": False}, {"npi": _NPI_T2}),
        "sam_entity": SourceResult.ok("SAM_GOV", {"found": True, "matched_by": "uei",
                                                  "registration_current": True,
                                                  "excluded": False, "excluded_known": True,
                                                  "identity_ambiguous": False}, {}),
        "sam_exclusion": SourceResult.ok("SAM_GOV", {"found": True, "matched_by": "uei",
                                                     "excluded": False, "excluded_known": True,
                                                     "identity_ambiguous": False}, {}),
        "pecos": SourceResult.ok("CMS_PECOS", {"found": True}, {}),
    }
    base.update(over)
    return base


def _classify(sources: dict, fields: dict | None = None):
    return BucketClassifier(rules=SEED_RULES_V3).classify(
        {"sources": sources, "fields": fields or {}, "confidence_score": None})


# ── 1. Conflicting identifiers never fall through to name/address ────────────

class TestSafeguard1IdentifierConflict:
    def test_resolver_treats_differing_npis_as_decisive_non_match(self):
        """entity_resolver._by_identifier: two present, different NPIs end
        resolution right there — the identical name and address below are
        never consulted."""
        a = {"npi": _NPI_T2, "name": "Same Org", "address": "1 Same St, Town, MD 21201"}
        b = {"npi": _OTHER_NPI, "name": "Same Org", "address": "1 Same St, Town, MD 21201"}
        r = EntityResolver().resolve(a, b)
        assert r.is_match is False
        assert r.method == "identifier"
        assert r.requires_manual_review is False
        assert r.confidence == 1.0

    def test_resolver_signals_report_identifier_conflict(self):
        a = {"npi": _NPI_T2, "name": "Same Org", "address": "1 Same St", "entity_type": "x"}
        b = {"npi": _OTHER_NPI, "name": "Same Org", "address": "1 Same St", "entity_type": "x"}
        resolver = EntityResolver()
        base = resolver.resolve(a, b)
        signals = resolver._evidence_signals(a, b, base, {"source_agreement": True})
        assert signals["no_conflicting_fields"] is False
        assert signals["npi_exact_match"] is False

    def test_release1_npi_rule_makes_ambiguity_an_exception_not_a_candidate_search(self):
        """source_matching: the NPI on more than one registry entity is an
        EXCEPTION — the engine never proceeds to a descriptive (name/address)
        candidate search to break the tie."""
        d = sx.evaluate_npi_match(source_npi=_NPI_T2,
                                  nppes_evidence={"enumeration_type": "NPI-2"},
                                  registry_entities_with_npi=["e1", "e2"],
                                  source_record_key="k")
        assert d["status"] == sx.sm.MATCH_EXCEPTION
        assert "candidates" not in d


# ── 2. Missing parent values never establish a relationship ──────────────────

class TestSafeguard2MissingParents:
    def test_two_blank_identifiers_establish_nothing(self):
        """Blank-equals-blank is not identity: with no identifier on either
        side `_by_identifier` abstains (None), it does not match."""
        assert EntityResolver()._by_identifier({"npi": "", "tefcaid": ""},
                                               {"npi": "", "tefcaid": ""}) is None

    @pytest.mark.asyncio
    async def test_unresolved_parent_writes_an_observation_and_no_edge(self):
        """relationship_history.sync_edge with parent_id=None (the delivered
        partOf resolved to no entity) records UNRESOLVED_PARENT and asserts no
        relationship. Pass 2 of promotion also skips a blank partOf outright
        (promotion.py: `if not row.part_of ...: continue`)."""
        from app.tefca_registry.rce import relationship_history as rh
        from app.tefca_registry.rce import snapshot_models as sm

        added = []

        class _DB:
            def add(self, obj):
                added.append(obj)

        state = rh.EdgeState()
        outcome = await rh.sync_edge(
            _DB(), state, intake_id=None, boundary=date(2026, 9, 1),
            child_id="child", parent_id=None, rel_type="sub_participant_of",
            delivered_parent_oid="", child_qhin_oid="1.2.3", actor="test",
            notes="", enforce_same_qhin=True)
        assert outcome == sm.OBS_UNRESOLVED_PARENT
        assert state.counters["unresolved_parent"] == 1
        assert state.counters["asserted"] == 0
        assert len(added) == 1
        assert isinstance(added[0], sm.TefcaRelationshipObservation)
        assert added[0].parent_entity_id is None
        assert state.active == {}


# ── 3. OneKey identity stays within its namespace ────────────────────────────

class TestSafeguard3OneKeyNamespace:
    def test_a_onekey_shaped_key_is_refused_as_an_npi(self):
        """An IQVIA HCE_ID is never an NPI. Offered where an NPI is expected it
        fails NPI validation and becomes an EXCEPTION — it cannot be matched
        across namespaces by accident."""
        d = sx.evaluate_npi_match(source_npi="WUS0001234567",
                                  nppes_evidence={"enumeration_type": "NPI-2"},
                                  registry_entities_with_npi=["e1"],
                                  source_record_key="WUS0001234567")
        assert d["status"] == sx.sm.MATCH_EXCEPTION
        assert d["evidence"]["npi_valid"] is False

    def test_observation_sources_cannot_assert_tefca_identity_facts(self):
        with pytest.raises(sx.SourceAuthorityViolation):
            sx.assert_not_tefca_fact(sx.sm.SOURCE_IQVIA_HCO, ["tefcaid", "part_of"])
        sx.assert_not_tefca_fact(sx.sm.SOURCE_ONC_RCE, ["tefcaid"])  # the one authority


# ── 4. Representative-provider evidence ≠ organisation identity ──────────────

class TestSafeguard4RepresentativeEvidence:
    def test_validation_engine_flags_an_individual_npi_on_an_organisation(self):
        """validation_engine.py: NPI-1 (an individual) returned for a
        PARTICIPANT/SUBPARTICIPANT record is ENTITY_TYPE_MISMATCH (Bucket 3),
        never a clean match on the strength of the person's NPI."""
        result = ValidationEngine().validate(
            _onc_entity("PARTICIPANT"), _sources(nppes=_nppes(enumeration_type="NPI-1")))
        assert FindingCode.ENTITY_TYPE_MISMATCH in result["finding_codes"]
        assert result["bucket"] >= 3
        assert result["auto_classify"] is False

    def test_release1_rule_never_auto_matches_a_type1_npi_to_an_organisation(self):
        d = sx.evaluate_npi_match(source_npi=_NPI_T2,
                                  nppes_evidence={"enumeration_type": "NPI-1"},
                                  registry_entities_with_npi=["e1"],
                                  source_record_key="k")
        assert d["status"] == sx.sm.MATCH_EXCEPTION


# ── 5. Address comparisons respect corporate / mailing / practice meaning ────

class TestSafeguard5AddressPurpose:
    _LOCATION = {"address_purpose": "LOCATION", "address_1": "1200 Health Center Dr",
                 "city": "Baltimore", "state": "MD", "postal_code": "212010000"}
    _MAILING = {"address_purpose": "MAILING", "address_1": "PO Box 9",
                "city": "Dallas", "state": "TX", "postal_code": "75201"}

    def test_manual_path_prefers_practice_location_over_mailing(self):
        from app.tefca_registry.review_service import _practice_address

        line = _practice_address({"addresses": [self._MAILING, self._LOCATION]})
        assert line.startswith("1200 Health Center Dr")
        assert "PO Box" not in line

    def test_manual_path_does_not_compare_against_a_mailing_address(self):
        """No LOCATION row -> no practice address -> not compared. Before the
        2026-10-03 fix the first (MAILING) row was used instead."""
        from app.tefca_registry.review_service import _practice_address

        assert _practice_address({"addresses": [self._MAILING]}) == ""

    def test_validation_engine_does_not_compare_against_a_mailing_address(self):
        result = ValidationEngine().validate(
            _onc_entity(), _sources(nppes=_nppes(addresses=[self._MAILING])))
        addr = [c for c in result["field_comparisons"] if c["field"] == "address"]
        assert addr and addr[0]["result"] == "NOT_COMPARED"
        assert FindingCode.ADDRESS_STATE_CONFLICT not in result["finding_codes"]

    def test_bulk_path_selects_location_only(self):
        from app.Tefca.evidence_assembly import _nppes_location

        assert _nppes_location({"addresses": [self._MAILING]}) is None
        assert _nppes_location({"addresses": [self._MAILING, self._LOCATION]})["city"] == "Baltimore"


# ── 6. Errors and insufficient screening never become clearance ──────────────

class TestSafeguard6NoClearanceFromErrors:
    def test_all_sources_errored_is_never_b1(self):
        sources = {"nppes": {"status": FAILED}, "pecos": {"status": UNAVAILABLE},
                   "oig_leie": {"status": FAILED}, "sam_gov": {"status": NOT_CHECKED}}
        r = _classify(sources)
        assert r.bucket != "B1"
        assert r.rule_code not in ("RULE-001", "RULE-002")

    def test_unavailable_exclusion_sources_block_the_partial_pass(self):
        """RULE-002 tolerates an unavailable PECOS/SAM only; an unavailable
        LEIE or NPPES is not 'the remainder were unreachable'."""
        sources = {"nppes": {"status": VERIFIED}, "pecos": {"status": UNAVAILABLE},
                   "oig_leie": {"status": UNAVAILABLE}, "sam_gov": {"status": NOT_CHECKED}}
        assert _classify(sources).bucket != "B1"

    def test_validation_engine_marks_unavailable_sources_indeterminate(self):
        sources = _sources(
            leie_npi=SourceResult(source_name="OIG_LEIE", success=False, error="timeout"))
        result = ValidationEngine().validate(_onc_entity(), sources)
        assert result["indeterminate"] is True
        assert result["auto_classify"] is False
        assert "OIG_LEIE" in result["unavailable_sources"]

    @pytest.mark.asyncio
    async def test_manual_probe_reports_a_raising_connector_as_failed(self, monkeypatch):
        from app.tefca_registry import review_service as svc
        import app.Tefca.connectors as conns

        class _Boom:
            async def lookup_by_npi(self, npi):
                raise RuntimeError("upstream 500")

        class _Mgr:
            nppes = pecos = leie = _Boom()

        monkeypatch.setattr(conns, "SourceConnectorManager", lambda: _Mgr())

        class _DB:
            async def execute(self, *_a, **_k):
                class R:
                    @staticmethod
                    def scalar_one_or_none():
                        return _NPI_T2
                return R()

        out = await svc.probe_sources(_DB(), "e")
        assert {out[k]["status"] for k in ("nppes", "pecos", "oig_leie")} == {FAILED}
        assert out["oig_leie"]["status"] != "clear"


# ── 7. EIN format / corroboration / OIG stay separate from IRS verification ─

class TestSafeguard7EINSeparation:
    def test_ein_family_is_government_restricted_and_not_contractor_verifiable(self):
        from app.Tefca import identifier_boundary as ib

        for ident in ("EIN", "TIN", "FEIN"):
            assert ib.is_government_restricted(ident)
            assert ib.authority_for(ident).contractor_verifiable is False
            assert ib.authority_for(ident).authority == "Internal Revenue Service"
        # The exclusion list is keyed on NPI, a different authority entirely.
        assert ib.authority_for("NPI").contractor_verifiable is True

    def test_irs_is_a_disclosed_non_check_never_clear(self):
        from app.tefca_registry.review_service import NO_CONNECTOR

        assert "irs" in NO_CONNECTOR
        assert "no public IRS API" in NO_CONNECTOR["irs"]

    def test_no_classifier_rule_reads_an_irs_or_ein_signal(self):
        """Nothing can conflate an EIN format check with IRS confirmation
        because no rule consumes either as a verification source."""
        for rule in SEED_RULES_V3:
            for clause in rule["conditions"].values():
                if not isinstance(clause, list):
                    continue
                for cond in clause:
                    if isinstance(cond, dict):
                        assert cond.get("source") not in ("irs", "ein", "tin")
                        assert "ein" not in str(cond.get("field", "")).lower()
                    else:
                        assert cond not in ("irs", "ein")


# ── 8. Source disagreement is interpreted, not auto-noncompliance ────────────

class TestSafeguard8DisagreementIsNotB4:
    def test_nppes_pecos_disagreement_routes_to_b3_not_b4(self):
        sources = {"nppes": {"status": VERIFIED}, "pecos": {"status": NOT_FOUND},
                   "oig_leie": {"status": "clear"}, "sam_gov": {"status": NOT_CHECKED}}
        r = _classify(sources, {"nppes_pecos_conflict": True,
                                "multiple_source_conflict": True})
        assert r.bucket == "B3"
        assert r.rule_code == "RULE-004"

    def test_b4_fires_only_on_exclusion_revocation_or_invalid_identifier(self):
        rule5 = next(r for r in SEED_RULES_V3 if r["rule_code"] == "RULE-005")
        assert "all_of" not in rule5["conditions"]
        for cond in rule5["conditions"]["any_of"]:
            if "source" in cond:
                assert cond["source"] in ("oig_leie", "sam_gov")
                assert cond["status"] in ("excluded", "debarred", "not_found")
            else:
                assert cond["field"] in ("npi_validation", "required_verification_failed")
        assert not any("conflict" in str(c) for c in rule5["conditions"]["any_of"])

    def test_an_unavailable_source_is_a_gap_not_a_conflict(self):
        from app.tefca_registry.review_service import _derived_fields

        f = _derived_fields({"nppes": {"status": VERIFIED}, "pecos": {"status": UNAVAILABLE},
                             "oig_leie": {"status": "clear"}}, npi_flagged=False)
        assert f["nppes_pecos_conflict"] is False
        assert f["multiple_source_conflict"] is False
