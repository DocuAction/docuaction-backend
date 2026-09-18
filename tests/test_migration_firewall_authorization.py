"""Unit tests for .github/scripts/migration_firewall_authorization.py - the
extracted validation/naming logic behind migration-preflight.yml's automated
temporary firewall authorization (2026-09-18, replacing the manual
runner-IP handshake).

These test the exact functions the workflow calls, not a reimplementation -
importing the script directly rather than duplicating its regex.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github" / "scripts" / "migration_firewall_authorization.py"
)
_spec = importlib.util.spec_from_file_location("migration_firewall_authorization", _SCRIPT_PATH)
mfa = importlib.util.module_from_spec(_spec)
sys.modules["migration_firewall_authorization"] = mfa
_spec.loader.exec_module(mfa)


# ── IP validation ────────────────────────────────────────────────────────────

def test_valid_ipv4_is_accepted():
    assert mfa.validate_runner_ip("20.109.239.211") == "20.109.239.211"
    assert mfa.validate_runner_ip("1.2.3.4") == "1.2.3.4"
    assert mfa.validate_runner_ip("255.255.255.255") == "255.255.255.255"


def test_empty_value_is_rejected():
    with pytest.raises(mfa.InvalidRunnerIP, match="empty"):
        mfa.validate_runner_ip("")
    with pytest.raises(mfa.InvalidRunnerIP, match="empty"):
        mfa.validate_runner_ip("   ")
    with pytest.raises(mfa.InvalidRunnerIP):
        mfa.validate_runner_ip(None)


def test_ipv6_is_rejected():
    with pytest.raises(mfa.InvalidRunnerIP, match="IPv6"):
        mfa.validate_runner_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334")
    with pytest.raises(mfa.InvalidRunnerIP, match="IPv6"):
        mfa.validate_runner_ip("::1")


def test_zero_zero_zero_zero_is_rejected():
    with pytest.raises(mfa.InvalidRunnerIP, match="0.0.0.0"):
        mfa.validate_runner_ip("0.0.0.0")


def test_ip_range_is_rejected():
    with pytest.raises(mfa.InvalidRunnerIP, match="range"):
        mfa.validate_runner_ip("20.109.239.0/24")
    with pytest.raises(mfa.InvalidRunnerIP, match="range|single"):
        mfa.validate_runner_ip("20.109.239.1-20.109.239.10")
    with pytest.raises(mfa.InvalidRunnerIP):
        mfa.validate_runner_ip("20.109.239.1,20.109.239.2")


def test_malformed_values_are_rejected():
    with pytest.raises(mfa.InvalidRunnerIP):
        mfa.validate_runner_ip("not-an-ip")
    with pytest.raises(mfa.InvalidRunnerIP):
        mfa.validate_runner_ip("999.1.1.1")            # out-of-range octet
    with pytest.raises(mfa.InvalidRunnerIP):
        mfa.validate_runner_ip("1.2.3")                # too few octets
    with pytest.raises(mfa.InvalidRunnerIP):
        mfa.validate_runner_ip("1.2.3.4.5")             # too many octets
    with pytest.raises(mfa.InvalidRunnerIP):
        mfa.validate_runner_ip("<html>error</html>")    # a misbehaving IP-echo service


# ── rule naming ───────────────────────────────────────────────────────────────

def test_unique_rule_name_generated():
    name_a = mfa.generate_rule_name("35362478212", "1", "development")
    name_b = mfa.generate_rule_name("35362478212", "2", "development")  # re-run, same run_id
    name_c = mfa.generate_rule_name("99999999999", "1", "development")  # different run
    assert name_a != name_b
    assert name_a != name_c
    assert "35362478212" in name_a
    assert "1" in name_a.split("-")
    assert "development" in name_a


def test_rule_name_rejects_unsafe_characters():
    with pytest.raises(ValueError):
        mfa.generate_rule_name("123; rm -rf /", "1", "development")
    with pytest.raises(ValueError):
        mfa.generate_rule_name("123", "1", "prod uction")  # space not allowed


# ── CLI entry point (what the workflow actually invokes) ────────────────────

def test_cli_validate_ip_success():
    import subprocess
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "validate-ip", "20.109.239.211"],
        capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == "20.109.239.211"


def test_cli_validate_ip_rejects_ipv6():
    import subprocess
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "validate-ip", "::1"],
        capture_output=True, text=True)
    assert result.returncode == 1
    assert "IPv6" in result.stderr


def test_cli_rule_name():
    import subprocess
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "rule-name", "123", "1", "development"],
        capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == "gh-123-1-development"
