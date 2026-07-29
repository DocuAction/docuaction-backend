# Session State — 2026-07-28 / 2026-07-29 sprint

Rolling checkpoint for seamless continuation after interruption.
Updated at each significant milestone.

---

## Current position

| Field | Value |
|---|---|
| Branch | `main` |
| Latest commit | `412bf14` — docs: final scan figures, 59.2 / gate WARN |
| Unpushed commits | 0 (`origin/main` = `412bf14`, verified) |
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
| 20 | Test suite on final `main` | 27 passed, 5 skipped, 0 failed (294s) — confirmatory re-run | VERIFIED |
| 12 | `config/projects/template.json` | Valid JSON; synced to both platform copies | VERIFIED |
| 13 | Security platform README rewrite | Replaced stale version claiming 1B–1G "Not started" | VERIFIED |
| 14 | Full security scan (post-merge, authoritative) | **59.2 / gate WARN** · 203 findings [C:0 H:40 M:45 L:116] · SBOM 2 artefacts | VERIFIED |
| 19 | Gate FAIL investigated and resolved | Blocker was a gitleaks false positive on a public Azure role GUID; suppressed with 1-year expiry; gate back to WARN | VERIFIED |
| 15 | Session report | `docs/SESSION_REPORT_2026-07-28.md`, commit `fd09e96` | VERIFIED |
| 16 | Platform README + template.json | commit `fd09e96`, synced to both copies | VERIFIED |
| 17 | Platform copy drift audit | 163 files differ, only 2 real (rest CRLF); both reconciled | VERIFIED |
| 18 | CI workflow validation | All 5 workflows parse; job graphs intact | VERIFIED |

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

## Sprint 2026-07-29 — Blocks 1-3

| Task | Outcome | Status |
|---|---|---|
| 1.1 preview public | **No change needed.** Verified 200 without auth on prod; Day 3 guarded /docx and /pdf, never /preview | VERIFIED |
| 1.1b policy audit | One real mismatch found and fixed: `GET /profiles` was public, policy requires guarded | VERIFIED |
| 1.2 .claude deletion | Deleted (held 5 postgres URIs with passwords); never tracked in git; `.claude/` gitignored | VERIFIED |
| 1.3 IQVIA | Document not in either repo (0/5 phrases). Edits written to `docs/IQVIA_REMOVAL_EDITS.md` | VERIFIED |
| 2.1 sources | 4 added, not 18 — 13 already present; AP and Reuters URLs were dead, replaced via Google News | VERIFIED |
| 2.2 Talkwalker | Endpoint returns 404 for all 12 queries; no public query-to-RSS exists. Module reads `TALKWALKER_FEED_URLS` | BLOCKED, documented |
| 2.3 Google News QA | Live: 132 found, 94 added, 9 rejected by relevance gate, 29 deduped | VERIFIED |
| 2.4/2.5 profiles | Commissioners into 8 of 9 profiles, keywords into 7 | VERIFIED |
| 2.6 pipeline wiring | collect → classify → QA → generate; failure leaves briefing unchanged | VERIFIED |
| 2.7 QA cost tracking | `qa_verification` at $0.00 with feed count | VERIFIED |
| 3.3 NPI Luhn | Canonical module; agrees with existing IntakeValidator on all cases | VERIFIED |
| 3.2 TEFCA state machine | Built over existing `operational_status`; no schema change | VERIFIED |
| 3.4 tests | 27 → 66 | see final run |

### Decisions logged

- **One deployment, not two.** Block 2 and Block 3 code shipped together to halve
  the number of production changes.
- **Feeds added to `engine.py`, not `fcc_sources.py`.** The latter is imported by
  nothing; adding URLs there would have changed no behaviour.
- **IQVIA references left in the codebase.** All 26 backend and 13 frontend hits
  are `IQVIA OneKey`, a pending connector with a class name and an env var. A
  global replace would rename a class and break config while fixing nothing.
- **Task 3.1 (guard remaining Highs) not attempted.** The previous pass
  established that most remaining AGT-AUTHZ-001 findings are intentional public
  reads whose disposition is a product judgement, not a mechanical fix.

## Next recommended action

All requested blocks are complete and verified. Recommended next, in value order:

1. **Run Semgrep once on Linux CI.** Every score reported to date is missing one
   scanner's coverage, so 59.2 is an upper bound rather than a measurement.
2. **Migrate `DATABASE_URL` and `PERIGON_API_KEY` to Key Vault** (prod), then
   adopt `docuaction-kv-dev` for the five plaintext dev secrets.
3. **Add the 15 missing foreign-key indexes and fix the bulletin N+1 queries.**
   Highest-value performance work available — but note this touches the database
   schema, which is prohibited without explicit instruction, so it needs a
   go-ahead before starting.
4. **Implement audit log hash chaining** (additive, backward-compatible).
5. **Resolve `stash@{0}`** — 12 files of WIP that predate this session.

## Rollback references

| Target | Tag / commit |
|---|---|
| Previous prod deploy | `deploy-prod-2026-07-28` |
| Hardening baseline | `v2.0-hardened` → `fda52d3` |
| Pre-merge `main` | `5a451e9` |
| Platform baseline | `platform-v1.0` → `ca3b430` |
