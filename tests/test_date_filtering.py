"""Freshness / date-filtering tests.

Context: a March article reached an August briefing. The cause was not one bad
filter but the absence of any gate at all — enforce_freshness() existed, was
correct, and was never called. Each source policed its own dates, and Congress
filtered on re-index time while dating on action time.

These pin the gate that now runs after every source and before classification.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.bulletin_intelligence.editorial_rules import (
    DATE_UNKNOWN, HARD_MAX_AGE_HOURS, enforce_freshness)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


class Art:
    def __init__(self, title, published_at):
        self.title = title
        self.published_at = published_at
        self.url = f"https://x.test/{title}"


def _run(articles, lookback_hours=48):
    return enforce_freshness(articles, lookback_hours=lookback_hours, now=NOW)


def test_today_is_accepted():
    a = Art("today", "2026-08-05T09:00:00+00:00")
    assert _run([a]) == [a]


def test_yesterday_is_accepted_within_the_editorial_window():
    """Yesterday is CORRECT for a 00:01 briefing — it covers the day that just
    closed. The bug was never 'yesterday appears'."""
    a = Art("yesterday", "2026-08-04T09:00:00+00:00")
    assert _run([a], lookback_hours=48) == [a]


def test_last_week_is_rejected():
    a = Art("last week", "2026-07-28T09:00:00+00:00")
    assert _run([a]) == []


def test_march_article_is_rejected():
    """The reported production defect."""
    a = Art("MARCH", "2026-03-15T09:00:00+00:00")
    assert _run([a]) == []
    # ...and it stays rejected even if some caller passes an absurd window,
    # because the hard rail is independent of the editorial window.
    assert _run([a], lookback_hours=24 * 365) == []


def test_future_dated_is_rejected():
    """A date in the future is a source error, not a scoop."""
    assert _run([Art("future", "2026-09-01T09:00:00+00:00")]) == []
    # Small clock skew is tolerated rather than treated as an error.
    skewed = Art("skewed", (NOW + timedelta(hours=2)).isoformat())
    assert _run([skewed]) == [skewed]


def test_undated_is_kept_and_flagged_not_dropped_and_not_backdated():
    """The two wrong answers are dropping it (loses real stories) and stamping
    now() (fabricates a date and hides it from every future filter). Mark it and
    let a human decide."""
    missing = Art("no date", "")
    junk = Art("unparseable", "not a date at all")
    kept = _run([missing, junk])

    assert kept == [missing, junk]
    assert missing.date_status == DATE_UNKNOWN
    assert junk.date_status == DATE_UNKNOWN
    assert missing.date_status_reason == "missing"
    assert junk.date_status_reason == "unparseable"


def test_rfc2822_dates_are_understood():
    """RSS feeds emit RFC-2822, not ISO-8601. Failing to parse them would send
    every RSS article down the undated path."""
    a = Art("rss style", "Tue, 04 Aug 2026 09:00:00 GMT")
    assert _run([a]) == [a]
    assert not hasattr(a, "date_status") or a.date_status != DATE_UNKNOWN


def test_naive_timestamps_are_treated_as_utc():
    a = Art("naive", "2026-08-04T09:00:00")
    assert _run([a]) == [a]


def test_mixed_batch_keeps_only_what_it_should():
    arts = [
        Art("today", "2026-08-05T09:00:00+00:00"),
        Art("yesterday", "2026-08-04T09:00:00+00:00"),
        Art("last week", "2026-07-28T09:00:00+00:00"),
        Art("march", "2026-03-15T09:00:00+00:00"),
        Art("no date", ""),
        Art("future", "2026-09-01T09:00:00+00:00"),
    ]
    kept = {a.title for a in _run(arts)}
    assert kept == {"today", "yesterday", "no date"}


def test_hard_rail_default_is_48_hours():
    assert HARD_MAX_AGE_HOURS == 48


def test_congress_action_date_parsing():
    """Congress.gov returns YYYY-MM-DD. The bug was filtering on index time while
    dating on action time, so the action date must parse for the fix to bite."""
    from app.bulletin_intelligence.engine import _parse_action_date

    assert _parse_action_date("2026-03-15") == datetime(2026, 3, 15, tzinfo=timezone.utc)
    assert _parse_action_date("") is None
    assert _parse_action_date("not-a-date") is None

    cutoff = NOW - timedelta(hours=48)
    assert _parse_action_date("2026-03-15") < cutoff, "a March bill must fall outside"
    assert _parse_action_date("2026-08-05") > cutoff
