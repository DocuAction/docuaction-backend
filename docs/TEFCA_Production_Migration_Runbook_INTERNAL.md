# TEFCA ARC — Production Migration Runbook

**Internal engineering record. Not a Government deliverable. No secrets, no
credentials, no Government values.**
Contract 7571MN26F80064 · Step #18 · 31 August 2026

**NOTHING IN THIS DOCUMENT HAS BEEN EXECUTED.** It is a procedure prepared in
advance so that a later authorized deployment is deliberate and attributable
rather than improvised.

---

## 0. Authority

This runbook does not itself authorize anything. A production deployment
requires a named approver and a named release. Neither is chosen here.

**Do not begin if any PRECHECK item is unresolved.** A precheck that is
"probably fine" is a precheck that failed.

---

## 1. PRECHECK

| # | Item | How to satisfy it | Blocking |
|---|---|---|---|
| 1 | Approved release ref | A named tag or commit, approved by the release owner. Not "latest", not a branch. | Yes |
| 2 | Backup verified | A recovery point later than the last write, **and** a restore rehearsal completed at least once (readiness matrix #21). | Yes |
| 3 | Migration head | Repo head equals the single Alembic head; the PROD head recorded before the run. | Yes |
| 4 | Ownership roles | PROD runtime role confirmed as the application role, not an administrator. Area 1 grants verified. | Yes |
| 5 | Secrets | Every secret resolved through a Key Vault reference, or an explicit accepted-risk record signed by the release owner. | Yes |
| 6 | Key Vault | App identity holds Key Vault Secrets User; the vault is reachable from the app subnet. | Yes |
| 7 | Artifact storage | A durable store configured, or the release explicitly accepts that artifacts are ephemeral. | Yes |
| 8 | Monitoring | Alerts enabled, with a receiver a human actually reads. | Yes |
| 9 | Government authorization state | Recorded as-is. **This runbook never sets it.** | Yes |
| 10 | Expected PROD data state | Recorded before the change, so "unchanged" can be proven after. | Yes |

Record every value. A precheck nobody wrote down did not happen.

---

## 2. DEPLOYMENT

1. **Freeze.** Announce the window. No other change lands during it.
2. **Capture the rollback point** — current image digest, current Alembic head,
   current app-setting *names*, current instance count.
3. **Image.** Deploy by **digest**, never by a moving tag. PROD is already
   digest-pinned; keep it that way.
4. **Migration.** Run Alembic as the **owner** role, not the runtime role, as a
   separate step before the application starts. Startup schema mutation stays
   disabled — the guard refuses it in production, and since Step #18 also on a
   deployed host whose `ENVIRONMENT` has gone missing.
5. **Application.** Update the site, then wait for the health probe.
6. **Health.** `/health` must pass before traffic is considered restored.

---

## 3. VALIDATION

Run in this order. Stop at the first failure.

| Check | Pass condition |
|---|---|
| Authentication | A known account signs in; an unknown one does not |
| RBAC | A viewer cannot produce an export; a QA lead can |
| Database roles | `SELECT current_user` returns the runtime role, not an administrator |
| Area 1 controls | DELETE, TRUNCATE and DDL are refused on the Area 1 tables |
| Reports | A report renders and states its classification |
| Artifacts | A download re-hashes and matches its registered digest |
| Export | A synthetic export job reaches SUCCEEDED and its artifact downloads |
| Health | `/health` returns healthy; the platform probe agrees |
| Monitoring | An alert fires on a deliberately induced synthetic condition, or is confirmed enabled with a live receiver |
| Data state | Environment, identity and authorization report the expected values |

---

## 4. ROLLBACK

**Application.** Redeploy the previous image digest. This is the fast path and
the default response to any validation failure.

**Migration.** Alembic downgrade is **not** assumed safe. Each migration carries
its own downgrade, but a downgrade that drops a column loses the data in it. The
safe rollback for a schema change is **restore from the recovery point captured
in PRECHECK #2** — which is why that precheck is blocking.

**Configuration.** Reapply the captured settings. Names were captured; values
come from Key Vault or the release owner, never from this document.

**After any rollback: re-run VALIDATION.** A rollback is a deployment.

---

## 5. STOP CONDITIONS

Stop immediately, roll back, and escalate if:

* the runtime connects as a database administrator;
* Alembic proposes a migration nobody reviewed;
* the application starts creating tables;
* Area 1 counts or the corpus digest change;
* a report or export is classified GOVERNMENT while the authorization marker is
  absent;
* an artifact download fails its integrity check;
* health does not pass within the agreed window.

The last is a judgement call. The others are not.
