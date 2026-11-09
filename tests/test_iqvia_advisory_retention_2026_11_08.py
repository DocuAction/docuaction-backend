"""IQVIA advisory comparison outcome RETENTION (D-10): bulk, append-only, opt-in.

Invariants under test: the default (persist off) path writes nothing anywhere;
persisted mode writes exactly one attributable row per compared entity;
re-sending a page for the same run is idempotent (no duplicates, no error);
rows are append-only (trigger rejects UPDATE/DELETE); the advisory boundary is
pinned by CHECK constraints (affects_verification can never be true, the
determination can never claim more than AUTOMATED_CANDIDATE_NOT_CONFIRMED).
"""
from __future__ import annotations

import csv
import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.tefca_registry.rce import iqvia_advisory_comparison as advc
from app.tefca_registry.rce import iqvia_advisory_retention as ret
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import snapshot_models as smod
from rce_traceability_support import (  # noqa: F401
    NPI_REGISTERED, NPI_VALID_OTHER, SYN, rolled_back_db, seed_entity)

pytestmark = pytest.mark.regression

AFFIL_HEADER = ["HCP_HCE_ID", "HCO_HCE_ID", "AFFL_TYP_ID", "TITL_TYP_ID",
                "ORG_NPI", "ORG_CCN_ID", "NPI",
                "ADDR_LN_1_TXT", "CITY_NM", "ST_CD", "ZIP5_CD"]


def _write(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(header)
        w.writerows(rows)
    return path


async def _stage_affil(db, tmp_path):
    rows = [
        [f"{SYN}-P1", f"{SYN}-O1", "A1", "", NPI_REGISTERED, "", "",
         "1 Any St", "Testville", "MA", "02101"],
        [f"{SYN}-P2", f"{SYN}-O2", "A1", "", NPI_VALID_OTHER, "", "",
         "9 Far Rd", "Elsewhere", "TX", "99999"],
    ]
    f = _write(tmp_path / "affil_ret.csv", AFFIL_HEADER, rows)
    summary = await ii.import_affiliation_csv(db, file_path=f,
                                              label=f"{SYN}-retention", created_by=SYN)
    assert summary.rows_rejected == 0 and summary.completed
    return summary.snapshot_id


async def _outcome_rows(db):
    return (await db.execute(select(ret.IqviaAdvisoryComparisonOutcome))).scalars().all()


async def _outcome_count(db) -> int:
    return (await db.execute(select(func.count()).select_from(
        ret.IqviaAdvisoryComparisonOutcome))).scalar_one()


async def _compare(db, sid, entity_ids):
    return await advc.advisory_comparison_batch(
        db, snapshot_id=sid, entity_ids=entity_ids, diagnostic_allow_pending=True)


# ── default OFF: exactly today's behaviour ────────────────────────────────────

def test_route_request_persist_defaults_off_and_requires_run_id_shape():
    from app.tefca_registry.rce.iqvia_routes import AdvisoryComparisonRequest

    req = AdvisoryComparisonRequest(entity_ids=[uuid.uuid4()])
    assert req.persist is False and req.run_id is None
    with pytest.raises(ValueError):
        AdvisoryComparisonRequest(entity_ids=[uuid.uuid4()], run_id="has spaces")


async def test_comparison_alone_writes_no_retention_row(rolled_back_db, tmp_path):
    db = rolled_back_db
    eid = await seed_entity(db, oid=f"{SYN}-R1", name=f"{SYN} Ret", npi=NPI_REGISTERED)
    sid = await _stage_affil(db, tmp_path)
    before_outcomes = await _outcome_count(db)
    before_matches = (await db.execute(select(func.count()).select_from(
        smod.EntitySourceMatch))).scalar_one()
    out = await _compare(db, sid, [eid])
    assert out["results"][0]["presence"]["outcome"] == advc.SUPPORTS
    assert "persistence" not in out
    assert await _outcome_count(db) == before_outcomes      # retention untouched
    assert (await db.execute(select(func.count()).select_from(
        smod.EntitySourceMatch))).scalar_one() == before_matches


# ── persisted mode: one attributable row per entity ───────────────────────────

async def test_persist_writes_one_attributable_row_per_entity(rolled_back_db, tmp_path):
    db = rolled_back_db
    e1 = await seed_entity(db, oid=f"{SYN}-R1", name=f"{SYN} Match", npi=NPI_REGISTERED)
    e2 = await seed_entity(db, oid=f"{SYN}-R2", name=f"{SYN} Diff", npi=NPI_VALID_OTHER)
    ghost = uuid.uuid4()
    sid = await _stage_affil(db, tmp_path)
    out = await _compare(db, sid, [e1, e2, ghost])
    summary = await ret.persist_comparison_outcomes(
        db, run_id="RUN-2026-11-08-A", comparison=out, created_by="tester@example.test")
    assert summary == {"run_id": "RUN-2026-11-08-A", "persisted": 2,
                       "already_persisted": 0, "skipped_errors": 1}
    rows = {r.entity_id: r for r in await _outcome_rows(db)}
    assert set(rows) == {e1, e2}
    r1 = rows[e1]
    assert r1.run_id == "RUN-2026-11-08-A"
    assert r1.created_by == "tester@example.test"
    assert r1.source_snapshot_id == sid
    assert str(r1.root_snapshot_id) == out["root_snapshot_id"]
    assert r1.source_system == smod.SOURCE_IQVIA_AFFILIATION
    assert r1.presence_outcome == advc.SUPPORTS
    assert r1.address_outcome == advc.ADDRESS_CONSISTENT
    assert rows[e2].address_outcome == advc.ADDRESS_DIFFERS
    # advisory boundary pinned per row; identifiers stored MASKED only
    for r in rows.values():
        assert r.determination == "AUTOMATED_CANDIDATE_NOT_CONFIRMED"
        assert r.requires_analyst_review is True
        assert r.affects_verification is False
        assert all("***" in v for v in r.identifier_basis["npi"])
        assert NPI_REGISTERED not in str(r.identifier_basis)


async def test_persist_is_idempotent_per_run_and_separable_per_run(rolled_back_db, tmp_path):
    db = rolled_back_db
    eid = await seed_entity(db, oid=f"{SYN}-R1", name=f"{SYN} Idem", npi=NPI_REGISTERED)
    sid = await _stage_affil(db, tmp_path)
    out = await _compare(db, sid, [eid])
    first = await ret.persist_comparison_outcomes(
        db, run_id="RUN-IDEM", comparison=out, created_by=SYN)
    assert first["persisted"] == 1
    again = await ret.persist_comparison_outcomes(
        db, run_id="RUN-IDEM", comparison=out, created_by=SYN)
    assert again == {"run_id": "RUN-IDEM", "persisted": 0,
                     "already_persisted": 1, "skipped_errors": 0}
    assert await _outcome_count(db) == 1                     # no duplicate row
    other = await ret.persist_comparison_outcomes(
        db, run_id="RUN-IDEM-2", comparison=out, created_by=SYN)
    assert other["persisted"] == 1                           # a new run is a new fact
    assert await _outcome_count(db) == 2


# ── append-only and advisory-boundary constraints ─────────────────────────────

async def test_rows_are_append_only(rolled_back_db, tmp_path):
    db = rolled_back_db
    eid = await seed_entity(db, oid=f"{SYN}-R1", name=f"{SYN} AppendOnly", npi=NPI_REGISTERED)
    sid = await _stage_affil(db, tmp_path)
    out = await _compare(db, sid, [eid])
    await ret.persist_comparison_outcomes(db, run_id="RUN-AO", comparison=out, created_by=SYN)
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(update(ret.IqviaAdvisoryComparisonOutcome)
                         .values(presence_outcome="DIFFERS"))


async def test_affects_verification_cannot_be_true(rolled_back_db, tmp_path):
    db = rolled_back_db
    eid = await seed_entity(db, oid=f"{SYN}-R1", name=f"{SYN} Boundary", npi=NPI_REGISTERED)
    sid = await _stage_affil(db, tmp_path)
    out = await _compare(db, sid, [eid])
    rows = ret._rows_from_comparison(run_id="RUN-CK", comparison=out, created_by=SYN)
    rows[0]["affects_verification"] = True
    db.add(ret.IqviaAdvisoryComparisonOutcome(**rows[0]))
    with pytest.raises(IntegrityError, match="ck_iqvia_adv_outcome_not_verification"):
        await db.flush()


async def test_determination_cannot_be_upgraded(rolled_back_db, tmp_path):
    db = rolled_back_db
    eid = await seed_entity(db, oid=f"{SYN}-R1", name=f"{SYN} Pinned", npi=NPI_REGISTERED)
    sid = await _stage_affil(db, tmp_path)
    out = await _compare(db, sid, [eid])
    rows = ret._rows_from_comparison(run_id="RUN-DET", comparison=out, created_by=SYN)
    rows[0]["determination"] = "VERIFIED"
    db.add(ret.IqviaAdvisoryComparisonOutcome(**rows[0]))
    with pytest.raises(IntegrityError, match="ck_iqvia_adv_outcome_determination"):
        await db.flush()
