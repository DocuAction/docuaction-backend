"""Issue history: findings recorded under rules that are NOT lanes are preserved
and listed per delivery, recorded only (no comparability, no recurrence).
Synthetic data; legacy shape and normal shape; viewer redaction.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.tefca_registry.rce import issue_history as svc
from app.tefca_registry.rce import issue_history_core as core
from app.tefca_registry.rce import models as m

from issue_history_legacy_2026_10_08 import LEG, legacy_settings, seed_legacy
from issue_history_support_2026_10_07 import (BAD_LEN_NPI, deliver, entry_of, history,
                                              rolled_back_db, record_of)  # noqa: F401

SECRET = "SYN-ORIGINAL-VALUE-XYZ"


async def add_issue(db, intake_id, oid, rule_id, version, field="name", sev="LOW",
                    itype="SYN_OTHER_FINDING"):
    rec = await record_of(db, intake_id, oid)
    run = (await db.execute(select(m.RceIngestionRun).where(
        m.RceIngestionRun.source_intake_id == intake_id))).scalars().first()
    db.add(m.RceIssue(
        id=uuid.uuid4(), issue_code="SYN-" + uuid.uuid4().hex[:20],
        source_intake_id=intake_id, source_record_id=rec.id, run_id=run.id,
        rule_id=rule_id, rule_version=version, issue_type=itype, severity=sev,
        field_name=field, original_value=SECRET, correction_authority="HUMAN_REQUIRED",
        description=f"synthetic finding for {rule_id}: value {SECRET}"))
    await db.flush()


def by_rule(entry):
    return {i["rule_id"]: i for i in entry["other_findings"]["items"]}


@pytest.fixture(autouse=True)
def results_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENABLE_RECORD_CHECK_RESULTS", True)


@pytest.mark.asyncio
async def test_normal_shape_other_findings_july_only_september_only_and_both(rolled_back_db):
    db = rolled_back_db
    jul = await deliver(db, 7)
    sep = await deliver(db, 9)
    from issue_history_support_2026_10_07 import OID
    await add_issue(db, jul, OID, "CON-005", "1.0.0")
    await add_issue(db, jul, OID, "FMT-001", "1.0.0", field="npi")
    await add_issue(db, sep, OID, "CON-005", "1.3.0")
    await add_issue(db, sep, OID, "ACT-001", "1.3.0", field="active")
    rev = await history(db, reviewer=True)
    j, s = entry_of(rev, jul)["other_findings"], entry_of(rev, sep)["other_findings"]
    assert j["count"] == 2 and s["count"] == 2
    assert set(by_rule(entry_of(rev, jul))) == {"CON-005", "FMT-001"}          # July only: FMT-001
    assert set(by_rule(entry_of(rev, sep))) == {"CON-005", "ACT-001"}          # Sept only: ACT-001
    item = by_rule(entry_of(rev, sep))["ACT-001"]
    assert item["rule_version"] == "1.3.0" and item["field"] == "active"
    assert item["severity"] == "LOW" and item["finding_type"] == "SYN_OTHER_FINDING"
    assert item["original_value"] == SECRET and SECRET in item["description"]
    for e in (j, s):
        assert "no comparability or recurrence is assessed" in e["note"]
        assert all(i["recorded_only"] and i["recurrence"] is None and i["comparability"] is None
                   for i in e["items"])
    # nothing leaked into the lanes or the gaps
    assert not any(l["rule_id"] in ("CON-005", "FMT-001", "ACT-001")
                   for e in rev["deliveries"] for l in e["lanes"])


@pytest.mark.asyncio
async def test_redacted_viewer_gets_rule_field_severity_count_but_no_values(rolled_back_db):
    db = rolled_back_db
    from issue_history_support_2026_10_07 import OID
    jul = await deliver(db, 7)
    await add_issue(db, jul, OID, "CON-005", "1.0.0")
    view = await history(db)
    o = entry_of(view, jul)["other_findings"]
    assert o["count"] == 1
    (item,) = o["items"]
    assert (item["rule_id"], item["field"], item["severity"]) == ("CON-005", "name", "LOW")
    assert "original_value" not in item and "description" not in item
    assert SECRET not in json.dumps(view)
    assert core.forbidden_keys_present(view) == []


@pytest.mark.asyncio
async def test_a_delivery_without_other_findings_says_zero(rolled_back_db):
    db = rolled_back_db
    jul = await deliver(db, 7)
    o = entry_of(await history(db), jul)["other_findings"]
    assert o["count"] == 0 and o["items"] == []


@pytest.mark.asyncio
async def test_legacy_shape_keeps_non_lane_findings_under_the_old_rule_set(rolled_back_db):
    db = rolled_back_db
    ids = await seed_legacy(db)
    await add_issue(db, ids["jul"], LEG["BOTH"], "CON-005", "1.0.0")
    await add_issue(db, ids["jul"], LEG["BOTH"], "FMT-001", "1.0.0", field="npi")
    await add_issue(db, ids["sep"], LEG["BOTH"], "ACT-001", "1.3.0", field="active")
    for reviewer in (True, False):
        resp = await svc.get_issue_history(db, LEG["BOTH"], reviewer_or_above=reviewer,
                                           settings=legacy_settings(ids))
        jul, sep = entry_of(resp, ids["jul"]), entry_of(resp, ids["sep"])
        assert set(by_rule(jul)) == {"CON-005", "FMT-001"} and set(by_rule(sep)) == {"ACT-001"}
        assert all(i["rule_version"] == "1.0.0" for i in jul["other_findings"]["items"])
        assert jul["other_findings"]["count"] == 2
        assert (SECRET in json.dumps(resp)) == reviewer
    # the lanes of the legacy delivery are unchanged: no pass, not persisted
    assert all(l["check"]["outcome"] != "PASS" for l in jul["lanes"])
