"""The golden matrix for evidence-methodology hardening.

THE DEFECT UNDER TEST
─────────────────────
"1 of 3 sources agree" as a determination. It treats NPPES, SAM.gov and LEIE as
interchangeable votes on one question. They are not, and the consequences are
concrete: an HIE with no NPI looks like a provider hiding one, and a SAM.gov
absence — non-determinative for almost every TEFCA participant — looks like a
finding.

Every case below asserts the chain the methodology requires:

    input -> classification -> control -> applicable sources -> evidence states
          -> verification coverage -> preliminary assessment

No Government record is read, written or exported. Every fixture is synthetic.
"""

from __future__ import annotations

import pytest

from app.core.evidence_vocabulary import ObservationState
from app.tefca_registry.verification_methodology import (
    APPLICABILITY_MATRIX, Control, ContractualCompliance, EntityClass,
    EntityVerification, EvidenceState, NON_ADVERSE_STATES, Observation,
    PARTICIPATION_ANCHOR, Requirement, assess_control, classify_entity,
    ManualEvidence, controls_for, evidence_state_for,
    preliminary_assessment)

OBS = ObservationState


def rec(org_type, **extra):
    base = {"sequoia_org_type": org_type, "name": "SYNTHETIC ORG"}
    base.update(extra)
    return base


def obs(source, state, **kw):
    return Observation(source=source, state=state.value, **kw)


def control_of(assessment_list, control):
    return next(a for a in assessment_list if a.control is control)


# ═══ 1-8  classification and applicability ══════════════════════════════════

@pytest.mark.parametrize("org_type,expected", [
    ("Provider Organization", EntityClass.PROVIDER),
    ("Regional Hospital System", EntityClass.HOSPITAL_HEALTH_SYSTEM),
    ("Health Information Exchange", EntityClass.HIE_HIN),
    ("QHIN", EntityClass.HIE_HIN),
    ("Health IT Developer", EntityClass.HEALTH_IT_ORGANIZATION),
    ("Managed Care Payer", EntityClass.HEALTH_PLAN_PAYER),
    ("State Public Health Department", EntityClass.PUBLIC_HEALTH_ORGANIZATION),
    ("Federal Agency", EntityClass.FEDERAL_GOVERNMENT_ORGANIZATION),
])
def test_01_08_entities_classify_from_the_delivered_record(org_type, expected):
    """Classification uses the DELIVERED record. No external source is asked
    whether an organisation participates in TEFCA — that is D1."""
    result = classify_entity(rec(org_type))
    assert result.entity_class is expected
    assert result.signal == "sequoia_org_type"
    assert org_type.lower()[:6] in result.rationale.lower() or result.signal_value


def test_08_an_unrecognised_entity_asks_a_human_rather_than_guessing():
    """Guessing PROVIDER because most entities are providers would make the
    matrix silently wrong for exactly the entities that need care."""
    result = classify_entity(rec("Something Nobody Mapped Yet"))
    assert result.entity_class is EntityClass.REQUIRES_CLASSIFICATION
    assert "human" in result.rationale.lower()

    # and it does NOT inherit a provider's obligations
    controls = {c.control for c in controls_for(result.entity_class)}
    assert Control.PROVIDER_ENUMERATION not in controls
    assert Control.EXCLUSION_SCREENING not in controls


# ═══ 1  provider with a valid NPI ═══════════════════════════════════════════

def test_01_provider_with_a_valid_npi_is_verified():
    assessment = preliminary_assessment("9.99.999.P1", rec("Provider Practice"), [
        obs("rce", OBS.MATCH_OBSERVED),
        obs("nppes", OBS.MATCH_OBSERVED, matched_identifier="1234567893"),
        obs("leie", OBS.NO_MATCH_OBSERVED),
        obs("pecos", OBS.MATCH_OBSERVED),
        obs("sam_gov", OBS.NO_MATCH_OBSERVED),
    ])
    assert control_of(assessment.controls,
                      Control.PROVIDER_ENUMERATION).state is EvidenceState.VERIFIED
    assert assessment.coverage.satisfied >= 4
    assert assessment.contractual_compliance is ContractualCompliance.SATISFIED
    assert assessment.entity_verification in (
        EntityVerification.VERIFIED, EntityVerification.PARTIALLY_VERIFIED)


# ═══ 2  provider with conflicting attributes ════════════════════════════════

def test_02_conflicting_npi_attributes_are_a_potential_finding_not_a_verdict():
    """A CONFLICT is the strongest thing automation may say — and it is still
    only POTENTIAL_FINDING, never NON_COMPLIANT."""
    assessment = preliminary_assessment("9.99.999.P2", rec("Provider Practice"), [
        obs("rce", OBS.MATCH_OBSERVED),
        obs("nppes", OBS.MATCH_OBSERVED, contradicts=True,
            matched_name="A COMPLETELY DIFFERENT ORGANISATION",
            detail="Returned legal name is a different organisation."),
        obs("leie", OBS.NO_MATCH_OBSERVED),
    ])
    identity = control_of(assessment.controls, Control.ENTITY_IDENTITY)
    assert identity.state is EvidenceState.CONFLICT
    assert assessment.entity_verification is EntityVerification.CONFLICTING
    assert assessment.contractual_compliance is \
        ContractualCompliance.POTENTIAL_FINDING
    assert assessment.contractual_compliance is not \
        ContractualCompliance.NON_COMPLIANT


# ═══ 3-4  entities that legitimately have no NPI ════════════════════════════

@pytest.mark.parametrize("org_type", ["Health Information Exchange",
                                      "Health IT Developer"])
def test_03_04_no_npi_is_not_applicable_not_a_discrepancy(org_type):
    """The headline case. An HIE or Health IT organisation has no NPI, and that
    must be NOT_APPLICABLE — never NOT_FOUND, and never a finding."""
    assessment = preliminary_assessment("9.99.999.H", rec(org_type), [
        obs("rce", OBS.MATCH_OBSERVED),
        obs("sam_gov", OBS.NO_MATCH_OBSERVED),
    ])
    enumeration = control_of(assessment.controls, Control.PROVIDER_ENUMERATION)
    assert enumeration.state is EvidenceState.NOT_APPLICABLE
    assert enumeration.requirement is Requirement.NOT_APPLICABLE
    assert "no clinical care" in enumeration.rationale.lower() or \
        "not enumerated" in enumeration.rationale.lower()

    # it does not count against coverage at all
    assert assessment.coverage.not_applicable >= 1
    assert assessment.contractual_compliance is not \
        ContractualCompliance.POTENTIAL_FINDING


# ═══ 5-7  payer, public health, federal ═════════════════════════════════════

def test_05_payer_is_not_measured_against_provider_enrolment():
    assessment = preliminary_assessment("9.99.999.PAY", rec("Health Plan"), [
        obs("rce", OBS.MATCH_OBSERVED),
    ])
    for control in (Control.PROVIDER_ENUMERATION, Control.MEDICARE_ENROLMENT):
        assert control_of(assessment.controls, control).state is \
            EvidenceState.NOT_APPLICABLE


def test_06_public_health_enumeration_is_corroborative_not_required():
    """Some public health agencies are enumerated; many are not."""
    spec = next(c for c in controls_for(EntityClass.PUBLIC_HEALTH_ORGANIZATION)
                if c.control is Control.PROVIDER_ENUMERATION)
    assert spec.requirement is Requirement.CORROBORATIVE

    assessment = preliminary_assessment(
        "9.99.999.PH", rec("State Public Health Department"),
        [obs("rce", OBS.MATCH_OBSERVED), obs("nppes", OBS.NO_MATCH_OBSERVED)])
    assert control_of(assessment.controls,
                      Control.PROVIDER_ENUMERATION).state is EvidenceState.NOT_FOUND
    assert assessment.contractual_compliance is not \
        ContractualCompliance.POTENTIAL_FINDING


def test_07_federal_organisation_is_not_exclusion_screened():
    assessment = preliminary_assessment("9.99.999.FED", rec("Federal Agency"),
                                        [obs("rce", OBS.MATCH_OBSERVED)])
    assert control_of(assessment.controls,
                      Control.EXCLUSION_SCREENING).state is \
        EvidenceState.NOT_APPLICABLE


# ═══ 9-11  the three non-adverse states ═════════════════════════════════════

def test_09_sam_absence_is_non_determinative():
    """SAM.gov registration is not a TEFCA participation requirement."""
    assessment = preliminary_assessment("9.99.999.S", rec("Provider Practice"), [
        obs("rce", OBS.MATCH_OBSERVED),
        obs("nppes", OBS.MATCH_OBSERVED),
        obs("leie", OBS.NO_MATCH_OBSERVED),
        obs("sam_gov", OBS.NO_MATCH_OBSERVED),
    ])
    award = control_of(assessment.controls, Control.FEDERAL_AWARD_ELIGIBILITY)
    assert award.requirement is Requirement.CORROBORATIVE
    assert award.state is EvidenceState.NOT_FOUND
    assert "non-determinative" in award.rationale.lower() or \
        "corroborative" in award.rationale.lower()
    assert assessment.contractual_compliance is ContractualCompliance.SATISFIED


def test_10_npi_not_applicable_never_becomes_not_found():
    assert evidence_state_for(OBS.LOOKUP_NOT_APPLICABLE.value) is \
        EvidenceState.NOT_APPLICABLE
    assert evidence_state_for(OBS.NO_MATCH_OBSERVED.value) is \
        EvidenceState.NOT_FOUND
    assert EvidenceState.NOT_APPLICABLE is not EvidenceState.NOT_FOUND


def test_11_a_source_outage_is_a_fact_about_the_source():
    assessment = preliminary_assessment("9.99.999.O", rec("Provider Practice"), [
        obs("rce", OBS.MATCH_OBSERVED),
        obs("nppes", OBS.SOURCE_UNAVAILABLE),
        obs("leie", OBS.SOURCE_UNAVAILABLE),
    ])
    enumeration = control_of(assessment.controls, Control.PROVIDER_ENUMERATION)
    assert enumeration.state is EvidenceState.SOURCE_UNAVAILABLE
    assert "not about the entity" in enumeration.rationale.lower()
    assert assessment.contractual_compliance is \
        ContractualCompliance.UNABLE_TO_DETERMINE
    assert assessment.contractual_compliance is not \
        ContractualCompliance.POTENTIAL_FINDING


# ═══ 12-13  exclusion screening ═════════════════════════════════════════════

def test_12_leie_clear_is_a_positive_negative():
    assessment = preliminary_assessment("9.99.999.L1", rec("Provider Practice"), [
        obs("rce", OBS.MATCH_OBSERVED), obs("nppes", OBS.MATCH_OBSERVED),
        obs("leie", OBS.NO_MATCH_OBSERVED,
            dataset_version="LEIE-2026-08", match_method="bulk_screening"),
    ])
    screening = control_of(assessment.controls, Control.EXCLUSION_SCREENING)
    # Absence is what SATISFIES this control — a provider not on the LEIE is the
    # good outcome. Sending every clean screen to a human would make the queue
    # unusable and teach reviewers to click through.
    assert screening.state is EvidenceState.VERIFIED
    assert "satisfies this control" in screening.rationale.lower()


def test_13_a_potential_adverse_match_is_a_conflict_and_stops_there():
    assessment = preliminary_assessment("9.99.999.L2", rec("Provider Practice"), [
        obs("rce", OBS.MATCH_OBSERVED), obs("nppes", OBS.MATCH_OBSERVED),
        obs("leie", OBS.MATCH_OBSERVED, contradicts=True,
            match_method="oig_authorized_search",
            detail="Synthetic exclusion match on the screened identity."),
    ])
    screening = control_of(assessment.controls, Control.EXCLUSION_SCREENING)
    assert screening.state is EvidenceState.CONFLICT
    assert assessment.contractual_compliance is \
        ContractualCompliance.POTENTIAL_FINDING
    assert assessment.contractual_compliance is not \
        ContractualCompliance.NON_COMPLIANT


# ═══ 14-15  ambiguity and manual evidence ═══════════════════════════════════

def test_14_an_ambiguous_match_goes_to_a_human_not_to_conflict():
    """Cardinality and weak matches are not contradictions."""
    for state in (OBS.MULTIPLE_MATCHES, OBS.AMBIGUOUS,
                  OBS.INSUFFICIENT_IDENTIFIER, OBS.ERROR):
        assert evidence_state_for(state.value) is \
            EvidenceState.MANUAL_VERIFICATION_REQUIRED, state


def test_15_manual_verification_is_a_first_class_state():
    assessment = preliminary_assessment("9.99.999.M", rec("Provider Practice"), [
        obs("rce", OBS.MATCH_OBSERVED),
        obs("nppes", OBS.MULTIPLE_MATCHES,
            detail="Two synthetic records share the delivered name."),
        obs("leie", OBS.NO_MATCH_OBSERVED),
    ])
    enumeration = control_of(assessment.controls, Control.PROVIDER_ENUMERATION)
    assert enumeration.state is EvidenceState.MANUAL_VERIFICATION_REQUIRED
    assert assessment.coverage.requires_analyst >= 1
    assert "require analyst review" in assessment.coverage.summary()


# ═══ 16  a genuine potential finding ════════════════════════════════════════

def test_16_a_genuine_potential_finding_is_still_only_potential():
    assessment = preliminary_assessment("9.99.999.F", rec("Provider Practice"), [
        obs("rce", OBS.MATCH_OBSERVED),
        obs("nppes", OBS.MATCH_OBSERVED, contradicts=True,
            detail="Delivered legal name does not match the enumerated name."),
        obs("leie", OBS.MATCH_OBSERVED, contradicts=True,
            detail="Synthetic exclusion match."),
    ])
    assert assessment.contractual_compliance is \
        ContractualCompliance.POTENTIAL_FINDING
    # automation may never reach the adverse conclusion
    assert assessment.contractual_compliance is not \
        ContractualCompliance.NON_COMPLIANT


# ═══ the two rules that make all of the above hold ══════════════════════════

def test_the_three_non_adverse_states_can_never_produce_a_finding():
    """The rule this module exists for, asserted directly and exhaustively."""
    assert NON_ADVERSE_STATES == {EvidenceState.NOT_FOUND,
                                  EvidenceState.NOT_APPLICABLE,
                                  EvidenceState.SOURCE_UNAVAILABLE}

    for state in (OBS.NO_MATCH_OBSERVED, OBS.LOOKUP_NOT_APPLICABLE,
                  OBS.SOURCE_UNAVAILABLE):
        for org in ("Provider Practice", "Health Information Exchange",
                    "Health Plan", "Federal Agency"):
            assessment = preliminary_assessment(
                "9.99.999.X", rec(org),
                [obs(s, state) for s in
                 ("rce", "nppes", "leie", "pecos", "sam_gov")])
            assert assessment.contractual_compliance is not \
                ContractualCompliance.POTENTIAL_FINDING, (state, org)
            assert assessment.contractual_compliance is not \
                ContractualCompliance.NON_COMPLIANT, (state, org)


def test_automation_can_never_emit_non_compliant():
    """NON_COMPLIANT is reachable only after a human decision that independent
    QA approved. No input to the automated path may produce it."""
    import itertools

    states = list(ObservationState)
    for org in ("Provider Practice", "Health Information Exchange",
                "Federal Agency", "Unmapped Thing"):
        for combo in itertools.product(states, repeat=2):
            observations = [obs("rce", combo[0]), obs("nppes", combo[1]),
                            obs("leie", combo[0], contradicts=True)]
            result = preliminary_assessment("9.99.999.A", rec(org), observations)
            assert result.contractual_compliance is not \
                ContractualCompliance.NON_COMPLIANT


def test_participation_is_anchored_to_the_delivered_population():
    """D1. No external database decides whether an entity participates."""
    assert PARTICIPATION_ANCHOR == "RCE_DELIVERED_POPULATION"
    assessment = preliminary_assessment(
        "9.99.999.D1", rec("Provider Practice"),
        [obs("nppes", OBS.NO_MATCH_OBSERVED),
         obs("sam_gov", OBS.NO_MATCH_OBSERVED),
         obs("leie", OBS.NO_MATCH_OBSERVED)])
    assert assessment.participation_anchor == "RCE_DELIVERED_POPULATION"
    # absent from every external source, still not a finding
    assert assessment.contractual_compliance is \
        ContractualCompliance.UNABLE_TO_DETERMINE


# ═══ coverage replaces the source-vote headline ═════════════════════════════

def test_coverage_is_stated_in_controls_not_source_votes():
    assessment = preliminary_assessment("9.99.999.C", rec("Provider Practice"), [
        obs("rce", OBS.MATCH_OBSERVED), obs("nppes", OBS.MATCH_OBSERVED),
        obs("pecos", OBS.MATCH_OBSERVED), obs("leie", OBS.NO_MATCH_OBSERVED),
        obs("sam_gov", OBS.SOURCE_UNAVAILABLE),
    ])
    summary = assessment.coverage.summary()
    assert "applicable controls satisfied" in summary
    assert "sources agree" not in summary.lower()
    assert assessment.coverage.applicable == \
        assessment.coverage.satisfied + assessment.coverage.requires_analyst + \
        assessment.coverage.not_found + assessment.coverage.source_unavailable


def test_source_agreement_survives_inside_a_control():
    """Agreement is still useful — as supporting evidence, in its place."""
    spec = next(c for c in controls_for(EntityClass.PROVIDER)
                if c.control is Control.ENTITY_IDENTITY)
    result = assess_control(spec, [obs("rce", OBS.MATCH_OBSERVED),
                                   obs("nppes", OBS.NO_MATCH_OBSERVED)])
    assert result.agreement == (1, 2)
    assert result.to_dict()["agreement"] == {"agreeing": 1,
                                             "applicable_observations": 2}


def test_entity_verification_and_compliance_are_separate_vocabularies():
    """"Identity not automatically verified" must never read as "non-compliant"."""
    assert set(EntityVerification) != set(ContractualCompliance)
    assessment = preliminary_assessment("9.99.999.SEP", rec("Provider Practice"),
                                        [obs("rce", OBS.MATCH_OBSERVED),
                                         obs("nppes", OBS.SOURCE_UNAVAILABLE),
                                         obs("leie", OBS.SOURCE_UNAVAILABLE)])
    assert assessment.entity_verification is not \
        EntityVerification.VERIFIED
    assert assessment.contractual_compliance is \
        ContractualCompliance.UNABLE_TO_DETERMINE


# ═══ provenance ═════════════════════════════════════════════════════════════

def test_every_observation_carries_reproducible_provenance():
    observation = obs("leie", OBS.NO_MATCH_OBSERVED,
                      matched_name=None, match_method="bulk_screening",
                      dataset_version="LEIE-2026-08",
                      retrieved_at="2026-08-31T12:00:00Z",
                      query_attributes={"name": "SYNTHETIC ORG"},
                      evidence_hash="a" * 64)
    payload = observation.to_dict()
    for key in ("source", "observation_state", "match_method",
                "dataset_version", "retrieved_at", "query_attributes",
                "evidence_hash"):
        assert key in payload

    assessment = preliminary_assessment("9.99.999.PV", rec("Provider Practice"),
                                        [observation,
                                         obs("rce", OBS.MATCH_OBSERVED)])
    rendered = assessment.to_dict()
    assert rendered["methodology_version"]
    assert rendered["participation_anchor"] == "RCE_DELIVERED_POPULATION"
    assert any(c["observations"] for c in rendered["controls"])


def test_no_sensitive_identifier_is_carried_into_the_assessment():
    """EIN/TIN must not travel into reports or logs unnecessarily."""
    assessment = preliminary_assessment(
        "9.99.999.T", rec("Provider Practice", ein="12-3456789"),
        [obs("rce", OBS.MATCH_OBSERVED)])
    blob = repr(assessment.to_dict()).lower()
    for forbidden in ("12-3456789", '"ein"', "'ein'", "tin", "ssn"):
        assert forbidden not in blob, f"the assessment carries {forbidden}"


# ═══ the matrix is configuration, and internally consistent ═════════════════

def test_the_applicability_matrix_covers_every_entity_class():
    for entity_class in EntityClass:
        specs = controls_for(entity_class)
        assert specs, f"{entity_class} has no controls at all"
        controls = [s.control for s in specs]
        assert len(controls) == len(set(controls)), \
            f"{entity_class} lists a control twice"
        for spec in specs:
            assert spec.contract_task, f"{spec.control} names no contract task"
            assert spec.rationale, f"{spec.control} has no rationale"
            if spec.requirement is Requirement.NOT_APPLICABLE:
                assert spec.sources == (), \
                    f"{spec.control} is NOT_APPLICABLE yet names sources"
            else:
                assert spec.sources, \
                    f"{spec.control} is {spec.requirement} with no source"


def test_identity_and_relationship_are_required_of_every_entity():
    for entity_class in EntityClass:
        controls = {s.control: s for s in controls_for(entity_class)}
        assert controls[Control.ENTITY_IDENTITY].requirement is \
            Requirement.REQUIRED
        assert controls[Control.TEFCA_RELATIONSHIP].requirement is \
            Requirement.REQUIRED


def test_no_entity_class_requires_sam_gov():
    """SAM.gov is never determinative of TEFCA participation."""
    for entity_class in EntityClass:
        for spec in controls_for(entity_class):
            if "sam_gov" in spec.sources:
                assert spec.requirement is not Requirement.REQUIRED, entity_class


# ═══ the service adapter and the API surface ════════════════════════════════

def test_the_adapter_keeps_absent_and_unavailable_apart():
    """The distinction the whole methodology turns on, at the boundary where it
    would be easiest to lose."""
    from types import SimpleNamespace

    from app.tefca_registry.verification_coverage_service import (
        observation_from_row)

    def row(status):
        return SimpleNamespace(source="LEIE", verification_status=status,
                               lookup_identifier="9.99.999.X", detail=None,
                               data_source_label="synthetic", verified_at=None)

    assert observation_from_row(row("not_found")).state ==         OBS.NO_MATCH_OBSERVED.value
    assert observation_from_row(row("unavailable")).state ==         OBS.SOURCE_UNAVAILABLE.value
    assert observation_from_row(row("not_applicable")).state ==         OBS.LOOKUP_NOT_APPLICABLE.value

    # none of the three asserts a contradiction
    for status in ("not_found", "unavailable", "not_applicable"):
        assert observation_from_row(row(status)).contradicts is False

    # and only an explicit adverse status does
    assert observation_from_row(row("excluded")).contradicts is True


def test_an_unmapped_status_goes_to_a_human_not_to_a_match():
    from types import SimpleNamespace

    from app.tefca_registry.verification_coverage_service import (
        observation_from_row)

    row = SimpleNamespace(source="NPPES", verification_status="something_new",
                          lookup_identifier=None, detail=None,
                          data_source_label=None, verified_at=None)
    observation = observation_from_row(row)
    assert observation.state == OBS.AMBIGUOUS.value
    assert evidence_state_for(observation.state) is         EvidenceState.MANUAL_VERIFICATION_REQUIRED


def test_the_evidence_hash_is_stable_and_observation_scoped():
    from app.tefca_registry.verification_coverage_service import evidence_hash

    first = evidence_hash("leie", "not_found", "9.99.999.X", None)
    again = evidence_hash("leie", "not_found", "9.99.999.X", None)
    different = evidence_hash("leie", "excluded", "9.99.999.X", None)
    assert first == again and first != different
    assert len(first) == 64


def test_the_coverage_route_is_read_only_and_role_gated():
    import ast
    import inspect

    import app.tefca_registry.review_routes as routes

    route = next(r for r in routes.router.routes
                 if "verification-coverage" in r.path)
    floors = []
    for dependency in route.dependant.dependencies:
        for cell in getattr(dependency.call, "__closure__", None) or ():
            value = cell.cell_contents
            if isinstance(value, str):
                floors.append(value)
    assert "viewer" in floors, "the coverage route enforces no role"

    tree = ast.parse(inspect.getsource(routes.entity_verification_coverage))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                called.add(node.func.id)
    for forbidden in ("commit", "add", "flush", "delete"):
        assert forbidden not in called, (
            f"the coverage route calls {forbidden} - it is meant to read only")


# ═══ 15  manual / documentary verification, in the same audit chain ═════════

def _doc(**kw):
    base = dict(control=Control.PROVIDER_ENUMERATION,
                evidence_type="participation agreement",
                source="Synthetic Participant", received_date="2026-08-31",
                document_hash="d" * 64, analyst="analyst-a@synthetic.test",
                analyst_rationale="Agreement names the delivered organisation.",
                qa_reviewer="qa@synthetic.test", qa_disposition="APPROVE")
    base.update(kw)
    return ManualEvidence(**base)


def _needs_manual():
    return preliminary_assessment("9.99.999.MD", rec("Provider Practice"), [
        obs("rce", OBS.MATCH_OBSERVED),
        obs("nppes", OBS.MULTIPLE_MATCHES),
        obs("leie", OBS.NO_MATCH_OBSERVED),
    ])


def test_15_qa_approved_documentary_evidence_resolves_the_control():
    before = _needs_manual()
    assert control_of(before.controls, Control.PROVIDER_ENUMERATION).state is         EvidenceState.MANUAL_VERIFICATION_REQUIRED

    after = preliminary_assessment(
        "9.99.999.MD", rec("Provider Practice"),
        [obs("rce", OBS.MATCH_OBSERVED), obs("nppes", OBS.MULTIPLE_MATCHES),
         obs("leie", OBS.NO_MATCH_OBSERVED)],
        manual=[_doc()])
    control = control_of(after.controls, Control.PROVIDER_ENUMERATION)
    assert control.state is EvidenceState.VERIFIED
    assert "documentary evidence" in control.rationale.lower()
    assert control.manual_evidence and control.manual_evidence[0].qa_approved


def test_unapproved_or_self_approved_documents_resolve_nothing():
    """Documentary evidence enters the SAME maker/checker chain. An analyst
    cannot attach a document and count it themselves."""
    for doc in (_doc(qa_disposition=None, qa_reviewer=None),
                _doc(qa_disposition="RETURN"),
                _doc(qa_reviewer="analyst-a@synthetic.test")):   # self-approval
        result = preliminary_assessment(
            "9.99.999.MD", rec("Provider Practice"),
            [obs("rce", OBS.MATCH_OBSERVED), obs("nppes", OBS.MULTIPLE_MATCHES),
             obs("leie", OBS.NO_MATCH_OBSERVED)],
            manual=[doc])
        assert control_of(result.controls, Control.PROVIDER_ENUMERATION).state             is EvidenceState.MANUAL_VERIFICATION_REQUIRED, doc.qa_disposition


def test_a_document_can_never_overturn_a_conflict():
    """A document asserting the contrary of the evidence is a disagreement for
    a human to weigh, not an override."""
    result = preliminary_assessment(
        "9.99.999.MC", rec("Provider Practice"),
        [obs("rce", OBS.MATCH_OBSERVED),
         obs("nppes", OBS.MATCH_OBSERVED, contradicts=True)],
        manual=[_doc(control=Control.ENTITY_IDENTITY)])
    assert control_of(result.controls, Control.ENTITY_IDENTITY).state is         EvidenceState.CONFLICT


def test_documentary_evidence_carries_a_full_audit_record():
    payload = _doc().to_dict()
    for key in ("control", "evidence_type", "source", "received_date",
                "document_hash", "analyst", "analyst_rationale",
                "qa_reviewer", "qa_disposition", "qa_approved"):
        assert key in payload
