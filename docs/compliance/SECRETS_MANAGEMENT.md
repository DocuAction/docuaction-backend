# Secrets Management — Control Record (finding SEC-01)

**Status:** CODE HARDENED — rotation is a MANUAL action for the account owner (§4).
**Date:** 2026-07-26 · **Sprint:** 1 — Critical & High Security Remediation
**Branch:** `sprint1/sec-01-secrets-management`
**OWASP** A02 · **CWE** 798 · **NIST** SC-12 / IA-5

All Azure facts below were read live from the subscription `AGT-DocuAction`
(`6ce81f40-…`) on 2026-07-26 — **read-only**; no Azure resource, Key Vault secret, or
`.env` value was created, modified, or rotated.

---

## 1. Secrets inventory

| Secret | Local `.env` | Prod `Docuaction` (rg-docuaction-prod) | Dev `docuaction-dev` (rg-docuaction-dev) | Recommended source | Status |
|---|---|---|---|---|---|
| `SECRET_KEY` | present, **42 chars** — below the 64 floor, app cannot boot locally | **Key Vault ref → Resolved** (`SECRET-KEY`) | plaintext (96) | Key Vault | ✅ prod · ❌ dev · ❌ local |
| `ANTHROPIC_API_KEY` | present (71) | **Key Vault ref → Resolved** (`ANTHROPIC-API-KEY`) | plaintext (108) | Key Vault | ✅ prod · ❌ dev |
| `SENDGRID_API_KEY` | absent | **Key Vault ref → Resolved** (`SENDGRID-API-KEY`) | plaintext (69) | Key Vault | ✅ prod · ❌ dev |
| `AZURE_AD_CLIENT_SECRET` | absent | **Key Vault ref → Resolved** (`AZURE-AD-CLIENT-SECRET`) | plaintext (40) | Key Vault | ✅ prod · ❌ dev |
| `DATABASE_URL` | present (62) | **plaintext app setting (107)** — contains the DB password | plaintext (123) | Key Vault ref, or Entra managed-identity auth to Postgres | ❌ both |
| `OPENAI_API_KEY` | **present (164)** | **not set at all** | not set | Key Vault *if the feature is used* | ⚠️ local-only |
| `APPINSIGHTS_INSTRUMENTATIONKEY` | absent | plaintext (36) | plaintext | Key Vault (low severity) | ❌ |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | absent | plaintext | plaintext | Key Vault (embeds the instrumentation key) | ❌ |
| **`JWT_SECRET`** | — | — | — | — | **DOES NOT EXIST — see §1.2** |
| Azure credentials | n/a | **system-assigned managed identity — no stored credential** | system-assigned | Managed identity | ✅ already correct |

### 1.1 Additional secret-bearing variables the code reads but nothing provides

`grep` of `os.getenv` / `os.environ` across `app/` shows **~18 secret-bearing
variables**. Beyond the table above, these are read by code and are set in **no**
environment (prod, dev, or local) — features depending on them are inert:

`GEMINI_API_KEY`, `PERPLEXITY_API_KEY`, `TAVILY_API_KEY`, `NEWSAPI_KEY`,
`NEWSAPI_AI_KEY`, `CONGRESS_API_KEY`, `GOVINFO_API_KEY`, `SAM_GOV_API_KEY`,
`RCE_DIRECTORY_API_KEY`, `IQVIA_ONEKEY_API_KEY`, `YOUTUBE_API_KEY`,
`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, plus `ZOOM_CLIENT_SECRET`,
`GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_SECRET` (declared in `config.py`, empty).

**The SEC-01 finding as written ("live Anthropic/OpenAI API keys in working-tree
`.env`") understates the scope.** The issue is not two keys in one file; it is that
the platform has ~18 secret-bearing inputs and a Key Vault policy covering **4**.

### 1.2 Correction: there is no `JWT_SECRET`

The requested inventory lists `JWT_SECRET`. No such variable exists anywhere in the
codebase. JWTs are signed with **`SECRET_KEY`** (`app/core/security.py` —
`jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")`). That row should be
struck from the inventory, not migrated — creating a `JWT_SECRET` would be dead
configuration and would imply a second signing key that does not exist.

### 1.3 `.env` git history — definitively clean

```
$ git rev-list --all --objects | <extract paths> | grep -E "(^|/)\.env"
.env.example
```

A full object scan across **every reachable commit on every ref** finds only
`.env.example`. `.env` is listed at `.gitignore:3`, is not tracked
(`git ls-files --error-unmatch .env` → no match), and **has never been committed**.
The finding's parenthetical "gitignored, not in history" is confirmed, not assumed.

Practical consequence: **no history rewrite (BFG / filter-repo) is required**, and
the keys were never exposed via the repository. Rotation is still warranted (§4)
because the keys have sat in a working-tree file on a developer workstation, but
this is a lower-urgency rotation than a committed-secret incident.

---

## 2. Configuration architecture

### How secrets reach the application today

```
                      ┌──────────────────────── Azure App Service ───────────────────┐
Azure Key Vault       │                                                              │
docuaction-kv-prod    │  app setting: SECRET_KEY =                                   │
  SECRET-KEY  ────────┼──►  "@Microsoft.KeyVault(VaultName=...;SecretName=SECRET-KEY)"│
  ANTHROPIC-API-KEY   │        │                                                     │
  SENDGRID-API-KEY    │        │ resolved BEFORE process start by the site's          │
  AZURE-AD-CLIENT-…   │        │ system-assigned managed identity                     │
  (public network     │        ▼                                                     │
   access DISABLED)   │  process env: SECRET_KEY=<real secret>                        │
                      │        │                                                     │
                      └────────┼─────────────────────────────────────────────────────┘
                               ▼
                   pydantic-settings Settings()  (app/core/config.py)
                               │
                   env vars first, then .env file (local dev only)
```

**Key Vault integration exists — at the platform layer, not in application code.**
There is no `azure-identity` / `azure-keyvault-secrets` dependency, no
`DefaultAzureCredential`, and no `SecretClient` anywhere in `app/`. That is
**correct** for App Service and was left unchanged deliberately (§3).

### Verified prod state (read live)

| Check | Result |
|---|---|
| Key Vault references | **4**, all `"status": "Resolved"` |
| Resolving identity | `SystemAssigned`; `keyVaultReferenceIdentity: SystemAssigned` |
| Site principal | `5a909adf-0130-4032-aaa7-502e03a5df07` |
| Vault network | **public network access DISABLED** — a workstation `az keyvault secret list` returns `Forbidden / ForbiddenByConnection` |
| RBAC | Key Vault Secrets User granted to the site identity (`infra/modules/appService.bicep`) |
| `https://api.docuaction.io/health` | **200** |

### Environment parity gap

**Dev uses no Key Vault at all.** `docuaction-kv-dev` exists, but all six
secret-bearing dev app settings are plaintext. Dev is therefore a weaker copy of
prod holding real working credentials — and dev's `ANTHROPIC_API_KEY` (108 chars) is
a *different value* from both prod's and local's (71 chars), so there are up to
**three distinct Anthropic keys** in circulation. Factor that into rotation scope.

---

## 3. Code change made

**One change, in `app/core/config.py`: an unresolved-Key-Vault-reference guard.**

### The defect it fixes

When an App Service Key Vault reference fails to resolve — managed identity loses
`Key Vault Secrets User`, vault firewall change, secret renamed / disabled /
expired, vault outage — **App Service does not fail the start. It injects the
literal reference string as the environment variable value.**

For `SECRET_KEY` that is silently dangerous, because the literal string satisfies
the existing entropy floor:

```
literal:  @Microsoft.KeyVault(VaultName=docuaction-kv-prod;SecretName=SECRET-KEY)
length:   71          SECRET_KEY floor: 64          passes the floor: TRUE
```

Without the guard the application **boots successfully and signs every JWT with a
publicly derivable constant** — anyone who knows the vault name and secret name (both
non-secret, and both visible in `infra/`) can reconstruct the signing key and forge
tokens for any account, including admin. The pre-existing 64-char check does not
catch this; the reference string's length is precisely what lets it through.

### Behaviour

| Setting class | On unresolved reference |
|---|---|
| `SECRET_KEY`, `DATABASE_URL` (required) | **Hard `RuntimeError` at import — the app refuses to start.** A deploy that cannot reach its secrets must not serve traffic on a predictable signing key. |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (optional) | **Logged at `ERROR`, startup continues.** One integration's misconfiguration must not take the whole platform down; the dependent feature fails on its own, and the log makes the reason obvious instead of surfacing as a confusing upstream 401. |

The check runs **before** the length check — order is load-bearing and is commented
as such in the source. The error message lists the four things to check, in
diagnostic order, plus the `az rest …/configreferences/appsettings` command that
reports per-setting resolution status.

### What was deliberately NOT done

| Not done | Why |
|---|---|
| Add `azure-keyvault-secrets` + `DefaultAzureCredential` to fetch secrets in code | Duplicates a platform mechanism that already works, adds a dependency and network round-trips to startup, and breaks local development (no Azure identity). App Service references are the recommended pattern; the correct fix was to make failures loud, not to reimplement resolution. |
| Move `DATABASE_URL` to a Key Vault reference | Infrastructure change. Documented in §4 as a manual action. |
| Add Key Vault references to dev | Infrastructure change. Documented in §4. |
| Generate, rotate, or modify any key; touch Key Vault; touch `.env` | Explicitly out of scope per instruction. |
| Raise the local `SECRET_KEY` to 64+ chars | Would modify `.env`. Flagged in §4 for manual action. |

---

## 4. Manual rotation checklist — FOR IMRAN

Nothing in this section was performed. Each item needs an operator with Azure and
vendor-console access. **Nothing here is urgent-incident work: `.env` was never
committed (§1.3), so these are hygiene rotations, not breach response.**

Order matters — do §4.1 before §4.2 so a stale key is never live in only one place.

### 4.1 Rotate the two keys named in the finding

- [ ] **`ANTHROPIC_API_KEY`** — up to **three** distinct values in circulation
      (prod via Key Vault, dev plaintext, local `.env`). At console.anthropic.com:
      1. Create a new key.
      2. `az keyvault secret set --vault-name docuaction-kv-prod --name ANTHROPIC-API-KEY --value <new>`
         (run from a network the vault permits — public access is disabled, so this
         needs a private endpoint, a permitted VNet, or a temporary firewall
         allowance you then remove).
      3. Restart the prod site (or wait for the reference cache to refresh) and
         confirm `status: Resolved` via the `az rest` command in §5.
      4. Update dev and local separately — ideally with **different** keys so dev
         traffic is attributable and revocable independently of prod.
      5. Revoke the old key(s) in the Anthropic console **after** verifying prod.
- [ ] **`OPENAI_API_KEY`** — present only in local `.env` (164 chars); **not set in
      prod or dev at all**. Decide first whether Whisper transcription is actually
      in use. If yes, rotate and add to Key Vault; if no, **delete the line from
      `.env`** — an unused live key is pure liability.

### 4.2 Close the gaps this review found

- [ ] **`DATABASE_URL` → Key Vault reference** (prod and dev). Currently a plaintext
      app setting containing the DB password, readable by anyone with Reader on the
      resource group. Store the full connection string as a vault secret and
      reference it exactly like the other four. `infra/modules/appService.bicep`
      already carries a comment stating this is the intended design.
      *Better end state:* Entra managed-identity authentication to Azure Postgres,
      which removes the password entirely — larger change, separate task.
- [ ] **Dev environment → Key Vault.** `docuaction-kv-dev` exists and is unused; all
      six dev secrets are plaintext. Apply the same four references plus
      `DATABASE_URL`, and grant the dev site identity Key Vault Secrets User.
- [ ] **Local `SECRET_KEY` is 42 chars** — below the enforced 64 floor, so the app
      cannot start locally from `.env` alone. Generate a local-only value:
      `python -c "import secrets; print(secrets.token_urlsafe(64))"`.
      **Never reuse the production key locally.**
- [ ] **`APPLICATIONINSIGHTS_CONNECTION_STRING` / `APPINSIGHTS_INSTRUMENTATIONKEY`**
      — plaintext in both environments. Lower severity (telemetry ingestion only,
      no data read) but still a credential; move to Key Vault when convenient.
- [ ] **Rotate `SECRET_KEY`?** *Not recommended as routine hygiene* — it was rotated
      2026-07-18 and rotating it **invalidates every active session and JWT**,
      forcing all users to re-authenticate. Do it only on suspected compromise, and
      during a maintenance window.
- [ ] **Adopt a rotation cadence** — NIST IA-5 expects defined periodicity. Suggest
      90 days for vendor API keys, on-compromise for `SECRET_KEY`, and per Entra
      policy for `AZURE_AD_CLIENT_SECRET` (note: **client secrets expire** — check
      its expiry now and calendar the renewal, because expiry causes an
      Entra SSO outage that the §3 guard will *not* catch: the reference still
      resolves, the value is simply no longer valid).

### 4.3 Standing hygiene

- [ ] Confirm `.env` stays in `.gitignore` (currently line 3) and never gets
      force-added. Consider a pre-commit secret scanner — `.pre-commit-config.yaml`
      already exists in the repo, so this is a small addition.
- [ ] Keep prod Key Vault public network access **disabled** (verified as-is).
- [ ] Enable Key Vault soft-delete and purge protection if not already on, so a
      deleted secret cannot become an unrecoverable outage.

---

## 5. Rotation and verification procedure

Set a Key Vault secret (from a network the vault permits):

```bash
az keyvault secret set \
  --vault-name docuaction-kv-prod \
  --name ANTHROPIC-API-KEY \
  --value '<new-secret>'
```

Verify every reference still resolves — run this **after any secret, RBAC, or vault
firewall change**, and after every deploy:

```bash
SUB=$(az account show --query id -o tsv)
az rest --method get --uri \
  "/subscriptions/$SUB/resourceGroups/rg-docuaction-prod/providers/Microsoft.Web/sites/Docuaction/config/configreferences/appsettings?api-version=2022-03-01" \
  --query "value[].{name:name,status:properties.status,detail:properties.details}" -o table
```

Every row must read `Resolved`. Then confirm the app: `curl -s -o /dev/null -w '%{http_code}' https://api.docuaction.io/health` → `200`.

**Note the guard's interaction with rotation:** if a reference breaks, the site will
now **fail to start** rather than boot with a bad `SECRET_KEY`. That is intended — a
loud failure beats silently signing JWTs with a public constant — but it means a
botched vault change becomes an availability incident instead of a silent security
incident. Verify resolution *before* restarting the site, and keep
`docs/deployment/rollback-procedures.md` to hand.

---

## 6. Remaining risks

| # | Risk | Severity | Owner |
|---|---|---|---|
| 1 | `DATABASE_URL` password in plaintext app settings, prod **and** dev | High | Infra — §4.2 |
| 2 | Dev environment entirely un-vaulted; holds real working credentials | Medium | Infra — §4.2 |
| 3 | Local `.env` holds live Anthropic + OpenAI keys on a developer workstation (never committed, but unencrypted at rest) | Medium | §4.1 |
| 4 | Up to three distinct Anthropic keys in circulation with no inventory of which is which | Medium | §4.1 |
| 5 | ~14 further secret-bearing variables read by code with no Key Vault policy — they will be adopted ad hoc as features light up unless a policy is set now | Medium | Policy |
| 6 | `AZURE_AD_CLIENT_SECRET` **expiry** causes an SSO outage the §3 guard cannot detect (the reference resolves; the value is merely invalid) | Medium | §4.2 |
| 7 | App Insights connection string / instrumentation key plaintext | Low | §4.2 |
| 8 | No automated rotation cadence or expiry alerting | Low | Policy |
| 9 | No pre-commit secret scanning despite `.pre-commit-config.yaml` existing | Low | §4.3 |
| 10 | Secret **reads** are not audited — Key Vault diagnostic logging should be confirmed enabled and retained, so an anomalous read pattern is detectable | Low | Infra |

---

## 7. Deployment notes

- **No dependency, schema, migration, or infrastructure change.** The only change is
  additional validation in `app/core/config.py`.
- **Deploy is safe with prod as it stands today:** all four references currently
  report `Resolved`, so the new guard is a no-op on the current configuration —
  verified live before writing this document.
- **New failure mode, by design:** a deploy whose Key Vault references do not
  resolve will now fail fast at startup instead of running with a predictable
  `SECRET_KEY`. Run the §5 verification before restarting the site.
- **Local development is unaffected** by the guard — a literal `@Microsoft.KeyVault(`
  value never appears locally. Local dev is separately blocked by the 42-char
  `SECRET_KEY` in `.env` (§4.2), which predates this change.
- **Rollback:** `git revert <sha>`. No state to unwind.

---

## 8. Finding register update

`security_findings.md` row **SEC-01** is updated to record: `.env` verified **never
committed** (full object scan, so no history rewrite needed); scope corrected from
2 keys to **~18 secret-bearing variables of which 4 are Key Vault backed**;
`JWT_SECRET` **does not exist** (JWTs use `SECRET_KEY`); a previously unreported
latent defect (unresolved Key Vault reference accepted as `SECRET_KEY`) found and
fixed in code; **rotation itself remains OPEN and manual**.
