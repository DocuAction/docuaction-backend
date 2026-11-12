"""Source-record lineage: the REAL classification writers, then the REAL loader.

`verify_and_classify` (bulk) and `run_review` (single-record) create the
ReviewRecord rows; `october_review_loader` reads them back by
`review_records.source_record_id`. These tests run both writers for real (real
rules, real classification, real ReviewRecord creation, real review-id
allocation) inside a rolled-back transaction, replacing ONLY the network edge:
evidence gathering for the bulk writer, and the external connector/USPS/AI probes
for the single-record writer. Then the loader reads what the writers wrote.

What must hold:
  * a new review is anchored to the delivered record it is about - taken from the
    NAMED delivery's own curated row, never from a shared entity;
  * the same entity delivered twice (two deliveries) never lends one delivery's
    result to the other;
  * several delivered records of one delivery each get their own review;
  * an ambiguous or contextless review stays UNANCHORED and the loader says so;
  * historical reviews with no anchor are never attached and never rewritten, and
    the record they would otherwise have filled stays explicitly incomplete.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.reports.data import october_review_loader as ld
from app.tefca_registry import models as reg
from app.tefca_registry import review_service as rs
from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import models as m
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, seed_entity, seed_intake,
)

T_RUN_START = datetime(2026, 11, 7, 8, 0, 0)
T_RUN_DONE = datetime(2026, 11, 8, 9, 0, 0)
T_HISTORIC = datetime(2026, 11, 1, 12, 0, 0)


def _cutoff():
    return datetime.utcnow() + timedelta(days=1)       # after every writer-created row


# ── seeding helpers ─────────────────────────────────────────────────────────

async def _run(db, intake_id):
    db.add(m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="test",
        rule_config_hash="0" * 64, started_at=T_RUN_START, completed_at=T_RUN_DONE,
        run_status="COMPLETE",
        assessment_metadata={"assessment_date": "2026-11-08", "source": "pinned_param"}))
    await db.flush()


async def _records(db, intake_id):
    return {r.line_number - 1: r for r in (await db.execute(
        select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}


async def _curate(db, intake_id, rec, entity_id, status="CLEAN"):
    db.add(m.RceCuratedRecord(
        id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id,
        record_status=status, rce_org_oid=rec.source_rce_id,
        transformation_version="test", canonical_entity_id=entity_id))
    await db.flush()


async def _entity(db, tag):
    return await seed_entity(db, oid=f"{SYN}-LIN-{tag}-{uuid.uuid4().hex[:8]}",
                             name=f"{SYN} lineage {tag}")


def _patch_bulk_gather(monkeypatch, entity_by_ref):
    """Replace only the network edge of the bulk writer."""
    async def fake(refs, timing_log=None):
        out = []
        for ref in refs:
            ent = entity_by_ref.get(ref)
            if ent is None:
                out.append({"ref": ref, "entity": None, "evidence": None})
                continue
            out.append({
                "ref": ref,
                "entity": {"id": ref, "name": f"entity {ref}",
                           "_registry_entity_id": ent, "identifier": [], "_rce": {},
                           "_resolution_source": "test"},
                "evidence": {"dimensions": [], "applicability": {}, "sufficiency": {},
                             "data_quality_flags": [],
                             "generated_at": "2026-11-08T12:00:00Z"}})
        return out
    monkeypatch.setattr(ap, "_gather_all_evidence", fake)


def _patch_single_edges(monkeypatch):
    """Replace only the external probes of the single-record writer."""
    async def sources(_db, _eid):
        return {"nppes": {"status": "verified", "label": "n"},
                "oig_leie": {"status": "clear", "label": "l"},
                "pecos": {"status": "verified", "label": "p"}}

    async def none(*_a, **_k):
        return None

    monkeypatch.setattr(rs, "probe_sources", sources)
    monkeypatch.setattr(rs, "_resolve_entity", none)
    monkeypatch.setattr(rs, "_compare_addresses", none)


async def _load(db, intake_id):
    return await ld.load_record_facts(db, intake_id=intake_id, evidence_cutoff=_cutoff())


def _by_line(out):
    return {f["source_line"] - 1: f for f in out["facts"]}


async def _two_deliveries_sharing_entities(db, arc):
    """A and B deliver the SAME two organisations (same business ids), both
    promoted onto the SAME two canonical entities."""
    rows = make_rows(2, arc=arc)
    a_id, _ = await seed_intake(db, rows, with_job=False)
    b_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, a_id)
    await _run(db, b_id)
    ra, rb = await _records(db, a_id), await _records(db, b_id)
    ents = {i: await _entity(db, f"s{i}") for i in (1, 2)}
    for intake, recs in ((a_id, ra), (b_id, rb)):
        for i in (1, 2):
            await _curate(db, intake, recs[i], ents[i])
    return rows, a_id, b_id, ra, rb, ents


async def _review_rows(db, recs):
    return (await db.execute(
        select(reg.ReviewRecord).where(
            reg.ReviewRecord.source_record_id.in_([r.id for r in recs])))).scalars().all()


# ── pure: the anchoring decision ────────────────────────────────────────────

def test_source_anchor_decision_is_conservative():
    a, ent, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    one = {"urn:x": [(a, ent)]}
    assert ap._source_anchor(one, "urn:x", ent) == (a, ap.LINEAGE_ANCHORED)
    assert ap._source_anchor(one, "urn:x", str(ent)) == (a, ap.LINEAGE_ANCHORED)  # str/UUID alike
    assert ap._source_anchor(one, "urn:x", other) == (None, ap.LINEAGE_ENTITY_MISMATCH)
    assert ap._source_anchor(one, "urn:x", None) == (None, ap.LINEAGE_ENTITY_MISMATCH)
    assert ap._source_anchor({"urn:x": [(a, None)]}, "urn:x", ent) == (None, ap.LINEAGE_ENTITY_MISMATCH)
    two = {"urn:x": [(a, ent), (uuid.uuid4(), ent)]}
    assert ap._source_anchor(two, "urn:x", ent) == (None, ap.LINEAGE_AMBIGUOUS)
    assert ap._source_anchor({}, "urn:x", ent) == (None, ap.LINEAGE_NO_DELIVERY_RECORD)


# ── bulk writer -> loader ───────────────────────────────────────────────────

async def test_bulk_writer_anchors_each_review_to_its_own_delivery_not_the_shared_entity(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    rows, a_id, b_id, ra, rb, ents = await _two_deliveries_sharing_entities(db, "9.99.777.90")
    refs = {rows[0]["id"]: ents[1], rows[1]["id"]: ents[2]}
    _patch_bulk_gather(monkeypatch, refs)

    # 1. review ONLY delivery A
    res_a = await ap.verify_and_classify(db, list(refs), intake_id=a_id)
    assert res_a["lineage"] == {ap.LINEAGE_ANCHORED: 2, ap.LINEAGE_AMBIGUOUS: 0,
                                ap.LINEAGE_ENTITY_MISMATCH: 0, ap.LINEAGE_NO_DELIVERY_RECORD: 0}
    assert {o["source_record_id"] for o in res_a["outcomes"]} == {str(ra[1].id), str(ra[2].id)}
    out_a, out_b = await _load(db, a_id), await _load(db, b_id)
    fa, fb = _by_line(out_a), _by_line(out_b)
    assert fa[1]["bucket"] is not None and fa[2]["bucket"] is not None     # A's own results
    assert fb[1]["bucket"] is None and fb[2]["bucket"] is None             # B never borrows them,
    assert ld.NO_ANCHORED_REVIEW in fb[1]["bucket_basis"]                  # though the entity is shared
    assert out_b["meta"]["stats"]["classified"] == 0
    # the shared entity carries A's reviews, and B must not claim them as unanchored either
    assert out_b["meta"]["stats"]["records_with_unanchored_entity_reviews"] == 0

    # 2. now review delivery B: B gets its OWN review rows; A's are unchanged
    a_review_ids = {f["evidence_reference"].split("review=")[1].split(";")[0] for f in out_a["facts"]}
    res_b = await ap.verify_and_classify(db, list(refs), intake_id=b_id)
    assert {o["source_record_id"] for o in res_b["outcomes"]} == {str(rb[1].id), str(rb[2].id)}
    out_a2, out_b2 = await _load(db, a_id), await _load(db, b_id)
    b_review_ids = {f["evidence_reference"].split("review=")[1].split(";")[0] for f in out_b2["facts"]}
    assert a_review_ids.isdisjoint(b_review_ids) and len(a_review_ids | b_review_ids) == 4
    assert {f["evidence_reference"].split("review=")[1].split(";")[0]
            for f in out_a2["facts"]} == a_review_ids                      # A untouched by B's run
    assert all(f["bucket"] is not None for f in out_b2["facts"])


async def test_bulk_writer_gives_every_delivered_record_of_one_delivery_its_own_review(
        rolled_back_db, monkeypatch):
    """Two delivered records resolve to ONE canonical entity: each record still
    gets its own anchored review, not one review shared by both."""
    db = rolled_back_db
    rows = make_rows(2, arc="9.99.777.91")
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    ent = await _entity(db, "multi")
    await _curate(db, intake_id, recs[1], ent)
    await _curate(db, intake_id, recs[2], ent)                  # SAME entity, other record
    refs = {rows[0]["id"]: ent, rows[1]["id"]: ent}
    _patch_bulk_gather(monkeypatch, refs)
    res = await ap.verify_and_classify(db, list(refs), intake_id=intake_id)
    assert res["lineage"][ap.LINEAGE_ANCHORED] == 2
    by_rec = {o["source_record_id"]: o["review_id"] for o in res["outcomes"]}
    assert set(by_rec) == {str(recs[1].id), str(recs[2].id)} and len(set(by_rec.values())) == 2
    facts = _by_line(await _load(db, intake_id))
    for i in (1, 2):
        assert f"review={by_rec[str(recs[i].id)]}" in facts[i]["evidence_reference"]
        assert facts[i]["bucket"] is not None


async def test_ambiguous_duplicate_delivered_id_stays_unanchored_and_the_loader_says_so(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.777.92")
    rows[2]["id"] = rows[0]["id"]                               # two records share one delivered id
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    ent_dup, ent_ok = await _entity(db, "dup"), await _entity(db, "ok")
    await _curate(db, intake_id, recs[1], ent_dup)
    await _curate(db, intake_id, recs[3], ent_dup)
    await _curate(db, intake_id, recs[2], ent_ok)
    refs = {rows[0]["id"]: ent_dup, rows[1]["id"]: ent_ok}
    _patch_bulk_gather(monkeypatch, refs)
    res = await ap.verify_and_classify(db, list(refs), intake_id=intake_id)
    assert res["lineage"][ap.LINEAGE_ANCHORED] == 1 and res["lineage"][ap.LINEAGE_AMBIGUOUS] == 1
    amb = [o for o in res["outcomes"] if o["lineage"] == ap.LINEAGE_AMBIGUOUS][0]
    assert amb["source_record_id"] is None                      # never guessed
    out = await _load(db, intake_id)
    f = _by_line(out)
    assert f[2]["bucket"] is not None                           # the unambiguous record is anchored
    for i in (1, 3):                                            # the ambiguous pair is NOT filled
        assert f[i]["bucket"] is None
        assert "carry no source-record anchor and were NOT attached" in f[i]["bucket_basis"]
    assert out["meta"]["stats"]["records_with_unanchored_entity_reviews"] == 2


async def test_bulk_writer_without_a_delivery_context_leaves_reviews_unanchored(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.93")
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    ent = await _entity(db, "noctx")
    await _curate(db, intake_id, rec, ent)
    _patch_bulk_gather(monkeypatch, {rows[0]["id"]: ent})
    res = await ap.verify_and_classify(db, [rows[0]["id"]])           # no intake_id
    assert res["lineage"][ap.LINEAGE_NO_DELIVERY_RECORD] == 1
    assert res["outcomes"][0]["source_record_id"] is None
    f = _by_line(await _load(db, intake_id))[1]
    assert f["bucket"] is None and "NOT attached" in f["bucket_basis"]


async def test_resolved_entity_that_differs_from_the_curated_link_is_not_anchored(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.94")
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    linked, resolved = await _entity(db, "linked"), await _entity(db, "resolved")
    await _curate(db, intake_id, rec, linked)
    _patch_bulk_gather(monkeypatch, {rows[0]["id"]: resolved})        # resolver says another entity
    res = await ap.verify_and_classify(db, [rows[0]["id"]], intake_id=intake_id)
    assert res["lineage"][ap.LINEAGE_ENTITY_MISMATCH] == 1
    assert res["outcomes"][0]["source_record_id"] is None


# ── historical reviews without an anchor ────────────────────────────────────

async def test_historical_unanchored_reviews_are_never_attached_or_rewritten(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.95")
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    ent = await _entity(db, "hist")
    await _curate(db, intake_id, rec, ent)
    for n in (1, 2):                                            # two pre-anchor reviews of the entity
        db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id=f"REV-9995-00000{n}", entity_id=ent,
                                classification_bucket="B1", created_at=T_HISTORIC))
    await db.flush()

    async def snapshot():
        return sorted((await db.execute(
            select(reg.ReviewRecord.review_id, reg.ReviewRecord.source_record_id,
                   reg.ReviewRecord.classification_bucket)
            .where(reg.ReviewRecord.review_id.like("REV-9995-%")))).all())

    before = await snapshot()
    out = await _load(db, intake_id)
    f = _by_line(out)[1]
    assert f["bucket"] is None                                  # NOT B1 from the historical rows
    assert "2 historical review(s)" in f["bucket_basis"] and "NOT attached" in f["bucket_basis"]
    assert out["meta"]["stats"]["records_with_unanchored_entity_reviews"] == 1
    dataset = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=_cutoff(),
                                    report_id="R")
    assert dataset["summary"]["complete"] is False
    assert dataset["summary"]["unclassified_non_held_ids"] == [str(rec.id)]
    assert await snapshot() == before                           # loader rewrote nothing

    # a NEW anchored review is added; the historical rows are still untouched
    _patch_bulk_gather(monkeypatch, {rows[0]["id"]: ent})
    await ap.verify_and_classify(db, [rows[0]["id"]], intake_id=intake_id)
    after = await snapshot()
    assert after == before                                      # historical rows: same ids, NULL anchor, same bucket
    assert all(sr is None for _rid, sr, _b in after)
    f2 = _by_line(await _load(db, intake_id))[1]
    assert f2["bucket"] is not None and f"source_record={rec.id}" in f2["evidence_reference"]
    complete = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=_cutoff(), report_id="R")
    assert complete["summary"]["complete"] is True


# ── single-record writer -> loader ──────────────────────────────────────────

async def test_single_record_writer_anchors_only_an_explicit_validated_record(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    rows, a_id, b_id, ra, rb, ents = await _two_deliveries_sharing_entities(db, "9.99.777.96")
    _patch_single_edges(monkeypatch)
    ent_row = await db.get(reg.TefcaRegEntity, ents[1])
    ent_row.source_record_id = ra[1].id          # the entity column names DELIVERY A's record
    await db.flush()

    # entity-only call (priority review / entity verify): UNANCHORED, even though
    # entity.source_record_id points at A's record - that column is never trusted
    plain = await rs.run_review(db, ent_row)
    assert plain["source_record_id"] is None
    stored = (await db.execute(select(reg.ReviewRecord.source_record_id).where(
        reg.ReviewRecord.review_id == plain["review_id"]))).scalar()
    assert stored is None
    fa = _by_line(await _load(db, a_id))[1]
    assert fa["bucket"] is None and "NOT attached" in fa["bucket_basis"]   # not lent to A

    # explicit anchor to B's record: anchored to B, invisible to A
    anchored = await rs.run_review(db, ent_row, source_record_id=rb[1].id)
    assert anchored["source_record_id"] == str(rb[1].id)
    fb, fa = _by_line(await _load(db, b_id))[1], _by_line(await _load(db, a_id))[1]
    assert fb["bucket"] is not None and f"review={anchored['review_id']}" in fb["evidence_reference"]
    assert fa["bucket"] is None
    # a string id is accepted as well
    again = await rs.run_review(db, ent_row, source_record_id=str(ra[1].id))
    assert again["source_record_id"] == str(ra[1].id)
    assert _by_line(await _load(db, a_id))[1]["bucket"] is not None


async def test_single_record_writer_refuses_a_record_that_is_not_the_entitys_and_writes_nothing(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    rows, a_id, b_id, ra, rb, ents = await _two_deliveries_sharing_entities(db, "9.99.777.97")
    _patch_single_edges(monkeypatch)
    ent1 = await db.get(reg.TefcaRegEntity, ents[1])

    async def count():
        return (await db.execute(select(func.count()).select_from(reg.ReviewRecord))).scalar()

    n0 = await count()
    with pytest.raises(ValueError, match="not linked to entity"):
        await rs.run_review(db, ent1, source_record_id=ra[2].id)       # entity 2's record
    with pytest.raises(ValueError, match="is not a UUID"):
        await rs.run_review(db, ent1, source_record_id="nope")
    with pytest.raises(ValueError, match="no curated record"):
        await rs.run_review(db, ent1, source_record_id=uuid.uuid4())
    assert await count() == n0                  # refused BEFORE any write
