"""WHAT CHANGED (historical delta) and the SYSTEM EVIDENCE ASSESSMENT."""
from __future__ import annotations

import pytest

from app.core.entity_intelligence.assessment import (FORBIDDEN_TERMS, SystemEvidenceAssessment, assess)
from app.core.entity_intelligence.comparison import compare_all
from app.core.entity_intelligence.delta import DeltaType, VariationSignal, compute_deltas, explain_deltas
from app.core.entity_intelligence.observations import LocationRole, ObservationType
from app.core.entity_intelligence.service import EntityIntelligenceService
from app.core.entity_intelligence import FeatureDisabled

from ei_fixtures import BALTIMORE, FREDERICK, ROCKVILLE, delivered, enable_all, unavailable
from test_entity_intelligence_comparison import SRC, dba, legal, loc, npi, src


def _by(deltas, dtype):
    return [d for d in deltas if d.delta_type is dtype]


class TestDelta:
    def test_new_entity_and_unchanged(self):
        cur = delivered("SYNTHETIC HEALTHCARE LLC", BALTIMORE)
        assert compute_deltas([], cur)[0].delta_type is DeltaType.NEW_ENTITY
        d = compute_deltas(cur, delivered("SYNTHETIC HEALTHCARE LLC", BALTIMORE))
        assert d and all(x.delta_type is DeltaType.UNCHANGED for x in d)

    def test_name_address_identifier_relationship_changes(self):
        prior = delivered("SYNTHETIC HEALTHCARE LLC", BALTIMORE, npi="9999900001",
                          relationships=[{"kind": "PROGRAM_PARENT", "name": "SYNTHETIC QHIN ONE"}])
        cur = delivered("SYNTHETIC MOBILE CLINIC", FREDERICK, npi="9999900002",
                        relationships=[{"kind": "PROGRAM_PARENT", "name": "SYNTHETIC QHIN TWO"}])
        d = compute_deltas(prior, cur)
        types = {x.delta_type for x in d}
        assert {DeltaType.NAME_CHANGED, DeltaType.ADDRESS_CHANGED, DeltaType.IDENTIFIER_CHANGED,
                DeltaType.RELATIONSHIP_CHANGED} <= types
        name = _by(d, DeltaType.NAME_CHANGED)[0]
        assert name.before["value"] == "SYNTHETIC HEALTHCARE LLC" and name.after["value"] == "SYNTHETIC MOBILE CLINIC"
        assert "→" in name.explanation

    def test_new_and_removed_values_in_multi_valued_slots(self):
        prior = [loc(LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, FREDERICK)]
        cur = [loc(LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, FREDERICK),
               loc(LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, ROCKVILLE)]
        d = compute_deltas(prior, cur)
        assert len(_by(d, DeltaType.NEW_VALUE)) == 1 and len(_by(d, DeltaType.UNCHANGED)) == 1
        d2 = compute_deltas(cur, prior)
        assert len(_by(d2, DeltaType.REMOVED_VALUE)) == 1

    def test_source_side_change_is_reported_with_its_source(self):
        d = compute_deltas([legal("SYNTHETIC HEALTHCARE LLC")], [legal("SYNTHETIC HEALTHCARE GROUP LLC")])
        assert d[0].delta_type is DeltaType.NAME_CHANGED and d[0].source_id == SRC

    def test_explainable_variation_from_dba(self):
        prior = delivered("SYNTHETIC HEALTHCARE LLC", BALTIMORE)
        cur = delivered("SYNTHETIC MOBILE CLINIC", BALTIMORE) + [npi("9999900001"), legal("SYNTHETIC HEALTHCARE LLC"),
                                                                 dba("SYNTHETIC MOBILE CLINIC"),
                                                                 loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE)]
        deltas = explain_deltas(compute_deltas(prior, cur), compare_all(cur, source_id=SRC))
        name = _by(deltas, DeltaType.NAME_CHANGED)[0]
        assert name.variation is VariationSignal.EXPLAINABLE_VARIATION_SIGNAL
        assert name.variation_basis == ["DBA_MATCH_IDENTIFIED"] and "may be explainable" in name.explanation

    def test_explainable_location_variation_from_additional_location(self):
        prior = delivered("SYNTHETIC HEALTHCARE LLC", BALTIMORE)
        cur = delivered("SYNTHETIC HEALTHCARE LLC", FREDERICK) + [npi("9999900001"), legal("SYNTHETIC HEALTHCARE LLC"),
                                                                  loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE),
                                                                  loc(LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, FREDERICK)]
        deltas = explain_deltas(compute_deltas(prior, cur), compare_all(cur, source_id=SRC))
        addr = _by(deltas, DeltaType.ADDRESS_CHANGED)[0]
        assert addr.variation is VariationSignal.EXPLAINABLE_LOCATION_VARIATION_SIGNAL

    def test_unexplained_variation(self):
        prior = delivered("SYNTHETIC HEALTHCARE LLC", BALTIMORE)
        cur = delivered("SYNTHETIC HEALTHCARE LLC", ROCKVILLE) + [npi("9999900001"), legal("SYNTHETIC HEALTHCARE LLC"),
                                                                  loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE)]
        deltas = explain_deltas(compute_deltas(prior, cur), compare_all(cur, source_id=SRC))
        addr = _by(deltas, DeltaType.ADDRESS_CHANGED)[0]
        assert addr.variation is VariationSignal.UNEXPLAINED_VARIATION and "LOCATION_CONFLICT" in addr.variation_basis


class TestAssessment:
    def _run(self, cur, prior=None):
        comps = compare_all(cur, source_id=SRC)
        deltas = explain_deltas(compute_deltas(prior, cur), comps) if prior is not None else []
        return assess(comps, deltas)

    def test_corroborated(self):
        cur = delivered("SYNTHETIC HEALTHCARE LLC", BALTIMORE) + [npi("9999900001"), legal("SYNTHETIC HEALTHCARE LLC"),
                                                                  loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE)]
        assert self._run(cur).assessment is SystemEvidenceAssessment.EVIDENCE_CORROBORATES

    def test_partially_corroborated(self):
        cur = delivered("SYNTHETIC HEALTHCARE LLC") + [npi("9999900001"), legal("SYNTHETIC HEALTHCARE LLC")]
        r = self._run(cur)
        assert r.assessment is SystemEvidenceAssessment.EVIDENCE_PARTIALLY_CORROBORATES
        assert any("LOCATION_IDENTITY" in q for q in r.open_questions)

    def test_explainable_variation(self):
        cur = delivered("SYNTHETIC MOBILE CLINIC", FREDERICK) + [npi("9999900001"), legal("SYNTHETIC HEALTHCARE LLC"),
                                                                 dba("SYNTHETIC MOBILE CLINIC"),
                                                                 loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE),
                                                                 loc(LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, FREDERICK)]
        r = self._run(cur)
        assert r.assessment is SystemEvidenceAssessment.EXPLAINABLE_VARIATION_IDENTIFIED
        assert r.requires_human_review and len(r.open_questions) == 2

    def test_conflict_is_not_outvoted(self):
        """Identifier and location agree; the name conflicts. One authoritative
        conflict surfaces — agreement elsewhere is not a vote."""
        cur = delivered("COMPLETELY DIFFERENT ORG", BALTIMORE) + [npi("9999900001"), legal("SYNTHETIC HEALTHCARE LLC"),
                                                                  loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE)]
        assert self._run(cur).assessment is SystemEvidenceAssessment.CONFLICTING_EVIDENCE

    def test_insufficient_and_unavailable(self):
        assert self._run(delivered("SYNTHETIC X", npi=None)).assessment is SystemEvidenceAssessment.INSUFFICIENT_EVIDENCE
        assert self._run(delivered("SYNTHETIC X", BALTIMORE) + [unavailable(SRC)]).assessment is SystemEvidenceAssessment.SOURCE_UNAVAILABLE

    def test_vocabulary_never_reads_as_a_determination(self):
        for member in SystemEvidenceAssessment:
            assert not (set(member.value.split("_")) & FORBIDDEN_TERMS)
        r = self._run(delivered("SYNTHETIC X", npi=None))
        payload = r.to_dict()
        machine = " ".join([payload["assessment"], *payload["basis"],
                            *[c["signal"] for c in payload["comparisons"]],
                            *[d["variation"] for d in payload["deltas"]]]).upper()
        for bad in ("COMPLIANT", "APPROVED", "REJECTED", "VERDICT", "PASS", "FAIL"):
            assert bad not in machine

    def test_assessment_payload_states_its_limits(self):
        payload = self._run(delivered("SYNTHETIC X", npi=None)).to_dict()
        assert payload["requires_human_review"] is True and "not a verdict" in payload["note"]


class TestService:
    def test_off_by_default(self):
        with pytest.raises(FeatureDisabled):
            EntityIntelligenceService().evaluate(canonical_entity_id="e", current=delivered("X"), source_ids=[SRC])

    def test_end_to_end_when_enabled(self, monkeypatch):
        enable_all(monkeypatch)
        prior = delivered("SYNTHETIC HEALTHCARE LLC", BALTIMORE)
        cur = delivered("SYNTHETIC MOBILE CLINIC", FREDERICK) + [npi("9999900001"), legal("SYNTHETIC HEALTHCARE LLC"),
                                                                 dba("SYNTHETIC MOBILE CLINIC"),
                                                                 loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE),
                                                                 loc(LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, FREDERICK)]
        run = EntityIntelligenceService().evaluate(canonical_entity_id="ent-1", current=cur, prior=prior, source_ids=[SRC])
        assert run.assessment.assessment is SystemEvidenceAssessment.EXPLAINABLE_VARIATION_IDENTIFIED
        subject = [d for d in run.deltas if d.subject and d.delta_type is not DeltaType.UNCHANGED]
        assert {d.variation for d in subject} == {
            VariationSignal.EXPLAINABLE_VARIATION_SIGNAL, VariationSignal.EXPLAINABLE_LOCATION_VARIATION_SIGNAL}
        evidence_side = [d for d in run.deltas if not d.subject]
        assert evidence_side and all(d.variation is VariationSignal.NOT_APPLICABLE for d in evidence_side)
        payload = run.to_dict()
        assert payload["run_id"] and "analyst determines" in payload["note"]
