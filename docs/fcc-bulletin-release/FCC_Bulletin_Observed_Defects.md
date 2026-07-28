# FCC Bulletin v1.0 — Observed Defects (Staging)

**Executed:** 2026-07-07 · Local staging (v1.0 backend vs `docuaction-db`, real feeds).

**Important:** every item below was **actually observed** during staging. **No FCC Bulletin *code* defect was found.** All findings are environment/config or documented-limitation. None were fixed (all fall outside the FCC Bulletin module or are config items for AGT); per the rules, no speculative code changes were made.

**Type:** Config (environment) · Framework (shared, out of module scope) · Limitation (by design).

---

### DEF-S1 — Invalid Anthropic API key → AI classification/summaries fail
- **Severity:** High · **Type:** Config · **Environment:** staging `.env`
- **Steps to reproduce:** boot backend with the `.env` `ANTHROPIC_API_KEY`; run `POST /run/fcc`; watch engine logs.
- **Expected:** articles classified/summarized by Claude.
- **Observed:** every call → `401 authentication_error: invalid x-api-key` (hundreds). Engine **fell back** to non-AI classification and still produced a 150-article briefing.
- **Probable root cause:** the key value in `.env` is expired/invalid (verified my extraction is byte-correct: raw `.env` key == exported, `sk-ant-…`, len 71).
- **Recommended fix:** provide a valid `ANTHROPIC_API_KEY` in the staging/prod environment; re-run the cycle to validate AI summaries/classification quality.
- **Risk:** Medium — AI summaries are core to the product's value; ship-blocking for the *AI* claim until validated. Collection/dedup/briefing still function without it (good resilience).

### DEF-S2 — `DATABASE_URL` must be exported (not just `.env`) or DB engine uses a bad fallback
- **Severity:** Medium (High for local/staging ops) · **Type:** Framework (shared `app/core/database.py`, **out of FCC Bulletin scope**)
- **Steps to reproduce:** boot with `DATABASE_URL` only in `.env` (not exported).
- **Expected:** app connects to the configured DB.
- **Observed:** `database.py` reads `os.getenv("DATABASE_URL")` (not settings/.env), fell back to `postgres:postgres@localhost:5432/railway`, auth-failed 7×, ran **memory-only**; bulletin store unavailable, no tables created.
- **Probable root cause:** shared engine factory uses `os.getenv` rather than the pydantic `settings` that loads `.env`.
- **Recommended fix:** ensure deploy environments **export** `DATABASE_URL` (Railway already sets real env vars, so production is unaffected). **Do not modify the shared framework as part of the FCC Bulletin release.**
- **Risk:** Low for prod (env vars set by platform); High for any local/staging run that relies on `.env` only.

### DEF-S3 — No email credential → delivery is dry-run only
- **Severity:** Medium · **Type:** Config
- **Steps to reproduce:** `POST /send/fcc/{briefing_id}` with no `SENDGRID_API_KEY`.
- **Expected (for a real send):** email delivered to the recipient.
- **Observed:** `status:dry_run, recipients:1` — no real email sent; handled gracefully (no crash).
- **Probable root cause:** no email sending credential configured (delivery uses an `httpx` HTTP call; the `sendgrid` SDK is not installed).
- **Recommended fix:** configure the email credential/endpoint in staging; re-test real send, rendering, and retry.
- **Risk:** Medium — daily delivery is the operational deliverable; must be validated before production.

### DEF-S4 — Audit does not capture `/run`-triggered or scheduled cycles
- **Severity:** Low · **Type:** Limitation (by design)
- **Steps to reproduce:** `POST /run/fcc` with `BULLETIN_AUDIT_ENABLED=true`; check `/audit`.
- **Expected (per UAT wording):** all protected/collection actions audited.
- **Observed:** `bulletin_audit_log`=0 after `/run`; but after `POST /send` (an audited route) a row appeared (`event_type:delivery, action:send, result:ok`). Audit hooks exist only on `/collect`, `/send`, `/approve`, `/purge`.
- **Probable root cause:** audit hooks are wired at specific route handlers, not inside `run_daily_cycle`; the scheduler/`/run` path is instrumented (run log) but not audited.
- **Recommended fix (future, out of this release's scope):** add an audit event inside `run_daily_cycle` (or on `/run`) if collection-cycle auditing is required. Documented as a known limitation.
- **Risk:** Low — instrumentation (`/runs`) already records every cycle; audit covers explicit API actions.

---

## Observations (not defects)

- **Cycle duration 254 s is not a valid benchmark** — it was inflated by the AI 401 retry storm (DEF-S1). A healthy-key run should be re-timed.
- **Positive resilience:** the engine produced a complete briefing despite total AI failure (fallback classification) — no crash, graceful degradation.
- **Coverage % honesty held under real data:** `pending_instrumentation` with an empty registry (even with 125 outcomes), and a correctly-computed `100.0%` only after seeding real sources.

## Fixes implemented this session
**None.** No verified defect lies inside the FCC Bulletin code. DEF-S1/S3 are config, DEF-S2 is shared framework (out of scope; prod unaffected), DEF-S4 is a documented by-design limitation. No speculative changes were made.
