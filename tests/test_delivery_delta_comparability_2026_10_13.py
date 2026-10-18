"""Predecessor identity and comparability of a delivery delta (2026-10-13).

The facts below are the frozen DEV values of the two intakes the September 2 snapshot
screenshot showed: 4417b334 (onc-snapshot-20260902.csv, 24,589 records, ONC/RCE) was
compared against 51bf4011 (stage3_fx2_relationships_baseline.csv, 8 records, declared
'SYNTHETIC-QA-STAGE3 (not ONC/RCE)'), so all 24,589 were NEW and 8 were NOT_PRESENT.
Pure functions: no database.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import uuid

from app.tefca_registry.rce import delivery_delta as dd

FP = "1cd655e9120dc9d0d6a52697ea470519b138fe0f9334af6f69467f3485ade3d0"


def _intake(**over):
    base = dict(id=uuid.uuid4(), original_filename="x.csv", delivery_label="L",
                source_metadata={"declared_source": "ONC/RCE"}, record_count=24589,
                schema_fingerprint=FP, received_at=datetime(2026, 9, 2),
                created_at=datetime(2026, 9, 24, 19, 56, 10), status="PARSED")
    base.update(over)
    return SimpleNamespace(**base)


SEPT = _intake(original_filename="onc-snapshot-20260902.csv")
FX2 = _intake(original_filename="stage3_fx2_relationships_baseline.csv",
              delivery_label="STG3-SYNTHETIC-2026-09-21-FX2", record_count=8,
              received_at=datetime(2026, 9, 1),
              source_metadata={"declared_source": "SYNTHETIC-QA-STAGE3 (not ONC/RCE)",
                               "registration_notes": "SYNTHETIC TEST DATA - Stage 3 QA"})
JULY = _intake(original_filename="onc-snapshot-20260720.csv", delivery_label="ONC-ASTP-2026-08-21",
               record_count=23566, received_at=datetime(2026, 8, 21), source_metadata={})


def test_the_real_september_pair_is_not_comparable_and_says_why():
    verdict = dd.assess_comparability(dd.intake_facts(SEPT), dd.intake_facts(FX2))
    assert verdict["status"] == "NOT_COMPARABLE"
    codes = {r["code"] for r in verdict["reasons"]}
    assert codes == {"PREDECESSOR_SYNTHETIC", "DIFFERENT_DECLARED_SOURCE", "SIZE_ORDER_OF_MAGNITUDE"}
    assert "3074x" in " ".join(r["detail"] for r in verdict["reasons"])  # 24,589 / 8


def test_the_july_legacy_intake_is_a_comparable_candidate():
    verdict = dd.assess_comparability(dd.intake_facts(SEPT), dd.intake_facts(JULY))
    assert verdict["status"] == "COMPARABLE" and verdict["reasons"] == []


def test_no_predecessor_is_a_baseline_not_a_comparison():
    assert dd.assess_comparability(dd.intake_facts(SEPT), None)["status"] == "NO_PREDECESSOR"


def test_schema_difference_and_current_synthetic_are_reasons():
    other = _intake(schema_fingerprint="0" * 64)
    assert {r["code"] for r in dd.assess_comparability(
        dd.intake_facts(SEPT), dd.intake_facts(other))["reasons"]} == {"SCHEMA_FINGERPRINT_DIFFERS"}
    syn_current = _intake(source_metadata={"declared_source": "ONC/RCE", "registration_notes": "synthetic"})
    assert "CURRENT_SYNTHETIC" in {r["code"] for r in dd.assess_comparability(
        dd.intake_facts(syn_current), dd.intake_facts(JULY))["reasons"]}


def test_two_synthetic_deliveries_of_the_same_scale_are_not_flagged_for_synthetic():
    a = _intake(record_count=8, source_metadata={"declared_source": "SYNTHETIC-A"})
    b = _intake(record_count=9, source_metadata={"declared_source": "SYNTHETIC-A"})
    assert dd.assess_comparability(dd.intake_facts(a), dd.intake_facts(b))["status"] == "COMPARABLE"


def test_a_real_looking_delivery_is_not_called_synthetic_by_content_alone():
    assert dd.is_synthetic_intake(filename="onc-snapshot-20260902.csv", label="Capacity Test",
                                  meta={"declared_source": "ONC/RCE",
                                        "registration_notes": "Authorized DEV-only test."}) is False


def test_facts_keep_snapshot_received_and_ingestion_apart():
    facts = dd.intake_facts(SEPT)
    assert facts["received_date"] == "2026-09-02"            # the delivery's calendar date
    assert facts["received_at"] == "2026-09-02T00:00:00"      # stored naive value, 00:00
    assert "without an offset" in facts["received_at_tz"]
    assert facts["created_at"] == "2026-09-24T19:56:10"       # ingestion time, a different day
    assert "ingestion" in facts["created_at_meaning"]
