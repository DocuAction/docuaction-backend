"""Learning Center ↔ application synchronisation.

Cross-repo where the frontend checkout is beside this one (as the Phase 7.5
cutover tests already assume): every traced route is a real screen, every
help key the screens ask for exists, every Learn-more target resolves, and the
change-check script maps changed screens to the modules that teach them.

Skipped, not failed, when the frontend checkout is absent — a missing sibling
repo is a CI layout fact, not an LMS defect.
"""
from __future__ import annotations

import os
import re

import pytest

from app.core.learning.framework import KNOWLEDGE_VERSION, Role
from app.Tefca.learning_content import HELP, MODULES, REGISTRY
from app.Tefca.learning_path_content import EXTRA_HELP, FEATURES, KEYWORDS, PATH_ORDER

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(os.path.dirname(ROOT), "frontend")
needs_frontend = pytest.mark.skipif(
    not os.path.isdir(os.path.join(FRONTEND, "src", "app", "tefca-arc")),
    reason="frontend checkout not beside the backend")


def _frontend_sources():
    for base, _dirs, files in os.walk(os.path.join(FRONTEND, "src")):
        for name in files:
            if name.endswith((".js", ".jsx", ".ts", ".tsx")):
                path = os.path.join(base, name)
                with open(path, encoding="utf-8", errors="replace") as handle:
                    yield path, handle.read()


class TestRoutesAndKeys:

    @needs_frontend
    def test_every_traced_route_is_a_real_screen(self):
        for link in FEATURES:
            page = os.path.join(FRONTEND, "src", "app", *link.route.strip("/").split("/"), "page.js")
            assert os.path.isfile(page), f"{link.feature}: {link.route} has no page"

    @needs_frontend
    def test_every_help_key_the_screens_ask_for_exists(self):
        used = set()
        for _path, code in _frontend_sources():
            used.update(re.findall(r'helpKey="([a-z_.]+)"', code))
        assert used, "no LearningHelp usage found in the frontend"
        missing = sorted(k for k in used if REGISTRY.help_for(k) is None)
        assert not missing, f"screens ask for help keys the registry lacks: {missing}"

    @needs_frontend
    def test_the_learning_center_route_exists_and_reads_the_deep_link(self):
        page = os.path.join(FRONTEND, "src", "app", "tefca-arc", "help", "page.js")
        with open(page, encoding="utf-8") as handle:
            code = handle.read()
        assert "params.get('module')" in code and "params.get('lesson')" in code
        assert REGISTRY.learning_center_route == "/tefca-arc/help"

    @needs_frontend
    def test_the_sidebar_offers_the_learning_center(self):
        layout = os.path.join(FRONTEND, "src", "components", "AppLayout.js")
        with open(layout, encoding="utf-8") as handle:
            code = handle.read()
        assert "'/tefca-arc/help'" in code and "'tefca_help'" in code

    def test_every_learn_more_target_resolves(self):
        for topic in HELP + EXTRA_HELP:
            assert topic.learn_more, topic.key
            module = REGISTRY.module(topic.module_slug)
            assert module is not None, topic.key
            if topic.lesson_slug:
                assert topic.lesson_slug in {l.slug for l in module.lessons}, topic.key
            path = REGISTRY.learning_center_path(topic.module_slug, topic.lesson_slug)
            assert path.startswith("/tefca-arc/help?module=") and "/api/" not in path

    def test_help_learn_more_is_a_ui_route_not_an_api_url(self):
        from app.core.learning import routes as r
        import inspect
        src = inspect.getsource(r.contextual_help)
        assert "learning_center_path" in src


class TestModuleIntegrity:

    def test_sixteen_unique_modules_in_order(self):
        slugs = [m.slug for m in MODULES]
        assert len(slugs) == 16 and len(set(slugs)) == 16
        assert slugs == PATH_ORDER

    def test_every_module_is_searchable_by_a_plain_word(self):
        for m in MODULES:
            assert m.keywords, f"{m.slug} has no search keywords"

    @pytest.mark.parametrize("term,slug", [
        ("ingestion", "delivery-and-ingestion"), ("QHIN", "qhin-relationships"),
        ("sampling", "stratification-and-sampling"), ("assignment", "work-creation-and-assignment"),
        ("analyst", "analyst-review"), ("evidence", "evidence-and-sources"),
        ("QA", "qa-review"), ("maker checker", "maker-checker"), ("report", "reports"),
        ("D3.1", "reports"), ("PM release", "pm-review-and-delivery"),
        ("security", "security-and-data-handling"),
    ])
    def test_search_finds_the_module_for_the_operational_term(self, term, slug):
        hits = REGISTRY.search(term, role=Role.PROGRAM_MANAGER, limit=50)
        assert slug in {h.get("module_slug") for h in hits}, f"{term!r} does not find {slug}"

    def test_required_sections_present_on_every_module(self):
        for m in MODULES:
            payload = m.to_dict()
            for key in ("guide", "version", "effective_date", "status", "history", "keywords",
                        "lessons", "checks", "audience"):
                assert key in payload, f"{m.slug}.{key}"
            for part in ("what_is_this", "why_it_matters", "what_automation_does",
                         "what_human_does", "steps", "what_not_to_do", "what_happens_next"):
                assert payload["guide"][part], f"{m.slug}.guide.{part}"

    def test_changed_modules_carry_history(self):
        for slug in ("reports", "qa-review", "pm-review-and-delivery"):
            m = REGISTRY.module(slug)
            assert m.history and m.version != m.history[0].version, slug


class TestContractualTruth:

    def _text(self, m):
        parts = [m.objective]
        if m.guide:
            g = m.guide.to_dict()
            parts += [str(v) for v in g.values()]
        for l in m.lessons:
            parts += [l.body, l.example or "", *l.common_mistakes]
        return "\n".join(parts).lower()

    def test_no_internal_target_is_taught_as_a_contractual_sla(self):
        for m in MODULES:
            text = self._text(m)
            for phrase in ("within 2 days", "within 5 days", "within 10 days", "within 21 days",
                           "sla of", "service level of"):
                assert phrase not in text, (m.slug, phrase)

    def test_automation_never_makes_the_determination(self):
        for m in MODULES:
            text = self._text(m)
            assert "system determines" not in text.replace("system determines nothing", "")
            assert "automatically non-compliant" not in text
            assert "automatically emails" not in text and "automatically sends" not in text

    def test_d2_is_not_presented_as_accepted(self):
        from app.Tefca.learning_path_content import LIBRARY
        d2 = next(i for i in LIBRARY if "D2" in i.title)
        assert d2.authority.value == "PROPOSED_METHODOLOGY"
        assert "not accepted" in d2.note.lower()

    def test_no_report_lesson_teaches_the_retired_five_gate_watermark(self):
        reports = REGISTRY.module("reports")
        text = self._text(reports)
        assert "five gates" not in text and "not for cor release" not in text
        assert "pm_reviewed" in text and "ready_for_delivery" in text

    def test_synthetic_data_is_never_called_government_data(self):
        for m in MODULES:
            text = self._text(m)
            assert "synthetic government" not in text and "synthetic onc data" not in text

    def test_the_separation_of_stages_is_taught(self):
        overview = self._text(REGISTRY.module("tefca-arc-overview"))
        assert "observes" in overview and "determine" in overview and "qa" in overview


class TestChangeCheckScript:

    def test_maps_changed_screens_to_modules(self):
        from scripts.lms_change_check import (learning_content_changed,
                                              modules_for_changed_files)
        needed = modules_for_changed_files(
            ["src/app/tefca-arc/reports/page.js", "src/lib/formatDate.js"], FEATURES)
        assert "reports" in needed and "pm-review-and-delivery" in needed
        assert "delivery-and-ingestion" not in needed
        assert modules_for_changed_files(["src/lib/formatDate.js"], FEATURES) == {}
        assert learning_content_changed(["app/Tefca/learning_path_content.py"])
        assert not learning_content_changed(["app/reports/routes.py"])

    def test_traceability_is_verified_at_the_shipped_version(self):
        assert all(f.last_verified_version == KNOWLEDGE_VERSION for f in FEATURES)
        assert REGISTRY.stale_features() == []


class TestApiShape:

    def test_program_payload_shape_for_every_role(self):
        for role in Role:
            payload = REGISTRY.to_dict(role=role)
            for key in ("program", "program_title", "knowledge_version", "last_updated",
                        "modules", "paths", "features", "library", "glossary",
                        "contextual_help", "statement_classifications"):
                assert key in payload, (role, key)
            assert payload["paths"][0]["role"] == role.value or role is Role.ANY

    def test_keywords_are_not_rendered_as_taxonomy(self):
        """Keywords are for search; the guide is the content standard."""
        for m in MODULES:
            assert m.guide and m.keywords
            assert all(isinstance(k, str) and k.strip() for k in m.keywords)
