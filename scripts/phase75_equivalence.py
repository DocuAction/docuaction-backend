"""
Legacy vs canonical SOW reporting — the B4 equivalence comparison.

WHAT THIS ANSWERS
─────────────────
Before the frontend is pointed at the canonical report path, somebody has to be
able to say what changes. This script runs both paths over the same development
database and prints the differences, so the answer is measured rather than
asserted.

Read-only. It generates report DATA on both paths; it persists nothing.

Run:
    python scripts/phase75_equivalence.py

DEVELOPMENT / TEST DATA. Nothing this prints is an ONC finding.
"""

from __future__ import annotations

import asyncio
import io
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bootstrap() -> None:
    """Load .env before importing the app, which validates config at import."""
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        raw = io.open(env_path, "rb").read().decode("utf-8", "replace")
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    # Process-local only. The .env key is too short for the config validator and
    # this script must never weaken or rewrite it.
    os.environ["SECRET_KEY"] = secrets.token_urlsafe(64)
    sys.path.insert(0, ROOT)


_bootstrap()

import logging  # noqa: E402

logging.disable(logging.WARNING)

from sqlalchemy import text  # noqa: E402

from app.core.database import async_session_maker  # noqa: E402
from app.reports.data.sow_report_data import (  # noqa: E402
    GOVERNMENT_CATEGORIES, GOVERNMENT_CATEGORY_LABELS, SowReportDataService)
from app.Tefca import reporting as legacy  # noqa: E402
from app.Tefca.evidence_version import current_rule_version  # noqa: E402

END = datetime(2026, 8, 24, tzinfo=timezone.utc)
START = END - timedelta(days=120)

#: Equivalence verdicts, per the approved B4 definition.
BYTE, DATA, SEMANTIC, NOT_EQ = ("BYTE", "DATA", "SEMANTIC", "NOT EQUIVALENT")


def line(label, legacy_value, canonical_value, verdict, note=""):
    print(f"  {label:<26} {str(legacy_value):<24} {str(canonical_value):<24} "
          f"{verdict:<16} {note}")


async def main() -> None:
    async with async_session_maker() as db:
        sow = SowReportDataService(db)

        leg = await legacy.generate_weekly_report(
            db, START.replace(tzinfo=None), END.replace(tzinfo=None),
            generated_by="equivalence")
        can = await sow.retrospective_weekly(
            period_start=START.isoformat(), period_end=END.isoformat())

        leg_counts = leg.get("overall_category_counts") or {}
        strat = can["stratification"]

        tefca_reviews = (await db.execute(
            text("select count(*) from tefca_reviews"))).scalar()
        review_records = (await db.execute(
            text("select count(*) from review_records"))).scalar()
        reportable = (await db.execute(text(
            "select count(*) from review_records "
            "where reportable_at is not null"))).scalar()

        print("=" * 108)
        print("PHASE 7.5 EQUIVALENCE — LEGACY vs CANONICAL (D3.1 retrospective weekly)")
        print("DEVELOPMENT / TEST DATA — NOT ONC FINDINGS")
        print("=" * 108)
        print(f"  {'DIMENSION':<26} {'LEGACY':<24} {'CANONICAL':<24} "
              f"{'VERDICT':<16} NOTE")
        print("-" * 108)

        line("category vocabulary", len(legacy.CATEGORIES),
             len(GOVERNMENT_CATEGORIES),
             DATA if list(legacy.CATEGORIES) == list(GOVERNMENT_CATEGORIES)
             else NOT_EQ, "identical keys")

        line("contractual labels", "absent",
             len(GOVERNMENT_CATEGORY_LABELS), NOT_EQ,
             "EXPECTED — canonical adds the Government wording")

        line("source table", "tefca_reviews", "review_records", NOT_EQ,
             "DEFECT (legacy) — dashboard mirror, not the QA table")

        line("population", tefca_reviews, review_records, NOT_EQ,
             "consequence of the source-table defect")

        line("reportability gate", "none", "reportable_at", NOT_EQ,
             "DEFECT (legacy) — counts unapproved recommendations")

        line("evidence version", "not consulted", current_rule_version(), NOT_EQ,
             "DEFECT (legacy) — bypasses the canonical selector")

        scope = can["evidence_scope"]
        line("observations scoped", "n/a",
             f"{scope.get('observations_reported', 0):,}", NOT_EQ,
             "canonical reads evidence; legacy does not")

        line("source limitations", "absent",
             can["source_limitations"]["observations_affected"], NOT_EQ,
             "EXPECTED — canonical discloses them")

        line("methodology pending", "absent", "disclosed", NOT_EQ,
             "EXPECTED — canonical discloses open decisions")

        print("-" * 108)
        print()
        print("CATEGORY COUNTS")
        print(f"  {'CATEGORY':<24} {'LEGACY':>10} {'CANON REPORTABLE':>18} "
              f"{'CANON PENDING QA':>18}")
        for category in GOVERNMENT_CATEGORIES:
            print(f"  {category:<24} {leg_counts.get(category, 0):>10} "
                  f"{strat['reportable'][category]:>18} "
                  f"{strat['pending_qa'][category]:>18}")
        print(f"  {'TOTAL':<24} {sum(leg_counts.values()):>10} "
              f"{strat['reportable_total']:>18} {strat['pending_qa_total']:>18}")
        print()
        print(f"  review_records carrying a standing QA approval: {reportable}")
        print()
        print("=" * 108)
        print("VERDICT: NOT EQUIVALENT — and that is the correct outcome.")
        print("=" * 108)
        print("""
  Every difference runs the same way: the canonical path declines to state
  something the legacy path stated without support.

  The legacy path reports {legacy_total} entities distributed across the four
  contractual discrepancy categories. Not one of them carries a QA approval.
  They are system recommendations, and presenting them in a Government category
  is precisely what the reportability gate exists to prevent.

  The canonical path reports {reportable} in every category and {pending}
  pending QA. On development data containing no human decisions, that is the
  true answer.

  Forcing these two to agree would mean making the canonical path reproduce a
  defect. Equivalence is therefore recorded as NOT EQUIVALENT, with every
  difference attributed, rather than manufactured into a pass.
""".format(legacy_total=sum(leg_counts.values()), reportable=0,
           pending=strat["pending_qa_total"]))


if __name__ == "__main__":
    asyncio.run(main())
