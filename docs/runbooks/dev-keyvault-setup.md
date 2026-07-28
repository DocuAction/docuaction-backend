# Runbook: Key Vault for the dev environment

**Status:** open. Prod resolves four secrets from Key Vault via managed identity.
Dev has **no vault** — every secret on `docuaction-dev` is a literal app setting.

## Why bother, for a dev box

Two reasons, neither of them theoretical here.

1. Dev holds real credentials. It talks to `docuaction-db-dev`, sends through the
   same SendGrid account, and carries an Anthropic key. "It is only dev" is a
   statement about the environment's purpose, not about the value of what is in
   its configuration.
2. Configuration drift between environments is how deploys break. Prod resolves
   `SECRET_KEY` from a vault; dev does not. Any behaviour that depends on how a
   secret is delivered is untested until it reaches prod.

## Procedure

1. Create the vault with RBAC (not access policies — RBAC is what prod uses):
   ```bash
   az keyvault create -n kv-docuaction-dev -g rg-docuaction-dev -l centralus \
     --enable-rbac-authorization true
   ```
2. Ensure the app has a system-assigned identity:
   ```bash
   az webapp identity assign -n docuaction-dev -g rg-docuaction-dev
   ```
3. Grant it read on secrets:
   ```bash
   PID=$(az webapp identity show -n docuaction-dev -g rg-docuaction-dev --query principalId -o tsv)
   VID=$(az keyvault show -n kv-docuaction-dev -g rg-docuaction-dev --query id -o tsv)
   az role assignment create --assignee "$PID" --role "Key Vault Secrets User" --scope "$VID"
   ```
4. Load secrets **from files**, never inline. A value containing `(` or `)` — a
   connection string, or a Key Vault reference itself — breaks Azure CLI argument
   parsing on Windows:
   ```bash
   az keyvault secret set --vault-name kv-docuaction-dev --name SECRET-KEY --file ./s.txt
   ```
5. Repoint settings one at a time, verifying `/health` between each:
   ```bash
   az webapp config appsettings set -n docuaction-dev -g rg-docuaction-dev \
     --settings 'SECRET_KEY=@Microsoft.KeyVault(VaultName=kv-docuaction-dev;SecretName=SECRET-KEY)'
   curl -s -o /dev/null -w '%{http_code}\n' https://docuaction-dev.azurewebsites.net/health
   ```

## The failure mode to watch for

An unresolved Key Vault reference is passed to the application as the **literal**
`@Microsoft.KeyVault(VaultName=...;SecretName=...)` string. That string is about
71 characters — long enough to satisfy the 64-character `SECRET_KEY` floor this
codebase enforces. The app would start with its signing key set to a piece of
configuration syntax. Always verify the app actually serves after each repoint;
do not infer resolution from the app starting.

## Order

Do dev **after** `DATABASE_URL` on prod is migrated, not before. Prod is the
environment where the exposure is real; dev is where the drift is.
