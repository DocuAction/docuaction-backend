# FCC Bulletin v1.0 — Azure Migration Recommendations

**Date:** 2026-07-08 · **Scope:** forward-looking recommendations only (one page). **No implementation.** These are for the future Azure migration project, not the current release.

---

1. **Separate Development and Production databases.** The current shared PostgreSQL was an approved temporary choice; Azure should give each environment its own datastore so releases can be validated without touching production data.

2. **Independent deploy targets per environment.** Decouple Development and Production so `main` (or a release branch) can deploy to a non-production environment first, enabling pre-production validation and a promote-to-prod step.

3. **Staged promotion pipeline.** Introduce Dev → Staging → Production promotion with an approval gate before Production, replacing the current single-branch-to-both model.

4. **Environment-scoped secrets/keys.** Provision provider keys (`NEWSAPI_AI_KEY`, `NEWSAPI_KEY`, `TAVILY_API_KEY`, `ANTHROPIC_API_KEY`) per environment so non-production validation exercises all providers with isolated credentials.

5. **Deployment provenance surfaced by the app.** Expose the deployed git SHA (e.g. on `/health`) so "which commit is live" is verifiable without dashboard access — a gap felt directly during this release.

6. **Isolated validation environment on demand.** Support ephemeral/preview environments (own DB + keys) for release validation, discarded afterward.

7. **Observability & alerting.** Centralize logs/metrics and alert on recurring runtime errors (the watchdog error recurred hourly for hours before being addressed) and on per-provider collection health.

8. **Scheduler ownership clarity.** Run scheduled jobs (bulletin daily cycle, TEFCA QA monitor) on a designated single instance with clear env gating, avoiding duplicate execution across replicas.

9. **Backup & rollback for separated data.** Once databases are separated, define backup/restore and a data-aware rollback procedure (not needed today because no schema changes ship, but required once environments diverge).

10. **Migration sequencing.** Migrate datastore separation and environment decoupling first (they unblock everything else), then secrets isolation, then the staged pipeline and observability.

> These recommendations do not modify the current release and require no action now. They are inputs to the Azure migration project.
