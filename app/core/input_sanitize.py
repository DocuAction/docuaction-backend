"""Request-level input sanitisation.

PostgreSQL cannot store or compare a NUL byte inside a text value, so a raw
``\\x00`` reaching a query raises at the driver and the request ends as an
HTTP 500. That is a validation failure being reported as a server fault: the
input is bad, and the caller should be told so with a 4xx.

Percent-encoded ``%00`` arrives as the literal characters ``%``, ``0``, ``0``
and is harmless — only a decoded NUL is rejected here.

Found by Block 3 Suite A test A9 (2026-08-02).
"""
from urllib.parse import unquote

from fastapi import HTTPException


def reject_null_bytes(value, field: str = "input"):
    """Raise HTTP 422 if ``value`` contains a NUL byte. Returns it otherwise.

    Accepts non-string values untouched so it can be dropped into a query
    dependency without caring about the parameter's type.
    """
    if isinstance(value, str) and "\x00" in value:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field}: null bytes are not permitted.",
        )
    return value


def sanitize_input(value: str) -> str:
    """Utility form: raise ValueError on a NUL byte, else return the value."""
    if value and "\x00" in value:
        raise ValueError("Invalid input: null bytes not permitted")
    return value


def find_null_byte(path: str, query: str):
    """Return "path"/"query string" if a decoded NUL is present, else None.

    Percent-decoding happens here because ``%00`` in a raw URL is what actually
    reaches a handler as ``\\x00`` once Starlette decodes the parameter. The raw
    string is checked too, for a NUL delivered literally.

    Scope is deliberately the URL only. Buffering request bodies in middleware
    would hold every CSV/FHIR upload in memory before the route ever sees it, and
    the import endpoints exist to take multi-megabyte files. Body-borne NULs stay
    the handlers' responsibility via reject_null_bytes / sanitize_input.
    """
    for label, raw in (("path", path), ("query string", query)):
        if not raw:
            continue
        if "\x00" in raw or "\x00" in unquote(raw):
            return label
    return None
