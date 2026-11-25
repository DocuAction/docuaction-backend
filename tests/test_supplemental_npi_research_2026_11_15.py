"""Supplemental NPI research: the persisted entry path, and the synthetic pilot.

Database-backed (rolled-back transaction), synthetic rows only. Needs migration 20261115_rce_npi_research (applied to the
disposable test database only). Real services throughout: the routes' own functions, the QA gate, the loader, the
dataset and the package script.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.reports.data import october_review_dataset as ds
from app.tefca_registry import models as reg
from app.tefca_registry import qa_gate
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import routes as rce_routes
from app.tefca_registry.rce import supplemental_research as sr
from rce_traceability_support import SYN, make_rows, rolled_back_db, seed_entity, seed_intake  # noqa: F401
from scripts import generate_record_review_package as pkg

pytestmark = pytest.mark.regression
TODAY = date(2026, 11, 15)
REVIEW_IDS = {}
GOOD_NPI = "1234567893"                       # synthetic, Luhn-valid
RATIONALE = "NPPES organization record matches the delivered name, state and taxonomy of this participant"
REFERENCE = "NPPES NPI Registry lookup by organization name (synthetic reference)"


def analyst(tag="ghania"):
    return SimpleNamespace(id=uuid.uuid4(), email=f"{tag}@synthetic-test.docuaction.invalid", role="reviewer")


def qa_user(tag="bilal"):
    return SimpleNamespace(id=uuid.uuid4(), email=f"{tag}@synthetic-test.docuaction.invalid", role="qalead")


class _Ctx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_a):
        return False


async def _seed(db, arc, n=2):
    rows = make_rows(n, arc=arc, purposesofuse="T-TRTMNT,T-PH")
    for r in rows:
        r["NPI"] = ""                                            # delivered blank
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    run = m.RceIngestionRun(id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="t", rule_config_hash="0" * 64,
                            started_at=datetime(2026, 11, 7), completed_at=datetime(2026, 11, 8, 9), run_status="COMPLETE",
                            assessment_metadata={"assessment_date": "2026-11-08", "source": "pinned_param"})
    db.add(run)
    await db.flush()
    recs = {r.line_number - 1: r for r in (await db.execute(
        select(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}
    for i, status in ((1, "HELD"), (2, "CLEAN")):
        ent = await seed_entity(db, oid=f"{SYN}-R-{i}-{uuid.uuid4().hex[:8]}", name=f"{SYN} research {i}")
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=recs[i].id,
                                  record_status=status, status_reason="blocking identity" if status == "HELD" else None,
                                  rce_org_oid=recs[i].source_rce_id, transformation_version="t",
                                  canonical_entity_id=None if status == "HELD" else ent))
        if status == "CLEAN":
            REVIEW_IDS[intake_id] = f"REV-9980-{uuid.uuid4().int % 10**6:06d}"
            db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id=REVIEW_IDS[intake_id], entity_id=ent, source_record_id=recs[i].id,
                                    classification_bucket="B4", classification_rule="RULE-006", classification_rule_version=4,
                                    classification_rationale="automated Treatment B4 candidate",
                                    created_at=datetime(2026, 11, 8, 10)))
    await db.flush()
    return intake_id, recs


def _expected(rec):
    return {"source_line": rec.line_number, "hcid": rec.hcid, "tefca_id": rec.tefcaid}


def _body(rec, **over):
    base = dict(expected=_expected(rec), researched_npi=GOOD_NPI, nppes_reference=REFERENCE,
                nppes_check_date="2026-11-14", match_rationale=RATIONALE)
    base.update(over)
    return rce_routes.SupplementalNpiResearchRequest(**base)


async def _enter(db, intake_id, rec, user=None, **over):
    return await rce_routes.enter_supplemental_npi_research(str(intake_id), str(rec.id), _body(rec, **over), db=db,
                                                            user=user or analyst())


# ── the entry path ───────────────────────────────────────────────────────────

async def test_entry_persists_with_provenance_reads_back_and_leaves_the_delivered_record_alone(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.781.01")
    rec = recs[2]
    rec_id = rec.id
    before = (rec.parsed, rec.npi, rec.hcid, rec.tefcaid, rec.record_sha256)
    out = await _enter(db, intake_id, rec)
    assert out["status"] == "inserted"
    entry = out["entry"]
    assert entry["original_submitted_npi"] == "" and entry["researched_npi"] == GOOD_NPI
    assert entry["nppes_reference"] == REFERENCE and entry["nppes_check_date"] == "2026-11-14"
    assert entry["match_rationale"] == RATIONALE and entry["researcher"].startswith("ghania@")
    assert entry["agt_record_id"] == str(rec_id) and entry["non_decision"] is True

    db.expire_all()                                              # reload from the database
    got = await rce_routes.read_supplemental_npi_research(str(intake_id), source_record_id=str(rec_id), current_only=True,
                                                          limit=50, db=db, user=analyst())
    assert [e["id"] for e in got["entries"]] == [entry["id"]]
    same = await db.get(m.RceSourceRecord, rec_id, populate_existing=True)
    assert (same.parsed, same.npi, same.hcid, same.tefcaid, same.record_sha256) == before, "the delivered record is untouched"
    assert sr.delivered_npi(same) == ""


async def test_amend_and_withdraw_append_history_and_never_edit(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.781.02")
    rec = recs[2]
    first = (await _enter(db, intake_id, rec))["entry"]
    amended = (await _enter(db, intake_id, rec, action="AMEND", supersedes_id=first["id"],
                            change_reason="the first lookup matched a sister organization",
                            researched_npi="1245319599", nppes_check_date="2026-11-15"))["entry"]
    withdrawn = (await _enter(db, intake_id, rec, action="WITHDRAW", supersedes_id=amended["id"],
                              change_reason="research withdrawn pending owner review"))["entry"]
    history = (await rce_routes.read_supplemental_npi_research(str(intake_id), source_record_id=str(rec.id),
                                                               current_only=False, limit=50, db=db, user=analyst()))["entries"]
    assert [h["entry_action"] for h in history] == ["RECORD", "AMEND", "WITHDRAW"]
    assert [h["supersedes_id"] for h in history] == [None, first["id"], amended["id"]]
    assert history[0]["researched_npi"] == GOOD_NPI, "the earlier entry is preserved, not rewritten"
    current = (await rce_routes.read_supplemental_npi_research(str(intake_id), source_record_id=str(rec.id),
                                                               current_only=True, limit=50, db=db, user=analyst()))["entries"]
    assert current == [] and withdrawn["researched_npi"] is None
    with pytest.raises(HTTPException) as exc:                    # a withdrawn chain is not silently restarted
        await _enter(db, intake_id, rec)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("over,needle", [
    (dict(expected={"source_line": 999, "hcid": "x", "tefca_id": "y"}), "do not match the record"),
    (dict(expected={"source_line": 2}), "expected.hcid is required"),
    (dict(researched_npi="123456789"), "exactly 10 digits"),
    (dict(researched_npi="1234567890"), "check digit"),
    (dict(researched_npi="12345678AB"), "exactly 10 digits"),
    (dict(nppes_reference=" "), "nppes_reference is required"),
    (dict(nppes_check_date="2999-01-01"), "in the future"),
    (dict(nppes_check_date="15/11/2026"), "YYYY-MM-DD"),
    (dict(match_rationale="looks right"), "match_rationale is required"),
    (dict(action="AMEND"), "must name the entry it supersedes"),
])
async def test_incomplete_or_mismatched_entries_are_refused_and_write_nothing(rolled_back_db, over, needle):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.781.03")
    rec = recs[2]
    with pytest.raises(HTTPException) as exc:
        await _enter(db, intake_id, rec, **over)
    assert exc.value.status_code == 409 and needle in exc.value.detail
    assert (await db.execute(text("select count(*) from rce_supplemental_npi_research where source_intake_id = :i"),
                             {"i": intake_id})).scalar() == 0


async def test_there_is_no_entity_only_or_cross_delivery_fallback(rolled_back_db):
    db = rolled_back_db
    intake_a, recs_a = await _seed(db, "9.99.781.04")
    intake_b, recs_b = await _seed(db, "9.99.781.05")
    rec_b = recs_b[2]
    with pytest.raises(HTTPException) as exc:                    # a record id from another delivery
        await rce_routes.enter_supplemental_npi_research(str(intake_a), str(rec_b.id), _body(rec_b), db=db, user=analyst())
    assert "does not exist in that delivery" in exc.value.detail
    with pytest.raises(HTTPException) as exc:                    # no record id at all
        await rce_routes.enter_supplemental_npi_research(str(intake_a), "", _body(rec_b), db=db, user=analyst())
    assert "explicit source_record_id" in exc.value.detail
    with pytest.raises(sr.ResearchRefused):                      # the service itself refuses an entity-style call
        await sr.record_entry(db, intake_id=intake_a, source_record_id=None, expected={}, entry={}, actor_email="a@x.invalid")


async def test_the_database_itself_enforces_linkage_snapshot_and_append_only(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.781.06")
    rec = recs[2]
    entry = (await _enter(db, intake_id, rec))["entry"]
    base = dict(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id, source_line=rec.line_number,
                hcid=rec.hcid, tefca_id=rec.tefcaid, original_submitted_npi="", entry_action="RECORD",
                researched_npi=GOOD_NPI, nppes_reference="r", nppes_check_date=TODAY, match_rationale=RATIONALE,
                researcher="x@synthetic-test.docuaction.invalid", idempotency_key=uuid.uuid4().hex + uuid.uuid4().hex)
    from app.tefca_registry.rce.supplemental_research_models import RceSupplementalNpiResearch as R
    # The record already has a root, so the ONLY thing this insert can trip is the unique-root index. The other
    # constraints (snapshot, linkage, non-decision, NPI shape, change reason) are each exercised on an unused
    # record, with the constraint/trigger name asserted, in test_npi_research_review_fixes_2026_11_15.py.
    sp = await db.begin_nested()
    with pytest.raises(Exception) as exc:
        db.add(R(**{**base, "id": uuid.uuid4(), "idempotency_key": uuid.uuid4().hex + uuid.uuid4().hex}))
        await db.flush()
    assert "uq_rce_npi_research_root" in str(exc.value)
    await sp.rollback()
    for stmt in ("update rce_supplemental_npi_research set researcher = 'z'", "delete from rce_supplemental_npi_research"):
        sp = await db.begin_nested()
        with pytest.raises(Exception) as exc:
            await db.execute(text(stmt))
        assert "append-only" in str(exc.value)
        await sp.rollback()
    assert entry["original_submitted_npi"] == ""


async def test_refuses_cleanly_when_the_migration_is_not_applied(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.781.07")

    async def absent(_db):
        raise sr.ResearchStoreNotApplied("SUPPLEMENTAL_RESEARCH_NOT_APPLIED: not applied")

    monkeypatch.setattr(sr, "_assert_store", absent)
    with pytest.raises(HTTPException) as exc:
        await _enter(db, intake_id, recs[2])
    assert exc.value.status_code == 409 and "SUPPLEMENTAL_RESEARCH_NOT_APPLIED" in exc.value.detail
    assert (await sr.current_by_record(db, intake_id, [recs[2].id])) in ({}, {})


def test_the_table_is_migration_owned_and_the_routes_require_an_analyst_role():
    from app.tefca_registry.rce.traceability_models import MIGRATION_OWNED_TABLES
    assert "rce_supplemental_npi_research" in MIGRATION_OWNED_TABLES
    assert rce_routes.NPI_RESEARCH_ENTRY_ROLE == "reviewer" and rce_routes.NPI_RESEARCH_READ_ROLE == "reviewer"


# ── the synthetic pilot: entry -> save -> reload -> audit -> determination -> separate QA -> report ──

async def test_pilot_research_does_not_overwrite_the_blank_remove_b4_or_release_a_hold(rolled_back_db, monkeypatch, tmp_path):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.781.10")
    held, clean = recs[1], recs[2]
    review_id = REVIEW_IDS[intake_id]
    ghania, izaan, bilal = analyst("ghania"), analyst("izaan"), qa_user("bilal")

    audit_before = {a.id for a in (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "supplemental_npi_research_entered"))).scalars().all()}
    # 1. entry for the B4 record and for the HELD record (separate entries, explicit linkage)
    e1 = (await _enter(db, intake_id, clean, user=ghania))["entry"]
    e2 = (await _enter(db, intake_id, held, user=ghania, researched_npi="1245319599"))["entry"]
    # 2. save -> reload -> read-back and audit history
    db.expire_all()
    back = (await rce_routes.read_supplemental_npi_research(str(intake_id), source_record_id=None, current_only=False, limit=50, db=db, user=izaan))["entries"]
    assert {b["id"] for b in back} == {e1["id"], e2["id"]}
    audit = [a for a in (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "supplemental_npi_research_entered"))).scalars().all() if a.id not in audit_before]
    assert len(audit) == 2 and {a.actor_email for a in audit} == {ghania.email}
    # 3. analyst determination (Izaan), then a SEPARATE QA action (Bilal). The same person cannot do both.
    await qa_gate.record_analyst_determination(db, review_id, user=izaan, determination="CONFIRM",
                                               rationale="researched NPI exists but the submission requirement is about the delivered value")
    with pytest.raises(qa_gate.QaGateRefused):
        await qa_gate.submit_qa_review(db, review_id, user=izaan, qa_action="APPROVE", qa_reason="approving my own determination")
    await qa_gate.submit_qa_review(db, review_id, user=bilal, qa_action="APPROVE", qa_reason="synthetic QA test action - workflow only")
    assert (await qa_gate.review_state(db, review_id))["qa_state"] == "APPROVED"

    # 4. the report: original blank + B4 + HELD all preserved; research shown separately; disposition separate
    import app.core.database as database
    monkeypatch.setattr(database, "async_session_maker", lambda: _Ctx(db))
    out = tmp_path / "pilot"
    args = SimpleNamespace(intake=str(intake_id), cutoff="2099-01-01T00:00:00", out=str(out), run_id=None,
                           formats="csv", baseline=None, classification="DRAFT")
    assert await pkg.build(args) == 0
    lines = list(csv.reader(io.StringIO(next(p for p in out.glob("*.csv") if "field-findings" not in p.name)
                                        .read_bytes().decode("utf-8-sig"))))
    cols, data = lines[1], lines[2:]
    C = lambda row, name: row[cols.index(name)]                                       # noqa: E731
    by_line = {int(r[cols.index("Source_Line")]): r for r in data}
    b4, hold = by_line[clean.line_number], by_line[held.line_number]
    assert C(b4, "Submitted NPI (original, verbatim)") == "" and C(b4, "Bucket") == "B4", "blank NPI and B4 remain"
    assert C(b4, "Supplemental NPI (manual research - NOT an accepted correction)") == GOOD_NPI
    prov = C(b4, "Supplemental NPI Research (stored entry; NOT an accepted correction)")
    for needle in (REFERENCE, "2026-11-14", ghania.email, RATIONALE, e1["id"]):
        assert needle in prov, needle
    assert C(b4, "Analyst/QA Status") == "Approved by QA" and C(b4, "Reviewed Disposition (QA-approved only)") == "Confirmed B4"
    assert C(hold, "Status") == "HELD" and C(hold, "Bucket") == "B4" and C(hold, "Submitted NPI (original, verbatim)") == ""
    assert "remains HELD" in C(hold, "Reviewed Disposition (QA-approved only)")
    # the stored classification and the delivered record are exactly as before
    rev = (await db.execute(select(reg.ReviewRecord).where(reg.ReviewRecord.review_id == review_id))).scalar_one()
    assert rev.classification_bucket == "B4" and rev.classification_rule == "RULE-006"
    cur = (await db.execute(select(m.RceCuratedRecord).where(m.RceCuratedRecord.source_record_id == held.id))).scalar_one()
    assert cur.record_status == "HELD"
