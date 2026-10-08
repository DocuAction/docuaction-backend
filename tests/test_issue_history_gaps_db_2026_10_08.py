"""Issue history, track A6 (2026-10-08): explicit gaps and comparability against
the REAL engine and database (rolled-back transactions, synthetic data only).
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text

from app.tefca_registry.rce import issue_history as svc
from app.tefca_registry.rce import issue_history_core as core

from issue_history_support_2026_10_07 import (  # noqa: F401  (fixture imported)
    BAD_LEN_NPI, FEED, GOOD_NPI, OID, entity_row, entry_of, filler_row, history,
    lane, rolled_back_db, run_engine, seed_delivery, deliver)

NPI2 = "NPI-002"


@pytest.fixture(autouse=True)
def results_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENABLE_RECORD_CHECK_RESULTS", True)


def kinds(resp):
    return [(g["kind"], g["delivery_id"]) for g in resp["sequence_gaps"]]


@pytest.mark.asyncio
async def test_clean_sequence_has_an_empty_gap_list(rolled_back_db):
    db = rolled_back_db
    await deliver(db, 7)
    await deliver(db, 8)
    resp = await history(db)
    assert resp["sequence_gaps"] == []


@pytest.mark.asyncio
async def test_absent_and_rekeyed_entity_is_an_explicit_gap_never_a_pass(rolled_back_db):
    db = rolled_back_db
    jul = await deliver(db, 7)
    # August carries the same organisation (same NPI) under a DIFFERENT key
    aug = await deliver(db, 8, oid="SYN-OID-REKEYED")
    sep = await deliver(db, 9)
    resp = await history(db)
    assert kinds(resp) == [("ENTITY_ABSENT_OR_REKEYED", str(aug))]
    e = entry_of(resp, aug)
    assert e["record_present"] is False
    assert lane(e, NPI2)["check"]["outcome"] == "NOT_AVAILABLE"
    assert lane(entry_of(resp, sep), NPI2)["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"
    # no identity is inferred: the other key never appears in the response
    assert "SYN-OID-REKEYED" not in str(resp)


@pytest.mark.asyncio
async def test_delivery_that_was_never_processed_is_a_gap(rolled_back_db):
    db = rolled_back_db
    await deliver(db, 7)
    aug = await deliver(db, 8, process=False)
    await deliver(db, 9)
    resp = await history(db)
    assert ("NO_COMPLETED_RUN", str(aug)) in kinds(resp)
    assert entry_of(resp, aug)["status"] == "NOT_COMPARABLE"


@pytest.mark.asyncio
async def test_failed_intake_between_deliveries_is_listed_but_is_not_a_delivery(rolled_back_db):
    db = rolled_back_db
    jul = await deliver(db, 7)
    rows = [entity_row(OID), filler_row("failed-mid")]
    await seed_delivery(db, rows, received_at=datetime(2026, 8, 5), status="FAILED")
    sep = await deliver(db, 9)
    resp = await history(db)
    assert [e["delivery_id"] for e in resp["deliveries"]] == [str(jul), str(sep)]
    assert kinds(resp) == [("DELIVERY_NOT_PROCESSED", None)]
    (g,) = resp["sequence_gaps"]
    assert g["received_at"].startswith("2026-08-05")
    assert sep and lane(entry_of(resp, sep), NPI2)["recurrence"]["state"] == \
        "PERSISTENT_OR_UNVERIFIED"


@pytest.mark.asyncio
async def test_failed_intake_of_a_feed_the_caller_may_not_read_is_not_disclosed(rolled_back_db):
    db = rolled_back_db
    await deliver(db, 7)
    await deliver(db, 9)
    await seed_delivery(db, [entity_row(OID), filler_row("hid")],
                        received_at=datetime(2026, 8, 5), feed="SYN-HIDDEN",
                        status="FAILED")
    resp = await history(db)
    assert resp["sequence_gaps"] == []
    assert "SYN-HIDDEN" not in str(resp)


@pytest.mark.asyncio
async def test_failed_intake_before_the_first_delivery_of_the_entity_is_not_a_gap(rolled_back_db):
    db = rolled_back_db
    await seed_delivery(db, [filler_row("early")], received_at=datetime(2026, 6, 5),
                        status="FAILED")
    await deliver(db, 7)
    assert (await history(db))["sequence_gaps"] == []


@pytest.mark.asyncio
async def test_page_window_carries_only_the_gaps_inside_it(rolled_back_db):
    db = rolled_back_db
    await deliver(db, 5, oid=None)             # before the entity: cut, not a gap
    await deliver(db, 6)
    aug = await deliver(db, 7, oid="SYN-OID-OTHER")
    for month in (8, 9, 10, 11):
        await deliver(db, month)
    full = await history(db)
    assert kinds(full) == [("ENTITY_ABSENT_OR_REKEYED", str(aug))]
    newest = await history(db, limit=2)
    assert newest["sequence_gaps"] == [] and newest["paging"]["earlier_available"]
    older = await history(db, limit=2, before=newest["paging"]["next_before"])
    assert older["sequence_gaps"] == []
    oldest = await history(db, limit=3, before=older["paging"]["next_before"])
    assert kinds(oldest) == [("ENTITY_ABSENT_OR_REKEYED", str(aug))]


@pytest.mark.asyncio
async def test_viewer_and_reviewer_both_get_gaps_and_the_viewer_stays_redacted(rolled_back_db):
    db = rolled_back_db
    await deliver(db, 7)
    await deliver(db, 8, oid="SYN-OID-OTHER")
    await deliver(db, 9)
    for reviewer in (False, True):
        resp = await history(db, reviewer=reviewer)
        assert [g["kind"] for g in resp["sequence_gaps"]] == ["ENTITY_ABSENT_OR_REKEYED"]
        if not reviewer:
            assert core.forbidden_keys_present(resp) == []


@pytest.mark.asyncio
async def test_a_changed_rule_scope_is_not_comparable(rolled_back_db):
    db = rolled_back_db
    await deliver(db, 7)
    aug = await deliver(db, 8, npi=GOOD_NPI)
    await deliver(db, 9)
    await db.execute(text(
        "update rce_rule_execution_history set scope = 'POPULATION' "
        "where rule_id = 'NPI-002' and run_id in (select id from rce_ingestion_runs "
        "where source_intake_id = :i)"), {"i": aug})
    resp = await history(db)
    assert lane(entry_of(resp, aug), NPI2)["check"] == {
        "outcome": "PASS", "comparability": "NOT_COMPARABLE",
        "reason": "RULE_SCOPE_CHANGED"}
    sep = resp["deliveries"][-1]
    assert lane(sep, NPI2)["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


@pytest.mark.asyncio
async def test_all_empty_reference_coverage_is_source_unavailable_not_clear(rolled_back_db):
    db = rolled_back_db
    await deliver(db, 7, npi=GOOD_NPI)
    aug = await deliver(db, 8, npi=GOOD_NPI)
    await db.execute(text(
        "update rce_rule_execution_history set coverage = "
        "jsonb_set(jsonb_set(jsonb_set(coverage, '{delivery_ids}', '0'), "
        "'{registry_oids}', '0'), '{qhin_oids}', '0') "
        "where rule_id = 'INT-002' and run_id in (select id from rce_ingestion_runs "
        "where source_intake_id = :i)"), {"i": aug})
    resp = await history(db)
    assert lane(entry_of(resp, aug), "INT-002")["check"]["reason"] == "SOURCE_UNAVAILABLE"
    assert ("SOURCE_UNAVAILABLE", str(aug)) in kinds(resp)
