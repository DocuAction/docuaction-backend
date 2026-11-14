"""Database-backed tests for the record-level reporting loader.

Every test runs inside the outer transaction of `rolled_back_db` (rolled back at
the end): nothing here reaches disk, and nothing touches a real delivery. These
tests REQUIRE a database; they are listed in the PostgreSQL CI job, whose
"fail if skipped for want of a database" step makes a silent skip a failure.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.reports.data import october_review_dataset as ds_mod
from app.reports.data import october_review_loader as ld
from app.Tefca.models import TEFCADimensionEvidence as E
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, seed_entity, seed_intake,
)

CUTOFF = datetime(2026, 11, 9, 0, 0, 0)
T_RUN_START = datetime(2026, 11, 7, 8, 0, 0)
T_RUN_DONE = datetime(2026, 11, 8, 9, 0, 0)
T_OLD = datetime(2026, 11, 8, 10, 0, 0)
T_NEW = datetime(2026, 11, 8, 18, 0, 0)
T_AFTER_CUTOFF = datetime(2026, 11, 10, 9, 0, 0)
HDR = ds_mod.RECORD_COLUMNS


# ── seeding helpers (rows are synthetic; the outer transaction is rolled back) ──

async def _records(db, intake_id):
    return {r.line_number - 1: r for r in (await db.execute(
        select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}


async def _run(db, intake_id, *, date_str="2026-11-08", status="COMPLETE"):
    run = m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="test",
        rule_config_hash="0" * 64, started_at=T_RUN_START,
        completed_at=T_RUN_DONE if status == "COMPLETE" else None,
        run_status=status,
        assessment_metadata={"assessment_date": date_str, "source": "pinned_param"})
    db.add(run)
    await db.flush()
    return run


async def _curate(db, intake_id, rec, status="CLEAN", *, entity_id=None, reason=None):
    db.add(m.RceCuratedRecord(
        id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id,
        record_status=status, status_reason=reason, rce_org_oid=rec.source_rce_id,
        transformation_version="test", canonical_entity_id=entity_id))
    await db.flush()


async def _review(db, rec, entity_id, review_id, bucket, created, *, rule="RULE-001"):
    db.add(reg.ReviewRecord(
        id=uuid.uuid4(), review_id=review_id, entity_id=entity_id,
        source_record_id=rec.id, classification_bucket=bucket,
        classification_rule=rule, classification_rule_version=2,
        classification_rationale=f"rationale for {review_id}", created_at=created))
    await db.flush()


async def _evidence(db, entity_id, source, dimension, disposition, created, *,
                    original_values=None, normalized_values=None, note=None,
                    retrieved="2026-11-08"):
    row = E(id=uuid.uuid4(), entity_id=str(entity_id), evidence_dimension=dimension,
            source=source, disposition=disposition, original_values=original_values or {},
            normalized_values=normalized_values or {}, note=note,
            retrieved_at=retrieved, generation_timestamp=f"gen-{created.isoformat()}",
            created_at=created)
    db.add(row)
    await db.flush()
    return row


async def _entity(db, tag):
    return await seed_entity(db, oid=f"{SYN}-ENT-{tag}-{uuid.uuid4().hex[:8]}",
                             name=f"{SYN} entity {tag}")


async def _load(db, intake_id, **kw):
    kw.setdefault("evidence_cutoff", CUTOFF)
    return await ld.load_record_facts(db, intake_id=intake_id, **kw)


def _by_line(loaded):
    return {f["source_line"] - 1: f for f in loaded["facts"]}


def _col(row, name):
    return row[HDR.index(name)]


# ── 1. starting set: every source record, including HELD / unpromoted ───────

async def test_every_source_record_is_loaded_including_held_and_unpromoted(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(6, arc="9.99.777.20"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    ent = await _entity(db, "a")
    await _curate(db, intake_id, recs[1], "CLEAN", entity_id=ent)
    await _curate(db, intake_id, recs[2], "HELD", reason="blocking identity issue")
    await _curate(db, intake_id, recs[3], "REJECTED", reason="unparseable")
    await _curate(db, intake_id, recs[4], "CLEAN")            # curated, not promoted
    # recs[5], recs[6]: no curated row at all
    await _review(db, recs[1], ent, "REV-9920-000001", "B1", T_OLD)

    out = await _load(db, intake_id)
    facts = _by_line(out)
    assert sorted(facts) == [1, 2, 3, 4, 5, 6]
    assert {f["agt_record_id"] for f in facts.values()} == {str(r.id) for r in recs.values()}
    assert facts[1]["bucket"] == "B1"
    assert facts[2]["status"] == "HELD" and facts[2]["bucket"] is None
    assert facts[2]["held_reason"] == "blocking identity issue"
    assert facts[3]["status"] == "REJECTED"
    assert facts[5]["status"] == "NOT CURATED" and facts[6]["status"] == "NOT CURATED"
    assert out["meta"]["source_records_loaded"] == 6 and out["meta"]["row_count_matches"]
    assert out["meta"]["stats"]["held"] == 1


# ── 2. identifiers: AGT = rce_source_records.id; others separate ────────────

async def test_agt_id_is_the_source_record_id_and_other_identifiers_stay_separate(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.21")
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    ent = await _entity(db, "b")
    await _curate(db, intake_id, rec, "CLEAN", entity_id=ent)
    await _review(db, rec, ent, "REV-9921-000001", "B2", T_OLD, rule="RULE-003")
    f = (await _load(db, intake_id))["facts"][0]
    assert f["agt_record_id"] == str(rec.id)
    assert f["source_line"] == rec.line_number
    ref = f["evidence_reference"]
    assert f"source_record={rec.id}" in ref
    assert f"source_rce_id={rows[0]['id']}" in ref        # business id, separate
    assert f"entity={ent}" in ref and "review=REV-9921-000001" in ref
    assert str(rec.id) != rows[0]["id"] != str(ent)
    assert f["hcid"] == rows[0]["HCID"] and f["tefca_id"] == rows[0]["TEFCAID"]
    assert f["bucket_basis"].startswith("Persisted classification: rule RULE-003 v2")


# ── 3. delivery isolation / no cross-delivery leakage ───────────────────────

async def test_other_deliveries_reviews_evidence_and_records_never_leak(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(2, arc="9.99.777.22")
    a_id, _ = await seed_intake(db, rows, with_job=False)
    b_id, _ = await seed_intake(db, rows, with_job=False)         # SAME business ids / HCID / TEFCAID
    await _run(db, a_id)
    await _run(db, b_id)
    ra, rb = await _records(db, a_id), await _records(db, b_id)
    shared = await _entity(db, "shared")                          # one entity, both deliveries
    for intake, recs in ((a_id, ra), (b_id, rb)):
        await _curate(db, intake, recs[1], "CLEAN", entity_id=shared)
        await _curate(db, intake, recs[2], "CLEAN")
    await _review(db, ra[1], shared, "REV-9922-000001", "B1", T_OLD)
    await _review(db, rb[1], shared, "REV-9922-000002", "B4", T_NEW)       # B's decision
    # legacy review: entity only, NO source_record_id - must never be joined
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id="REV-9922-000003",
                            entity_id=shared, classification_bucket="B3",
                            created_at=T_NEW))
    await db.flush()
    await _evidence(db, shared, "NPPES", "IDENTITY", "PASS", T_OLD)

    out_a, out_b = await _load(db, a_id), await _load(db, b_id)
    fa, fb = _by_line(out_a), _by_line(out_b)
    assert len(out_a["facts"]) == len(out_b["facts"]) == 2
    ids_a = {f["agt_record_id"] for f in out_a["facts"]}
    ids_b = {f["agt_record_id"] for f in out_b["facts"]}
    assert ids_a.isdisjoint(ids_b)                                # same business ids, different AGT ids
    assert fa[1]["bucket"] == "B1" and fb[1]["bucket"] == "B4"    # each its own decision
    assert "REV-9922-000002" not in fa[1]["evidence_reference"]
    assert "REV-9922-000003" not in fa[1]["evidence_reference"]
    assert fa[2]["bucket"] is None and fb[2]["bucket"] is None    # no leak onto the other record


async def test_annotation_or_comment_for_another_deliverys_record_is_rejected_not_attached(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.23")
    a_id, _ = await seed_intake(db, rows, with_job=False)
    b_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, a_id)
    rb = (await _records(db, b_id))[1]
    foreign = str(rb.id)
    out = await _load(db, a_id,
                      annotations={foreign: {"supplemental_npi": "1234567893"}},
                      comments={foreign: {"original": {"text": "x", "source": "f; sha"}}})
    assert out["meta"]["rejected_annotation_ids"] == [foreign]
    assert out["meta"]["rejected_comment_ids"] == [foreign]
    f = out["facts"][0]
    assert f["supplemental_npi"] == "" and "qa_comments" not in f


# ── 4. no duplicate joins / fan-out ─────────────────────────────────────────

async def test_multiple_reviews_and_evidence_generations_yield_one_row_per_record(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(2, arc="9.99.777.24"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    ent = await _entity(db, "dup")
    await _curate(db, intake_id, recs[1], "CLEAN", entity_id=ent)
    await _curate(db, intake_id, recs[2], "CLEAN", entity_id=ent)       # two records, ONE entity
    await _review(db, recs[1], ent, "REV-9924-000001", "B3", T_OLD)
    await _review(db, recs[1], ent, "REV-9924-000002", "B1", T_NEW)     # newest at/before cutoff
    await _review(db, recs[1], ent, "REV-9924-000003", "B4", T_AFTER_CUTOFF)  # after cutoff
    for when, disp in ((T_OLD, "FAIL"), (T_NEW, "PASS"), (T_AFTER_CUTOFF, "UNAVAILABLE")):
        await _evidence(db, ent, "NPPES", "IDENTITY", disp, when)
        await _evidence(db, ent, "OIG_LEIE", "EXCLUSION_REVOCATION", "NOT_FOUND", when)
    out = await _load(db, intake_id)
    assert len(out["facts"]) == 2
    f1 = _by_line(out)[1]
    assert f1["bucket"] == "B1" and "REV-9924-000002" in f1["evidence_reference"]
    assert f1["nppes"].startswith("PASS")                 # not the older FAIL nor the post-cutoff one
    assert out["meta"]["stats"]["records_with_multiple_reviews"] == 1
    # the shared entity's evidence is read for BOTH records, each exactly once
    assert _by_line(out)[2]["nppes"].startswith("PASS")
    assert len({f["agt_record_id"] for f in out["facts"]}) == 2


async def test_evidence_and_reviews_after_the_cutoff_are_never_read(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.25"), with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    ent = await _entity(db, "late")
    await _curate(db, intake_id, rec, "CLEAN", entity_id=ent)
    await _review(db, rec, ent, "REV-9925-000001", "B1", T_AFTER_CUTOFF)
    await _evidence(db, ent, "NPPES", "IDENTITY", "PASS", T_AFTER_CUTOFF)
    f = (await _load(db, intake_id))["facts"][0]
    assert f["bucket"] is None and ld.NO_ANCHORED_REVIEW in f["bucket_basis"]
    assert f["nppes"] == ld.NO_EVIDENCE


# ── 5. missing classification => incomplete, never a manufactured bucket ────

async def test_missing_persisted_classification_makes_the_dataset_incomplete(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(4, arc="9.99.777.26"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    ent = await _entity(db, "c")
    for i in (1, 2, 3):
        await _curate(db, intake_id, recs[i], "CLEAN", entity_id=ent if i == 1 else None)
    await _curate(db, intake_id, recs[4], "HELD", reason="blocker")
    await _review(db, recs[1], ent, "REV-9926-000001", "B1", T_OLD)
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id="REV-9926-000002",
                            source_record_id=recs[2].id, classification_bucket=None,
                            created_at=T_OLD))             # review exists, NO bucket
    await db.flush()
    dataset = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF,
                                    report_id="R-TEST", findings_count=1336)
    s = dataset["summary"]
    assert s["complete"] is False and s["unclassified_non_held_count"] == 2
    assert set(s["unclassified_non_held_ids"]) == {str(recs[2].id), str(recs[3].id)}
    assert s["buckets"] == {"B1": 1, "B2": 0, "B3": 0, "B4": 1}      # 1 HELD, once
    assert s["held_inside_b4"] == 1 and s["field_findings"] == 1336
    assert "Unclassified" in dataset["sheet_order"] and "INCOMPLETE" in dataset["banner"]
    rows = {r[1]: r for r in dataset["sheets"]["Records"]["rows"]}
    assert _col(rows[str(recs[2].id)], "Bucket") == ds_mod.NOT_CLASSIFIED
    assert _col(rows[str(recs[4].id)], "Bucket") == "B4"
    assert _col(rows[str(recs[4].id)], "Bucket Basis").startswith(
        "Reporting annotation: HELD counted once in B4")
    # HELD never had a persisted classification and is not listed as unclassified
    assert str(recs[4].id) not in s["unclassified_non_held_ids"]


# ── 6. NPPES Entity_Type needs an established original-submitted-NPI link ───

async def test_entity_type_requires_nppes_npi_to_equal_the_original_submitted_npi(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.777.27")
    rows[0]["NPI"] = "1234567893"        # NPPES returns the same NPI -> link
    rows[1]["NPI"] = "1234567893"        # NPPES returns a different NPI -> no link
    rows[2]["NPI"] = ""                  # blank submitted NPI (the line-4686 shape)
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    returned = ["1234567893", "1659346005", "1659346005"]
    for i in (1, 2, 3):
        ent = await _entity(db, f"et{i}")
        await _curate(db, intake_id, recs[i], "CLEAN", entity_id=ent)
        await _evidence(db, ent, "NPPES", "IDENTITY", "PASS", T_OLD,
                        original_values={"npi": returned[i - 1], "enumeration_type": "NPI-2"})
    dataset = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF,
                                    report_id="R")
    by = {r[0]: r for r in dataset["sheets"]["Records"]["rows"]}
    et = "Entity_Type (NPPES Entity Type Code)"
    assert _col(by[str(recs[1].line_number)], et) == "NPI-2"
    assert _col(by[str(recs[2].line_number)], et) == ds_mod.ENTITY_TYPE_NOT_ESTABLISHED
    assert _col(by[str(recs[3].line_number)], et) == ds_mod.ENTITY_TYPE_NOT_ESTABLISHED
    # the blank submitted NPI stays blank - NPPES did not "supply" one
    assert _col(by[str(recs[3].line_number)], "Submitted NPI (original, verbatim)") == ""


async def test_supplemental_npi_is_separate_and_never_fills_the_submitted_blank(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.28"), with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    out = await _load(db, intake_id,
                      annotations={str(rec.id): {"supplemental_npi": "1234567893"}})
    f = out["facts"][0]
    assert f["submitted_npi"] == "" and f["supplemental_npi"] == "1234567893"
    assert "entity_type_basis" not in f


# ── 7. source-specific statuses and limitations are preserved ───────────────

async def test_actual_source_statuses_and_cause_specific_limitations_are_preserved(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.29"), with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    ent = await _entity(db, "src")
    await _curate(db, intake_id, rec, "CLEAN", entity_id=ent)
    await _evidence(db, ent, "CMS_PPEF_ENROLLMENT", "MEDICARE_ENROLLMENT", "UNAVAILABLE", T_OLD,
                    note="key missing: no NPI was submitted for this record")
    await _evidence(db, ent, "OIG_LEIE", "EXCLUSION_REVOCATION", "NOT_FOUND", T_OLD)
    await _evidence(db, ent, "SAM_GOV", "EXCLUSION_REVOCATION", "NOT_FOUND", T_OLD,
                    normalized_values={"screening_leg": "registration_only"})
    out = await _load(db, intake_id, annotations={
        str(rec.id): {"pecos_limitation": "confirmed missing key (no submitted NPI)"}})
    f = out["facts"][0]
    assert f["pecos"].startswith("UNAVAILABLE")
    assert "recorded note: key missing" in f["pecos"]
    assert "Limitation: confirmed missing key" in f["pecos"]
    assert "lost-identifier/outage family" not in f["pecos"].lower()
    assert f["leie"].startswith("NOT_FOUND -> clear")            # actual status + interpretation
    assert "registration-only" in f["sam"] and "NOT screened" in f["sam"]
    assert f["sam"].startswith("NOT_FOUND") and "-> unavailable" in f["sam"]
    assert f["usps"] == "" and ds_mod.build_record_row(f)[HDR.index("USPS Address Check")] \
        == ds_mod.USPS_UNAVAILABLE


async def test_unpromoted_record_reports_the_specific_cause_not_a_blank(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.30"), with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    await _curate(db, intake_id, rec, "HELD", reason="blocker")
    f = (await _load(db, intake_id))["facts"][0]
    for k in ("nppes", "pecos", "leie", "sam"):
        assert f[k] == ld.NOT_PROMOTED


# ── 8. assessment run / cutoff selection ────────────────────────────────────

async def test_assessment_date_comes_from_the_selected_run_of_this_delivery(rolled_back_db):
    db = rolled_back_db
    a_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.31"), with_job=False)
    b_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.32"), with_job=False)
    run_a = await _run(db, a_id, date_str="2026-11-08")
    run_b = await _run(db, b_id, date_str="2027-03-02")
    out = await _load(db, a_id)
    assert out["assessment_date"] == "2026-11-08"                 # NOT the other delivery's 2027 run
    assert out["meta"]["assessment_run_id"] == str(run_a.id)
    with pytest.raises(ld.LoaderRefused, match="unrelated run"):
        await _load(db, a_id, run_id=run_b.id)
    ok = await _load(db, b_id, run_id=run_b.id)
    assert ok["assessment_date"] == "2027-03-02"


async def test_loader_refuses_without_a_completed_run_or_without_a_cutoff(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.33"), with_job=False)
    with pytest.raises(ld.LoaderRefused, match="no completed assessment run"):
        await _load(db, intake_id)
    await _run(db, intake_id, status="RUNNING")                   # unfinished != selected
    with pytest.raises(ld.LoaderRefused, match="no completed assessment run"):
        await _load(db, intake_id)
    await _run(db, intake_id)
    with pytest.raises(ld.LoaderRefused, match="evidence_cutoff is required"):
        await ld.load_record_facts(db, intake_id=intake_id, evidence_cutoff=None)
    with pytest.raises(ld.LoaderRefused, match="No delivery"):
        await _load(db, uuid.uuid4())
    with pytest.raises(ld.LoaderRefused, match="not a UUID"):
        await _load(db, "not-a-uuid")


# ── 9. comments: attribution + verbatim + separation ────────────────────────

async def test_original_and_izaan_comments_attach_verbatim_and_separately_with_attribution(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(2, arc="9.99.777.34"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    orig = "  Original\ncomment,  two  spaces — é  "
    iz = "Izaan: \"quoted\"; tail "
    src = {"file": "izaan.xlsx", "sha256": "cd" * 32, "sheet": "S", "column": "F"}
    comments = {str(recs[1].id): {
        "original": {"text": orig, "source": "ONC workbook; sheet=October Records"},
        "izaan": {"text": iz, "source": src}}}
    dataset = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF,
                                    report_id="R", comments=comments)
    by = {r[0]: r for r in dataset["sheets"]["Records"]["rows"]}
    r1, r2 = by[str(recs[1].line_number)], by[str(recs[2].line_number)]
    assert _col(r1, ds_mod.ORIGINAL_COMMENTS_COLUMN) == orig
    assert _col(r1, ds_mod.IZAAN_COMMENTS_COLUMN) == iz
    assert _col(r2, ds_mod.ORIGINAL_COMMENTS_COLUMN) == "" == _col(r2, ds_mod.IZAAN_COMMENTS_COLUMN)
    assert "izaan.xlsx" in dataset["summary"]["comment_sources"]["izaan"][0]
    assert _col(r1, "Bucket") == ds_mod.NOT_CLASSIFIED            # a comment decided nothing


async def test_izaan_comment_without_attribution_is_refused_end_to_end(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.35"), with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    with pytest.raises(ds_mod.DatasetRefused, match="attributable source"):
        await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="R",
                              comments={str(rec.id): {"izaan": {"text": "no source"}}})


async def test_unknown_annotation_key_is_refused(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.36"), with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    with pytest.raises(ld.LoaderRefused, match="unknown key"):
        await _load(db, intake_id, annotations={str(rec.id): {"bucket": "B1"}})


# ── 10. annotations stay distinguishable from persisted classification ──────

async def test_provisional_annotation_does_not_alter_the_persisted_classification(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.37"), with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    ent = await _entity(db, "prov")
    await _curate(db, intake_id, rec, "CLEAN", entity_id=ent)
    await _review(db, rec, ent, "REV-9937-000001", "B2", T_OLD, rule="RULE-003")
    dataset = await ld.load_dataset(
        db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="R",
        annotations={str(rec.id): {"provisional": True,
                                   "bucket_annotation": "provisional B2 (Q1 pending)",
                                   "flag": "MAILING-VS-PRACTICE-ONLY"}})
    row = dataset["sheets"]["Records"]["rows"][0]
    assert _col(row, "Bucket") == "B2"
    basis = _col(row, "Bucket Basis")
    assert basis.startswith("Persisted classification: rule RULE-003")
    assert "; Reporting annotation: provisional B2 (Q1 pending)" in basis
    assert "PROVISIONAL B2" in _col(row, "Flag") and "MAILING-VS-PRACTICE-ONLY" in _col(row, "Flag")
    stored = (await db.execute(select(reg.ReviewRecord.classification_bucket)
                               .where(reg.ReviewRecord.review_id == "REV-9937-000001"))).scalar()
    assert stored == "B2"                                          # stored decision untouched


# ── 11. the loader writes nothing ───────────────────────────────────────────

async def test_loader_changes_no_stored_state(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(2, arc="9.99.777.38"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    ent = await _entity(db, "ro")
    await _curate(db, intake_id, recs[1], "HELD", reason="blocker")
    await _curate(db, intake_id, recs[2], "CLEAN", entity_id=ent)
    await _review(db, recs[2], ent, "REV-9938-000001", "B1", T_OLD)

    async def snapshot():
        cur = (await db.execute(select(m.RceCuratedRecord.record_status,
                                       m.RceCuratedRecord.status_reason)
                                .where(m.RceCuratedRecord.source_intake_id == intake_id)
                                .order_by(m.RceCuratedRecord.source_record_id))).all()
        rev = (await db.execute(select(reg.ReviewRecord.review_id,
                                       reg.ReviewRecord.classification_bucket)
                                .where(reg.ReviewRecord.review_id.like("REV-9938-%")))).all()
        n_ev = (await db.execute(select(E.id).where(E.entity_id == str(ent)))).all()
        return sorted(map(tuple, cur)), sorted(map(tuple, rev)), len(n_ev)

    before = await snapshot()
    await _load(db, intake_id)
    await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="R")
    assert await snapshot() == before            # HELD still HELD; nothing released/reclassified


# ── 12. one dataset, three formats, reconciled ──────────────────────────────

async def test_db_loaded_dataset_reconciles_across_excel_csv_and_pdf_html(rolled_back_db):
    import openpyxl
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(4, arc="9.99.777.39"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    ent = await _entity(db, "fmt")
    await _curate(db, intake_id, recs[1], "CLEAN", entity_id=ent)
    await _curate(db, intake_id, recs[2], "HELD", reason="blocker")
    await _review(db, recs[1], ent, "REV-9939-000001", "B1", T_OLD)
    dataset = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF,
                                    report_id="R-FMT", findings_count=11)
    want = dataset["sheets"]["Records"]["rows"]
    assert len(want) == 4 == dataset["summary"]["unique_records"]
    wb = openpyxl.load_workbook(io.BytesIO(ds_mod.to_xlsx(dataset)))
    ws = wb["Records"]
    hdr_row = next(i for i, r in enumerate(ws.iter_rows(values_only=True), 1)
                   if r and r[0] == "Source_Line")
    xl = [["" if v is None else str(v) for v in r]
          for r in ws.iter_rows(min_row=hdr_row + 1, values_only=True)]
    assert xl == want
    text = ds_mod.to_csv(dataset).decode("utf-8-sig")
    lines = list(csv.reader(io.StringIO(text)))
    assert "INCOMPLETE" in lines[0][0]
    assert lines[1] == HDR and lines[2:] == want
    page = ds_mod.to_html(dataset)
    assert "DATASET INCOMPLETE" in page and all(r[1] in page for r in want)
    s = dataset["summary"]
    assert s["buckets"]["B4"] == 1 and s["held_inside_b4"] == 1       # HELD once
    assert s["buckets"]["B1"] == 1 and s["unclassified_non_held_count"] == 2
    assert sum(s["buckets"].values()) + s["unclassified_non_held_count"] == 4


# ── 13. gaps found by mutation checking ─────────────────────────────────────

async def test_curated_row_pointing_at_another_intake_is_not_used(rolled_back_db):
    """Defence in depth: a corrupt curated row whose source_intake_id disagrees
    with its source record's delivery must not supply this delivery's state."""
    db = rolled_back_db
    a_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.40"), with_job=False)
    b_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.41"), with_job=False)
    await _run(db, a_id)
    rec_a = (await _records(db, a_id))[1]
    await _curate(db, b_id, rec_a, "HELD", reason="belongs to the wrong delivery")
    f = (await _load(db, a_id))["facts"][0]
    assert f["status"] == "NOT CURATED" and f["held_reason"] == ""


async def test_analyst_reclassification_is_never_a_persisted_classification(rolled_back_db):
    """`reclassified_to` is an analyst determination that needs independent QA;
    the dataset reports the persisted classifier decision only."""
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(2, arc="9.99.777.42"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id="REV-9942-000001",
                            source_record_id=recs[1].id, classification_bucket="B3",
                            reclassified_to="B1", created_at=T_OLD))
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id="REV-9942-000002",
                            source_record_id=recs[2].id, classification_bucket=None,
                            reclassified_to="B2", created_at=T_OLD))
    await db.flush()
    facts = _by_line(await _load(db, intake_id))
    assert facts[1]["bucket"] == "B3"        # the classifier's decision, not the analyst's B1
    assert facts[2]["bucket"] is None        # reclassified_to alone gives no bucket
    assert "carries no classification" in facts[2]["bucket_basis"]


# ── 14. stored comments (rce_record_comments) flow into the three separate columns ──

from app.tefca_registry.rce import record_comments as _rc      # noqa: E402

_CSHA = "ef" * 32


def _centry(rec, kind="QA_ORIGINAL", body="c", **over):
    e = {"source_record_id": str(rec.id), "comment_kind": kind, "comment_text": body,
         "expected": {"source_line": rec.line_number, "hcid": rec.hcid, "tefca_id": rec.tefcaid},
         "source": {"file_name": "comments.xlsx", "file_sha256": _CSHA, "sheet": "S",
                    "column": "QA Comments", "row": rec.line_number}}
    if kind == "QA_IZAAN":
        e["author_attribution"] = "Izaan"
        e["attribution_basis"] = "owner designation of the source file"
    e.update(over)
    return e


async def test_stored_comments_reach_the_three_separate_columns_verbatim_and_decide_nothing(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(3, arc="9.99.779.50"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    await _curate(db, intake_id, recs[2], "HELD", reason="blocker")           # HELD, unpromoted
    orig = "  Original \r\nwording,  \"quoted\" — é =x  "
    izaan = "Izaan wording\twith tab "
    clar = "Generated clarification  text"
    out = await _rc.import_comments(db, intake_id=intake_id, actor="qalead@x.invalid", entries=[
        _centry(recs[1], "QA_ORIGINAL", orig), _centry(recs[1], "QA_IZAAN", izaan),
        _centry(recs[1], "REPORT_CLARIFICATION", clar), _centry(recs[2], "QA_ORIGINAL", "held rec comment")])
    assert out["counts"]["inserted"] == 4
    loaded = await ld.load_record_facts(db, intake_id=intake_id, evidence_cutoff=_cutoff_now())
    assert loaded["meta"]["comment_store"] == "applied"
    assert loaded["meta"]["stats"]["stored_comments"] == {
        "QA_ORIGINAL": 2, "QA_IZAAN": 1, "REPORT_CLARIFICATION": 1}
    dataset = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=_cutoff_now(), report_id="R")
    by = {r[0]: r for r in dataset["sheets"]["Records"]["rows"]}
    r1, r2, r3 = (by[str(recs[i].line_number)] for i in (1, 2, 3))
    assert _col(r1, ds_mod.ORIGINAL_COMMENTS_COLUMN) == orig                  # verbatim, not stripped
    assert _col(r1, ds_mod.IZAAN_COMMENTS_COLUMN) == izaan
    assert _col(r1, "Report Clarifications") == clar
    assert orig not in _col(r1, ds_mod.IZAAN_COMMENTS_COLUMN)                  # kinds never merged
    assert _col(r2, ds_mod.ORIGINAL_COMMENTS_COLUMN) == "held rec comment"      # HELD takes a comment
    assert _col(r2, "Bucket") == "B4" and _col(r2, "Status") == "HELD"          # ... and stays HELD in B4
    assert _col(r3, ds_mod.ORIGINAL_COMMENTS_COLUMN) == "" == _col(r3, ds_mod.IZAAN_COMMENTS_COLUMN)
    assert _col(r1, "Bucket") == ds_mod.NOT_CLASSIFIED                         # a comment is not a classification
    cs = dataset["summary"]["comment_sources"]
    assert cs["izaan"] and "comments.xlsx" in cs["izaan"][0] and _CSHA in cs["izaan"][0]


def _cutoff_now():
    return datetime.utcnow() + timedelta(days=1)


async def test_only_the_current_version_is_reported_and_other_deliveries_never_leak(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.779.51")
    a_id, _ = await seed_intake(db, rows, with_job=False)
    b_id, _ = await seed_intake(db, rows, with_job=False)               # same business ids
    await _run(db, a_id)
    ra, rb = await _records(db, a_id), await _records(db, b_id)
    await _rc.import_comments(db, intake_id=a_id, actor="q@x.invalid",
                              entries=[_centry(ra[1], "QA_ORIGINAL", "v1")])
    await _rc.import_comments(db, intake_id=a_id, actor="q@x.invalid",
                              entries=[_centry(ra[1], "QA_ORIGINAL", "v2", amend=True)])
    await _rc.import_comments(db, intake_id=b_id, actor="q@x.invalid",
                              entries=[_centry(rb[1], "QA_ORIGINAL", "other delivery")])
    out_a = await ld.load_record_facts(db, intake_id=a_id, evidence_cutoff=_cutoff_now())
    fa = _by_line(out_a)[1]
    assert fa["qa_comments"] == "v2" and fa["qa_comments_source"]["file"] == "comments.xlsx"
    assert "other delivery" not in str(fa)
    # the superseded v1 is history, not a second CURRENT comment
    assert out_a["meta"]["stats"]["records_with_multiple_current_comments_of_one_kind"] == 0
    assert out_a["meta"]["stats"]["stored_comments"]["QA_ORIGINAL"] == 1


async def test_a_missing_comment_store_is_reported_not_fatal(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.779.52"), with_job=False)
    await _run(db, intake_id)

    async def absent(_db):
        return False

    monkeypatch.setattr(ld, "_comment_store_exists", absent)
    out = await _load(db, intake_id)
    assert out["meta"]["comment_store"] == "not_applied"
    assert "qa_comments" not in out["facts"][0] and "qa_comments_izaan" not in out["facts"][0]
    off = await ld.load_record_facts(db, intake_id=intake_id, evidence_cutoff=_cutoff_now(),
                                     include_stored_comments=False)
    assert off["meta"]["comment_store"] == "not_requested"


async def test_caller_supplied_comments_override_stored_ones_and_are_counted(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.779.53"), with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    await _rc.import_comments(db, intake_id=intake_id, actor="q@x.invalid",
                              entries=[_centry(rec, "QA_ORIGINAL", "stored")])
    out = await _load(db, intake_id, evidence_cutoff=_cutoff_now(), comments={str(rec.id): {"original": {"text": "supplied",
                                                                            "source": "file; sha"}}})
    assert out["facts"][0]["qa_comments"] == "supplied"
    assert out["meta"]["stats"]["caller_comments_overriding_stored"] == 1


async def test_an_explicit_annotation_wins_over_a_stored_clarification(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(2, arc="9.99.779.54"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    await _rc.import_comments(db, intake_id=intake_id, actor="q@x.invalid", entries=[
        _centry(recs[1], "REPORT_CLARIFICATION", "stored clarification 1"),
        _centry(recs[2], "REPORT_CLARIFICATION", "stored clarification 2")])
    out = await _load(db, intake_id, evidence_cutoff=_cutoff_now(), annotations={
        str(recs[1].id): {"report_clarifications": "explicit annotation"}})
    f = _by_line(out)
    assert f[1]["report_clarifications"] == "explicit annotation"       # the explicit input wins
    assert f[2]["report_clarifications"] == "stored clarification 2"    # otherwise the stored one


async def test_comment_versions_are_selected_at_the_requested_cutoff(rolled_back_db):
    from app.tefca_registry.rce.record_comment_models import RceRecordComment
    from datetime import timezone
    db = rolled_back_db
    intake, _ = await seed_intake(db, make_rows(1, arc="9.99.779.97"), with_job=False)
    await _run(db, intake)
    rec = (await _records(db, intake))[1]
    first = await _rc.import_comments(db, intake_id=intake, actor="q@x.invalid",
        entries=[_centry(rec, "QA_ORIGINAL", "original wording")])
    row = await db.get(RceRecordComment, uuid.UUID(first["results"][0]["comment_id"]))
    between = row.imported_at
    # Real stored timestamps, no updates to append-only history.
    second = await _rc.import_comments(db, intake_id=intake, actor="q@x.invalid",
        entries=[_centry(rec, "QA_ORIGINAL", "later wording", amend=True)])
    newer = await db.get(RceRecordComment, uuid.UUID(second["results"][0]["comment_id"]))
    assert newer.imported_at > between
    async def at(cutoff):
        return _by_line(await ld.load_record_facts(db, intake_id=intake, evidence_cutoff=cutoff))[1]
    assert "qa_comments" not in await at(between - timedelta(microseconds=1))
    assert (await at(between))["qa_comments"] == "original wording"
    assert (await at(newer.imported_at))["qa_comments"] == "later wording"
