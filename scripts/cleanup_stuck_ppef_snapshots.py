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

It is also narrowly scoped by design:

  * only `pending` snapshots are touched — a `complete` snapshot is evidence
    and is never rewritten;
  * only snapshots OLDER than --older-than-hours (default 2) are touched, so a
    load genuinely in flight is not killed by a maintenance script;
  * orphaned RECORD rows are removed for each closed snapshot, because a partial
    row set under a failed snapshot is loadable-looking garbage. The snapshot
    row itself, with its counts and its reason, stays.

Going forward this cleanup should not be needed: the durable job table plus the
stale-job reaper closes these automatically. This script exists for the rows
that predate that mechanism.

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
    from app.Tefca.models import TEFCAPPEFRecord, TEFCAPPEFSnapshot

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
        stuck = (await db.execute(
            select(TEFCAPPEFSnapshot)
            .where(TEFCAPPEFSnapshot.ingest_status == "pending")
            .where(TEFCAPPEFSnapshot.ingested_at < cutoff)
            .order_by(TEFCAPPEFSnapshot.ingested_at.asc())
        )).scalars().all()

        if not stuck:
            print("no stuck snapshots found — nothing to do.")
            return 0

        for snap in stuck:
            orphaned = await db.scalar(
                select(func.count()).select_from(TEFCAPPEFRecord)
                .where(TEFCAPPEFRecord.snapshot_id == snap.id))
            print(f"  {str(snap.id)[:8]}  {snap.component:22} "
                  f"created={snap.ingested_at}  orphaned_rows={orphaned}")
            if confirm:
                snap.ingest_status = "failed"
                snap.error = REASON
                # Partial rows under a failed snapshot are not a smaller
                # dataset; they are a misleading one. The snapshot row stays.
                await db.execute(delete(TEFCAPPEFRecord)
                                 .where(TEFCAPPEFRecord.snapshot_id == snap.id))

        if confirm:
            await db.commit()

    print("-" * 78)
    print(f"snapshots {'closed' if confirm else 'that would be closed'}: {len(stuck)}")
    print(f"reason written: {REASON}")
    print("records PRESERVED — the snapshot rows document the attempts and are not deleted.")
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
