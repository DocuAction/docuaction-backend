"""Read-only presence check for the Entra SSO configuration.

Prints, for each AZURE_AD_* setting, ONLY one of SET / UNSET plus a length
bucket. It NEVER prints a value, a prefix, a suffix or a hash.

Operator usage (run against the app's environment export, not against Azure):

    # a KEY=VALUE export (for example a saved `az webapp config appsettings
    # list` converted to .env, or a container env dump):
    python scripts/sso_config_presence_check.py --env-file path/to/exported.env

    # or the current process environment:
    python scripts/sso_config_presence_check.py

Also reports whether AZURE_AD_ID_TOKEN_VALIDATION is 'enforce' (a mode name is a
fixed vocabulary, not a secret, so only that one value class is named).
"""
from __future__ import annotations

import argparse
import os
import sys

NAMES = [
    "AZURE_AD_CLIENT_ID",
    "AZURE_AD_CLIENT_SECRET",
    "AZURE_AD_TENANT_ID",
    "AZURE_AD_DEFAULT_ROLE",
    "AZURE_AD_POST_LOGIN_REDIRECT",
    "AZURE_AD_ID_TOKEN_VALIDATION",
]
REQUIRED = ["AZURE_AD_CLIENT_ID", "AZURE_AD_CLIENT_SECRET", "AZURE_AD_TENANT_ID"]
MODES = {"audit", "enforce"}


def length_bucket(value: str) -> str:
    """Coarse length class; never the exact length."""
    n = len(value)
    if n == 0:
        return "empty"
    if n <= 8:
        return "1-8"
    if n <= 16:
        return "9-16"
    if n <= 40:
        return "17-40"
    if n <= 80:
        return "41-80"
    return ">80"


def read_env_file(path: str) -> dict:
    """Parse KEY=VALUE lines (comments and blanks ignored). Values stay in memory only."""
    env: dict = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip().removeprefix("export ").strip()] = val.strip().strip('"').strip("'")
    return env


def classify(env: dict) -> list:
    """Return (name, 'SET'|'UNSET', bucket) rows; no values."""
    rows = []
    for name in NAMES:
        value = (env.get(name) or "").strip()
        rows.append((name, "SET" if value else "UNSET", length_bucket(value) if value else "-"))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", help="KEY=VALUE export to inspect (default: process environment)")
    args = ap.parse_args(argv)
    env = read_env_file(args.env_file) if args.env_file else dict(os.environ)

    print(f"{'NAME':32} {'STATE':6} LENGTH")
    for name, state, bucket in classify(env):
        print(f"{name:32} {state:6} {bucket}")
    missing = [n for n in REQUIRED if not (env.get(n) or "").strip()]
    print()
    print("SSO enabled by code (all three required settings SET): "
          + ("YES" if not missing else "NO - UNSET: " + ", ".join(missing)))
    mode = (env.get("AZURE_AD_ID_TOKEN_VALIDATION") or "").strip().lower()
    if mode in MODES:
        print(f"ID-token validation mode: {mode}")
    elif mode:
        print("ID-token validation mode: unrecognised (treated as audit)")
    else:
        print("ID-token validation mode: UNSET (code default audit; absent on code without this setting)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
