"""GET /api/tefca/rce/iqvia/source-status -- inventory status of the IQVIA source.

SOURCE INVENTORY, NOT COVERAGE: the status is derived from persisted snapshot rows and
the consumption flag, reads only, and never appears in per-delivery verification coverage.
The derivation is a pure function; the route reuses the snapshot-discovery gate and its
faked page query (no database).
"""
from __future__ import annotations

import pytest

from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce import verification_coverage as vc

# Fixtures and the synthetic-row builder are the discovery test's own.
from test_iqvia_snapshot_list_2026_10_12 import _snap, as_role, fake_page  # noqa: F401

pytestmark = pytest.mark.regression

PATH = "/api/tefca/rce/iqvia/source-status"


def _approved_successor(predecessor, **over):
    return _snap(supersedes_snapshot_id=predecessor.id, status=sm.SNAPSHOT_APPROVED,
                 approved_by="qalead@test.local", **over)


def test_no_snapshot_is_not_staged_never_hard_coded_staged():
    out = routes.derive_iqvia_source_status([], total=0, consumption_enabled=False)
    assert out["state"] == "NOT_STAGED" and out["latest_snapshot"] is None
    assert "Staged" not in out["display_text"]


def test_staged_and_unapproved_reads_staged_approval_pending():
    row = _snap()  # the DEV shape: PENDING, import completed, flag off
    out = routes.derive_iqvia_source_status([(row, None)], total=1, consumption_enabled=False)
    assert out["state"] == "STAGED_APPROVAL_PENDING"
    assert out["display_text"] == "Staged—approval/consumption pending"
    assert out["counts"]["staged_complete"] == 1 and out["counts"]["approved"] == 0
    assert out["discovery"] == {"api": "/api/tefca/rce/iqvia/snapshots", "ui": "/tefca-arc/iqvia/"}


def test_import_not_completed_is_not_called_staged():
    row = _snap(metadata_={"import_status": "staging"})
    out = routes.derive_iqvia_source_status([(row, None)], total=1, consumption_enabled=False)
    assert out["state"] == "IMPORT_INCOMPLETE"


def test_approval_and_consumption_are_separate_states():
    row = _snap()
    pair = [(row, _approved_successor(row))]
    off = routes.derive_iqvia_source_status(pair, total=1, consumption_enabled=False)
    on = routes.derive_iqvia_source_status(pair, total=1, consumption_enabled=True)
    assert off["state"] == "APPROVED_NOT_CONSUMED"
    assert on["state"] == "APPROVED_CONSUMPTION_ENABLED"


def test_flag_on_without_an_approved_affiliation_snapshot_is_not_consumed():
    hco = _snap(source_system=sm.SOURCE_IQVIA_HCO)
    out = routes.derive_iqvia_source_status([(hco, _approved_successor(hco, source_system=sm.SOURCE_IQVIA_HCO))],
                                            total=1, consumption_enabled=True)
    assert out["state"] == "APPROVED_NOT_CONSUMED"
    pending = routes.derive_iqvia_source_status([(_snap(), None)], total=1, consumption_enabled=True)
    assert pending["state"] == "STAGED_APPROVAL_PENDING"


def test_inventory_status_never_touches_delivery_coverage():
    out = routes.derive_iqvia_source_status([(_snap(), None)], total=1, consumption_enabled=False)
    assert out["affects_delivery_coverage"] is False
    assert not any("iqvia" in k.lower() for k in vc.STATE_SOURCE_KEYS)
    assert not any("iqvia" in k.lower() for k in vc.SOURCES)


@pytest.mark.parametrize("role", ["contributor", "viewer"])
def test_below_reviewer_is_403(as_role, fake_page, role):
    assert as_role(role)(PATH).status_code == 403
    assert fake_page[0] == []


def test_licensed_flag_off_is_403(as_role, fake_page, monkeypatch):
    from app.core.config import settings
    get = as_role("reviewer")
    monkeypatch.setattr(settings, "ENABLE_IQVIA_SOURCES", False, raising=False)
    assert get(PATH).status_code == 403
    assert fake_page[0] == []


def test_reviewer_reads_status_without_writing(as_role, fake_page):
    calls, payload = fake_page
    row = _snap()
    payload["total"], payload["page"] = 1, [(row, None)]
    r = as_role("reviewer")(PATH)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["state"] == "STAGED_APPROVAL_PENDING" and body["source_key"] == "iqvia"
    assert calls[0]["include_successors"] is False
