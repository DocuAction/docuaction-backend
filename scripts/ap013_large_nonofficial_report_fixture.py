"""AP-013: a large, clearly non-official synthetic report, for exercising
AP-001's fix (GET /reports/{report_id} summarising long lists instead of
transferring them -- app/reports/routes.py, _DATASET_LIST_PREVIEW_LIMIT) at
a scale the small day-to-day test fixtures never reach.

Workbook note: "No large non-official HTML; did not Generate". The OFFICIAL
job (0930826c, 24,589 rows) must never be touched, re-uploaded, or
regenerated for a test -- this creates a SEPARATE, large, unmistakably
synthetic delivery (ARC 9.99.777.92, --rows synthetic records) and generates
a real `delivery_processing` report from it through the normal
`generate_report()` path, so the report is a genuine stored artifact with a
genuinely large dataset, not a hand-crafted blob.

USAGE
-----
    DATABASE_URL=postgresql+asyncpg://... python scripts/ap013_large_nonofficial_report_fixture.py --rows 2000

Prints the intake_id, job_id and report_id. Refuses ENVIRONMENT=production,
same guard every other fixture script in this directory uses.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

ARC = "9.99.777.92"


async def _main(rows_n: int) -> int:
    if (os.environ.get("ENVIRONMENT") or "").strip().lower() == "production":
        print("REFUSED: ENVIRONMENT=production.", file=sys.stderr)
        return 2

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import _normalize_url
    from app.tefca_registry.rce.promotion import promote_delivery
    from app.reports.generator import generate_report
    from rce_traceability_support import make_rows, run_quality_and_curation, seed_intake

    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        rows = make_rows(rows_n, arc=ARC)
        intake_id, job = await seed_intake(db, rows)
        await db.commit()
        await run_quality_and_curation(db, intake_id)
        await db.commit()
        await promote_delivery(db, intake_id, actor="ap013-fixture")
        await db.commit()

        result = await generate_report(
            db, report_type="delivery_processing", generated_by="ap013-fixture",
            query_parameters={"job_id": str(job.id)})

        print(f"intake_id={intake_id}")
        print(f"job_id={job.id}")
        print(f"report_id={result['report_id']}")
        print(f"rows={rows_n}")
        print("NOT OFFICIAL -- synthetic fixture, distinct from job 0930826c")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=500,
                   help="synthetic record count (default 500; large enough to "
                        "exceed _DATASET_LIST_PREVIEW_LIMIT=25 by a wide margin)")
    args = ap.parse_args()
    sys.exit(asyncio.run(_main(args.rows)))
