#!/usr/bin/env python
"""
Close out PPEF snapshots that were orphaned by a recycled worker.

WHY THESE ROWS EXIST
Ingestion used to run in a FastAPI BackgroundTask, which lives and dies with its
worker. When the App Service container recycled mid-load the task vanished and
left the snapshot at `ingest_status = pending` with `error = None`. Five such
rows accumulated on dev. Nothing was working on them and nothing said so.

WHAT THIS DOES AND DELIBERATELY DOES NOT DO
It marks them `failed` and writes the reason. It does NOT delete them: the row
is the record of what happened, and deleting it would remove the only evidence
that a load was attempted. The `pending` -> `failed` change is what makes the
history truthful, not what makes it shorter.

THE GAP THIS ALSO CLOSES
The in-app reaper (`reap_stale_jobs` / `close_orphaned_snapshots`) marks a dead
load `pending` -> `failed` and deliberately does NOT delete its partial RECORD
rows: a DELETE racing rows another transaction is still inserting is how a
reaper corrupts a live load. It leaves them "to the out-of-band cleanup script"
— this one.

But this script used to select on `ingest_status == 'pending'` ONLY. So the
moment the reaper did its job, the snapshot left this script's field of view
forever, and its orphaned rows became unreclaimable by any automated path. The
two halves of the mechanism did not compose. On dev that stranded 3,450,000
rows under three `failed` snapshots — rows no process would ever collect.

So there are two passes, and the second is the fix:

  PASS 1  `pending` and older than the cutoff -> mark `failed`, delete its rows.
  PASS 2  ALREADY `failed` and older than the cutoff -> delete its rows.
          The snapshot row is not touched; it is already truthful. Only the
          loadable-looking garbage underneath it is collected.

It is narrowly scoped by design:

  * a `complete` snapshot is evidence and is never rewritten or emptied — it is
    excluded from both passes;
  * only snapshots OLDER than --older-than-hours (default 2) are touched, so a
    load genuinely in flight is not killed by a maintenance script;
  * pass 2 additionally skips any snapshot still claimed by a NON-TERMINAL job.
    A `failed` snapshot with a live job means a retry is running against it, and
    its rows belong to that retry, not to this script;
  * no snapshot row is ever deleted, in either pass. The row is the record that
    a load was attempted, and deleting it would remove the only evidence of it.

Going forward pass 1 should rarely fire: the durable job table plus the
stale-job reaper closes those automatically. Pass 2 is the standing collector
for what the reaper is not allowed to touch.

USAGE
    python scripts/cleanup_stuck_ppef_snapshots.py                  # dry run
    python scripts/cleanup_stuck_ppef_snapshots.py --confirm
    python scripts/cleanup_stuck_ppef_snapshots.py --confirm --allow-prod
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REASON = "worker_recycled_before_completion"


async def run(confirm: bool, allow_prod: bool, older_than_hours: int) -> int:
    from sqlalchemy import delete, func, select

    from app.core.database import async_session_maker
    from app.Tefca.models import (TEFCAPPEFIngestJob, TEFCAPPEFRecord,
                                  TEFCAPPEFSnapshot)

    environment = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").lower()
    if environment in {"production", "prod"} and not allow_prod:
        print("REFUSING: ENVIRONMENT is production and --allow-prod was not given.")
        return 2

    cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)
    mode = "APPLY" if confirm else "DRY RUN (nothing written)"
    print(f"stuck PPEF snapshot cleanup — {mode}")
    print(f"environment: {environment or '(unset)'}")
    print(f"closing `pending` snapshots created before {cutoff.isoformat()}Z")
    print("-" * 78)

    async with async_session_maker() as db:
        # PASS 1 — still `pending`: no one ever closed it out.
        stuck = (await db.execute(
            select(TEFCAPPEFSnapshot)
            .where(TEFCAPPEFSnapshot.ingest_status == "pending")
            .where(TEFCAPPEFSnapshot.ingested_at < cutoff)
            .order_by(TEFCAPPEFSnapshot.ingested_at.asc())
        )).scalars().all()

        # PASS 2 — already `failed`: the reaper closed it and, correctly, left
        # its partial rows behind. Collecting them is this script's job and
        # nothing else's. See "THE GAP THIS ALSO CLOSES" above.
        failed = (await db.execute(
            select(TEFCAPPEFSnapshot)
            .where(TEFCAPPEFSnapshot.ingest_status == "failed")
            .where(TEFCAPPEFSnapshot.ingested_at < cutoff)
            .order_by(TEFCAPPEFSnapshot.ingested_at.asc())
        )).scalars().all()

        # A `failed` snapshot with a live job is a retry in progress. Its rows
        # belong to that retry. Terminal jobs do not protect anything.
        if failed:
            live = set((await db.execute(
                select(TEFCAPPEFIngestJob.snapshot_id)
                .where(TEFCAPPEFIngestJob.snapshot_id.isnot(None))
                .where(TEFCAPPEFIngestJob.state.notin_(
                    list(TEFCAPPEFIngestJob.TERMINAL_STATES)))
            )).scalars().all())
            for snap in [s for s in failed if s.id in live]:
                print(f"  {str(snap.id)[:8]}  {snap.component:22} "
                      f"SKIPPED — a non-terminal job still claims this snapshot")
            failed = [s for s in failed if s.id not in live]

        if not stuck and not failed:
            print("no stuck snapshots and no orphaned rows found — nothing to do.")
            return 0

        reclaimed = 0
        collected = 0

        if stuck:
            print("PASS 1 — `pending` snapshots to close:")
        for snap in stuck:
            orphaned = await db.scalar(
                select(func.count()).select_from(TEFCAPPEFRecord)
                .where(TEFCAPPEFRecord.snapshot_id == snap.id))
            reclaimed += orphaned or 0
            print(f"  {str(snap.id)[:8]}  {snap.component:22} "
                  f"created={snap.ingested_at}  orphaned_rows={orphaned}")
            if confirm:
                snap.ingest_status = "failed"
                snap.error = REASON
                # Partial rows under a failed snapshot are not a smaller
                # dataset; they are a misleading one. The snapshot row stays.
                await db.execute(delete(TEFCAPPEFRecord)
                                 .where(TEFCAPPEFRecord.snapshot_id == snap.id))

        header_done = False
        for snap in failed:
            orphaned = await db.scalar(
                select(func.count()).select_from(TEFCAPPEFRecord)
                .where(TEFCAPPEFRecord.snapshot_id == snap.id))
            if not orphaned:
                continue
            if not header_done:
                print("PASS 2 — already-`failed` snapshots with rows still under them:")
                header_done = True
            reclaimed += orphaned
            collected += 1
            print(f"  {str(snap.id)[:8]}  {snap.component:22} "
                  f"created={snap.ingested_at}  orphaned_rows={orphaned}")
            if confirm:
                # The snapshot row is NOT rewritten. It is already `failed` and
                # already carries the reason the reaper recorded — which is more
                # specific than this script's generic one and must not be
                # overwritten with something vaguer.
                await db.execute(delete(TEFCAPPEFRecord)
                                 .where(TEFCAPPEFRecord.snapshot_id == snap.id))

        if confirm:
            await db.commit()

    print("-" * 78)
    print(f"snapshots {'closed' if confirm else 'that would be closed'} (pass 1): {len(stuck)}")
    print(f"snapshots {'collected' if confirm else 'that would be collected'} (pass 2): "
          f"{collected}")
    print(f"reason written to pass-1 snapshots: {REASON}")
    print(f"orphaned RECORD rows {'deleted' if confirm else 'that would be deleted'}: "
          f"{reclaimed:,}")
    # Say which rows survive and which do not. The previous wording here read
    # "records PRESERVED", which described the exact opposite of what the code
    # does: RECORD rows are what gets deleted, SNAPSHOT rows are what stays. An
    # operator reading that line before typing --confirm was told the deletion
    # would not happen.
    print("SNAPSHOT rows preserved — they document the attempts and are never deleted.")
    if not confirm:
        print("\nDRY RUN — re-run with --confirm to apply.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm", action="store_true",
                        help="actually write changes (default is a dry run)")
    parser.add_argument("--allow-prod", action="store_true",
                        help="required when ENVIRONMENT=production")
    parser.add_argument("--older-than-hours", type=int, default=2,
                        help="only close snapshots older than this, so a load "
                             "genuinely in flight is never killed (default 2)")
    args = parser.parse_args()
    return asyncio.run(run(args.confirm, args.allow_prod, args.older_than_hours))


if __name__ == "__main__":
    raise SystemExit(main())
