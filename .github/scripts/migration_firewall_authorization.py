"""Validation and naming logic for the automated migration firewall
authorization (migration-preflight.yml, 2026-09-18).

Extracted out of the workflow's inline bash so it is independently testable
(see tests/test_migration_firewall_authorization.py) rather than only
exercisable by actually running the workflow. The workflow calls this module
as a script; nothing here talks to Azure or Postgres - it is pure
validation and string logic.
"""

from __future__ import annotations

import re
import sys

#: Four dot-separated 1-3 digit groups, nothing else on the line. Rejects
#: IPv6 (no colons match), a CIDR range (the trailing /NN has no match for
#: the whole-string anchors), a second address (space/comma has no match),
#: and any surrounding text from a misbehaving IP-echo service.
_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


class InvalidRunnerIP(ValueError):
    """The detected runner IP is not a single, valid, non-zero IPv4 address."""


def validate_runner_ip(raw: str) -> str:
    """Return `raw` unchanged if it is exactly one valid, non-zero IPv4
    address. Raise InvalidRunnerIP otherwise - never silently coerces or
    truncates a bad value into something that looks usable.
    """
    if raw is None or raw.strip() == "":
        raise InvalidRunnerIP("runner IP is empty - refusing to authorize an empty/wildcard rule")

    value = raw.strip()

    if ":" in value:
        raise InvalidRunnerIP(f"{value!r} looks like an IPv6 address - only IPv4 is accepted")

    if "/" in value:
        raise InvalidRunnerIP(f"{value!r} is a CIDR range, not a single address - refusing to authorize a range")

    if "-" in value or "," in value or " " in value:
        raise InvalidRunnerIP(f"{value!r} is not a single bare address - refusing a range or multi-address value")

    m = _IPV4_RE.match(value)
    if not m:
        raise InvalidRunnerIP(f"{value!r} is not a well-formed IPv4 address")

    octets = [int(g) for g in m.groups()]
    for octet in octets:
        if not (0 <= octet <= 255):
            raise InvalidRunnerIP(f"{value!r} has an out-of-range octet ({octet})")

    if value == "0.0.0.0":
        raise InvalidRunnerIP("runner IP resolved to 0.0.0.0 - refusing to authorize this "
                              "(this would be indistinguishable from an 'allow all' rule)")

    return value


def generate_rule_name(run_id: str, run_attempt: str, environment: str) -> str:
    """`gh-<run_id>-<run_attempt>-<environment>` - unique per run AND per
    attempt (a re-run of the same run_id gets its own rule name), and
    identifies which GitHub Environment authorized it. Postgres flexible
    server firewall rule names allow letters, numbers, hyphens and
    underscores only; every input here is already restricted to that set
    (GitHub run ids/attempts are numeric, environment names are
    operator-chosen identifiers), but reject anything else rather than
    silently stripping it - a rule name that silently changed shape would be
    harder to find in an audit than a rejected input.
    """
    name = f"gh-{run_id}-{run_attempt}-{environment}"
    if not re.match(r"^[A-Za-z0-9_-]+$", name):
        raise ValueError(f"generated rule name {name!r} contains characters Postgres firewall "
                         f"rule names do not allow (letters, digits, hyphen, underscore only)")
    return name


if __name__ == "__main__":
    # CLI entry point for the workflow: `python migration_firewall_authorization.py
    # validate-ip <ip>` or `... rule-name <run_id> <run_attempt> <environment>`.
    # Prints the validated/generated value on success (exit 0) or an error
    # to stderr (exit 1) - the workflow captures stdout into GITHUB_OUTPUT.
    if len(sys.argv) < 2:
        print("usage: migration_firewall_authorization.py validate-ip <ip> "
              "| rule-name <run_id> <run_attempt> <environment>", file=sys.stderr)
        sys.exit(2)

    command = sys.argv[1]
    try:
        if command == "validate-ip":
            print(validate_runner_ip(sys.argv[2] if len(sys.argv) > 2 else ""))
        elif command == "rule-name":
            print(generate_rule_name(sys.argv[2], sys.argv[3], sys.argv[4]))
        else:
            print(f"unknown command {command!r}", file=sys.stderr)
            sys.exit(2)
    except (InvalidRunnerIP, ValueError, IndexError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        sys.exit(1)
