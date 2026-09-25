"""The delivery-job list derives status for a page in a bounded number of
queries (2026-09-25 remediation).

On DEV `GET /api/tefca/rce/delivery-jobs` took 134-271 s server-side because
`status_for_job` ran about eight queries per row, several scanning the
delivery's curated records. `status_for_jobs` reads the same evidence with a
constant number of grouped statements. Pinned here:

  (a) the bulk path yields EXACTLY what the per-row derivation yields, for
      every job in a mixed population (clean, held, failed, no intake);
  (b) the statement count of the bulk path does not grow with the page;
  (c) `limit`/`offset` slice the newest-first order and `total` is the
      filtered count;
  (d) per-row vs bulk timings are printed for the record.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime

import pytest
from sqlalchemy import event

from app.tefca_registry.rce import delivery_jobs as jobs
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
from rce_traceability_support import (  # noqa: F401
    NPI_BAD_CHECKSUM, SYN, make_rows, rolled_back_db, seed_intake,
)
from support_delivery_api import cleanup, headers_for, seed_delivery
from test_delivery_runner_events import _run_recoverable

BASE = "/api/tefca/rce"


# ── population ───────────────────────────────────────────────────────────────

async def _failed_job_without_intake(db):
    now = datetime.utcnow()
    job = RceDeliveryJob(
        id=uuid.uuid4(), identity=uuid.uuid4().hex, delivery_label=f"{SYN}-FAILED",
        original_filename="synthetic.csv", storage_path="(synthetic)",
        sha256="0" * 64, file_size_bytes=1, state=RceDeliveryJob.STATE_FAILED,
        stage=RceDeliveryJob.STAGE_PARSING, active_marker=None, registered_by=SYN,
        created_at=now, started_at=now, failed_at=now, attempt_count=1,
        error_reason="worker_stopped_without_reporting", stage_detail={})
    db.add(job)
    await db.commit()
    return job


async def _population(db, n_clean: int):
    """`n_clean` clean deliveries run through the recoverable stages, one
    delivery with an invalid NPI (held records, open HIGH findings, a DQ
    review case), and one FAILED job with no intake."""
    out = []
    for i in range(n_clean):
        rows = make_rows(3, arc=f"9.99.777.7{i}")
        intake_id, job = await seed_intake(db, rows)
        await _run_recoverable(db, job, intake_id, 3)
        await db.refresh(job)
        out.append(job)
    rows = make_rows(2, arc="9.99.777.79", NPI=NPI_BAD_CHECKSUM)
    intake_id, job = await seed_intake(db, rows)
    await _run_recoverable(db, job, intake_id, 2)
    await db.refresh(job)
    out.append(job)
    out.append(await _failed_job_without_intake(db))
    return out


class _StatementCounter:
    def __init__(self, db):
        self.engine = db.sync_session.get_bind().engine
        self.statements = []

    def _listener(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._listener)

    @property
    def count(self) -> int:
        return len(self.statements)


async def _per_row(db, population):
    return [await jobs._status_for_job_reference(db, job) for job in population]


# ── (a) equivalence, (b) bounded statements, (d) timing ─────────────────────

async def test_bulk_status_equals_per_row_status_and_is_bounded(rolled_back_db, capsys):
    db = rolled_back_db
    population = await _population(db, n_clean=4)   # 4 clean + 1 held + 1 failed = 6
    assert len(population) == 6
    # A mixed population, or the comparison proves little.
    codes = {(await jobs._status_for_job_reference(db, j))["processing_outcome"]["code"]
             for j in population}
    assert len(codes) >= 2, codes

    # (a) identical dicts, job by job — outcome, review state AND the inputs.
    await _per_row(db, population[:1])            # warm both paths (statement
    await jobs.status_for_jobs(db, population[:1])  # compilation, codec setup)
    t0 = time.perf_counter()
    reference = await _per_row(db, population)
    per_row_seconds = time.perf_counter() - t0
    t0 = time.perf_counter()
    bulk = await jobs.status_for_jobs(db, population)
    bulk_seconds = time.perf_counter() - t0
    assert len(bulk) == len(reference)
    for job, ref, got in zip(population, reference, bulk):
        assert got["processing_outcome"] == ref["processing_outcome"], job.id
        assert got["review_state"] == ref["review_state"], job.id
        assert got["inputs"] == ref["inputs"], job.id
    # The public single-job entry point is the one-job case of the bulk path.
    single = await jobs.status_for_job(db, population[0])
    assert single == reference[0]

    # (b) statements: per-row grows with n, bulk does not.
    with _StatementCounter(db) as c_ref_2:
        await _per_row(db, population[:2])
    with _StatementCounter(db) as c_ref_6:
        await _per_row(db, population)
    with _StatementCounter(db) as c_bulk_2:
        await jobs.status_for_jobs(db, population[:2])
    with _StatementCounter(db) as c_bulk_6:
        await jobs.status_for_jobs(db, population)
    assert c_ref_6.count > c_ref_2.count, "the per-row oracle must scale with n"
    assert c_bulk_6.count <= 9, c_bulk_6.statements
    assert abs(c_bulk_6.count - c_bulk_2.count) <= 1, (c_bulk_2.count, c_bulk_6.count)
    assert c_bulk_6.count < c_ref_6.count

    # (d) for the record.
    with capsys.disabled():
        print(f"\n[list-perf] n=6 jobs: per-row {c_ref_6.count} statements in "
              f"{per_row_seconds * 1000:.0f} ms; bulk {c_bulk_6.count} statements in "
              f"{bulk_seconds * 1000:.0f} ms; n=2: per-row {c_ref_2.count}, "
              f"bulk {c_bulk_2.count} statements")


async def test_bulk_status_of_an_empty_page_runs_no_query(rolled_back_db):
    with _StatementCounter(rolled_back_db) as c:
        assert await jobs.status_for_jobs(rolled_back_db, []) == []
    assert c.count == 0


# ── (c) pagination ───────────────────────────────────────────────────────────

async def test_list_jobs_offset_slices_the_newest_first_order(rolled_back_db):
    db = rolled_back_db
    seeded = []
    for i in range(5):
        job = await _failed_job_without_intake(db)
        job.created_at = datetime(2020, 1, 1, 0, 0, i)   # a strict, known order
        seeded.append(job)
    await db.commit()
    everything = await jobs.list_jobs(db, limit=200, state="FAILED")
    ids = [j.id for j in everything]
    assert len(ids) == len(set(ids))
    for offset, limit in ((0, 2), (2, 2), (4, 2), (1, 3), (0, 200)):
        page = await jobs.list_jobs(db, limit=limit, state="FAILED", offset=offset)
        assert [j.id for j in page] == ids[offset:offset + limit], (offset, limit)
    # Beyond the end is empty, not an error.
    assert await jobs.list_jobs(db, limit=2, state="FAILED", offset=len(ids) + 10) == []


@pytest.fixture(scope="module")
def three_failed_jobs():
    seeded = [seed_delivery(state="FAILED", with_intake=False) for _ in range(3)]
    yield seeded
    cleanup()


def test_list_endpoint_pages_and_reports_the_filtered_total(client, three_failed_jobs):
    full = client.get(f"{BASE}/delivery-jobs?state=FAILED&limit=200",
                      headers=headers_for("viewer"))
    assert full.status_code == 200, full.text
    body = full.json()
    assert set(body) >= {"items", "count", "total", "limit", "offset"}
    assert body["offset"] == 0 and body["limit"] == 200
    assert body["total"] >= 3
    ids = [item["job_id"] for item in body["items"]]
    assert {d["job_id"] for d in three_failed_jobs} <= set(ids)
    assert all(item["state"] == "FAILED" for item in body["items"])
    assert all(item["processing_outcome"]["code"] == "FAILED" for item in body["items"])
    if body["total"] <= 200:
        assert body["count"] == body["total"]

    first = client.get(f"{BASE}/delivery-jobs?state=FAILED&limit=2&offset=0",
                       headers=headers_for("viewer")).json()
    second = client.get(f"{BASE}/delivery-jobs?state=FAILED&limit=2&offset=2",
                        headers=headers_for("viewer")).json()
    assert first["count"] == 2 and first["offset"] == 0 and first["limit"] == 2
    assert [i["job_id"] for i in first["items"]] == ids[0:2]
    assert [i["job_id"] for i in second["items"]] == ids[2:4]
    assert second["offset"] == 2
    assert first["total"] == second["total"] == body["total"]

    # The default page is 20, and the validators hold.
    default = client.get(f"{BASE}/delivery-jobs", headers=headers_for("viewer")).json()
    assert default["limit"] == 20 and default["offset"] == 0
    assert client.get(f"{BASE}/delivery-jobs?offset=-1",
                      headers=headers_for("viewer")).status_code == 422
    assert client.get(f"{BASE}/delivery-jobs?limit=0",
                      headers=headers_for("viewer")).status_code == 422
