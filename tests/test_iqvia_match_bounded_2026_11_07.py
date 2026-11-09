"""Bounded IQVIA HCO matching (D-09 / DEF-14): keyset paging, per-page
transactions, advisory-lock in-flight guard, capped detail list.

Defect demonstrated on 2026-11-06 in DEV: a full-snapshot match loaded all
154,683 observation rows at once, took the container past its memory limit
(measured 1,048 MB on the 1.75 GB tier) and recycled the app; repeated client
retries each restarted the same full scan. These tests pin the correction:
identical match decisions in bounded pages, resumable with `limit`, refusing
concurrent runs instead of stacking them. All data is synthetic.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import iqvia_match as im
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce import source_matching as sx
from rce_traceability_support import NPI_REGISTERED, SYN, rolled_back_db, seed_entity  # noqa: F401
from test_iqvia_import import HCO_HEADER, _User, write_csv

pytestmark = pytest.mark.regression


async def _approved_snapshot(db, tmp_path, rows, label):
    f = write_csv(tmp_path / f"{label}.csv", HCO_HEADER, rows)
    imported = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-{label}", created_by=SYN)
    qalead = _User("qalead", "qa@example.test")
    return await sx.approve_snapshot(db, imported.snapshot_id, user=qalead,
                                     approval_ref=f"{SYN}-ref-{label}")


async def _nppes_type2(npi):
    return {"enumeration_type": "NPI-2"}


class TestBoundedMatching:
    async def test_paged_run_writes_the_same_decisions_as_one_page(self, rolled_back_db, tmp_path):
        """7 observations, page_size=3 -> 3 pages; decisions identical to the
        pre-paging implementation: 1 auto-approval written, 6 no-identifier
        rows counted, nothing else."""
        db = rolled_back_db
        await seed_entity(db, oid=f"{SYN}.PAGED.1", name=f"{SYN} Paged Target", npi=NPI_REGISTERED)
        rows = [[f"{SYN}-PG-{i}", "", "", f"{SYN} Paged Nothing {i}", "02101"] for i in range(1, 7)]
        rows.append([f"{SYN}-PG-7", NPI_REGISTERED, "", f"{SYN} Paged Target", "02101"])
        approved = await _approved_snapshot(db, tmp_path, rows, "paged")

        summary = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                              nppes_type_lookup=_nppes_type2, page_size=3)
        assert summary.observations_considered == 7
        assert summary.pages == 3
        assert summary.auto_approved == 1
        assert summary.unmatchable_no_identifier == 6
        assert summary.details_truncated is False
        written = (await db.execute(select(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id == approved.id))).scalars().all()
        assert len(written) == 1
        assert written[0].match_status == sm.MATCH_AUTO_APPROVED

    async def test_limited_run_resumes_without_double_writing(self, rolled_back_db, tmp_path):
        """`limit` stops the run early; the next full run skips what was
        already matched (the resumability the memory incident needs)."""
        db = rolled_back_db
        await seed_entity(db, oid=f"{SYN}.PAGED.2", name=f"{SYN} Resume Target", npi=NPI_REGISTERED)
        # keyset order is source_record_key ASC: AA sorts before ZZ
        rows = [[f"{SYN}-RS-AA", NPI_REGISTERED, "", f"{SYN} Resume Target", "02101"],
                [f"{SYN}-RS-ZZ", "", "", f"{SYN} Resume Nothing", "02101"]]
        approved = await _approved_snapshot(db, tmp_path, rows, "resume")

        first = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                            nppes_type_lookup=_nppes_type2,
                                            limit=1, page_size=1)
        assert first.newly_evaluated == 1
        assert first.auto_approved == 1
        # A REPEATED limited run must advance past the already-matched row and
        # evaluate the next one - `limit` counts newly evaluated observations,
        # never skips (CodeRabbit round-1 Major on this change, fixed).
        second = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                             nppes_type_lookup=_nppes_type2,
                                             limit=1, page_size=1)
        assert second.already_matched_skipped == 1
        assert second.newly_evaluated == 1
        assert second.auto_approved == 0
        assert second.unmatchable_no_identifier == 1
        third = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                            nppes_type_lookup=_nppes_type2, page_size=1)
        assert third.already_matched_skipped == 1  # only the written row skips
        assert third.newly_evaluated == 1          # the no-identifier row re-evaluates
        written = (await db.execute(select(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id == approved.id))).scalars().all()
        assert len(written) == 1  # never duplicated across the resumed runs

    async def test_details_list_is_capped_but_counters_stay_exact(
            self, rolled_back_db, tmp_path, monkeypatch):
        db = rolled_back_db
        monkeypatch.setattr(im, "MAX_DETAILS", 2)
        rows = [[f"{SYN}-CAP-{i}", "", "", f"{SYN} Cap Nothing {i}", "02101"] for i in range(1, 6)]
        approved = await _approved_snapshot(db, tmp_path, rows, "cap")
        summary = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN)
        assert summary.observations_considered == 5
        assert summary.unmatchable_no_identifier == 5
        assert len(summary.details) == 0  # no-identifier rows never wrote details
        # rows with decisions DO write details; reuse the cap on a decision-writing run
        monkeypatch.setattr(im, "MAX_DETAILS", 0)
        rows2 = [[f"{SYN}-CAP2-1", "0000000000", "", f"{SYN} Cap Two", "02101"]]
        approved2 = await _approved_snapshot(db, tmp_path, rows2, "cap2")
        s2 = await im.match_hco_snapshot(db, snapshot_id=approved2.id, actor=SYN)
        assert s2.observations_considered == 1
        assert s2.details == []
        assert s2.details_truncated is True

    async def test_concurrent_run_is_refused_not_stacked(self, rolled_back_db, tmp_path):
        """While another connection holds the snapshot's advisory lock, a
        match attempt raises MatchInProgressError instead of starting a
        second full scan. (The run now takes a SESSION-level lock up front
        on a dedicated connection — same lock space, so this xact-lock
        holder still contends — and a refusal can only happen BEFORE any
        page commits, never mid-run; round-3 review finding.)"""
        db = rolled_back_db
        rows = [[f"{SYN}-LOCK-1", "", "", f"{SYN} Lock Nothing", "02101"]]
        approved = await _approved_snapshot(db, tmp_path, rows, "lock")
        sid = approved.id  # plain value: the rollback below expires ORM objects

        from app.core.database import _normalize_url
        engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]),
                                     poolclass=NullPool)
        holder = await engine.connect()
        trans = await holder.begin()
        try:
            got = (await holder.execute(
                text("select pg_try_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"iqvia-hco-match:{sid}"})).scalar()
            assert got is True
            with pytest.raises(im.MatchInProgressError):
                await im.match_hco_snapshot(db, snapshot_id=sid, actor=SYN)
            await db.rollback()  # clear the failed guard transaction
        finally:
            await trans.rollback()
            await holder.close()
            await engine.dispose()

        # with the lock released, the same run proceeds normally
        summary = await im.match_hco_snapshot(db, snapshot_id=sid, actor=SYN)
        assert summary.observations_considered == 1
        assert summary.unmatchable_no_identifier == 1

    async def test_after_key_cursor_resumes_past_rows_that_write_no_match_row(
            self, rolled_back_db, tmp_path):
        """Round-2 review finding, verified: rows that write no match row
        (no identifier, zero candidates) are re-evaluated by every fresh run
        and count against `limit`, so enough of them early in key order starve
        every later limited run. The `after_key`/`next_after_key` cursor is
        the fix: installments continue where the last one stopped. Also pins
        the mid-page semantics -- the cursor is the last PROCESSED key, not
        the page's last key."""
        db = rolled_back_db
        await seed_entity(db, oid=f"{SYN}.CURSOR.1", name=f"{SYN} Cursor Target",
                          npi=NPI_REGISTERED)
        # keyset order: the two no-identifier rows sort BEFORE the matchable one
        rows = [[f"{SYN}-CU-AA1", "", "", f"{SYN} Cursor Nothing 1", "02101"],
                [f"{SYN}-CU-AA2", "", "", f"{SYN} Cursor Nothing 2", "02101"],
                [f"{SYN}-CU-ZZ", NPI_REGISTERED, "", f"{SYN} Cursor Target", "02101"]]
        approved = await _approved_snapshot(db, tmp_path, rows, "cursor")

        # One page of 3, limit 2: the limit breaks MID-PAGE after the two
        # no-identifier rows; the cursor must point at the second of them,
        # never at the page's last key (which would silently skip ZZ).
        first = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                            nppes_type_lookup=_nppes_type2,
                                            limit=2, page_size=3)
        assert first.newly_evaluated == 2
        assert first.auto_approved == 0
        assert first.next_after_key == f"{SYN}-CU-AA2"

        # STARVATION, demonstrated: the same limited call WITHOUT the cursor
        # burns its whole budget on the same two rows again, forever.
        starved = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                              nppes_type_lookup=_nppes_type2,
                                              limit=2, page_size=3)
        assert starved.newly_evaluated == 2
        assert starved.auto_approved == 0
        assert starved.next_after_key == f"{SYN}-CU-AA2"

        # The cursor resumes strictly after the stall point and reaches ZZ.
        resumed = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                              nppes_type_lookup=_nppes_type2,
                                              limit=2, page_size=3,
                                              after_key=first.next_after_key)
        assert resumed.observations_considered == 1
        assert resumed.newly_evaluated == 1
        assert resumed.auto_approved == 1
        assert resumed.next_after_key == f"{SYN}-CU-ZZ"
        written = (await db.execute(select(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id == approved.id))).scalars().all()
        assert len(written) == 1

        # Fresh-full-run contract unchanged: no cursor, no limit -- the match
        # row skips, the no-identifier rows legitimately re-evaluate.
        full = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                           nppes_type_lookup=_nppes_type2)
        assert full.already_matched_skipped == 1
        assert full.unmatchable_no_identifier == 2
        written = (await db.execute(select(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id == approved.id))).scalars().all()
        assert len(written) == 1  # still exactly one -- never duplicated
