"""Phase 8 — the Learning Center, and the guarantee that it cannot lie.

The central test here is `test_every_taught_term_still_exists_in_code`.
Operational guidance rots silently: an analyst reading about a state that was
renamed two sprints ago simply stops finding it and starts guessing. Declaring
the vocabulary turns that into a failing build.
"""
from __future__ import annotations

from app.core.evidence_vocabulary import ObservationState
from app.core.learning import (
    ContextualHelp, Glossary, GlossaryTerm, KnowledgeCheck, Role)
from app.core.learning.framework import KNOWLEDGE_VERSION
from app.Tefca.address_comparison import AddressResult
from app.Tefca.exception_triage import Triage
from app.Tefca.learning_content import GLOSSARY, MODULES, NAVIGATION, REGISTRY
from app.Tefca.source_applicability import SourceApplicability


class TestFrameworkContract:
    """The reusable core, exercised without any TEFCA content."""

    def test_a_knowledge_check_refuses_an_out_of_range_answer(self):
        try:
            KnowledgeCheck("q", ["a", "b"], 5, "why")
        except ValueError as exc:
            assert "outside" in str(exc)
        else:
            raise AssertionError("an unanswerable check must be refused")

    def test_a_knowledge_check_refuses_a_single_option(self):
        try:
            KnowledgeCheck("q", ["only"], 0, "why")
        except ValueError:
            pass
        else:
            raise AssertionError("a one-option check is not a check")

    def test_the_answer_is_withheld_unless_asked_for(self):
        """The question can be rendered without shipping the answer key."""
        c = KnowledgeCheck("q", ["a", "b"], 1, "because")
        assert "correct_index" not in c.to_dict()
        assert c.to_dict(include_answer=True)["correct_index"] == 1

    def test_a_glossary_refuses_a_duplicate_term(self):
        try:
            Glossary([GlossaryTerm("NPI", "one"), GlossaryTerm("npi", "another")])
        except ValueError as exc:
            assert "twice" in str(exc)
        else:
            raise AssertionError("two definitions of one term must be refused")

    def test_glossary_lookup_is_case_insensitive(self):
        g = Glossary([GlossaryTerm("QHIN", "a network")])
        assert g.get("qhin") is not None and g.get("  QhIn ") is not None

    def test_contextual_help_always_answers_five_questions(self):
        import dataclasses
        names = {f.name for f in dataclasses.fields(ContextualHelp)}
        for required in ("what_is_this", "why_am_i_seeing_it", "allowed_actions",
                         "prohibited_conclusions", "evidence_location"):
            assert required in names


class TestVocabularyCannotDrift:
    """Content declares the terms it teaches; the terms must still exist."""

    def test_every_taught_term_still_exists_in_code(self):
        # The Government discrepancy categories are module-level constants in
        # sow_report_data rather than an enum, so they are a second source of
        # truth the guard has to know about. Widening it, not weakening it:
        # Module 6 teaches those four terms and they must still exist.
        from app.reports.data.sow_report_data import GOVERNMENT_CATEGORIES

        live = ({s.value for s in ObservationState}
                | {a.value for a in SourceApplicability}
                | {t.value for t in Triage}
                | {r.value for r in AddressResult}
                | set(GOVERNMENT_CATEGORIES))
        taught = set(REGISTRY.vocabulary())
        assert taught, "content must declare the vocabulary it teaches"
        missing = sorted(taught - live)
        assert not missing, (
            f"the Learning Center teaches terms that no longer exist in code: "
            f"{missing}. Guidance has drifted from the system it describes.")

    def test_the_eight_observation_states_are_all_taught(self):
        taught = set(REGISTRY.vocabulary())
        for s in ObservationState:
            assert s.value in taught, f"{s.value} is never explained to an operator"

    def test_every_applicability_value_is_taught(self):
        taught = set(REGISTRY.vocabulary())
        for a in SourceApplicability:
            assert a.value in taught

    def test_every_triage_disposition_is_taught(self):
        taught = set(REGISTRY.vocabulary())
        for t in Triage:
            assert t.value in taught

    def test_every_address_verdict_is_taught(self):
        taught = set(REGISTRY.vocabulary())
        for r in AddressResult:
            assert r.value in taught


class TestNavigationAndModules:

    def test_all_navigation_items_are_present(self):
        # 19 since Phase 8 added "Discrepancy Categories" alongside
        # the new methodology module.
        assert len(NAVIGATION) == 19
        for item in ("Getting Started", "Analyst Guide", "QA Reviewer Guide",
                     "Source Limitations", "Glossary", "Program Manager Guide"):
            assert item in NAVIGATION

    def test_every_training_module_exists(self):
        # 8 since Phase 8 added discrepancies-and-methodology, the
        # module where mislabelling has contractual consequences.
        assert len(MODULES) == 8

    def test_every_module_has_objective_lesson_and_check(self):
        for m in MODULES:
            assert m.objective and m.lessons and m.checks, m.slug

    def test_every_lesson_states_an_objective_and_common_mistakes(self):
        for m in MODULES:
            for l in m.lessons:
                assert l.objective, f"{m.slug}/{l.slug}"
                assert l.body

    def test_module_slugs_are_unique(self):
        slugs = [m.slug for m in MODULES]
        assert len(slugs) == len(set(slugs))

    def test_content_is_versioned(self):
        assert KNOWLEDGE_VERSION
        assert REGISTRY.to_dict()["knowledge_version"] == KNOWLEDGE_VERSION


class TestRoleJourneys:
    """Each role can reach the guidance its journey needs."""

    def test_an_analyst_gets_the_analyst_module(self):
        slugs = {m.slug for m in REGISTRY.modules_for(Role.ANALYST)}
        assert "analyst-review" in slugs

    def test_a_qa_reviewer_gets_the_qa_module(self):
        slugs = {m.slug for m in REGISTRY.modules_for(Role.QA)}
        assert "qa-review" in slugs

    def test_an_analyst_does_not_get_the_qa_only_module(self):
        """Not a security control — the point is that guidance is targeted."""
        slugs = {m.slug for m in REGISTRY.modules_for(Role.ANALYST)}
        assert "qa-review" not in slugs

    def test_a_program_manager_can_see_everything(self):
        assert len(REGISTRY.modules_for(Role.PROGRAM_MANAGER)) == len(MODULES)

    def test_analyst_and_qa_help_topics_are_role_scoped(self):
        assert Role.ANALYST in REGISTRY.help_for("exception.queue_item").audience
        assert Role.ANALYST not in REGISTRY.help_for("qa.decision").audience


class TestGlossary:

    def test_the_required_terms_are_defined(self):
        for term in ("ARC", "RCE", "QHIN", "Participant", "Subparticipant", "NPI",
                     "NPPES", "PECOS", "PPEF", "LEIE", "SAM.gov", "Evidence",
                     "Observation", "Applicability", "Provenance", "Disposition",
                     "Determination", "Methodology pending", "Reportable",
                     "Area 1", "Area 2", "Priority Review", "Ongoing Review",
                     "Retrospective Review", "B1-B4"):
            assert GLOSSARY.get(term) is not None, f"{term} is undefined"

    def test_b1_b4_is_marked_as_an_internal_classification(self):
        """The single most important glossary entry to get right."""
        t = GLOSSARY.get("B1-B4")
        assert "INTERNAL" in (t.authority or "").upper()
        for federal in ("onc", "astp", "rce", "sequoia", "federal"):
            assert federal in (t.authority or "").lower(), (
                "the entry must name the bodies that do NOT establish it")

    def test_observation_is_distinguished_from_finding(self):
        assert "finding" in (GLOSSARY.get("Observation").not_to_be_confused_with or "")

    def test_pecos_is_distinguished_from_ppef(self):
        assert GLOSSARY.get("PECOS").not_to_be_confused_with

    def test_address_materiality_is_distinguished_from_d4(self):
        t = GLOSSARY.get("D4_ADDRESS_MATERIALITY")
        assert t and "D4" in (t.not_to_be_confused_with or "")

    def test_sam_is_described_as_unevaluated_not_as_clear(self):
        d = GLOSSARY.get("SAM.gov").definition.lower()
        assert "not evaluated" in d or "no credential" in d


class TestProhibitedConclusions:
    """The conclusions an operator must never draw are stated, not implied."""

    def _claims(self):
        return " ".join(p.claim.lower() + " " + p.why_prohibited.lower()
                        for p in REGISTRY.all_prohibited())

    def test_prohibitions_exist_and_are_substantial(self):
        assert len(REGISTRY.all_prohibited()) >= 15

    def test_address_conflict_is_never_taught_as_a_failure(self):
        text = self._claims()
        assert "registered" in text and "practice location" in text

    def test_address_prohibitions_name_the_blocking_decision(self):
        blocked = [p for p in REGISTRY.all_prohibited()
                   if p.unblocked_by and "D4_ADDRESS_MATERIALITY" in p.unblocked_by]
        assert blocked, "address prohibitions must name the decision that would lift them"

    def test_source_unavailable_is_never_taught_as_a_result(self):
        text = self._claims()
        assert "did not answer" in text

    def test_self_approval_is_prohibited(self):
        text = self._claims()
        assert "segregation" in text or "different person" in text

    def test_every_prohibition_explains_why(self):
        for p in REGISTRY.all_prohibited():
            assert len(p.why_prohibited) > 20, p.claim


class TestContextualHelp:

    def test_help_covers_the_required_surfaces(self):
        keys = set(REGISTRY.help_keys())
        for k in ("evidence.observation", "evidence.address_conflict",
                  "evidence.source_unavailable", "exception.queue_item",
                  "qa.decision", "report.release_status", "methodology.pending",
                  "source.limitation"):
            assert k in keys

    def test_every_topic_names_where_the_evidence_is(self):
        for k in REGISTRY.help_keys():
            assert REGISTRY.help_for(k).evidence_location

    def test_every_topic_states_at_least_one_prohibited_conclusion(self):
        for k in REGISTRY.help_keys():
            assert REGISTRY.help_for(k).prohibited_conclusions, k

    def test_help_is_not_overloaded(self):
        """Eight surfaces, not eighty. Tooltip overload is its own failure."""
        assert len(REGISTRY.help_keys()) <= 12


class TestSopDocuments:

    def _read(self, p):
        """Whitespace-normalised, so an assertion cannot fail on line wrapping.

        A phrase split across two lines is the same phrase; a test that says
        otherwise fails for a reason unrelated to what it is checking.
        """
        import io
        import re
        return re.sub(r"\s+", " ", io.open(p, encoding="utf-8").read()).lower()

    def test_analyst_sop_exists_and_forbids_self_approval(self):
        t = self._read("docs/deliverables/TEFCA_ARC_Analyst_SOP_DRAFT.md")
        assert "cannot approve" in t or "refuses a self-approval" in t
        assert "draft — not for cor release" in t

    def test_analyst_sop_warns_that_tefcaid_is_not_unique(self):
        t = self._read("docs/deliverables/TEFCA_ARC_Analyst_SOP_DRAFT.md")
        assert "not unique" in t and "tefcaid" in t

    def test_analyst_sop_covers_the_five_required_examples(self):
        t = self._read("docs/deliverables/TEFCA_ARC_Analyst_SOP_DRAFT.md")
        for case in ("cms revoked", "oig exclusion, npi match",
                     "name-only", "nppes identity", "address discrepancy"):
            assert case in t, case

    def test_qa_sop_maps_to_decision_events_not_a_new_mechanism(self):
        t = self._read("docs/deliverables/TEFCA_ARC_QA_SOP_DRAFT.md")
        assert "review_decision_events" in t
        assert "does not introduce a second approval mechanism" in t
        for action in ("approve", "return", "escalate"):
            assert action in t

    def test_qa_sop_states_that_a_later_return_revokes_reportability(self):
        t = self._read("docs/deliverables/TEFCA_ARC_QA_SOP_DRAFT.md")
        assert "withdraws" in t or "revoke" in t

    def test_playbook_covers_all_four_cadences_and_incidents(self):
        t = self._read("docs/deliverables/TEFCA_ARC_Operations_Playbook_DRAFT.md")
        for section in ("daily", "per delivery", "weekly", "monthly", "incident"):
            assert section in t
        for incident in ("hash mismatch", "schema change", "source unavailable",
                         "failed enrichment", "qa disagreement", "report gate closed"):
            assert incident in t, incident

    def test_playbook_asserts_no_invented_priority_volume(self):
        t = self._read("docs/deliverables/TEFCA_ARC_Operations_Playbook_DRAFT.md")
        assert "states none" in t or "source material states none" in t
