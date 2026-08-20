# PPEF Ingestion Job Orchestration

**Status:** implemented, dev-only as of 2026-08-20
**Scope:** CMS PPEF quarterly snapshot loads (ENROLLMENT, PRACTICE_LOCATION, ADDITIONAL_NPIS, SECONDARY_SPECIALTY, REASSIGNMENT)

---

## 1. The defect this replaces

Ingestion ran inside a FastAPI `BackgroundTask`. A background task is an object
on the worker's event loop: it lives and dies with the process. When the Azure
App Service container recycled mid-load — a routine event, not a fault — the
task simply stopped existing.

What made this a defect rather than an inconvenience is what it left behind:

```
ingest_status = 'pending'
error         = NULL
```

Five such rows accumulated on dev. Nothing was working on them, nothing had
reported a failure, and nothing distinguished them from a load that had started
thirty seconds ago. **The bug was not that the work stopped. The bug was that
the record did not say so.**

A retry was also impossible without a human first deciding, by eye, which
`pending` rows were dead.

---

## 2. Division of responsibility

| Component | Holds | Does not hold |
|---|---|---|
| **APScheduler** (`ppef_scheduler.py`) | triggers, polling cadence | any job state |
| **Postgres** (`tefca_ppef_ingest_jobs`) | every state fact, every heartbeat, every failure reason | — |

APScheduler's default `MemoryJobStore` dies with the process. That is precisely
the failure being fixed, so it is deliberately **not** used to store anything.
The scheduler asks the database questions; it never remembers answers. This
mirrors the Bulletin Intelligence watchdog, which recovers by asking "does
today's briefing exist?" rather than by remembering that it ran.

`SQLAlchemyJobStore` was considered and rejected: it would make APScheduler's
internal job records a second, parallel source of truth about work that the
application already tracks in its own table, with no way to express the
domain-specific facts (checksum, row count, quarter, truncation) that a
determination is later defended with. A test asserts it stays absent.

---

## 3. Lifecycle

```
QUEUED ──claim──▶ STARTED ──▶ DOWNLOADING ──▶ VALIDATING ──▶ LOADING ──▶ VALIDATING ──▶ COMPLETE
                     │             │              │              │            │
                     └─────────────┴──────────────┴──────────────┴────────────┴──▶ FAILED
```

`VALIDATING` legitimately appears twice, and the repeat is not a bug:

1. **before LOADING** — the CSV header is schema-checked as parsing begins;
2. **after LOADING** — the post-load gate compares parsed rows to written rows
   and confirms join keys are populated.

Every transition is `COMMIT`ted before the work that follows it. A process
killed at any point therefore leaves a truthful record of how far it got.

### Terminal states release the slot

`finish_complete` and `finish_failed` both set `active_marker = NULL`. That is
what permits a clean retry after a failure, and what lets the next quarter be
ingested.

---

## 4. Concurrency is enforced by the database

```sql
CREATE UNIQUE INDEX uq_ppef_job_active_component
    ON tefca_ppef_ingest_jobs (component, resource_version, active_marker)
 WHERE active_marker IS TRUE;
```

A check-then-insert has a window between the `SELECT` and the `INSERT` in which
a second caller passes the same check. The partial unique index has no such
window. Two callers racing produce one job and one `IntegrityError`, which
`queue_job` translates to `JobConflict` → HTTP 409. It never retries; retrying
would defeat the guard.

The index is **partial** on purpose. A full unique index would also forbid a
second *completed* load of the same quarter, which would make retry after
failure impossible — a guard that causes its own outage. `NULL`s do not collide,
so history accumulates freely while at most one *active* job per
(component, quarter) can exist.

Claiming uses `SELECT ... FOR UPDATE SKIP LOCKED`, so two pollers take different
jobs rather than both taking the same one.

---

## 5. Heartbeat and reaping — the centre of the design

**A process that dies cannot report that it died.** The only signal a dead
worker emits is silence. Liveness therefore has to be inferred from the absence
of a recent write.

| Constant | Value | Why |
|---|---|---|
| `HEARTBEAT_INTERVAL_SECONDS` | 20 | bounds DB churn on a 3.9M-row load |
| `STALE_HEARTBEAT_SECONDS` | 300 | 15 consecutive missed beats before death is assumed |
| `POLL_INTERVAL_SECONDS` | 20 | how often the queue is checked |
| `REAP_INTERVAL_SECONDS` | 60 | how often silence is checked for |

The 15× margin means a slow CMS response is never mistaken for a dead worker. A
test asserts the margin stays at 5× or better.

The reaper marks a stale job `FAILED` with a reason naming the last heartbeat
and the threshold, and marks its half-filled snapshot `failed` too — so nothing
downstream can mistake it for a completed load. **It never downgrades a snapshot
already at `complete`**: that snapshot is evidence, and a race between the
completion write and the reaper must not destroy a good load.

A startup reap runs 30 seconds after boot, because a deployment or crash is
precisely when jobs are orphaned; waiting a full interval would leave them
looking alive.

---

## 6. Activation gate

A snapshot becomes readable evidence — `ingest_status = 'complete'` — only after
**all** of:

- SHA-256 checksum present over the bytes actually ingested
- schema validated (header matched `EXPECTED_FIELDS`)
- row count > 0
- **parity**: rows parsed == rows written
- **relational validation**: `ENRLMT_ID` populated, plus `RCV_BNFT_ENRLMT_ID`
  for REASSIGNMENT (the key Amendment 5 traversal depends on — without it every
  row count would still look right while entity→practitioner traversal was
  silently broken)

A FAILED, stale, partial or abandoned job never produces evidence. Partial loads
are **not** salvaged: a half-loaded quarter is not a smaller quarter, it is a
misleading one.

Partial-file resume is deliberately out of scope for this phase.

---

## 7. Idempotency

`POST /ppef/snapshots/ingest` first discovers the current CMS quarter, then
checks for an existing `complete`, **untruncated** snapshot at that
`resource_version`. If one exists it returns `ALREADY_LOADED` and queues
nothing — re-downloading 3.9M rows to produce a byte-identical result is cost
without information.

Truncated snapshots are excluded from that check. A capped load is a different
artefact; treating it as "already loaded" would permanently block the real one
and every status display would report success.

`force=true` queues anyway, for a deliberate re-ingest.

If discovery fails the job is still queued (the runner discovers again) but the
idempotency check is skipped, because "already loaded" cannot be decided without
knowing which quarter.

---

## 8. Endpoints

| Method | Path | Role | Returns |
|---|---|---|---|
| POST | `/api/tefca/ppef/snapshots/ingest` | admin | 202 + `job_id`, or `ALREADY_LOADED`, or 409 |
| GET | `/api/tefca/ppef/snapshots/ingest/{job_id}` | viewer | persisted job state |
| GET | `/api/tefca/ppef/jobs` | viewer | recent jobs + scheduler status |

Starting a load is admin-only; **observing** one is viewer-readable, because the
operators who need to see a stuck load are not all admins.

Status is read from the database, never from process memory, and reports
`heartbeat_age_seconds` / `heartbeat_stale` so an operator can see a worker has
gone quiet *before* the reaper formally fails the job.

---

## 9. MULTI-WORKER FUTURE GUARD

**Today: one gunicorn worker.** Nothing enforces this — there is no `--workers`
flag and no `WEB_CONCURRENCY` setting; one worker is simply the default.

If the deployment ever scales out, **every worker starts its own
`AsyncIOScheduler` and polls the same queue.** APScheduler 3.x provides no
distributed coordination of its own.

What already holds under multiple workers:

- **Duplicate execution is prevented by the database.** `FOR UPDATE SKIP LOCKED`
  means two pollers claim different jobs; the partial unique index means two
  queuers cannot both create an active job for the same component and quarter.
- **The reaper is idempotent.** Two reapers marking the same stale job `FAILED`
  produce the same row.

What still needs review before scaling out:

1. **Poller multiplication.** N workers means N pollers hitting the queue every
   20s. Harmless at N=2, wasteful at N=20. Consider gating the scheduler behind
   an env flag set on one box (the pattern Bulletin Intelligence already uses
   for `ENABLE_SCHEDULER`) or moving to an advisory-lock leader election.
2. **Memory.** Each concurrently running job spools a file and batches rows. Two
   workers each loading a different component doubles the footprint. The current
   `_poll_tick` runs one job at a time *per worker*, not per cluster.
3. **The reaper threshold vs. deploy time.** A rolling deploy that takes longer
   than `STALE_HEARTBEAT_SECONDS` will have jobs reaped mid-deploy. That is the
   correct outcome (they genuinely stopped), but it means a deploy during a load
   costs a re-ingest.

The PPEF scheduler starts **unconditionally**, unlike the bulletin scheduler.
This is deliberate: bulletin is gated because two boxes running it would email
subscribers duplicate briefings — a side effect that cannot be taken back. PPEF
has no such hazard, and the reaper is what recovers jobs orphaned by the recycle
that just happened, so it must run on every box that comes up.

---

## 10. Measured behaviour on dev (2026-08-20)

### Kill test — an actual container restart, twice

| | Kill 1 (ENROLLMENT) | Kill 2 (REASSIGNMENT) |
|---|---|---|
| killed in state | LOADING | LOADING |
| restart issued | 08:28:46Z | 10:09:16Z |
| last heartbeat written | 08:31:15Z | 10:09:04Z |
| `heartbeat_stale` visible to operator | 08:36:25Z | 10:14:10Z |
| reaper marked FAILED | 08:37:12Z | 10:14:58Z |
| detection latency from last heartbeat | 5m57s | 5m54s |

Both land inside the design bound of `STALE_HEARTBEAT_SECONDS` (300s) + one reap
interval (60s) = 360s max.

Note the gap between "restart issued" and "last heartbeat": Azure's graceful
shutdown let the old worker keep writing for ~2.5 minutes after the restart
command returned. The mechanism measures the worker's actual silence, not the
operator's intent, which is the correct thing to measure.

In both cases the snapshot was marked `failed`, never reached `complete`, and an
existing good snapshot for the same component was left untouched.

### A full load, end to end

ENROLLMENT — never previously loaded — ran through the new mechanism to
COMPLETE: 2,978,925 rows, SHA-256 `e5d362addc825ced…`, 1h25m50s, one attempt.
Active evidence on dev is now 8,578,709 rows across all five components, every
one `complete` and untruncated.

### Load cost is index-bound, and batch time GROWS

Download took ~26s; the remaining 85 minutes were COPY into
`tefca_ppef_records`. Observed batch time (50,000 rows) rose from ~48s early to
~68s late as the table's four indexes filled.

**This is the one number to watch.** The reaper kills a job whose heartbeat is
older than 300s, and heartbeats are emitted per batch. At ~68s per batch the
margin is ~4.4x — comfortable, but it is a margin that *shrinks as the table
grows*. If batch time ever approaches 300s the reaper would kill a live load.
Before this runs against a materially larger dataset, either raise
`STALE_HEARTBEAT_SECONDS`, lower `BATCH_ROWS`, or emit heartbeats from inside
`copy_records` rather than between batches.

### Poller noise

While a long job runs, APScheduler logs `maximum number of running instances
reached (1)` every 20s. That is the serial-execution design working, not a
fault, but it is ~180 WARNING lines per hour of loading. Raising
`POLL_INTERVAL_SECONDS` to 60 would cut it 3x at the cost of up to 60s of extra
queue latency.

---

## 11. Cleaning up the pre-existing stuck rows

`scripts/cleanup_stuck_ppef_snapshots.py` closes snapshots orphaned before this
mechanism existed. It marks them `failed` with reason
`worker_recycled_before_completion` and deletes their orphaned record rows — but
**preserves the snapshot rows themselves**, which are the record that a load was
attempted. Dry-run by default; only touches `pending` rows older than 2 hours,
so a load genuinely in flight is never killed by maintenance.
