"""The idle-sources disclosure must not render the legacy connector-
availability key `pecos` as bare "PECOS" (2026-10-28).

`pecos` here is the B1-B4 classifier's legacy NPPES-reachability key (see
app/Tefca/source_registry.py), not the genuine CMS PPEF Enrollment
connector (CMS_PPEF_ENROLLMENT). A reader seeing "PECOS: 0 of N attempted"
in a delivery report's limitations section could reasonably read that as a
claim about CMS enrollment screening never having run -- which is not what
this disclosure is actually about. The label must say "legacy NPPES proxy"
so it cannot be mistaken for the genuine connector.
"""
from __future__ import annotations

from app.reports.data.delivery_processing_data import DeliveryProcessingDataService


def test_idle_pecos_disclosure_uses_the_honest_legacy_label():
    svc = DeliveryProcessingDataService(db=None)
    svc._note_coverage_limits({
        "sources": [
            {"name": "nppes", "attempted": 5, "eligible": 5},
            {"name": "pecos", "attempted": 0, "eligible": 5},
            {"name": "leie", "attempted": 5, "eligible": 5},
            {"name": "sam", "attempted": 5, "eligible": 5},
        ],
    })
    assert len(svc.limitations) == 1
    note = svc.limitations[0]
    assert "PECOS (legacy NPPES proxy)" in note
    # The bare word must never appear on its own -- only as part of the
    # honest, longer label.
    assert "PECOS," not in note and "PECOS:" not in note and "PECOS)" not in note.replace(
        "PECOS (legacy NPPES proxy)", "")


def test_all_sources_idle_still_uses_the_honest_legacy_label():
    svc = DeliveryProcessingDataService(db=None)
    svc._note_coverage_limits({
        "sources": [
            {"name": "nppes", "attempted": 0, "eligible": 5},
            {"name": "pecos", "attempted": 0, "eligible": 5},
            {"name": "leie", "attempted": 0, "eligible": 5},
            {"name": "sam", "attempted": 0, "eligible": 5},
        ],
    })
    note = svc.limitations[0]
    assert "PECOS (legacy NPPES proxy)" in note
