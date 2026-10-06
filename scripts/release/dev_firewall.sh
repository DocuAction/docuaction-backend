#!/usr/bin/env bash
# DEV database firewall window for ONE release job: open -> (work) -> close.
#
#   dev_firewall.sh open    create a /32 rule for THIS runner's public IPv4 and wait until the
#                           database accepts a TCP connection from it
#   dev_firewall.sh close   delete that rule and prove it is gone (retries; fails loudly)
#   dev_firewall.sh sweep   delete rules left by EARLIER, COMPLETED runs of this repository
#
# Design limits, all enforced here rather than trusted to the caller:
#   * The rule is always start = end = the runner's own IPv4. There is no argument that can
#     widen it, and a private/reserved/zero address is refused.
#   * Only rules named exactly temp-run-<digits> or temp-run-<digits>-gv can be created,
#     deleted or swept. Standing rules (devapp-*) and manually named rules are never touched.
#   * Uses ARM REST via `az rest` against ONE server, so the identity needs only
#     Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/{read,write,delete} at that
#     server's scope. (`az postgres flexible-server firewall-rule` polls a long-running
#     operation through subscription-level "locations/..." actions, which would force a wider
#     assignment; plain REST does not.)
set -euo pipefail

MODE="${1:?usage: dev_firewall.sh open|close|sweep}"
SUB="${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID is required}"
RG="${DEV_RESOURCE_GROUP:-rg-docuaction-dev}"
SERVER="${DEV_DB_SERVER:-docuaction-db-dev}"
PGHOST="${PGHOST:-${SERVER}.postgres.database.azure.com}"
PGPORT="${PGPORT:-5432}"
RULE="${FIREWALL_RULE_NAME:?FIREWALL_RULE_NAME is required}"
API="${ARM_API_VERSION:-2022-12-01}"
ARM="${ARM_ENDPOINT:-https://management.azure.com}"
PY="${PYTHON:-python3}"
REACH_ATTEMPTS="${REACH_ATTEMPTS:-40}"; REACH_SLEEP="${REACH_SLEEP:-10}"
GONE_ATTEMPTS="${GONE_ATTEMPTS:-12}";   GONE_SLEEP="${GONE_SLEEP:-10}"

BASE="${ARM}/subscriptions/${SUB}/resourceGroups/${RG}/providers/Microsoft.DBforPostgreSQL/flexibleServers/${SERVER}/firewallRules"
NAME_RE='^temp-run-[0-9]+(-gv)?$'

[[ "$RULE" =~ $NAME_RE ]] || { echo "::error::refusing rule name '$RULE': only temp-run-<run id>[-gv] is managed here"; exit 2; }

rule_url() { echo "${BASE}/$1?api-version=${API}"; }

# GET one rule. Prints its JSON; returns 0 found, 44 not found, 1 any other error.
get_rule() {
  local out err rc
  err="$(mktemp)"
  if out="$(az rest --method get --url "$(rule_url "$1")" -o json 2>"$err")"; then
    rm -f "$err"; echo "$out"; return 0
  fi
  rc=1
  if grep -qiE 'ResourceNotFound|NotFound|\(404\)|404' "$err"; then rc=44; fi
  cat "$err" >&2; rm -f "$err"; return "$rc"
}

delete_rule() { az rest --method delete --url "$(rule_url "$1")" -o none; }

public_ipv4() {
  local ip="" i
  for i in 1 2 3; do
    ip="$(curl -fsS --max-time 15 https://api.ipify.org 2>/dev/null || true)"
    [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] && break
    ip=""; sleep 3
  done
  echo "$ip"
}

valid_public_ipv4() {
  local ip="$1" a b c d
  [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  a=${BASH_REMATCH[1]}; b=${BASH_REMATCH[2]}; c=${BASH_REMATCH[3]}; d=${BASH_REMATCH[4]}
  for o in $a $b $c $d; do [ "$((10#$o))" -le 255 ] || return 1; done
  a=$((10#$a)); b=$((10#$b))
  [ "$a" -eq 0 ] && return 1
  [ "$a" -eq 10 ] && return 1
  [ "$a" -eq 127 ] && return 1
  [ "$a" -ge 224 ] && return 1
  [ "$a" -eq 169 ] && [ "$b" -eq 254 ] && return 1
  [ "$a" -eq 172 ] && [ "$b" -ge 16 ] && [ "$b" -le 31 ] && return 1
  [ "$a" -eq 192 ] && [ "$b" -eq 168 ] && return 1
  [ "$a" -eq 100 ] && [ "$b" -ge 64 ] && [ "$b" -le 127 ] && return 1
  return 0
}

json_field() { "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("properties",{}).get(sys.argv[1],""))' "$1"; }

case "$MODE" in
open)
  IP="$(public_ipv4)"
  valid_public_ipv4 "$IP" || { echo "::error::could not determine a valid public IPv4 for this runner (got '${IP}') - no rule created"; exit 1; }
  echo "runner public IPv4: $IP"
  BODY="$("$PY" -c 'import json,sys; print(json.dumps({"properties":{"startIpAddress":sys.argv[1],"endIpAddress":sys.argv[1]}}))' "$IP")"
  az rest --method put --url "$(rule_url "$RULE")" --body "$BODY" -o none
  GOT="$(get_rule "$RULE")" || { echo "::error::rule $RULE was not readable after creation"; exit 1; }
  S="$(echo "$GOT" | json_field startIpAddress)"; E="$(echo "$GOT" | json_field endIpAddress)"
  if [ "$S" != "$IP" ] || [ "$E" != "$IP" ]; then
    echo "::error::rule $RULE reads back as $S-$E, not exactly $IP - removing it and stopping"
    delete_rule "$RULE" || true
    exit 1
  fi
  [ -n "${GITHUB_OUTPUT:-}" ] && echo "ip=$IP" >> "$GITHUB_OUTPUT"
  echo "rule $RULE = $IP/32; waiting for the database to accept this runner (bounded)"
  for i in $(seq 1 "$REACH_ATTEMPTS"); do
    if "$PY" -c "import socket;s=socket.socket();s.settimeout(5);s.connect(('$PGHOST',$PGPORT))" 2>/dev/null; then
      echo "DEV Postgres reachable after $i attempt(s)"; exit 0
    fi
    sleep "$REACH_SLEEP"
  done
  echo "::error::DEV Postgres not reachable within the bounded wait after creating $RULE - failing closed; the close step will remove the rule"
  exit 1
  ;;
close)
  # Idempotent: DELETE of a rule that was never created is not an error.
  delete_rule "$RULE" || echo "delete request for $RULE returned an error; verifying state below"
  for i in $(seq 1 "$GONE_ATTEMPTS"); do
    set +e; get_rule "$RULE" >/dev/null 2>&1; rc=$?; set -e
    if [ "$rc" -eq 44 ]; then echo "CLEANUP OK: $RULE is gone"; exit 0; fi
    # still present, or state unreadable: ask again for the delete, then re-check
    [ "$i" -lt "$GONE_ATTEMPTS" ] && { delete_rule "$RULE" 2>/dev/null || true; sleep "$GONE_SLEEP"; }
  done
  echo "::error::CLEANUP FAILED: could not confirm that $RULE is gone. Remove it now:"
  echo "::error::az rest --method delete --url '$(rule_url "$RULE")'"
  exit 1
  ;;
sweep)
  # Best effort, and only ever for rules this automation names itself.
  CUR_ID="${GITHUB_RUN_ID:-0}"
  LIST="$(az rest --method get --url "${BASE}?api-version=${API}" -o json 2>/dev/null)" || { echo "sweep: could not list rules (skipped)"; exit 0; }
  echo "$LIST" | "$PY" -c 'import json,sys; [print(r["name"]) for r in json.load(sys.stdin).get("value",[])]' | tr -d '\r' | while read -r NAME; do
    [[ "$NAME" =~ $NAME_RE ]] || continue
    RID="${NAME#temp-run-}"; RID="${RID%-gv}"
    [ "$RID" = "$CUR_ID" ] && continue
    ST="$(gh run view "$RID" --repo "${GITHUB_REPOSITORY:?}" --json status -q .status 2>/dev/null || true)"
    if [ "$ST" = "completed" ]; then
      echo "sweep: $NAME belongs to completed run $RID - deleting"
      delete_rule "$NAME" || echo "sweep: could not delete $NAME (left for the next run)"
    else
      echo "sweep: leaving $NAME (run $RID status '${ST:-unknown}')"
    fi
  done
  ;;
*) echo "unknown mode '$MODE'"; exit 2 ;;
esac
