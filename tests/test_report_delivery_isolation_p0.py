"""QA-034/035/036/033 — cross-delivery report leakage, fail-closed.

The defect on DEV (2026-09-19, job 31fba38d…): the 50-record delivery's
Reports tab listed `DA-ARC-2026-001`, a registry-wide report generated on
2026-08-24, and `Download CSV` served it. Mechanism, traced in code:

  1. `next_report_id` swallowed its query error and answered `…-001`;
  2. `store_report` swallowed the UNIQUE violation and returned None;
  3. `record_report_generation` linked the delivery to the colliding id;
  4. the download routes authorised by report id only.

Every step now fails closed, and this file pins each one against a real
database with two independently-seeded synthetic deliveries.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    SYN, artifact_root, rolled_back_db, seed_delivery)


async def _generate(db, report_type="delivery_processing", **params):
    from app.reports.generator import generate_report

    return await generate_report(db, report_type=report_type, persist=True,
                                 query_parameters=params,
                                 generated_by="analyst@synthetic.invalid")


# ── 1. id allocation ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_next_report_id_continues_from_the_highest_issued_number(rolled_back_db):
    from datetime import datetime, timezone

    from app.reports.data.report_snapshot import next_report_id
    from app.tefca_registry import models as reg

    db = rolled_back_db
    year = datetime.now(timezone.utc).year
    db.add(reg.ReviewReport(id=uuid.uuid4(), report_id=f"DA-ARC-{year}-940",
                            report_type="verification", report_data={}, report_html="<p/>"))
    await db.flush()
    # A row count would say "1 existing -> 002" and collide with 940 later;
    # the allocator must continue from the maximum.
    assert await next_report_id(db) == f"DA-ARC-{year}-941"


@pytest.mark.asyncio
async def test_next_report_id_raises_instead_of_restarting_at_001():
    from app.reports.data.report_snapshot import ReportIdAllocationError, next_report_id

    class _BrokenSession:
        async def execute(self, *a, **k):
            raise RuntimeError("current transaction is aborted")

    with pytest.raises(ReportIdAllocationError):
        await next_report_id(_BrokenSession())


# ── 2. storage is fail-closed for a scoped report ─────────────────────────────

@pytest.mark.asyncio
async def test_strict_store_raises_on_a_colliding_report_id(rolled_back_db):
    from app.reports.data.report_snapshot import (ReportSnapshot, ReportStorageError,
                                                  store_report)
    from app.tefca_registry import models as reg

    db = rolled_back_db
    rid = f"DA-ARC-9999-{uuid.uuid4().hex[:3]}"
    db.add(reg.ReviewReport(id=uuid.uuid4(), report_id=rid, report_type="verification",
                            report_data={}, report_html="<p/>"))
    await db.flush()
    snap = ReportSnapshot(report_id=rid, report_type="data_quality",
                          generation_timestamp="2026-09-20T00:00:00+00:00")
    with pytest.raises(ReportStorageError):
        await store_report(db, snap, {"delivery": {"job_id": "x"}}, "<p/>", strict=True)


# ── 3. links and listings are verified against the stored report ──────────────

@pytest.mark.asyncio
async def test_a_poisoned_link_never_surfaces_in_the_other_deliverys_listing(
        rolled_back_db, artifact_root):
    from app.reports.data.delivery_report_links import (DELIVERY_MISMATCH, links_for_job,
                                                        quarantined_links_for_job)
    from app.tefca_registry.rce import traceability_models as tm

    db = rolled_back_db
    a = await seed_delivery(db, label=f"{SYN} A")
    b = await seed_delivery(db, label=f"{SYN} B")
    result_a = await _generate(db, job_id=str(a["job_id"]))
    report_a = result_a["report_id"]
    assert result_a["stored_id"] and result_a["delivery_link"]["written"] is True

    # The defect's residue: a link row for delivery B pointing at A's report.
    db.add(tm.RceDeliveryReportLink(
        job_id=b["job_id"], intake_id=b["intake_id"], snapshot_id=b["snapshot_ids"][-1],
        report_id=report_a, report_type="delivery_processing", artifact_id=None,
        template_version="1", generated_by="poison", correlation_id="poison"))
    await db.flush()

    listed_b = await links_for_job(db, b["job_id"])
    assert all(link["report_id"] != report_a for link in listed_b), listed_b
    quarantined = await quarantined_links_for_job(db, b["job_id"])
    assert [q["report_id"] for q in quarantined] == [report_a]
    assert quarantined[0]["code"] == DELIVERY_MISMATCH
    assert quarantined[0]["stored_job_id"] == str(a["job_id"])

    # Delivery A still lists its own report.
    assert any(link["report_id"] == report_a for link in await links_for_job(db, a["job_id"]))


@pytest.mark.asyncio
async def test_delivery_job_detail_reports_block_is_verified_and_lists_artifacts(
        rolled_back_db, artifact_root):
    from app.tefca_registry.rce.delivery_routes import _reports
    from app.tefca_registry.rce import traceability_models as tm

    db = rolled_back_db
    a = await seed_delivery(db, label=f"{SYN} A")
    b = await seed_delivery(db, label=f"{SYN} B")
    result_a = await _generate(db, job_id=str(a["job_id"]))
    db.add(tm.RceDeliveryReportLink(
        job_id=b["job_id"], intake_id=b["intake_id"], snapshot_id=b["snapshot_ids"][-1],
        report_id=result_a["report_id"], report_type="delivery_processing",
        template_version="1", generated_by="poison", correlation_id="poison"))
    await db.flush()

    assert await _reports(db, b["job_id"]) == []
    own = await _reports(db, a["job_id"])
    assert [r["report_id"] for r in own] == [result_a["report_id"]]
    entry = own[0]
    assert entry["delivery_verified"] is True
    # QA-033: artifacts carry format, filename, size and checksum — not bare ids.
    formats = {art["format"] for art in entry["artifacts"]}
    assert {"html", "csv"} <= formats
    for art in entry["artifacts"]:
        assert art["filename"].endswith(f".{art['format']}")
        assert art["sha256"] and art["size_bytes"] and art["status"] == "registered"


@pytest.mark.asyncio
async def test_record_report_generation_refuses_when_the_id_resolves_to_another_row(
        rolled_back_db):
    from app.models.database import AuditLog
    from app.reports.data.delivery_report_links import (IDENTITY_MISMATCH,
                                                        record_report_generation)
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import traceability_models as tm

    db = rolled_back_db
    a = await seed_delivery(db, label=f"{SYN} A")
    older = uuid.uuid4()
    rid = f"DA-ARC-9998-{uuid.uuid4().hex[:3]}"
    db.add(reg.ReviewReport(id=older, report_id=rid, report_type="verification",
                            report_data={"dataset": {"delivery": None}}, report_html="<p/>"))
    await db.flush()
    dataset = {"delivery": {"job_id": str(a["job_id"]), "intake_id": str(a["intake_id"])},
               "snapshot_id": str(a["snapshot_ids"][-1])}
    out = await record_report_generation(
        db, report_id=rid, report_type="data_quality", dataset=dataset,
        template_version="1", generated_by="x", stored_id=str(uuid.uuid4()))
    assert out["refusal"] == IDENTITY_MISMATCH and out["link_ids"] == []
    links = (await db.execute(select(tm.RceDeliveryReportLink)
                              .where(tm.RceDeliveryReportLink.report_id == rid))).scalars().all()
    assert links == []
    audit = (await db.execute(select(AuditLog).where(AuditLog.resource_id == rid))).scalar_one()
    assert audit.outcome == "refused" and audit.details["refusal"] == IDENTITY_MISMATCH


# ── 4. download path is delivery-scoped when a job is named ───────────────────

@pytest.mark.asyncio
async def test_download_with_the_other_deliverys_job_is_refused_409(rolled_back_db, artifact_root):
    from app.reports.data.delivery_report_links import DELIVERY_MISMATCH
    from app.reports.routes import _stored

    db = rolled_back_db
    a = await seed_delivery(db, label=f"{SYN} A")
    b = await seed_delivery(db, label=f"{SYN} B")
    result_a = await _generate(db, job_id=str(a["job_id"]))

    row = await _stored(db, result_a["report_id"], str(a["job_id"]))
    assert row.report_id == result_a["report_id"]
    with pytest.raises(HTTPException) as exc:
        await _stored(db, result_a["report_id"], str(b["job_id"]))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == DELIVERY_MISMATCH


@pytest.mark.asyncio
async def test_a_global_report_is_refused_in_any_delivery_context(rolled_back_db):
    from app.reports.routes import _stored
    from app.tefca_registry import models as reg

    db = rolled_back_db
    a = await seed_delivery(db, label=f"{SYN} A")
    rid = f"DA-ARC-9997-{uuid.uuid4().hex[:3]}"
    db.add(reg.ReviewReport(id=uuid.uuid4(), report_id=rid, report_type="verification",
                            report_data={"dataset": {"delivery": None}}, report_html="<p/>"))
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await _stored(db, rid, str(a["job_id"]))
    assert exc.value.status_code == 409


# ── 5. provenance and reconciliation ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_csv_and_stored_dataset_carry_full_delivery_provenance(rolled_back_db, artifact_root):
    from app.core import request_context

    db = rolled_back_db
    a = await seed_delivery(db, label=f"{SYN} A")
    result = await _generate(db, report_type="data_quality", job_id=str(a["job_id"]))
    prov = result["dataset"]["provenance"]
    assert prov["scope_type"] == "DELIVERY"
    assert prov["delivery_job_id"] == str(a["job_id"])
    assert prov["intake_id"] == str(a["intake_id"])
    assert prov["reconciliation_snapshot_id"] == str(a["snapshot_ids"][-1])
    assert prov["build_sha"] == request_context.build_sha()
    assert prov["source_file_sha256"] and len(prov["source_file_sha256"]) == 64
    header = result["csv"].split("\r\n")[:14]
    joined = "\n".join(header)
    for needle in (f"# Delivery job: {a['job_id']}", f"# Intake: {a['intake_id']}",
                   f"# Reconciliation snapshot: {a['snapshot_ids'][-1]}",
                   f"# Build SHA: {prov['build_sha']}",
                   f"# Source file SHA-256: {prov['source_file_sha256']}"):
        assert needle in joined, joined
    assert "# Review cycle: All records" not in joined


def test_reconcile_dataset_refuses_a_section_larger_than_the_population():
    from app.reports.data.report_reconciliation import is_scoped, reconcile_dataset

    leaky = {"delivery": {"job_id": "j"},
             "scope": {"records_received": 50, "records_evaluated": 40},
             "buckets": {"total": 40},
             "entity_status": {"total": 22309},
             "coverage": {"sources": [{"source": "NPPES", "total": 21000}]},
             "qhins": {"qhins": [{"qhin": "X", "total": 212}]}}
    problems = reconcile_dataset(leaky)
    assert is_scoped(leaky)
    assert any("entity_status" in p for p in problems)
    assert any("coverage[NPPES]" in p for p in problems)
    assert any("QHIN comparison" in p for p in problems)

    clean = {"delivery": {"job_id": "j"},
             "scope": {"records_received": 50, "records_evaluated": 40},
             "buckets": {"total": 40}, "entity_status": {"total": 40},
             "coverage": {"sources": [{"source": "NPPES", "total": 40}]},
             "qhins": {"qhins": [{"qhin": "X", "total": 40}]}}
    assert reconcile_dataset(clean) == []
    assert not is_scoped({"scope": {"records_received": 108, "records_evaluated": 212}})


@pytest.mark.asyncio
async def test_generator_refuses_an_unreconciled_scoped_dataset(rolled_back_db, monkeypatch):
    """Belt and braces: even if a data service regressed to registry-wide
    sections, the generator refuses to issue a SCOPED document."""
    from app.reports import generator as gen
    from app.reports.data import report_data_service as rds
    from app.tefca_registry import models as reg

    db = rolled_back_db
    cycle = reg.ReviewCycle(id=uuid.uuid4(), cycle_type="retrospective", sample_id=None)
    db.add(cycle)
    await db.flush()

    async def _leaky(self, review_cycle_id=None):
        return {"counts": {"not_verified": 21209}, "total": 21209, "percentages": {},
                "insufficient_data": False}

    monkeypatch.setattr(rds.ReportDataService, "get_entity_status_breakdown", _leaky)
    with pytest.raises(gen.ReportGenerationError) as exc:
        await gen.generate_report(db, report_type="verification", persist=False,
                                  review_cycle_id=str(cycle.id), generated_by="x")
    assert "REPORT_SCOPE_UNRECONCILED" in str(exc.value)


# ── 6. idempotent generation (QA-031) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_repeated_idempotency_key_replays_the_same_report(rolled_back_db, artifact_root):
    from app.core import request_context
    from app.reports.routes import _replay_for_key

    db = rolled_back_db
    a = await seed_delivery(db, label=f"{SYN} A")
    key = f"rep-{uuid.uuid4()}"
    assert await _replay_for_key(db, key, None) is None
    with request_context.bind(idempotency_key=key):
        first = await _generate(db, job_id=str(a["job_id"]))
    replay = await _replay_for_key(db, key, None)
    assert replay is not None and replay["replayed"] is True
    assert replay["report_id"] == first["report_id"]
    assert replay["stored_id"] == first["stored_id"]
    assert replay["delivery_link"]["job_id"] == str(a["job_id"])
    # A different key is a different generation.
    assert await _replay_for_key(db, f"rep-{uuid.uuid4()}", None) is None
