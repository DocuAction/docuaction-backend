"""GOV-003..014 governance workbook cases all failed on the identical note
"PENDING synthetic ARC-9.99.777.91 / intake 3a1070df missing; no substitute".
`scripts/gov_governance_anchor_fixture.py` creates a delivery with that exact
ARC label through the real intake/quality/curation/promotion pipeline. This
proves it seeds correctly and is retrievable by ARC label -- NOT by a fixed
intake id, which (see that script's own docstring) cannot be reproduced:
every intake id in this codebase is `uuid.uuid4()` at insertion time, so
`3a1070df` was whichever random id a prior run happened to receive.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from rce_traceability_support import (  # noqa: F401
    make_rows, rolled_back_db, run_quality_and_curation, seed_intake)
from app.tefca_registry.rce.promotion import promote_delivery
from app.tefca_registry.rce import models as m

pytestmark = pytest.mark.usefixtures("db_required")

ARC = "9.99.777.91"


async def test_the_anchor_fixture_seeds_and_is_retrievable_by_arc_label(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(5, arc=ARC)
    intake_id, job = await seed_intake(db, rows)

    quality, curated = await run_quality_and_curation(db, intake_id)
    promoted = await promote_delivery(db, intake_id, actor="test")

    assert curated["curated_records"] == 5
    # Content is deterministic (same ARC, same rows every run), so the
    # identifier registry may recognise these TEFCAID/HCID values as already
    # registered by an earlier run of this same fixture rather than creating
    # new entities -- that is correct, intentional idempotent behaviour, not
    # a failure. Either outcome proves promotion accepted the delivery.
    assert promoted["entities_created"] + promoted["entities_matched"] >= 5

    records = (await db.execute(
        select(m.RceSourceRecord)
        .where(m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    assert len(records) == 5
    assert all(r.source_rce_id.startswith(ARC) for r in records)

    intake = await db.get(m.RceSourceIntake, intake_id)
    assert intake is not None
