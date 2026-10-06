"""scripts/release/dev_firewall.sh -- the temporary DEV database firewall window.

The script is run for real, under bash, with stub `az`, `curl` and `gh` on PATH that behave like the
Azure management API (rules are files in a state directory). Nothing here reaches Azure or the network.

What is proved:
  * the rule it creates is exactly the runner's own IPv4, start == end, and nothing else
  * private / reserved / missing addresses are refused before any rule is created
  * a rule that reads back wider than requested is removed and the step fails
  * an unreachable database fails closed, and close then removes the rule
  * close proves the rule is gone, is idempotent, and fails LOUDLY when it cannot confirm removal
  * only temp-run-<run id>[-gv] names can be created, deleted or swept; standing (devapp-*) and
    manually named rules are never touched
  * sweep removes rules of COMPLETED earlier runs only
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "release", "dev_firewall.sh")
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the release scripts")

AZ_STUB = r'''#!/usr/bin/env bash
STATE="${STUB_STATE:?}"; mkdir -p "$STATE/rules"
echo "az $*" >> "$STATE/calls.log"
[ "$1" = "rest" ] || { echo "unexpected az invocation: $*" >&2; exit 9; }
shift
METHOD=""; URL=""; BODY=""
while [ $# -gt 0 ]; do
  case "$1" in --method) METHOD="$2"; shift 2;; --url) URL="$2"; shift 2;; --body) BODY="$2"; shift 2;; *) shift;; esac
done
NAME="$(echo "$URL" | sed -n 's#.*firewallRules/\([^?]*\).*#\1#p')"
case "$METHOD" in
  put)
    if [ -n "${STUB_WIDEN:-}" ]; then BODY='{"properties":{"startIpAddress":"0.0.0.0","endIpAddress":"255.255.255.255"}}'; fi
    echo "$BODY" > "$STATE/rules/$NAME"; echo '{}' ;;
  get)
    if [ -z "$NAME" ]; then
      printf '{"value":['; first=1
      for f in "$STATE/rules"/*; do
        [ -e "$f" ] || continue
        [ "$first" = 1 ] || printf ','
        printf '{"name":"%s"}' "$(basename "$f")"; first=0
      done
      printf ']}'
    elif [ -e "$STATE/rules/$NAME" ]; then
      cat "$STATE/rules/$NAME"
    else
      echo "(ResourceNotFound) The Resource '$NAME' under resource group was not found." >&2; exit 1
    fi ;;
  delete)
    if [ -n "${STUB_DELETE_ERROR:-}" ]; then echo "(InternalServerError) boom" >&2; exit 1; fi
    if [ -z "${STUB_DELETE_NOOP:-}" ]; then rm -f "$STATE/rules/$NAME"; fi ;;
  *) echo "unexpected method $METHOD" >&2; exit 9 ;;
esac
'''

CURL_STUB = r'''#!/usr/bin/env bash
if [ -z "${STUB_IP:-}" ]; then exit 22; fi
echo "$STUB_IP"
'''

GH_STUB = r'''#!/usr/bin/env bash
# gh run view <id> --repo <r> --json status -q .status
ID="$3"
if [ -e "${STUB_STATE:?}/runs/$ID" ]; then cat "$STUB_STATE/runs/$ID"; else exit 1; fi
'''


@pytest.fixture()
def env(tmp_path):
    bindir = tmp_path / "bin"
    state = tmp_path / "state"
    (state / "rules").mkdir(parents=True)
    (state / "runs").mkdir()
    bindir.mkdir()
    for name, body in (("az", AZ_STUB), ("curl", CURL_STUB), ("gh", GH_STUB)):
        p = bindir / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)

    # A reachable "database": a local TCP listener.
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(8)
    stop = threading.Event()

    def accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                c, _ = srv.accept(); c.close()
            except Exception:
                pass
    t = threading.Thread(target=accept, daemon=True); t.start()

    e = dict(os.environ)
    e.update({
        "PATH": str(bindir) + os.pathsep + e["PATH"],
        "STUB_STATE": str(state), "STUB_IP": "20.30.40.50",
        "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
        "FIREWALL_RULE_NAME": "temp-run-4242",
        "GITHUB_RUN_ID": "4242", "GITHUB_REPOSITORY": "DocuAction/docuaction-backend",
        "PGHOST": "127.0.0.1", "PGPORT": str(srv.getsockname()[1]),
        "PYTHON": sys.executable,
        "REACH_ATTEMPTS": "3", "REACH_SLEEP": "0", "GONE_ATTEMPTS": "3", "GONE_SLEEP": "0",
    })
    e.pop("GITHUB_OUTPUT", None)
    yield {"env": e, "state": state, "tmp": tmp_path}
    stop.set(); srv.close()


def run(ctx, mode, **override):
    e = dict(ctx["env"]); e.update(override)
    r = subprocess.run([BASH, SCRIPT, mode], env=e, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


def rules(ctx):
    return sorted(p.name for p in (ctx["state"] / "rules").iterdir())


def rule(ctx, name):
    return json.loads((ctx["state"] / "rules" / name).read_text())["properties"]


def seed(ctx, name, start="1.1.1.1", end="1.1.1.1"):
    (ctx["state"] / "rules" / name).write_text(
        json.dumps({"properties": {"startIpAddress": start, "endIpAddress": end}}))


def test_open_creates_exactly_the_runner_ip_and_nothing_else(env):
    seed(env, "devapp-out-1", "9.9.9.9", "9.9.9.9")
    rc, out = run(env, "open")
    assert rc == 0, out
    assert rules(env) == ["devapp-out-1", "temp-run-4242"]
    assert rule(env, "temp-run-4242") == {"startIpAddress": "20.30.40.50", "endIpAddress": "20.30.40.50"}
    assert rule(env, "devapp-out-1")["startIpAddress"] == "9.9.9.9", "a standing rule must not be touched"
    assert "reachable after 1 attempt" in out


@pytest.mark.parametrize("bad", ["", "10.1.2.3", "192.168.0.7", "172.20.1.1", "127.0.0.1", "0.0.0.0",
                                 "169.254.1.1", "100.64.0.9", "224.0.0.1", "300.1.1.1", "1.2.3", "not-an-ip"])
def test_open_refuses_missing_private_or_malformed_addresses(env, bad):
    rc, out = run(env, "open", STUB_IP=bad)
    assert rc != 0
    assert rules(env) == [], f"no rule may be created for {bad!r}: {out}"


def test_open_removes_a_rule_that_reads_back_wider_than_requested(env):
    rc, out = run(env, "open", STUB_WIDEN="1")
    assert rc != 0 and "not exactly 20.30.40.50" in out
    assert rules(env) == [], "a widened rule must be deleted immediately"


def test_open_fails_closed_when_the_database_stays_unreachable_and_close_removes_the_rule(env):
    dead = socket.socket(); dead.bind(("127.0.0.1", 0)); port = dead.getsockname()[1]; dead.close()
    rc, out = run(env, "open", PGPORT=str(port), REACH_ATTEMPTS="2")
    assert rc != 0 and "not reachable" in out
    assert rules(env) == ["temp-run-4242"], "the rule exists until close runs"
    rc, out = run(env, "close")
    assert rc == 0 and "CLEANUP OK" in out
    assert rules(env) == []


def test_close_is_idempotent_when_no_rule_was_ever_created(env):
    rc, out = run(env, "close")
    assert rc == 0 and "CLEANUP OK" in out


def test_close_retries_a_failing_delete_then_succeeds_or_fails_loudly(env):
    seed(env, "temp-run-4242", "20.30.40.50", "20.30.40.50")
    # delete returns success but the rule stays: removal can never be confirmed
    rc, out = run(env, "close", STUB_DELETE_NOOP="1")
    assert rc != 0 and "CLEANUP FAILED" in out and "az rest --method delete" in out
    assert rules(env) == ["temp-run-4242"]
    # delete errors outright: still a loud failure, never a silent pass
    rc, out = run(env, "close", STUB_DELETE_ERROR="1")
    assert rc != 0 and "CLEANUP FAILED" in out


@pytest.mark.parametrize("name", ["devapp-out-1", "temp-run-abc", "temp-run-", "temp-diag-37350464323",
                                  "temp-run-12; rm -rf", "TEMP-RUN-12", "temp-run-12-x"])
def test_only_the_managed_name_pattern_can_be_created_or_deleted(env, name):
    seed(env, "devapp-out-1", "9.9.9.9", "9.9.9.9")
    for mode in ("open", "close"):
        rc, out = run(env, mode, FIREWALL_RULE_NAME=name)
        assert rc == 2 and "refusing rule name" in out
    assert rules(env) == ["devapp-out-1"]


def test_the_verify_job_rule_name_is_accepted(env):
    rc, out = run(env, "open", FIREWALL_RULE_NAME="temp-run-4242-gv")
    assert rc == 0, out
    assert rules(env) == ["temp-run-4242-gv"]


def test_sweep_deletes_only_rules_of_completed_earlier_runs(env):
    for name in ("devapp-out-1", "devapp-possible-2", "temp-run-4242", "temp-run-100", "temp-run-200-gv",
                 "temp-run-300", "temp-run-400", "manual-rule", "temp-diag-37350464323"):
        seed(env, name)
    (env["state"] / "runs" / "100").write_text("completed")
    (env["state"] / "runs" / "200").write_text("completed")
    (env["state"] / "runs" / "300").write_text("in_progress")
    (env["state"] / "runs" / "4242").write_text("completed")      # the CURRENT run: must be kept
    # run 400 is unknown to gh: left alone
    rc, out = run(env, "sweep")
    assert rc == 0, out
    assert rules(env) == ["devapp-out-1", "devapp-possible-2", "manual-rule", "temp-diag-37350464323",
                          "temp-run-300", "temp-run-400", "temp-run-4242"]


def test_sweep_never_fails_the_release_when_azure_cannot_be_listed(env):
    (env["tmp"] / "bin" / "az").write_text("#!/usr/bin/env bash\nexit 1\n", newline="\n")
    rc, out = run(env, "sweep")
    assert rc == 0 and "skipped" in out
