"""AGT_Record_ID identity mode for the case-update import PREVIEW.

Scope: preview/identity validation only. Apply behaviour is unchanged (only
`assign_to_email` on an ACCEPTED row is applied); notes/corrections stay STAGED
because the comment-storage decision is still open. No comment is stored here,
no determination/resolution/QA endpoint is involved, and nothing is written.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry import review_routes as rr
from app.tefca_registry.rce import case_update_import as cui
from app.tefca_registry.rce import models as m
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, seed_entity, seed_intake,
)

AGT = str(uuid.uuid4())
INTAKE = str(uuid.uuid4())


def _row(**kw):
    base = {cui.IDENTITY_INTAKE: INTAKE, cui.IDENTITY_AGT: AGT}
    base.update(kw)
    return base


def _validator(*, problem=None, held=False, record=True):
    calls = []

    async def validate(identity):
        calls.append(dict(identity))
        if problem:
            return {"problem": problem, "source_record": None}
        sr = {"agt_record_id": identity[cui.IDENTITY_AGT], "source_rce_id": "9.99.1",
              "source_line": 2, "curated_status": "HELD" if held else "CLEAN",
              "held": held, "promoted": not held} if record else None
        return {"problem": None, "source_record": sr}

    validate.calls = calls
    return validate


def _resolver(*ids):
    calls = []

    async def resolve(identity):
        calls.append(dict(identity))
        return list(ids)

    resolve.calls = calls
    return resolve


# ── header / row identity ───────────────────────────────────────────────────

def test_header_accepts_agt_with_intake_and_rejects_agt_alone():
    ok = cui.validate_header(["intake_id", "agt_record_id", "analyst_note"])
    assert ok["ok"] and "agt_record_id" in ok["mapped"]
    bad = cui.validate_header(["agt_record_id", "analyst_note"])
    assert not bad["ok"] and "agt_record_id" in bad["missing"][0]
    assert cui.validate_header(["review_id", "analyst_note"])["ok"]                 # unchanged
    assert cui.validate_header(["intake_id", "source_rce_id", "analyst_note"])["ok"]  # unchanged


@pytest.mark.parametrize("row,needle", [
    ({cui.IDENTITY_AGT: AGT, "analyst_note": "x"}, "requires intake_id"),
    (_row(**{cui.IDENTITY_AGT: "not-a-uuid", "analyst_note": "x"}), "not a UUID"),
    (_row(**{cui.IDENTITY_INTAKE: "nope", "analyst_note": "x"}), "not a UUID"),
])
async def test_malformed_agt_rows_are_rejected_before_any_lookup(row, needle):
    res, val = _resolver("REV-2026-000001"), _validator()
    out = await cui.preview_rows([row], res, val)
    item = out["rows"][0]
    assert item["disposition"] == cui.DISP_REJECTED and needle in item["reasons"][0]
    assert res.calls == [] and val.calls == []


async def test_an_agt_row_without_a_validator_is_never_trusted():
    res = _resolver("REV-2026-000001")
    out = await cui.preview_rows([_row(analyst_note="x")], res)        # no validator
    assert out["rows"][0]["disposition"] == cui.DISP_REJECTED
    assert "cannot be validated" in out["rows"][0]["reasons"][0] and res.calls == []


async def test_validator_problem_rejects_with_its_exact_reason_and_skips_the_resolver():
    msg = "agt_record_id X belongs to delivery A, not B; refusing to attach it to another delivery"
    res = _resolver("REV-2026-000001")
    out = await cui.preview_rows([_row(analyst_note="x")], res, _validator(problem=msg))
    assert out["rows"][0]["disposition"] == cui.DISP_REJECTED
    assert out["rows"][0]["reasons"] == [msg] and res.calls == []


async def test_source_rce_id_is_kept_as_a_separate_cross_check_identifier():
    val = _validator()
    out = await cui.preview_rows([_row(source_rce_id="9.99.1", analyst_note="x")],
                                 _resolver(), val)
    ident = out["rows"][0]["identity"]
    assert ident[cui.IDENTITY_AGT] == AGT and ident[cui.IDENTITY_SOURCE] == "9.99.1"
    assert ident[cui.IDENTITY_AGT] != ident[cui.IDENTITY_SOURCE]
    assert val.calls[0][cui.IDENTITY_SOURCE] == "9.99.1"


# ── dispositions: apply behaviour preserved ─────────────────────────────────

async def test_agt_row_with_a_case_and_assignment_is_accepted_exactly_like_other_modes():
    out = await cui.preview_rows(
        [_row(assign_to_email="analyst@example.invalid")],
        _resolver("REV-2026-000042"), _validator())
    item = out["rows"][0]
    assert item["disposition"] == cui.DISP_ACCEPTED and item["matched_review_ids"] == ["REV-2026-000042"]
    plan = cui.plan_apply(out)
    assert plan["assignments"] == [{"row": 1, "review_id": "REV-2026-000042",
                                    "assign_to_email": "analyst@example.invalid"}]


async def test_held_source_record_without_a_case_keeps_its_note_staged_and_applies_nothing():
    out = await cui.preview_rows(
        [_row(analyst_note="reviewer says: please check the NPI")],
        _resolver(), _validator(held=True))
    item = out["rows"][0]
    assert item["disposition"] == cui.DISP_UNCHANGED
    reason = item["reasons"][0]
    assert ", HELD" in reason and "STAGED only" in reason and "no review case" in reason
    assert item["source_record"]["held"] is True
    plan = cui.plan_apply(out)
    assert plan["assignments"] == [] and plan["staged_not_applied"] == []   # not even staged for apply
    assert plan["reconciles"] and cui.describe_apply(out)["available"] is False


async def test_agt_row_with_no_case_cannot_be_assigned_or_carry_a_workflow_request():
    for kw in ({"assign_to_email": "a@example.invalid"},
               {"requested_state": "released"},
               {"proposed_correction_value": "B1"}):
        out = await cui.preview_rows([_row(**kw)], _resolver(), _validator())
        assert out["rows"][0]["disposition"] == cui.DISP_REJECTED, kw
        assert "no existing case" in out["rows"][0]["reasons"][0]


async def test_workflow_value_on_an_agt_row_with_a_case_is_still_only_a_request():
    out = await cui.preview_rows([_row(requested_state="approved")],
                                 _resolver("REV-2026-000042"), _validator())
    item = out["rows"][0]
    assert item["disposition"] == cui.DISP_NEEDS_REVIEW and item["requested_workflow_values"]
    assert cui.plan_apply(out)["assignments"] == []


async def test_duplicate_agt_record_in_one_file_is_not_applied_twice():
    rows = [_row(analyst_note="first"), _row(analyst_note="second"),
            _row(**{cui.IDENTITY_AGT: str(uuid.uuid4()), "analyst_note": "other"})]
    out = await cui.preview_rows(rows, _resolver(), _validator())
    d = [r["disposition"] for r in out["rows"]]
    assert d == [cui.DISP_UNCHANGED, cui.DISP_NEEDS_REVIEW, cui.DISP_UNCHANGED]
    assert "duplicate agt_record_id (first at row 1)" in out["rows"][1]["reasons"][0]
    assert out["reconciles"] and sum(out["counts"].values()) == out["rows_read"] == 3


async def test_existing_identity_modes_are_unchanged_and_need_no_validator():
    out = await cui.preview_rows(
        [{"review_id": "REV-2026-000001", "assign_to_email": "a@example.invalid"},
         {"intake_id": INTAKE, "source_rce_id": "9.99.1", "analyst_note": "n"}],
        _resolver("REV-2026-000001"))
    assert [r["identity_mode"] for r in out["rows"]] == ["review_id", "delivery"]
    assert out["rows"][0]["disposition"] == cui.DISP_ACCEPTED


# ── the real validator / resolver against the database ──────────────────────

async def _seed_two_deliveries(db, arc):
    rows = make_rows(3, arc=arc)
    a_id, _ = await seed_intake(db, rows, with_job=False)
    b_id, _ = await seed_intake(db, rows, with_job=False)      # SAME business ids in both
    recs = {}
    for key, iid in (("a", a_id), ("b", b_id)):
        recs[key] = {r.line_number - 1: r for r in (await db.execute(
            select(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == iid))
        ).scalars().all()}
    return rows, a_id, b_id, recs


async def test_validator_accepts_held_and_unpromoted_records_and_refuses_foreign_ones(rolled_back_db):
    db = rolled_back_db
    rows, a_id, b_id, recs = await _seed_two_deliveries(db, "9.99.777.80")
    db.add(m.RceCuratedRecord(
        id=uuid.uuid4(), source_intake_id=a_id, source_record_id=recs["a"][1].id,
        record_status="HELD", status_reason="blocker", rce_org_oid=rows[0]["id"],
        transformation_version="t"))
    await db.flush()
    validate = rr._agt_identity_validator(db)
    ident = lambda rec, intake, **k: {cui.IDENTITY_AGT: str(rec.id), cui.IDENTITY_INTAKE: str(intake), **k}

    held = await validate(ident(recs["a"][1], a_id))
    assert held["problem"] is None and held["source_record"]["held"] is True
    assert held["source_record"]["promoted"] is False
    plain = await validate(ident(recs["a"][2], a_id))          # no curated row at all
    assert plain["problem"] is None and plain["source_record"]["curated_status"] is None

    foreign = await validate(ident(recs["b"][1], a_id))        # B's record named under A
    assert foreign["problem"] and str(b_id) in foreign["problem"] and str(a_id) in foreign["problem"]
    assert "refusing to attach it to another delivery" in foreign["problem"]
    unknown = await validate({cui.IDENTITY_AGT: str(uuid.uuid4()), cui.IDENTITY_INTAKE: str(a_id)})
    assert "is not a source record" in unknown["problem"]
    ok_bid = await validate(ident(recs["a"][1], a_id, source_rce_id=rows[0]["id"]))
    assert ok_bid["problem"] is None
    bad_bid = await validate(ident(recs["a"][1], a_id, source_rce_id=rows[2]["id"]))
    assert "does not match this source record's own" in bad_bid["problem"]


async def test_resolver_finds_cases_through_the_source_record_anchor_only(rolled_back_db):
    db = rolled_back_db
    rows, a_id, b_id, recs = await _seed_two_deliveries(db, "9.99.777.81")
    shared = await seed_entity(db, oid=f"{SYN}-AGT-{uuid.uuid4().hex[:8]}", name=f"{SYN} shared")
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id="REV-9981-000001", entity_id=shared,
                            source_record_id=recs["a"][1].id, created_at=datetime.utcnow()))
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id="REV-9981-000002", entity_id=shared,
                            source_record_id=recs["b"][1].id, created_at=datetime.utcnow()))
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id="REV-9981-000003", entity_id=shared,
                            created_at=datetime.utcnow()))               # legacy: entity only
    await db.flush()
    resolve = rr._case_identity_resolver(db)
    got_a = await resolve({cui.IDENTITY_AGT: str(recs["a"][1].id), cui.IDENTITY_INTAKE: str(a_id)})
    got_b = await resolve({cui.IDENTITY_AGT: str(recs["b"][1].id), cui.IDENTITY_INTAKE: str(b_id)})
    assert got_a == ["REV-9981-000001"] and got_b == ["REV-9981-000002"]   # no sharing via the entity
    assert await resolve({cui.IDENTITY_AGT: str(recs["a"][2].id)}) == []


async def test_end_to_end_csv_preview_uses_the_real_validator_and_applies_nothing_but_assignment(rolled_back_db):
    db = rolled_back_db
    rows, a_id, b_id, recs = await _seed_two_deliveries(db, "9.99.777.82")
    ent = await seed_entity(db, oid=f"{SYN}-AGT2-{uuid.uuid4().hex[:8]}", name=f"{SYN} e2e")
    db.add(m.RceCuratedRecord(
        id=uuid.uuid4(), source_intake_id=a_id, source_record_id=recs["a"][2].id,
        record_status="HELD", status_reason="blocker", rce_org_oid=rows[1]["id"],
        transformation_version="t"))
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id="REV-9982-000001", entity_id=ent,
                            source_record_id=recs["a"][1].id, created_at=datetime.utcnow()))
    await db.flush()
    csv_text = "\n".join([
        "intake_id,agt_record_id,source_rce_id,analyst_note,assign_to_email",
        f"{a_id},{recs['a'][1].id},{rows[0]['id']},,qa1@example.invalid",      # has a case  -> accepted
        f"{a_id},{recs['a'][2].id},{rows[1]['id']},note for a HELD record,",   # HELD, no case -> unchanged
        f"{a_id},{recs['b'][1].id},,note,",                                    # other delivery -> rejected
        f"{a_id},{uuid.uuid4()},,note,",                                       # unknown        -> rejected
        f"{a_id},{recs['a'][1].id},{rows[0]['id']},,qa2@example.invalid",      # duplicate AGT -> needs_review
    ]).encode("utf-8")
    result = await cui.build_preview(
        "agt.csv", csv_text, purpose=cui.PURPOSE_CASE_UPDATES, worksheet=None,
        resolve=rr._case_identity_resolver(db), uploaded_by="pytest",
        validate_identity=rr._agt_identity_validator(db))
    pv = result["preview"]
    assert [r["disposition"] for r in pv["rows"]] == [
        cui.DISP_ACCEPTED, cui.DISP_UNCHANGED, cui.DISP_REJECTED, cui.DISP_REJECTED,
        cui.DISP_NEEDS_REVIEW]
    assert pv["reconciles"] and sum(pv["counts"].values()) == 5
    assert "another delivery" in pv["rows"][2]["reasons"][0]
    plan = cui.plan_apply(pv)
    assert [a["review_id"] for a in plan["assignments"]] == ["REV-9982-000001"]
    assert plan["staged_not_applied"] == []          # the HELD note was not staged for apply
    # nothing was written: the HELD record is still HELD, no new review row exists
    cur = (await db.execute(select(m.RceCuratedRecord.record_status).where(
        m.RceCuratedRecord.source_record_id == recs["a"][2].id))).scalar()
    assert cur == "HELD"
    n = (await db.execute(select(reg.ReviewRecord.review_id).where(
        reg.ReviewRecord.review_id.like("REV-9982-%")))).all()
    assert len(n) == 1
