#!/usr/bin/env bash
# Runs report_export_jobs_ownership_repair.sql against the DEV database from
# Azure Cloud Shell (Bash), as the server admin login.
#
#   bash run_report_export_jobs_ownership_repair.sh            DRY RUN (rolled back)
#   APPLY=1 bash run_report_export_jobs_ownership_repair.sh    apply and commit
#
# DEV only: subscription, resource group and server are fixed below. The only
# Azure change is ONE temporary firewall rule for this shell's own current IP,
# created by this script and removed by an EXIT trap on success or failure.
# The admin password is read from the keyboard, held in memory for the psql
# call, and never written to disk, echoed, or passed on a command line.
set -euo pipefail
set +x

SUB="6ce81f40-7f0f-4e6d-97e3-2569b4d18611"
RG="rg-docuaction-dev"
SERVER="docuaction-db-dev"
HOST="${SERVER}.postgres.database.azure.com"
DB="postgres"
ADMIN_LOGIN="${ADMIN_LOGIN:-pgadmin}"
APPLY="${APPLY:-0}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL="${HERE}/report_export_jobs_ownership_repair.sql"
RULE="temp-repair-$(date -u +%Y%m%d-%H%M%S)-${RANDOM}"
OUT="${HOME}/report_export_jobs_repair_$([ "$APPLY" = "1" ] && echo apply || echo dryrun)_$(date -u +%Y%m%dT%H%M%SZ).txt"
RULE_ATTEMPTED=""

command -v psql >/dev/null || { echo "STOP: psql is not available."; exit 1; }
command -v az   >/dev/null || { echo "STOP: az is not available."; exit 1; }
[ -f "$SQL" ] || { echo "STOP: ${SQL} not found beside this script."; exit 1; }
case "$APPLY" in 0|1) ;; *) echo "STOP: APPLY must be 0 or 1."; exit 1 ;; esac
echo "Mode:   $([ "$APPLY" = "1" ] && echo 'APPLY (commits if every check passes)' || echo 'DRY RUN (always rolled back)')"
echo "Target: ${HOST} / ${DB} as ${ADMIN_LOGIN}   (subscription $(az account show --subscription "$SUB" --query name -o tsv))"
echo "Script: $(sha256sum "$SQL" | cut -d' ' -f1)  report_export_jobs_ownership_repair.sql"

if az postgres flexible-server firewall-rule create --help 2>/dev/null | grep -q -- '--server-name'; then
  fw() { local verb="$1"; shift; az postgres flexible-server firewall-rule "$verb" \
           --subscription "$SUB" --resource-group "$RG" --server-name "$SERVER" --name "$RULE" "$@"; }
else
  fw() { local verb="$1"; shift; az postgres flexible-server firewall-rule "$verb" \
           --subscription "$SUB" --resource-group "$RG" --name "$SERVER" --rule-name "$RULE" "$@"; }
fi

cleanup() {
  local rc=$?
  set +e
  unset PGPASSWORD
  if [ -n "$RULE_ATTEMPTED" ]; then
    echo; echo "CLEANUP: removing the temporary firewall rule ${RULE} ..."
    fw delete --yes -o none 2>/dev/null
    if fw show -o none 2>/dev/null; then
      echo "CLEANUP FAILED: ${RULE} STILL EXISTS. Remove it now:"
      echo "  az postgres flexible-server firewall-rule delete --subscription ${SUB} -g ${RG} -s ${SERVER} -n ${RULE} --yes"
    else
      echo "CLEANUP OK: ${RULE} is gone."
    fi
  fi
  exit "$rc"
}
trap cleanup EXIT

read -r -s -p "Password for ${ADMIN_LOGIN} (not shown, not stored): " PGPASSWORD; echo
[ -n "$PGPASSWORD" ] || { echo "STOP: empty password."; exit 1; }
export PGPASSWORD

IP="$(curl -fsS --max-time 15 https://api.ipify.org)"
[[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "STOP: could not determine this shell's public IP."; exit 1; }
echo "Opening ${RULE} for this shell only (${IP}/32) ..."
RULE_ATTEMPTED="yes"
fw create --start-ip-address "$IP" --end-ip-address "$IP" -o none
sleep 20

export PGSSLMODE=require PGCONNECT_TIMEOUT=30
set +e
psql -h "$HOST" -p 5432 -U "$ADMIN_LOGIN" -d "$DB" -X -v ON_ERROR_STOP=1 -v apply="$APPLY" -f "$SQL" 2>&1 | tee "$OUT"
RC=${PIPESTATUS[0]}
set -e
echo
if [ "$RC" -ne 0 ]; then
  echo "RESULT: FAILED (psql exit ${RC}). The transaction was rolled back; nothing was changed. Output: ${OUT}"
  exit "$RC"
fi
echo "RESULT: $([ "$APPLY" = "1" ] && echo 'APPLIED' || echo 'DRY RUN PASSED - nothing changed'). Output: ${OUT}"
