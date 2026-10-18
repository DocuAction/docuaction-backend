"""Prepare a scan-local OpenAPI file for the OWASP ZAP API scan.

Why this exists
---------------
The app's generated ``/openapi.json`` has no ``servers`` entry. ZAP's OpenAPI
importer then falls back to the relative server URL ``/``, cannot build an
absolute URL from a *local file*, logs ``Unable to obtain any server URL from
the definition`` and imports 0 URLs (run 38265533318, and every weekly run since
2026-09-07). The scan then never starts, so no security coverage is obtained.

What it does
------------
Reads the application's original specification (never modified), and writes a
separate scan-local copy whose root ``servers`` holds exactly one explicit,
allowlisted DEV origin. Path-level and operation-level ``servers`` overrides take
precedence over the root in ZAP/OpenAPI, so each is checked too: a relative
override is rewritten onto the allowlisted origin, an absolute override is kept
only if its origin is allowlisted, and anything else fails the run closed.

Production protection is hard-coded and cannot be relaxed by ``--allow-origin``.
A spec with zero operations fails. Nothing here suppresses an error.

Exit codes: 0 prepared; 2 refused (bad target, production, non-allowlisted
override, unsupported spec); 3 zero operations.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from urllib.parse import urlsplit

DEFAULT_ALLOWED_ORIGINS = ("https://docuaction-dev.azurewebsites.net",)

# Mirrors the "Refuse to run against production" step in zap-scan.yml. These
# are checked against every origin, including ones passed via --allow-origin.
PRODUCTION_MARKERS = (
    "api.docuaction.io",
    "api-prod.docuaction.io",
    "docuaction-emffhfgwc0gffgc9",
)

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class SpecRefused(Exception):
    """The target or the specification is not safe or not usable to scan."""


def _origin(url: str) -> str:
    """Return ``scheme://host[:port]`` for an absolute http(s) URL, else raise."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SpecRefused(f"not an absolute http(s) URL: {url!r}")
    if parts.username or parts.password:
        raise SpecRefused("server URL must not contain credentials")
    if parts.query or parts.fragment:
        raise SpecRefused("server URL must not contain a query or fragment")
    host = parts.hostname.lower()
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


def assert_not_production(url: str) -> None:
    """Refuse any URL that names a production host."""
    low = url.lower()
    for marker in PRODUCTION_MARKERS:
        if marker in low:
            raise SpecRefused(f"{url!r} points at production; DAST is dev-only")


def validate_server_url(server_url: str, allowed: tuple[str, ...]) -> str:
    """Return the validated server URL (origin plus optional path), no trailing /."""
    assert_not_production(server_url)
    for a in allowed:
        assert_not_production(a)
    if "{" in server_url or "}" in server_url:
        raise SpecRefused("server URL must not use server variables")
    origin = _origin(server_url)
    if origin not in {_origin(a) for a in allowed}:
        raise SpecRefused(f"{origin} is not in the allowlist {sorted(allowed)}")
    return server_url.rstrip("/")


def _fix_override(entry: dict, base: str, allowed: tuple[str, ...], where: str) -> dict:
    """Return a safe copy of one ``servers`` override entry or raise."""
    url = entry.get("url")
    if not isinstance(url, str) or not url:
        raise SpecRefused(f"{where}: server override has no url")
    if "{" in url or "}" in url:
        raise SpecRefused(f"{where}: server variables are not supported ({url!r})")
    assert_not_production(url)
    fixed = copy.deepcopy(entry)
    if url.startswith("/"):
        # Relative override: anchor it on the allowlisted origin.
        fixed["url"] = _origin(base) + url.rstrip("/")
        return fixed
    if _origin(url) not in {_origin(a) for a in allowed}:
        raise SpecRefused(f"{where}: override origin {_origin(url)} is not allowlisted")
    return fixed


def prepare(spec: dict, server_url: str, allowed: tuple[str, ...]) -> tuple[dict, dict]:
    """Return ``(scan_spec, report)``. ``spec`` is not modified."""
    version = str(spec.get("openapi", ""))
    if not version.startswith("3."):
        raise SpecRefused(f"unsupported specification version {version or '(none)'!r}")
    base = validate_server_url(server_url, allowed)
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise SpecRefused("specification has no paths object")

    out = copy.deepcopy(spec)
    report = {
        "server_url": base,
        "root_servers_before": copy.deepcopy(spec.get("servers")),
        "path_overrides": 0,
        "operation_overrides": 0,
        "operations": 0,
    }
    out["servers"] = [{"url": base}]

    for path, item in out["paths"].items():
        if not isinstance(item, dict):
            continue
        if "servers" in item:
            item["servers"] = [
                _fix_override(e, base, allowed, f"paths[{path}].servers") for e in item["servers"]
            ]
            report["path_overrides"] += 1
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            report["operations"] += 1
            if "servers" in op:
                op["servers"] = [
                    _fix_override(e, base, allowed, f"paths[{path}].{method}.servers")
                    for e in op["servers"]
                ]
                report["operation_overrides"] += 1

    if report["operations"] == 0:
        raise SpecRefused("specification has zero operations; nothing to scan")
    return out, report


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _same_file(input_path: str, output_path: str) -> bool:
    """True when input_path and output_path name the same file via a symlink
    or hard link, even though the strings differ. Only meaningful when both
    already exist; a nonexistent --output is a normal (non-aliasing) write."""
    try:
        return os.path.samefile(input_path, output_path)
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", required=True, help="original spec (read only)")
    ap.add_argument("--output", required=True, help="scan-local spec to write")
    ap.add_argument("--server-url", required=True, help="absolute DEV server URL")
    ap.add_argument(
        "--allow-origin",
        action="append",
        default=None,
        help="allowlisted origin (repeatable); replaces the default DEV allowlist. "
        "Production markers are refused regardless.",
    )
    ap.add_argument("--report", help="write a JSON change report here")
    args = ap.parse_args(argv)
    if args.input == args.output or _same_file(args.input, args.output):
        # CodeRabbit CR152-1: a string comparison alone misses a symlink or
        # hard link aliasing --output onto --input under a different path --
        # the open(..., "w") below would then silently overwrite the original
        # spec the docstring promises is "read only". os.path.samefile() opens
        # both paths and compares (st_dev, st_ino), so it is immune to the
        # string-level alias; a nonexistent --output can't alias anything, so
        # that case is left to the normal write path.
        print("::error::--output must differ from --input; the original is preserved", file=sys.stderr)
        return 2
    allowed = tuple(args.allow_origin) if args.allow_origin else DEFAULT_ALLOWED_ORIGINS

    before = _sha256(args.input)
    try:
        with open(args.input, encoding="utf-8") as fh:
            spec = json.load(fh)
        scan_spec, report = prepare(spec, args.server_url, allowed)
    except SpecRefused as exc:
        msg = str(exc)
        print(f"::error::{msg}", file=sys.stderr)
        return 3 if msg.startswith("specification has zero operations") else 2
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(scan_spec, fh, indent=2)
    report["original_sha256"] = before
    report["scan_spec_sha256"] = _sha256(args.output)
    if _sha256(args.input) != before:  # defensive: the original must be untouched
        print("::error::original specification changed during preparation", file=sys.stderr)
        return 2
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
