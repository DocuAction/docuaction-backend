"""Review SLA: due dates, overdue detection, and status bands (QA-2.1 - QA-2.3).

WHY THIS FILE EXISTS AS PURE FUNCTIONS

Before this, the registry had no notion of a review being due. There was no
due_date column, no dashboard, and no overdue metric — so "the dashboard shows
no overdue metrics" was not a display bug, it was a missing model. Deciding when
a review is late is a policy question, and policy that lives inline in a route
handler cannot be tested without a database and cannot be found when it needs to
change.

THE POLICY, STATED PLAINLY

A sampled entity becomes due a fixed number of days after the sample it belongs
to was drawn. The window depends on the review cadence, because a weekly review
and a quarterly one are not late at the same time:

    weekly     7 days
    quarterly  90 days
    priority   3 days   (COR-directed, and the point of it is speed)

These numbers are a starting policy, NOT a contractual SLA anyone has ratified.
They are declared here, in one place, so that changing them is a one-line edit
against a named constant rather than a hunt through query filters. If the
contract specifies different windows, this dict is the only thing that changes.

The status bands come from the QA report and are exact:

    days_remaining <  0   overdue
    days_remaining <= 2   at_risk
    otherwise             on_track

Boundary worth stating because it is easy to get backwards: a review due later
today has 0 days remaining, which is at_risk, not overdue. It only becomes
overdue once the due moment has passed.

DATES ARE ISO 8601 EVERYWHERE (QA-2.2)

Every date this module emits is `datetime.isoformat()`. Formatting for humans is
the display layer's job — an API that returns "Aug 7, 2026" cannot be sorted,
compared, or parsed by a caller in another timezone, and mixing the two formats
in one payload is the inconsistency QA-2.2 reports.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# Days from sample-drawn to due, by review cadence. See the module docstring:
# a starting policy, deliberately in one editable place.
REVIEW_SLA_DAYS: Dict[str, int] = {
    "weekly": 7,
    "quarterly": 90,
    "priority": 3,
}
DEFAULT_SLA_DAYS = 7

# A review is "at risk" at this many days remaining or fewer. From QA-2.3.
AT_RISK_DAYS = 2

OVERDUE = "overdue"
AT_RISK = "at_risk"
ON_TRACK = "on_track"


def sla_days_for(review_type: Optional[str]) -> int:
    """Window for a cadence. An unrecognised cadence gets the default rather
    than an error: a review with a typo'd type still needs a due date, and
    failing here would take out the whole dashboard."""
    return REVIEW_SLA_DAYS.get((review_type or "").strip().lower(), DEFAULT_SLA_DAYS)


def _as_naive_utc(value: datetime) -> datetime:
    """Drop tzinfo after converting to UTC.

    The database columns are naive (`DateTime` without timezone) while a caller
    may hand us an aware `now`. Subtracting one from the other raises TypeError,
    and doing that inside a dashboard query turns a display feature into a 500.
    Normalising on the way in is cheaper than auditing every call site.
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def due_date_for(started_at: Optional[datetime],
                 review_type: Optional[str] = None) -> Optional[datetime]:
    """When a review drawn at `started_at` is due. None in, None out — a sample
    with no drawn_at has no computable due date, and inventing one would report
    a review as overdue on the strength of a missing timestamp."""
    if started_at is None:
        return None
    return _as_naive_utc(started_at) + timedelta(days=sla_days_for(review_type))


def days_remaining(due_date: Optional[datetime],
                   now: Optional[datetime] = None) -> Optional[int]:
    """Whole days until due; negative once past. None when there is no due date.

    Truncating toward negative infinity (which is what timedelta.days does) is
    the behaviour we want: a review due 1 hour ago yields -1, not 0, so it reads
    as overdue rather than as due-today.
    """
    if due_date is None:
        return None
    now = _as_naive_utc(now or datetime.utcnow())
    return (_as_naive_utc(due_date) - now).days


def sla_status(due_date: Optional[datetime],
               now: Optional[datetime] = None) -> Optional[str]:
    """One of overdue | at_risk | on_track, or None when there is no due date."""
    remaining = days_remaining(due_date, now)
    if remaining is None:
        return None
    if remaining < 0:
        return OVERDUE
    if remaining <= AT_RISK_DAYS:
        return AT_RISK
    return ON_TRACK


def is_overdue(due_date: Optional[datetime], now: Optional[datetime] = None,
               completed: bool = False) -> bool:
    """Overdue means past due AND not yet done.

    The `completed` half is not a detail. A finished review whose due date has
    passed is a review that was delivered late, not an outstanding one, and
    counting it in an overdue queue makes the number grow forever and stop
    meaning anything.
    """
    if completed or due_date is None:
        return False
    return sla_status(due_date, now) == OVERDUE


def describe(started_at: Optional[datetime], review_type: Optional[str] = None,
             *, now: Optional[datetime] = None,
             completed: bool = False) -> Dict[str, Any]:
    """The SLA block attached to each review in an API response.

    Dates are ISO 8601 strings (QA-2.2). A completed review reports its status
    as `on_track` regardless of dates — it is no longer racing anything — while
    `due_date` and `sla_days_remaining` are still reported so a late delivery
    stays visible rather than being erased by completion.
    """
    due = due_date_for(started_at, review_type)
    remaining = days_remaining(due, now)
    status = ON_TRACK if completed else sla_status(due, now)
    return {
        "due_date": due.isoformat() if due else None,
        "sla_status": status,
        "sla_days_remaining": remaining,
        "sla_window_days": sla_days_for(review_type),
    }
