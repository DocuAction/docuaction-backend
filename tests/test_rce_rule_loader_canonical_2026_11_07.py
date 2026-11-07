"""D-13 / DEF-04: `arc_pipeline._rule_set` must apply the canonical
`BucketClassifier.load_rules` semantics.

Divergence demonstrated at f492383: `_rule_set` selected `is_active` rows
directly, so (a) a retired-dated row, (b) a superseded older version and
(c) a rule row referencing an unregistered signal were all still evaluated
on the RCE/delivery path, while the manual review path (which loads through
`BucketClassifier.load_rules`) filtered or refused them. These tests pin the
unified behavior. All rows are synthetic and rolled back.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.core.vocabulary_contract import UnknownSignalReference
from app.tefca_registry import models as reg
from app.tefca_registry.bucket_classifier import BucketClassifier
from app.tefca_registry.rce.arc_pipeline import _rule_set
from rce_traceability_support import rolled_back_db  # noqa: F401

pytestmark = pytest.mark.regression


def _rule_row(code, *, version=99, bucket="B2", retired=None, effective=None,
              conditions=None):
    return reg.ReviewRule(
        id=uuid.uuid4(), rule_code=code, name=f"SYNTHETIC {code} v{version}",
        bucket=bucket, priority=35,
        conditions=conditions or {"any_of": [{"field": "name_mismatch",
                                               "severity": "minor"}]},
        description="synthetic loader-semantics test row", version=version,
        effective_date=effective, retired_date=retired, is_active=True)


class TestCanonicalLoaderSemantics:
    async def test_baseline_matches_canonical_loader_exactly(self, rolled_back_db):
        """With the table as seeded, _rule_set returns exactly what
        BucketClassifier.load_rules returns - same codes, versions, order."""
        db = rolled_back_db
        via_pipeline = await _rule_set(db)
        via_canonical = await BucketClassifier().load_rules(db)
        key = lambda rs: [(r["rule_code"], r["version"], r["priority"]) for r in rs]
        assert key(via_pipeline) == key(via_canonical)
        assert len(via_pipeline) >= 5  # the seeded v3 set

    async def test_retired_dated_row_is_excluded(self, rolled_back_db):
        """An is_active row whose retired_date has passed was previously still
        evaluated by the RCE path; now it is filtered like the manual path."""
        db = rolled_back_db
        db.add(_rule_row("RULE-SYN-RET", retired=date.today() - timedelta(days=1)))
        await db.flush()
        rules = await _rule_set(db)
        assert all(r["rule_code"] != "RULE-SYN-RET" for r in rules)

    async def test_future_effective_row_is_excluded(self, rolled_back_db):
        db = rolled_back_db
        db.add(_rule_row("RULE-SYN-FUT", effective=date.today() + timedelta(days=30)))
        await db.flush()
        rules = await _rule_set(db)
        assert all(r["rule_code"] != "RULE-SYN-FUT" for r in rules)

    async def test_newest_version_per_code_wins(self, rolled_back_db):
        db = rolled_back_db
        db.add(_rule_row("RULE-SYN-VER", version=1))
        db.add(_rule_row("RULE-SYN-VER", version=2))
        await db.flush()
        rules = await _rule_set(db)
        mine = [r for r in rules if r["rule_code"] == "RULE-SYN-VER"]
        assert [r["version"] for r in mine] == [2]

    async def test_unregistered_signal_reference_refuses_to_load(self, rolled_back_db):
        """CHECK-4 now runs on the RCE path too: a rule row referencing a
        signal nothing produces raises instead of silently never firing."""
        db = rolled_back_db
        db.add(_rule_row("RULE-SYN-UNK", conditions={
            "any_of": [{"field": "synthetic_signal_nothing_produces",
                        "status": True}]}))
        await db.flush()
        with pytest.raises(UnknownSignalReference):
            await _rule_set(db)
