"""Real delivery processed in DEV is never labelled synthetic (owner, 2026-11-27).

The record-review export took its classification from the deployment-wide data state: every intake without the
Government authorisation marker was DEVELOPMENT_TEST, whose wording elsewhere is "synthetic demonstration data".
For a REAL delivered file processed in development that is false. The owner attests such an intake through
`RCE_REAL_DELIVERY_PERIODS`; its per-delivery export is then REAL_DELIVERY_PROCESSED_IN_DEV and carries the fixed
title "<Month YYYY> RCE Results — Processed in DEV — Pending Human QA Acceptance". Nothing else changes:
GOVERNMENT, NO_DATASET_LOADED and production deployments are untouched; no Government marker is set or implied.
Pure tests (no database).
"""
from __future__ import annotations

import asyncio
import io

import pytest
from openpyxl import load_workbook

from app.reports.data import october_review_dataset as d
from app.reports.data import source_provenance as sp
from app.reports.engine.xlsx_engine import render_workbook
from record_review_pdf_support import _fact  # tests/ is on sys.path (pytest rootdir conftest)

INTAKE = "cc1716a0-e685-4ca3-a0a0-467324743d93"
OTHER = "00000000-0000-4000-8000-000000000001"
LABEL = "November 2026 RCE Results — Processed in DEV — Pending Human QA Acceptance"


@pytest.fixture
def attested(monkeypatch):
    monkeypatch.setenv(sp.REAL_DELIVERY_PERIODS_ENV, '{"%s": "November 2026"}' % INTAKE.upper())


def test_exact_owner_wording_and_code_fits_the_column():
    assert sp.real_delivery_label("November 2026") == LABEL
    assert len(sp.CLASSIFICATION_REAL_DELIVERY_DEV) <= 32
    for word in ("Government", "Production", "Approved", "Final", "synthetic", "MOCK"):
        assert word.lower() not in LABEL.lower()


def test_attestation_is_read_per_intake(attested):
    assert sp.attested_real_delivery_period(INTAKE) == "November 2026"
    assert sp.attested_real_delivery_period(INTAKE.upper()) == "November 2026"
    assert sp.attested_real_delivery_period(OTHER) is None
    assert sp.attested_real_delivery_period("not-a-uuid") is None
    assert sp.attested_real_delivery_period(None) is None


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", '{"%s": "Nov 2026"}' % INTAKE,
                                 '{"%s": "November 26"}' % INTAKE, '{"%s": 2026}' % INTAKE,
                                 '{"%s": "November 2026 (final)"}' % INTAKE])
def test_malformed_setting_is_ignored_never_guessed(monkeypatch, raw):
    monkeypatch.setenv(sp.REAL_DELIVERY_PERIODS_ENV, raw)
    assert sp.attested_real_delivery_period(INTAKE) is None


def _resolve(monkeypatch, base, env="development", intake=INTAKE):
    async def fake(db):
        return base
    monkeypatch.setattr(sp, "resolve_classification", fake)
    monkeypatch.setenv("ENVIRONMENT", env)
    return asyncio.run(sp.resolve_export_classification(None, intake))


def test_attested_intake_in_development_is_real_delivery(monkeypatch, attested):
    assert _resolve(monkeypatch, sp.CLASSIFICATION_DEVELOPMENT) == sp.CLASSIFICATION_REAL_DELIVERY_DEV


def test_unattested_intake_keeps_development_test(monkeypatch, attested):
    assert _resolve(monkeypatch, sp.CLASSIFICATION_DEVELOPMENT, intake=OTHER) == sp.CLASSIFICATION_DEVELOPMENT


@pytest.mark.parametrize("base", [sp.CLASSIFICATION_GOVERNMENT, sp.CLASSIFICATION_NONE])
def test_government_and_no_dataset_are_never_changed(monkeypatch, attested, base):
    assert _resolve(monkeypatch, base) == base


def test_production_deployment_is_never_changed(monkeypatch, attested):
    assert _resolve(monkeypatch, sp.CLASSIFICATION_DEVELOPMENT, env="production") == sp.CLASSIFICATION_DEVELOPMENT


def _facts():
    return [_fact(0, "B1"), _fact(1, "B2"), _fact(2, "B3"), _fact(3, "B4"),
            _fact(4, "B4", status="HELD", flag="HELD")]


def _build(classification, label):
    return d.build_dataset(_facts(), report_id="RR-TEST", findings_count=None, assessment_date="2026-11-26",
                           classification=classification, provenance_label=label)


def test_label_leads_the_banner_and_has_a_summary_row():
    ds = _build(sp.CLASSIFICATION_REAL_DELIVERY_DEV, LABEL)
    assert ds["banner"].startswith(LABEL + ". ")
    assert d.DRAFT_BANNER in ds["banner"]                       # DRAFT / QA PENDING wording is kept
    assert ds["provenance_label"] == LABEL
    rows = [r for sheet in ds["sheets"].values() for r in sheet.get("rows", [])]
    assert ["Data provenance", LABEL] in rows
    assert all(sheet.get("note", ds["banner"]).startswith(LABEL) for sheet in ds["sheets"].values()
               if "note" in sheet)


@pytest.mark.parametrize("classification,label", [
    (sp.CLASSIFICATION_REAL_DELIVERY_DEV, None),                # the code without the title
    (sp.CLASSIFICATION_DEVELOPMENT, LABEL),                     # the title on development/synthetic data
    (sp.CLASSIFICATION_GOVERNMENT, LABEL),                      # the title on Government data
])
def test_label_and_classification_travel_together(classification, label):
    with pytest.raises(d.DatasetRefused, match="provenance label"):
        _build(classification, label)


def test_existing_classifications_render_exactly_as_before():
    before = d.build_dataset(_facts(), report_id="RR-TEST", findings_count=None, assessment_date="2026-11-26",
                             classification=sp.CLASSIFICATION_DEVELOPMENT)
    assert before["banner"].startswith(d.DRAFT_BANNER) and before["provenance_label"] is None
    assert LABEL not in before["banner"]
    rows = [r for sheet in before["sheets"].values() for r in sheet.get("rows", [])]
    assert not any(r and r[0] == "Data provenance" for r in rows)


def test_workbook_carries_the_label_and_code():
    wb = load_workbook(io.BytesIO(render_workbook(_build(sp.CLASSIFICATION_REAL_DELIVERY_DEV, LABEL))))
    assert wb.properties.category == sp.CLASSIFICATION_REAL_DELIVERY_DEV
    text = " ".join(str(c) for ws in wb.worksheets for row in ws.iter_rows(max_row=6, values_only=True)
                    for c in row if c is not None)
    assert LABEL in text
    assert "synthetic demonstration" not in text.lower()
