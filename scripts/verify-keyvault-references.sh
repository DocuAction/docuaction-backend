#!/bin/bash
# Verifies all Key Vault references resolve correctly
set -euo pipefail

APP=${1:-Docuaction}
RG=${2:-rg-docuaction-prod}

echo "Checking Key Vault references for $APP..."

REFS=$(az webapp config appsettings list --name "$APP" --resource-group "$RG" \
  --query "[?contains(value, '@Microsoft.KeyVault')].[name,value]" -o tsv)

if [ -z "$REFS" ]; then
  echo "No Key Vault references found"
  exit 1
fi

echo "$REFS"
echo ""
echo "Checking health..."

if [ "$APP" = "Docuaction" ]; then
  curl -s https://api-prod.docuaction.io/health | python -m json.tool
else
  curl -s "https://${APP}.azurewebsites.net/health" | python -m json.tool
fi

echo ""
echo "NOTE: the listing above shows the reference SYNTAX is present. It does not"
echo "prove Azure resolved it - an unresolved reference looks identical here."
echo "A healthy response is the resolution evidence, because the application"
echo "cannot reach the database with an unresolved connection string."
