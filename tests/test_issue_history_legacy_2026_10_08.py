"""Issue history: legacy intakes with a completed run but NO delivery job, NO
persisted check results and an older rule set (the real-data shape). Synthetic.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.tefca_registry.rce import issue_history as svc
from app.tefca_registry.rce import issue_history_core as core

from issue_history_legacy_2026_10_08 import LEG, LEG_FEED, legacy_settings, seed_legacy
from issue_history_support_2026_10_07 import (BAD_LEN_NPI, entity_row, entry_of, filler_row,
                                              lane, rolled_back_db, seed_delivery)  # noqa: F401

NPI2 = "NPI-002"
STATEMENT = "Issue observed again; persistence or recurrence cannot be established."


async def hist(db, ids, key, reviewer=False, **kw):
    return await svc.get_issue_history(db, LEG[key], reviewer_or_above=reviewer,
                                       settings=legacy_settings(ids), **kw)


@pytest.mark.asyncio
async def test_untagged_intakes_stay_invisible_without_the_mapping(rolled_back_db):
    await seed_legacy(rolled_back_db)
    off = SimpleNamespace(ISSUE_HISTORY_FEEDS_VIEWER=LEG_FEED, ISSUE_HISTORY_FEEDS_REVIEWER="",
                          ISSUE_HISTORY_INTAKE_FEEDS="")
    with pytest.raises(svc.HistoryNotFound):
        await svc.get_issue_history(rolled_back_db, LEG["BOTH"], reviewer_or_above=False,
                                    settings=off)


@pytest.mark.asyncio
async def test_a_feed_tag_is_never_overridden_by_the_mapping(rolled_back_db):
    from sqlalchemy import text

    db = rolled_back_db
    await seed_legacy(db)
    await seed_delivery(db, [entity_row("9.99.777.100.99"), filler_row("tg")],
                        received_at=datetime(2026, 9, 3), feed="OTHER-FEED")
    tagged = (await db.execute(text(
        "select id from rce_source_intakes where source_metadata->>'feed'='OTHER-FEED'"))).scalar()
    conf = SimpleNamespace(ISSUE_HISTORY_FEEDS_VIEWER=LEG_FEED, ISSUE_HISTORY_FEEDS_REVIEWER="",
                           ISSUE_HISTORY_INTAKE_FEEDS=f"{tagged}:{LEG_FEED}")
    with pytest.raises(svc.HistoryNotFound):
        await svc.get_issue_history(db, "9.99.777.100.99", reviewer_or_above=False, settings=conf)


@pytest.mark.asyncio
async def test_legacy_july_and_current_september_one_history_never_recurring(rolled_back_db):
    ids = await seed_legacy(rolled_back_db)
    resp = await hist(rolled_back_db, ids, "BOTH", reviewer=True)
    jul, sep = (entry_of(resp, ids[k]) for k in ("jul", "sep"))
    assert [e["delivery_id"] for e in resp["deliveries"]] == [str(ids["jul"]), str(ids["sep"])]
    assert jul["delivery_job"] == {"recorded": False, "job_ids": [],
                                   "note": "not recorded (legacy intake, no delivery job)"}
    assert jul["intake_id"] == str(ids["jul"]) and jul["job_ids"] == []
    assert jul["dates"]["received_at"]["value"].startswith("2026-08-21")
    assert jul["dates"]["received_at"]["note"] == "system receipt time of the intake"
    for k in ("as_of", "operator_received_date", "verified_transmission"):
        assert jul["dates"][k] == {"value": None, "note": "not recorded"}
    assert jul["runs"]["selected"]["run_id"]
    assert jul["runs"]["selected"]["rule_set_version"] == "1.0.0"
    assert sep["runs"]["selected"]["rule_set_version"] != "1.0.0"
    # no persisted check result: not one pass, every finding says why
    for l in jul["lanes"]:
        assert l["check"]["outcome"] != "PASS", l["rule_id"]
        assert l["check"]["comparability"] == "NOT_COMPARABLE"
        if l["check"]["outcome"] == "FAIL":
            assert l["check"]["reason"] == "CHECK_RESULT_NOT_PERSISTED"
            assert l["rule_version"] == "1.0.0"
    n_jul, n_sep = lane(jul, NPI2), lane(sep, NPI2)
    assert n_jul["check"]["outcome"] == "FAIL" and n_jul["finding"]["finding_type"]
    assert n_jul["recurrence"]["state"] == "FIRST_OBSERVED"
    r = n_sep["recurrence"]
    assert r["state"] == "PERSISTENT_OR_UNVERIFIED" and r["comparable_pass"] is None
    assert r["statement"] == STATEMENT and r["reason"] == "RULE_SET_CHANGED"
    assert "Rule set changed (1.0.0 to" in n_sep["note"] and "not comparable" in n_sep["note"]
    for e in (jul, sep):
        assert all(l["recurrence"] is None or l["recurrence"]["state"] != "RECURRING"
                   for l in e["lanes"])


@pytest.mark.asyncio
async def test_july_only_and_september_only_ids_are_absent_not_clear(rolled_back_db):
    ids = await seed_legacy(rolled_back_db)
    only_jul = await hist(rolled_back_db, ids, "JULONLY")
    sep = entry_of(only_jul, ids["sep"])
    assert sep["record_present"] is False and sep["identity"]["match"] == "NONE"
    assert "not assumed to have been re-keyed or removed" in sep["identity"]["limitation"]
    assert lane(sep, NPI2)["check"]["outcome"] == "NOT_AVAILABLE"
    assert [g["kind"] for g in only_jul["sequence_gaps"]] == ["ENTITY_ABSENT_OR_REKEYED"]
    assert only_jul["earlier_without_record"] is None
    only_sep = await hist(rolled_back_db, ids, "SEPONLY")
    assert [e["delivery_id"] for e in only_sep["deliveries"]] == [str(ids["sep"])]
    ew = only_sep["earlier_without_record"]
    assert ew["count"] == 1 and ew["delivery_ids"] == [str(ids["jul"])]
    assert "not a pass and not a correction" in ew["text"]


@pytest.mark.asyncio
async def test_legacy_history_for_the_metadata_only_audience_is_redacted(rolled_back_db):
    ids = await seed_legacy(rolled_back_db)
    resp = await hist(rolled_back_db, ids, "BOTH")
    assert BAD_LEN_NPI not in json.dumps(resp)
    assert core.forbidden_keys_present(resp) == []
    assert entry_of(resp, ids["jul"])["npi"]["state"] == "INVALID"
    rev = await hist(rolled_back_db, ids, "BOTH", reviewer=True)
    assert entry_of(rev, ids["jul"])["npi"]["submitted_value"] == BAD_LEN_NPI


def test_intake_feed_mapping_parser_drops_malformed_pairs():
    u = "11111111-1111-1111-1111-111111111111"
    assert core.parse_intake_feeds(f" {u.upper()} : F1 ,bad,:x,{u}:,") == {u: "F1"}
    assert core.parse_intake_feeds(None) == {}
