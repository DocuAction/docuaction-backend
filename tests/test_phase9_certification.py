"""Phase 9 — end-to-end certification.

Validation, not feature work. Every test here asserts a property the system must
already have; none of them makes it true. Where a property can only be shown
against live data, the assertion lives in the certification script rather than
here, so this file stays runnable without a database.

The recurring theme is the same one that has governed every phase: the system
must be incapable of confusing "we could not ask" with "the answer was no", and
incapable of turning an observation into a finding without a human.
"""
from __future__ import annotations

import uuid

from app.core.evidence_vocabulary import ObservationState
from app.tefca_registry import models as reg
from app.tefca_registry.qa_gate import is_reportable
from app.Tefca.address_comparison import AddressResult, compare_to_nppes, compare_to_ppef
from app.Tefca.evidence_version import (
    APPROVED_RULE_VERSIONS, current_rule_version, historical_rule_versions)
from app.Tefca.exception_triage import Triage, triage

E = reg.ReviewDecisionEvent


def _obs(source, state, applicability="REQUIRED", dimension="IDENTITY", comparison=None):
    return {"source": source, "observation_result": state,
            "dimension_applicability": applicability,
            "evidence_dimension": dimension, "dimension_disposition": comparison}


def _event(seq, event_type, actor, *, qa_action=None, determination=None):
    return E(id=uuid.uuid4(), review_id="REV-2026-000001", sequence_number=seq,
             event_type=event_type, actor_user_id=actor,
             actor_email=f"{actor}@agtbi.com", actor_role="reviewer",
             rationale="a rationale long enough to satisfy the constraint",
             qa_action=qa_action, determination=determination)


# ── Step 20 — enrichment semantics ──────────────────────────────────────────

class TestEnrichmentSemantics:
    """The three distinctions the whole evidence model exists to preserve."""

    def test_source_unavailable_is_not_no_match(self):
        a = triage(_obs("SAM_GOV", ObservationState.SOURCE_UNAVAILABLE.value))
        b = triage(_obs("OIG_LEIE", ObservationState.NO_MATCH_OBSERVED.value))
        assert a.disposition is Triage.SOURCE_LIMITATION
        assert b.disposition is Triage.INFORMATIONAL_ONLY
        assert a.disposition is not b.disposition, (
            "an outage and an informative negative must not collapse together")

    def test_not_applicable_is_not_no_match(self):
        a = triage(_obs("CMS_PPEF_ENROLLMENT", ObservationState.LOOKUP_NOT_APPLICABLE.value,
                        applicability="NOT_APPLICABLE"))
        b = triage(_obs("CMS_PPEF_ENROLLMENT", ObservationState.NO_MATCH_OBSERVED.value))
        assert a.disposition is Triage.INFORMATIONAL_ONLY
        assert b.disposition is Triage.INFORMATIONAL_ONLY
        # Same disposition, but the STATES remain distinct in the evidence.
        assert ObservationState.LOOKUP_NOT_APPLICABLE != ObservationState.NO_MATCH_OBSERVED

    def test_methodology_pending_is_not_a_failure(self):
        d = triage(_obs("NPPES", ObservationState.MATCH_OBSERVED.value,
                        dimension="ADDRESS", comparison="CONFLICT"))
        assert d.disposition is Triage.METHODOLOGY_PENDING
        assert d.disposition is not Triage.READY_FOR_ANALYST
        assert d.blocked_by == "D4_ADDRESS_MATERIALITY"

    def test_insufficient_identifier_is_our_limit_not_the_entitys(self):
        d = triage(_obs("NPPES", ObservationState.INSUFFICIENT_IDENTIFIER.value))
        assert d.disposition is Triage.SOURCE_LIMITATION

    def test_our_error_is_distinguished_from_a_source_outage(self):
        err = triage(_obs("NPPES", ObservationState.ERROR.value))
        out = triage(_obs("NPPES", ObservationState.SOURCE_UNAVAILABLE.value))
        assert "OUR code" in err.reason or "our code" in err.reason.lower()
        assert err.reason != out.reason


# ── Step 22 — QA certification ──────────────────────────────────────────────

class TestQaCertification:

    ANALYST, QA = uuid.uuid4(), uuid.uuid4()

    def test_no_qa_approval_means_no_reportable_finding(self):
        """The single most important property in the system."""
        assert is_reportable([]) is False
        assert is_reportable([_event(1, E.ANALYST_DETERMINATION, self.ANALYST,
                                     determination="CONFIRM")]) is False

    def test_approve_makes_it_reportable(self):
        assert is_reportable([
            _event(1, E.ANALYST_DETERMINATION, self.ANALYST, determination="CONFIRM"),
            _event(2, E.QA_REVIEW, self.QA, qa_action=E.QA_APPROVE)]) is True

    def test_return_and_escalate_do_not(self):
        for action in (E.QA_RETURN, E.QA_ESCALATE):
            assert is_reportable([
                _event(1, E.ANALYST_DETERMINATION, self.ANALYST, determination="CONFIRM"),
                _event(2, E.QA_REVIEW, self.QA, qa_action=action)]) is False

    def test_a_later_return_revokes_an_earlier_approval(self):
        assert is_reportable([
            _event(1, E.ANALYST_DETERMINATION, self.ANALYST, determination="CONFIRM"),
            _event(2, E.QA_REVIEW, self.QA, qa_action=E.QA_APPROVE),
            _event(3, E.QA_REVIEW, self.QA, qa_action=E.QA_RETURN)]) is False

    def test_the_database_enforces_the_invariants_not_just_the_code(self):
        names = {c.name for c in E.__table__.constraints if c.name}
        for required in ("ck_review_event_qa_action",
                         "ck_review_event_qa_action_vocab",
                         "ck_review_event_escalation_complete",
                         "ck_review_event_rationale"):
            assert required in names, f"{required} must be a database constraint"

    def test_there_is_no_override_and_no_modify(self):
        assert "override" not in {c.name for c in E.__table__.columns}
        assert not hasattr(E, "MODIFY")

    def test_sod_exception_requires_a_grantor_and_a_reason(self):
        cols = {c.name for c in E.__table__.columns}
        assert "sod_exception_granted_by" in cols
        assert "sod_exception_reason" in cols

    def test_actor_role_is_captured_on_the_event(self):
        """A later demotion must not rewrite what a past decision was authorised by."""
        assert "actor_role" in {c.name for c in E.__table__.columns}


# ── Step 24/25 — reconstruction and reproducibility ─────────────────────────

class TestEvidenceVersioning:

    def test_exactly_one_version_is_current(self):
        assert current_rule_version() == APPROVED_RULE_VERSIONS[-1]
        assert current_rule_version() not in historical_rule_versions()

    def test_the_original_run_remains_queryable(self):
        assert "phase6-bulk-1.0.0" in historical_rule_versions()

    def test_versions_do_not_overlap(self):
        assert set(historical_rule_versions()).isdisjoint({current_rule_version()})


class TestReportReproducibility:
    """Same inputs, same verdict. Pure functions make this checkable."""

    RCE = {"address_line": "123 Main St.", "address_city": "Boston",
           "address_state": "MA", "address_postalCode": "02118"}
    NPPES = {"Provider First Line Business Practice Location Address": "123 MAIN STREET",
             "Provider Business Practice Location Address City Name": "BOSTON",
             "Provider Business Practice Location Address State Name": "MA"}

    def test_the_same_inputs_always_give_the_same_verdict(self):
        a = compare_to_nppes(self.RCE, self.NPPES)
        b = compare_to_nppes(self.RCE, self.NPPES)
        assert a.result is b.result
        assert a.field_matches == b.field_matches
        assert a.normalized_left == b.normalized_left

    def test_the_normalised_values_are_retained_so_a_verdict_can_be_rechecked(self):
        c = compare_to_nppes(self.RCE, self.NPPES)
        assert c.normalized_left and c.normalized_right, (
            "a verdict that cannot be re-derived is not evidence")

    def test_a_comparison_records_its_own_rule_version(self):
        assert compare_to_nppes(self.RCE, self.NPPES).rule_version


# ── Step 31 — failure and recovery ──────────────────────────────────────────

class TestFailsSafelyAndVisibly:

    def test_an_unreachable_source_yields_a_named_state_not_a_crash(self):
        c = compare_to_nppes({"address_line": "1 A St"}, None)
        assert c.result is AddressResult.SOURCE_UNAVAILABLE
        assert c.note, "a failure must explain itself"

    def test_absent_data_is_insufficient_not_conflict(self):
        c = compare_to_ppef({"address_city": "Boston"}, None)
        assert c.result is AddressResult.INSUFFICIENT_DATA
        assert c.result is not AddressResult.CONFLICT

    def test_an_unmapped_observation_state_is_parked_not_assumed_harmless(self):
        d = triage(_obs("NPPES", "A_STATE_THAT_DOES_NOT_EXIST"))
        assert d.disposition is Triage.METHODOLOGY_PENDING
        assert d.blocked_by == "UNMAPPED_STATE"

    def test_a_closed_gate_blocks_release_and_says_how_to_open_it(self):
        from app.reports.release_gates import DRAFT_WATERMARK, evaluate
        d = evaluate(evidence_rule_version=current_rule_version(),
                     qa_approved_findings=0, asserted_findings=0,
                     provenance_documented=False, report_rendered=True)
        assert d.is_cor_releasable is False
        assert d.label == DRAFT_WATERMARK
        assert all(r.remedy for r in d.closed)

    def test_a_superseded_evidence_version_cannot_be_reported_from(self):
        from app.reports.release_gates import Gate, evaluate
        d = evaluate(evidence_rule_version="phase6-bulk-1.0.0",
                     qa_approved_findings=0, asserted_findings=0,
                     provenance_documented=True, report_rendered=True)
        assert Gate.EVIDENCE in {r.gate for r in d.closed}

    def test_an_unknown_version_is_refused_loudly(self):
        from app.Tefca.evidence_version import historical_filter
        import sqlalchemy as sa
        try:
            historical_filter(sa.column("rule_version"), "phase6-bulk-9.9.9")
        except ValueError:
            pass
        else:
            raise AssertionError("an unknown version must be refused, not silently empty")


# ── Step 32 — auditability ──────────────────────────────────────────────────

class TestAuditability:

    def test_a_decision_event_records_who_what_when_and_why(self):
        cols = {c.name for c in E.__table__.columns}
        for required in ("actor_user_id", "actor_email", "actor_role",
                         "occurred_at", "event_type", "rationale"):
            assert required in cols, required

    def test_supersession_points_at_what_it_replaces(self):
        cols = {c.name for c in E.__table__.columns}
        assert "supersedes_decision_id" in cols
        assert "supersession_reason" in cols

    def test_an_observation_records_everything_needed_to_recheck_it(self):
        from app.Tefca.models import TEFCADimensionEvidence
        cols = {c.name for c in TEFCADimensionEvidence.__table__.columns}
        for required in ("source", "source_version_id", "dataset_version_anchor",
                         "identifier_searched", "observation_result",
                         "observation_hash", "rule_version", "retrieved_at"):
            assert required in cols, required

    def test_a_relationship_hop_names_its_component_and_source_row(self):
        from app.Tefca.models import EvidenceRelationshipPath
        cols = {c.name for c in EvidenceRelationshipPath.__table__.columns}
        for required in ("ppef_component", "source_row_key", "source_version_id",
                         "relationship_type", "hop_sequence"):
            assert required in cols, required


# ── Step 37 — no unsupported compliance language ────────────────────────────

class TestNoUnsupportedComplianceLanguage:

    def test_triage_never_emits_a_compliance_verdict(self):
        forbidden = {"PASS", "FAIL", "COMPLIANT", "NON_COMPLIANT", "VERIFIED"}
        for source in ("OIG_LEIE", "NPPES", "CMS_REVOCATION", "SAM_GOV",
                       "CMS_PPEF_ENROLLMENT"):
            for state in [s.value for s in ObservationState]:
                for comparison in (None, "CONFLICT", "EXACT_MATCH"):
                    d = triage(_obs(source, state, dimension="ADDRESS",
                                    comparison=comparison))
                    assert d.disposition.value not in forbidden

    def test_the_address_prohibition_is_taught_to_operators(self):
        from app.Tefca.learning_content import REGISTRY
        blocked = [p for p in REGISTRY.all_prohibited()
                   if p.unblocked_by and "D4_ADDRESS_MATERIALITY" in p.unblocked_by]
        assert blocked, "operators must be told they may not call a conflict a failure"
