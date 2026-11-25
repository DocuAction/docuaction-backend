"""A refused correction leaves no decision behind (2026-11-19).

Observed on DEV, build af288dd, synthetic finding DQ-20260930-6a2df06e-000021
(PUR-001, field `purposesofuse`), request 1c5aa7a3-2a14-41f0-b77c-0de5db359e22:
"Correct with value" answered 409 ("does not map to a curated column") and
left the finding APPROVED with `resolution_notes` "[CORRECT] ...", which the
exception ledger then showed as a CORRECT decision by the reviewer.

`curation.apply_disposition` walked the finding to APPROVED (each step
commits) and only then called `apply_correction`, which refused. Every refusal
that can be known from the request and the stored rows alone is now made
BEFORE the first write.

The stale-value refusal is NOT one of those and is unchanged: it is decided by
`apply_correction` against the row as it is when the correction is applied, and
it deliberately returns the finding to UNDER_REVIEW.

Both flag states are covered: the walk is the same code with the flag off.
"""

from __future__ import annotations

import uuid

import pytest
import support_delivery_api as support
from support_delivery_api import cleanup, headers_for, run

pytestmark = pytest.mark.usefixtures("db_required")

BASE = "/api/tefca/rce"
FLAG = "ENABLE_DEFERRED_DISPOSITION_PROCESSING"
REASON = "synthetic refused-correction check"


@pytest.fixture(scope="module", autouse=True)
def _module_cleanup():
    yield

    async def _drop_followups():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            await db.execute(
                text("DELETE FROM rce_disposition_followups WHERE intake_id::text = ANY(:i)"),
                {"i": list(support._SEEDED["intakes"]) or [None]},
            )
            await db.commit()

    run(_drop_followups())
    cleanup()


@pytest.fixture(params=["flag_on", "flag_off"])
def flag(request, monkeypatch):
    """Each test runs once with deferred processing on and once with it off."""
    from app.tefca_registry.rce import disposition_followups as followups

    if request.param == "flag_on":
        monkeypatch.setenv(FLAG, "true")
    else:
        monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr(followups, "_stage_hook", None)
    run(followups.drain_all())
    yield request.param
    run(followups.drain_all())


def fresh_delivery():
    support.TAG = uuid.uuid4().hex[:8]
    return support.seed_delivery(state="SUCCEEDED", issues=3)


async def _set_issue(issue_id, **values):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        issue = await db.get(m.RceIssue, uuid.UUID(issue_id))
        for key, value in values.items():
            setattr(issue, key, value)
        await db.commit()


async def _state(issue_id):
    """What a refusal must not have changed, read on a fresh session."""
    from sqlalchemy import func, select

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import disposition_followup_models as fum
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
        corrections = (
            await db.execute(
                select(func.count())
                .select_from(m.RceCorrectionDetail)
                .where(m.RceCorrectionDetail.issue_id == issue.id)
            )
        ).scalar()
        followup_states = (
            (
                await db.execute(
                    select(fum.RceDispositionFollowup.state).where(
                        fum.RceDispositionFollowup.issue_id == issue.id
                    )
                )
            )
            .scalars()
            .all()
        )
        return {
            "resolution": issue.resolution or "OPEN",
            "resolved_by": issue.resolved_by,
            "resolved_at": issue.resolved_at,
            "notes": issue.resolution_notes,
            "record_status": curated.record_status,
            "npi": curated.npi,
            "corrections": int(corrections or 0),
            "followups": list(followup_states),
        }


def state(issue_id):
    return run(_state(issue_id))


def ledger_item(client, delivery, issue_id):
    listing = client.get(
        f"{BASE}/deliveries/{delivery['intake_id']}/exceptions", headers=headers_for("reviewer")
    )
    assert listing.status_code == 200, listing.text
    return next(x for x in listing.json()["items"] if x["issue_id"] == issue_id)


def correct(client, issue_id, value):
    return client.post(
        f"{BASE}/issues/{issue_id}/dispositions",
        json={"decision": "CORRECT", "reason": REASON, "corrected_value": value},
        headers=headers_for("reviewer"),
    )


def assert_nothing_recorded(client, delivery, issue_id, before):
    after = state(issue_id)
    assert after == before, after
    assert after["resolution"] == "OPEN" and after["resolved_by"] is None
    assert after["notes"] is None and after["corrections"] == 0
    assert after["followups"] == []
    item = ledger_item(client, delivery, issue_id)
    assert item["status"] in (None, "OPEN")
    assert item["issue_decisions"] == [] and item["latest_decision"] is None


def test_a_correction_to_a_field_with_no_curated_column_records_nothing(client, flag):
    """The DEV case: PUR-001 on `purposesofuse`."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    run(
        _set_issue(
            issue_id,
            rule_id="PUR-001",
            issue_type="UNKNOWN_PURPOSE_TOKEN",
            field_name="purposesofuse",
            original_value="TREATMENT",
        )
    )
    before = state(issue_id)

    r = correct(client, issue_id, "T-TRTMNT")

    assert r.status_code == 409, r.text
    assert "does not map to a curated column" in r.text
    assert_nothing_recorded(client, d, issue_id, before)


def test_a_correction_with_an_invalid_npi_records_nothing(client, flag):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    before = state(issue_id)

    r = correct(client, issue_id, "123")

    assert r.status_code == 409, r.text
    assert "not a valid NPI" in r.text
    assert_nothing_recorded(client, d, issue_id, before)


def test_a_refused_correction_can_be_decided_again(client, flag):
    """Nothing was recorded, so the finding is exactly as open as before."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    assert correct(client, issue_id, "123").status_code == 409

    r = client.post(
        f"{BASE}/issues/{issue_id}/dispositions",
        json={"decision": "DEFER", "reason": REASON},
        headers=headers_for("reviewer"),
    )

    assert r.status_code in (200, 202), r.text
    decisions = ledger_item(client, d, issue_id)["issue_decisions"]
    assert [x["decision"] for x in decisions] == ["DEFER"]


def test_the_stale_value_refusal_is_unchanged(client, flag):
    """The value the finding was raised against is no longer the stored value:
    still refused by `apply_correction`, still returned to UNDER_REVIEW, and
    still nothing applied. This protection is not moved or weakened."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    run(_set_issue(issue_id, original_value="0000000000"))
    before = state(issue_id)

    r = correct(client, issue_id, support_valid_npi())

    assert r.status_code == 409, r.text
    assert "Approval invalidated before application" in r.text
    after = state(issue_id)
    assert after["resolution"] == "UNDER_REVIEW"
    assert after["notes"].startswith("Approval invalidated before application")
    assert after["corrections"] == 0
    assert after["npi"] == before["npi"] and after["record_status"] == before["record_status"]
    assert after["followups"] == (["REFUSED"] if flag == "flag_on" else [])
    item = ledger_item(client, d, issue_id)
    assert item["status"] == "UNDER_REVIEW"
    assert [x["decision"] for x in item["issue_decisions"]] == []


def support_valid_npi() -> str:
    """A Luhn-valid NPI unique to this call."""
    body = f"1{uuid.uuid4().int % 10**8:08d}"
    digits = [int(c) for c in "80840" + body]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return body + str((10 - total % 10) % 10)


@pytest.mark.parametrize("authority", ["QA_REQUIRED", "NO_CORRECTION"])
def test_a_correction_the_approval_rules_refuse_records_nothing(client, flag, authority):
    """The walk used to commit PROPOSED with "[CORRECT] reason" before the
    APPROVED step refused."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    run(_set_issue(issue_id, correction_authority=authority))
    before = state(issue_id)

    r = correct(client, issue_id, support_valid_npi())

    assert r.status_code == 409, r.text
    assert_nothing_recorded(client, d, issue_id, before)


def test_accepting_a_suggestion_that_cannot_be_applied_records_nothing(client, flag):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    run(_set_issue(issue_id, field_name="purposesofuse", suggested_value="T-TRTMNT"))
    before = state(issue_id)

    r = client.post(
        f"{BASE}/issues/{issue_id}/dispositions",
        json={"decision": "ACCEPT", "reason": REASON},
        headers=headers_for("reviewer"),
    )

    assert r.status_code == 409, r.text
    assert_nothing_recorded(client, d, issue_id, before)


def test_an_invalid_value_is_refused_before_the_stale_value_is_looked_at(client, flag):
    """Order, stated: a value that could never be applied is refused first and
    the finding stays OPEN. Before 2026-11-19 this ended UNDER_REVIEW."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    run(_set_issue(issue_id, original_value="0000000000"))
    before = state(issue_id)

    r = correct(client, issue_id, "123")

    assert r.status_code == 409 and "not a valid NPI" in r.text, r.text
    assert_nothing_recorded(client, d, issue_id, before)


async def _conflict_state(conflict):
    """The registry side of an identifier conflict, read fresh."""
    from sqlalchemy import func, select

    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import traceability_models as tm

    async with async_session_maker() as db:
        events = (
            await db.execute(
                select(func.count())
                .select_from(tm.TefcaIdentifierDecisionEvent)
                .where(tm.TefcaIdentifierDecisionEvent.issue_id == uuid.UUID(conflict["issue_id"]))
            )
        ).scalar()
        identifiers = (
            await db.execute(
                select(
                    reg.TefcaEntityIdentifier.identifier_type,
                    reg.TefcaEntityIdentifier.identifier_value,
                    reg.TefcaEntityIdentifier.identifier_status,
                ).where(reg.TefcaEntityIdentifier.entity_id == uuid.UUID(conflict["entity_id"]))
            )
        ).all()
        return {"events": int(events or 0), "identifiers": sorted(tuple(x) for x in identifiers)}


def test_a_refused_correction_of_a_conflict_changes_no_identifier(client, flag):
    """On an identifier conflict the registry decision is made BEFORE the walk.
    A correction the approval rules refuse used to change the registered
    identifier and then answer 409. It must not reach the registry."""
    from test_deferred_disposition_processing_2026_11_18 import _make_conflict

    d = fresh_delivery()
    conflict = run(_make_conflict(d))
    issue_id = conflict["issue_id"]
    run(_set_issue(issue_id, correction_authority="QA_REQUIRED"))
    before, registry_before = state(issue_id), run(_conflict_state(conflict))

    r = correct(client, issue_id, support_valid_npi())

    assert r.status_code == 409 and "QA_REQUIRED" in r.text, r.text
    assert run(_conflict_state(conflict)) == registry_before
    assert_nothing_recorded(client, d, issue_id, before)


def test_a_stale_correction_of_a_conflict_changes_no_identifier(client, flag):
    """The stale-value guard on an identifier conflict: the finding still ends
    UNDER_REVIEW, and the registry decision that used to be committed before
    the guard spoke is not made."""
    from test_deferred_disposition_processing_2026_11_18 import _make_conflict

    d = fresh_delivery()
    conflict = run(_make_conflict(d))
    issue_id = conflict["issue_id"]
    run(_set_issue(issue_id, original_value="0000000000"))
    before, registry_before = state(issue_id), run(_conflict_state(conflict))

    r = correct(client, issue_id, support_valid_npi())

    assert r.status_code == 409, r.text
    assert "Approval invalidated before application" in r.text
    assert run(_conflict_state(conflict)) == registry_before
    after = state(issue_id)
    assert after["resolution"] == "UNDER_REVIEW"
    assert after["notes"].startswith("Approval invalidated before application")
    assert after["corrections"] == 0
    assert after["npi"] == before["npi"] and after["record_status"] == before["record_status"]
    assert after["followups"] == (["REFUSED"] if flag == "flag_on" else [])
    item = ledger_item(client, d, issue_id)
    assert item["status"] == "UNDER_REVIEW"
    assert [x["decision"] for x in item["issue_decisions"]] == []


def test_a_current_correction_of_a_conflict_still_registers_the_value(client, flag):
    """The other side of the guard: nothing stale, so the correction goes
    through and the registry takes the corrected identifier."""
    from test_deferred_disposition_processing_2026_11_18 import _make_conflict

    d = fresh_delivery()
    conflict = run(_make_conflict(d))
    value = support_valid_npi()

    r = correct(client, conflict["issue_id"], value)

    assert r.status_code in (200, 202), r.text
    registry = run(_conflict_state(conflict))
    assert ("npi", value, "active") in registry["identifiers"]
    assert state(conflict["issue_id"])["corrections"] == 1
