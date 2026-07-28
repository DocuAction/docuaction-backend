# FCC Bulletin v1.0 — Lessons Learned

**Date:** 2026-07-08

---

## What went well

- **Strict module isolation paid off.** Confining every change to `app/bulletin_intelligence/` made the release safe to reason about: the impact analysis was a quick, evidence-backed "no cross-module effect," which is exactly what a shared-database environment needs.
- **Additive, backward-compatible design.** New `Article` fields serialized into the existing JSON-in-TEXT storage, so a substantial feature (provider tracking/analytics) landed with **no database migration** and no risk to other modules.
- **Honest measurement discipline.** Metrics were only reported when actually measured; unmeasured items were labeled "Pending Measurement" rather than estimated. This kept the release trustworthy for a federal contract.
- **Root-cause before fix.** The scheduler bug was reproduced against real APScheduler (exact error message) before fixing, and re-verified after — so the fix was proven, not assumed. A live-run validation also surfaced a real false-positive (an exec-hire) that unit tests alone had missed.
- **Feature-flag/kill-switch design.** `BULLETIN_EDITORIAL_STRICT` and key-gated collectors mean behavior can be reverted without a redeploy.

## What was hard / what we learned

- **Shared Dev/Prod pipeline blocks pre-production validation.** Because both services deploy from `main` and share one database, there is no way to validate the running code in Development without also deploying to Production. This is the single biggest process constraint and the main reason the release closed at "engineering complete" rather than "validated in Dev." (Accepted as a temporary, AGT-approved architecture; slated for the Azure migration.)
- **Deployment state must be verified, not assumed.** "Development is deployed" was reported, but the live Railway logs showed Dev still running the old commit with the watchdog error recurring. Verifying the deployed commit hash and logs directly caught this. Lesson: always confirm the *deployed* artifact, not just the *pushed* artifact.
- **Local validation can't cover key-gated providers.** NewsAPI.ai/Tavily/NewsAPI.org keys live only in Railway, so a complete multi-provider comparison is impossible locally. Lesson: plan for an environment that has the real keys and an isolated datastore for validation.
- **No ground-truth "missed story" instrument exists.** Absolute miss-rate can't be reported without an external reference feed (e.g. Talkwalker) diffed against the archive. Reports were scoped honestly around this gap.
- **`asyncio.get_event_loop()` is a trap on Python 3.10+.** Sync APScheduler jobs run in a thread pool with no event loop; calling `get_event_loop()` there raises. The durable pattern is coroutine jobs on `AsyncIOScheduler` (or `get_running_loop()` where a loop is guaranteed).

## Recommendations for the next phase (process, not architecture)

- Provide an isolated validation target with real provider keys before the next release requiring live validation (naturally addressed by the Azure migration).
- Add lightweight deployment-provenance (e.g. surface the git SHA on `/health`) so "which commit is live" is answerable without dashboard access.
- Consider per-provider timing instrumentation to complete the Provider Performance report (currently honest `null`).
- Wire an external reference-feed diff to convert "missed story" from structural analysis into a measured rate.
