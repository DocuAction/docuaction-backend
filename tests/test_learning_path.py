"""The Learning Center as a product requirement: the 16-step path, the
seven-part content standard, role paths, feature-to-training traceability,
versioning, and the reference library's authority classification.

What is pinned:

  * the programme path has exactly the 16 modules in the required order and
    every one carries a guide (WHAT / WHY / AUTOMATION / HUMAN / STEPS /
    DON'T / NEXT) and a version with an effective date;
  * every role has a path and every path names only real modules;
  * every feature row maps to a real module, is owned, dated, and verified at
    the current knowledge version — a stale row is served, not hidden;
  * the library classifies authority and ONLY Contract/SOW items are
    contractual; research and industry references never are;
  * the API serves paths, traceability and library, and contextual help
    carries an in-app Learning Center path (not just an API URL).
"""
from __future__ import annotations

import pytest

from app.core.learning.framework import (
    KNOWLEDGE_VERSION, Authority, Classification, FeatureLink, LearningPath,
    LearningRegistry, Glossary, Module, Lesson, KnowledgeCheck, Role)
from app.Tefca.learning_content import MODULES, REGISTRY
from app.Tefca.learning_path_content import (
    CONTENT_VERSION, FEATURES, LIBRARY, PATH_ORDER, PATHS)


REQUIRED_ORDER = [
    "tefca-arc-overview", "delivery-and-ingestion", "automated-observations",
    "qhin-relationships", "stratification-and-sampling",
    "work-creation-and-assignment", "analyst-review", "evidence-and-sources",
    "determination-and-rationale", "maker-checker", "qa-review",
    "discrepancies-and-methodology", "auditability", "reports",
    "pm-review-and-delivery", "security-and-data-handling",
]


class TestSixteenModulePath:

    def test_the_path_is_the_sixteen_required_modules_in_order(self):
        assert [m.slug for m in MODULES] == REQUIRED_ORDER
        assert PATH_ORDER == REQUIRED_ORDER

    def test_every_module_meets_the_content_standard(self):
        for m in MODULES:
            g = m.guide
            assert g is not None, f"{m.slug} has no guide"
            for part in ("what_is_this", "why_it_matters", "what_automation_does",
                         "what_human_does", "what_happens_next"):
                assert getattr(g, part).strip(), f"{m.slug}.{part}"
            assert g.steps and g.what_not_to_do, m.slug

    def test_every_module_is_versioned_with_an_effective_date(self):
        for m in MODULES:
            assert m.version and m.effective_date and m.status == "current", m.slug
            payload = m.to_dict()
            assert payload["version"] == m.version
            assert payload["effective_date"] == m.effective_date
            assert "history" in payload and "guide" in payload

    def test_history_is_preserved_not_overwritten(self):
        pm = next(m for m in MODULES if m.slug == "pm-review-and-delivery")
        assert pm.history and pm.history[0].status == "superseded"
        assert pm.history[0].version != pm.version

    def test_titles_are_not_numbered_in_content(self):
        """The path numbers modules; a hard-coded '6.' in a title that sits at
        step 14 would be wrong on every screen."""
        for m in MODULES:
            assert not m.title[:3].strip().rstrip(".").isdigit(), m.title

    def test_government_statements_cite_the_contract(self):
        for st in REGISTRY.statements():
            if st.classification is Classification.GOVERNMENT_REQUIREMENT:
                source = st.source or ""
                assert "7571MN26F80064" in source or "7571MN26Q00038" in source, st.text

    def test_internal_shorthand_is_never_a_category_in_new_modules(self):
        for slug in ("determination-and-rationale", "maker-checker", "pm-review-and-delivery"):
            m = REGISTRY.module(slug)
            for lesson in m.lessons:
                assert "B1-B4" not in lesson.title and "B2 =" not in lesson.body


class TestRolePaths:

    def test_every_operating_role_has_a_path(self):
        roles = {p.role for p in PATHS}
        assert {Role.PROGRAM_MANAGER, Role.ANALYST, Role.QA, Role.ADMIN} <= roles

    def test_a_read_only_path_exists_for_viewers_and_the_cor(self):
        ro = [p for p in PATHS if p.read_only]
        assert ro and ro[0].role is Role.ANY
        assert "reports" in ro[0].module_slugs

    def test_paths_name_only_real_modules(self):
        known = {m.slug for m in MODULES}
        for p in PATHS:
            assert set(p.module_slugs) <= known, p.slug
            assert len(p.module_slugs) == len(set(p.module_slugs)), p.slug

    def test_the_pm_path_is_the_full_programme(self):
        pm = REGISTRY.path("program-manager")
        assert pm.module_slugs == REQUIRED_ORDER

    def test_the_analyst_path_does_not_include_the_qa_only_module(self):
        analyst = REGISTRY.path("analyst")
        assert "qa-review" not in analyst.module_slugs
        assert "determination-and-rationale" in analyst.module_slugs

    def test_the_callers_own_path_comes_first(self):
        assert REGISTRY.paths_for(Role.QA)[0].role is Role.QA
        assert REGISTRY.paths_for(Role.ANALYST)[0].role is Role.ANALYST

    def test_a_path_to_an_unknown_module_is_refused(self):
        module = Module(slug="only", title="Only", audience=[Role.ANY], objective="x",
                        lessons=[Lesson("l", "L", "o", "b")],
                        checks=[KnowledgeCheck("q", ["a", "b"], 0, "e")])
        with pytest.raises(ValueError):
            LearningRegistry(modules=[module], glossary=Glossary([]), help_topics=[],
                             navigation=[], program="X",
                             paths=[LearningPath("p", "P", Role.ANY, "d", ["missing"])])


class TestFeatureTraceability:

    def test_every_row_is_complete(self):
        for f in FEATURES:
            assert f.feature and f.screen and f.route.startswith("/") and f.roles
            assert f.module_slug and f.owner and f.last_updated
            assert f.last_verified_version == CONTENT_VERSION == KNOWLEDGE_VERSION

    def test_no_row_is_stale_at_this_version(self):
        assert REGISTRY.stale_features() == []

    def test_the_screens_that_gained_learn_more_links_are_traced(self):
        routes = {f.route for f in FEATURES}
        for route in ("/tefca-arc/deliveries", "/tefca-arc/workspace", "/tefca-arc/qa",
                      "/tefca-arc/reports", "/tefca-arc/operations", "/tefca-arc/help"):
            assert route in routes, route

    def test_a_row_to_an_unknown_module_is_refused(self):
        module = Module(slug="only", title="Only", audience=[Role.ANY], objective="x",
                        lessons=[Lesson("l", "L", "o", "b")],
                        checks=[KnowledgeCheck("q", ["a", "b"], 0, "e")])
        with pytest.raises(ValueError):
            LearningRegistry(modules=[module], glossary=Glossary([]), help_topics=[],
                             navigation=[], program="X",
                             features=[FeatureLink("f", "s", "/r", [Role.ANY], "missing",
                                                   "1.0.0", "2026-09-11", "o")])


class TestReferenceLibrary:

    def test_only_contract_items_are_contractual(self):
        for item in LIBRARY:
            payload = item.to_dict()
            assert payload["contractual"] is (item.authority is Authority.CONTRACT_SOW)

    def test_research_is_never_presented_as_a_requirement(self):
        research = [i for i in LIBRARY if i.authority is Authority.RESEARCH_INDUSTRY]
        assert research
        for item in research:
            assert item.to_dict()["contractual"] is False
            assert item.note and "not an ONC requirement" in item.note

    def test_every_authority_class_is_labelled(self):
        for a in Authority:
            assert a.label

    def test_the_hhs_logo_policy_is_shelved_as_a_reporting_guide(self):
        item = next(i for i in LIBRARY if "HHS logo policy" in i.title)
        assert item.authority is Authority.FEDERAL_GUIDANCE
        assert "may not use the HHS logo" in item.summary

    def test_the_library_covers_the_required_classes(self):
        """Every class except ACCEPTED_METHODOLOGY, which stays empty until a
        written acceptance exists — labelling D2 accepted would be a fabricated
        Government decision."""
        present = {i.authority for i in LIBRARY}
        assert present == set(Authority) - {Authority.ACCEPTED_METHODOLOGY}
        assert Authority.ACCEPTED_METHODOLOGY not in present


class TestApi:

    def _schema(self):
        from app.main import app
        return app.openapi()["paths"]

    def test_routes_are_registered(self):
        paths = self._schema()
        for p in ("/api/learning/{program}/paths", "/api/learning/{program}/paths/{slug}",
                  "/api/learning/{program}/traceability", "/api/learning/{program}/library"):
            assert p in paths, p

    def test_registry_payload_carries_paths_features_and_library(self):
        payload = REGISTRY.to_dict(role=Role.PROGRAM_MANAGER)
        assert payload["knowledge_version"] == "1.2.0"
        assert len(payload["paths"]) == len(PATHS)
        assert len(payload["features"]) == len(FEATURES)
        assert len(payload["library"]) == len(LIBRARY)
        assert payload["last_updated"] == "2026-09-11"

    def test_help_payload_has_an_in_app_learning_center_path(self):
        assert REGISTRY.learning_center_path("pm-review-and-delivery", "release-and-package") == \
            "/tefca-arc/help?module=pm-review-and-delivery&lesson=release-and-package"
        assert REGISTRY.learning_center_path(None) is None
        # The core never names a programme's route; the programme supplies it.
        import ast
        import inspect
        from app.core.learning import routes as r
        code = ast.unparse(ast.parse(inspect.getsource(r)))  # docstrings/comments aside
        assert "tefca-arc" not in code.lower()
