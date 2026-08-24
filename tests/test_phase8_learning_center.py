"""Phase 8 — Learning Center framework, TEFCA modules, methodology transparency.

The framework existed before this phase as a data structure with no API: 7
modules, a glossary and 8 help topics that nothing could reach. It also had no
search, no content classification, no programme key and no deep links, and it
was missing the one module where mislabelling has contractual consequences.

The tests here pin what was added, and — more importantly — the two properties
that make the content trustworthy: that a reader can always tell an agency
requirement from AGT's own choice, and that an open methodology decision is
never presented as decided.

DEVELOPMENT/TEST DATA. Nothing here is an ONC finding.
"""
from __future__ import annotations

import inspect

import pytest

from app.core.learning import (PROGRAMS, Classification, ContextualHelp,
                               Glossary, GlossaryTerm, LearningRegistry, Lesson,
                               Module, ProgramRegistry, Role, Statement)
from app.Tefca.learning_content import REGISTRY


# ═══ CORE framework ══════════════════════════════════════════════════════════

class TestClassificationVocabulary:
    """The single most important label in the system."""

    def test_all_five_classifications_exist(self):
        assert {c.value for c in Classification} == {
            "GOVERNMENT_REQUIREMENT", "AGT_IMPLEMENTATION",
            "AGT_RECOMMENDATION", "PROGRAM_GUIDANCE_REQUESTED",
            "SOURCE_LIMITATION"}

    def test_only_a_government_requirement_is_authoritative(self):
        assert Classification.GOVERNMENT_REQUIREMENT.is_authoritative is True
        for other in (Classification.AGT_IMPLEMENTATION,
                      Classification.AGT_RECOMMENDATION,
                      Classification.PROGRAM_GUIDANCE_REQUESTED,
                      Classification.SOURCE_LIMITATION):
            assert other.is_authoritative is False

    def test_a_government_requirement_must_cite_a_source(self):
        """A requirement nobody can trace to a document is indistinguishable
        from an assumption."""
        with pytest.raises(ValueError):
            Statement("The contractor shall do X",
                      Classification.GOVERNMENT_REQUIREMENT)
        with pytest.raises(ValueError):
            Statement("The contractor shall do X",
                      Classification.GOVERNMENT_REQUIREMENT, source="   ")

    def test_agt_statements_need_no_source(self):
        """AGT's own choices are not citations of anybody."""
        assert Statement("We queue these first",
                         Classification.AGT_IMPLEMENTATION).source is None

    def test_the_classification_survives_serialisation(self):
        d = Statement("x", Classification.AGT_RECOMMENDATION).to_dict()
        assert d["classification"] == "AGT_RECOMMENDATION"
        assert d["is_authoritative"] is False


class TestFrameworkIsReusable:
    """The architecture test: could another programme use this?"""

    @staticmethod
    def _other_program():
        """A second programme built ONLY from core imports.

        Deliberately minimal and thrown away. It exists to prove the framework
        carries no TEFCA assumption, not to be a programme.
        """
        lesson = Lesson(slug="intro", title="Intro", objective="o", body="b",
                        statements=[Statement("Agency rule",
                                              Classification.GOVERNMENT_REQUIREMENT,
                                              "Some Reg §1")])
        module = Module(slug="basics", title="Basics", audience=[Role.ANY],
                        objective="o", lessons=[lesson])
        return LearningRegistry(
            modules=[module],
            glossary=Glossary([GlossaryTerm("Widget", "A widget.")]),
            help_topics=[ContextualHelp(
                key="screen.widget", what_is_this="w", why_am_i_seeing_it="y",
                allowed_actions=["look"], prohibited_conclusions=[],
                evidence_location="somewhere", learn_more="basics/intro")],
            navigation=["Basics"], program="OTHER_PROGRAM",
            program_title="Another Programme", last_updated="2026-08-24")

    def test_a_second_program_needs_no_tefca_import(self):
        registry = self._other_program()
        assert registry.program == "OTHER_PROGRAM"
        assert len(registry.modules) == 1

    def test_it_gets_navigation_search_and_help_for_free(self):
        registry = self._other_program()
        assert registry.search("widget")
        assert registry.help_for("screen.widget") is not None
        assert registry.modules_for(Role.ANALYST)

    def test_it_gets_classification_for_free(self):
        registry = self._other_program()
        counts = registry.statements_by_classification()
        assert counts["GOVERNMENT_REQUIREMENT"] == 1

    @staticmethod
    def _executable_code(module):
        """Source with docstrings and comments removed.

        Prose is allowed to name a programme — routes.py opens by stating that
        it imports no TEFCA, which is the property under test, and a naive grep
        reads that as the violation. What must be clean is the code.
        """
        import ast

        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef))
                    and ast.get_docstring(node) is not None):
                node.body = node.body[1:]
        return ast.unparse(tree)

    @pytest.mark.parametrize("term", [
        "TEFCA", "QHIN", "NPPES", "PECOS", "LEIE", "discrepancy",
    ])
    def test_the_core_framework_names_no_program(self, term):
        """If this file learns what a QHIN is, the next programme cannot use it.

        Word-boundary matched: "RCE" is a substring of SOURCE_UNAVAILABLE, and
        a plain `in` check flags the framework's own generic vocabulary.
        """
        import re

        from app.core.learning import framework

        code = self._executable_code(framework)
        assert not re.search(rf"\b{term}\b", code, re.IGNORECASE), \
            f"core framework leaked {term!r}"

    @pytest.mark.parametrize("term", ["TEFCA", "QHIN", "NPPES", "PECOS"])
    def test_the_core_api_names_no_program(self, term):
        import re

        from app.core.learning import routes

        code = self._executable_code(routes)
        assert not re.search(rf"\b{term}\b", code, re.IGNORECASE), \
            f"core learning API leaked {term!r}"

    def test_the_core_api_imports_nothing_program_specific(self):
        """The property the docstring claims, checked against the imports."""
        import ast

        from app.core.learning import routes

        tree = ast.parse(inspect.getsource(routes))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
        assert not [m for m in imported if "Tefca" in m or "tefca" in m], imported

    def test_the_program_registry_refuses_a_collision(self):
        registry = ProgramRegistry()
        registry.register(self._other_program())
        with pytest.raises(ValueError):
            registry.register(self._other_program())

    def test_the_program_registry_refuses_an_unnamed_program(self):
        bad = LearningRegistry(modules=[], glossary=Glossary([]),
                               help_topics=[], navigation=[])
        with pytest.raises(ValueError):
            ProgramRegistry().register(bad)


class TestDeepLinksResolve:

    def test_a_broken_deep_link_is_refused_at_construction(self):
        """A link that goes nowhere is worse than no link: the reader follows
        it, hits an error, and stops trusting the help."""
        module = Module(slug="m", title="M", audience=[Role.ANY], objective="o",
                        lessons=[Lesson(slug="l", title="L", objective="o", body="b")])
        with pytest.raises(ValueError, match="unknown module"):
            LearningRegistry(
                modules=[module], glossary=Glossary([]), navigation=[],
                help_topics=[ContextualHelp(
                    key="k", what_is_this="w", why_am_i_seeing_it="y",
                    allowed_actions=[], prohibited_conclusions=[],
                    evidence_location="e", learn_more="does-not-exist")],
                program="P")

    def test_a_broken_lesson_deep_link_is_refused(self):
        module = Module(slug="m", title="M", audience=[Role.ANY], objective="o",
                        lessons=[Lesson(slug="l", title="L", objective="o", body="b")])
        with pytest.raises(ValueError, match="unknown lesson"):
            LearningRegistry(
                modules=[module], glossary=Glossary([]), navigation=[],
                help_topics=[ContextualHelp(
                    key="k", what_is_this="w", why_am_i_seeing_it="y",
                    allowed_actions=[], prohibited_conclusions=[],
                    evidence_location="e", learn_more="m/nope")],
                program="P")


class TestSearch:

    def test_an_empty_query_returns_nothing(self):
        assert REGISTRY.search("") == []
        assert REGISTRY.search("   ") == []

    def test_a_title_match_outranks_a_body_match(self):
        results = REGISTRY.search("address")
        assert results
        assert "address" in results[0]["title"].lower()

    def test_every_result_carries_a_deep_link(self):
        for hit in REGISTRY.search("evidence"):
            assert hit["deep_link"]

    def test_search_respects_the_role_filter(self):
        """Search must not surface what the sidebar hides."""
        everything = {r["deep_link"] for r in REGISTRY.search("review", limit=100)}
        as_analyst = {r["deep_link"]
                      for r in REGISTRY.search("review", role=Role.ANALYST,
                                               limit=100)}
        assert as_analyst <= everything

    def test_the_glossary_is_searchable(self):
        assert any(r["type"] == "glossary" for r in REGISTRY.search("QHIN", limit=100)) \
            or any(r["type"] == "glossary" for r in REGISTRY.search("evidence", limit=100))


# ═══ TEFCA content ═══════════════════════════════════════════════════════════

class TestTefcaProgramIsRegistered:

    def test_it_is_registered_under_a_real_key(self):
        assert PROGRAMS.get("TEFCA_ARC") is REGISTRY

    def test_it_carries_a_last_updated_date(self):
        assert REGISTRY.last_updated

    def test_lookup_is_case_insensitive(self):
        assert PROGRAMS.get("tefca_arc") is REGISTRY


class TestRequiredModules:
    """B3's seven, all present."""

    REQUIRED = {
        "tefca-arc-overview": "programme overview",
        "evidence-and-sources": "source evidence",
        "automated-observations": "evidence model",
        "analyst-review": "analyst workflow",
        "qa-review": "QA workflow",
        "discrepancies-and-methodology": "discrepancies / methodology",
        "reports": "reporting",
    }

    @pytest.mark.parametrize("slug", sorted(REQUIRED))
    def test_module_exists(self, slug):
        assert REGISTRY.module(slug) is not None, f"missing: {self.REQUIRED[slug]}"

    def test_the_methodology_module_was_the_one_missing(self):
        """Added in Phase 8. It is the module where mislabelling has
        contractual consequences."""
        module = REGISTRY.module("discrepancies-and-methodology")
        assert module is not None
        assert len(module.lessons) >= 2
        assert len(module.checks) >= 3

    def test_analyst_and_qa_modules_are_role_scoped(self):
        assert Role.ANY not in REGISTRY.module("qa-review").audience
        assert Role.ANALYST not in REGISTRY.module("qa-review").audience


class TestGovernmentVersusAgt:
    """The distinction that must never blur."""

    def test_content_carries_classified_statements(self):
        counts = REGISTRY.statements_by_classification()
        assert counts["GOVERNMENT_REQUIREMENT"] >= 1
        assert counts["AGT_IMPLEMENTATION"] >= 1
        assert counts["AGT_RECOMMENDATION"] >= 1
        assert counts["PROGRAM_GUIDANCE_REQUESTED"] >= 1
        assert counts["SOURCE_LIMITATION"] >= 1

    def test_every_government_statement_cites_a_source(self):
        for statement in REGISTRY.statements():
            if statement.classification.is_authoritative:
                assert statement.source, statement.text

    def test_b1_b4_is_never_claimed_as_a_federal_taxonomy(self):
        """The prohibition is stated, not merely implied."""
        claims = " ".join(p.claim + " " + p.why_prohibited
                          for p in REGISTRY.all_prohibited()).lower()
        assert "b1-b4" in claims
        assert "shorthand" in claims or "internal" in claims

    def test_category_labels_come_from_the_report_constants(self):
        """A lesson that spells a contractual term differently from the report
        is worse than no lesson."""
        from app.reports.data.sow_report_data import GOVERNMENT_CATEGORY_LABELS
        from app.Tefca.learning_methodology import category_guidance

        for entry in category_guidance():
            assert entry["government_label"] == \
                GOVERNMENT_CATEGORY_LABELS[entry["category"]]

    def test_the_four_categories_are_in_the_solicitations_order(self):
        from app.Tefca.learning_methodology import category_guidance

        assert [e["number"] for e in category_guidance()] == [1, 2, 3, 4]


class TestD1ToD9StaysUnresolved:

    def test_none_is_presented_as_decided(self):
        """Inventing a COR response would be fabricating a Government decision."""
        from app.Tefca.learning_methodology import DECIDED, decision_status

        for entry in decision_status()["decisions"]:
            assert entry["status"] != DECIDED, entry["id"]

    def test_every_decision_states_what_it_affects(self):
        from app.Tefca.learning_methodology import decision_status

        for entry in decision_status()["decisions"]:
            assert entry["consequence"]
            assert entry["affects"]

    def test_all_nine_plus_the_address_decision_are_present(self):
        from app.Tefca.learning_methodology import decision_status

        ids = {e["id"] for e in decision_status()["decisions"]}
        assert {f"D{n}" for n in range(1, 10)} <= ids
        assert "D4_ADDRESS_MATERIALITY" in ids

    def test_the_status_view_exposes_no_engineering_internals(self):
        """A programme manager should see what is undecided, not the schema."""
        import json

        from app.Tefca.learning_methodology import decision_status

        payload = json.dumps(decision_status()).lower()
        for leak in ("alembic", "sqlalchemy", "tefca_dimension_evidence",
                     "review_records", "migration", "__tablename__",
                     "psycopg", "select "):
            assert leak not in payload, f"engineering internal leaked: {leak}"


class TestSourceLimitationsAreNeverCollapsed:
    """B6: three collapses that must be impossible."""

    def _prohibited_text(self):
        return " ".join(p.claim + " " + p.why_prohibited
                        for p in REGISTRY.all_prohibited()).lower()

    def test_source_unavailable_is_not_no_issue(self):
        text = self._prohibited_text()
        assert "unavailable" in text or "not reached" in text

    def test_pending_is_not_a_pass_or_a_fail(self):
        text = self._prohibited_text()
        assert "pending means" in text

    def test_the_distinct_states_all_have_guidance(self):
        keys = set(REGISTRY.help_keys())
        assert "evidence.source_unavailable" in keys
        assert "methodology.pending" in keys
        assert "source.limitation" in keys
        assert "evidence.address_conflict" in keys


class TestContextualHelp:

    def test_every_topic_deep_links_somewhere(self):
        for key in REGISTRY.help_keys():
            assert REGISTRY.help_for(key).learn_more, f"{key} has no deep link"

    def test_every_deep_link_resolves_to_a_real_module(self):
        for key in REGISTRY.help_keys():
            topic = REGISTRY.help_for(key)
            assert REGISTRY.module(topic.module_slug) is not None, key

    def test_a_lesson_deep_link_resolves_to_a_real_lesson(self):
        for key in REGISTRY.help_keys():
            topic = REGISTRY.help_for(key)
            if topic.lesson_slug:
                module = REGISTRY.module(topic.module_slug)
                assert topic.lesson_slug in {l.slug for l in module.lessons}, key

    def test_every_topic_names_a_prohibited_conclusion(self):
        """The question most easily left out, and the one preventing the most
        damage."""
        for key in REGISTRY.help_keys():
            assert REGISTRY.help_for(key).prohibited_conclusions, key


# ═══ API surface ═════════════════════════════════════════════════════════════

class TestLearningApi:

    @staticmethod
    def _paths():
        from app.main import app
        return app.openapi()["paths"]

    @pytest.mark.parametrize("path", [
        "/api/learning/programs",
        "/api/learning/{program}",
        "/api/learning/{program}/navigation",
        "/api/learning/{program}/search",
        "/api/learning/{program}/modules/{slug}",
        "/api/learning/{program}/modules/{slug}/{lesson_slug}",
        "/api/learning/{program}/help/{key}",
        "/api/learning/{program}/glossary",
        "/api/learning/{program}/prohibited",
        "/api/tefca/methodology/status",
        "/api/tefca/methodology/categories",
        "/api/tefca/methodology/categories/{category}",
    ])
    def test_endpoint_is_registered(self, path):
        assert path in self._paths()

    def test_every_learning_endpoint_requires_authentication(self):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app, raise_server_exceptions=False)
        for path in ("/api/learning/programs",
                     "/api/learning/TEFCA_ARC",
                     "/api/learning/TEFCA_ARC/navigation",
                     "/api/learning/TEFCA_ARC/search?q=address",
                     "/api/learning/TEFCA_ARC/glossary",
                     "/api/tefca/methodology/status",
                     "/api/tefca/methodology/categories"):
            response = client.get(path)
            assert response.status_code in (401, 403), \
                f"{path} answered {response.status_code} unauthenticated"


# ═══ No unsupported Government-policy wording ════════════════════════════════

import os  # noqa: E402

FRONTEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "frontend", "src", "app", "tefca-arc")


def _frontend_code(relative: str) -> str:
    """Frontend source with comment lines stripped.

    The files document what they were corrected FROM, and a naive grep reads
    that explanation as the violation it describes.
    """
    path = os.path.normpath(os.path.join(FRONTEND, relative))
    if not os.path.exists(path):
        pytest.skip(f"frontend source not present: {relative}")
    with open(path, encoding="utf-8") as fh:
        return "\n".join(line for line in fh
                         if not line.lstrip().startswith(("//", "*", "/*")))


class TestNoUnsupportedPolicyWording:
    """Operator-facing content may not invent a Government requirement.

    Phase 8 found three in the static help page: fixed per-category review
    deadlines with no contractual basis, B1-B4 presented as the classification
    without the Government/AGT distinction, and a sample described as already
    drawn.
    """

    def test_no_invented_per_category_deadlines(self):
        """The contract sets the priority deadline per request (¶146) and no
        standing per-category turnaround at all."""
        code = _frontend_code("help/page.js")
        for invented in ("B2 = 30 days", "B3 = 21 days", "B4 = 10 days"):
            assert invented not in code, f"invented deadline still shown: {invented}"

    def test_the_sample_is_not_described_as_drawn(self):
        code = _frontend_code("help/page.js")
        assert "fixed, auditable seed" not in code
        assert "awaiting COR confirmation" in code

    def test_b1_b4_is_qualified_wherever_it_appears(self):
        code = _frontend_code("help/page.js")
        if "B1-B4" in code or "(B1)" in code:
            assert "shorthand" in code, (
                "B1-B4 appears without being identified as AGT shorthand")

    def test_the_government_categories_use_contract_wording(self):
        from app.reports.data.sow_report_data import GOVERNMENT_CATEGORY_LABELS

        code = _frontend_code("help/page.js").lower()
        for label in GOVERNMENT_CATEGORY_LABELS.values():
            assert label.lower() in code, f"missing contract wording: {label}"

    def test_the_help_page_points_at_the_authoritative_guidance(self):
        code = _frontend_code("help/page.js")
        assert "/api/learning/TEFCA_ARC" in code

    def test_backend_guidance_states_no_fixed_sla(self):
        """The same rule, on the side that generates the reports."""
        from app.Tefca.learning_methodology import decision_status

        import json
        payload = json.dumps(decision_status())
        for invented in ("30 days", "21 days", "10 days"):
            assert invented not in payload


class TestContextualHelpComponent:

    def test_the_component_exists(self):
        code = _frontend_code("components/LearningHelp.js")
        assert "LearningHelp" in code

    def test_it_fetches_rather_than_hard_coding_guidance(self):
        """Hard-coded copy drifts; the API derives its vocabulary from the live
        enums and fails its own build when it stops matching."""
        code = _frontend_code("components/LearningHelp.js")
        assert "/api/learning/" in code
        assert "tefcaFetch" in code

    def test_it_renders_the_classification(self):
        code = _frontend_code("components/LearningHelp.js")
        for classification in ("GOVERNMENT_REQUIREMENT", "AGT_IMPLEMENTATION",
                               "AGT_RECOMMENDATION",
                               "PROGRAM_GUIDANCE_REQUESTED",
                               "SOURCE_LIMITATION"):
            assert classification in code

    def test_it_shows_prohibited_conclusions(self):
        code = _frontend_code("components/LearningHelp.js")
        assert "prohibited_conclusions" in code
        assert "NOT conclude" in code

    def test_it_is_accessible(self):
        """Semantic headings, an announced region, and labelled controls."""
        code = _frontend_code("components/LearningHelp.js")
        assert 'role="region"' in code
        assert "aria-label" in code
        assert "aria-expanded" in code
        assert "aria-controls" in code
        assert "<h3" in code and "<h4" in code
        # Decorative icons must not be announced.
        assert 'aria-hidden="true"' in code

    def test_meaning_is_not_carried_by_colour_alone(self):
        """Every classification tag renders its words, not just a colour."""
        code = _frontend_code("components/LearningHelp.js")
        assert "CLASSIFICATION_LABEL" in code
        assert "Government requirement" in code

    def test_a_failed_load_says_so(self):
        """An empty panel reads as 'there is nothing to know here'."""
        code = _frontend_code("components/LearningHelp.js")
        assert "could not be loaded" in code

    def test_it_is_wired_into_a_real_screen(self):
        code = _frontend_code("reports/page.js")
        assert "LearningHelp" in code
        assert "report.release_status" in code
