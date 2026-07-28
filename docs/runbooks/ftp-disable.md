# Runbook: disable FTP/FTPS deployment

**Status:** open on both apps. FTP state defaults to `AllAllowed`.

## Why

FTP is a second, parallel way to write to `wwwroot` that bypasses the deployment
pipeline entirely — no artifact verification, no `.env` check, no record in
`az webapp log deployment list`. Plain FTP also transmits credentials in the
clear. Nothing in this project deploys over FTP: prod uses `az webapp deploy`
with a pre-built `pydeps` zip, dev uses the same command with an Oryx build.

## Check current state

```bash
az webapp config show -n Docuaction -g rg-docuaction-prod --query "ftpsState" -o tsv
az webapp config show -n docuaction-dev -g rg-docuaction-dev --query "ftpsState" -o tsv
```

## Procedure

```bash
az webapp config set -n Docuaction -g rg-docuaction-prod --ftps-state Disabled
az webapp config set -n docuaction-dev -g rg-docuaction-dev --ftps-state Disabled
```

`Disabled` turns off both FTP and FTPS. If some tool genuinely needs it, use
`FtpsOnly` — that at least removes the cleartext path — but confirm what that
tool is first, because the answer is usually "nothing".

## Verify

```bash
az webapp config show -n Docuaction -g rg-docuaction-prod --query "ftpsState" -o tsv   # Disabled
curl -s -o /dev/null -w '%{http_code}\n' https://api-prod.docuaction.io/health         # 200
```

## Impact

None on zip deploy or on the Kudu/SCM endpoint, which is a separate channel and
is what CI uses. This does not affect `az webapp deploy`.
