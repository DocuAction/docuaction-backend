"""Controls for pulling bytes from somewhere we do not control.

Nothing in the repository did SSRF checking before this. Every existing
connector talks to a URL that is a module constant, which is safe by
construction — but the moment an operator, a config row or a CMS index supplies
a URL, "we only call constants" stops being true, and it stops being true
quietly.

WHAT THIS DEFENDS AGAINST, AND WHY EACH ONE
  SSRF            a supplied URL pointed at 169.254.169.254 or 127.0.0.1 reaches
                  the instance metadata service or a bound admin port. Checked
                  against the resolved address, not the hostname, because
                  `evil.example.com` can resolve to a loopback address.
  redirects       a permitted host can 302 to a forbidden one, so redirects are
                  not followed automatically; each hop is re-validated.
  payload size    an unbounded download is a memory exhaustion primitive.
  content type    a parser handed a type it was not written for is an attack
                  surface, not a bug report.
  archive paths   a zip entry named `../../etc/passwd` writes outside the
                  extraction directory. So does an absolute path, and so does a
                  symlink.
  secrets         a token in a URL ends up in a log, an audit row and an error
                  message. `redact()` is applied before anything is recorded.
"""
from __future__ import annotations

import ipaddress
import os
import posixpath
import re
import socket
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

#: Bytes. A quarterly CMS extract is tens of megabytes; a gigabyte is a mistake
#: or an attack. Overridable per call for a source known to be larger.
DEFAULT_MAX_BYTES = 512 * 1024 * 1024

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_ATTEMPTS = 3
#: Beyond this many hops something is wrong with the source, not the network.
MAX_REDIRECTS = 3

ALLOWED_SCHEMES: FrozenSet[str] = frozenset({"https"})

#: Query and header names whose values never appear in a log or an audit row.
_SECRET_KEYS = re.compile(
    r"(api[-_]?key|apikey|token|secret|password|passwd|signature|sig|"
    r"access[-_]?key|auth)", re.I)

_SECRET_VALUE = re.compile(
    r"(?i)\b(bearer\s+[A-Za-z0-9._\-]{8,}|[A-Za-z0-9_\-]{32,})\b")


class SecurityViolation(RuntimeError):
    """A control refused. Never downgraded to a warning."""


@dataclass(frozen=True)
class UrlPolicy:
    """What a given ingestion run is allowed to reach."""

    allowed_hosts: FrozenSet[str] = frozenset()
    allowed_schemes: FrozenSet[str] = ALLOWED_SCHEMES
    allow_private_addresses: bool = False

    def with_host(self, host: str) -> "UrlPolicy":
        return UrlPolicy(
            allowed_hosts=self.allowed_hosts | {host.lower()},
            allowed_schemes=self.allowed_schemes,
            allow_private_addresses=self.allow_private_addresses,
        )


def _resolve(host: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SecurityViolation(f"cannot resolve host {host!r}: {exc}") from None
    return sorted({info[4][0] for info in infos})


def _is_forbidden_address(address: str) -> Optional[str]:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return f"{address!r} is not an IP address"
    if ip.is_loopback:
        return "loopback"
    # Link-local is checked BEFORE private: `ipaddress` reports 169.254.0.0/16
    # as private too, so the more general answer would win and the message would
    # say "private" for the cloud metadata endpoint. That is the one address an
    # operator most needs named, because seeing it means something tried to read
    # instance credentials.
    if ip.is_link_local:
        return "link-local (includes the cloud metadata endpoint)"
    if ip.is_private:
        return "private (RFC1918 / unique-local)"
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return "reserved, multicast or unspecified"
    return None


def validate_url(url: str, policy: UrlPolicy) -> str:
    """Refuse a URL that should not be fetched. Returns it unchanged if allowed.

    Resolution happens here and the ADDRESS is checked, not the name. A check on
    the hostname alone is defeated by a DNS record pointing at 127.0.0.1.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in policy.allowed_schemes:
        raise SecurityViolation(
            f"scheme {parsed.scheme!r} is not permitted; allowed: "
            f"{sorted(policy.allowed_schemes)}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise SecurityViolation(f"no host in {redact(url)!r}")
    if policy.allowed_hosts and host not in policy.allowed_hosts:
        raise SecurityViolation(
            f"host {host!r} is not on this source's allow-list "
            f"{sorted(policy.allowed_hosts)}")
    if not policy.allow_private_addresses:
        for address in _resolve(host):
            reason = _is_forbidden_address(address)
            if reason:
                raise SecurityViolation(
                    f"host {host!r} resolves to {address} ({reason}). Refusing: "
                    f"a supplied URL must not be able to reach internal "
                    f"addresses.")
    return url


def validate_redirect(from_url: str, to_url: str, policy: UrlPolicy,
                      hop: int) -> str:
    """Re-validate every hop. A permitted host may redirect to a forbidden one."""
    if hop > MAX_REDIRECTS:
        raise SecurityViolation(
            f"more than {MAX_REDIRECTS} redirects starting at {redact(from_url)}")
    return validate_url(to_url, policy)


def enforce_size(byte_count: int, *, limit: int = DEFAULT_MAX_BYTES) -> int:
    if byte_count > limit:
        raise SecurityViolation(
            f"payload is {byte_count:,} bytes, over the {limit:,} byte limit")
    return byte_count


def enforce_content_type(content_type: Optional[str],
                         accepted: Iterable[str]) -> str:
    """Compare the media type only; a charset parameter is not a mismatch."""
    accepted = tuple(accepted)
    if not content_type:
        raise SecurityViolation(
            f"no content type; expected one of {list(accepted)}")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type not in {a.lower() for a in accepted}:
        raise SecurityViolation(
            f"content type {media_type!r} is not one of {list(accepted)}")
    return media_type


def safe_archive_member(name: str, *, destination: str) -> str:
    """Where an archive entry may be written, or refuse it.

    Rejects absolute paths, drive letters, and any path that escapes the
    destination once resolved — which is the only check that catches `a/../../b`
    as well as `../b`.
    """
    if not name or name.endswith("/"):
        raise SecurityViolation(f"archive entry {name!r} has no file name")
    normalised = posixpath.normpath(name.replace("\\", "/"))
    if normalised.startswith("/") or normalised.startswith(".."):
        raise SecurityViolation(
            f"archive entry {name!r} escapes the extraction directory")
    if re.match(r"^[a-zA-Z]:", normalised):
        raise SecurityViolation(f"archive entry {name!r} carries a drive letter")
    target = os.path.realpath(os.path.join(destination, normalised))
    root = os.path.realpath(destination)
    if not (target == root or target.startswith(root + os.sep)):
        raise SecurityViolation(
            f"archive entry {name!r} resolves outside {destination}")
    return target


#: A URL embedded anywhere in a string. Error messages are prose with a URL in
#: the middle far more often than they are a bare URL, and a redactor that only
#: handles the bare case leaks exactly where it matters most.
_EMBEDDED_URL = re.compile(r"https?://[^\s'\"<>]+")


def _redact_one_url(url: str) -> str:
    parsed = urlparse(url)
    result = url
    if parsed.query:
        pairs = []
        for pair in parsed.query.split("&"):
            key, sep, _value = pair.partition("=")
            pairs.append(f"{key}=REDACTED" if _SECRET_KEYS.search(key) else pair)
        result = urlunparse(parsed._replace(query="&".join(pairs)))
    if parsed.password:
        result = result.replace(parsed.password, "REDACTED")
    return result


def redact(text: Optional[str]) -> str:
    """Strip anything that looks like a credential before it is recorded.

    Applied to URLs, error strings and audit detail. Deliberately aggressive: a
    log line that is slightly less useful beats a token in an audit trail, which
    cannot be un-written.
    """
    if not text:
        return ""
    result = _EMBEDDED_URL.sub(lambda m: _redact_one_url(m.group(0)), text)
    return _SECRET_VALUE.sub("REDACTED", result)


def classify_failure(status_code: Optional[int],
                     exception: Optional[BaseException] = None) -> Tuple[bool, str]:
    """Is another attempt worth making?

    Returns (retryable, reason). The distinction matters: retrying a 404 burns
    the maintenance window and hides a source that moved, while giving up on a
    503 discards a delivery over a transient blip.
    """
    if exception is not None and status_code is None:
        name = type(exception).__name__
        transient = ("Timeout", "ConnectError", "ConnectionError", "ReadError",
                     "RemoteProtocolError", "PoolTimeout", "ReadTimeout")
        if any(t in name for t in transient):
            return True, f"transient transport error: {name}"
        return False, f"non-transport error: {name}"
    if status_code is None:
        return False, "no status and no exception"
    if status_code == 429:
        return True, "rate limited"
    if 500 <= status_code < 600:
        return True, f"server error {status_code}"
    if 400 <= status_code < 500:
        return False, f"client error {status_code} — will recur until changed"
    return False, f"unexpected status {status_code}"
