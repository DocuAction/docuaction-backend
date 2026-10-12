"""Issue history, synthetic cases A-E (2026-10-08): what a user selecting a record
sees across July / August / September deliveries, asserted against the real
engine, database and service (rolled-back transactions, synthetic data only).
"""
from __future__ import annotations

import json

import pytest

from app.tefca_registry.rce import issue_history_core as core

from issue_history_cases_2026_10_08 import (FEEDS, GOOD_NPI_2, OIDS, SEEDERS, seed_case_a,
                                            seed_case_b, seed_case_c, seed_case_d,
                                            seed_case_e)
from issue_history_support_2026_10_07 import (BAD_LEN_NPI, GOOD_NPI,  # noqa: F401
                                              entry_of, history, lane, rolled_back_db)

NPI2 = "NPI-002"
STATEMENT = "Issue observed again; persistence or recurrence cannot be established."


@pytest.fixture(autouse=True)
def results_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENABLE_RECORD_CHECK_RESULTS", True)


async def hist(db, case, oid_key=None, **kw):
    return await history(db, OIDS[oid_key or case], viewer_feeds=FEEDS[case[0]], **kw)


@pytest.mark.asyncio
async def test_case_a_recurring_only_because_a_comparable_pass_intervenes(rolled_back_db):
    ids = await seed_case_a(rolled_back_db)
    resp = await hist(rolled_back_db, "A", reviewer=True)
    assert [e["delivery_id"] for e in resp["deliveries"]] == [
        str(ids["jul"]), str(ids["aug"]), str(ids["sep"])]
    jul, aug, sep = (lane(entry_of(resp, ids[k]), NPI2) for k in ("jul", "aug", "sep"))
    assert jul["check"]["outcome"] == "FAIL" and jul["recurrence"]["state"] == "FIRST_OBSERVED"
    assert aug["check"] == {"outcome": "PASS", "comparability": "COMPARABLE", "reason": None}
    r = sep["recurrence"]
    assert r["state"] == "RECURRING" and r["comparable_pass"] == str(ids["aug"])
    assert r["earlier_occurrence"] == str(ids["jul"])
    assert r["statement"] == core.RECURRING_STATEMENT
    assert STATEMENT not in json.dumps(sep["recurrence"])
    # (3) NPI state per delivery
    states = [entry_of(resp, ids[k])["npi"]["state"] for k in ("jul", "aug", "sep")]
    assert states == ["INVALID", "PRESENT", "INVALID"]
    assert entry_of(resp, ids["aug"])["npi"]["change"] == "CHANGED"


@pytest.mark.asyncio
async def test_case_b_no_august_delivery_is_never_recurring(rolled_back_db):
    ids = await seed_case_b(rolled_back_db)
    resp = await hist(rolled_back_db, "B")
    assert [e["delivery_id"] for e in resp["deliveries"]] == [str(ids["jul"]), str(ids["sep"])]
    sep = lane(entry_of(resp, ids["sep"]), NPI2)
    assert sep["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"
    assert sep["recurrence"]["statement"] == STATEMENT
    assert sep["recurrence"]["comparable_pass"] is None
    assert [g["to_delivery_id"] for g in resp["gaps"] if g["rule_id"] == NPI2] == [str(ids["sep"])]
    assert resp["sequence_gaps"] == []          # no delivery was received in August
    assert sep["recurrence"]["state"] != "RECURRING"


@pytest.mark.asyncio
async def test_case_c_changed_npi_under_the_same_record_id_is_one_history(rolled_back_db):
    ids = await seed_case_c(rolled_back_db)
    rev = await hist(rolled_back_db, "C", reviewer=True)
    assert len(rev["deliveries"]) == 2 and rev["candidate_associations"] == []
    jul, sep = (entry_of(rev, ids[k]) for k in ("jul", "sep"))
    assert jul["npi"]["state"] == "PRESENT" and jul["npi"]["change"] is None
    assert sep["npi"]["state"] == "PRESENT" and sep["npi"]["change"] == "CHANGED"
    assert sep["npi"]["compared_with_delivery_id"] == str(ids["jul"])
    # original values are preserved on both sides for those allowed to see them
    assert jul["npi"]["submitted_value"] == GOOD_NPI
    assert sep["npi"]["submitted_value"] == GOOD_NPI_2 and sep["npi"]["previous_value"] == GOOD_NPI
    assert sep["identity"]["match"] == "EXACT_ONE" and sep["identity"]["limitation"] is None


@pytest.mark.asyncio
async def test_case_d_same_npi_under_different_record_ids_is_never_merged(rolled_back_db):
    ids = await seed_case_d(rolled_back_db)
    rev1 = await hist(rolled_back_db, "D", "D1", reviewer=True)
    rev2 = await hist(rolled_back_db, "D", "D2", reviewer=True)
    for resp, other in ((rev1, OIDS["D2"]), (rev2, OIDS["D1"])):
        assert [e["delivery_id"] for e in resp["deliveries"]] == [str(ids["jul"]), str(ids["sep"])]
        (cand,) = resp["candidate_associations"]
        assert cand["record_id"] == other and cand["status"] == "UNCONFIRMED"
        assert cand["basis"] == "SAME_NPI_DIFFERENT_RECORD_ID"
        assert cand["shared_npi"] == [GOOD_NPI]
        assert sorted(cand["deliveries"]) == sorted([str(ids["jul"]), str(ids["sep"])])
        assert "not merged" in cand["text"]
        # the other record's own history is not folded in
        assert other not in json.dumps(resp["deliveries"])
    # below reviewer level the association does not exist at all: no key, no id
    viewer = await hist(rolled_back_db, "D", "D1")
    assert "candidate_associations" not in viewer
    assert OIDS["D2"] not in json.dumps(viewer) and GOOD_NPI not in json.dumps(viewer)
    # identical top-level shape whether or not associations exist (case C has none)
    await seed_case_c(rolled_back_db)
    plain = await hist(rolled_back_db, "C")
    assert set(plain) == set(viewer)


@pytest.mark.asyncio
async def test_case_e_duplicate_and_missing_record_ids_are_explicit_limitations(rolled_back_db):
    ids = await seed_case_e(rolled_back_db)
    resp = await hist(rolled_back_db, "E", reviewer=True)
    jul, aug, sep = (entry_of(resp, ids[k]) for k in ("jul", "aug", "sep"))
    assert jul["identity"]["match"] == "MULTIPLE" and jul["identity"]["matching_records"] == 2
    assert "no record was chosen" in jul["identity"]["limitation"]
    assert jul["npi"]["state"] == "NOT_AVAILABLE" and jul["npi"]["reason"] == "DUPLICATE_OID_IN_DELIVERY"
    assert jul["npi"]["submitted_value"] is None
    assert lane(jul, NPI2)["check"]["reason"] == "DUPLICATE_OID_IN_DELIVERY"
    assert aug["identity"]["match"] == "EXACT_ONE" and aug["identity"]["records_without_id"] == 0
    assert sep["identity"]["records_without_id"] == 1
    assert "no record ID" in sep["identity"]["records_without_id_note"]
    # the record with no id is a candidate lead only, never linked
    (cand,) = resp["candidate_associations"]
    assert cand["record_id"] is None and cand["status"] == "UNCONFIRMED"
    assert cand["deliveries"] == [str(ids["sep"])] and "cannot be linked" in cand["text"]
    kinds = [g["kind"] for g in resp["sequence_gaps"]]
    assert "DUPLICATE_OID_IN_DELIVERY" in kinds


@pytest.mark.asyncio
async def test_dates_are_separate_and_missing_ones_say_not_recorded(rolled_back_db):
    ids = await seed_case_b(rolled_back_db)
    resp = await hist(rolled_back_db, "B")
    d = entry_of(resp, ids["jul"])["dates"]
    assert d["received_at"]["value"].startswith("2026-07-05")
    assert d["as_of"] == {"value": None, "note": "not recorded"}
    assert d["verified_transmission"] == {"value": None, "note": "not recorded"}
    assert d["operator_received_date"] == {"value": None, "note": "not recorded"}


@pytest.mark.asyncio
async def test_identifiers_and_run_are_reported_for_every_delivery(rolled_back_db):
    ids = await seed_case_a(rolled_back_db)
    resp = await hist(rolled_back_db, "A")
    assert resp["oid"] == OIDS["A"]
    for e in resp["deliveries"]:
        assert e["identity"]["record_id"] == OIDS["A"]
        assert e["delivery_id"] and e["runs"]["selected"]["run_id"]
        assert e["runs"]["selected"]["rule_set_version"]
        assert all(l["rule_version"] for l in e["lanes"] if l["check"]["outcome"] != "NOT_AVAILABLE")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["A", "B", "C", "D", "E"])
async def test_metadata_only_history_carries_states_but_never_a_value(rolled_back_db, case):
    await SEEDERS[case](rolled_back_db)
    key = "D1" if case == "D" else case
    resp = await hist(rolled_back_db, case, key)
    blob = json.dumps(resp)
    for secret in (BAD_LEN_NPI, GOOD_NPI, GOOD_NPI_2):
        assert secret not in blob, (case, secret)
    assert core.forbidden_keys_present(resp) == []
    assert all(e["npi"]["state"] for e in resp["deliveries"])
    rev = await hist(rolled_back_db, case, key, reviewer=True)
    assert any(e["npi"].get("submitted_value") for e in rev["deliveries"])
