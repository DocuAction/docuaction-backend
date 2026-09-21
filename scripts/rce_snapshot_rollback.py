"""Compensating rollback of ONE delivery snapshot's relationship changes.

    python scripts/rce_snapshot_rollback.py --intake <uuid> --plan
    python scripts/rce_snapshot_rollback.py --intake <uuid> --apply \
        --actor <email> --role program_manager --reason "<why>"

This is DATA COMPENSATION, distinct from an Alembic schema downgrade (which
refuses when evidence rows exist and never touches tefca_entity_relationships)
and from an application/image rollback (which leaves already-applied data as
it is). It uses the snapshot's own relationship-observation evidence:

  * replacement edges the snapshot ASSERTED are retired (end_date = today,
    status = rolled_back) and evidenced ROLLED_BACK;
  * edges the snapshot SUPERSEDED are restored as current (end_date = NULL,
    status = active) and evidenced RESTORED;
  * a ROLLED_BACK row is appended to the snapshot chain and a registry audit
    row records actor, role, reason, intake, build SHA and correlation id.

It FAILS CLOSED (nothing changes) when a later APPROVED snapshot exists, the
evidence is incomplete, the scope does not match, the prior edge is ambiguous,
or a restore would leave two active parents. It is IDEMPOTENT: a repeated
`--apply` changes nothing. `--plan` never writes.

Never run against PROD without the runbook (docs/rce/RELATIONSHIP_ROLLBACK_RUNBOOK.md).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _main(args) -> int:
    from app.core.database import _normalize_url
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.tefca_registry.rce import relationship_history as rh

    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        try:
            result = await rh.compensate_snapshot(
                db, uuid.UUID(args.intake), actor=args.actor or "OPERATOR",
                role=args.role, reason=args.reason or "", apply=bool(args.apply))
        except rh.RollbackRefused as exc:
            print(json.dumps({"refused": True, "reason": str(exc)}, indent=1))
            return 2
        print(json.dumps(result, indent=1, default=str))
    await engine.dispose()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--intake", required=True, help="the delivery (rce_source_intakes.id) whose snapshot to roll back")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="read-only plan")
    g.add_argument("--apply", action="store_true", help="apply the compensation")
    ap.add_argument("--actor", help="operator email (required with --apply)")
    ap.add_argument("--role", default="program_manager", help="operator role (Data Operations or above)")
    ap.add_argument("--reason", help="why (required with --apply)")
    args = ap.parse_args()
    if args.apply and not (args.actor and args.reason):
        ap.error("--apply requires --actor and --reason")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
