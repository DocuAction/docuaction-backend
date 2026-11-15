"""CodeRabbit findings on #233 (head 5e857216), each reproduced against synthetic data on the disposable database.

* a NULL researched_npi must be refused by the DATABASE for RECORD and AMEND (a CHECK that evaluates to NULL passes);
* the loader applies the evidence cutoff to supplemental research (amendment/withdrawal after the cutoff are invisible);
* an exact replay of a committed RECORD/AMEND is answered already_present, not refused;
* current_only filters before LIMIT (a truncated window must not show a superseded/withdrawn row as current);
* a concurrent duplicate first entry is a 409, not a 500;
* the constraint tests use an unused record per case, so the unique-root index cannot satisfy them.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, DBAPIError

from app.reports.data import october_review_loader as loader
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import routes as rce_routes
from app.tefca_registry.rce import supplemental_research as sr
from app.tefca_registry.rce.supplemental_research_models import RceSupplementalNpiResearch as R
from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401

pytestmark = pytest.mark.regression
TODAY = date(2026, 11, 15)
GOOD_NPI = "1234567893"
SECOND_NPI = "1245319599"                      # synthetic, Luhn-valid
RATIONALE = "NPPES organization record matches the delivered name, state and taxonomy of this participant"
REFERENCE = "NPPES NPI Registry lookup by organization name (synthetic reference)"
WHO = "analyst@synthetic-test.docuaction.invalid"


async def _seed(db, arc, n=3):
    rows = make_rows(n, arc=arc, purposesofuse="T-TRTMNT,T-PH")
    for r in rows:
        r["NPI"] = ""
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    recs = list((await db.execute(select(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == intake_id)
                                  .order_by(m.RceSourceRecord.line_number))).scalars().all())
    return intake_id, recs


def _expected(rec):
    return {"source_line": rec.line_number, "hcid": rec.hcid, "tefca_id": rec.tefcaid}


def _entry(**over):
    base = dict(action="RECORD", researched_npi=GOOD_NPI, nppes_reference=REFERENCE, nppes_check_date="2026-11-14",
                match_rationale=RATIONALE)
    base.update(over)
    return base


async def _record(db, intake_id, rec, **over):
    return await sr.record_entry(db, intake_id=intake_id, source_record_id=rec.id, expected=_expected(rec),
                                 entry=_entry(**over), actor_email=WHO, today=TODAY, commit=False)


def _row(rec, **over):
    base = dict(id=uuid.uuid4(), source_intake_id=rec.source_intake_id, source_record_id=rec.id,
                source_line=rec.line_number, hcid=rec.hcid, tefca_id=rec.tefcaid, original_submitted_npi="",
                entry_action="RECORD", researched_npi=GOOD_NPI, nppes_reference=REFERENCE,
                nppes_check_date=TODAY, match_rationale=RATIONALE, researcher=WHO,
                idempotency_key=uuid.uuid4().hex + uuid.uuid4().hex)
    base.update(over)
    return R(**base)


async def _expect_db_refusal(db, row, needle):
    sp = await db.begin_nested()
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        db.add(row)
        await db.flush()
    await sp.rollback()
    assert needle in str(exc.value), str(exc.value)[:300]


# ── 4580174205: NULL researched_npi ─────────────────────────────────────────

async def test_database_refuses_a_null_researched_npi_for_record_and_amend(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.782.01")
    fresh, with_root = recs[0], recs[1]              # `fresh` has NO root, so the unique-root index cannot mask this
    await _expect_db_refusal(db, _row(fresh, researched_npi=None), "ck_rce_npi_research_complete")
    root = (await _record(db, intake_id, with_root))["entry"]
    amend = _row(with_root, entry_action="AMEND", researched_npi=None, supersedes_id=uuid.UUID(root["id"]),
                 change_reason="correcting the previous research entry")
    await _expect_db_refusal(db, amend, "ck_rce_npi_research_complete")


# ── 4580174239: exact replay ────────────────────────────────────────────────

async def test_an_exact_replay_of_record_and_amend_is_already_present_not_refused(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.782.02")
    rec = recs[0]
    first = await _record(db, intake_id, rec)
    assert first["status"] == "inserted"
    replay = await _record(db, intake_id, rec)
    assert replay["status"] == "already_present" and replay["entry"]["id"] == first["entry"]["id"]
    amend_kw = dict(action="AMEND", researched_npi=SECOND_NPI, supersedes_id=first["entry"]["id"],
                    change_reason="the first lookup matched the wrong organization")
    amended = await _record(db, intake_id, rec, **amend_kw)
    assert amended["status"] == "inserted"
    again = await _record(db, intake_id, rec, **amend_kw)
    assert again["status"] == "already_present" and again["entry"]["id"] == amended["entry"]["id"]
    # a DIFFERENT payload is still refused
    with pytest.raises(sr.ResearchRefused):
        await _record(db, intake_id, rec, researched_npi=SECOND_NPI)
    with pytest.raises(sr.ResearchRefused):
        await _record(db, intake_id, rec, **{**amend_kw, "supersedes_id": first["entry"]["id"],
                                             "researched_npi": "1730123456"})
    count = (await db.execute(text("select count(*) from rce_supplemental_npi_research"))).scalar()
    assert count == 2


# ── 4580174245: current_only before LIMIT ───────────────────────────────────

async def test_current_only_is_filtered_before_the_limit(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.782.03")
    rec = recs[0]
    root = (await _record(db, intake_id, rec))["entry"]
    await _record(db, intake_id, rec, action="WITHDRAW", supersedes_id=root["id"],
                  change_reason="entered against the wrong record in error")
    window = await sr.list_entries(db, intake_id=intake_id, source_record_id=rec.id, current_only=True, limit=1)
    assert window == [], "the superseded root must not appear as current just because the WITHDRAW fell outside LIMIT"
    other = recs[1]
    keep = (await _record(db, intake_id, other))["entry"]
    got = await sr.list_entries(db, intake_id=intake_id, current_only=True, limit=1)
    assert [e["id"] for e in got] == [keep["id"]]
    full = await sr.list_entries(db, intake_id=intake_id, current_only=False, limit=10)
    assert len(full) == 3


# ── 4580174217: evidence cutoff ─────────────────────────────────────────────

async def _set_entered(db, entry_id, when):
    # entered_at is append-only evidence; the disposable rolled-back transaction rewrites it for the test only
    await db.execute(text("alter table rce_supplemental_npi_research disable trigger trg_rce_npi_research_append_only"))
    await db.execute(text("update rce_supplemental_npi_research set entered_at = :t where id = :i"),
                     {"t": when, "i": uuid.UUID(entry_id)})
    await db.execute(text("alter table rce_supplemental_npi_research enable trigger trg_rce_npi_research_append_only"))


async def test_the_evidence_cutoff_applies_to_supplemental_research(rolled_back_db):
    db = rolled_back_db
    from test_supplemental_npi_research_2026_11_15 import _seed as _seed_run     # adds a completed assessment run
    intake_id, recs = await _seed_run(db, "9.99.782.04")
    amend_rec, withdraw_rec = recs[2], recs[1]
    t0 = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    cutoff = t0 + timedelta(days=1)
    after = cutoff + timedelta(days=2)
    a_root = (await _record(db, intake_id, amend_rec))["entry"]
    a_new = (await _record(db, intake_id, amend_rec, action="AMEND", researched_npi=SECOND_NPI,
                           supersedes_id=a_root["id"], change_reason="corrected after a second lookup"))["entry"]
    w_root = (await _record(db, intake_id, withdraw_rec))["entry"]
    w_out = (await _record(db, intake_id, withdraw_rec, action="WITHDRAW", supersedes_id=w_root["id"],
                           change_reason="entered against the wrong record in error"))["entry"]
    await _set_entered(db, a_root["id"], t0)
    await _set_entered(db, a_new["id"], after)
    await _set_entered(db, w_root["id"], t0)
    await _set_entered(db, w_out["id"], after)

    at_cutoff = await sr.current_by_record(db, intake_id, [amend_rec.id, withdraw_rec.id], cutoff=cutoff)
    assert at_cutoff[amend_rec.id]["researched_npi"] == GOOD_NPI, "an amendment after the cutoff must not appear"
    assert at_cutoff[withdraw_rec.id]["researched_npi"] == GOOD_NPI, "a later withdrawal must not remove earlier research"
    now = await sr.current_by_record(db, intake_id, [amend_rec.id, withdraw_rec.id])
    assert now[amend_rec.id]["researched_npi"] == SECOND_NPI and withdraw_rec.id not in now

    facts = await loader.load_record_facts(db, intake_id=intake_id, evidence_cutoff=cutoff.replace(tzinfo=None))
    npis = sorted(f.get("supplemental_npi") for f in facts["facts"] if f.get("supplemental_npi"))
    assert npis == [GOOD_NPI, GOOD_NPI], npis       # both records keep their pre-cutoff research at the cutoff


# ── 4580174235: concurrent duplicate first entry -> 409 ─────────────────────

async def test_a_duplicate_first_entry_losing_the_race_is_a_409_not_a_500(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.782.05")
    rec = recs[0]
    await _record(db, intake_id, rec)                   # the winner, already stored

    async def lost_race(_db, *a, **k):                  # the loser passed the pre-checks before the winner committed
        raise IntegrityError("INSERT", {}, Exception('duplicate key value violates unique constraint "uq_rce_npi_research_idem"'))

    monkeypatch.setattr(sr, "record_entry", lost_race)
    body = rce_routes.SupplementalNpiResearchRequest(expected=_expected(rec), researched_npi=GOOD_NPI,
                                                     nppes_reference=REFERENCE, nppes_check_date="2026-11-14",
                                                     match_rationale=RATIONALE)
    user = SimpleNamespace(id=uuid.uuid4(), email=WHO, role="reviewer")
    with pytest.raises(HTTPException) as exc:
        await rce_routes.enter_supplemental_npi_research(str(intake_id), str(rec.id), body, db=db, user=user)
    assert exc.value.status_code == 409


# ── 4580174259: each database constraint is exercised on its own, on an unused record ──

@pytest.mark.parametrize("over,needle", [
    (dict(original_submitted_npi="1111111112"), "original-NPI snapshot differs"),
    (dict(source_line=9999), "do not match record"),
    (dict(non_decision=False), "ck_rce_npi_research_not_a_decision"),
    (dict(researched_npi="12AB"), "ck_rce_npi_research_complete"),
    (dict(match_rationale="too short"), "ck_rce_npi_research_complete"),
    (dict(entry_action="AMEND"), "ck_rce_npi_research"),            # AMEND without supersedes/change_reason
])
async def test_each_database_constraint_is_exercised_on_an_unused_record(rolled_back_db, over, needle):
    db = rolled_back_db
    _intake, recs = await _seed(db, "9.99.782.06", n=1)
    rec = recs[0]                                                   # no root exists, so the unique-root index is not the reason
    if "source_line" in over:
        over = {**over}
    await _expect_db_refusal(db, _row(rec, **over), needle)
    # the same row without the defect IS accepted: the refusal is attributable to the one changed field
    sp = await db.begin_nested()
    db.add(_row(rec))
    await db.flush()
    await sp.rollback()


async def test_a_second_root_and_a_cross_record_amendment_are_refused_by_the_database(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.782.07")
    a, b = recs[0], recs[1]
    root = (await _record(db, intake_id, a))["entry"]
    await _expect_db_refusal(db, _row(a), "uq_rce_npi_research_root")
    cross = _row(b, entry_action="AMEND", supersedes_id=uuid.UUID(root["id"]),
                 change_reason="amending an entry that belongs to another record")
    await _expect_db_refusal(db, cross, "must stay on the same source record")
