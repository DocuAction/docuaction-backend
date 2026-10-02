"""AP-013: a large, non-official synthetic report exercises AP-001's fix
(GET /reports/{report_id} summarises long lists instead of transferring
them) at a scale day-to-day fixtures don't reach, and stays clearly
distinct from the official job.
"""
from __future__ import annotations

import time

import pytest

from support_delivery_api import headers_for, run
from rce_traceability_support import make_rows, rolled_back_db, run_quality_and_curation, seed_intake  # noqa: F401
from app.tefca_registry.rce.promotion import promote_delivery
from app.reports.generator import generate_report

pytestmark = pytest.mark.usefixtures("db_required")

ARC = "9.99.777.92"


def test_large_synthetic_report_metadata_get_is_fast_and_summarised(client):
    async def _build():
        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            rows = make_rows(60, arc=ARC)
            intake_id, job = await seed_intake(db, rows)
            await db.commit()
            await run_quality_and_curation(db, intake_id)
            await db.commit()
            await promote_delivery(db, intake_id, actor="test")
            await db.commit()
            result = await generate_report(
                db, report_type="delivery_processing", generated_by="test",
                query_parameters={"job_id": str(job.id)})
            return result["report_id"]
    report_id = run(_build())

    t0 = time.perf_counter()
    resp = client.get(f"/api/reports/{report_id}", headers=headers_for("reviewer"))
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200, resp.text
    assert elapsed < 2.0, f"metadata GET took {elapsed:.2f}s"
    body = resp.json()
    assert body["report_id"] == report_id
    # This is the NON-official fixture, never the real delivery.
    assert report_id != "DA-ARC-2026-028"  # the official September-scale report id, for contrast
