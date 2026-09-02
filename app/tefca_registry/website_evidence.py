"""URL safety and page-reading for website corroboration.

WHY THIS IS A LIBRARY AND NOT A CONNECTOR
─────────────────────────────────────────
`Tefca/evidence_service.website_corroboration` is ALREADY the website connector.
It is wired into `EvidenceService`, its result vocabulary is consumed by
`evidence_assembly`, and `tests/test_cms_pecos_evidence.py` pins its behaviour —
in particular that an unreachable site can never become a finding against an
entity. Adding a second website connector beside it would be exactly the
duplication this pipeline is supposed to avoid: two definitions of "what the
site said", diverging the first time one is fixed.

So this module holds the two things that connector was missing and nothing else:

    URL SAFETY   — the SSRF guard it had no equivalent of
    EXTRACTION   — the observed name / address / phone / contact it returned
                   as `"address": None` with a note saying extraction was not
                   attempted

`website_corroboration` calls both. Its `result` vocabulary, its keys and its
"never affects the determination" contract are untouched.

SSRF IS THE REAL RISK
─────────────────────
The URL comes from a delivered Government data file — attacker-influenced input
in the general case — and the existing connector dialled it with
`follow_redirects=True` and no address checking at all. That is the textbook
Server-Side Request Forgery shape: a delivered value of `http://169.254.169.254/`
would have been fetched from inside the deployment, and a redirect to it would
have been followed silently.

Hence: scheme and port allow-lists, DNS resolved and EVERY resolved address
checked against private, loopback, link-local, reserved and multicast ranges
before connecting, and each redirect hop re-checked rather than trusted because
the first URL passed.

WHAT THE EVIDENCE IS WORTH
──────────────────────────
Corroborating identity and contact evidence only. A website is self-published;
the organisation controls every byte of it. It is never authoritative for NPI
(NPPES is), enrolment (PECOS/PPEF is), exclusion (OIG LEIE is), registration
(SAM.gov is) or TEFCA status (the RCE delivery is). `AUTHORITATIVE_FOR` is empty
and stated as a constant so that it is greppable and testable rather than merely
absent.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger("docuaction.tefca.website")

#: Nothing. See the module docstring.
AUTHORITATIVE_FOR: tuple = ()

SOURCE_NAME = "ORGANIZATION_WEBSITE"
EVIDENCE_KIND = "CORROBORATING_IDENTITY_CONTACT"

ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = (80, 443)

#: One page per entity. Never more — this module discovers no URLs and queues
#: nothing, so there is no crawl to bound beyond the redirect chain.
MAX_REDIRECTS = 3

#: Read cap. A home page larger than this will not yield better contact details,
#: and an unbounded read is a memory risk on a shared worker.
MAX_BYTES = int(os.getenv("WEBSITE_EVIDENCE_MAX_BYTES", str(2 * 1024 * 1024)))


class UrlRefused(RuntimeError):
    """The URL was refused before any connection was attempted."""


# ── URL safety ───────────────────────────────────────────────────────────────

def normalize_url(raw: Optional[str]) -> Optional[str]:
    """A dialable absolute URL, or None if there is nothing usable.

    A bare `example.org` is assumed https. The query and fragment are dropped:
    they are not needed to read a home page, and they are the part of a
    delivered URL most likely to be doing something other than naming a page.
    """
    if not raw or not str(raw).strip():
        return None
    candidate = str(raw).strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/",
                       "", "", ""))


def _addresses_are_public(host: str):
    """(ok, reason). Resolve and refuse anything not publicly routable.

    EVERY resolved address must pass. A hostname that resolves to one public and
    one private address is refused, because which one the client library ends up
    dialling is not ours to decide.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"host does not resolve ({exc.strerror or 'DNS failure'})"
    if not infos:
        return False, "host resolved to no addresses"

    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False, "host resolved to an unusable address"
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, ("host resolves to a non-public address; refused "
                           "before connecting")
    return True, None


def check_url(url: Optional[str]):
    """(ok, reason) for a URL. Performs DNS resolution and nothing else.

    Synchronous, because the scheme and port checks are pure and the DNS lookup
    is the only blocking part. Async callers must use `check_url_async`, which
    is the same function moved off the event loop.
    """
    if not url:
        return False, "no URL"
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return False, f"scheme {parsed.scheme!r} is not permitted"
    host = parsed.hostname
    if not host:
        return False, "no host in URL"
    try:
        port = parsed.port
    except ValueError:
        return False, "malformed port"
    if port is not None and port not in ALLOWED_PORTS:
        return False, f"port {port} is not permitted"
    return _addresses_are_public(host)


async def check_url_async(url: Optional[str]):
    """`check_url` off the event loop.

    `socket.getaddrinfo` blocks. One blocked lookup is imperceptible; a
    verification pass over thousands of delivered organisations stalls the
    whole worker, including every unrelated request it is serving. So the
    lookup runs in a thread and the loop stays free.
    """
    import asyncio

    return await asyncio.to_thread(check_url, url)


def resolve_redirect(current: str, location: str) -> Optional[str]:
    """The next hop of a redirect, normalized. None if it is not usable."""
    if not location:
        return None
    if "://" in location:
        return normalize_url(location)
    base = urlparse(current)
    if location.startswith("/"):
        return normalize_url(f"{base.scheme}://{base.netloc}{location}")
    path = base.path.rsplit("/", 1)[0]
    return normalize_url(f"{base.scheme}://{base.netloc}{path}/{location}")


# ── extraction ───────────────────────────────────────────────────────────────

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_SITE = re.compile(
    r"<meta[^>]+property=[\"']og:site_name[\"'][^>]+content=[\"']([^\"']{1,200})",
    re.I)
_JSONLD_NAME = re.compile(r"\"name\"\s*:\s*\"([^\"]{2,200})\"")
_PHONE = re.compile(
    r"(?:\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})\b")
_EMAIL = re.compile(
    r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}")
_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_ANY_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_ADDRESS = re.compile(
    r"\d{1,6}\s+[A-Za-z0-9.\- ]{2,60}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|"
    r"Court|Ct|Place|Pl|Parkway|Pkwy|Suite|Ste)\b[A-Za-z0-9.,\- ]{0,80}",
    re.I)


def extract(html: str) -> Dict[str, Any]:
    """Read identity and contact details out of one page. Bounded, best-effort.

    Deliberately shallow. This is corroborating evidence an analyst READS, not a
    parsed record anything computes on, so a regex over the visible text is the
    honest tool: it finds roughly what a person would find, and when it finds
    nothing it returns None rather than guessing.

    The first match wins for each field and every value is length-capped, so a
    hostile or enormous page cannot turn one observation into an unbounded
    string. `OBSERVED` is in every key name because that is what these are —
    what the site published, not what is true.
    """
    source = html or ""
    text_only = _WHITESPACE.sub(
        " ", _ANY_TAG.sub(" ", _SCRIPT_STYLE.sub(" ", source))).strip()

    name = None
    for pattern in (_META_SITE, _JSONLD_NAME, _TITLE):
        found = pattern.search(source)
        if found:
            candidate = _WHITESPACE.sub(
                " ", _ANY_TAG.sub("", found.group(1))).strip()
            if candidate:
                name = candidate[:200]
                break

    phone = None
    phone_match = _PHONE.search(text_only)
    if phone_match:
        phone = (f"({phone_match.group(1)}) {phone_match.group(2)}"
                 f"-{phone_match.group(3)}")

    email_match = _EMAIL.search(text_only)
    address_match = _ADDRESS.search(text_only)

    return {
        "organization_name_observed": name,
        "phone_observed": phone,
        "contact_observed": (email_match.group(0)[:200] if email_match else None),
        "address_observed": (
            _WHITESPACE.sub(" ", address_match.group(0)).strip()[:200]
            if address_match else None),
    }


def body_text(response) -> str:
    """The response body as text, capped at MAX_BYTES.

    Prefers `.content` so the cap applies to BYTES — capping `.text` would mean
    decoding an unbounded body first, which is the allocation the cap exists to
    prevent. Falls back to `.text` for any response object that does not expose
    raw bytes.
    """
    raw = getattr(response, "content", None)
    if isinstance(raw, (bytes, bytearray)):
        return decode(bytes(raw[:MAX_BYTES]),
                      getattr(response, "encoding", None))
    return (getattr(response, "text", "") or "")[:MAX_BYTES]


def decode(body: bytes, declared_encoding: Optional[str]) -> str:
    """Bytes to text, never raising on a bad declaration."""
    try:
        return body.decode(declared_encoding or "utf-8", errors="replace")
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def observation_fields(url: Optional[str], html: Optional[str] = None,
                       *, reachable: bool = False) -> Dict[str, Any]:
    """The §13.E display fields for one website observation.

    Returned as a flat dict for the caller to merge into ITS result envelope —
    this module deliberately owns no result vocabulary, because the connector
    that calls it already has one and a second would be a second answer.
    """
    parsed = urlparse(url) if url else None
    fields: Dict[str, Any] = {
        "domain": (parsed.hostname if parsed else None),
        "https": ((parsed.scheme == "https") if parsed else None),
        "reachable": reachable,
        "organization_name_observed": None,
        "address_observed": None,
        "phone_observed": None,
        "contact_observed": None,
        "authoritative": False,
        "authoritative_for": list(AUTHORITATIVE_FOR),
        "evidence_kind": EVIDENCE_KIND,
    }
    if html is not None:
        fields.update(extract(html))
    return fields


def health() -> Dict[str, Any]:
    """Whether website reading is usable, in the shape the other connectors use."""
    return {
        "source": SOURCE_NAME,
        "configured": True,
        "status": "available",
        "authoritative_for": list(AUTHORITATIVE_FOR),
        "note": ("One page per entity, no crawling, SSRF-guarded. "
                 "Corroborating identity and contact evidence only; never "
                 "authoritative for NPI, enrolment, exclusion, registration or "
                 "TEFCA status."),
    }
