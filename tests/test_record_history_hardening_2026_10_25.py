"""Pure checks for the 2026-10-25 hardening of record_history_comparison
(CodeRabbit findings on PR #169): activation direction and deterministic
delivery chronology. No database."""
from datetime import datetime

import pytest

from app.tefca_registry.rce import record_history_comparison as svc


@pytest.mark.parametrize("earlier, later, expected", [
    ("1", "0", svc.ACTIVE_DEACTIVATED),
    ("0", "1", svc.ACTIVE_REACTIVATED),
    ("1.0", "0.0", svc.ACTIVE_DEACTIVATED),      # spreadsheet float round-trip forms
    ("true", "false", svc.ACTIVE_DEACTIVATED),
    ("1", "1", None),
    ("0", "0.0", None),                           # same normalised value: no change
    ("1", "maybe", svc.ACTIVE_CHANGED_OTHER),
    ("", "1", svc.ACTIVE_CHANGED_OTHER),
])
def test_active_direction_is_stated_from_the_values_not_from_the_fact_of_change(earlier, later, expected):
    assert svc.active_direction(earlier, later) == expected


def test_delivery_order_key_breaks_same_instant_ties_by_intake_id_string():
    t = datetime(2026, 9, 2, 12, 0, 0)
    a = svc.delivery_order_key(t, "00000000-0000-0000-0000-00000000000a")
    b = svc.delivery_order_key(t, "00000000-0000-0000-0000-00000000000b")
    assert a < b
    assert svc.delivery_order_key(datetime(2026, 7, 20), "zzz") < a


def test_chronology_error_is_a_value_error_and_not_found_is_not():
    assert issubclass(svc.ChronologyError, ValueError)
    assert not issubclass(svc.RecordHistoryNotFound, ValueError)


def test_compare_requires_the_caller_context():
    import inspect
    params = inspect.signature(svc.compare_two_records).parameters
    for required in ("reviewer_or_above", "settings"):
        assert required in params and params[required].default is inspect.Parameter.empty
