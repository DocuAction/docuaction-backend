# Session State — 2026-07-28

Rolling checkpoint for seamless continuation after interruption.
Updated at each significant milestone.

---

## Current position

| Field | Value |
|---|---|
| Branch | `main` |
| Latest commit | `68b1595` — Merge security hardening + DevOps + compliance |
| Unpushed commits | 0 (`origin/main` = `68b1595`, verified by fetch) |
| Working tree | clean at time of writing |
| Production | `/health` 200 · 8/8 endpoint sweep · scheduler running, 4 jobs |
| Development | `/health` 200 · 6/6 endpoint sweep when paced |
| Frontend | 3/3 (prod SWA, dev SWA, app.docuaction.io) |

---

## Completed this session (with evidence)

| # | Task | Evidence | Status |
|---|---|---|---|
| 1 | Health check configured on dev | `healthCheckPath` = `/health`; dev `/health` → 200 | VERIFIED |
| 2 | Health check on prod | Already configured before this session — no change made | VERIFIED (pre-existing) |
| 3 | FTP disabled, dev | `ftpsState` = `Disabled`; `/health` → 200 after | VERIFIED |
| 4 | FTP disabled, prod | `ftpsState` = `Disabled`; `/health` → 200 after | VERIFIED |
| 5 | Key Vault reference audit | prod 4 KV / 4 plaintext; dev 0 KV / 5 plaintext | VERIFIED |
| 6 | App Service security config report | Both: httpsOnly true, TLS 1.2, remoteDebugging false | VERIFIED |
| 7 | Merge branch → main | `git push`: `5a451e9..68b1595 main -> main`, exit 0 | VERIFIED |
| 8 | Merged-branch cleanup | 20 local branches deleted; 2 unmerged kept | VERIFIED |
| 9 | Stash reported, not popped | `stash@{0}`, 12 files, 486+/279- | VERIFIED |
| 10 | Production verification sweep | prod 8/8, dev 6/6 paced, frontend 3/3 | VERIFIED |
| 11 | Test suite on merged tree | 27 passed, 5 skipped, 0 failed (304s) | VERIFIED |
| 12 | `config/projects/template.json` | Valid JSON; synced to both platform copies | VERIFIED |
| 13 | Security platform README rewrite | Replaced stale version claiming 1B–1G "Not started" | VERIFIED |
| 14 | Full security scan (authoritative) | Running from LIVE platform | IN PROGRESS |
| 15 | Session report | Pending | NOT STARTED |

---

## Key finding this session

**Scanning from the wrong platform copy produces a false FAIL.**

The first Block 3 scan ran from `backend/security-platform/` and reported
**44.5 / gate FAIL / 5 Criticals**. Investigation showed the repo copy has
**0 suppressions** (versus 65 in the live copy) and is **missing four scanners**
because `tools/` is gitignored. Nothing about the code had changed.

This is now documented at the top of `security-platform/README.md`. It is an easy
mistake to repeat and it produces exactly the kind of alarming result that sends
someone hunting a non-existent regression.

---

## Azure changes made (all low-risk, all verified)

| Resource | Setting | Before | After |
|---|---|---|---|
| `docuaction-dev` | `healthCheckPath` | `null` | `/health` |
| `docuaction-dev` | `ftpsState` | `FtpsOnly` | `Disabled` |
| `Docuaction` (prod) | `ftpsState` | `FtpsOnly` | `Disabled` |

Prod `healthCheckPath` was already `/health` — no change was needed, correcting an
earlier assumption. `ftpsState` was `FtpsOnly` on both, not `AllAllowed` as
earlier documentation stated, so cleartext FTP was already off; this change closed
the FTPS path as well.

Pre-check performed before enabling the health probe: `ALLOWED_HOSTS` confirmed to
include the probe hostname on both apps. Without that, TrustedHost middleware
returns 400 on every path including `/health`, which would have failed every probe
and pulled instances out of rotation.

---

## Open issues

| Issue | Severity | Notes |
|---|---|---|
| BAA with Anthropic | High | PHI-capable path, no agreement |
| BAA with OpenAI | High | Audio transcription, no agreement |
| `DATABASE_URL` plaintext (prod) | Moderate | Script ready: `scripts/migrate-db-url-to-keyvault.sh` |
| Dev has 0 Key Vault references | Moderate | `docuaction-kv-dev` exists but is unused |
| `PERIGON_API_KEY` plaintext (prod) | Moderate | Newly observed this session; also flagged for rotation |
| Semgrep never run | Moderate | Every Windows score is missing one scanner |
| Audit log has no tamper-evidence | Moderate | Additive change, not implemented |
| `stash@{0}` unresolved | Low | 12 files WIP, not in HEAD, left untouched |
| Tracked runtime log dirties tree | Low | `logs/FCC_BULLETIN_EDITOR_AUDIT_*.log` blocks branch switches |

---

## Next recommended action

1. Finish the authoritative security scan and record the score (Block 3).
2. Write `docs/SESSION_REPORT_2026-07-28.md` (Block 6).
3. Commit and push the platform packaging work (Block 7).
4. Then continuous improvement: the highest-value low-risk items are the 15
   missing FK indexes and the N+1 queries in the bulletin pipeline.

## Rollback references

| Target | Tag / commit |
|---|---|
| Previous prod deploy | `deploy-prod-2026-07-28` |
| Hardening baseline | `v2.0-hardened` → `fda52d3` |
| Pre-merge `main` | `5a451e9` |
| Platform baseline | `platform-v1.0` → `ca3b430` |
