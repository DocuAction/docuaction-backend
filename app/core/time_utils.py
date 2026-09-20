"""Single place to serialize a stored timestamp to ISO-8601 with an explicit
UTC offset (QA-010).

Every `datetime.utcnow()`-written naive `DateTime` column in this codebase
(`RceDeliveryJob.created_at`/`started_at`/`completed_at`/`failed_at` among
others) stores a genuine UTC instant with no timezone attached. Calling
`.isoformat()` on it directly produces a string with NO offset; a browser's
`Date` parser then treats that offset-less string as LOCAL time and shifts it
when converting to UTC for display. That is the exact mechanism behind
QA-010: the Delivery Overview's "Completed" timestamp showed four hours later
than the identical event's timestamp in Audit History / Processing Timeline
(both instants were the same stored value; only the Overview path was naive).

This function never adjusts the stored value — it labels what is already
there. A naive datetime is UTC by construction in this codebase, so it is
given a `+00:00` offset with its digits unchanged. An aware datetime is
converted to UTC (safe to call on `DateTime(timezone=True)` columns too, so
one function can serve every timestamp field without the caller needing to
know the column's type). A hard-coded shift (e.g. always subtracting four
hours) would be wrong the moment the browser's timezone changed, which is
exactly the class of fix this function is not.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional, Union


def utc_isoformat(value: Optional[Union[datetime, date]]) -> Optional[str]:
    """ISO-8601 for a stored timestamp, with an explicit UTC offset.

    - `None` -> `None`.
    - A naive `datetime` (no `tzinfo`) is treated as UTC-by-construction (the
      convention every writer in this codebase already follows) and labelled
      `+00:00`. The wall-clock digits are never changed.
    - An aware `datetime` is converted to UTC and serialized with `+00:00`.
    - A `date` (no time component, e.g. an operator-supplied delivery period)
      has no timezone to attach and is serialized as-is.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value
