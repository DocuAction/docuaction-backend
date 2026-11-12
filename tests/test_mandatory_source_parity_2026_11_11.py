"""Mandatory-source enforcement parity (B1 of the 2026-11-11 code update).

(1) The bulk path and the single-record path apply the SAME
    `rce.source_policy.enforce_on_classification` over the SAME active policy
    rows. No policy row (the shipped state) is a strict no-op; PECOS is never
    made mandatory by this code - only an active row can do that.

No database is required: the single-record path is driven through a fake
session, and the run/intake linkage through a fake `get`.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.tefca_registry.bucket_classifier import ClassificationResult
from app.tefca_registry.rce import source_policy as sp


# ── helpers ──────────────────────────────────────────────────────────────────

def _policy(source="sam_gov", role="mandatory", applicability=None, version=3):
    return {"id": "11111111-1111-1111-1111-111111111111", "version": version,
            "source": source, "role": role, "applicability": applicability or {},
            "policy_reference": "M-1 owner decision (test)",
            "approval_reference": "22222222-2222-2222-2222-222222222222"}


def _cls(bucket="B1", rule_code="RULE-001"):
    return ClassificationResult(
        bucket=bucket, rule_code=rule_code, rule_version=2, rule_name="r",
        rationale="matched", matched_conditions=["c"],
        evidence_summary={"k": "v"}, evaluated_rules=["RULE-001"])


# ── 1. one enforcement function ──────────────────────────────────────────────

def test_no_active_policy_is_a_strict_noop_same_object():
    c = _cls()
    out, matched = sp.enforce_on_classification(
        c, {"sam_gov": {"status": "not_checked"}}, {"has_submitted_npi": True}, [])
    assert out is c and matched is None


def test_mandatory_source_without_answer_sends_b1_to_b3_with_full_provenance():
    pol = _policy()
    out, matched = sp.enforce_on_classification(
        _cls("B1"), {"sam_gov": {"status": "not_checked"}},
        {"has_submitted_npi": True}, [pol])
    assert out.bucket == "B3"
    assert out.rule_code == sp.SOURCE_POLICY_RULE_CODE == "SOURCE-POLICY-M4"
    assert out.rule_version == 3                       # the ACTUAL row version
    assert out.matched_conditions == ["SOURCE_NOT_COMPLETED_SAM_GOV"]
    assert matched is pol
    for needle in (pol["id"], "v3", pol["policy_reference"],
                   pol["approval_reference"], "SOURCE_NOT_COMPLETED_SAM_GOV"):
        assert needle in out.rationale
    assert out.evaluated_rules[-1] == "source_policy(M-4)"
    assert out.evidence_summary == {"k": "v"}


def test_rationale_text_is_byte_identical_to_the_pre_extraction_bulk_text():
    """Historical determinations were written with this exact sentence."""
    pol = _policy()
    out, _ = sp.enforce_on_classification(
        _cls("B2"), {}, {"has_submitted_npi": True}, [pol])
    expected = (
        "Mandatory-source policy: SOURCE_NOT_COMPLETED_SAM_GOV. "
        "An applicable mandatory check without an acceptable answer cannot "
        "classify B1/B2 (M-4). Policy row "
        "11111111-1111-1111-1111-111111111111 v3 - "
        "M-1 owner decision (test); approval "
        "22222222-2222-2222-2222-222222222222.")
    assert out.rationale == expected


def test_possible_match_keeps_its_own_specific_reason():
    out, _ = sp.enforce_on_classification(
        _cls("B1"), {"sam_gov": "possible_match"}, {"has_submitted_npi": True},
        [_policy()])
    assert out.bucket == "B3"
    assert out.matched_conditions == ["EXCLUSION_POSSIBLE_MATCH_PENDING"]


@pytest.mark.parametrize("state", ["verified", "clear"])
def test_acceptable_answer_leaves_b1_alone(state):
    c = _cls("B1")
    out, matched = sp.enforce_on_classification(
        c, {"sam_gov": {"status": state}}, {"has_submitted_npi": True}, [_policy()])
    assert out is c and matched is None


@pytest.mark.parametrize("state", ["not_found", "excluded", "deactivated"])
def test_answered_adverse_is_a_finding_not_relabelled_source_not_completed(state):
    c = _cls("B1")
    out, matched = sp.enforce_on_classification(
        c, {"sam_gov": {"status": state}}, {"has_submitted_npi": True}, [_policy()])
    assert out is c and matched is None


@pytest.mark.parametrize("bucket", ["B3", "B4"])
def test_b3_b4_stand(bucket):
    c = _cls(bucket)
    out, matched = sp.enforce_on_classification(
        c, {"sam_gov": {"status": "unavailable"}}, {"has_submitted_npi": True},
        [_policy()])
    assert out is c and matched is None


def test_applicability_requires_npi_is_honoured_and_advisory_never_blocks():
    c = _cls("B1")
    cond = _policy(source="pecos", role="conditional",
                   applicability={"requires_npi": True})
    out, _ = sp.enforce_on_classification(
        c, {"pecos": {"status": "unavailable"}}, {"has_submitted_npi": False}, [cond])
    assert out is c                                     # NOT_APPLICABLE
    adv = _policy(source="pecos", role="advisory")
    out, _ = sp.enforce_on_classification(
        c, {"pecos": {"status": "unavailable"}}, {"has_submitted_npi": True}, [adv])
    assert out is c
    # conditional with NO stated applicability applies nowhere
    out, _ = sp.enforce_on_classification(
        c, {"pecos": {"status": "unavailable"}}, {"has_submitted_npi": True},
        [_policy(source="pecos", role="conditional", applicability={})])
    assert out is c


def test_pecos_is_never_mandatory_without_its_own_active_row():
    """Only the recorded M-1 (sam_gov) row is active here: an unavailable PECOS
    must not block B1."""
    c = _cls("B1")
    out, matched = sp.enforce_on_classification(
        c, {"sam_gov": {"status": "clear"}, "pecos": {"status": "unavailable"}},
        {"has_submitted_npi": True}, [_policy()])
    assert out is c and matched is None


def test_both_paths_call_the_one_function_and_not_the_other_module():
    from app.tefca_registry import review_service
    from app.tefca_registry.rce import arc_pipeline
    for mod in (arc_pipeline.verify_and_classify, review_service.run_review):
        src = inspect.getsource(mod)
        assert "enforce_on_classification" in src
    # The metadata module is untouched by enforcement.
    assert "enforce_on_classification" not in inspect.getsource(
        __import__("app.Tefca.source_policy", fromlist=["x"]))


# ── 2. single-record path, end to end through a fake session ─────────────────

class _Scalar:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalar(self):
        return self._v


class _FakeDB:
    def __init__(self, npi="1234567893", source_row=None):
        self.npi, self.source_row, self.added = npi, source_row, []

    async def execute(self, *_a, **_k):
        return _Scalar(self.npi)

    async def get(self, _model, _id):
        return self.source_row

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None


def _wire_run_review(monkeypatch, policies, sources, base_bucket="B1"):
    from app.tefca_registry import processing_controls as pc
    from app.tefca_registry import review_routes
    from app.tefca_registry import review_service as rs
    from app.tefca_registry.rce import verification_findings as vf

    async def _noop(*_a, **_k):
        return None

    async def _sources(_db, _eid):
        return {k: dict(v) for k, v in sources.items()}

    async def _persisted(_db, _eid):
        return {}

    async def _rules(_session):
        return []

    async def _classify(_session, _results):
        return _cls(base_bucket)

    async def _review_id(_db):
        return "REV-TEST-000001"

    async def _policies(_db):
        return policies

    monkeypatch.setattr(rs, "ensure_seed_rules", _noop)
    monkeypatch.setattr(rs, "ensure_rules_v2", _noop)
    monkeypatch.setattr(rs, "ensure_rules_v3", _noop)
    monkeypatch.setattr(pc, "enter_processing", _noop)
    monkeypatch.setattr(pc, "assert_exclusion_configuration_consistent",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(rs._classifier, "load_rules", _rules)
    monkeypatch.setattr(rs._classifier, "classify_with_db", _classify)
    monkeypatch.setattr(rs, "probe_sources", _sources)
    monkeypatch.setattr(rs, "latest_persisted_exclusion_evidence", _persisted)
    monkeypatch.setattr(rs, "_resolve_entity", _noop)
    monkeypatch.setattr(rs, "_compare_addresses", _noop)
    monkeypatch.setattr(rs.reg_audit, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(vf, "record_from_sources", _noop)
    monkeypatch.setattr(review_routes, "generate_review_id", _review_id)
    monkeypatch.setattr(sp, "load_active_policies", _policies)
    return rs


def _entity(source_record_id=None):
    return SimpleNamespace(id=uuid.uuid4(), source_record_id=source_record_id)


async def test_single_record_path_now_enforces_the_mandatory_source(monkeypatch):
    rs = _wire_run_review(
        monkeypatch, [_policy()],
        {"nppes": {"status": "verified"}, "oig_leie": {"status": "clear"},
         "sam_gov": {"status": "not_checked", "reason": "no answer"}})
    out = await rs.run_review(_FakeDB(), _entity())
    cl = out["classification"]
    assert cl["bucket"] == "B3"
    assert cl["rule_code"] == "SOURCE-POLICY-M4"        # not DEFAULT-UNMATCHED
    assert cl["rule_version"] == 3
    assert "SOURCE_NOT_COMPLETED_SAM_GOV" in cl["rationale"]
    assert "v3" in cl["rationale"] and "M-1 owner decision (test)" in cl["rationale"]


async def test_single_record_path_unchanged_with_no_active_policy(monkeypatch):
    rs = _wire_run_review(
        monkeypatch, [],
        {"nppes": {"status": "verified"}, "oig_leie": {"status": "clear"},
         "sam_gov": {"status": "not_checked"}})
    out = await rs.run_review(_FakeDB(), _entity())
    assert out["classification"]["bucket"] == "B1"
    assert out["classification"]["rule_code"] == "RULE-001"


async def test_single_record_path_acceptable_answer_stays_b1(monkeypatch):
    rs = _wire_run_review(
        monkeypatch, [_policy()],
        {"nppes": {"status": "verified"}, "sam_gov": {"status": "clear"}})
    out = await rs.run_review(_FakeDB(), _entity())
    assert out["classification"]["bucket"] == "B1"


async def test_single_record_path_passes_submitted_npi_fact_to_applicability(monkeypatch):
    rs = _wire_run_review(
        monkeypatch,
        [_policy(source="sam_gov", role="conditional",
                 applicability={"requires_npi": True})],
        {"sam_gov": {"status": "not_checked"}})
    no_npi = await rs.run_review(_FakeDB(npi=None), _entity())
    assert no_npi["classification"]["bucket"] == "B1"   # not applicable, no NPI
    with_npi = await rs.run_review(_FakeDB(npi="1234567893"), _entity())
    assert with_npi["classification"]["bucket"] == "B3"
