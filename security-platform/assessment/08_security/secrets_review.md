# Secrets Review

> Manual review for hardcoded secrets, key exposure, and secret handling. Read-only. **No secret values are reproduced in this report.**

## SEC-01 — Live API keys present in `backend/.env` (High, CWE-798, OWASP A02)
`backend/.env` contains **real production credentials**: a live Anthropic API key, a live OpenAI API key, and DB credentials (`.env:2-5`).

**Mitigating facts (verified):**
- `.env` **is gitignored and NOT tracked** — `git ls-files` / `git log` show it was never committed (no git-history exposure).
- `.env.example` is clean (placeholders only).
- The local `.env` `SECRET_KEY` is a 42-char placeholder (< 64), so `config.py:97` would **refuse to boot** with this file — real deployments must supply a strong key via environment/Key Vault.

**Residual risk:** real, working keys sit in a plaintext working-tree file on the developer machine (and any backup/sync of it). Because they've been on disk (and possibly shared), they should be treated as potentially exposed. **Fix:** (1) **rotate both API keys now**; (2) keep only placeholders in local `.env`; (3) source real keys from Key Vault (backend already vaults `ANTHROPIC-API-KEY` in prod — the gap is local dev hygiene). Effort: 0.5d + rotation.

## SEC-02 — Secrets in logs — GOOD (Info)
Log statements report only **key-presence state** (e.g. `"ANTHROPIC_API_KEY not set"` at `admin.py:32-33`) — **no key values are logged**. `.env`-loaded secrets are not echoed. **CWE-532 (for secrets): not present.**

## SEC-03 — `DATABASE_URL` as a direct credential (Medium, cross-ref Part 1/9)
`DATABASE_URL` embeds the DB password and is passed as a **direct app setting** rather than a Key Vault reference (confirmed in `infra/appService.bicep` + README as a **known, deliberate gap**). Rotating the DB password therefore requires a config change + restart, not a vault update. **Fix:** vault `DATABASE_URL` (or split into vaulted components). Effort: 0.5d.

## SEC-04 — Frontend secret scan (Info / follow-up)
No AI SDKs or server secrets are bundled in the frontend (AI is server-side). A targeted `NEXT_PUBLIC_*` scan for accidentally-exposed secrets is recommended as a quick follow-up, but none were surfaced in this pass. The only public config is the API base URL (`lib/api.js`), which is not a secret.

## Key Vault posture (cross-ref Part 9)
Prod vaults **4 secrets** (`SECRET-KEY`, `ANTHROPIC-API-KEY`, `AZURE-AD-CLIENT-SECRET`, `SENDGRID-API-KEY`) via RBAC + managed identity + purge protection. **Gaps:** `DATABASE_URL` not vaulted (SEC-03); **no rotation policy** on any secret (Part 9 §19). Note: `infra` params show KV `publicNetworkAccess: Enabled` with the private endpoint **authored-but-not-deployed** — verify against the live tenant (Part 9 flagged this contradiction with the memory note).

## Verdict
No secrets are committed to git (the most important negative result). The live **API keys in the working-tree `.env` are the actionable High** — rotate and move to Key Vault. `DATABASE_URL`-as-direct-credential and the missing rotation policy are Medium hygiene items. OWASP **A02** contribution: the `.env` keys.

## NIST mapping
SC-12/SC-28 (key management/at-rest) ◐, IA-5 (authenticator mgmt) ◐, SA-15 — rotation policy absent.
