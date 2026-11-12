"""scripts/generate_record_review_package.py: one dataset, three formats, reopened and
recounted. Database-backed (rolled-back transaction); the script's own session factory
is pointed at the test session, so nothing is committed.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import record_comments as rcm
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, seed_entity, seed_intake,
)
from scripts import generate_record_review_package as pkg

T_DONE = datetime(2026, 11, 8, 9, 0, 0)


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_baseline_parsing_is_strict():
    assert pkg.parse_baseline("B1=18812, B2=21,TOTAL=25806,HELD=42") == {
        "B1": 18812, "B2": 21, "TOTAL": 25806, "HELD": 42}
    assert pkg.parse_baseline(None) == {} and pkg.parse_baseline("") == {}
    for bad in ("B5=1", "B1=x", "B1", "B1=-3", "total=1.5"):
        with pytest.raises(ValueError):
            pkg.parse_baseline(bad)


def _cols():
    from app.reports.data import october_review_dataset as ds
    return list(ds.RECORD_COLUMNS)


def _row(cols, **kv):
    r = [""] * len(cols)
    for k, v in kv.items():
        r[cols.index(k)] = v
    return r


def test_recount_counts_what_the_rows_contain():
    cols = _cols()
    rows = [_row(cols, AGT_Record_ID="a", Bucket="B1", Status="CLEAN"),
            _row(cols, AGT_Record_ID="b", Bucket="B4", Status="HELD",
                 **{"QA Comments (verbatim)": "x", "Evidence Reference": "e"}),
            _row(cols, AGT_Record_ID="c", Bucket="NOT CLASSIFIED", Status="CLEAN",
                 **{"QA Comments - Izaan (verbatim)": "y", "Report Clarifications": "z"})]
    c = pkg.recount_rows(rows, cols)
    assert c["rows"] == 3 and c["unique_agt_record_ids"] == 3 and c["blank_agt_record_ids"] == 0
    assert c["buckets"] == {"B1": 1, "B2": 0, "B3": 0, "B4": 1} and c["not_classified"] == 1
    assert c["held_rows"] == 1 and c["held_in_b4"] == 1
    assert (c["original_comments"], c["izaan_comments"], c["report_clarifications"]) == (1, 1, 1)
    assert c["evidence_reference_blank"] == 2


def test_recount_exposes_duplicate_blank_ids_and_a_held_row_outside_b4():
    cols = _cols()
    rows = [_row(cols, AGT_Record_ID="a", Bucket="B3", Status="HELD"),
            _row(cols, AGT_Record_ID="a", Bucket="B1", Status="CLEAN"),
            _row(cols, AGT_Record_ID="", Bucket="B1", Status="CLEAN")]
    c = pkg.recount_rows(rows, cols)
    assert c["unique_agt_record_ids"] == 2 and c["blank_agt_record_ids"] == 1
    assert c["held_rows"] == 1 and c["held_in_b4"] == 0


def test_baseline_is_compared_never_forced_and_differences_are_explained():
    saved = {"buckets": {"B1": 5, "B2": 0, "B3": 1, "B4": 2}, "rows": 10, "held_in_b4": 1}
    summary = {"unclassified_non_held_count": 2}
    diffs = pkg.compare_baseline(saved, summary, {"B1": 5, "B3": 3, "TOTAL": 12, "HELD": 1})
    assert {d["item"] for d in diffs} == {"B3", "TOTAL"}
    d = {x["item"]: x for x in diffs}
    assert d["B3"]["baseline"] == 3 and d["B3"]["saved"] == 1 and d["B3"]["difference"] == -2
    assert "NO usable persisted classification" in d["B3"]["explanation"]
    assert pkg.compare_baseline(saved, {"unclassified_non_held_count": 0}, {"B1": 99})[0]["explanation"] \
        .startswith("no recorded explanation")
    assert pkg.compare_baseline(saved, summary, {"B1": 5, "HELD": 1}) == []      # equal => nothing to say


def test_pdf_check_reports_what_it_can_and_never_claims_a_layout_inspection():
    info = pkg.check_pdf(b"%PDF-1.7\n...")
    assert info["starts_with_pdf_header"] is True
    assert "NOT performed" in info["layout_inspection"]
    assert pkg.check_pdf(b"<html>")["starts_with_pdf_header"] is False


def _counts(**over):
    base = {"rows": 4, "unique_agt_record_ids": 4, "blank_agt_record_ids": 0,
            "buckets": {"B1": 1, "B2": 0, "B3": 1, "B4": 2}, "not_classified": 0,
            "held_rows": 1, "held_in_b4": 1, "original_comments": 0, "izaan_comments": 0,
            "report_clarifications": 0, "evidence_reference_blank": 0}
    base.update(over)
    return base


_SUMMARY = {"unique_records": 4, "buckets": {"B1": 1, "B2": 0, "B3": 1, "B4": 2}}


def test_consistency_checks_reach_every_branch():
    ok = _counts()
    assert pkg.consistency_failures({"csv": ok, "xlsx": dict(ok)}, _SUMMARY) == []
    assert pkg.consistency_failures({}, _SUMMARY) == []                        # nothing saved: nothing to cross-check
    f = lambda counts, summary=_SUMMARY: pkg.consistency_failures(counts, summary)
    assert "csv and xlsx recounts disagree" in f({"csv": ok, "xlsx": _counts(rows=5)})[0]
    assert any("row count differs" in x for x in f({"csv": _counts(rows=5)}))
    assert any("not unique/complete" in x for x in f({"csv": _counts(unique_agt_record_ids=3)}))
    assert any("not unique/complete" in x for x in f({"csv": _counts(blank_agt_record_ids=1)}))
    assert any("HELD row is outside reporting B4" in x for x in f({"csv": _counts(held_in_b4=0)}))
    assert any("bucket counts differ" in x for x in f(
        {"csv": _counts(buckets={"B1": 2, "B2": 0, "B3": 0, "B4": 2})}))


# ── end to end on a small seeded delivery ────────────────────────────────────

class _Ctx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_a):
        return False


async def _seed(db, arc):
    intake_id, _ = await seed_intake(db, make_rows(4, arc=arc), with_job=False)
    db.add(m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="t", rule_config_hash="0" * 64,
        started_at=datetime(2026, 11, 7), completed_at=T_DONE, run_status="COMPLETE",
        assessment_metadata={"assessment_date": "2026-11-08", "source": "pinned_param"}))
    recs = {r.line_number - 1: r for r in (await db.execute(
        select(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}
    ent = await seed_entity(db, oid=f"{SYN}-PKG-{uuid.uuid4().hex[:8]}", name=f"{SYN} pkg")
    for i, status, eid in ((1, "CLEAN", ent), (2, "HELD", None)):
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id,
                                  source_record_id=recs[i].id, record_status=status,
                                  status_reason=("blocker" if status == "HELD" else None),
                                  rce_org_oid=recs[i].source_rce_id, transformation_version="t",
                                  canonical_entity_id=eid))
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id=f"REV-{uuid.uuid4().int % 10**4:04d}-000009",
                            entity_id=ent, source_record_id=recs[1].id, classification_bucket="B1",
                            classification_rule="RULE-001", classification_rule_version=2,
                            created_at=datetime(2026, 11, 8, 10)))
    await db.flush()
    sha = "ab" * 32

    def e(rec, kind, body, **o):
        d = {"source_record_id": str(rec.id), "comment_kind": kind, "comment_text": body,
             "expected": {"source_line": rec.line_number, "hcid": rec.hcid, "tefca_id": rec.tefcaid},
             "source": {"file_name": "c.xlsx", "file_sha256": sha, "sheet": "S", "column": "QA Comments"}}
        d.update(o)
        return d
    await rcm.import_comments(db, intake_id=intake_id, actor="q@x.invalid", entries=[
        e(recs[1], "QA_ORIGINAL", "orig one"),
        e(recs[1], "QA_IZAAN", "izaan one", author_attribution="Izaan", attribution_basis="owner designation"),
        e(recs[2], "QA_ORIGINAL", "held one")])
    return intake_id, recs


async def test_package_is_written_reopened_recounted_and_hashed(rolled_back_db, monkeypatch, tmp_path):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.779.60")
    out = tmp_path / "pkg"
    # xlsx+csv are produced everywhere; the PDF needs WeasyPrint's native libraries (CI/container)
    import app.core.database as database
    monkeypatch.setattr(database, "async_session_maker", lambda: _Ctx(db))
    args = SimpleNamespace(intake=str(intake_id), cutoff="2099-01-01T00:00:00", out=str(out),
                           run_id=None, formats="xlsx,csv", baseline="B1=1,B4=1,TOTAL=4,HELD=1",
                           classification="DRAFT")
    code = await pkg.build(args)
    assert code == 0, code
    man = json.loads((next(out.glob("*.MANIFEST.json"))).read_text(encoding="utf-8"))
    assert man["self_check_failures"] == [] and man["formats_skipped"] == {}
    assert "INDEPENDENT QA PENDING" in man["status"] and "DATASET INCOMPLETE" in man["status"]
    assert man["summary"]["unique_records"] == 4 and man["summary"]["held_inside_b4"] == 1
    assert man["summary"]["buckets"] == {"B1": 1, "B2": 0, "B3": 0, "B4": 1}
    assert man["summary"]["unclassified_non_held_count"] == 2
    assert len(man["unclassified_non_held_ids"]) == 2 and str(recs[2].id) not in man["unclassified_non_held_ids"]
    files = {f["format"]: f for f in man["files"]}
    assert set(files) == {"xlsx", "csv"}
    for f in files.values():                                    # recorded hash == hash of the bytes on disk
        data = (out / f["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"] and len(data) == f["size_bytes"]
    assert files["xlsx"]["recount"] == files["csv"]["recount"]  # saved xlsx and csv agree
    rc = files["csv"]["recount"]
    assert (rc["rows"], rc["unique_agt_record_ids"], rc["held_rows"], rc["held_in_b4"]) == (4, 4, 1, 1)
    assert (rc["original_comments"], rc["izaan_comments"]) == (2, 1)       # incl. the HELD record's comment
    assert rc["not_classified"] == 2 and rc["buckets"]["B4"] == 1
    # baseline compared, not forced: TOTAL/B1/B4/HELD match, nothing invented to match
    assert man["baseline_comparison"]["differences"] == []
    # earlier output is preserved: a second run into the same directory is refused
    assert await pkg.build(args) == 2


async def test_baseline_differences_are_reported_with_the_datasets_own_explanation(
        rolled_back_db, monkeypatch, tmp_path):
    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.61")
    import app.core.database as database
    monkeypatch.setattr(database, "async_session_maker", lambda: _Ctx(db))
    out = tmp_path / "pkg2"
    args = SimpleNamespace(intake=str(intake_id), cutoff="2099-01-01T00:00:00", out=str(out),
                           run_id=None, formats="csv", baseline="B1=100,B3=50,TOTAL=4", classification="DRAFT")
    assert await pkg.build(args) == 0
    man = json.loads((next(out.glob("*.MANIFEST.json"))).read_text(encoding="utf-8"))
    d = {x["item"]: x for x in man["baseline_comparison"]["differences"]}
    assert set(d) == {"B1", "B3"} and d["B1"]["saved"] == 1 and d["B3"]["saved"] == 0
    assert "NO usable persisted classification" in d["B1"]["explanation"]
    assert man["summary"]["buckets"]["B1"] == 1                           # still what the data says


async def test_a_corrupted_rendering_fails_the_self_check(rolled_back_db, monkeypatch, tmp_path):
    """The script must catch a saved file that does not match the dataset."""
    from app.reports.data import october_review_dataset as ds
    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.62")
    import app.core.database as database
    monkeypatch.setattr(database, "async_session_maker", lambda: _Ctx(db))
    real = ds.to_csv

    def tampered(dataset):
        text = real(dataset).decode("utf-8-sig")
        return text.replace("orig one", "orig ONE").encode("utf-8-sig")

    monkeypatch.setattr(ds, "to_csv", tampered)
    out = tmp_path / "pkg3"
    args = SimpleNamespace(intake=str(intake_id), cutoff="2099-01-01T00:00:00", out=str(out),
                           run_id=None, formats="csv", baseline=None, classification="DRAFT")
    assert await pkg.build(args) == 3
    man = json.loads((next(out.glob("*.MANIFEST.json"))).read_text(encoding="utf-8"))
    assert any("saved rows differ from the dataset rows" in f for f in man["self_check_failures"])


async def test_the_script_refuses_instead_of_guessing(rolled_back_db, monkeypatch, tmp_path):
    db = rolled_back_db
    bare, _ = await seed_intake(db, make_rows(1, arc="9.99.779.63"), with_job=False)   # no completed run
    import app.core.database as database
    monkeypatch.setattr(database, "async_session_maker", lambda: _Ctx(db))
    base = dict(cutoff="2099-01-01T00:00:00", run_id=None, formats="csv", baseline=None,
                classification="DRAFT")
    assert await pkg.build(SimpleNamespace(intake=str(bare), out=str(tmp_path / "a"), **base)) == 2
    assert await pkg.build(SimpleNamespace(intake=str(uuid.uuid4()), out=str(tmp_path / "b"), **base)) == 2
    assert await pkg.build(SimpleNamespace(intake=str(bare), out=str(tmp_path / "c"),
                                           **{**base, "formats": "docx"})) == 2
    nonempty = tmp_path / "d"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("earlier output")
    assert await pkg.build(SimpleNamespace(intake=str(bare), out=str(nonempty), **base)) == 2
    assert (nonempty / "keep.txt").read_text() == "earlier output"                # preserved
    assert pkg.main(["--intake", "x", "--cutoff", "y", "--out", str(tmp_path / "e"),
                     "--baseline", "B9=1"]) == 2
