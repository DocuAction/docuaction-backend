"""Appendix E / CSV Annex A: a finding's description may quote the reference profile
("4,584 of 23,566 records (19.45%) in the profiled delivery"). Report DA-ARC-2026-074 showed that
such figures can be read as the current delivery's results. The narrow correction is a note, in the
PDF/HTML template and on the CSV annex summary line, that labels those figures as the calibration
profile. Stored descriptions are NOT rewritten (historical findings keep their text and version).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from app.reports.engine.csv_engine import delivery_processing_to_csv

ROOT = Path(__file__).resolve().parents[1]


def _dataset():
    return {
        "delivery": {"job_id": "j", "intake_id": "i", "label": "L", "file_name": "f.txt", "sha256": "0" * 64},
        "build": {"git_sha": "abc", "migration_revision": "m"},
        "dispositions": {"available": False, "rows": []},
        "reconciliation": {"available": False},
        "findings": {"available": True, "rows_shown": 1, "rows_total": 1, "total": 1, "open_high": 0,
                     "rows": [{"issue_code": "DQ-1", "line_number": 2, "rule_id": "NPI-001", "rule_version": "1.0.0",
                               "issue_type": "NPI_NOT_SUPPLIED", "severity": "INFORMATIONAL", "field_name": "NPI",
                               "correction_authority": "NO_CORRECTION", "resolution": "OPEN",
                               "description": "No NPI supplied. 4,584 of 23,566 records (19.45%) in the profiled delivery."}]},
        "verification": {"sources": []},
        "analyst": {"available": False, "review_records": []},
        "limitations": [],
    }


def _annex_a(text):
    rows = list(csv.reader(io.StringIO(text)))
    start = next(i for i, r in enumerate(rows) if r and r[0].startswith("## Annex A"))
    return rows[start + 1:]


def test_csv_annex_a_summary_line_labels_profile_figures_and_keeps_its_shape():
    text = delivery_processing_to_csv(_dataset(), "DA-ARC-2026-999", "2026-10-11T00:00:00", rule_set_version=3)
    a = _annex_a(text)
    assert a[0][0].startswith("Rows included 1 of 1")
    assert "reference profile the rule set was calibrated against" in a[0][0]
    assert "not results for this delivery" in a[0][0]
    assert a[1][0] == "Issue code"                      # header still on line 1
    assert a[2][0] == "DQ-1"                            # first finding still on line 2
    assert "4,584 of 23,566" in a[2][-1]                # the stored description is untouched


def test_html_template_carries_the_note_inside_appendix_e():
    src = (ROOT / "app" / "reports" / "templates" / "delivery_processing.html").read_text(encoding="utf-8")
    start = src.index("<h2>Appendix E. Findings</h2>")
    end = src.index("<table", start)
    block = src[start:end]
    assert "the profiled delivery" in block
    assert "reference profile the rule set was" in block
    assert "not a result for this delivery" in block


def test_stored_rule_text_is_not_rewritten_by_this_change():
    # The rule text itself still carries the profile rationale (history must not be reinterpreted).
    from app.tefca_registry.rce import quality_rules as qr
    src = Path(qr.__file__).read_text(encoding="utf-8")
    assert "4,584 of 23,566 records (19.45%) in the profiled delivery" in src
