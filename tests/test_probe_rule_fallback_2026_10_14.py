"""Regression for the stray active DEV rule RULE-RBAC-PROBE v1 (2026-10-14 finding).

The row on DEV is: bucket B1, priority 97, conditions {} (empty), active, never retired. The classifier treats a
rule with no conditions as matching EVERY input, and evaluates it after RULE-005/001/002/003/004, so it converts
every case no genuine rule matches from the documented default B3 into B1.

This file does not change classifier code. It pins (1) the hazard, so the reason for retiring the row stays
documented, and (2) the post-retirement contract: once the row carries a retired_date, the loader excludes it and an
unmatched case falls back to the documented B3 default, while cases a genuine rule matches are unaffected.
Pure unit test: the rule loader runs against a stub session, no database.
"""
import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

from app.tefca_registry.bucket_classifier import SEED_RULES_V3, BucketClassifier

PROBE = dict(rule_code="RULE-RBAC-PROBE", version=1, name="probe", description="probe", bucket="B1",
             priority=97, conditions={}, is_active=True, effective_date=date(2026, 8, 2), retired_date=None)

# The exact classifier input stored on all 126 DEV review records classified by the probe rule (read 2026-10-14):
# NPPES, PECOS and SAM unavailable, LEIE clear, no field signals. No genuine rule matches it (RULE-001/002 need
# NPPES verified; RULE-003 needs a field mismatch; RULE-004 needs a not_found/conflict signal).
UNMATCHED = {"sources": {"nppes": "unavailable", "pecos": "unavailable", "sam_gov": "unavailable", "oig_leie": "clear"},
             "fields": {}, "confidence_score": None}
ALL_VERIFIED = {"sources": {"nppes": "verified", "oig_leie": "clear", "pecos": "verified"}, "fields": {},
                "confidence_score": None}
NPPES_NOT_FOUND = {"sources": {"nppes": "not_found", "oig_leie": "clear"}, "fields": {}, "confidence_score": None}


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
    """Returns the given rows for the loader's single SELECT (the SQL is_active filter is the database's job;
    the retired_date/effective_date filtering under test is the loader's own Python)."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _Result(self._rows)


def _classify(results, probe_retired):
    rows = [_row(r, version=r.get("version", 3)) for r in SEED_RULES_V3]
    rows.append(_row(PROBE, retired_date=date.today() - timedelta(days=0) if probe_retired else None))
    clf = BucketClassifier()
    return asyncio.run(clf.classify_with_db(_StubSession(rows), results))


def test_active_empty_conditions_probe_turns_the_b3_default_into_b1():
    """Characterisation of the defect (this is what DEV does today)."""
    r = _classify(UNMATCHED, probe_retired=False)
    assert (r.bucket, r.rule_code) == ("B1", "RULE-RBAC-PROBE")


def test_retired_probe_is_not_loaded_and_an_unmatched_case_falls_back_to_b3():
    r = _classify(UNMATCHED, probe_retired=True)
    assert r.bucket == "B3" and r.rule_code is None and r.rule_version is None
    assert "default" in r.rationale.lower()
    assert "RULE-RBAC-PROBEv1" not in r.evaluated_rules


def test_cases_a_genuine_rule_matches_are_identical_with_or_without_the_probe():
    for results, bucket, code in ((ALL_VERIFIED, "B1", "RULE-001"), (NPPES_NOT_FOUND, "B3", "RULE-004")):
        before = _classify(results, probe_retired=False)
        after = _classify(results, probe_retired=True)
        assert (before.bucket, before.rule_code, before.rule_version) == (after.bucket, after.rule_code, after.rule_version)
        assert (after.bucket, after.rule_code) == (bucket, code)
