"""Source outcome vs classification vs overall completeness (2026-10-04).

A SAM.gov timeout / error body / 429 must not count as a successful SAM
verification, and an entity whose checks did not all answer must not sit
under an unqualified "verified" -- WITHOUT changing a bucket, a rule, an
entity status or a reportability gate (those are policy; see P1).
"""
from __future__ import annotations

import uuid

import pytest

from app.tefca_registry.rce import verification_completeness as vc


def _vr(**sources):
    return {"classifier_input": {"sources": {
        k: (v if isinstance(v, dict) else {"status": v}) for k, v in sources.items()}}}


COMPLETE = dict(nppes={"status": "verified", "disposition": "PASS"},
                oig_leie={"status": "clear", "disposition": "NOT_FOUND"},
                sam_gov={"status": "clear", "disposition": "NOT_FOUND"},
                cms_revocation={"status": "verified", "disposition": "PASS"},
                pecos={"status": "verified", "disposition": "PASS"})
SAM_DOWN = dict(COMPLETE, sam_gov={"status": "unavailable", "disposition": "UNAVAILABLE"})


# -- 1. source level -----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ({"status": "verified", "disposition": "PASS"}, vc.CONFIRMED),
    ({"status": "clear", "disposition": "NOT_FOUND"}, vc.NOT_LISTED),
    ({"status": "unavailable", "disposition": "UNAVAILABLE"}, vc.UNAVAILABLE),
    ("unavailable", vc.UNAVAILABLE),
    ({"status": "not_checked", "disposition": "INSUFFICIENT_EVIDENCE"}, vc.INSUFFICIENT_EVIDENCE),
    ({"status": "not_checked", "disposition": "NOT_APPLICABLE"}, vc.NOT_APPLICABLE),
    ({"status": "not_checked"}, vc.NOT_EVALUATED),
    (None, vc.NOT_EVALUATED),
    ({"status": "not_found", "disposition": "REVIEW"}, vc.ANSWERED_WITH_FINDING),
    ({"status": "failed", "disposition": "FAIL"}, vc.ANSWERED_WITH_FINDING),
    ("excluded", vc.ANSWERED_WITH_FINDING),
])
def test_source_outcome_vocabulary(raw, expected):
    assert vc.source_outcome(raw) == expected


def test_an_unavailable_source_is_never_a_success_a_finding_or_a_not_found():
    out = vc.source_outcome({"status": "unavailable", "disposition": "UNAVAILABLE"})
    assert out not in vc.SUCCESSFUL          # not a successful verification
    assert out not in vc.ANSWERED            # not an answer about the entity
    assert out != vc.ANSWERED_WITH_FINDING   # not excluded / non-compliant / not found
    assert out in vc.MISSING


# -- 3. completeness -----------------------------------------------------------

def test_complete_only_when_every_owed_check_answered():
    assert vc.completeness(_vr(**COMPLETE))["state"] == vc.COMPLETE
    down = vc.completeness(_vr(**SAM_DOWN))
    assert down["state"] == vc.INCOMPLETE
    assert [(i["source"], i["outcome"]) for i in down["incomplete"]] == [
        ("sam_gov", vc.UNAVAILABLE)]
    assert "sam_gov" not in down["successful_sources"]
    assert "oig_leie" in down["successful_sources"]


def test_an_exclusion_control_that_was_never_attempted_is_incomplete_not_complete():
    partial = vc.completeness(_vr(nppes="verified", oig_leie="clear"))
    assert partial["state"] == vc.INCOMPLETE
    assert {i["source"] for i in partial["incomplete"]} == {"sam_gov", "cms_revocation"}


def test_an_adverse_answer_is_complete_it_is_a_finding_not_a_gap():
    hit = vc.completeness(_vr(**dict(COMPLETE, sam_gov={"status": "not_found",
                                                         "disposition": "REVIEW"})))
    assert hit["state"] == vc.COMPLETE
    assert hit["source_outcomes"]["sam_gov"] == vc.ANSWERED_WITH_FINDING
    assert "sam_gov" not in hit["successful_sources"]


def test_not_applicable_is_not_a_gap():
    na = vc.completeness(_vr(**dict(COMPLETE, cms_revocation={
        "status": "not_checked", "disposition": "NOT_APPLICABLE"})))
    assert na["state"] == vc.COMPLETE


def test_no_stored_source_results_is_unknown_never_assumed_complete():
    for vr in (None, {}, {"dimensions": []}, {"classifier_input": {"sources": {}}}):
        assert vc.completeness(vr)["state"] == vc.NOT_RECORDED
    assert vc.overall_status("verified", vc.completeness(None)) == \
        vc.VERIFIED_CHECKS_NOT_RECORDED


def test_the_manual_path_shape_is_read_too():
    manual = {"sources": {"nppes": {"status": "verified"}, "oig_leie": {"status": "clear"},
                          "sam_gov": {"status": "not_checked"}}}
    comp = vc.completeness(manual)
    assert comp["state"] == vc.INCOMPLETE
    assert comp["source_outcomes"]["sam_gov"] == vc.NOT_EVALUATED


# -- the qualified overall label -------------------------------------------------

def test_only_verified_is_qualified_every_other_status_passes_through():
    down = vc.completeness(_vr(**SAM_DOWN))
    assert vc.overall_status("verified", down) == vc.VERIFIED_CHECKS_INCOMPLETE
    assert vc.overall_status("verified", vc.completeness(_vr(**COMPLETE))) == "verified"
    for status in ("in_review", "not_verified", "failed"):
        assert vc.overall_status(status, down) == status
    block = vc.describe("verified", down)
    assert block["overall_label"] == "Verified - checks incomplete"
    assert block["classification_unchanged"] is True


def test_the_classifier_is_untouched_by_this_module():
    """B1 stays B1: completeness describes, it does not classify."""
    from app.tefca_registry.bucket_classifier import BucketClassifier, _v3_rules
    inp = _vr(**SAM_DOWN)["classifier_input"]
    inp["fields"] = {}
    before = BucketClassifier().classify(inp, rules=_v3_rules())
    vc.describe("verified", vc.completeness({"classifier_input": inp}))
    after = BucketClassifier().classify(inp, rules=_v3_rules())
    assert (before.bucket, before.rule_code) == (after.bucket, after.rule_code) == ("B1", "RULE-001")


# -- counts: registry list, registry stats, report ------------------------------

async def _seed(tag):
    """Three `verified` entities (complete / SAM down / nothing stored) and one
    `in_review` entity whose SAM was also down."""
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    ids = {}
    async with async_session_maker() as db:
        for key, status, vr in (
                ("complete", "verified", _vr(**COMPLETE)),
                ("sam_down", "verified", _vr(**SAM_DOWN)),
                ("no_record", "verified", None),
                ("held", "in_review", _vr(**SAM_DOWN))):
            e = reg.TefcaRegEntity(id=uuid.uuid4(), name=f"SYNTHETIC-TRACE COMPLETENESS {tag} {key}",
                                   entity_level="participant", entity_type="provider",
                                   verification_status=status)
            db.add(e)
            await db.flush()
            if vr is not None:
                db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id=f"RC{uuid.uuid4().hex[:14]}",
                                        entity_id=e.id, verification_results=vr,
                                        classification_bucket="B1"))
            ids[key] = e.id
        await db.commit()
    return ids


@pytest.mark.asyncio
async def test_registry_rows_and_counts_qualify_verified(db_required):
    from app.core.database import async_session_maker
    from app.tefca_registry import queries

    tag = uuid.uuid4().hex[:8]
    ids = await _seed(tag)
    async with async_session_maker() as db:
        page = await queries.list_entities(db, q=f"COMPLETENESS {tag}", limit=10)
        by_id = {row["id"]: row for row in page["items"]}
        # the stored status is untouched...
        assert {by_id[ids[k]]["verification_status"] for k in ("complete", "sam_down", "no_record")} \
            == {"verified"}
        # ...and what a screen shows is qualified
        assert by_id[ids["complete"]]["verification_overall"] == "verified"
        assert by_id[ids["complete"]]["verification_overall_label"] == "Verified"
        assert by_id[ids["sam_down"]]["verification_overall"] == vc.VERIFIED_CHECKS_INCOMPLETE
        assert by_id[ids["sam_down"]]["verification_incomplete_sources"] == ["SAM.gov"]
        assert by_id[ids["no_record"]]["verification_overall"] == vc.VERIFIED_CHECKS_NOT_RECORDED
        assert by_id[ids["held"]]["verification_overall"] == "in_review"

        detail = await queries.get_entity_detail(db, ids["sam_down"])
        assert detail["verification_overall_label"] == "Verified - checks incomplete"

        split = await vc.split_verified_counts(
            db, {"verified": 3, "in_review": 1}, list(ids.values()))
        assert split["split"] == {"verified": 1, vc.VERIFIED_CHECKS_INCOMPLETE: 1,
                                  vc.VERIFIED_CHECKS_NOT_RECORDED: 1}
        assert sum(split["counts"].values()) == 4          # nothing dropped, nothing added
        assert split["incomplete_by_source"] == {"sam_gov": 1}   # `held` is not `verified`

        stats = await queries.stats(db)
        raw, qualified = stats["by_verification_status"], stats["by_verification_overall"]
        assert sum(raw.values()) == sum(qualified.values())
        assert qualified.get(vc.VERIFIED_CHECKS_INCOMPLETE, 0) >= 1
        assert qualified.get("verified", 0) <= raw.get("verified", 0) - 2


@pytest.mark.asyncio
async def test_the_report_never_prints_a_bare_verified_count_for_incomplete_checks(db_required):
    from app.core.database import async_session_maker
    from app.reports.charts import entity_status_chart
    from app.reports.data.report_data_service import ReportDataService

    await _seed(uuid.uuid4().hex[:8])
    async with async_session_maker() as db:
        before = dict((await db.execute(__import__("sqlalchemy").text(
            "select verification_status, count(*) from tefca_reg_entities group by 1"))).all())
        data = await ReportDataService(db).get_entity_status_breakdown()
    counts = data["counts"]
    assert data["total"] == sum(before.values()) == sum(counts.values())
    assert counts.get(vc.VERIFIED_CHECKS_INCOMPLETE, 0) >= 1
    assert counts.get(vc.VERIFIED_CHECKS_NOT_RECORDED, 0) >= 1
    assert counts.get("verified", 0) < before["verified"]
    assert data["verified_completeness"]["verified_total"] == before["verified"]
    assert "not a successful check" in data["language_note"]
    chart = entity_status_chart(data)
    assert "verified checks incomplete" in chart.categories
    assert "not a completed verification" in chart.notes
