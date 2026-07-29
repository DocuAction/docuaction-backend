#!/bin/bash
# Migrates DATABASE_URL from plaintext app setting to Key Vault reference
# Pre-requisites:
#   - Access to docuaction-kv-prod private network
#   - az cli authenticated with sufficient permissions
#
# This script PRINTS the commands rather than running them. The migration touches
# the production database credential; it is executed deliberately, one step at a
# time, with verification between each.

set -euo pipefail

VAULT="docuaction-kv-prod"
PROD_APP="Docuaction"
PROD_RG="rg-docuaction-prod"
DEV_APP="docuaction-dev"
DEV_RG="rg-docuaction-dev"
DEV_VAULT="docuaction-kv-dev"

echo "=== Step 1: Read current DATABASE_URL from app settings ==="
echo "Run this from a network that can reach the Key Vault private endpoint"
echo ""
echo "  az webapp config appsettings list --name $PROD_APP --resource-group $PROD_RG \\"
echo "    --query \"[?name=='DATABASE_URL'].value\" -o tsv > ./dburl.txt"
echo ""
echo "  Redirect to a file rather than to the terminal. The value contains the"
echo "  production database password; terminal scrollback is a copy of it."
echo ""
echo "=== Step 2: Store in Key Vault ==="
echo "  az keyvault secret set --vault-name $VAULT --name DATABASE-URL --file ./dburl.txt"
echo "  shred -u ./dburl.txt   # or Remove-Item on Windows"
echo ""
echo "  Use --file, not --value. An inline value lands in shell history, and a"
echo "  connection string containing parentheses breaks az argument parsing"
echo "  on Windows."
echo ""
echo "=== Step 3: Update app setting to KV reference ==="
echo "  az webapp config appsettings set --name $PROD_APP --resource-group $PROD_RG \\"
echo "    --settings DATABASE_URL=\"@Microsoft.KeyVault(VaultName=$VAULT;SecretName=DATABASE-URL)\""
echo ""
echo "=== Step 4: Restart and verify ==="
echo "  az webapp restart --name $PROD_APP --resource-group $PROD_RG"
echo "  sleep 60"
echo "  curl -s https://api-prod.docuaction.io/health"
echo ""
echo "  VERIFY THE APP SERVES, not just that it started. An unresolved reference"
echo "  is handed to the app as the literal Microsoft.KeyVault string - about 71"
echo "  characters, long enough to pass a naive length check. The app would boot"
echo "  and then fail to reach the database."
echo ""
echo "  Confirm the reference resolved:"
echo "    ./scripts/verify-keyvault-references.sh $PROD_APP $PROD_RG"
echo ""
echo "=== Step 5: Repeat for dev ==="
echo "  Same steps with $DEV_APP / $DEV_RG / $DEV_VAULT"
echo "  ($DEV_VAULT exists. Earlier documentation saying dev had no vault was wrong.)"
echo ""
echo "=== Rollback ==="
echo "  az webapp config appsettings set --name $PROD_APP --resource-group $PROD_RG \\"
echo "    --settings DATABASE_URL=\"<original literal value>\""
echo "  Keep the captured value until /health returns 200 on the reference."
echo ""
echo "=== Do NOT ==="
echo "  Do not rotate the database password in the same change. If the app stops"
echo "  serving you will not know whether the reference failed to resolve or the"
echo "  credential is wrong. Migrate, verify, then rotate."
