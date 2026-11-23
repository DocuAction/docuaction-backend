"""A ZIP/state mismatch (FMT-003) takes no corrected value (2026-11-19).

Owner decision: the finding alone cannot say which of the two fields is wrong,
so it is not correctable through the API or the UI; corrections stay available
through the finding for the field itself.

Observed on DEV, build af288dd, synthetic finding DQ-20260930-6a2df06e-000016,
request e29dd2bc-0585-428a-a521-fd45111442a1: the finding stores the PAIR
"75003 / CO" against field `address_postalCode`, so the staleness guard could
never match it to the postal-code column; every "Correct with value" was
refused as stale and moved the finding to UNDER_REVIEW.

The finding's correction authority is NOT changed (it stays HUMAN_REQUIRED,
so it still opens a review case and no rule or rule-set version moves). What
is removed is "Correct with value": `quality_rules.NON_CORRECTABLE_RULES`,
enforced by `curation`. Existing rows and new rows are therefore treated the
same. Each case runs with deferred processing on and off.
"""

# ruff: noqa: F811 - the imported `flag` fixture is used as a test argument
from __future__ import annotations

import uuid

import pytest
from support_delivery_api import headers_for, run
from test_refused_correction_leaves_no_decision_2026_11_19 import (  # noqa: F401 - fixtures
    BASE,
    REASON,
    _module_cleanup,
    _set_issue,
    assert_nothing_recorded,
    correct,
    flag,
    fresh_delivery,
    ledger_item,
    state,
)

pytestmark = pytest.mark.usefixtures("db_required")

ZIP, STATE = "75003", "CO"


async def _set_address(issue_id, *, postal_code, state_code):
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        issue = await db.get(m.RceIssue, uuid.UUID(issue_id))
        curated = (
            await db.execute(
                select(m.RceCuratedRecord).where(
                    m.RceCuratedRecord.source_record_id == issue.source_record_id
                )
            )
        ).scalar_one()
        curated.address_postal_code = postal_code
        curated.address_state = state_code
        await db.commit()


async def _address(issue_id):
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        issue = await db.get(m.RceIssue, uuid.UUID(issue_id))
        curated = (
            await db.execute(
                select(m.RceCuratedRecord).where(
                    m.RceCuratedRecord.source_record_id == issue.source_record_id
                )
            )
        ).scalar_one()
        return curated.address_postal_code, curated.address_state


def zip_state_finding():
    """A delivery whose first finding is a ZIP/state mismatch as the rule
    stores it: field `address_postalCode`, value "<zip> / <state>"."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    run(_set_address(issue_id, postal_code=ZIP, state_code=STATE))
    run(
        _set_issue(
            issue_id,
            rule_id="FMT-003",
            issue_type="ZIP_STATE_MISMATCH",
            severity="MEDIUM",
            field_name="address_postalCode",
            original_value=f"{ZIP} / {STATE}",
            correction_authority="HUMAN_REQUIRED",
        )
    )
    return d, issue_id


def test_the_rule_still_asks_for_human_review_and_is_listed_as_not_correctable():
    """Authority and version are unchanged: the finding still opens a review
    case. Only the correction is removed."""
    from app.tefca_registry.rce import quality_rules as qr

    assert "FMT-003" in qr.NON_CORRECTABLE_RULES
    assert qr.NON_CORRECTABLE_RULES.isdisjoint(qr.AUTO_SAFE_RULES)
    # The emitted authority (HUMAN_REQUIRED) is pinned by
    # tests/test_rce_pipeline.py::test_zip_state_mismatch_is_reported_not_corrected.
    assert qr.RULE_BY_ID["FMT-003"].version == "1.0.0"


def test_a_correction_is_refused_and_records_nothing(client, flag):
    """Refused before anything is written. It is no longer moved to
    UNDER_REVIEW by the staleness guard, which is never reached."""
    d, issue_id = zip_state_finding()
    before = state(issue_id)

    r = correct(client, issue_id, "80202")

    assert r.status_code == 409, r.text
    assert "does not establish which of the two values is wrong" in r.text
    assert_nothing_recorded(client, d, issue_id, before)
    assert run(_address(issue_id)) == (ZIP, STATE)


def test_the_ledger_tells_the_ui_not_to_offer_a_correction(client, flag):
    d, issue_id = zip_state_finding()

    item = ledger_item(client, d, issue_id)

    assert item["can_correct"] is False
    assert "does not establish which of the two values is wrong" in item["cannot_correct_reason"]
    assert item["can_dispose"] is True  # it can still be accepted or rejected
    other = ledger_item(client, d, d["issue_ids"][2])  # an ordinary NPI finding
    assert other["can_correct"] is True and other["cannot_correct_reason"] is None


def test_the_finding_can_still_be_accepted_without_changing_the_address(client, flag):
    d, issue_id = zip_state_finding()

    r = client.post(
        f"{BASE}/issues/{issue_id}/dispositions",
        json={"decision": "ACCEPT", "reason": REASON},
        headers=headers_for("reviewer"),
    )

    assert r.status_code in (200, 202), r.text
    after = state(issue_id)
    assert after["resolution"] == "RESOLVED" and after["corrections"] == 0
    assert run(_address(issue_id)) == (ZIP, STATE)
    decisions = ledger_item(client, d, issue_id)["issue_decisions"]
    assert [x["decision"] for x in decisions] == ["ACCEPT"]


def test_the_postal_code_is_still_correctable_through_its_own_finding(client, flag):
    """A finding about the field itself stores that field's value, so the
    staleness guard matches and the correction is applied."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    run(_set_address(issue_id, postal_code="7500", state_code="TX"))
    run(
        _set_issue(
            issue_id,
            rule_id="FMT-001",
            issue_type="ZIP_FORMAT",
            severity="MEDIUM",
            field_name="address_postalCode",
            original_value="7500",
            correction_authority="HUMAN_REQUIRED",
        )
    )

    r = correct(client, issue_id, "75003")

    assert r.status_code in (200, 202), r.text
    assert run(_address(issue_id)) == ("75003", "TX")
    after = state(issue_id)
    assert after["resolution"] == "RESOLVED" and after["corrections"] == 1


def test_a_field_with_no_curated_column_is_not_offered_a_correction_either(client, flag):
    """The DEV case behind the refused-correction fix (PUR-001 on
    `purposesofuse`): the ledger now says so before anyone tries."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    run(_set_issue(issue_id, rule_id="PUR-001", field_name="purposesofuse"))

    item = ledger_item(client, d, issue_id)

    assert item["can_correct"] is False
    assert "does not map to a curated column" in item["cannot_correct_reason"]
    assert item["correction_authority"] == "HUMAN_REQUIRED"  # as stored
