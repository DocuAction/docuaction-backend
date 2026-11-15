"""CodeRabbit on #233 (reproduced): under an active sam_gov mandatory policy the single-record review (`run_review`)
must see the SAME dated-extract state the bulk path supplies, so a clear extract satisfies M-1 when the live API did not
answer, and a candidate extract is a review reason - on BOTH paths. Synthetic only; the fake-session harness of the
parity suite is reused unchanged."""
from types import SimpleNamespace

import pytest

from test_mandatory_source_parity_2026_11_11 import _FakeDB, _entity, _policy, _wire_run_review  # noqa: F401

pytestmark = pytest.mark.regression
LIVE_NOT_ANSWERED = {"nppes": {"status": "verified"}, "oig_leie": {"status": "clear"},
                     "sam_gov": {"status": "not_checked", "reason": "no answer"}}


def _extract_row(disposition):
    return SimpleNamespace(source="SAM_EXTRACT_SUPPLEMENTARY", disposition=disposition, generation_timestamp="2026-11-06T00:00:00",
                           query_identifier="npi=1234567893", id="00000000-0000-0000-0000-000000000001", review_id=None,
                           rule_applied="SAM_PUBLIC_EXTRACT_SUPPLEMENTARY_SCREEN", normalized_values=None)


def _with_extract(monkeypatch, rs, row):
    async def _latest(_db, _eid, **_kw):
        return row
    monkeypatch.setattr(rs, "latest_persisted_extract_evidence", _latest, raising=False)


async def test_a_clear_persisted_extract_satisfies_the_requirement_on_the_single_record_path(monkeypatch):
    rs = _wire_run_review(monkeypatch, [_policy()], LIVE_NOT_ANSWERED)
    _with_extract(monkeypatch, rs, _extract_row("PASS"))
    out = await rs.run_review(_FakeDB(), _entity())
    assert out["classification"]["bucket"] == "B1" and out["classification"]["rule_code"] == "RULE-001"


async def test_a_candidate_persisted_extract_is_a_review_reason_on_the_single_record_path(monkeypatch):
    rs = _wire_run_review(monkeypatch, [_policy()], LIVE_NOT_ANSWERED)
    _with_extract(monkeypatch, rs, _extract_row("REVIEW"))
    out = await rs.run_review(_FakeDB(), _entity())
    assert out["classification"]["bucket"] == "B3" and "EXCLUSION_POSSIBLE_MATCH_PENDING" in out["classification"]["rationale"]


@pytest.mark.parametrize("row", [None, "UNAVAILABLE", "NOT_APPLICABLE"])
async def test_without_a_usable_extract_the_requirement_is_still_not_completed(monkeypatch, row):
    rs = _wire_run_review(monkeypatch, [_policy()], LIVE_NOT_ANSWERED)
    _with_extract(monkeypatch, rs, _extract_row(row) if row else None)
    out = await rs.run_review(_FakeDB(), _entity())
    assert out["classification"]["bucket"] == "B3" and "SOURCE_NOT_COMPLETED_SAM_GOV" in out["classification"]["rationale"]


async def test_no_active_policy_means_the_extract_changes_nothing(monkeypatch):
    rs = _wire_run_review(monkeypatch, [], LIVE_NOT_ANSWERED)
    _with_extract(monkeypatch, rs, _extract_row("REVIEW"))
    out = await rs.run_review(_FakeDB(), _entity())
    assert out["classification"]["bucket"] == "B1"
