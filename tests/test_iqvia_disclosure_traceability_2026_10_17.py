"""Delivery IQVIA disclosure: stable references, deterministic rendering, honest gaps (found on DEV, report DA-ARC-2026-080).

DA-ARC-2026-080 showed two defects the existing tests could not see:
  1. The registered CSV and the CSV served by "Download CSV" differed by 48 bytes: the two decision rows were printed in
     opposite order. The dataset is stored as JSONB, which reorders object keys (shorter first); the CSV and the HTML
     iterated the mapping in stored order. Two renderings of one report must be byte-identical.
  2. The disclosure omitted what a reviewer needs to trace it: a stable assessment reference, assessment timestamps,
     assessor attribution as a role, entity/review linkage coverage, the source date (or "not recorded"), and the
     current lookup availability separate from historical use.
No database: local fixtures only. Every identifier is synthetic.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone

import pytest

from app.reports.engine.csv_engine import delivery_processing_to_csv
from app.reports.engine.template_engine import render_html
from app.tefca_registry.rce import iqvia_advisory_assessment as aa

pytestmark = pytest.mark.regression

SNAP = uuid.UUID("87616d23-055b-4905-802d-2add5557e230")
A1 = uuid.UUID("190fcede-63c9-4038-9684-9dc1e5688bb5")
A2 = uuid.UUID("f5a8e089-0db6-4ef8-a2fa-e6503e738b08")


def _rows():
    base = {"snapshot_id": SNAP, "snapshot_sha256_prefix": "dbc52f5269a3",
            "snapshot_received_at": datetime(2026, 10, 8, 8, 37, tzinfo=timezone.utc)}
    return [
        {**base, "id": A1, "decision": aa.D_NOT_FOUND, "assessed_at": datetime(2026, 10, 15, 16, 42, 30, tzinfo=timezone.utc),
         "assessed_by": "qa@example.invalid", "assessed_role": "qalead", "review_id": "REV-2026-000983", "entity_id": uuid.uuid4()},
        {**base, "id": A2, "decision": aa.D_INSUFFICIENT, "assessed_at": datetime(2026, 10, 17, 22, 40, 18, tzinfo=timezone.utc),
         "assessed_by": "qa@example.invalid", "assessed_role": "qalead", "review_id": None, "entity_id": None},
    ]


def _jsonb_roundtrip(obj):
    """Postgres JSONB sorts object keys by length, then bytewise; reproduce that for any mapping."""
    if isinstance(obj, dict):
        return {k: _jsonb_roundtrip(obj[k]) for k in sorted(obj, key=lambda k: (len(k.encode()), k.encode()))}
    if isinstance(obj, list):
        return [_jsonb_roundtrip(x) for x in obj]
    return obj


def _disclosure(flag=True):
    return json.loads(json.dumps(aa.disclosure_from_rows(flag_enabled=flag, rows=_rows()), default=str))


def _csv(iq):
    return delivery_processing_to_csv({"iqvia_use": iq}, "RPT-1", "2026-10-17T00:00:00Z")


def test_jsonb_reordering_really_flips_the_two_decisions():
    """Guard for the premise: if this stops being true the next test proves nothing."""
    d = _disclosure()
    assert list(d["by_decision"]) == sorted(d["by_decision"])           # as built: alphabetical
    assert list(_jsonb_roundtrip(d)["by_decision"]) != list(d["by_decision"])  # as stored: shorter key first


def test_csv_is_byte_identical_whether_or_not_the_dataset_went_through_jsonb():
    d = _disclosure()
    assert _csv(d) == _csv(_jsonb_roundtrip(d))
    text = _csv(_jsonb_roundtrip(d))
    assert text.index("Decision INSUFFICIENT_EVIDENCE") < text.index("Decision NOT_FOUND_IN_EXTRACT")


def _html(iq):
    """The real delivery_processing template over the real stored 060 fixture, with only iqvia_use replaced."""
    import copy
    import test_delivery_report_layout_2026_10_07 as L
    d = copy.deepcopy(L._fixture("060"))
    ds, snap = d["dataset"], d["snapshot"]
    ds["iqvia_use"] = iq
    ctx = {k: v for k, v in ds.items() if k not in ("chart_list", "service_version", "review_cycle_id")}
    brand = L.current_branding()
    ctx.update(chart_images={}, branding={**brand.to_dict(), "agt_logo": brand.agt_logo,
                                          "government_logo": brand.government_logo},
               pdf_author=brand.prepared_by, pdf_keywords="t", document_status="Draft", reviewed_by=None,
               progress=None, annex=None)
    return render_html("delivery_processing.html", {**ctx, "snapshot": snap, **L._marking_context(snap)})


def test_html_decision_order_is_deterministic_and_shows_references():
    d = _disclosure()
    a, b = _html(d), _html(_jsonb_roundtrip(d))
    assert a == b
    assert a.index("INSUFFICIENT_EVIDENCE: 1") < a.index("NOT_FOUND_IN_EXTRACT: 1")
    for needle in ("IQ-190fcede", "IQ-f5a8e089", "source effective date not recorded", "Assessor role(s): qalead (no names)",
                   "separate from the historical use"):
        assert needle in a, needle
    assert "qa@example.invalid" not in a


def test_disclosure_carries_stable_identifier_free_references():
    d = _disclosure()
    refs = d["assessment_refs"]
    assert [r["ref"] for r in refs] == ["IQ-190fcede", "IQ-f5a8e089"]                   # stable, chronological
    assert [r["review_linked"] for r in refs] == [True, False] and [r["entity_linked"] for r in refs] == [True, False]
    assert d["entities_linked"] == 1 and d["reviews_linked"] == 1
    assert d["assessor_roles"] == ["qalead"]
    flat = json.dumps(d)
    assert "qa@example.invalid" not in flat and "REV-2026-000983" not in flat   # no assessor name, no review id


def test_source_dates_are_stated_or_marked_not_recorded():
    sn = _disclosure()["snapshots"][0]
    assert sn["received_on"] == "2026-10-08" and sn["source_effective_date"] is None
    text = _csv(_disclosure())
    assert ["Snapshot source received (UTC date)", "2026-10-08"] in list(csv.reader(io.StringIO(text)))
    assert ["Snapshot source effective date", "not recorded"] in list(csv.reader(io.StringIO(text)))


def test_csv_lists_each_assessment_reference_with_linkage_and_role():
    rows = list(csv.reader(io.StringIO(_csv(_disclosure()))))
    refs = [r for r in rows if r and r[0] == "Assessment reference"]
    assert [r[1] for r in refs] == ["IQ-190fcede", "IQ-f5a8e089"]
    assert refs[0][5:] == ["review linked", "entity linked"] and refs[1][5:] == ["no review", "no entity"]
    assert all(r[4] == "role qalead" for r in refs)


def test_current_availability_is_separate_from_historical_use():
    on, off = _disclosure(True), _disclosure(False)
    assert on["lookup_availability"] == "ENABLED" and off["lookup_availability"] == "DISABLED"
    assert "separate from the historical use" in off["lookup_availability_statement"]
    assert off["assessments"] == on["assessments"] == 2                      # switching off never hides recorded use
    assert any("availability" in c for r in csv.reader(io.StringIO(_csv(off))) for c in r)


def test_no_assessments_still_says_not_used_and_adds_no_empty_linkage_rows():
    rows = list(csv.reader(io.StringIO(_csv(aa.disclosure_from_rows(flag_enabled=True, rows=[])))))
    assert ["State", "IQVIA_NOT_USED"] in rows
    assert not any(r and r[0] in ("Assessment reference", "Assessments linked to a registry entity") for r in rows)
