"""Append-only, NON-DECISION record comments (rce_record_comments): service,
database guards and routes.

Database-backed (rolled-back transaction); listed in the PostgreSQL CI job, where
the migration chain has created the table. Fixture rows are synthetic.
"""
from __future__ import annotations

import ast
import inspect
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import record_comments as rc
from app.tefca_registry.rce import routes as rce_routes
from app.tefca_registry.rce.record_comment_models import RceRecordComment
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, seed_entity, seed_intake,
)

SHA = "ab" * 32
SHA2 = "cd" * 32
ACTOR = "qalead@synthetic-test.docuaction.invalid"


# ── helpers ──────────────────────────────────────────────────────────────────

async def _records(db, intake_id):
    return {r.line_number - 1: r for r in (await db.execute(
        select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}


def _entry(rec, kind="QA_ORIGINAL", body="a comment", **over):
    e = {"source_record_id": str(rec.id), "comment_kind": kind, "comment_text": body,
         "expected": {"source_line": rec.line_number, "hcid": rec.hcid, "tefca_id": rec.tefcaid},
         "source": {"file_name": "src.xlsx", "file_sha256": SHA, "sheet": "S1",
                    "column": "QA Comments", "row": rec.line_number}}
    if kind == "QA_IZAAN":
        e["author_attribution"] = "Izaan"
        e["attribution_basis"] = "owner designation of the source file"
    e.update(over)
    return e


async def _import(db, intake_id, entries, **kw):
    kw.setdefault("actor", ACTOR)
    return await rc.import_comments(db, intake_id=intake_id, entries=entries, **kw)


async def _seed(db, n=2, arc="9.99.779.10"):
    intake_id, _ = await seed_intake(db, make_rows(n, arc=arc), with_job=False)
    return intake_id, await _records(db, intake_id)


async def _counts(db, intake_id):
    async def n(model, col=None):
        return (await db.execute(select(func.count()).select_from(model))).scalar()
    cur = (await db.execute(select(m.RceCuratedRecord.record_status, func.count())
                            .where(m.RceCuratedRecord.source_intake_id == intake_id)
                            .group_by(m.RceCuratedRecord.record_status))).all()
    return {"reviews": await n(reg.ReviewRecord), "issues": await n(m.RceIssue),
            "curated": sorted(map(tuple, cur)),
            "bucketed": (await db.execute(select(func.count()).select_from(reg.ReviewRecord)
                                          .where(reg.ReviewRecord.classification_bucket.isnot(None)))).scalar()}


# ── pure validation (no database) ────────────────────────────────────────────

def _rec(**kw):
    base = dict(id=uuid.uuid4(), source_intake_id=uuid.uuid4(), line_number=7,
                hcid="urn:oid:1.2", tefcaid="T-1", parsed={})
    base.update(kw)
    return SimpleNamespace(**base)


def _val(entry, rec=None, intake=None):
    rec = rec or _rec()
    intake = intake or rec.source_intake_id
    base = _entry(rec)
    base.update(entry)
    return rc._validate_entry(base, rec, intake)


def test_valid_entry_normalises_without_touching_the_text():
    rec = _rec()
    body = "  keep\r\nthis  exactly\t — é =x  "
    v = _val({"comment_text": body}, rec)
    assert "problem" not in v and v["text"] == body                    # verbatim
    assert rc.text_sha256(v["text"]) == rc.text_sha256(body)


@pytest.mark.parametrize("over,needle", [
    ({"comment_kind": "OTHER"}, "comment_kind must be one of"),
    ({"comment_text": ""}, "empty"),
    ({"comment_text": "   \n "}, "empty"),
    ({"comment_text": None}, "empty"),
    ({"comment_text": "a\x00b"}, "NUL"),
    ({"source_record_id": "nope"}, "not a UUID"),
    ({"expected": {"source_line": 7, "hcid": "urn:oid:1.2"}}, "cross-check incomplete"),
    ({"expected": {}}, "cross-check incomplete"),
    ({"expected": {"source_line": "x", "hcid": "urn:oid:1.2", "tefca_id": "T-1"}}, "not an integer"),
    ({"expected": {"source_line": 8, "hcid": "urn:oid:1.2", "tefca_id": "T-1"}}, "cross-check failed"),
    ({"expected": {"source_line": 7, "hcid": "urn:oid:9", "tefca_id": "T-1"}}, "cross-check failed"),
    ({"expected": {"source_line": 7, "hcid": "urn:oid:1.2", "tefca_id": "T-9"}}, "cross-check failed"),
    ({"source": {"file_name": "", "file_sha256": SHA}}, "source.file_name"),
    ({"source": {"file_name": "f", "file_sha256": "short"}}, "64-hex"),
    ({"source": {"file_name": "f", "file_sha256": SHA, "row": "x"}}, "not an integer"),
    ({"authored_at": "2026-11-01T10:00:00"}, "no timezone"),
    ({"authored_at": "not a date"}, "not an ISO datetime"),
    ({"intake_id": str(uuid.uuid4())}, "names intake"),
])
def test_invalid_entries_are_rejected_with_a_reason(over, needle):
    v = _val(over)
    assert "problem" in v and needle in v["problem"], v


def test_izaan_kind_needs_an_attribution_but_other_kinds_do_not():
    rec = _rec()
    bare = _entry(rec, "QA_IZAAN")
    bare.pop("author_attribution"); bare.pop("attribution_basis")
    assert "attribution" in rc._validate_entry(bare, rec, rec.source_intake_id)["problem"]
    assert "problem" not in rc._validate_entry(_entry(rec, "QA_IZAAN"), rec, rec.source_intake_id)
    assert "problem" not in rc._validate_entry(_entry(rec, "QA_ORIGINAL"), rec, rec.source_intake_id)


def test_unknown_authored_time_stays_unknown_and_aware_time_is_kept():
    assert _val({})["authored_at"] is None
    v = _val({"authored_at": "2026-11-01T10:00:00+00:00"})
    assert v["authored_at"] == datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)


def test_a_record_of_another_delivery_or_no_record_is_rejected():
    rec = _rec()
    other = uuid.uuid4()
    assert "belongs to delivery" in rc._validate_entry(_entry(rec), rec, other)["problem"]
    assert "is not a source record" in rc._validate_entry(_entry(rec), None, rec.source_intake_id)["problem"]


def test_idempotency_key_is_stable_and_separates_records_kinds_text_and_versions():
    a, b = uuid.uuid4(), uuid.uuid4()
    k = rc.idempotency_key(a, "QA_ORIGINAL", "h")
    assert k == rc.idempotency_key(a, "QA_ORIGINAL", "h") and len(k) == 64
    assert len({k, rc.idempotency_key(b, "QA_ORIGINAL", "h"), rc.idempotency_key(a, "QA_IZAAN", "h"),
                rc.idempotency_key(a, "QA_ORIGINAL", "h2"),
                rc.idempotency_key(a, "QA_ORIGINAL", "h", uuid.uuid4())}) == 5


def test_the_service_can_only_reach_the_comment_table_not_decision_code():
    """Static guard: the service imports nothing that classifies, resolves, approves
    or releases anything - only the source-record models and the comment model."""
    tree = ast.parse(inspect.getsource(rc))
    full = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            full.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            full.update(a.name for a in node.names)
    app_imports = {x for x in full if x.startswith("app.")}
    assert app_imports, "expected the service to import its two model modules"
    allowed = ("app.tefca_registry.rce.models", "app.tefca_registry.rce.record_comment_models")
    assert all(x.startswith(allowed) for x in app_imports), sorted(app_imports)


async def test_without_the_migration_the_service_refuses_cleanly():
    class _NoTable:
        async def execute(self, *_a, **_k):
            return SimpleNamespace(scalar=lambda: None)
    with pytest.raises(rc.CommentStoreNotApplied, match="COMMENT_STORE_NOT_APPLIED"):
        await rc.import_comments(_NoTable(), intake_id=uuid.uuid4(), entries=[{}], actor=ACTOR)
    with pytest.raises(rc.CommentStoreNotApplied):
        await rc.list_comments(_NoTable(), intake_id=uuid.uuid4())


# ── import + readback (database) ─────────────────────────────────────────────

async def test_comments_are_stored_verbatim_with_full_provenance_and_read_back(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.11")
    body = "  Line one\r\nLine two,  \"quoted\" — é =cmd()  \n"
    out = await _import(db, intake_id, [_entry(recs[1], body=body, authored_at="2026-10-30T09:15:00+00:00",
                                               author_attribution="A. Reviewer",
                                               attribution_basis="named in the source cell")])
    assert out["counts"]["inserted"] == 1 and out["reconciles"]
    got = (await rc.list_comments(db, intake_id=intake_id))["comments"]
    assert len(got) == 1
    c = got[0]
    assert c["comment_text"] == body and c["text_sha256"] == rc.text_sha256(body)
    assert c["source_record_id"] == str(recs[1].id) and c["comment_kind"] == "QA_ORIGINAL"
    assert (c["source_file_name"], c["source_file_sha256"], c["source_sheet"],
            c["source_column"], c["source_row"]) == ("src.xlsx", SHA, "S1", "QA Comments", recs[1].line_number)
    assert c["author_attribution"] == "A. Reviewer" and c["attribution_basis"] == "named in the source cell"
    assert c["authored_at"].startswith("2026-10-30T09:15:00")
    assert c["imported_by"] == ACTOR                                   # the REAL actor
    assert c["imported_at"] and c["import_batch_id"] == out["batch_id"]
    assert c["identity_check"] == {"validated": True, "source_line": recs[1].line_number,
                                   "hcid": recs[1].hcid, "tefca_id": recs[1].tefcaid}
    assert c["non_decision"] is True and c["supersedes_comment_id"] is None


async def test_unknown_authored_time_is_stored_as_unknown(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.12")
    await _import(db, intake_id, [_entry(recs[1])])
    c = (await rc.list_comments(db, intake_id=intake_id))["comments"][0]
    assert c["authored_at"] is None and c["author_attribution"] is None


async def test_the_three_kinds_stay_separate_for_one_record(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.13")
    same = "identical wording in all three"
    out = await _import(db, intake_id, [
        _entry(recs[1], "QA_ORIGINAL", same), _entry(recs[1], "QA_IZAAN", same),
        _entry(recs[1], "REPORT_CLARIFICATION", same)])
    assert out["counts"]["inserted"] == 3                               # same text, three kinds, three rows
    for kind in ("QA_ORIGINAL", "QA_IZAAN", "REPORT_CLARIFICATION"):
        one = (await rc.list_comments(db, intake_id=intake_id, kind=kind))["comments"]
        assert [c["comment_kind"] for c in one] == [kind]
    izaan = (await rc.list_comments(db, intake_id=intake_id, kind="QA_IZAAN"))["comments"][0]
    assert izaan["author_attribution"] == "Izaan"


async def test_identity_is_strict_and_rejects_never_attach_or_invent(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(2, arc="9.99.779.14")
    a_id, _ = await seed_intake(db, rows, with_job=False)
    b_id, _ = await seed_intake(db, rows, with_job=False)               # same business ids, other delivery
    ra, rb = await _records(db, a_id), await _records(db, b_id)
    bad_hcid = _entry(ra[1]); bad_hcid["expected"] = {**bad_hcid["expected"], "hcid": "urn:oid:wrong"}
    name_only = {"comment_kind": "QA_ORIGINAL", "comment_text": "found by name",
                 "organization": rows[0]["name"], "source": {"file_name": "f", "file_sha256": SHA}}
    out = await _import(db, a_id, [
        _entry(rb[1]),                                                  # B's record under A
        _entry(ra[1], source_record_id=str(uuid.uuid4())),              # unknown record
        _entry(ra[1], source_record_id="not-a-uuid"),
        bad_hcid, name_only,
        _entry(ra[1], expected={"source_line": ra[1].line_number}),     # cross-checks missing
        _entry(ra[2], intake_id=str(b_id))])                            # entry names another delivery
    assert out["counts"]["rejected"] == 7 and out["counts"]["inserted"] == 0 and out["reconciles"]
    reasons = " | ".join(r["reason"] for r in out["results"])
    assert "belongs to delivery" in reasons and "cross-check failed" in reasons
    assert "cross-check incomplete" in reasons and "not a UUID" in reasons
    n = (await db.execute(select(func.count()).select_from(RceRecordComment))).scalar()
    assert n == 0                                                       # nothing written, nothing invented


async def test_held_and_unpromoted_records_take_comments_without_any_review_row(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, n=3, arc="9.99.779.15")
    db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id,
                              source_record_id=recs[1].id, record_status="HELD",
                              status_reason="blocker", rce_org_oid=recs[1].source_rce_id,
                              transformation_version="t"))
    await db.flush()                                                    # recs[2], recs[3]: no curated row
    before = await _counts(db, intake_id)
    out = await _import(db, intake_id, [_entry(recs[1]), _entry(recs[2], "QA_IZAAN"),
                                        _entry(recs[3], "REPORT_CLARIFICATION")])
    assert out["counts"]["inserted"] == 3
    after = await _counts(db, intake_id)
    assert after == before                                              # no review, no bucket, still HELD
    assert before["reviews"] == 0 and before["bucketed"] == 0 and ("HELD", 1) in before["curated"]


async def test_duplicates_in_a_batch_and_on_resend_are_already_present(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.16")
    e = _entry(recs[1], body="same words")
    first = await _import(db, intake_id, [e, e])
    assert [r["status"] for r in first["results"]] == ["inserted", "already_present"]
    again = await _import(db, intake_id, [e], batch_id=first["batch_id"])   # resumed batch
    assert again["counts"]["already_present"] == 1 and again["counts"]["inserted"] == 0
    other_file = _entry(recs[1], body="same words",
                        source={"file_name": "other.xlsx", "file_sha256": SHA2, "sheet": "x",
                                "column": "y", "row": 1})
    dup = await _import(db, intake_id, [other_file])
    assert dup["counts"]["already_present"] == 1                         # same verbatim comment, other file
    n = (await db.execute(select(func.count()).select_from(RceRecordComment))).scalar()
    assert n == 1


async def test_amendment_is_explicit_append_only_and_revertible(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.17")
    r = recs[1]
    v1 = await _import(db, intake_id, [_entry(r, body="first wording")])
    v1_id = v1["results"][0]["comment_id"]
    # a DIFFERENT comment of the same kind, no amend flag: needs review, nothing written
    held = await _import(db, intake_id, [_entry(r, body="second wording")])
    assert held["counts"]["needs_review"] == 1 and "amend=true" in held["results"][0]["reason"]
    assert (await db.execute(select(func.count()).select_from(RceRecordComment))).scalar() == 1
    # explicit amendment: a NEW row superseding v1
    v2 = await _import(db, intake_id, [_entry(r, body="second wording", amend=True)])
    assert v2["counts"]["amended"] == 1
    cur = (await rc.list_comments(db, intake_id=intake_id))["comments"]
    assert [c["comment_text"] for c in cur] == ["second wording"]
    assert cur[0]["supersedes_comment_id"] == v1_id
    allv = (await rc.list_comments(db, intake_id=intake_id, current_only=False))["comments"]
    assert [c["comment_text"] for c in allv] == ["first wording", "second wording"]   # history kept
    # reverting to the earlier text is an amendment too (not "already present")
    v3 = await _import(db, intake_id, [_entry(r, body="first wording", amend=True)])
    assert v3["counts"]["amended"] == 1
    cur3 = (await rc.list_comments(db, intake_id=intake_id))["comments"]
    assert [c["comment_text"] for c in cur3] == ["first wording"]
    assert len((await rc.list_comments(db, intake_id=intake_id, current_only=False))["comments"]) == 3
    # amending to the text that is already current is a no-op
    again = await _import(db, intake_id, [_entry(r, body="first wording", amend=True)])
    assert again["counts"]["already_present"] == 1


async def test_dry_run_predicts_without_writing_and_matches_the_real_run(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.18")
    entries = [_entry(recs[1], body="x"), _entry(recs[2], body="y"), _entry(recs[1], body="x"),
               _entry(recs[2], source_record_id="nope")]
    dry = await _import(db, intake_id, entries, dry_run=True)
    assert (await db.execute(select(func.count()).select_from(RceRecordComment))).scalar() == 0
    real = await _import(db, intake_id, entries)
    assert dry["counts"] == real["counts"]
    assert [r["status"] for r in dry["results"]] == [r["status"] for r in real["results"]]


async def test_batches_are_bounded_and_the_call_is_refused_not_truncated(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.19")
    with pytest.raises(rc.CommentRefused, match="bounded batch size"):
        await _import(db, intake_id, [_entry(recs[1])] * (rc.MAX_BATCH + 1))
    with pytest.raises(rc.CommentRefused, match="non-empty"):
        await _import(db, intake_id, [])
    with pytest.raises(rc.CommentRefused, match="actor"):
        await _import(db, intake_id, [_entry(recs[1])], actor="  ")
    with pytest.raises(rc.CommentRefused, match="No delivery"):
        await _import(db, uuid.uuid4(), [_entry(recs[1])])
    with pytest.raises(rc.CommentRefused, match="not a UUID"):
        await _import(db, "x", [_entry(recs[1])])
    with pytest.raises(rc.CommentRefused, match="batch_id"):
        await _import(db, intake_id, [_entry(recs[1])], batch_id="nope")
    assert (await db.execute(select(func.count()).select_from(RceRecordComment))).scalar() == 0


async def test_readback_is_scoped_to_the_named_delivery_and_pages_completely(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(5, arc="9.99.779.20")
    a_id, _ = await seed_intake(db, rows, with_job=False)
    b_id, _ = await seed_intake(db, rows[:1], with_job=False)
    ra, rb = await _records(db, a_id), await _records(db, b_id)
    await _import(db, a_id, [_entry(ra[i], body=f"a{i}") for i in range(1, 6)])
    await _import(db, b_id, [_entry(rb[1], body="b1")])
    seen, token = [], None
    while True:
        page = await rc.list_comments(db, intake_id=a_id, limit=2, after=token)
        seen += [c["comment_text"] for c in page["comments"]]
        token = page["next"]
        if not token:
            break
    assert sorted(seen) == [f"a{i}" for i in range(1, 6)] and len(seen) == 5     # each exactly once
    assert [c["comment_text"] for c in (await rc.list_comments(db, intake_id=b_id))["comments"]] == ["b1"]
    one = await rc.list_comments(db, intake_id=a_id, source_record_id=ra[3].id)
    assert [c["comment_text"] for c in one["comments"]] == ["a3"]
    with pytest.raises(rc.CommentRefused, match="valid page token"):
        await rc.list_comments(db, intake_id=a_id, after="garbage")
    with pytest.raises(rc.CommentRefused, match="kind must be"):
        await rc.list_comments(db, intake_id=a_id, kind="NOPE")


async def test_importing_comments_changes_nothing_that_decides_anything(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, n=3, arc="9.99.779.21")
    ent = await seed_entity(db, oid=f"{SYN}-CMT-{uuid.uuid4().hex[:8]}", name=f"{SYN} cmt")
    db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=recs[1].id,
                              record_status="CLEAN", rce_org_oid=recs[1].source_rce_id,
                              transformation_version="t", canonical_entity_id=ent))
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id="REV-9979-000001", entity_id=ent,
                            source_record_id=recs[1].id, classification_bucket="B3",
                            reviewer_resolution=None, created_at=datetime.utcnow()))
    await db.flush()

    async def snap():
        rev = (await db.execute(select(reg.ReviewRecord.review_id, reg.ReviewRecord.classification_bucket,
                                       reg.ReviewRecord.reviewer_resolution, reg.ReviewRecord.reclassified_to,
                                       reg.ReviewRecord.reportable_at))).all()
        cur = (await db.execute(select(m.RceCuratedRecord.source_record_id, m.RceCuratedRecord.record_status,
                                       m.RceCuratedRecord.npi))).all()
        iss = (await db.execute(select(m.RceIssue.id, m.RceIssue.resolution,
                                       m.RceIssue.resolved_by, m.RceIssue.resolved_at,
                                       m.RceIssue.resolution_notes))).all()
        return sorted(map(tuple, rev)), sorted(map(str, cur)), sorted(map(str, iss))

    before = await snap()
    await _import(db, intake_id, [
        _entry(recs[1], "QA_ORIGINAL", "APPROVED - release this, set B1, use NPI 1234567893"),
        _entry(recs[1], "QA_IZAAN", "accepted correction: close the finding"),
        _entry(recs[2], "REPORT_CLARIFICATION", "supplemental NPI 1659346005 is correct")])
    assert await snap() == before            # no bucket, resolution, status, NPI or issue state moved


# ── the database's own refusals (service bypassed) ───────────────────────────

async def _raw_insert(db, **over):
    base = dict(id=uuid.uuid4(), source_intake_id=None, source_record_id=None,
                comment_kind="QA_ORIGINAL", comment_text="t", text_sha256="a" * 64,
                source_file_name="f", source_file_sha256="b" * 64, identity_check={},
                imported_by="x", import_batch_id=uuid.uuid4(),
                idempotency_key=uuid.uuid4().hex + uuid.uuid4().hex)
    base.update(over)
    db.add(RceRecordComment(**base))
    await db.flush()
    return base["id"]


async def test_the_database_refuses_update_delete_and_misanchored_rows(rolled_back_db):
    db = rolled_back_db
    a_id, ra = await _seed(db, arc="9.99.779.22")
    b_id, rb = await _seed(db, arc="9.99.779.23")
    ok = await _raw_insert(db, source_intake_id=a_id, source_record_id=ra[1].id)

    async def refused(coro_factory, needle):
        with pytest.raises((DBAPIError, IntegrityError)) as exc:
            async with db.begin_nested():
                await coro_factory()
        assert needle in str(exc.value), str(exc.value)[:200]

    await refused(lambda: db.execute(text("UPDATE rce_record_comments SET comment_text='x' WHERE id=:i"),
                                     {"i": ok}), "append-only")
    await refused(lambda: db.execute(text("DELETE FROM rce_record_comments WHERE id=:i"), {"i": ok}),
                  "append-only")
    # record not in the named intake
    await refused(lambda: _raw_insert(db, source_intake_id=a_id, source_record_id=rb[1].id),
                  "does not belong to intake")
    # amendment must keep record and kind
    await refused(lambda: _raw_insert(db, source_intake_id=a_id, source_record_id=ra[2].id,
                                      supersedes_comment_id=ok), "same source record and kind")
    await refused(lambda: _raw_insert(db, source_intake_id=a_id, source_record_id=ra[1].id,
                                      comment_kind="QA_IZAAN", supersedes_comment_id=ok),
                  "same source record and kind")
    # constraints: non-decision, kind, text, hashes
    await refused(lambda: _raw_insert(db, source_intake_id=a_id, source_record_id=ra[1].id,
                                      non_decision=False), "ck_rce_record_comment_not_a_decision")
    await refused(lambda: _raw_insert(db, source_intake_id=a_id, source_record_id=ra[1].id,
                                      comment_kind="DECISION"), "ck_rce_record_comment_kind")
    await refused(lambda: _raw_insert(db, source_intake_id=a_id, source_record_id=ra[1].id,
                                      comment_text=""), "ck_rce_record_comment_text")
    await refused(lambda: _raw_insert(db, source_intake_id=a_id, source_record_id=ra[1].id,
                                      text_sha256="short"), "ck_rce_record_comment_hashes")
    # duplicate idempotency key; a predecessor can have only ONE successor
    key = uuid.uuid4().hex + uuid.uuid4().hex
    await _raw_insert(db, source_intake_id=a_id, source_record_id=ra[1].id, idempotency_key=key,
                      supersedes_comment_id=ok)
    await refused(lambda: _raw_insert(db, source_intake_id=a_id, source_record_id=ra[1].id,
                                      idempotency_key=key), "uq_rce_record_comment_idem")
    await refused(lambda: _raw_insert(db, source_intake_id=a_id, source_record_id=ra[1].id,
                                      supersedes_comment_id=ok), "uq_rce_record_comment_successor")


async def test_the_application_role_can_only_select_and_insert(rolled_back_db):
    db = rolled_back_db
    role = os.environ.get("DB_APP_ROLE", "docuaction_app")
    exists = (await db.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": role})).scalar()
    if not exists:
        pytest.skip(f"application role {role!r} does not exist in this database")
    privs = {p: (await db.execute(text(
        "SELECT has_table_privilege(:r, 'rce_record_comments', :p)"), {"r": role, "p": p})).scalar()
        for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    assert privs == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False}


# ── routes ───────────────────────────────────────────────────────────────────

USER = SimpleNamespace(email=ACTOR, id=None)


def test_comment_routes_are_gated_at_the_intended_roles():
    def gate(path, method):
        r = next(x for x in rce_routes.router.routes
                 if getattr(x, "path", "").endswith(path) and method in x.methods)
        calls = [d.call for d in r.dependant.dependencies]
        mins = {getattr(c, "minimum_role", None) for c in calls} - {None}
        if not mins:                       # plain require_role closure: read its bound role
            mins = {cell.cell_contents for c in calls for cell in (c.__closure__ or ())
                    if isinstance(cell.cell_contents, str)}
        return mins
    assert "qalead" in gate("/comments/preview", "POST")
    assert "qalead" in gate("/comments/import", "POST")
    assert "reviewer" in gate("/comments", "GET")


def test_anonymous_callers_are_refused_on_all_three_comment_routes(client):
    iid = str(uuid.uuid4())
    body = {"entries": [], "confirm": True}
    for method, path in (("post", f"/api/tefca/rce/deliveries/{iid}/comments/preview"),
                         ("post", f"/api/tefca/rce/deliveries/{iid}/comments/import"),
                         ("get", f"/api/tefca/rce/deliveries/{iid}/comments")):
        resp = getattr(client, method)(path, **({"json": body} if method == "post" else {}))
        assert resp.status_code in (401, 403), (path, resp.status_code)


async def test_preview_writes_nothing_import_needs_confirm_and_audit_holds_counts_not_text(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.24")
    secret = "PRIVATE reviewer wording that must not appear in the audit trail"
    req = rce_routes.RecordCommentImportRequest(entries=[_entry(recs[1], body=secret)])
    prev = await rce_routes.preview_record_comments(str(intake_id), req, db=db, user=USER)
    assert prev["dry_run"] is True and prev["counts"]["inserted"] == 1
    assert (await db.execute(select(func.count()).select_from(RceRecordComment))).scalar() == 0
    with pytest.raises(HTTPException) as exc:                                    # no confirm
        await rce_routes.import_record_comments(str(intake_id), req, db=db, user=USER)
    assert exc.value.status_code == 409 and "confirm=true" in exc.value.detail
    req.confirm = True
    res = await rce_routes.import_record_comments(str(intake_id), req, db=db, user=USER)
    assert res["counts"]["inserted"] == 1 and res["dry_run"] is False
    read = await rce_routes.read_record_comments(str(intake_id), source_record_id=None, kind=None,
                                                 current_only=True, limit=50, after=None, db=db, user=USER)
    assert [c["comment_text"] for c in read["comments"]] == [secret]             # verbatim readback
    audits = (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action.in_(("record_comments_previewed", "record_comments_imported"))))
    ).scalars().all()
    assert {a.action for a in audits} == {"record_comments_previewed", "record_comments_imported"}
    assert all(secret not in str(a.metadata_) for a in audits)                    # counts only, never text
    assert all("counts" in a.metadata_ for a in audits)


async def test_routes_translate_refusals_to_409(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.25")
    big = rce_routes.RecordCommentImportRequest(entries=[_entry(recs[1])] * (rc.MAX_BATCH + 1), confirm=True)
    with pytest.raises(HTTPException) as exc:
        await rce_routes.import_record_comments(str(intake_id), big, db=db, user=USER)
    assert exc.value.status_code == 409 and "bounded batch size" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await rce_routes.read_record_comments("not-a-uuid", source_record_id=None, kind=None,
                                              current_only=True, limit=10, after=None, db=db, user=USER)
    assert exc.value.status_code == 409


# ── review findings fixed 2026-11-12 ─────────────────────────────────────────

def test_an_oversized_comment_is_rejected_never_truncated():
    rec = _rec()
    ok = _val({"comment_text": "x" * rc.MAX_COMMENT_CHARS}, rec)
    assert "problem" not in ok and len(ok["text"]) == rc.MAX_COMMENT_CHARS
    big = _val({"comment_text": "x" * (rc.MAX_COMMENT_CHARS + 1)}, rec)
    assert "over the" in big["problem"] and "never truncated" in big["problem"]


async def test_a_conflicting_concurrent_write_is_needs_review_not_already_present(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.30")
    real_flush = db.flush
    state = {"n": 0}

    async def conflicting_flush(*a, **k):
        state["n"] += 1
        raise IntegrityError("INSERT", {}, Exception("uq_rce_record_comment_successor"))

    monkeypatch.setattr(db, "flush", conflicting_flush)
    out = await _import(db, intake_id, [_entry(recs[1], body="raced")])
    monkeypatch.setattr(db, "flush", real_flush)
    assert state["n"] == 1
    assert out["counts"]["needs_review"] == 1 and out["counts"]["already_present"] == 0
    assert "concurrent change conflicts" in out["results"][0]["reason"]
    assert (await db.execute(select(func.count()).select_from(RceRecordComment))).scalar() == 0


async def test_the_importing_actor_is_the_authenticated_user_never_a_placeholder(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, arc="9.99.779.31")
    req = rce_routes.RecordCommentImportRequest(entries=[_entry(recs[1])], confirm=True)
    anonymous = SimpleNamespace(email=None, id=None)
    with pytest.raises(HTTPException) as exc:
        await rce_routes.import_record_comments(str(intake_id), req, db=db, user=anonymous)
    assert exc.value.status_code == 409 and "actor could not be determined" in exc.value.detail
    with pytest.raises(HTTPException):
        await rce_routes.preview_record_comments(str(intake_id), req, db=db, user=anonymous)
    assert (await db.execute(select(func.count()).select_from(RceRecordComment))).scalar() == 0
    by_id = SimpleNamespace(email="  ", id=uuid.UUID(int=7))                   # blank email -> user id
    res = await rce_routes.import_record_comments(str(intake_id), req, db=db, user=by_id)
    assert res["counts"]["inserted"] == 1
    stored = (await rc.list_comments(db, intake_id=intake_id))["comments"][0]
    assert stored["imported_by"] == str(uuid.UUID(int=7)) and stored["imported_by"] != "unknown"
