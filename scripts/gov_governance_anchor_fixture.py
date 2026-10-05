"""Create the ARC-9.99.777.91 synthetic governance anchor delivery.

WORKBOOK CONTEXT (DA-ARC-2026-051, GOV-003..014)
-------------------------------------------------
Ten governance workbook cases all failed with the identical note "PENDING
synthetic ARC-9.99.777.91 / intake 3a1070df missing; no substitute". This
script creates a delivery with that exact official ARC label
(`9.99.777.91`), following the SAME convention already used by
`tests/test_snapshot_governance.py` (`ARC = "9.99.777.95"`) and
`tests/rce_traceability_support.py` (`make_rows`, `seed_intake`,
`run_quality_and_curation`) -- reusing the real ingestion/quality/curation/
promotion code paths rather than hand-writing rows into the database.

WHAT THIS DOES **NOT** DO, AND WHY
-----------------------------------
It cannot reproduce intake id `3a1070df-...` literally. Every intake id in
this codebase is a randomly generated UUID at the moment of insertion
(`seed_intake`: `intake_id = uuid.uuid4()`; the real
`POST /api/tefca/rce/official-deliveries` endpoint does the same) -- there
is no deterministic derivation from the ARC label anywhere in this repo.
`3a1070df` was most likely simply the UUID a PRIOR run of this exact
scenario happened to receive, recorded by the tester, and never persisted
anywhere this script can read back. Re-running this script, here or on DEV,
will mint a NEW, different intake id every time -- that is correct and
expected. What is reproducible, and is the actual anchor GOV-003..014 need,
is the delivery's ARC/official-identifier label (`9.99.777.91`) and its
content, byte-identical on every run.

USAGE
-----
    DATABASE_URL=postgresql+asyncpg://... python scripts/gov_governance_anchor_fixture.py

Prints the resulting intake_id (and job_id) on success. Each run creates a
NEW intake row (a new random intake_id every time -- this script does not
check for an existing one; "was this already run" is a governance/test-
environment question an operator should answer deliberately). Because the
row content is deterministic, a SECOND run's TEFCAID/HCID values are
recognised by the identifier registry as already registered by the first
run's promotion -- `entities_created` reads 0 and `entities_matched` reads
5 on a repeat, not a second set of new entities (verified:
tests/test_gov_governance_anchor_fixture.py). That is correct, intentional
registry behaviour, not a bug in this script.

SYNTHETIC / NOT GOVERNMENT DATA. Refuses to run with ENVIRONMENT=production,
same guard `scripts/qa_fixture_pack.py` and `scripts/seed_qa_test_data.py` use.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

ARC = "9.99.777.91"


async def _main() -> int:
    if (os.environ.get("ENVIRONMENT") or "").strip().lower() == "production":
        print("REFUSED: ENVIRONMENT=production. This script writes synthetic "
              "governance-anchor data and must never run against PROD.", file=sys.stderr)
        return 2

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import _normalize_url
    from app.tefca_registry.rce.promotion import promote_delivery
    from rce_traceability_support import make_rows, run_quality_and_curation, seed_intake

    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        rows = make_rows(5, arc=ARC)
        intake_id, job = await seed_intake(db, rows)
        await db.commit()

        quality, curated = await run_quality_and_curation(db, intake_id)
        await db.commit()

        promoted = await promote_delivery(db, intake_id, actor="gov-anchor-fixture")
        await db.commit()

        print(f"intake_id={intake_id}")
        print(f"job_id={job.id if job else None}")
        print(f"arc={ARC}")
        print(f"rows={len(rows)}")
        print(f"quality_run_summary={quality}")
        print(f"curated_records={curated.get('curated_records')}")
        print(f"entities_created={promoted.get('entities_created')}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
