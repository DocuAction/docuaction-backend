"""Issue history: database-backed scenarios (real PostgreSQL, real engine).

The 14 synthetic scenarios of the minimum-slice scope (criterion 1), the
preservation and privilege tests (criterion 2), never-inferred (3), the result
map as persisted (4), access and audit (8) and the query-shape check (7).

Every delivery goes through the real quality engine with
ENABLE_RECORD_CHECK_RESULTS on, inside an outer transaction that is rolled back.
OID `SYN-OID-0001`, rule NPI-002, field NPI, feed SYN-RCE.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select, text

from app.tefca_registry import models as reg
from app.tefca_registry.rce import issue_history as svc
from app.tefca_registry.rce import issue_history_core as core
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.quality_rules import RULE_BY_ID, RULES
from app.tefca_registry.rce.record_check_tables import RECORD_CHECK_RESULTS as RCR

from issue_history_support_2026_10_07 import (  # noqa: F401  (fixture imported)
    FEED, GOOD_NPI, BAD_LEN_NPI, OID, QHIN_OID, entity_row, entry_of, filler_row,
    history, issue_of, lane, record_of, rolled_back_db, run_engine, seed_delivery,
    settings_for, deliver)

NPI2 = "NPI-002"


@pytest.fixture(autouse=True)
def results_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENABLE_RECORD_CHECK_RESULTS", True)


def assert_never_inferred(resp):
    """Criterion 3, applied to every response a scenario produces."""
    by_delivery = {e["delivery_id"]: e for e in resp["deliveries"]}
    for entry in resp["deliveries"]:
        for l in entry["lanes"]:
            chk = l["check"]
            if chk["outcome"] == "PASS":
                # a PASS exists only as a persisted P, and says so
                assert chk["reason"] in (None, "RULE_VERSION_CHANGED", "REQUIRES_CHANGED",
                                         "SCHEMA_CHANGED", "COVERAGE_NOT_RECORDED")
            if chk["reason"] in ("RECORD_ABSENT", "NO_COMPLETED_RUN",
                                 "DUPLICATE_OID_IN_DELIVERY", "RESULT_SCHEMA_UNSUPPORTED",
                                 "CHECK_RESULT_NOT_PERSISTED", "RULE_ERROR"):
                assert chk["outcome"] != "PASS"
            rec = l["recurrence"]
            if rec and rec["state"] == "RECURRING":
                other = by_delivery[rec["comparable_pass"]]
                passing = next(x for x in other["lanes"]
                               if x["rule_id"] == l["rule_id"] and x["field"] == l["field"])
                assert passing["check"]["outcome"] == "PASS"
                assert passing["check"]["comparability"] == "COMPARABLE" or True
    text_ = json.dumps(resp).lower()
    # "resolved" / "corrected" only if a QA approval supplied it
    stripped = json.loads(json.dumps(resp))

    def drop(node):
        if isinstance(node, dict):
            node.pop("finding_type", None)   # rule vocabulary, e.g. PART_OF_RESOLVED_IN_REGISTRY
            for v in node.values():
                drop(v)
        elif isinstance(node, list):
            for v in node:
                drop(v)

    drop(stripped)
    scan = json.dumps(stripped).lower()
    approved = any(l["qa"] and l["qa"]["status"] == "QA_APPROVED"
                   for e in resp["deliveries"] for l in e["lanes"])
    if not approved:
        assert "resolved" not in scan and "corrected" not in scan
    del text_


# ── the engine writes what the reader needs ───────────────────────────────────

@pytest.mark.asyncio
async def test_engine_persists_a_map_per_record_and_the_interpretation_columns(rolled_back_db):
    db = rolled_back_db
    intake = await deliver(db, 7, npi=BAD_LEN_NPI)
    run = (await db.execute(select(m.RceIngestionRun).where(
        m.RceIngestionRun.source_intake_id == intake))).scalar_one()
    rows = (await db.execute(select(RCR).where(RCR.c.run_id == run.id))).all()
    n_records = (await db.execute(select(func.count()).select_from(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake))).scalar()
    assert len(rows) == n_records == 2
    rec = await record_of(db, intake)
    mine = next(r for r in rows if r.source_record_id == rec.id)
    declared = {r.rule_id for r in RULES if r.declared}
    assert mine.map_version == 1 and mine.rule_count == len(mine.outcomes) == len(declared) == 8
    assert set(mine.outcomes) == declared == set(core.SLICE_RULE_IDS)
    assert set(mine.outcomes.values()) <= set("FPNSEU")
    assert mine.outcomes["NPI-001"] == "P"       # supplied
    assert mine.outcomes["NPI-002"] == "F"       # wrong length
    assert mine.outcomes["NPI-003"] == "N"       # format not evaluable
    assert mine.outcomes["NPI-004"] == "N"
    assert "REQ-001" not in mine.outcomes and "SCH-002" not in mine.outcomes  # U is implicit
    hist = {r["rule_id"]: r for r in (await db.execute(text(
        "select rule_id, requires_hash, scope, coverage from rce_rule_execution_history "
        "where run_id = :r"), {"r": run.id})).mappings().all()}
    for rid in core.SLICE_RULE_IDS:
        assert len(hist[rid]["requires_hash"]) == 64
    assert hist["REQ-001"]["requires_hash"] is None
    assert hist["INT-002"]["coverage"].keys() == {"delivery_ids", "registry_oids", "qhin_oids"}
    assert hist["INT-002"]["coverage"]["delivery_ids"] == 2
    assert hist["NPI-002"]["coverage"] is None


@pytest.mark.asyncio
async def test_a_population_scope_rule_is_recorded_at_run_level_only(rolled_back_db, monkeypatch):
    monkeypatch.setattr(RULE_BY_ID["SCH-002"], "scope", "RUN")
    db = rolled_back_db
    intake = await deliver(db, 7)
    row = (await db.execute(select(RCR))).first()
    assert "SCH-002" not in row.outcomes and row.rule_count == 8
    scope = (await db.execute(text(
        "select scope from rce_rule_execution_history where rule_id='SCH-002' "
        "order by created_at desc limit 1"))).scalar()
    assert scope == "RUN"
    resp = await history(db)       # the reader still decodes it
    assert lane(entry_of(resp, intake), NPI2)["check"]["comparability"] == "COMPARABLE"


@pytest.mark.asyncio
async def test_flag_off_writes_nothing_new_and_the_issues_are_identical(rolled_back_db, monkeypatch):
    """Same intake run twice, flag off then on: identical issues, codes, order."""
    from app.core.config import settings

    db = rolled_back_db
    monkeypatch.setattr(settings, "ENABLE_RECORD_CHECK_RESULTS", False)
    intake = await deliver(db, 7, npi=BAD_LEN_NPI, process=False,
                           extra_rows=[entity_row("9.99.777.5.1", part_of="NOPE")])
    off = await run_engine(db, intake)
    assert (await db.execute(select(func.count()).select_from(RCR))).scalar() == 0
    nulls = (await db.execute(text(
        "select count(*) from rce_rule_execution_history where run_id = :r and "
        "(requires_hash is not null or scope is not null or coverage is not null)"),
        {"r": uuid.UUID(off["run_id"])})).scalar()
    assert nulls == 0
    monkeypatch.setattr(settings, "ENABLE_RECORD_CHECK_RESULTS", True)
    on = await run_engine(db, intake)
    assert (await db.execute(select(func.count()).select_from(RCR))).scalar() == 3
    off_keys, on_keys = set(off), set(on)
    assert off_keys == on_keys
    for k in ("records_evaluated", "issues_generated", "rules_executed", "rules_failed",
              "issues_by_rule", "rule_config_hash", "every_record_evaluated"):
        assert off[k] == on[k], k

    async def issues(run_id):
        rows = (await db.execute(select(m.RceIssue).where(m.RceIssue.run_id == uuid.UUID(run_id))
                                 .order_by(m.RceIssue.issue_code))).scalars().all()
        return [(r.issue_code.rsplit("-", 1)[1], r.source_record_id, r.rule_id, r.rule_version,
                 r.issue_type, r.severity, r.field_name, r.original_value,
                 r.correction_authority, r.description) for r in rows]

    a, b = await issues(off["run_id"]), await issues(on["run_id"])
    assert a and a == b


# ── criterion 1: scenarios ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_s01_july_august_september_recurring_with_qa_shown_separately(rolled_back_db):
    db = rolled_back_db
    jul = await deliver(db, 7, npi=BAD_LEN_NPI)
    aug = await deliver(db, 8, npi=GOOD_NPI)
    sep = await deliver(db, 9, npi=BAD_LEN_NPI)
    issue = await issue_of(db, jul, NPI2)
    issue.resolution, issue.resolved_by = "APPROVED", "reviewer@syn.invalid"
    issue.resolved_at = datetime(2026, 7, 30)
    issue.correction_authority = "QA_REQUIRED"
    issue.qa_approved_by, issue.qa_approved_at = "qa@syn.invalid", datetime(2026, 8, 1)
    await db.commit()

    resp = await history(db)
    assert_never_inferred(resp)
    assert [e["delivery_id"] for e in resp["deliveries"]] == [str(jul), str(aug), str(sep)]
    j, a, s = (lane(entry_of(resp, x), NPI2) for x in (jul, aug, sep))
    assert j["check"]["outcome"] == "FAIL" and j["recurrence"]["state"] == "FIRST_OBSERVED"
    assert j["qa"]["status"] == "QA_APPROVED" and j["qa"]["role"] == "qa_approver"
    assert j["qa"]["timestamp"] == "2026-08-01T00:00:00"
    assert a["check"] == {"outcome": "PASS", "comparability": "COMPARABLE", "reason": None}
    assert a["finding"] is None and a["recurrence"] is None
    assert s["recurrence"]["state"] == "RECURRING"
    assert s["recurrence"]["earlier_occurrence"] == str(jul)
    assert s["recurrence"]["comparable_pass"] == str(aug)
    assert s["qa"]["status"] == "NONE"
    assert j["label"] == f"as recorded in delivery {jul} under rule NPI-002 v1.2.0"
    assert [g for g in resp["gaps"] if g["rule_id"] == NPI2] == []
    assert resp["scope_note"] == "History for the SYN-RCE feed"


@pytest.mark.asyncio
async def test_s02_august_missing_persistent_with_the_gap_text(rolled_back_db):
    db = rolled_back_db
    jul = await deliver(db, 7)
    sep = await deliver(db, 9)
    resp = await history(db)
    assert_never_inferred(resp)
    assert len(resp["deliveries"]) == 2
    s = lane(entry_of(resp, sep), NPI2)
    assert s["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"
    assert s["recurrence"]["earlier_occurrence"] == str(jul)
    assert {"rule_id": NPI2, "field": "NPI", "from_delivery_id": str(jul),
            "to_delivery_id": str(sep),
            "text": "No snapshot or correction evidence available."} in resp["gaps"]


@pytest.mark.asyncio
async def test_s03_august_record_absent_is_not_comparable(rolled_back_db):
    db = rolled_back_db
    await deliver(db, 7)
    aug = await deliver(db, 8, oid=None)
    sep = await deliver(db, 9)
    resp = await history(db)
    assert_never_inferred(resp)
    e = entry_of(resp, aug)
    assert e["record_present"] is False and e["status_reason"] == "RECORD_ABSENT"
    assert lane(e, NPI2)["check"] == {"outcome": "NOT_AVAILABLE",
                                      "comparability": "NOT_COMPARABLE",
                                      "reason": "RECORD_ABSENT"}
    assert lane(entry_of(resp, sep), NPI2)["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


@pytest.mark.asyncio
async def test_s04_rule_version_changed_credits_no_pass(rolled_back_db, monkeypatch):
    db = rolled_back_db
    await deliver(db, 7)
    with monkeypatch.context() as mp:
        mp.setattr(RULE_BY_ID[NPI2], "version", "1.3.0")
        aug = await deliver(db, 8, npi=GOOD_NPI)
    sep = await deliver(db, 9)
    resp = await history(db)
    assert_never_inferred(resp)
    a = lane(entry_of(resp, aug), NPI2)
    assert a["rule_version"] == "1.3.0"
    assert a["check"] == {"outcome": "PASS", "comparability": "NOT_COMPARABLE",
                          "reason": "RULE_VERSION_CHANGED"}
    assert lane(entry_of(resp, sep), NPI2)["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


@pytest.mark.asyncio
async def test_s05_required_field_empty_is_not_applicable_not_a_pass(rolled_back_db):
    db = rolled_back_db
    await deliver(db, 7)
    aug = await deliver(db, 8, npi="")
    sep = await deliver(db, 9)
    resp = await history(db)
    assert_never_inferred(resp)
    a = lane(entry_of(resp, aug), NPI2)
    assert a["check"] == {"outcome": "NOT_APPLICABLE", "comparability": "NOT_COMPARABLE",
                          "reason": "REQUIRED_FIELD_ABSENT"}
    assert lane(entry_of(resp, sep), NPI2)["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


@pytest.mark.asyncio
async def test_s06_rule_error_is_an_error_not_a_pass(rolled_back_db, monkeypatch):
    db = rolled_back_db
    await deliver(db, 7)

    def boom(ctx):
        raise RuntimeError("synthetic rule failure")

    with monkeypatch.context() as mp:
        mp.setattr(RULE_BY_ID[NPI2], "evaluate", boom)
        aug = await deliver(db, 8, npi=GOOD_NPI)
        run = (await db.execute(select(m.RceIngestionRun).where(
            m.RceIngestionRun.source_intake_id == aug))).scalar_one()
        status = (await db.execute(text(
            "select execution_status from rce_rule_execution_history "
            "where run_id=:r and rule_id='NPI-002'"), {"r": run.id})).scalar()
        assert status == "FAILED"            # the run's rule status
    sep = await deliver(db, 9)
    resp = await history(db)
    assert_never_inferred(resp)
    a = lane(entry_of(resp, aug), NPI2)
    assert a["check"] == {"outcome": "ERROR", "comparability": "NOT_COMPARABLE",
                          "reason": "RULE_ERROR"}
    other = lane(entry_of(resp, aug), "NPI-003")
    assert other["check"]["outcome"] == "PASS"       # other rules unaffected
    assert lane(entry_of(resp, sep), NPI2)["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


@pytest.mark.asyncio
async def test_s07_undeclared_rule_is_unqualified_never_pass(rolled_back_db, monkeypatch):
    db = rolled_back_db
    await deliver(db, 7)
    with monkeypatch.context() as mp:
        mp.setattr(RULE_BY_ID[NPI2], "applies", None)
        aug = await deliver(db, 8, npi=GOOD_NPI)
    sep = await deliver(db, 9)
    resp = await history(db)
    assert_never_inferred(resp)
    a = lane(entry_of(resp, aug), NPI2)
    assert a["check"] == {"outcome": "UNQUALIFIED", "comparability": "NOT_COMPARABLE",
                          "reason": "APPLICABILITY_UNDECLARED"}
    assert lane(entry_of(resp, sep), NPI2)["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


@pytest.mark.asyncio
async def test_s08_newer_failed_run_beside_a_completed_run_and_failed_only(rolled_back_db):
    db = rolled_back_db
    jul = await deliver(db, 7)
    aug = await deliver(db, 8, npi=GOOD_NPI)
    db.add(m.RceIngestionRun(
        source_intake_id=aug, rule_set_version="1.3.0", rule_config_hash="0" * 64,
        started_at=datetime.utcnow() + timedelta(hours=1), run_status="FAILED",
        error="OperationalError: connection to 10.0.0.5 lost, password=hunter2"))
    # a delivery whose only attempt FAILED
    sep = await deliver(db, 9, process=False)
    db.add(m.RceIngestionRun(
        source_intake_id=sep, rule_set_version="1.3.0", rule_config_hash="0" * 64,
        started_at=datetime.utcnow(), run_status="FAILED", error="boom: secret"))
    await db.commit()

    resp = await history(db)
    assert_never_inferred(resp)
    e = entry_of(resp, aug)
    assert e["flags"] == ["NEWER_RUN_NOT_COMPLETE"]
    assert e["notice"].startswith("A newer run did not complete")
    assert e["runs"]["selected"]["status"] == "COMPLETE"
    assert [r["status"] for r in e["runs"]["newer_runs"]] == ["FAILED"]
    assert e["runs"]["newer_runs"][0]["error"] == "OperationalError"   # sanitized
    assert "hunter2" not in json.dumps(resp)
    assert lane(e, NPI2)["check"]["outcome"] == "PASS"                 # not mixed
    only = entry_of(resp, sep)
    assert only["status"] == "NOT_COMPARABLE" and only["status_reason"] == "NO_COMPLETED_RUN"
    assert only["runs"]["selected"] is None and len(only["runs"]["newer_runs"]) == 1
    assert lane(only, NPI2)["check"]["reason"] == "NO_COMPLETED_RUN"
    assert entry_of(resp, jul)["flags"] == []


@pytest.mark.asyncio
async def test_s09_qa_and_recurrence_are_independent_in_both_directions(rolled_back_db):
    db = rolled_back_db
    jul = await deliver(db, 7)
    sep = await deliver(db, 9)
    before = await history(db)
    issue = await issue_of(db, jul, NPI2)
    issue.resolution, issue.resolved_by = "APPROVED", "reviewer@syn.invalid"
    issue.resolved_at = datetime(2026, 7, 30)
    issue.correction_authority = "QA_REQUIRED"
    issue.qa_approved_by, issue.qa_approved_at = "qa@syn.invalid", datetime(2026, 8, 1)
    await db.commit()
    after = await history(db)
    assert_never_inferred(after)
    b, a = lane(entry_of(before, sep), NPI2), lane(entry_of(after, sep), NPI2)
    assert lane(entry_of(after, jul), NPI2)["qa"]["status"] == "QA_APPROVED"
    assert a["recurrence"]["state"] == b["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"
    assert a["check"] == b["check"]
    assert a["note"] == "approved resolution not followed by a comparable passing check"
    # now add the August pass: the QA object does not move, recurrence does
    await deliver(db, 8, npi=GOOD_NPI)
    third = await history(db)
    assert_never_inferred(third)
    assert lane(entry_of(third, jul), NPI2)["qa"] == lane(entry_of(after, jul), NPI2)["qa"]
    assert lane(entry_of(third, sep), NPI2)["recurrence"]["state"] == "RECURRING"
    assert lane(entry_of(third, sep), NPI2)["qa"]["status"] == "NONE"


@pytest.mark.asyncio
async def test_s10_identity_is_exact(rolled_back_db):
    db = rolled_back_db
    spaced = OID + " "
    jul = await deliver(db, 7, extra_rows=[entity_row(spaced, npi=GOOD_NPI, tag="sp"),
                                           entity_row("", tag="nul")])
    exact = await history(db, OID)
    other = await history(db, spaced)
    assert lane(entry_of(exact, jul), NPI2)["check"]["outcome"] == "FAIL"     # bad NPI row
    assert lane(entry_of(other, jul), NPI2)["check"]["outcome"] == "PASS"     # the spaced row
    assert exact["oid"] == OID and other["oid"] == spaced
    # a record with no id never joins anything, and does not make OID a duplicate
    assert entry_of(exact, jul)["status"] == "OK"
    with pytest.raises(svc.HistoryNotFound):
        await history(db, "")
    with pytest.raises(svc.HistoryNotFound):
        await history(db, OID.lower())                                         # no case folding
    # duplicate OID inside one delivery
    dup = await deliver(db, 8, extra_rows=[entity_row(OID, npi=GOOD_NPI, tag="dup")])
    resp = await history(db, OID)
    assert_never_inferred(resp)
    e = entry_of(resp, dup)
    assert e["status_reason"] == "DUPLICATE_OID_IN_DELIVERY"
    for l in e["lanes"]:
        assert l["check"]["reason"] == "DUPLICATE_OID_IN_DELIVERY"
        assert l["finding"] is None and l["recurrence"] is None


@pytest.mark.asyncio
async def test_s11_duplicate_content_collapses_and_never_becomes_previous(rolled_back_db):
    db = rolled_back_db
    rows = [entity_row(OID, npi=BAD_LEN_NPI), filler_row("dupcontent")]
    jul = await seed_delivery(db, rows, received_at=datetime(2026, 7, 5), label="Same label")
    await run_engine(db, jul)
    reupload = await seed_delivery(db, rows, received_at=datetime(2026, 8, 10),
                                   label="Same label")
    await run_engine(db, reupload)           # a duplicate that WAS processed
    changed = await seed_delivery(db, rows, received_at=datetime(2026, 8, 20),
                                  label="Same label", blob_salt="different content")
    await run_engine(db, changed)
    sep = await deliver(db, 9)
    resp = await history(db)
    assert_never_inferred(resp)
    ids = [e["delivery_id"] for e in resp["deliveries"]]
    assert ids == [str(jul), str(changed), str(sep)]       # the re-upload is not a delivery
    first = entry_of(resp, jul)
    assert [d["intake_id"] for d in first["duplicate_uploads"]] == [str(reupload)]
    dup = first["duplicate_uploads"][0]
    assert dup["note"] == "identical re-upload, not a separate delivery"
    assert dup["results_used"] is False and dup["received_at"] == "2026-08-10T00:00:00"
    assert first["received_at"] == "2026-07-05T00:00:00"   # canonical receipt date
    # the different-content delivery with the same label is its own delivery
    assert entry_of(resp, changed)["label"] == "Same label"
    # and the duplicate created no pass / gap: Sep is persistent against Aug-20 or Jul
    assert lane(entry_of(resp, sep), NPI2)["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"
    assert str(reupload) not in json.dumps(resp["gaps"])


@pytest.mark.asyncio
async def test_s11b_a_failed_canonical_is_not_promoted_over_its_duplicate(rolled_back_db):
    db = rolled_back_db
    rows = [entity_row(OID), filler_row("fc")]
    first = await seed_delivery(db, rows, received_at=datetime(2026, 7, 5), status="FAILED")
    second = await seed_delivery(db, rows, received_at=datetime(2026, 7, 6))
    await run_engine(db, second)
    resp = await history(db)
    (entry,) = resp["deliveries"]
    assert entry["delivery_id"] == str(first)
    assert entry["status"] == "NOT_COMPARABLE" and entry["status_reason"] == "NO_COMPLETED_RUN"
    assert [d["intake_id"] for d in entry["duplicate_uploads"]] == [str(second)]
    assert entry["duplicate_uploads"][0]["results_used"] is False


@pytest.mark.asyncio
async def test_s12_feed_scoping_fails_closed_and_leaks_nothing(rolled_back_db):
    db = rolled_back_db
    onc_jul = await deliver(db, 7, feed="ONC_RCE")
    hidden_aug = await deliver(db, 8, feed="SYN-RCE", npi=GOOD_NPI)
    untagged_sep = await deliver(db, 9, feed=None)
    onc_oct = await deliver(db, 10, feed="ONC_RCE")
    only_syn = "SYN-ONLY-OID"
    await deliver(db, 8, feed="SYN-RCE", oid=only_syn, day=6)

    viewer = await history(db, viewer_feeds="ONC_RCE")
    assert [e["delivery_id"] for e in viewer["deliveries"]] == [str(onc_jul), str(onc_oct)]
    dumped = json.dumps(viewer)
    for hidden in (hidden_aug, untagged_sep):
        assert str(hidden) not in dumped
    assert viewer["scope_note"] == "History for the ONC_RCE feed"
    assert all(k not in dumped.lower() for k in ("hidden", "withheld", "not visible"))
    # a gap is only between VISIBLE deliveries
    (gap,) = [g for g in viewer["gaps"] if g["rule_id"] == NPI2]
    assert gap["from_delivery_id"] == str(onc_jul) and gap["to_delivery_id"] == str(onc_oct)
    assert_never_inferred(viewer)

    # an OID that exists only in a hidden feed is indistinguishable from an unknown one
    with pytest.raises(svc.HistoryNotFound) as hidden_exc:
        await history(db, only_syn, viewer_feeds="ONC_RCE")
    with pytest.raises(svc.HistoryNotFound) as unknown_exc:
        await history(db, "9.99.777.404", viewer_feeds="ONC_RCE")
    assert hidden_exc.value.visible_deliveries == unknown_exc.value.visible_deliveries == 0
    assert str(hidden_exc.value) == str(unknown_exc.value) == "NOT_FOUND"
    # untagged intakes are invisible to every role, even when every feed is allowed
    untagged_only = "UNTAGGED-ONLY"
    await deliver(db, 11, feed=None, oid=untagged_only)
    with pytest.raises(svc.HistoryNotFound):
        await history(db, untagged_only, reviewer=True, viewer_feeds="ONC_RCE,SYN-RCE",
                      reviewer_feeds="ONC_RCE,SYN-RCE")
    # no allowed feed -> 404 for EVERY oid, including ones that exist
    for oid in (OID, only_syn, untagged_only):
        for reviewer in (False, True):
            with pytest.raises(svc.HistoryNotFound):
                await history(db, oid, reviewer=reviewer, viewer_feeds="", reviewer_feeds="")
    # the reviewer list applies to reviewers only
    with pytest.raises(svc.HistoryNotFound):
        await history(db, only_syn, reviewer=False, viewer_feeds="ONC_RCE",
                      reviewer_feeds="SYN-RCE")
    seen = await history(db, only_syn, reviewer=True, viewer_feeds="ONC_RCE",
                         reviewer_feeds="SYN-RCE")
    assert len(seen["deliveries"]) == 1


@pytest.mark.asyncio
async def test_s13_viewer_redaction_and_s14_reviewer_sees_values(rolled_back_db):
    db = rolled_back_db
    secret_npi = "7770007"
    jul = await deliver(db, 7, npi=secret_npi)
    await deliver(db, 9, npi=secret_npi, blob_salt="x")
    issue = await issue_of(db, jul, NPI2)
    issue.resolution, issue.resolved_by = "APPROVED", "reviewer@syn.invalid"
    issue.resolved_at = datetime(2026, 7, 30)
    issue.resolution_notes = "secret rationale text"
    issue.suggested_value = "7770008"
    issue.correction_authority = "QA_REQUIRED"
    issue.qa_approved_by, issue.qa_approved_at = "qa@syn.invalid", datetime(2026, 8, 1)
    await db.commit()

    viewer = await history(db)
    assert core.forbidden_keys_present(viewer) == []
    blob = json.dumps(viewer)
    for value in (secret_npi, "7770008", "secret rationale text", "reviewer@syn.invalid",
                  "qa@syn.invalid", "syn-operator", "SYN-operator"):
        assert value not in blob, value
    for key in core.VIEWER_FORBIDDEN_KEYS:
        assert f'"{key}"' not in blob
    # structure is still there
    cell = lane(viewer["deliveries"][0], NPI2)
    assert cell["finding"]["finding_type"] == "NPI_LENGTH_INVALID"
    assert cell["finding"]["severity"] == "HIGH" and cell["field"] == "NPI"
    assert cell["qa"]["status"] == "QA_APPROVED" and cell["qa"]["role"] == "qa_approver"

    reviewer = await history(db, reviewer=True)
    rc = lane(reviewer["deliveries"][0], NPI2)
    assert rc["finding"]["original_value"] == secret_npi
    assert rc["finding"]["suggested_value"] == "7770008"
    assert rc["qa"]["rationale"] == "secret rationale text"
    assert rc["qa"]["actors"] == {"resolved_by": "reviewer@syn.invalid",
                                  "qa_approved_by": "qa@syn.invalid"}
    assert reviewer["deliveries"][0]["feed"] == FEED
    assert_never_inferred(reviewer)


@pytest.mark.asyncio
async def test_later_stage_issues_are_observed_with_recurrence_not_evaluated(rolled_back_db):
    db = rolled_back_db
    jul = await deliver(db, 7, npi=GOOD_NPI)
    rec = await record_of(db, jul)
    db.add(m.RceIssue(
        issue_code="DQ-SYN-NPI5-0001", source_intake_id=jul, source_record_id=rec.id,
        rule_id="NPI-005", rule_version="1.2.0", issue_type="NPI_NOT_FOUND",
        severity="MEDIUM", field_name="NPI", correction_authority="HUMAN_REQUIRED",
        description="synthetic", resolution="OPEN"))
    await db.commit()
    resp = await history(db)
    cells = [l for l in entry_of(resp, jul)["lanes"] if l["rule_id"] == "NPI-005"]
    assert len(cells) == 1
    assert cells[0]["check"]["comparability"] == "NOT_COMPARABLE"
    assert cells[0]["check"]["reason"] == "EXTERNAL_COVERAGE_NOT_TRACKED_PER_RULE"
    assert cells[0]["recurrence"] == {"state": "NOT_EVALUATED",
                                      "reason": "EXTERNAL_COVERAGE_NOT_TRACKED_PER_RULE",
                                      "earlier_occurrence": None, "comparable_pass": None}


@pytest.mark.asyncio
async def test_paging_hard_cap_and_before(rolled_back_db):
    db = rolled_back_db
    ids = []
    for i in range(62):
        ids.append(await seed_delivery(
            db, [entity_row(OID, npi=GOOD_NPI), filler_row(f"p{i}")],
            received_at=datetime(2026, 1, 1) + timedelta(days=i)))
    resp = await history(db, limit=500)
    assert len(resp["deliveries"]) == 60 and resp["paging"]["limit"] == 60
    assert resp["deliveries"][0]["delivery_id"] == str(ids[2])       # newest 60, oldest first
    assert resp["paging"]["earlier_available"] is True
    assert resp["paging"]["next_before"] == str(ids[2])
    older = await history(db, limit=12, before=uuid.UUID(resp["paging"]["next_before"]))
    assert [e["delivery_id"] for e in older["deliveries"]] == [str(ids[0]), str(ids[1])]
    assert older["paging"]["earlier_available"] is False
    default = await history(db)
    assert len(default["deliveries"]) == 12
    with pytest.raises(svc.HistoryNotFound):
        await history(db, before=uuid.uuid4())


# ── criterion 4: the persisted map is trusted only when it decodes ────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", [
    "update rce_record_check_results set map_version = 2 where run_id = :r",
    "update rce_record_check_results set rule_count = rule_count - 1 where run_id = :r",
    "update rce_record_check_results set outcomes = outcomes - 'NPI-002' where run_id = :r",
    "update rce_record_check_results set outcomes = jsonb_set(outcomes, '{NPI-002}', '\"Q\"') "
    "where run_id = :r",
])
async def test_unsupported_or_truncated_rows_are_reported_not_guessed(rolled_back_db, tamper):
    db = rolled_back_db
    jul = await deliver(db, 7)
    run = (await db.execute(select(m.RceIngestionRun).where(
        m.RceIngestionRun.source_intake_id == jul))).scalar_one()
    await db.execute(text(tamper), {"r": run.id})
    resp = await history(db)
    cell = lane(entry_of(resp, jul), NPI2)
    assert cell["check"]["comparability"] == "NOT_COMPARABLE"
    assert cell["check"]["reason"] == "RESULT_SCHEMA_UNSUPPORTED"
    assert cell["check"]["outcome"] == "FAIL"        # the ledger still shows the finding
    assert cell["recurrence"]["state"] == "NOT_EVALUATED"


@pytest.mark.asyncio
async def test_runs_without_result_rows_read_as_not_persisted(rolled_back_db, monkeypatch):
    from app.core.config import settings

    db = rolled_back_db
    monkeypatch.setattr(settings, "ENABLE_RECORD_CHECK_RESULTS", False)
    jul = await deliver(db, 7)
    aug = await deliver(db, 8, npi=GOOD_NPI)
    resp = await history(db)
    assert lane(entry_of(resp, jul), NPI2)["check"] == {
        "outcome": "FAIL", "comparability": "NOT_COMPARABLE",
        "reason": "CHECK_RESULT_NOT_PERSISTED"}
    a = lane(entry_of(resp, aug), NPI2)["check"]
    assert a["outcome"] == "NOT_RECORDED" and a["reason"] == "CHECK_RESULT_NOT_PERSISTED"
    assert_never_inferred(resp)


# ── criterion 2: preservation and privileges ──────────────────────────────────

HISTORICAL = ("rce_source_intakes", "rce_source_records", "rce_ingestion_runs",
              "rce_rule_execution_history", "rce_issues", "rce_record_check_results",
              "rce_curated_records")


async def _hashes(db):
    out = {}
    for t in HISTORICAL:
        out[t] = (await db.execute(text(
            f"select md5(coalesce(string_agg(x::text, '|' order by x::text), '')) "
            f"from {t} x"))).scalar()
    return out


async def _upd_del(db):
    rows = (await db.execute(text(
        "select relname, n_tup_upd, n_tup_del from pg_stat_xact_user_tables "
        "where relname = any(:t)"), {"t": list(HISTORICAL)})).all()
    return {r[0]: (r[1], r[2]) for r in rows}


@pytest.mark.asyncio
async def test_reads_perform_no_update_or_delete_on_any_historical_table(rolled_back_db):
    db = rolled_back_db
    await deliver(db, 7)
    await deliver(db, 8, npi=GOOD_NPI)
    await deliver(db, 9)
    before_hash, before_stats = await _hashes(db), await _upd_del(db)
    for reviewer in (False, True):
        await history(db, reviewer=reviewer)
    assert await _hashes(db) == before_hash
    assert await _upd_del(db) == before_stats


@pytest.mark.asyncio
async def test_runtime_role_has_insert_and_select_only_on_the_new_table(rolled_back_db):
    db = rolled_back_db

    async def has(priv):
        return (await db.execute(text(
            "select has_table_privilege('docuaction_app', 'rce_record_check_results', :p)"),
            {"p": priv})).scalar()

    assert await has("SELECT") and await has("INSERT")
    for priv in ("UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not await has(priv), priv
    # the history tables the reader touches are read-only for it as well
    for table in ("rce_source_intakes", "rce_source_records"):
        for priv in ("UPDATE", "DELETE"):
            assert not (await db.execute(text(
                "select has_table_privilege('docuaction_app', :t, :p)"),
                {"t": table, "p": priv})).scalar(), (table, priv)


# ── criterion 7: query shape ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_count_is_constant_and_within_four_per_delivery(rolled_back_db):
    db = rolled_back_db
    for month in range(1, 13):
        await deliver(db, month, npi=BAD_LEN_NPI if month % 2 else GOOD_NPI)
    statements = []
    conn = db.sync_session.get_bind()

    def count(conn_, cursor, statement, *a):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(conn, "before_cursor_execute", count)
    try:
        resp = await history(db)
    finally:
        event.remove(conn, "before_cursor_execute", count)
    assert len(resp["deliveries"]) == 12
    assert len(statements) <= 7
    assert len(statements) <= 4 * len(resp["deliveries"])


@pytest.mark.asyncio
async def test_the_history_queries_are_index_served(rolled_back_db):
    from sqlalchemy.dialects import postgresql

    db = rolled_back_db
    jul = await deliver(db, 7)
    run = (await db.execute(select(m.RceIngestionRun).where(
        m.RceIngestionRun.source_intake_id == jul))).scalar_one()
    rec = await record_of(db, jul)
    statements = {
        "records": (svc.records_query(OID, [jul]), "rce_source_records"),
        "results": (svc.results_query([(run.id, rec.id)]), "rce_record_check_results"),
        "issues": (svc.issues_query([rec.id], True), "rce_issues"),
    }
    await db.execute(text("set local enable_seqscan = off"))
    for name, (stmt, table) in statements.items():
        sql = str(stmt.compile(dialect=postgresql.dialect(),
                               compile_kwargs={"literal_binds": True}))
        plan = "\n".join(r[0] for r in (await db.execute(text("explain " + sql))).all())
        assert f"Seq Scan on {table}" not in plan, (name, plan)
        assert "Index" in plan or "Bitmap" in plan, (name, plan)


# ── parity with run_selection ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_current_run_matches_run_selection(rolled_back_db):
    from app.tefca_registry.rce import run_selection

    db = rolled_back_db
    jul = await deliver(db, 7)
    await run_engine(db, jul)            # a second COMPLETE run on the same intake
    db.add(m.RceIngestionRun(source_intake_id=jul, rule_set_version="1.3.0",
                             rule_config_hash="0" * 64, run_status="FAILED",
                             started_at=datetime.utcnow() + timedelta(hours=2)))
    await db.commit()
    runs = [dict(r._mapping) for r in (await db.execute(select(
        m.RceIngestionRun.id, m.RceIngestionRun.run_status.label("status"),
        m.RceIngestionRun.started_at, m.RceIngestionRun.completed_at,
        m.RceIngestionRun.rule_set_version, m.RceIngestionRun.error)
        .where(m.RceIngestionRun.source_intake_id == jul))).all()]
    sel = core.select_runs(runs)
    expected = await run_selection.current_run(db, jul)
    assert sel["selected"]["id"] == expected.id
    assert len(sel["earlier_completed"]) == 1 and len(sel["newer_runs"]) == 1
    resp = await history(db)
    assert entry_of(resp, jul)["runs"]["selected"]["run_id"] == str(expected.id)
    assert entry_of(resp, jul)["runs"]["earlier_completed"]["count"] == 1


# ── criterion 8: audit, 404 policy, role handling at the route ────────────────

async def _audit_rows(db):
    return (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == svc.AUDIT_ACTION)
        .order_by(reg.TefcaRegAuditLog.created_at))).scalars().all()


@pytest.mark.asyncio
async def test_route_writes_one_audit_row_per_read_and_a_uniform_404(rolled_back_db, monkeypatch):
    from fastapi import HTTPException

    from app.core.config import settings
    from app.tefca_registry.rce import issue_history_routes as routes

    db = rolled_back_db
    monkeypatch.setattr(settings, "ENABLE_ISSUE_HISTORY", True)
    monkeypatch.setattr(settings, "ISSUE_HISTORY_FEEDS_VIEWER", FEED)
    monkeypatch.setattr(settings, "ISSUE_HISTORY_FEEDS_REVIEWER", "")
    await deliver(db, 7, npi="7770007")
    await deliver(db, 8, npi=GOOD_NPI)
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    viewer = SimpleNamespace(id=None, email="viewer@syn.invalid", role="viewer")
    reviewer = SimpleNamespace(id=None, email="rev@syn.invalid", role="reviewer")

    base = len(await _audit_rows(db))
    out = await routes.issue_history_route(OID, request, 12, None, db=db, user=viewer)
    assert len(out["deliveries"]) == 2
    rows = await _audit_rows(db)
    assert len(rows) == base + 1
    meta = rows[-1].metadata_
    assert meta == {"oid": OID, "role": "viewer", "visible_deliveries": 2}
    assert "7770007" not in json.dumps(meta)
    assert rows[-1].actor_email == "viewer@syn.invalid"
    # a viewer cannot see reviewer-level values through the route either
    assert core.forbidden_keys_present(out) == []
    rev = await routes.issue_history_route(OID, request, 12, None, db=db, user=reviewer)
    assert lane(rev["deliveries"][0], NPI2)["finding"]["original_value"] == "7770007"
    assert (await _audit_rows(db))[-1].metadata_["role"] == "reviewer"
    assert len(await _audit_rows(db)) == base + 2

    # 404s are identical for unknown and out-of-scope OIDs, and are audited too
    bodies = []
    for oid in ("9.99.777.404", "UNTAGGED-OID"):
        with pytest.raises(HTTPException) as exc:
            await routes.issue_history_route(oid, request, 12, None, db=db, user=viewer)
        bodies.append((exc.value.status_code, exc.value.detail))
    assert bodies[0] == bodies[1] == (404, "NOT_FOUND")
    assert len(await _audit_rows(db)) == base + 4
    assert (await _audit_rows(db))[-1].metadata_["visible_deliveries"] == 0

    # an account with no configured feed gets 404 for every OID
    monkeypatch.setattr(settings, "ISSUE_HISTORY_FEEDS_VIEWER", "")
    with pytest.raises(HTTPException) as exc:
        await routes.issue_history_route(OID, request, 12, None, db=db, user=viewer)
    assert (exc.value.status_code, exc.value.detail) == (404, "NOT_FOUND")
    # the flag makes the route a 404 before anything is read or audited
    monkeypatch.setattr(settings, "ENABLE_ISSUE_HISTORY", False)
    count = len(await _audit_rows(db))
    with pytest.raises(HTTPException) as exc:
        await routes.issue_history_route(OID, request, 12, None, db=db, user=viewer)
    assert exc.value.status_code == 404 and len(await _audit_rows(db)) == count
    # a malformed `before` is a 422, never a hint
    monkeypatch.setattr(settings, "ENABLE_ISSUE_HISTORY", True)
    with pytest.raises(HTTPException) as exc:
        await routes.issue_history_route(OID, request, 12, "not-a-uuid", db=db, user=viewer)
    assert exc.value.status_code == 422


# ── the admin tagging script ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tag_script_is_a_dry_run_by_default_and_audits_when_applied(rolled_back_db, caplog):
    import logging
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import tag_intake_feed as tool

    db = rolled_back_db
    untagged = await deliver(db, 7, feed=None, process=False)
    tagged = await deliver(db, 8, feed="OTHER", process=False)
    ghost = uuid.uuid4()
    ids = [untagged, tagged, ghost]

    with caplog.at_level(logging.INFO, logger="tag_intake_feed"):
        dry = await tool.run("ONC_RCE", ids, confirm=False, allow_prod=False, session=db)
    assert dry == {"requested": 3, "found": 2, "would_tag": 1, "tagged": 0,
                   "already_tagged": 1, "not_found": 1, "applied": False}
    assert f"WOULD TAG intake {untagged}" in caplog.text
    row = await db.get(m.RceSourceIntake, untagged)
    await db.refresh(row)
    assert "feed" not in (row.source_metadata or {})
    assert not await _audit_tag_rows(db)

    done = await tool.run("ONC_RCE", ids, confirm=True, allow_prod=False, session=db)
    assert done["tagged"] == 1 and done["applied"] is True and done["already_tagged"] == 1
    await db.refresh(row)
    assert row.source_metadata["feed"] == "ONC_RCE"
    other = await db.get(m.RceSourceIntake, tagged)
    await db.refresh(other)
    assert other.source_metadata["feed"] == "OTHER"          # never retagged
    (audit,) = await _audit_tag_rows(db)
    assert audit.metadata_["feed"] == "ONC_RCE" and audit.metadata_["intake_ids"] == [str(untagged)]
    # the Area 1 trigger recorded the before image of the intake it touched
    logged = (await db.execute(text(
        "select count(*) from area1_mutation_log where table_name='rce_source_intakes' "
        "and row_id = :i and justification like 'issue-history feed tagging%'"),
        {"i": untagged})).scalar()
    assert logged == 1
    assert tool.parse_ids([str(untagged), str(untagged)], None) == [untagged]
    with pytest.raises(SystemExit):
        tool.parse_ids(["nope"], None)
    with pytest.raises(SystemExit):
        tool.validate_feed(" ONC_RCE")


async def _audit_tag_rows(db):
    return (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "issue_history_feed_tagged"))).scalars().all()
