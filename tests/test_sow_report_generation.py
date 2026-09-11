"""SOW report families as delivered documents, PM release control, the package.

What is pinned here:

  * a Task 3 weekly / final report is a STRATIFIED LIST of Participants and
    Subparticipants under the four Government categories — entity rows in the
    right table, the Government's words as headings, contract number cited;
  * a record enters a category only on a standing QA approval; pending rows are
    listed separately and never as findings;
  * the weekly report carries suggested changes only, the final report carries
    suggested AND implemented changes (Section C, Task 3);
  * the delivered document passes the automated accessibility checks;
  * PM release moves DRAFT -> PM_REVIEWED -> READY_FOR_DELIVERY and refuses
    anything else, without touching the snapshot or dataset;
  * the package contains the HTML, the CSV, a README with the contract number
    and the SHA-256 of every member.

DEVELOPMENT/TEST DATA. Nothing here is an ONC finding.
"""
from __future__ import annotations

import io
import json
import re
import zipfile

import pytest


class _FakeResult:
    def scalars(self): return self
    def all(self): return []
    def scalar(self): return None
    def scalar_one_or_none(self): return None


class FakeDB:
    """Answers every query empty — the insufficient-data path end to end."""
    async def execute(self, *a, **k): return _FakeResult()
    async def get(self, *a, **k): return None
    def add(self, *a, **k): pass
    async def commit(self): pass
    async def rollback(self): pass


SYNTHETIC_ROWS = {
    "no_discrepancy": [
        {"review_id": "REV-9999-000001", "entity_name": "SYNTHETIC CLINIC ALPHA",
         "entity_level": "Subparticipant", "rce_org_oid": "9.99.555.4.1",
         "qhin": "QHIN 9.99.555.0.1", "category": "no_discrepancy",
         "category_label": "No discrepancies identified", "category_number": 1,
         "rule": "RULE-001 v2", "reportable_at": "2026-09-08T10:00:00",
         "reviewed_at": "2026-09-08T09:00:00"},
    ],
    "minor_administrative": [],
    "inexplicable": [
        {"review_id": "REV-9999-000002", "entity_name": "SYNTHETIC CLINIC BRAVO",
         "entity_level": "Participant", "rce_org_oid": "9.99.555.4.2",
         "qhin": "QHIN 9.99.555.0.1", "category": "inexplicable",
         "category_label": "Inexplicable discrepancies", "category_number": 3,
         "rule": "RULE-004 v2", "reportable_at": "2026-09-09T10:00:00",
         "reviewed_at": "2026-09-09T09:00:00"},
    ],
    "non_compliant": [],
}
SYNTHETIC_PENDING = [
    {"review_id": "REV-9999-000003", "entity_name": "SYNTHETIC CLINIC CHARLIE",
     "entity_level": "Subparticipant", "rce_org_oid": "9.99.555.4.3",
     "qhin": "Unresolved", "category": "inexplicable",
     "category_label": "Inexplicable discrepancies", "category_number": 3,
     "rule": "RULE-004 v2", "reportable_at": None, "reviewed_at": None,
     "pending_reason": "No standing QA approval"},
]


@pytest.fixture
def populated_sow(monkeypatch):
    """Point the stratified list at a fixed synthetic set; leave the rest real."""
    from app.reports.data import sow_report_data as m

    async def _lists(self, review_cycle_id=None, period_start=None, period_end=None):
        return {
            "columns": [{"key": k, "label": v} for k, v in m.SowReportDataService.ENTITY_COLUMNS],
            "entity_lists": {k: list(v) for k, v in SYNTHETIC_ROWS.items()},
            "pending_qa": list(SYNTHETIC_PENDING),
            "counts": {**{k: len(v) for k, v in SYNTHETIC_ROWS.items()},
                       "listed_total": 2, "pending_qa": 1},
            "note": "synthetic",
        }

    monkeypatch.setattr(m.SowReportDataService, "stratified_entities", _lists)
    return m


async def _generate(report_type, **params):
    from app.reports.generator import generate_report

    return await generate_report(FakeDB(), report_type=report_type, persist=False,
                                 query_parameters=params or None)


def _body(html: str) -> str:
    return re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL)


class TestTaskThreeWeekly:

    @pytest.mark.asyncio
    async def test_weekly_is_a_stratified_entity_list_with_government_wording(self, populated_sow):
        result = await _generate("retrospective_weekly",
                                 period_start="2026-09-07", period_end="2026-09-13",
                                 suggested_changes="Clarify D4 address materiality")
        html = _body(result["html"])
        for label in ("No discrepancies identified", "Minor or administrative discrepancies",
                      "Inexplicable discrepancies", "Non-compliant discrepancies"):
            assert label in html
        # The entities are on the page, in the right category section.
        alpha = html.index("SYNTHETIC CLINIC ALPHA")
        bravo = html.index("SYNTHETIC CLINIC BRAVO")
        assert html.index("1) No discrepancies identified") < alpha < html.index("2) Minor")
        assert html.index("3) Inexplicable discrepancies") < bravo < html.index("4) Non-compliant")
        # The pending record is listed as pending, not under a category.
        charlie = html.index("SYNTHETIC CLINIC CHARLIE")
        assert charlie > html.index("Reviews pending independent QA")
        assert "No standing QA approval" in html

    @pytest.mark.asyncio
    async def test_weekly_cites_the_contract_and_the_deliverable(self, populated_sow):
        result = await _generate("retrospective_weekly")
        html = result["html"]
        assert "7571MN26F80064" in html
        assert "Deliverable D3.1" in html
        assert "Task 3 Weekly Progress Report" in html
        assert result["dataset"]["deliverable"] == "D3.1"

    @pytest.mark.asyncio
    async def test_weekly_carries_suggested_but_not_implemented_changes(self, populated_sow):
        result = await _generate("retrospective_weekly",
                                 suggested_changes="Line one\nLine two",
                                 implemented_changes="Should not appear")
        html = _body(result["html"])
        assert "Line one" in html and "Line two" in html
        assert "Implemented changes" not in html
        assert "Should not appear" not in html

    @pytest.mark.asyncio
    async def test_weekly_passes_automated_accessibility_checks(self, populated_sow):
        result = await _generate("retrospective_weekly")
        assert result["accessibility"]["automated_checks_passed"], \
            result["accessibility"]["errors"]

    @pytest.mark.asyncio
    async def test_internal_shorthand_is_not_used_as_a_category_heading(self, populated_sow):
        result = await _generate("retrospective_weekly")
        headings = re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", _body(result["html"]), re.DOTALL)
        for h in headings:
            assert not re.search(r"\bB[1-4]\b", h), h

    @pytest.mark.asyncio
    async def test_csv_lists_each_entity_with_its_category_number(self, populated_sow):
        result = await _generate("retrospective_weekly")
        csv_text = result["csv"]
        assert "# Contract number: 7571MN26F80064" in csv_text
        assert "SYNTHETIC CLINIC ALPHA" in csv_text
        line = next(l for l in csv_text.splitlines() if "SYNTHETIC CLINIC BRAVO" in l)
        assert line.startswith("3,")
        assert "SYNTHETIC CLINIC CHARLIE" in csv_text.split("## Pending independent QA")[1]


class TestTaskThreeFinalAndOtherFamilies:

    @pytest.mark.asyncio
    async def test_final_report_carries_implemented_changes(self, populated_sow):
        result = await _generate("retrospective_final",
                                 suggested_changes="S1", implemented_changes="I1")
        html = _body(result["html"])
        assert "Implemented changes" in html and "I1" in html and "S1" in html
        assert result["dataset"]["deliverable"] == "D3.2"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("report_type,deliverable", [
        ("ongoing_biweekly", "D4.1"), ("ongoing_quarterly", "D4.2"),
        ("priority_status", "D5.1"), ("priority_quarterly", "D5.2"),
    ])
    async def test_every_family_renders_and_passes_accessibility(self, populated_sow,
                                                                 report_type, deliverable):
        result = await _generate(report_type)
        assert result["dataset"]["deliverable"] == deliverable
        assert f"Deliverable {deliverable}" in result["html"]
        assert result["accessibility"]["automated_checks_passed"], \
            result["accessibility"]["errors"]

    @pytest.mark.asyncio
    async def test_empty_database_renders_honestly(self):
        """No stub: every list is empty and the document says so."""
        result = await _generate("retrospective_weekly")
        html = _body(result["html"])
        assert "No Participant or Subparticipant is listed under this category" in html
        assert "No review record is pending QA" in html
        assert result["accessibility"]["automated_checks_passed"]

    def test_report_type_keys_fit_the_column(self):
        from app.reports.data.sow_report_data import SOW_REPORT_TYPES

        for key in SOW_REPORT_TYPES:
            assert len(key) <= 20, key  # review_reports.report_type is String(20)

    def test_the_stale_not_implemented_note_is_gone(self):
        import inspect

        from app.reports.data import sow_report_data

        assert "Per-QHIN sample draw is not implemented" not in inspect.getsource(sow_report_data)


class TestReleaseControl:

    def test_default_is_draft(self):
        from app.reports.data.release import STATUS_DRAFT, current_release

        assert current_release(None)["status"] == STATUS_DRAFT
        assert current_release({"snapshot": {}})["status"] == STATUS_DRAFT

    def test_happy_path_and_refusals(self):
        from app.reports.data.release import (ReleaseTransitionError,
                                              apply_transition)

        data = {"snapshot": {"report_id": "X"}, "dataset": {"a": 1}}
        with pytest.raises(ReleaseTransitionError):
            apply_transition(data, action="READY_FOR_DELIVERY", actor="pm")
        data, e1 = apply_transition(data, action="PM_REVIEWED", actor="pm@x", note="ok")
        assert data["release"]["status"] == "PM_REVIEWED"
        data, e2 = apply_transition(data, action="READY_FOR_DELIVERY", actor="pm@x")
        assert data["release"]["status"] == "READY_FOR_DELIVERY"
        assert [h["status"] for h in data["release"]["history"]] == ["PM_REVIEWED", "READY_FOR_DELIVERY"]
        with pytest.raises(ReleaseTransitionError):
            apply_transition(data, action="PM_REVIEWED", actor="pm@x")
        data, _ = apply_transition(data, action="RETURNED_TO_DRAFT", actor="pm@x")
        data, _ = apply_transition(data, action="PM_REVIEWED", actor="pm@x")
        # The frozen record is untouched throughout.
        assert data["snapshot"] == {"report_id": "X"} and data["dataset"] == {"a": 1}
        with pytest.raises(ReleaseTransitionError):
            apply_transition(data, action="SENT", actor="pm@x")

    def test_package_contents_and_hashes(self):
        import hashlib

        from app.reports.data.release import CONTRACT_NUMBER, build_package, current_release

        html = "<html><body>report</body></html>"
        csv_text = "# header\r\n1,REV,X\r\n"
        pkg = build_package(
            report_id="DA-ARC-2026-999", html=html, csv_text=csv_text, pdf_bytes=None,
            snapshot={"data_classification": "DEVELOPMENT_TEST",
                      "generation_timestamp": "2026-09-10T00:00:00+00:00",
                      "generated_by": "pm@x", "data_payload_hash": "abc"},
            release=current_release({"release": {"status": "READY_FOR_DELIVERY", "history": []}}),
            deliverable={"deliverable": "D3.1", "task": "Task 3", "title": "Task 3 Weekly Progress Report"},
            pdf_unavailable_reason="engine absent")
        archive = zipfile.ZipFile(io.BytesIO(pkg["bytes"]))
        names = set(archive.namelist())
        assert names == {"DA-ARC-2026-999.html", "DA-ARC-2026-999.csv", "README.txt", "manifest.json"}
        readme = archive.read("README.txt").decode("utf-8")
        assert CONTRACT_NUMBER in readme
        assert "NOT FOR GOVERNMENT DELIVERY" in readme
        assert "Ready for delivery" in readme
        assert "has not been transmitted" in readme
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["files"]["DA-ARC-2026-999.html"] == hashlib.sha256(html.encode()).hexdigest()
        assert archive.read("DA-ARC-2026-999.csv").startswith(b"\xef\xbb\xbf")

    def test_route_surface_exists(self):
        from app.reports.routes import router

        paths = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
        assert ("/api/reports/{report_id}/release", ("GET",)) in paths
        assert ("/api/reports/{report_id}/release", ("POST",)) in paths
        assert ("/api/reports/{report_id}/package", ("GET",)) in paths
