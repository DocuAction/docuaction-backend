#!/usr/bin/env python3
"""Verify the live RBAC matrix for every role against a running environment.

This is the check the suite could not perform: it logs in as each real account and
asserts, end to end, that the role its JWT carries gets the access the matrix
promises — in BOTH directions. A deny-only check passes perfectly against a system
that denies everyone, which is precisely how the "TEFCA is admin-only" P0 survived.

Usage
  export DOCUACTION_PW_VIEWER=...   DOCUACTION_PW_REVIEWER=...
  export DOCUACTION_PW_QALEAD=...   DOCUACTION_PW_ADMIN=...
  python scripts/verify_rbac_matrix.py --base-url https://docuaction-dev.azurewebsites.net

Any account whose password is not supplied is SKIPPED and reported as skipped —
never silently treated as passing.

Exit code 0 only if every executed expectation held.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://docuaction-dev.azurewebsites.net"

ACCOUNTS = [
    ("viewer",   "viewer@docuaction.io",   "DOCUACTION_PW_VIEWER"),
    ("reviewer", "reviewer@docuaction.io", "DOCUACTION_PW_REVIEWER"),
    ("qalead",   "qalead@docuaction.io",   "DOCUACTION_PW_QALEAD"),
    ("admin",    "admin@docuaction.io",    "DOCUACTION_PW_ADMIN"),
]

READS = [
    "/api/tefca/dashboard/summary",
    "/api/tefca/dashboard/trends",
    "/api/tefca/reports",
    "/api/tefca/reviews",
    "/api/tefca/findings",
    "/api/tefca/registry/entities",
    "/api/v1/tefca/cycles",
    "/api/v1/tefca/reports",
    "/api/v1/bulletin/latest/fcc",
]

# (method, path, minimum role that may reach it)
WRITES = [
    ("POST", "/api/tefca/registry/import/csv", "contributor"),
    ("POST", "/api/tefca/reports/weekly", "qalead"),
    ("GET", "/api/admin/users", "admin"),
]

LEVEL = {"viewer": 1, "contributor": 2, "manager": 3, "reviewer": 4,
         "senior_analyst": 5, "qalead": 6, "program_manager": 7, "admin": 8}


def call(method, url, token=None, timeout=60):
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # network / DNS / TLS
        return 0, str(e).encode()


def login(base, email, password):
    data = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(base + "/api/auth/login", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, "%s %s" % (e.code, e.read().decode("utf-8", "replace")[:200])
    except Exception as e:
        return None, str(e)


def jwt_role(token):
    """Read the role claim without verifying — we are checking what the SERVER put
    in the token, so an unverified decode is the right tool and the only one
    available client-side."""
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("role")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("DOCUACTION_BASE_URL", DEFAULT_BASE))
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    print("Environment: %s\n" % base)
    status, _ = call("GET", base + "/health")
    print("  /health -> %s\n" % status)

    failures, skipped = [], []

    for role, email, env in ACCOUNTS:
        pw = os.environ.get(env, "")
        if not pw:
            skipped.append("%s (%s not set)" % (role, env))
            print("SKIP  %-9s %s — %s not set" % (role, email, env))
            continue

        body, err = login(base, email, pw)
        if err:
            failures.append("%s: login failed (%s)" % (role, err))
            print("FAIL  %-9s login failed: %s" % (role, err))
            continue

        token = body.get("access_token") or body.get("token")
        actual = jwt_role(token)
        mark = "ok" if actual == role else "MISMATCH"
        print("\n%-9s login ok — JWT role=%r [%s]" % (role, actual, mark))
        if actual != role:
            failures.append("%s: JWT carries role %r, not %r" % (role, actual, role))
        print("          allowed_modules=%s" % (body.get("user") or {}).get("allowed_modules"))

        lvl = LEVEL.get(actual or role, 0)

        for path in READS:
            code, _ = call("GET", base + path, token)
            bad = code == 403
            print("    %-4s GET  %-42s %s" % ("FAIL" if bad else "ok", path, code))
            if bad:
                failures.append("%s: denied read %s (403)" % (role, path))

        for method, path, floor in WRITES:
            code, _ = call(method, base + path, token)
            should_reach = lvl >= LEVEL[floor]
            bad = (code == 403) if should_reach else (code != 403)
            print("    %-4s %-4s %-42s %s   (floor %s, expect %s)"
                  % ("FAIL" if bad else "ok", method, path, code, floor,
                     "reach" if should_reach else "403"))
            if bad:
                failures.append("%s: %s %s returned %s (floor %s)" % (role, method, path, code, floor))

    print("\n" + "=" * 70)
    if skipped:
        print("SKIPPED: %s" % ", ".join(skipped))
    if failures:
        print("FAILURES (%d):" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    if skipped and len(skipped) == len(ACCOUNTS):
        print("NOTHING VERIFIED — no credentials supplied.")
        return 1
    print("All executed expectations held.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
