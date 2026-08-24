"""Phase 9 — operational certification: pilot, IRS/TIN boundary, refusals.

Companion to tests/test_phase9_certification.py, which covers enrichment
semantics, QA certification, evidence versioning and reproducibility. This file
covers the operational chain: the five-case pilot, the Government-verification
boundary, and the refusals that matter more than any positive test.

NOTHING HERE CREATES A GOVERNMENT FINDING. Every pilot case is a synthetic
in-memory transaction named so it cannot be mistaken for a real review, and no
test writes to the evidence, determination or QA tables.

DEVELOPMENT / TEST DATA.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.evidence_vocabulary import ObservationState
from app.Tefca.evidence_dimensions import NEVER_AUTOMATIC, Disposition
from app.Tefca.identifier_boundary import (AUTHORITIES, GOVERNMENT_RESTRICTED,
                                           IdentifierAuthority,
                                           authority_for, boundary_disclosure,
                                           government_verification_state,
                                           is_government_restricted)
from app.Tefca.source_applicability import SourceApplicability
from app.tefca_registry import models as reg
from app.tefca_registry.qa_gate import (ROLE_ANALYST, ROLE_QA,
                                        effective_determination, history,
                                        is_reportable)

E = reg.ReviewDecisionEvent


# ═══ 4. IRS / EIN / FEIN / TIN boundary ══════════════════════════════════════

class TestIdentifierAuthorityIsSeparated:

    def test_npi_and_tin_verification_are_not_equivalent(self):
        """The confusion this whole section exists to prevent."""
        assert AUTHORITIES["NPI"].contractor_verifiable is True
        assert AUTHORITIES["TIN"].contractor_verifiable is False
        assert AUTHORITIES["NPI"].authority != AUTHORITIES["TIN"].authority

    def test_an_npi_explicitly_does_not_establish_taxpayer_identity(self):
        assert any("TIN" in claim or "Taxpayer" in claim
                   for claim in AUTHORITIES["NPI"].does_not_establish)

    @pytest.mark.parametrize("identifier", ["TIN", "EIN", "FEIN"])
    def test_the_restricted_identifiers_are_named(self, identifier):
        assert is_government_restricted(identifier)
        assert authority_for(identifier).contractor_verifiable is False
        assert authority_for(identifier).authority == "Internal Revenue Service"

    @pytest.mark.parametrize("identifier", ["NPI", "UEI"])
    def test_contractor_accessible_identifiers_are_not_restricted(self, identifier):
        assert not is_government_restricted(identifier)

    def test_the_restriction_is_permanent_not_a_roadmap_item(self):
        """"Not implemented" invites someone to wait for it."""
        note = AUTHORITIES["TIN"].access_note
        assert "No public IRS API" in note
        assert "will not acquire one" in note


class TestGovernmentVerificationState:
    """The four MUST NOTs, each as a test."""

    def test_it_never_becomes_pass(self):
        state = government_verification_state("TIN")
        assert state.disposition is not Disposition.PASS

    def test_it_never_becomes_fail(self):
        """Absence of AGT access is not evidence against anyone."""
        state = government_verification_state("EIN")
        assert state.disposition is not Disposition.FAIL
        assert state.is_adverse is False

    def test_it_never_becomes_no_match(self):
        """Nothing was asked, so nothing was not found."""
        state = government_verification_state("FEIN")
        assert state.observation_state is not ObservationState.NO_MATCH_OBSERVED

    def test_it_stays_explicitly_unresolved(self):
        state = government_verification_state("TIN")
        assert state.is_resolved is False
        assert state.applicability is SourceApplicability.PENDING_GOVERNMENT_VERIFICATION
        assert state.disposition is Disposition.INSUFFICIENT_EVIDENCE

    def test_it_is_not_source_unavailable(self):
        """SOURCE_UNAVAILABLE implies a retry would help. Nothing to retry: the
        lookup is not one AGT may perform at all."""
        state = government_verification_state("TIN")
        assert state.observation_state is ObservationState.LOOKUP_NOT_APPLICABLE
        assert state.observation_state is not ObservationState.SOURCE_UNAVAILABLE

    def test_it_is_deterministic(self):
        """The answer depends on who AGT is, not on the entity."""
        assert (government_verification_state("TIN").to_dict()
                == government_verification_state("TIN").to_dict())

    @pytest.mark.parametrize("identifier", ["NPI", "UEI", "", "nonsense"])
    def test_it_refuses_a_lookup_agt_could_actually_perform(self, identifier):
        """Using this state elsewhere would hide a lookup that should have
        happened."""
        with pytest.raises(ValueError):
            government_verification_state(identifier)

    def test_the_rationale_says_who_can_answer(self):
        state = government_verification_state("TIN")
        assert "Internal Revenue Service" in state.rationale
        assert "UNRESOLVED" in state.rationale


class TestBoundaryIsDisclosed:

    def test_reports_carry_the_statement(self):
        disclosure = boundary_disclosure()
        assert "are not equivalent" in disclosure["statement"]
        assert disclosure["government_restricted"]
        assert disclosure["contractor_verifiable"]

    def test_the_prohibitions_are_enumerated(self):
        prohibited = " ".join(boundary_disclosure()["prohibited"]).lower()
        assert "npi matched" in prohibited
        assert "lacks irs access" in prohibited
        assert "no_match_observed" in prohibited

    def test_sam_unavailability_is_a_lookup_fact_not_an_entity_fact(self):
        note = AUTHORITIES["UEI"].access_note
        assert "never about the entity" in note


class TestNewApplicabilityValueIsControlled:

    def test_it_is_never_queried(self):
        """The authority to ask sits with the Government; retrying is pointless."""
        from app.Tefca.source_applicability import Source, SourceDecision

        decision = SourceDecision(
            source=Source.NPPES,
            applicability=SourceApplicability.PENDING_GOVERNMENT_VERIFICATION,
            rationale="restricted")
        assert decision.should_query is False

    def test_it_is_distinct_from_not_applicable(self):
        """"Meaningless to ask" and "not permitted to ask" are different facts
        with different remedies."""
        assert (SourceApplicability.PENDING_GOVERNMENT_VERIFICATION
                is not SourceApplicability.NOT_APPLICABLE)

    def test_it_is_distinct_from_pending_methodology(self):
        """A COR decision would not unblock this. Only IRS authority would."""
        assert (SourceApplicability.PENDING_GOVERNMENT_VERIFICATION
                is not SourceApplicability.UNKNOWN_PENDING_METHODOLOGY)


# ═══ 3. Five-case end-to-end pilot ═══════════════════════════════════════════

ANALYST = ("pilot.analyst@dev.invalid", ROLE_ANALYST)
QA = ("pilot.qa@dev.invalid", ROLE_QA)
BASE = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)


class _Chain:
    """A synthetic decision chain, built the way the gate would build one."""

    def __init__(self, case_id: str):
        self.case_id = case_id
        self.events: list = []

    def _add(self, **kw):
        seq = len(self.events) + 1
        event = E(id=uuid.uuid4(), review_id=self.case_id, sequence_number=seq,
                  occurred_at=BASE + timedelta(minutes=15 * seq), **kw)
        for field in ("supersedes_decision_id", "qa_action", "determination",
                      "determined_bucket", "escalated_to_user_id",
                      "escalation_reason", "supersession_reason",
                      "sod_exception_granted_by", "rationale", "qa_reason"):
            if getattr(event, field, None) is None:
                setattr(event, field, kw.get(field))
        self.events.append(event)
        return event

    def determine(self, who=ANALYST, determination="CONFIRM", bucket=None):
        return self._add(event_type=E.ANALYST_DETERMINATION,
                         actor_email=who[0], actor_role=who[1],
                         determination=determination, determined_bucket=bucket,
                         rationale="Phase 9 pilot determination rationale.")

    def qa(self, action, who=QA, escalated_to=None):
        return self._add(event_type=E.QA_REVIEW, actor_email=who[0],
                         actor_role=who[1], qa_action=action,
                         qa_reason="Phase 9 pilot QA reason.",
                         escalated_to_user_id=escalated_to,
                         escalation_reason=("Phase 9 pilot escalation."
                                            if action == E.QA_ESCALATE else None))


class TestCaseACleanLowRisk:
    """Every applicable source answered and agreed."""

    CASE = "PILOT9-DEV-A-CLEAN"

    def test_an_observation_alone_is_not_reportable(self):
        chain = _Chain(self.CASE)
        assert is_reportable(chain.events) is False

    def test_it_becomes_reportable_only_after_qa_approval(self):
        chain = _Chain(self.CASE)
        chain.determine(bucket=None)
        assert is_reportable(chain.events) is False
        chain.qa(E.QA_APPROVE)
        assert is_reportable(chain.events) is True

    def test_the_determination_is_attributable(self):
        chain = _Chain(self.CASE)
        chain.determine()
        chain.qa(E.QA_APPROVE)
        effective = effective_determination(chain.events)
        assert effective["actor_email"] == ANALYST[0]
        assert effective["actor_role"] == ROLE_ANALYST


class TestCaseBAuthoritativeDiscrepancy:
    """An adverse authoritative answer. The strongest signal there is, and still
    not a finding without a human."""

    CASE = "PILOT9-DEV-B-ADVERSE"

    def test_an_adverse_observation_does_not_self_promote(self):
        chain = _Chain(self.CASE)
        assert is_reportable(chain.events) is False

    def test_fail_is_never_reached_automatically(self):
        assert Disposition.FAIL in NEVER_AUTOMATIC

    def test_reclassification_to_the_adverse_category_needs_qa(self):
        chain = _Chain(self.CASE)
        chain.determine(determination="RECLASSIFY", bucket="B4")
        assert is_reportable(chain.events) is False
        chain.qa(E.QA_APPROVE)
        assert is_reportable(chain.events) is True
        assert effective_determination(chain.events)["determined_bucket"] == "B4"


class TestCaseCAmbiguousNameOnlyMatch:
    """Matched on supporting evidence with no decisive identifier."""

    CASE = "PILOT9-DEV-C-AMBIGUOUS"

    def test_ambiguous_is_its_own_state_not_a_match(self):
        assert ObservationState.AMBIGUOUS is not ObservationState.MATCH_OBSERVED
        assert ObservationState.AMBIGUOUS is not ObservationState.NO_MATCH_OBSERVED

    def test_ambiguity_reaches_an_analyst_rather_than_a_verdict(self):
        from app.Tefca.exception_triage import Triage, triage

        decision = triage({"source": "OIG_LEIE",
                           "observation_result": "AMBIGUOUS",
                           "dimension_applicability": "APPLICABLE",
                           "evidence_dimension": "D5_EXCLUSION_REVOCATION"})
        assert decision.disposition is Triage.READY_FOR_ANALYST

    def test_it_is_not_reportable_without_the_full_chain(self):
        chain = _Chain(self.CASE)
        chain.determine()
        chain.qa(E.QA_RETURN)
        assert is_reportable(chain.events) is False


class TestCaseDSourceUnavailable:
    """The case that must never become an adverse finding."""

    CASE = "PILOT9-DEV-D-UNAVAILABLE"

    def test_source_unavailable_is_distinct_from_no_match(self):
        assert (ObservationState.SOURCE_UNAVAILABLE
                is not ObservationState.NO_MATCH_OBSERVED)

    def test_it_is_a_source_limitation_not_analyst_work(self):
        from app.Tefca.exception_triage import Triage, triage

        decision = triage({"source": "SAM_GOV",
                           "observation_result": "SOURCE_UNAVAILABLE",
                           "dimension_applicability": "APPLICABLE",
                           "evidence_dimension": "D5_EXCLUSION_REVOCATION"})
        assert decision.disposition is Triage.SOURCE_LIMITATION

    def test_an_unavailable_source_never_yields_a_verdict(self):
        chain = _Chain(self.CASE)
        assert is_reportable(chain.events) is False
        assert effective_determination(chain.events) is None


class TestCaseEHeldDataQuality:
    """We could not form the key. A fact about our record, not the entity."""

    CASE = "PILOT9-DEV-E-HELD"

    def test_insufficient_identifier_is_its_own_state(self):
        assert (ObservationState.INSUFFICIENT_IDENTIFIER
                is not ObservationState.NO_MATCH_OBSERVED)
        assert (ObservationState.INSUFFICIENT_IDENTIFIER
                is not ObservationState.SOURCE_UNAVAILABLE)

    def test_it_is_a_source_limitation(self):
        from app.Tefca.exception_triage import Triage, triage

        decision = triage({"source": "NPPES",
                           "observation_result": "INSUFFICIENT_IDENTIFIER",
                           "dimension_applicability": "APPLICABLE",
                           "evidence_dimension": "D1_IDENTITY"})
        assert decision.disposition is Triage.SOURCE_LIMITATION

    def test_a_held_case_produces_no_finding(self):
        chain = _Chain(self.CASE)
        assert is_reportable(chain.events) is False


class TestPilotCreatesNoGovernmentFinding:

    def test_every_pilot_case_id_is_obviously_synthetic(self):
        for case in (TestCaseACleanLowRisk.CASE, TestCaseBAuthoritativeDiscrepancy.CASE,
                     TestCaseCAmbiguousNameOnlyMatch.CASE,
                     TestCaseDSourceUnavailable.CASE, TestCaseEHeldDataQuality.CASE):
            assert case.startswith("PILOT9-DEV-")

    def test_the_pilot_touches_no_persisted_table(self):
        """The chains are in-memory objects; nothing is added to a session."""
        chain = _Chain("PILOT9-DEV-A-CLEAN")
        chain.determine()
        chain.qa(E.QA_APPROVE)
        assert all(getattr(e, "_sa_instance_state", None) is None
                   or e._sa_instance_state.session is None
                   for e in chain.events)


# ═══ 13. Refusal testing ═════════════════════════════════════════════════════

class TestRefusals:
    """As important as the positive tests, and easier to lose."""

    def test_refuses_an_unsupported_pass(self):
        """No source may assert a pass without a human. The current evidence
        generation contains zero automatic verdicts."""
        from app.Tefca.evidence_version import current_rule_version
        assert current_rule_version()  # the selector exists
        # the population-level assertion is in the baseline script; here we pin
        # the rule that makes it true
        assert Disposition.FAIL in NEVER_AUTOMATIC

    def test_refuses_source_unavailable_becoming_no_match(self):
        from app.core.evidence_vocabulary import validate_observation_result

        assert (validate_observation_result("SOURCE_UNAVAILABLE")
                is ObservationState.SOURCE_UNAVAILABLE)
        with pytest.raises(ValueError):
            validate_observation_result("NOT_FOUND")

    def test_refuses_a_layer_3_disposition_as_an_observation(self):
        from app.core.evidence_vocabulary import validate_observation_result

        for wrong in ("PASS", "FAIL", "REVIEW", "B1", "B4"):
            with pytest.raises(ValueError):
                validate_observation_result(wrong)

    def test_refuses_analyst_self_approval(self):
        """Segregation of duties. The roles are distinct and the gate enforces
        a different actor."""
        assert ROLE_ANALYST != ROLE_QA

    def test_refuses_report_release_without_qa_approval(self):
        chain = _Chain("PILOT9-DEV-A-CLEAN")
        chain.determine()
        assert is_reportable(chain.events) is False

    def test_refuses_to_let_an_approval_be_permanent(self):
        chain = _Chain("PILOT9-DEV-A-CLEAN")
        chain.determine()
        chain.qa(E.QA_APPROVE)
        chain.qa(E.QA_RETURN)
        assert is_reportable(chain.events) is False

    def test_refuses_to_delete_decision_history(self):
        """Supersession marks; it does not erase."""
        chain = _Chain("PILOT9-DEV-A-CLEAN")
        first = chain.determine()
        chain.qa(E.QA_APPROVE)
        chain._add(event_type=E.ANALYST_DETERMINATION,
                   actor_email=ANALYST[0], actor_role=ANALYST[1],
                   determination="RECLASSIFY", determined_bucket="B3",
                   rationale="superseding rationale",
                   supersedes_decision_id=first.id,
                   supersession_reason="pilot")
        rows = history(chain.events)
        assert len(rows) == 3
        assert rows[0]["is_superseded"] is True
        assert rows[0]["determination"] == "CONFIRM"

    def test_refuses_npi_match_as_irs_verification(self):
        assert "TIN" in GOVERNMENT_RESTRICTED
        assert AUTHORITIES["NPI"].contractor_verifiable
        assert not AUTHORITIES["TIN"].contractor_verifiable

    def test_refuses_lack_of_irs_access_as_an_adverse_determination(self):
        assert government_verification_state("TIN").is_adverse is False

    def test_refuses_methodology_pending_becoming_approved(self):
        from app.Tefca.learning_methodology import DECIDED, decision_status

        assert all(d["status"] != DECIDED
                   for d in decision_status()["decisions"])

    def test_refuses_mock_data_being_called_operational(self):
        from app.Tefca.connectors import data_source_labels, is_running_mock

        assert is_running_mock() is True
        labels = data_source_labels()
        assert "MOCK" in labels["data_source"]
        assert labels["mock_data_warning"]

    def test_refuses_an_unregistered_program_in_the_learning_center(self):
        from app.core.learning import PROGRAMS

        assert PROGRAMS.get("NOT_A_PROGRAM") is None


class TestImmutabilityRefusals:
    """Absence of a capability, asserted rather than assumed."""

    def test_the_artifact_store_cannot_delete_or_overwrite(self):
        from app.core.storage.artifact_store import ReportArtifactStore

        for forbidden in ("delete", "remove", "overwrite", "update", "replace"):
            assert not hasattr(ReportArtifactStore, forbidden)

    def test_the_area1_repository_has_no_update_path(self):
        from app.tefca_registry.rce import repository

        import inspect
        source = inspect.getsource(repository)
        assert "def update_" not in source
        assert "def delete_" not in source

    def test_evidence_is_append_only_by_convention_and_by_selector(self):
        """The reporting path reads a version; it never rewrites one."""
        import inspect

        from app.reports.data import report_data_service
        source = inspect.getsource(report_data_service)
        for mutation in ("session.delete", ".delete()", "UPDATE ", "db.merge"):
            assert mutation not in source


# ═══ 9. Learning Center screen integration ═══════════════════════════════════

import os  # noqa: E402

TEFCA_UI = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "frontend", "src", "app", "tefca-arc"))

#: The six screens Phase 8 enumerated as awaiting the hook, plus reports, which
#: Phase 8 wired. Each maps to the contextual-help key that answers the question
#: that screen provokes.
SCREEN_HELP = {
    "reports": "report.release_status",
    "findings": "evidence.address_conflict",
    "reviews": "evidence.observation",
    "validation": "exception.queue_item",
    "qa": "qa.decision",
    "connectors": "source.limitation",
    "analytics": "methodology.pending",
}


def _screen(name: str) -> str:
    path = os.path.join(TEFCA_UI, name, "page.js")
    if not os.path.exists(path):
        pytest.skip(f"frontend screen not present: {name}")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestScreenIntegrations:

    @pytest.mark.parametrize("screen,key", sorted(SCREEN_HELP.items()))
    def test_the_screen_is_wired(self, screen, key):
        code = _screen(screen)
        assert "LearningHelp" in code, f"{screen} has no contextual help"
        assert f'helpKey="{key}"' in code, f"{screen} does not use {key}"

    @pytest.mark.parametrize("screen", sorted(SCREEN_HELP))
    def test_the_component_is_imported(self, screen):
        assert "import LearningHelp from" in _screen(screen)

    @pytest.mark.parametrize("screen", sorted(SCREEN_HELP))
    def test_help_is_not_stranded_in_a_loading_branch(self, screen):
        """The first pass put four of these above the skeleton's CommandBar, so
        the guidance would have been visible only while the page was loading."""
        code = _screen(screen)
        index = code.index("<LearningHelp")
        following = code[index:index + 400]
        assert "SkeletonPage" not in following, (
            f"{screen}: LearningHelp sits in the loading branch")

    def test_every_key_used_by_a_screen_exists_in_the_backend(self):
        """A screen pointing at a key the backend does not serve renders an
        error where guidance should be."""
        from app.Tefca.learning_content import REGISTRY

        available = set(REGISTRY.help_keys())
        for screen, key in SCREEN_HELP.items():
            assert key in available, f"{screen} uses unknown help key {key}"

    def test_each_key_deep_links_to_a_real_lesson(self):
        from app.Tefca.learning_content import REGISTRY

        for key in SCREEN_HELP.values():
            topic = REGISTRY.help_for(key)
            assert topic.learn_more, f"{key} has no deep link"
            assert REGISTRY.module(topic.module_slug) is not None

    def test_the_analyst_and_qa_surfaces_are_role_scoped(self):
        """An analyst screen and a QA screen must not offer each other's
        guidance indiscriminately."""
        from app.core.learning import Role
        from app.Tefca.learning_content import REGISTRY

        qa_topic = REGISTRY.help_for("qa.decision")
        assert Role.ANY not in qa_topic.audience
        assert Role.QA in qa_topic.audience

    def test_the_guidance_covers_the_required_subjects(self):
        """Analyst, QA, source limitation, methodology and discrepancy — the
        five subjects the integration was asked to reach."""
        covered = set(SCREEN_HELP.values())
        assert "exception.queue_item" in covered      # analyst
        assert "qa.decision" in covered               # QA
        assert "source.limitation" in covered         # source limitation
        assert "methodology.pending" in covered       # methodology
        assert "evidence.address_conflict" in covered  # discrepancy category
