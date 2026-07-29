# Runbook: move `DATABASE_URL` into Key Vault

**Status:** open. Four secrets are Key Vault references on prod (`SECRET_KEY`,
`ANTHROPIC_API_KEY`, `AZURE_AD_CLIENT_SECRET`, `SENDGRID_API_KEY`).
`DATABASE_URL` is **not** — it sits in App Service configuration as a literal
connection string, password included.

**Why it matters:** anyone with Reader on the resource group can run
`az webapp config appsettings list` and read the production database password.
Reader is a role people get handed casually; database credentials are not.

## Preconditions

- The app already has a system-assigned managed identity with `get` on secrets
  (the other four references prove this).
- Vault name: check with
  `az keyvault list -g rg-docuaction-prod --query "[].name" -o tsv`.

## Procedure

1. Capture the current value. Do not paste it into a chat, a ticket, or a shell
   that logs history:
   ```bash
   az webapp config appsettings list -n Docuaction -g rg-docuaction-prod \
     --query "[?name=='DATABASE_URL'].value" -o tsv
   ```
2. Write it to the vault **from a file**, never as an inline argument:
   ```bash
   az keyvault secret set --vault-name <VAULT> --name DATABASE-URL --file ./dburl.txt
   shred -u ./dburl.txt   # or Remove-Item on Windows
   ```
   The `--file` form matters for two reasons: an inline `--value` lands in shell
   history, and a connection string containing `(` or `)` breaks Azure CLI
   argument parsing on Windows. That paren behaviour has bitten this project
   before.
3. Repoint the setting:
   ```bash
   az webapp config appsettings set -n Docuaction -g rg-docuaction-prod \
     --settings 'DATABASE_URL=@Microsoft.KeyVault(VaultName=<VAULT>;SecretName=DATABASE-URL)'
   ```
4. **Verify resolution before trusting it.** An unresolved reference is handed to
   the app as the literal 71-character `@Microsoft.KeyVault(...)` string. The app
   will fail to connect, but note the failure mode: a 71-character literal would
   pass a naive length check on a secret. Confirm the platform resolved it:
   ```bash
   az webapp config appsettings list -n Docuaction -g rg-docuaction-prod \
     --query "[?name=='DATABASE_URL'].{name:name,ref:value}" -o tsv
   ```
   then confirm the app is actually serving:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://api-prod.docuaction.io/health
   ```

## Rollback

Set `DATABASE_URL` back to the literal string and restart. Keep the captured
value until `/health` returns 200 on the Key Vault reference.

## Do not

Do not rotate the database password in the same change. If the app stops serving
you will not know whether the reference failed to resolve or the credential is
wrong. Migrate first, verify, rotate second.
