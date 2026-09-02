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

So this module holds what that connector was missing and nothing else:

    URL SAFETY   — the SSRF guard it had no equivalent of
    PINNING      — the answer to DNS rebinding
    STREAMING    — a byte cap that is a cap, not a trim
    EXTRACTION   — the observed name / address / phone / contact

`website_corroboration` calls these. Its `result` vocabulary, its keys and its
"never affects the determination" contract are untouched.

THE THREAT, STATED PLAINLY
──────────────────────────
The URL comes from a delivered Government data file — attacker-influenced input
in the general case — and is fetched from inside the deployment. That is the
shape of Server-Side Request Forgery, and the deployment sits next to things a
public website never should: the Azure instance metadata service at
169.254.169.254, VNet-integrated services on 100.64.0.0/10, the database, and
whatever else answers on RFC 1918 space.

FOUR CONTROLS, AND WHAT EACH ONE CLOSES

  1. RESOLVE-AND-CHECK. Every hostname is resolved and EVERY address it
     resolves to must be globally routable — not private, loopback, link-local,
     reserved, multicast, unspecified, carrier-grade NAT or benchmarking space.
     Python's `is_private` alone misses 100.64.0.0/10, which is why the check
     is `not is_global` PLUS explicit ranges rather than any one flag. This is
     what refuses `http://169.254.169.254/`, `http://[::ffff:127.0.0.1]/`, the
     NAT64 form of loopback, and a hostname that resolves to 10.x.

  2. PIN. Independent review found the gap between check 1 and the connection:
     the guard resolved the name, then httpx resolved it AGAIN to connect. A
     DNS answer that changes between those two lookups — deliberately, with a
     short TTL — is DNS rebinding, and it defeats check 1 completely. So the
     connection is made to the ADDRESS that passed, with the original hostname
     carried as the `Host` header and as SNI for certificate verification. The
     name is never resolved a second time.

  3. RE-CHECK EVERY HOP. Redirects are followed manually and each target goes
     through 1 and 2. A public site that 302s to the metadata service is the
     textbook case, and trusting the chain because the first URL passed is the
     hole.

  4. STREAM WITH A CAP. The first version read `response.content` and THEN
     sliced it — the entire body had already been buffered, so the "limit"
     limited nothing. The body is now read in chunks and reading stops at
     MAX_BYTES.

Also: userinfo is stripped from every URL before it is dialled. A delivered
`https://user:secret@host/` would otherwise send those credentials to `host`,
and a delivery file is not a credential store.

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

import asyncio
import ipaddress
import logging
import os
import re
import socket
from typing import Any, Dict, List, Optional, Tuple
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

#: Ranges Python's `is_private` does NOT cover but which are never a public
#: website. Checked explicitly so that the rule does not depend on which
#: Python release's notion of "private" is installed.
_NON_PUBLIC_NETWORKS: Tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("100.64.0.0/10"),    # RFC 6598 carrier-grade NAT; Azure VNet integration
    ipaddress.ip_network("192.0.0.0/24"),     # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),    # benchmarking
    ipaddress.ip_network("240.0.0.0/4"),      # reserved
    ipaddress.ip_network("64:ff9b::/96"),     # NAT64 — embeds an IPv4 that must be checked itself
    ipaddress.ip_network("64:ff9b:1::/48"),   # local-use NAT64
    ipaddress.ip_network("2001:db8::/32"),    # documentation
)


class UrlRefused(RuntimeError):
    """The URL was refused before any connection was attempted."""


# ── URL safety ───────────────────────────────────────────────────────────────

def normalize_url(raw: Optional[str]) -> Optional[str]:
    """A dialable absolute URL, or None if there is nothing usable.

    A bare `example.org` is assumed https. Userinfo, query and fragment are
    dropped: credentials in a delivered file must never be sent anywhere, and
    the query and fragment are not needed to read a home page — they are the
    part of a delivered URL most likely to be doing something other than
    naming a page.
    """
    if not raw or not str(raw).strip():
        return None
    candidate = str(raw).strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
        return None
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # an IPv6 literal must be bracketed to be re-parsed
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = f"{host}:{port}" if port else host
    return urlunparse((parsed.scheme, netloc, parsed.path or "/", "", "", ""))


def _is_public(ip: ipaddress._BaseAddress) -> bool:
    """Globally routable, and not in any range a website never lives in."""
    # An IPv4-mapped IPv6 address is judged by the IPv4 it carries — the
    # address family is a wrapper, not a location.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _is_public(mapped)
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or not ip.is_global):
        return False
    return not any(ip in network for network in _NON_PUBLIC_NETWORKS)


def _resolve_public(host: str) -> Tuple[List[str], Optional[str]]:
    """(public addresses, refusal reason). EVERY resolved address must pass.

    A hostname that resolves to one public and one private address is refused
    outright, because which one a client library ends up dialling is not ours
    to decide.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return [], f"host does not resolve ({exc.strerror or 'DNS failure'})"
    if not infos:
        return [], "host resolved to no addresses"

    addresses: List[str] = []
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return [], "host resolved to an unusable address"
        if not _is_public(ip):
            return [], ("host resolves to a non-public address; refused "
                        "before connecting")
        if address not in addresses:
            addresses.append(address)
    return addresses, None


def check_url(url: Optional[str]) -> Tuple[bool, Optional[str]]:
    """(ok, reason) for a URL. Performs DNS resolution and nothing else.

    Synchronous. Async callers use `check_url_async`, which is this off the
    event loop. Prefer `pinned_target` when you are about to connect — it
    returns the address that passed, so the name need not be resolved again.
    """
    _, target, reason = _check(url)
    return target is not None, reason


def _check(url: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(hostname, first public address, refusal reason)."""
    if not url:
        return None, None, "no URL"
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return None, None, f"scheme {parsed.scheme!r} is not permitted"
    host = parsed.hostname
    if not host:
        return None, None, "no host in URL"
    if parsed.username or parsed.password:
        return host, None, "credentials in URL are not permitted"
    try:
        port = parsed.port
    except ValueError:
        return host, None, "malformed port"
    if port is not None and port not in ALLOWED_PORTS:
        return host, None, f"port {port} is not permitted"
    addresses, reason = _resolve_public(host)
    if reason:
        return host, None, reason
    return host, addresses[0], None


async def check_url_async(url: Optional[str]) -> Tuple[bool, Optional[str]]:
    """`check_url` off the event loop.

    `socket.getaddrinfo` blocks. One blocked lookup is imperceptible; a
    verification pass over thousands of delivered organisations stalls the
    whole worker, including every unrelated request it is serving.
    """
    return await asyncio.to_thread(check_url, url)


class PinnedTarget:
    """A URL rewritten to the address that passed the guard.

    `url` is what to dial — the IP literal in place of the hostname. `headers`
    carries the original `Host`. `extensions` carries the original hostname as
    `sni_hostname`, which httpx/httpcore use for BOTH the TLS SNI and the
    certificate hostname check, so a pinned HTTPS connection is verified against
    the name, not the address.
    """

    __slots__ = ("url", "hostname", "address", "headers", "extensions")

    def __init__(self, url: str, hostname: str, address: str):
        parsed = urlparse(url)
        literal = f"[{address}]" if ":" in address else address
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = f"{literal}:{port}" if port else literal
        self.url = urlunparse((parsed.scheme, netloc, parsed.path or "/",
                               "", "", ""))
        self.hostname = hostname
        self.address = address
        host_header = hostname if port is None else f"{hostname}:{port}"
        self.headers = {"Host": host_header}
        self.extensions = ({"sni_hostname": hostname}
                           if parsed.scheme == "https" else {})


async def pinned_target(url: Optional[str]) -> Tuple[Optional[PinnedTarget], Optional[str]]:
    """Resolve once, check, and return the target to dial — or the refusal.

    THIS IS THE DNS-REBINDING CONTROL. The address returned here is the one the
    connection is made to. Nothing between this call and the socket looks the
    name up again, so a DNS answer that changes after the check cannot redirect
    the request.
    """
    hostname, address, reason = await asyncio.to_thread(_check, url)
    if address is None:
        return None, reason
    return PinnedTarget(url, hostname, address), None


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


# ── streaming read ───────────────────────────────────────────────────────────

async def read_capped(response, limit: int = MAX_BYTES) -> Tuple[bytes, bool]:
    """(bytes, truncated). Reads at most `limit` bytes and STOPS.

    For a streamed httpx response. A non-streaming response object (a test
    double with only `.text`, say) is read through `body_text` instead.
    """
    chunks: List[bytes] = []
    total = 0
    truncated = False
    aiter = getattr(response, "aiter_bytes", None)
    if aiter is None:
        raw = getattr(response, "content", b"") or b""
        return bytes(raw[:limit]), len(raw) > limit
    async for chunk in aiter():
        if not chunk:
            continue
        remaining = limit - total
        if len(chunk) >= remaining:
            chunks.append(chunk[:remaining])
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), truncated


def body_text(response) -> str:
    """The body of a NON-streamed response as text, capped at MAX_BYTES.

    Kept for response objects that expose only `.content` or `.text`. Live
    fetches go through `read_capped`, which is a real cap; this is a trim of
    something already in memory and is documented as such.
    """
    raw = getattr(response, "content", None)
    if isinstance(raw, (bytes, bytearray)):
        return decode(bytes(raw[:MAX_BYTES]), getattr(response, "encoding", None))
    return (getattr(response, "text", "") or "")[:MAX_BYTES]


def decode(body: bytes, declared_encoding: Optional[str]) -> str:
    """Bytes to text, never raising on a bad declaration."""
    try:
        return body.decode(declared_encoding or "utf-8", errors="replace")
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


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
        "note": ("One page per entity, no crawling. Resolve-and-check, pinned "
                 "connection, every redirect re-checked, streamed byte cap. "
                 "Corroborating identity and contact evidence only; never "
                 "authoritative for NPI, enrolment, exclusion, registration or "
                 "TEFCA status."),
    }
