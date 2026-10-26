"""Hardening after the RULE-RBAC-PROBE finding (DEV, active 2026-08-02 → retired 2026-10-25).

A review_rules row whose conditions assert nothing (`{}`, or `none_of` alone) matched every
input, so the documented "no rule matched → B3" default was silently replaced by B1 for 220
records. Three independent guards now hold:
  1. `_evaluate` never matches a rule without a positive clause.
  2. `load_rules` refuses such rows (logged, exposed on `rejected_rules`) even if still active.
  3. `POST/PUT /review-rules` reject them at validation time (422).
Pure unit tests: stub session, no database.
"""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.tefca_registry.bucket_classifier import (
    SEED_RULES_V3, BucketClassifier, has_positive_clause)
from app.tefca_registry.review_routes import RuleCreate, RuleUpdate

PROBE = dict(rule_code="RULE-RBAC-PROBE", version=1, name="probe", description="probe", bucket="B1",
             priority=97, conditions={}, is_active=True, effective_date=date(2026, 8, 2), retired_date=None)
NONE_OF_ONLY = dict(PROBE, rule_code="RULE-NEG-ONLY",
                    conditions={"none_of": [{"source": "oig_leie", "status": "excluded"}]})

# The stored classifier input on every probe-classified DEV record.
UNMATCHED = {"sources": {"nppes": "unavailable", "pecos": "unavailable", "sam_gov": "unavailable",
                         "oig_leie": "clear"}, "fields": {}, "confidence_score": None}
ALL_VERIFIED = {"sources": {"nppes": "verified", "oig_leie": "clear", "pecos": "verified"},
                "fields": {}, "confidence_score": None}


def _row(spec, **over):
    base = dict(spec)
    base.setdefault("is_active", True)
    base.setdefault("effective_date", date(2026, 10, 7))
    base.setdefault("retired_date", None)
    base.update(over)
    return SimpleNamespace(**base)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _StubSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _Result(self._rows)


def _loaded_classifier(extra_rows):
    rows = [_row(r, version=r.get("version", 3)) for r in SEED_RULES_V3] + extra_rows
    clf = BucketClassifier()
    asyncio.run(clf.load_rules(_StubSession(rows)))
    return clf


# ── guard 1: evaluation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("conditions", [{}, {"none_of": [{"source": "oig_leie", "status": "excluded"}]},
                                        {"all_of": [], "any_of": []}, None, "not-a-dict"])
def test_conditions_without_a_positive_clause_are_not_positive(conditions):
    assert has_positive_clause(conditions) is False


@pytest.mark.parametrize("conditions", [{"all_of": [{"source": "nppes", "status": "verified"}]},
                                        {"any_of": [{"source": "sam_gov", "status": "debarred"}]},
                                        {"any_unavailable": ["pecos"]}])
def test_conditions_with_a_positive_clause_are_positive(conditions):
    assert has_positive_clause(conditions) is True


def test_evaluate_never_matches_an_empty_conditions_rule_even_when_passed_directly():
    clf = BucketClassifier()
    rules = [dict(r, version=3) for r in SEED_RULES_V3] + [dict(PROBE)]
    r = clf.classify(UNMATCHED, rules=rules)
    assert (r.bucket, r.rule_code) == ("B3", None)
    assert "RULE-RBAC-PROBEv1" in r.evaluated_rules, "the rule is evaluated, it just cannot match"


# ── guard 2: loading ─────────────────────────────────────────────────────────

def test_loader_refuses_an_active_empty_conditions_row_and_falls_back_to_b3():
    clf = _loaded_classifier([_row(PROBE)])
    assert [r["rule_code"] for r in clf.rejected_rules] == ["RULE-RBAC-PROBE"]
    assert all(r["rule_code"] != "RULE-RBAC-PROBE" for r in clf._rules)
    r = clf.classify(UNMATCHED)
    assert (r.bucket, r.rule_code, r.rule_version) == ("B3", None, None)
    assert "RULE-RBAC-PROBEv1" not in r.evaluated_rules


def test_loader_refuses_a_none_of_only_row():
    clf = _loaded_classifier([_row(NONE_OF_ONLY)])
    assert [r["rule_code"] for r in clf.rejected_rules] == ["RULE-NEG-ONLY"]
    assert clf.classify(UNMATCHED).bucket == "B3"


def test_genuine_rules_are_unaffected_by_the_guard():
    with_probe = _loaded_classifier([_row(PROBE)]).classify(ALL_VERIFIED)
    without = _loaded_classifier([]).classify(ALL_VERIFIED)
    assert (with_probe.bucket, with_probe.rule_code) == (without.bucket, without.rule_code) == ("B1", "RULE-001")
    assert _loaded_classifier([]).rejected_rules == []


# ── guard 3: the admin API schema ────────────────────────────────────────────

def test_rule_create_rejects_empty_conditions():
    with pytest.raises(ValidationError, match="asserts nothing"):
        RuleCreate(rule_code="RULE-X", name="x", bucket="B1", priority=97, conditions={})


def test_rule_create_rejects_none_of_only_conditions():
    with pytest.raises(ValidationError):
        RuleCreate(rule_code="RULE-X", name="x", bucket="B1", priority=97,
                   conditions={"none_of": [{"source": "oig_leie", "status": "excluded"}]})


def test_rule_create_accepts_a_rule_that_asserts_something():
    RuleCreate(rule_code="RULE-X", name="x", bucket="B1", priority=97,
               conditions={"all_of": [{"source": "nppes", "status": "verified"}]})


def test_rule_update_rejects_empty_conditions_but_allows_omitting_them():
    with pytest.raises(ValidationError):
        RuleUpdate(conditions={})
    assert RuleUpdate(name="renamed").conditions is None
