"""Record-review dataset: NPPES and CMS PPEF enrolment cells are a record's result only when the NPI stored on
the evidence row equals the record's submitted NPI (2026-11-25, loader 1.5.0).

Evidence is stored under the canonical ENTITY, which other deliveries and coverage / recheck runs share. Before
this change the loader showed the entity's newest NPPES / CMS row in every linked record's cell, whatever NPI it
answered. These tests pin: a different NPI's answer is never shown as the record's; a row that stores no NPI is
quoted as stored and labelled as the entity's; an attributed row from an earlier check is kept and labelled with
its observation date; nothing after the cutoff is read; no bucket depends on any of it.

Database-backed (rolled-back transaction); every row is synthetic.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select

from app.Tefca.models import TEFCADimensionEvidence as E
from app.reports.data import october_review_dataset as ds_mod
from app.reports.data import october_review_loader as ld
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, seed_entity, seed_intake,
)

NPI_A, NPI_B = "1234567893", "1659346005"
T_RUN_START = datetime(2026, 11, 7, 8, 0, 0)
T_RUN_DONE = datetime(2026, 11, 8, 9, 0, 0)
T_BEFORE_RECEIPT = datetime(2026, 10, 2, 10, 0, 0)
T_RECEIVED = datetime(2026, 11, 1, 12, 0, 0)
T_OLD = datetime(2026, 11, 8, 10, 0, 0)
T_NEW = datetime(2026, 11, 8, 18, 0, 0)
CUTOFF = datetime(2026, 11, 9, 0, 0, 0)
T_AFTER_CUTOFF = datetime(2026, 11, 10, 9, 0, 0)
NPPES = ("NPPES", "IDENTITY")
CMS = ("CMS_PPEF_ENROLLMENT", "MEDICARE_ENROLLMENT")
CMS_API_KEY = "2457ea29-fc82-48b0-86ec-3b0755de7515:ENROLLMENT:NPI={npi}"
HDR = ds_mod.RECORD_COLUMNS


async def _delivery(db, arc, npis, *, received=T_RECEIVED):
    """One delivery whose records submit `npis` (in order), received at `received`, with a completed run."""
    rows = make_rows(len(npis), arc=arc)
    for row, npi in zip(rows, npis):
        row["NPI"] = npi
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    intake = await db.get(m.RceSourceIntake, intake_id)
    intake.received_at = received
    db.add(m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="test", rule_config_hash="0" * 64,
        started_at=T_RUN_START, completed_at=T_RUN_DONE, run_status="COMPLETE",
        assessment_metadata={"assessment_date": "2026-11-08", "source": "pinned_param"}))
    await db.flush()
    recs = {r.line_number - 1: r for r in (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}
    return intake_id, recs


async def _link(db, intake_id, rec, entity_id, *, bucket="B4", review_id=None, when=T_OLD):
    db.add(m.RceCuratedRecord(
        id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id, record_status="CLEAN",
        rce_org_oid=rec.source_rce_id, transformation_version="test", canonical_entity_id=entity_id))
    review_id = review_id or f"REV-{uuid.uuid4().int % 10**4:04d}-{uuid.uuid4().int % 10**6:06d}"
    db.add(reg.ReviewRecord(
        id=uuid.uuid4(), review_id=review_id, entity_id=entity_id, source_record_id=rec.id,
        classification_bucket=bucket, classification_rule="RULE-006", classification_rule_version=2,
        created_at=when))
    await db.flush()
    return review_id


async def _ev(db, entity_id, pair, disposition, created, *, answer_npi=None, query_identifier=None,
              records=None, review_id=None, note=None):
    """An evidence row shaped as the writers store it: NPPES puts the answer's NPI in
    `source_record_identifier` and `original_values.npi`; CMS stores a dataset / snapshot key and the records."""
    source, dimension = pair
    original = {}
    if source == "NPPES" and answer_npi:
        original = {"npi": answer_npi, "enumeration_type": "NPI-2"}
    if records is not None:
        original = {"records": records, "record_count": len(records)}
    row = E(id=uuid.uuid4(), entity_id=str(entity_id), evidence_dimension=dimension, source=source,
            disposition=disposition, original_values=original, normalized_values={}, note=note,
            source_record_identifier=answer_npi if source == "NPPES" else None,
            query_identifier=query_identifier, review_id=review_id,
            retrieved_at=created.date().isoformat(), generation_timestamp=f"gen-{created.isoformat()}",
            created_at=created)
    db.add(row)
    await db.flush()
    return row


async def _entity(db, tag):
    return await seed_entity(db, oid=f"{SYN}-ATT-{tag}-{uuid.uuid4().hex[:8]}", name=f"{SYN} attribution {tag}")


async def _facts(db, intake_id, cutoff=CUTOFF):
    out = await ld.load_record_facts(db, intake_id=intake_id, evidence_cutoff=cutoff)
    return {f["source_line"] - 1: f for f in out["facts"]}, out


# ── what a stored row says about the identifier (pure; exactly what the writers store) ─────────────────────────

def _row(**kw):
    base = dict(query_identifier=None, source_record_identifier=None, original_values={})
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_npi_is_read_only_from_what_the_row_stores():
    f = ld._stored_evidence_npis
    assert f("NPPES", _row(source_record_identifier=NPI_A))[0] == {NPI_A}
    assert f("NPPES", _row(original_values={"npi": NPI_A}))[0] == {NPI_A}
    assert f("NPPES", _row(query_identifier=f"npi={NPI_B}", source_record_identifier=NPI_A))[0] == {NPI_B}
    assert f("NPPES", _row())[0] == set()                                   # UNAVAILABLE rows store nothing
    assert f("CMS_PPEF_ENROLLMENT", _row(query_identifier=CMS_API_KEY.format(npi=NPI_A)))[0] == {NPI_A}
    assert f("CMS_PPEF_ENROLLMENT", _row(query_identifier="snapshot:abc",
                                         original_values={"records": [{"npi": NPI_A}, {"npi": NPI_A}]}))[0] == {NPI_A}
    # a snapshot not-found stores a snapshot key and no record: nothing says which NPI was asked about
    assert f("CMS_PPEF_ENROLLMENT", _row(query_identifier="snapshot:abc", original_values={"records": []}))[0] == set()
    assert f("CMS_PPEF_ENROLLMENT", _row(query_identifier="snapshot:abc"))[0] == set()


def test_attribution_needs_the_stored_npi_to_equal_the_submitted_npi_exactly():
    a = ld._answer_attribution
    assert a("NPPES", _row(source_record_identifier=NPI_A), NPI_A)[0] == "own"
    assert a("NPPES", _row(source_record_identifier=NPI_B), NPI_A)[:2] == (
        "foreign", "a different NPI than this record's submitted NPI")
    assert a("NPPES", _row(source_record_identifier=NPI_B), "")[:2] == (
        "foreign", "an NPI although this record's submitted NPI is blank")
    assert a("NPPES", _row(), NPI_A)[0] == "unrecorded"
    # surrounding whitespace is ignored; any other formatting difference is NOT normalised away (exact compare)
    assert a("NPPES", _row(source_record_identifier=f" {NPI_A} "), f"{NPI_A} ")[0] == "own"
    assert a("NPPES", _row(source_record_identifier=NPI_A), f"{NPI_A[:5]}-{NPI_A[5:]}")[0] == "foreign"
    # two different NPIs on the stored enrolment records never establish one record's NPI
    mixed = _row(query_identifier="snapshot:abc", original_values={"records": [{"npi": NPI_A}, {"npi": NPI_B}]})
    assert a("CMS_PPEF_ENROLLMENT", mixed, NPI_A)[0] == "foreign"


# ── NPPES ──────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_an_nppes_answer_for_another_npi_is_never_shown_as_this_records(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _delivery(db, "9.99.778.10", [NPI_A, ""])
    ent = await _entity(db, "n1")
    await _link(db, intake_id, recs[1], ent)
    await _link(db, intake_id, recs[2], ent)
    foreign = await _ev(db, ent, NPPES, "PASS", T_OLD, answer_npi=NPI_B)      # the entity's newest NPPES row
    facts, out = await _facts(db, intake_id)
    for line, why in ((1, "a different NPI than this record's submitted NPI"),
                      (2, "an NPI although this record's submitted NPI is blank")):
        cell = facts[line]["nppes"]
        assert cell.startswith("Not attributed to this record") and why in cell
        assert "PASS" not in cell and NPI_B not in cell                       # neither the result nor the other NPI
        assert "observed 2026-11-08" in cell
        # not silently dropped: the row is traceable, marked as not this record's
        assert f"NPPES:{foreign.id}@" in facts[line]["evidence_reference"]
        assert "(not attributed to this record: other identifier)" in facts[line]["evidence_reference"]
        assert "entity_type" not in facts[line]                               # no Entity_Type from another NPI
    assert out["meta"]["stats"]["evidence_rows_not_attributed"] == 2
    assert facts[2]["submitted_npi"] == ""


async def test_the_records_own_older_nppes_answer_is_used_instead_of_a_newer_one_for_another_npi(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _delivery(db, "9.99.778.11", [NPI_A])
    ent = await _entity(db, "n2")
    await _link(db, intake_id, recs[1], ent)
    own = await _ev(db, ent, NPPES, "REVIEW", T_OLD, answer_npi=NPI_A)
    other = await _ev(db, ent, NPPES, "PASS", T_NEW, answer_npi=NPI_B)
    facts, out = await _facts(db, intake_id)
    assert facts[1]["nppes"].startswith("REVIEW")                             # its own answer, not the newer PASS
    assert f"NPPES:{own.id}@" in facts[1]["evidence_reference"]
    assert str(other.id) not in facts[1]["evidence_reference"]
    assert out["meta"]["stats"]["evidence_rows_reselected_for_record"] == 1
    assert facts[1]["entity_type"] == "NPI-2"                                 # the established submitted-NPI link


async def test_two_deliveries_sharing_an_entity_each_show_only_their_own_npis_nppes_and_cms_answer(rolled_back_db):
    db = rolled_back_db
    a_id, ra = await _delivery(db, "9.99.778.12", [NPI_A])
    b_id, rb = await _delivery(db, "9.99.778.13", [NPI_B])
    shared = await _entity(db, "n3")
    await _link(db, a_id, ra[1], shared)
    await _link(db, b_id, rb[1], shared)
    await _ev(db, shared, NPPES, "REVIEW", T_OLD, answer_npi=NPI_A)
    await _ev(db, shared, NPPES, "PASS", T_NEW, answer_npi=NPI_B)
    await _ev(db, shared, CMS, "NOT_FOUND", T_OLD, query_identifier=CMS_API_KEY.format(npi=NPI_A))
    await _ev(db, shared, CMS, "PASS", T_NEW, query_identifier="snapshot:" + str(uuid.uuid4()),
              records=[{"npi": NPI_B, "enrollment_id": "SYNTHETIC-ENRL-1"}])
    fa, fb = (await _facts(db, a_id))[0][1], (await _facts(db, b_id))[0][1]
    assert fa["nppes"].startswith("REVIEW") and fb["nppes"].startswith("PASS")
    assert fa["pecos"].startswith("NOT_FOUND") and fb["pecos"].startswith("PASS")


async def test_an_nppes_row_that_stores_no_npi_is_quoted_as_stored_and_labelled_not_the_records(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _delivery(db, "9.99.778.14", [NPI_A])
    ent = await _entity(db, "n4")
    await _link(db, intake_id, recs[1], ent)
    await _ev(db, ent, NPPES, "PASS", T_OLD, answer_npi=NPI_A)                # an older answer of its own
    newest = await _ev(db, ent, NPPES, "UNAVAILABLE", T_NEW)                  # the newest stores no NPI
    facts, out = await _facts(db, intake_id)
    cell = facts[1]["nppes"]
    assert cell.startswith("Not attributed to this record")
    assert "does not record the identifier it was queried by" in cell
    assert "NOT established as this record's (observed 2026-11-08): UNAVAILABLE" in cell
    assert not cell.startswith("PASS") and "PASS" not in cell                 # no clearance from the older row
    assert f"NPPES:{newest.id}@" in facts[1]["evidence_reference"]
    assert "(not attributed to this record: no identifier recorded)" in facts[1]["evidence_reference"]
    assert out["meta"]["stats"]["evidence_rows_identifier_not_recorded"] == 1
    assert out["meta"]["stats"]["evidence_rows_not_attributed"] == 0


# ── CMS PPEF enrolment ─────────────────────────────────────────────────────────────────────────────────────────

async def test_cms_enrolment_is_attributed_by_the_recorded_query_filter_or_the_stored_records_npi(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _delivery(db, "9.99.778.15", [NPI_A, NPI_A, NPI_A, NPI_A])
    ents = [await _entity(db, f"c{i}") for i in range(4)]
    for i, ent in enumerate(ents, 1):
        await _link(db, intake_id, recs[i], ent)
    await _ev(db, ents[0], CMS, "NOT_FOUND", T_OLD, query_identifier=CMS_API_KEY.format(npi=NPI_A))
    await _ev(db, ents[1], CMS, "NOT_FOUND", T_OLD, query_identifier=CMS_API_KEY.format(npi=NPI_B))
    await _ev(db, ents[2], CMS, "PASS", T_OLD, query_identifier="snapshot:" + str(uuid.uuid4()),
              records=[{"npi": NPI_A, "enrollment_id": "SYNTHETIC-ENRL-2"}])
    await _ev(db, ents[3], CMS, "PASS", T_OLD, query_identifier="snapshot:" + str(uuid.uuid4()),
              records=[{"npi": NPI_B, "enrollment_id": "SYNTHETIC-ENRL-3"}])
    facts, out = await _facts(db, intake_id)
    assert facts[1]["pecos"].startswith("NOT_FOUND")                          # asked about this record's NPI
    assert facts[2]["pecos"].startswith("Not attributed to this record") and "NOT_FOUND" not in facts[2]["pecos"]
    assert "the recorded CMS query filter" in facts[2]["pecos"]
    assert facts[3]["pecos"].startswith("PASS")                               # the stored record is this NPI's
    assert facts[4]["pecos"].startswith("Not attributed to this record") and "PASS" not in facts[4]["pecos"]
    assert "the NPI on the stored enrolment record(s)" in facts[4]["pecos"]
    assert out["meta"]["stats"]["evidence_rows_not_attributed"] == 2


async def test_a_cms_not_found_from_an_ingested_edition_stores_no_npi_and_is_labelled_not_the_records(rolled_back_db):
    """Reported as stored: `snapshot:<id>` and no record. Nothing on the row says which NPI was asked about, so
    the not-found is quoted and labelled as the entity's - neither dropped nor shown as this record's result."""
    db = rolled_back_db
    intake_id, recs = await _delivery(db, "9.99.778.16", [NPI_A])
    ent = await _entity(db, "c5")
    await _link(db, intake_id, recs[1], ent)
    row = await _ev(db, ent, CMS, "NOT_FOUND", T_OLD, query_identifier="snapshot:" + str(uuid.uuid4()), records=[])
    facts, out = await _facts(db, intake_id)
    cell = facts[1]["pecos"]
    assert cell.startswith("Not attributed to this record: the newest CMS PPEF enrolment evidence row")
    assert "Stored for the ENTITY" in cell and cell.endswith("NOT_FOUND | retrieved 2026-11-08")
    assert f"CMS_PPEF_ENROLLMENT:{row.id}@" in facts[1]["evidence_reference"]
    assert out["meta"]["stats"]["evidence_rows_identifier_not_recorded"] == 1


# ── carried from an earlier check; the cutoff ──────────────────────────────────────────────────────────────────

async def test_valid_evidence_from_before_this_delivery_was_received_is_kept_and_labelled_carried(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _delivery(db, "9.99.778.17", [NPI_A, NPI_A])
    earlier, current = await _entity(db, "k1"), await _entity(db, "k2")
    await _link(db, intake_id, recs[1], earlier)
    await _link(db, intake_id, recs[2], current)
    for ent, when in ((earlier, T_BEFORE_RECEIPT), (current, T_OLD)):
        await _ev(db, ent, NPPES, "PASS", when, answer_npi=NPI_A)
        await _ev(db, ent, CMS, "PASS", when, query_identifier=CMS_API_KEY.format(npi=NPI_A))
    facts, out = await _facts(db, intake_id)
    note = "carried from an earlier check: observed 2026-10-02, before this delivery was received (2026-11-01)"
    for key in ("nppes", "pecos"):
        assert facts[1][key].startswith("PASS") and facts[1][key].endswith(note)   # kept, dated, labelled
        assert facts[2][key].startswith("PASS") and "carried from" not in facts[2][key]
    assert out["meta"]["stats"]["evidence_rows_carried_from_another_check"] == 2


async def test_evidence_recorded_under_a_review_of_a_different_delivered_record_is_labelled_carried(rolled_back_db):
    db = rolled_back_db
    a_id, ra = await _delivery(db, "9.99.778.18", [NPI_A])
    b_id, rb = await _delivery(db, "9.99.778.19", [NPI_A])                   # same NPI, another delivery
    shared = await _entity(db, "k3")
    review_a = await _link(db, a_id, ra[1], shared)
    await _link(db, b_id, rb[1], shared)
    await _ev(db, shared, NPPES, "PASS", T_OLD, answer_npi=NPI_A, review_id=review_a)   # after both receipts
    fa, fb = (await _facts(db, a_id))[0][1], (await _facts(db, b_id))[0][1]
    assert fa["nppes"].startswith("PASS") and "carried from" not in fa["nppes"]        # its own review's check
    assert fb["nppes"].startswith("PASS")
    assert fb["nppes"].endswith("carried from a check recorded for a different delivered record: observed 2026-11-08")


async def test_nothing_observed_after_the_cutoff_is_attributed_or_quoted(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _delivery(db, "9.99.778.20", [NPI_A, NPI_A])
    late_only, both = await _entity(db, "t1"), await _entity(db, "t2")
    await _link(db, intake_id, recs[1], late_only)
    await _link(db, intake_id, recs[2], both)
    await _ev(db, late_only, NPPES, "PASS", T_AFTER_CUTOFF, answer_npi=NPI_A)
    await _ev(db, both, CMS, "NOT_FOUND", T_OLD, query_identifier=CMS_API_KEY.format(npi=NPI_A))
    await _ev(db, both, CMS, "PASS", T_AFTER_CUTOFF, query_identifier=CMS_API_KEY.format(npi=NPI_A))
    facts, _ = await _facts(db, intake_id)
    assert facts[1]["nppes"] == ld.NO_EVIDENCE
    assert facts[2]["pecos"].startswith("NOT_FOUND")                          # the answer at the cutoff, not the later one


# ── presentation only ──────────────────────────────────────────────────────────────────────────────────────────

async def test_attribution_labels_change_no_bucket_status_or_total(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _delivery(db, "9.99.778.21", [NPI_A, NPI_A, ""])
    ents = [await _entity(db, f"p{i}") for i in range(3)]
    for i, (ent, bucket) in enumerate(zip(ents, ("B1", "B3", "B4")), 1):
        await _link(db, intake_id, recs[i], ent, bucket=bucket)
    await _ev(db, ents[0], NPPES, "PASS", T_OLD, answer_npi=NPI_B)            # foreign
    await _ev(db, ents[1], CMS, "UNAVAILABLE", T_OLD)                         # no NPI stored
    await _ev(db, ents[2], NPPES, "PASS", T_OLD, answer_npi=NPI_B)            # record submitted no NPI
    rows_before = (await db.execute(select(E.id, E.disposition, E.original_values, E.query_identifier).where(
        E.entity_id.in_([str(e) for e in ents])).order_by(E.id))).all()
    dataset = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="R-ATTRIBUTION")
    by_line = {int(r[HDR.index("Source_Line")]) - 1: r for r in dataset["sheets"]["Records"]["rows"]}
    assert [by_line[i][HDR.index("Bucket")] for i in (1, 2, 3)] == ["B1", "B3", "B4"]
    assert [by_line[i][HDR.index(ds_mod.AUTOMATED_BUCKET_COLUMN)] for i in (1, 2, 3)] == ["B1", "B3", "B4"]
    assert dataset["summary"]["buckets"] == {"B1": 1, "B2": 0, "B3": 1, "B4": 1}
    assert dataset["summary"]["complete"] is True
    assert by_line[1][HDR.index("NPPES")].startswith("Not attributed to this record")
    assert by_line[2][HDR.index("PECOS/CMS Enrollment")].startswith("Not attributed to this record")
    # stored evidence is read, never rewritten
    assert (await db.execute(select(E.id, E.disposition, E.original_values, E.query_identifier).where(
        E.entity_id.in_([str(e) for e in ents])).order_by(E.id))).all() == rows_before
    assert any("NPPES and CMS PPEF enrolment cells" in n for n in dataset["load_meta"]["notes"])
