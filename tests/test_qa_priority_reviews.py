"""Module 2 — Priority Reviews (QA-2.1 to QA-2.3).

The registry had no due-date model at all before this: no column, no dashboard,
no overdue metric. So these tests pin a policy as much as they pin behaviour,
and the boundaries are where the value is — off-by-one on "overdue" either
raises a false alarm on every review due today, or hides one that is already
late.

Time is injected rather than mocked. A test that computes its expectation from
`utcnow()` is a test that agrees with the code because both made the same
mistake.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.tefca_registry import sla

pytestmark = [pytest.mark.regression, pytest.mark.qa_defect]

NOW = datetime(2026, 8, 7, 12, 0, 0)


def _drawn(days_ago: float) -> datetime:
    return NOW - timedelta(days=days_ago)


# ── QA-2.3 — SLA bands ───────────────────────────────────────────────────────

def test_sla_overdue_status():
    """Drawn 10 days ago on a 7-day window: 3 days late."""
    due = sla.due_date_for(_drawn(10), "weekly")
    assert sla.sla_status(due, NOW) == sla.OVERDUE
    assert sla.days_remaining(due, NOW) == -3


def test_sla_at_risk_status():
    """Drawn 5 days ago on a 7-day window: 2 days left, which is the at_risk
    boundary the report specifies (days <= 2)."""
    due = sla.due_date_for(_drawn(5), "weekly")
    assert sla.days_remaining(due, NOW) == 2
    assert sla.sla_status(due, NOW) == sla.AT_RISK


def test_sla_on_track_status():
    due = sla.due_date_for(_drawn(1), "weekly")
    assert sla.days_remaining(due, NOW) == 6
    assert sla.sla_status(due, NOW) == sla.ON_TRACK


@pytest.mark.parametrize("remaining_days,expected", [
    (-1, sla.OVERDUE),
    (0, sla.AT_RISK),      # due later today is NOT yet overdue
    (2, sla.AT_RISK),      # inclusive upper bound
    (3, sla.ON_TRACK),     # first day outside the band
])
def test_the_band_boundaries_are_exact(remaining_days, expected):
    """Each of these is one day either side of a threshold. Getting any of them
    wrong changes what the dashboard claims about real reviews."""
    due = NOW + timedelta(days=remaining_days)
    assert sla.sla_status(due, NOW) == expected


def test_a_review_due_one_hour_ago_is_overdue_not_due_today():
    """Truncation direction. A due moment that has passed must read as overdue
    even when it passed within the same calendar day."""
    due = NOW - timedelta(hours=1)
    assert sla.days_remaining(due, NOW) == -1
    assert sla.sla_status(due, NOW) == sla.OVERDUE


def test_a_review_due_in_one_hour_is_at_risk():
    due = NOW + timedelta(hours=1)
    assert sla.days_remaining(due, NOW) == 0
    assert sla.sla_status(due, NOW) == sla.AT_RISK


# ── QA-2.1 — overdue detection ───────────────────────────────────────────────

def test_overdue_metrics_calculated():
    due = sla.due_date_for(_drawn(10), "weekly")
    assert sla.is_overdue(due, NOW) is True


def test_no_overdue_returns_zero():
    """The all-clear case. A dashboard that can only ever report a non-zero
    count is not reporting anything."""
    reviews = [sla.due_date_for(_drawn(d), "weekly") for d in (0, 1, 2)]
    assert sum(1 for d in reviews if sla.is_overdue(d, NOW)) == 0


def test_a_completed_review_is_never_overdue():
    """Otherwise the overdue count only grows: every review ever delivered late
    stays in the queue forever and the number stops meaning 'act on this'."""
    due = sla.due_date_for(_drawn(30), "weekly")
    assert sla.is_overdue(due, NOW, completed=False) is True
    assert sla.is_overdue(due, NOW, completed=True) is False


def test_a_missing_drawn_date_is_not_reported_as_overdue():
    """No timestamp means no computable due date. Treating that as overdue
    reports a data gap as a performance failure."""
    assert sla.due_date_for(None, "weekly") is None
    assert sla.is_overdue(None, NOW) is False
    assert sla.sla_status(None, NOW) is None


# ── Cadence windows ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("review_type,days", [
    ("weekly", 7), ("quarterly", 90), ("priority", 3),
])
def test_each_cadence_has_its_own_window(review_type, days):
    """A weekly review and a quarterly one are not late at the same time."""
    assert sla.sla_days_for(review_type) == days
    assert sla.due_date_for(NOW, review_type) == NOW + timedelta(days=days)


@pytest.mark.parametrize("bogus", ["", None, "fortnightly", "WEEKLY "])
def test_an_unknown_cadence_falls_back_rather_than_raising(bogus):
    """A typo'd review_type must not take the dashboard down."""
    expected = 7 if (bogus or "").strip().lower() == "weekly" else sla.DEFAULT_SLA_DAYS
    assert sla.sla_days_for(bogus) == expected


def test_cadence_lookup_is_case_and_whitespace_insensitive():
    assert sla.sla_days_for("  Weekly ") == 7
    assert sla.sla_days_for("QUARTERLY") == 90


# ── QA-2.2 — ISO 8601 ────────────────────────────────────────────────────────

def test_dates_iso_format():
    block = sla.describe(_drawn(1), "weekly", now=NOW)
    assert block["due_date"] == "2026-08-13T12:00:00"
    # Parseable back into the same instant — the actual point of ISO 8601.
    assert datetime.fromisoformat(block["due_date"]) == NOW + timedelta(days=6)


def test_describe_carries_every_field_the_ui_needs():
    block = sla.describe(_drawn(10), "weekly", now=NOW)
    assert set(block) == {"due_date", "sla_status", "sla_days_remaining",
                          "sla_window_days"}
    assert block["sla_status"] == sla.OVERDUE
    assert block["sla_days_remaining"] == -3
    assert block["sla_window_days"] == 7


def test_a_completed_review_still_reports_its_dates():
    """Status goes on_track because it is no longer racing anything, but the
    due date stays visible so a late delivery is not erased by completion."""
    block = sla.describe(_drawn(30), "weekly", now=NOW, completed=True)
    assert block["sla_status"] == sla.ON_TRACK
    assert block["due_date"] is not None
    assert block["sla_days_remaining"] == -23


# ── Timezone handling ────────────────────────────────────────────────────────

def test_an_aware_now_does_not_raise_against_naive_db_timestamps():
    """The DB columns are naive; a caller may pass an aware datetime. Mixing the
    two raises TypeError, and doing that inside a dashboard query turns a
    display feature into a 500."""
    aware_now = NOW.replace(tzinfo=timezone.utc)
    due = sla.due_date_for(_drawn(10), "weekly")
    assert sla.sla_status(due, aware_now) == sla.OVERDUE


def test_an_aware_drawn_at_is_normalised_too():
    aware_drawn = _drawn(10).replace(tzinfo=timezone.utc)
    assert sla.sla_status(sla.due_date_for(aware_drawn, "weekly"), NOW) == sla.OVERDUE
