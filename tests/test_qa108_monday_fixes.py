"""QA108 Monday-remediation fixes — focused proofs.

Covers, against real PostgreSQL where a database is involved:
1. Delivery detail defers the audit/lineage blocks by default (their tabs own
   dedicated endpoints; inline they cost a full-table ILIKE scan), reports the
   deferral honestly, computes them on ?include=, and rejects unknown blocks.
2. The reconciliation GET serves the PERSISTED snapshot in the live shape,
   falls back to live computation when no snapshot exists, and gates
   ?recompute=true at qalead.
3. The stored-file reason names the FILE, never the container path.
4. A PDF render that exceeds its budget answers a sanitized 503, not a hung
   connection.
"""
from __future__ import annotations

import time

import pytest

from support_delivery_api import (  # noqa: E402 — module handles its own DB skip
    _database_available,
    headers_for,
    run,
    seed_delivery,
)

pytestmark_db = pytest.mark.skipif(
    not _database_available(),
    reason="No database reachable at DATABASE_URL. This test exercises a "
           "database-backed path; skipping rather than reporting a false failure.")


# ── 1. detail deferral (PostgreSQL) ──────────────────────────────────────────

@pytestmark_db
def test_detail_defers_audit_and_lineage_by_default_and_computes_on_include(client):
    seeded = seed_delivery()
    job_id = seeded["job_ids"][0]
    h = headers_for("reviewer")

    r = client.get(f"/api/tefca/rce/delivery-jobs/{job_id}/detail", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["blocks_deferred"]) == ["audit", "lineage", "records"]
    assert body["audit"] is None and body["lineage"] is None
    # Deferred is NOT unavailable: the tabs' own endpoints serve the data.
    assert body["availability"]["audit"] not in ("unavailable", "requires_role:reviewer")
    assert body["availability"]["lineage"] not in ("unavailable", "requires_role:reviewer")
    assert body["availability"]["records"] not in ("unavailable", "requires_role:reviewer")
    # exceptions stays inline (its tab header reads it).
    assert body["exceptions"] is not None
    assert "timeline" in body, "timeline must stay inline (empty for this seed: no stage events)"

    r2 = client.get(f"/api/tefca/rce/delivery-jobs/{job_id}/detail?include=audit,lineage,records",
                    headers=h)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["blocks_deferred"] == []
    assert body2["audit"] is not None and "disposition_events" in body2["audit"]
    assert body2["lineage"] is not None and "entity_versions" in body2["lineage"]
    assert body2["records"] is not None and "by_parse_status" in body2["records"]

    r3 = client.get(f"/api/tefca/rce/delivery-jobs/{job_id}/detail?include=everything",
                    headers=h)
    assert r3.status_code == 422


@pytestmark_db
def test_detail_viewer_gating_is_unchanged_by_deferral(client):
    seeded = seed_delivery()
    job_id = seeded["job_ids"][0]
    r = client.get(f"/api/tefca/rce/delivery-jobs/{job_id}/detail",
                   headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    for block in ("records", "exceptions", "lineage", "audit"):
        assert body["availability"][block] == "requires_role:reviewer"
        assert body[block] is None


# ── 2. reconciliation snapshot serving (PostgreSQL) ──────────────────────────

@pytestmark_db
def test_reconciliation_get_serves_persisted_snapshot_and_gates_recompute(client):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.reconciliation import persist_snapshot, reconcile_delivery
    seeded = seed_delivery()
    intake_id = seeded["intake_id"]
    job_id = seeded["job_ids"][0]
    trigger = "MANUAL"

    async def _persist():
        async with async_session_maker() as db:
            live = await reconcile_delivery(db, intake_id)
            snap = await persist_snapshot(db, intake_id, live, job_id=job_id,
                                          actor="qa108-test", trigger=trigger)
            return live, str(snap.id)

    live, snap_id = run(_persist())

    r = client.get(f"/api/tefca/rce/deliveries/{intake_id}/reconciliation",
                   headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "persisted_snapshot"
    assert body["snapshot"]["id"] == snap_id
    # Same verdict, same equation, same checks as the live run it persisted.
    assert body["passed"] == live["passed"]
    assert body["equation"]["received"] == live["equation"]["received"]
    assert body["equation"]["accounted"] == live["equation"]["accounted"]
    assert [c["check"] for c in body["checks"]] == [c["check"] for c in live["checks"]]
    assert body["populations"] == live["populations"]
    # No container path leaks through the snapshot either.
    assert "/app/uploads" not in r.text

    # recompute is an explicit, gated operation.
    r_deny = client.get(
        f"/api/tefca/rce/deliveries/{intake_id}/reconciliation?recompute=true",
        headers=headers_for("viewer"))
    assert r_deny.status_code == 403
    r_live = client.get(
        f"/api/tefca/rce/deliveries/{intake_id}/reconciliation?recompute=true",
        headers=headers_for("qalead"))
    assert r_live.status_code == 200, r_live.text
    assert r_live.json()["source"] == "live_recompute"
    assert r_live.json()["passed"] == live["passed"]


@pytestmark_db
def test_reconciliation_get_without_snapshot_falls_back_to_live(client):
    seeded = seed_delivery()   # seed_delivery persists no reconciliation snapshot
    intake_id = seeded["intake_id"]
    r = client.get(f"/api/tefca/rce/deliveries/{intake_id}/reconciliation",
                   headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "live_compute_no_snapshot"


# ── 3. stored-file reason names the file, never the path ─────────────────────

async def test_stored_file_reason_carries_no_directory(monkeypatch):
    from app.tefca_registry.rce import repository

    class _Intake:
        storage_path = "/app/uploads/rce_deliveries/secret-dir/original.csv"

    async def _fake_get_intake(db, intake_id):
        return _Intake()

    monkeypatch.setattr(repository, "get_intake", _fake_get_intake)
    out = await repository.verify_stored_file(None, "any")
    assert out["checked"] is False
    assert "original.csv" in out["reason"]
    for leak in ("/app", "uploads", "secret-dir", "\\"):
        assert leak not in out["reason"], out["reason"]


# ── 4. PDF render budget → sanitized 503 ─────────────────────────────────────

async def test_pdf_render_budget_answers_503_not_a_hang(monkeypatch):
    from fastapi import HTTPException

    from app.reports import routes as rr
    from app.reports.engine import pdf_engine

    monkeypatch.setattr(pdf_engine, "pdf_available", lambda: True)

    def _slow_render(html, title=None):
        time.sleep(2)
        return b"%PDF-late"

    monkeypatch.setattr(pdf_engine, "render_pdf", _slow_render)
    monkeypatch.setattr(rr, "PDF_RENDER_BUDGET_SECONDS", 0.2)

    with pytest.raises(HTTPException) as exc:
        await rr._pdf_response("<html></html>", "QA108-TEST")
    assert exc.value.status_code == 503
    detail = str(exc.value.detail)
    assert "budget" in detail
    for leak in ("Traceback", "asyncio", "thread"):
        assert leak not in detail


@pytestmark_db
def test_detail_dispositions_come_from_the_persisted_snapshot_when_one_exists(client):
    """QA108-002 second pass: with a persisted snapshot the detail's
    disposition block is DERIVED from it (the pipeline persists a new snapshot
    on every disposition, so the latest snapshot IS current) and must equal
    what the live aggregation says."""
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import dispositions as disp
    from app.tefca_registry.rce.reconciliation import persist_snapshot, reconcile_delivery

    seeded = seed_delivery()
    intake_id = seeded["intake_id"]
    job_id = seeded["job_ids"][0]

    async def _persist_and_live():
        async with async_session_maker() as db:
            live = await reconcile_delivery(db, intake_id)
            await persist_snapshot(db, intake_id, live, job_id=job_id,
                                   actor="qa108-test", trigger="MANUAL")
            counts = await disp.counts_for_intake(db, intake_id)
            return counts

    live_counts = run(_persist_and_live())

    r = client.get(f"/api/tefca/rce/delivery-jobs/{job_id}/detail",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    block = r.json()["dispositions"]
    assert block["source"] == "persisted_snapshot"
    for key in ("created", "updated", "matched_unchanged", "held", "rejected",
                "missing_key", "excluded"):
        assert block[key] == live_counts[key.upper()], key
    assert block["total"] == live_counts["total"]
    assert block["equation"]["accounted"] == live_counts["total"]
