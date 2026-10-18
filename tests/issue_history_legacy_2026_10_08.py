"""Synthetic LEGACY-intake shape (mimics the real DEV July/September situation).

July: untagged intake, completed run under rule set 1.0.0, issues recorded under
1.0.0, NO delivery job, NO per-record check results (the write flag was off).
September: untagged intake processed with the current rule set and persisted
results. Neither carries a feed tag, so history reads them only through the
read-only ISSUE_HISTORY_INTAKE_FEEDS mapping. Synthetic data only.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Dict

from sqlalchemy import text

from issue_history_support_2026_10_07 import (BAD_LEN_NPI, entity_row, filler_row,
                                              run_engine, seed_delivery)

LEG_FEED = "SYN-LEGACY"
LEG = {"BOTH": "9.99.777.100.61", "JULONLY": "9.99.777.100.62",
       "SEPONLY": "9.99.777.100.63"}


async def seed_legacy(db) -> Dict[str, str]:
    from app.core.config import settings

    async def delivery(label, received, rows, results):
        settings.ENABLE_RECORD_CHECK_RESULTS = results
        try:
            iid = await seed_delivery(db, list(rows) + [filler_row(f"leg{label}")],
                                      received_at=received, feed=None)
            await run_engine(db, iid)
        finally:
            settings.ENABLE_RECORD_CHECK_RESULTS = False
        return iid

    jul = await delivery("jul", datetime(2026, 8, 21),
                         [entity_row(LEG["BOTH"], npi=BAD_LEN_NPI),
                          entity_row(LEG["JULONLY"], npi=BAD_LEN_NPI)], False)
    await db.execute(text("update rce_ingestion_runs set rule_set_version='1.0.0' "
                          "where source_intake_id=:i"), {"i": jul})
    await db.execute(text("update rce_issues set rule_version='1.0.0' "
                          "where source_intake_id=:i"), {"i": jul})
    await db.execute(text("update rce_rule_execution_history set rule_version='1.0.0' "
                          "where run_id in (select id from rce_ingestion_runs "
                          "where source_intake_id=:i)"), {"i": jul})
    sep = await delivery("sep", datetime(2026, 9, 2),
                         [entity_row(LEG["BOTH"], npi=BAD_LEN_NPI),
                          entity_row(LEG["SEPONLY"], npi=BAD_LEN_NPI)], True)
    return {"jul": jul, "sep": sep}


def legacy_settings(ids):
    return SimpleNamespace(
        ISSUE_HISTORY_FEEDS_VIEWER=LEG_FEED, ISSUE_HISTORY_FEEDS_REVIEWER="",
        ISSUE_HISTORY_INTAKE_FEEDS=",".join(f"{ids[k]}:{LEG_FEED}" for k in ("jul", "sep")))
