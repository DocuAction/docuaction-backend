# Security Review — Summary (Part 8)

> Manual code review (no scanners) of the **live** `app.main:app` surface + frontend where relevant. Read-only — no files modified. Full detail in the sibling files; all findings in `security_findings.md`.

## Headline
**Security score: 6.0 / 10.** This is a **security-aware codebase** — the classic technical vulnerability classes are genuinely well-defended — held back by **one Critical** (an unauthenticated PHI module) and a cluster of **design/governance Mediums** (PHI egress minimization, audit immutability, read-audit, observability).

## The most important correction to prior parts
Parts 1–2 flagged **~32 unauthenticated GovCon + ~44 ATS endpoints** as the top access-control risk. **Direct verification of `app/main.py` shows those routers are NOT registered — they are dead/unwired code.** The real live unauthenticated exposure is a **different module**: `app/case_management` (wired at `main.py:321`). The prior conditional-HIGH is **downgraded** (dead code) and **replaced** by a verified Critical elsewhere. This is why reading the live entrypoint mattered.

## Total findings by severity
| Severity | Count | Items |
|---|:--:|---|
| **Critical** | **1** | AUTHZ-01 (unauthenticated Case Management PHI router) |
| **High** | **2** | DP-02 (unauth+unmasked PHI → Anthropic), SEC-01 (live API keys in `.env`) |
| **Medium** | **6** | AUTH-03 (Entra id_token unverified), AUTHZ-02 (IDOR), DP-03 (PHI in query strings), CRYPTO-DBTLS (DB TLS not pinned), AUDIT-MUT (mutable audit log), AUDIT-READ (no read-audit), SH-03 (no SWA CSP) — *7 listed; A06 components rated Low-Medium separately* |
| **Low** | **7** | AUTH-01, AUTH-02, SH-01, AUTHZ-03, DP-01, SEC-03, DP-05, SH-04, FU-02 |
| Info/GOOD | 11 | positive controls (see register) |

## Category 3 (truly unauthenticated) endpoint final count
**12** unauthenticated state-changing PHI endpoints — **all in `app/case_management/routes.py`** (:189–:652). **0** truly-unauthenticated state-changing endpoints elsewhere on the live surface (GovCon/ATS routers are dead code, not wired). *Nuance:* the patient CRUD endpoints among the 12 are currently non-persisting stubs, but the AI-generation + upload endpoints accept and forward real PHI unauthenticated.

## OWASP Top 10 (2021) risk levels
A01 **Critical** · A02 Medium · A03 **Low** · A04 **High** · A05 Medium · A06 Low-Medium · A07 Medium · A08 Medium · A09 Medium · A10 **Low**.

## Critical / High findings list
1. **[Critical] AUTHZ-01** — `app/case_management` router has no auth; 12 PHI endpoints live (`routes.py:34`, `main.py:321`).
2. **[High] DP-02** — case-management engines send **unmasked** PHI to Anthropic with **no auth** (`ccm_engine.py:25,164`, `discharge_engine.py:19,33`); main pipeline masks but misses names.
3. **[High] SEC-01** — live Anthropic/OpenAI API keys in working-tree `.env` (gitignored, not in git history — rotate anyway).

## What's genuinely strong (ATO evidence)
Injection defense (ORM, list-arg subprocess, UUID paths), bcrypt + pinned-HS256 + refresh rotation + revocation epoch + login timing-attack mitigation, `secrets`-based randomness, universal TLS verification, generic error handling, a full backend security-header set with strict CORS, and a multi-layer file-upload scanner. **A03 and A10 are Low** — the classic vuln classes are handled.

## Top 5 remediation priorities
1. **[Critical] Authenticate/authorize the entire `app/case_management` router** — add role + module-gate dependencies; route the `voice-to-note` upload through `FileScanner`. (0.5–1d)
2. **[High] Stop unauthenticated + unmasked PHI egress to Anthropic** — gate behind auth, run expanded `mask_pii` (names/addresses) before any external call, confirm a signed BAA + zero-retention. (1–2d)
3. **[High] Rotate the `.env` API keys** and move real values to Key Vault; placeholders only locally. (0.5d + rotation)
4. **[Medium] Fix the Medium cluster** — verify Entra id_token (JWKS+nonce), close the healthcare-claims IDOR, pin DB TLS, move PHI out of query strings. (2–4d total)
5. **[Medium] Audit & observability** — make `audit_logs` append-only (stop the delete/update paths) + hash-chain; log PHI reads; add a CSP to the SWA. (3–5d total)

## Security score: **6.0 / 10**
Fixing priorities 1–3 alone would lift the profile from "Critical present" to roughly **7.5–8** — the foundation is already strong; the risk is concentrated in one module plus governance.

*Cross-refs: audit-immutability + read-audit detailed in Part 10 (`audit_trail_assessment.md`); infra misconfig (public KV/Postgres, no IP restrictions) in Part 9 (`azure_operations.md`); PHI flow in Part 10 (`phi_data_flow.md`).*
