"""Path identifiers that are UUIDs in the database.

WHY THIS EXISTS (2026-11-13)
    Several read routes declare a UUID path parameter as `str` and hand it
    straight to a query on a UUID column. A value that is not a UUID then
    fails INSIDE the database driver ("invalid input for query argument $1 ...
    invalid UUID") and the caller receives a 500 INTERNAL_ERROR - for what is
    simply the address of something that does not exist (a mistyped or
    truncated link). The same routes already answer 404, or an empty list, for
    a well-formed id that matches nothing.

`known_uuid` turns that case into the 404 the route already uses for "no such
thing". A well-formed id passes through in canonical form, so nothing changes
for any request that worked before.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException

_ECHO_LIMIT = 64


def known_uuid(value, what: str) -> str:
    """`value` as a canonical UUID string, or 404 "No <what> <value>"."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        shown = str(value)
        if len(shown) > _ECHO_LIMIT:
            shown = shown[:_ECHO_LIMIT] + "..."
        raise HTTPException(404, f"No {what} {shown}")
