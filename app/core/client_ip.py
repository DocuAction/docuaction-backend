"""Canonical client-IP derivation.

Behind Azure App Service the TCP peer is a platform front end, so
``request.client.host`` is the same value for every caller. Keying a throttle or
a lockout on it puts the entire user base in one bucket: twenty login attempts
across all users exhausts the window and everyone gets 429. That is the failure
this module exists to prevent.

The fix is X-Forwarded-For, but *which* entry matters. App Service appends the
address it observed to any chain the caller already sent, so the header reads:

    <whatever the client made up>, ..., <address App Service observed>
     ^ attacker-controlled                ^ platform-supplied

Only the RIGHTMOST entry is trustworthy. The leftmost is the conventional
"original client" position and is exactly what an attacker sets to hand
themselves a fresh rate-limit bucket per request. Uvicorn's own
``--forwarded-allow-ips='*'`` handling takes the leftmost entry on 0.30.x, which
is why security decisions here must not lean on ``request.client`` even when
that flag is set.

Use :func:`get_client_ip` for every throttle, lockout, and audit record.
"""

from __future__ import annotations

from typing import Any, Optional


def _strip_port(addr: str) -> str:
    """Remove a trailing ``:port``, leaving bare IPv4/IPv6 literals intact.

    App Service writes IPv4 entries as ``1.2.3.4:56789`` and IPv6 entries in the
    bracketed ``[2001:db8::1]:56789`` form. A bare IPv6 literal carries several
    colons and none of them are a port separator, so only the single-colon and
    bracketed cases are trimmed.
    """
    addr = addr.strip()
    if not addr:
        return ""
    if addr.startswith("["):
        host, sep, _ = addr.partition("]")
        return host[1:] if sep else addr
    if addr.count(":") == 1:
        return addr.split(":", 1)[0]
    return addr


def get_client_ip(request: Any) -> Optional[str]:
    """Return the caller's address as observed by the platform, or ``None``.

    Falls back to ``request.client.host`` when no X-Forwarded-For is present,
    which is the correct answer for direct connections (local development, tests
    and container-internal probes).
    """
    headers = getattr(request, "headers", None)
    xff = headers.get("x-forwarded-for", "") if headers is not None else ""
    if xff:
        for candidate in reversed([part.strip() for part in xff.split(",")]):
            if candidate:
                stripped = _strip_port(candidate)
                if stripped:
                    return stripped
    client = getattr(request, "client", None)
    return getattr(client, "host", None)
