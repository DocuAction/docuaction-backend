# Branch Review — SEC-01

**Backend:** `sprint1/sec-01-secrets-management` @ `9e041df` (**stacked on `da9ae7c`**)
**Frontend:** none
**Finding:** SEC-01 — High · OWASP A02 · CWE-798 · NIST SC-12 / IA-5
**Risk rating: LOW–MEDIUM** (Low as configured today; Medium because it introduces a new
*availability* failure mode by design)

---

## 1. Files changed

`9e041df` — 2 files, **+370 / −0**. No deletions at all; **310 additions are documentation**,
leaving **60 lines** in one file, of which ~28 are comment.

| File | + | − | Purpose |
|---|--:|--:|---|
| `app/core/config.py` | 60 | 0 | Unresolved-Key-Vault-reference guard: `_KV_REFERENCE_PREFIX`, `_assert_resolved()`, two required-setting calls, an optional-setting warn loop |
| `docs/compliance/SECRETS_MANAGEMENT.md` | 310 | 0 | **NEW** — inventory, architecture, rotation checklist, residual risk |

---

## 2. Why the change was necessary

Secrets reach this application as plain environment variables. On Azure App Service the
sensitive ones are **Key Vault references** — app settings of the form
`@Microsoft.KeyVault(VaultName=...;SecretName=...)` that the platform resolves with the
site's managed identity *before* the process starts.

**When resolution fails, App Service does not fail the start — it injects the literal
reference string as the value.** For `SECRET_KEY` that is silently dangerous, because the
literal string satisfies the pre-existing entropy floor:

```
literal:  @Microsoft.KeyVault(VaultName=docuaction-kv-prod;SecretName=SECRET-KEY)
length:   71          SECRET_KEY floor: 64          passes the floor: TRUE
```

Without the guard the application **boots and signs every JWT with a publicly derivable
constant** — the vault name and secret name are both non-secret and both visible in
`infra/`. Anyone who knows them can reconstruct the signing key and forge tokens for any
account, including admin. The existing 64-character check does not catch this; the
reference string's length is precisely what lets it through.

The guard **must run before the length check**, and does — that ordering is load-bearing
and is commented as such in the source.

Failure-mode split: required settings (`SECRET_KEY`, `DATABASE_URL`) fail hard, because a
deploy that cannot reach its secrets must not serve traffic on a predictable signing key.
Optional settings (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) log at ERROR and continue, so
one integration's misconfiguration cannot take the whole platform down.

**No Key Vault SDK was added.** No `azure-identity`, no `azure-keyvault-secrets`, no
`DefaultAzureCredential`. Key Vault already works at the platform layer, which is the
recommended App Service pattern; fetching secrets in code would duplicate a working
mechanism, add a dependency and network round-trips to startup, and break local
development. The correct fix was to make failure loud, not to reimplement resolution.

---

## 3. Database schema changes

**NONE.** No migration, no DDL, no model change, no data change.

---

## 4. API behaviour changes

**NONE.** The guard runs once at module import. No route, request, response, status code,
or auth behaviour changes. Confirmed at runtime: `/health` 200, 22/22 modules loaded with
none skipped, `/api/admin/users` 200, TEFCA dashboard + registry 200, case-management 403
anonymous / 200 authenticated.

---

## 5. Frontend behaviour changes

**NONE.** No frontend file touched.

---

## 6. Backward compatibility and boot-failure risk

You asked specifically whether the guard could cause a boot failure in production or dev,
and for the logic to be verified against all cases. I ran a nine-case matrix using the
**real value shapes read from each environment**, not hypotheticals:

| Case | Expected | Actual | |
|---|---|---|:--:|
| **PROD shape** — resolved KV secret + plaintext DB URL | BOOT | BOOT | ✅ |
| **DEV shape** — plaintext secret (96 ch) + plaintext DB URL | BOOT | BOOT | ✅ |
| **LOCAL shape** — 42-char `SECRET_KEY` | FAIL-length | FAIL-length | ✅ |
| BROKEN — unresolved `SECRET_KEY` reference | FAIL-guard | FAIL-guard | ✅ |
| BROKEN — unresolved `DATABASE_URL` reference | FAIL-guard | FAIL-guard | ✅ |
| EDGE — leading/trailing whitespace on the reference | FAIL-guard | FAIL-guard | ✅ |
| EDGE — value merely *contains* the marker mid-string | BOOT | BOOT | ✅ |
| EDGE — legitimate secret starting with `@` but not a reference | BOOT | BOOT | ✅ |
| EDGE — lowercase `@microsoft.keyvault(` (not an App Service form) | BOOT | BOOT | ✅ |

**All nine as designed.**

### Answers to the specific questions

- **Could it break production?** **No, as prod stands today.** All four references were
  verified live as `"status": "Resolved"`, so the guard is a no-op. It only fires if a
  reference *stops* resolving — i.e. only in the scenario where the alternative is
  signing JWTs with a public constant.
- **Could it break dev?** **No.** Dev has **zero** Key Vault references — all six
  secret-bearing settings are plaintext — so there is nothing for the guard to catch.
- **Could it break local development?** **No.** A literal `@Microsoft.KeyVault(` value
  never appears locally. Local dev is separately blocked by the 42-char `SECRET_KEY` in
  `.env` — a **pre-existing** condition caused by the earlier 64-char floor, not by this
  branch.
- **Prefix matching is case-sensitive and anchored** (`.strip().startswith()`). Deliberate:
  App Service always emits the exact `@Microsoft.KeyVault(` form, so anchoring avoids
  false positives on a legitimate secret that merely contains the substring, and
  case-sensitivity avoids flagging an unrelated value.

### Compatibility summary

| Consumer | Impact |
|---|---|
| Prod / dev / local as configured | None — guard is a no-op in all three |
| Any client, integration, workflow | None — startup-only validation |
| Stored data | None |
| Deploy pipeline | Unchanged, **but see §8** — a broken-reference deploy now fails fast instead of degrading silently |

---

## 7. Rollback procedure

```bash
cd "C:/Imran_Coding projects/DocuAction/backend"
git revert 9e041df
```

No schema, migration, config, data, or dependency change to unwind. Reverting restores
the previous behaviour — including the latent defect, so revert only to unblock an
outage, and reinstate afterwards.

**Emergency alternative if the guard fires during a deploy and you must restore service
immediately:** set the affected app setting to a real value directly (temporarily
bypassing Key Vault) rather than reverting code. That restores service *and* keeps the
guard, whereas reverting the guard would let the app run on the literal reference string.

**Stack caveat:** `9e041df` sits on `da9ae7c` → `4879e3e`. It touches only
`app/core/config.py`, which no other Sprint 1 commit modifies, so `git revert 9e041df` is
conflict-free regardless of what else is merged.

---

## 8. Risk rating: **LOW–MEDIUM**

| Factor | Assessment |
|---|---|
| Blast radius | One file; startup-only |
| Direction | Turns a silent security failure into a loud availability failure |
| Regression potential today | **None** — verified no-op in prod, dev, and local |
| Data risk | None |
| Reversibility | Complete; plus a non-revert emergency path (§7) |
| Verification | 9/9 case matrix; full app boot; `/health` 200 |

### The one genuinely new risk — do not overlook it

**A deploy whose Key Vault references do not resolve will now fail to start**, where
previously it would have started with a bad `SECRET_KEY`. This is intended and is the
whole point, but it converts a silent *security* incident into a visible *availability*
incident. Two consequences:

1. **Verify reference resolution *before* restarting the site** after any secret, RBAC,
   or vault-firewall change. Command in `docs/compliance/SECRETS_MANAGEMENT.md` §5.
2. **A botched vault change now causes downtime.** Keep
   `docs/deployment/rollback-procedures.md` to hand during any Key Vault work.

### What this branch does NOT fix — unchanged and still open

- **Rotation was not performed.** No key generated or rotated, no Key Vault or Azure
  resource modified, no `.env` value changed. Checklist in the doc §4; needs an operator.
- `DATABASE_URL` remains a **plaintext app setting containing the DB password** in prod
  *and* dev.
- **Dev is entirely un-vaulted** — 6 plaintext secrets, though `docuaction-kv-dev` exists.
- `OPENAI_API_KEY` exists **only** in local `.env`; set in neither environment.
- **`AZURE_AD_CLIENT_SECRET` expiry is a blind spot the guard cannot cover** — an expired
  secret still *resolves*, so the reference is valid and the value is merely rejected by
  Entra. That is an SSO outage this guard will not predict. Check its expiry and calendar
  the renewal.
