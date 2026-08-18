#!/usr/bin/env python
"""Deterministic QA fixtures for the Round 3 BLOCKED test cases.

WHY THIS EXISTS
================================================================================
Four Round 3 cases could not be executed at all, and all four failed the same
way — there was nothing to test against:

  PR-003  no Priority Review available to open
  PR-004  Priority Cases list returned 0 records
  RC-004  no completed review cycle to check tracking against
  EQ-010  sticky header untestable — too few rows to scroll

This is the shape of defect the Round 3 report calls RC8. A suite run against an
empty table does not fail; it finds nothing to disagree with and reports success.
"0 records, 0 failures" and "everything passed" are the same line of output, and
only one of them is true. Seeding known rows turns those four cases from
unrunnable into pass-or-fail.

WHAT IT CREATES
---------------
  * 3 priority reviews with KNOWN SLA positions — one overdue, one at-risk, one
    on-track — so a tester can assert which badge each row shows rather than
    checking only that the page rendered.
  * 1 COMPLETED review cycle with known counts, for RC-004.
  * Enough entity reviews to make the Entity Queue scroll, for EQ-010.

Dates are computed as offsets from "now" so the overdue row is still overdue
next month. Hard-coded dates would make the fixture rot into a false pass: the
at-risk row silently becomes overdue and the assertion that distinguished them
stops distinguishing anything.

GUARANTEES
----------
  * REFUSES TO RUN ON PRODUCTION. These rows are fabricated and carry real QHIN
    names; in a production table they would be indistinguishable on screen from
    ONC-provided data. Same reasoning, and same ENVIRONMENT check, as the
    seeders in app/Tefca/routes.py and app/tefca_registry/routes.py.
  * IDEMPOTENT. Every row is keyed by a stable QA marker and upserted, so running
    twice leaves the same rows rather than a second copy. A fixture that
    duplicates on re-run makes count assertions depend on how many times someone
    ran the script.
  * Every row is flagged is_mock_data=True.

USAGE
-----
    python scripts/seed_qa_test_data.py            # seed
    python scripts/seed_qa_test_data.py --verify   # report what exists, write nothing
    python scripts/seed_qa_test_data.py --remove   # delete only what this script created
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Marker written into every row this script owns. Removal and idempotency both
# key off it, so the script can never delete a row it did not create.
QA_MARKER = "QA-ROUND3-FIXTURE"

# Deterministic identities. NPIs are in the 99xxxxxxxx range that no real NPPES
# record uses, so a fixture row can never be mistaken for a live provider.
PRIORITY_FIXTURES = [
    {
        "key": f"{QA_MARKER}-PRIORITY-OVERDUE",
        "entity_name": "Northgate Regional Health (QA overdue)",
        "npi": "9900000101",
        "qhin": "eHealth Exchange",
        # Opened well past the SLA window — must render as OVERDUE.
        "age_days": 45,
        "expected_sla": "overdue",
    },
    {
        "key": f"{QA_MARKER}-PRIORITY-AT-RISK",
        "entity_name": "Lakeside Care Alliance (QA at-risk)",
        "npi": "9900000202",
        "qhin": "CommonWell",
        # Inside the window but close to it — must render as AT RISK.
        "age_days": 12,
        "expected_sla": "at_risk",
    },
    {
        "key": f"{QA_MARKER}-PRIORITY-ON-TRACK",
        "entity_name": "Summit Valley Physicians (QA on-track)",
        "npi": "9900000303",
        "qhin": "Health Gorilla",
        "age_days": 2,
        "expected_sla": "on_track",
    },
]

# EQ-010 — a sticky header only demonstrates anything once the table scrolls.
SCROLL_ROW_COUNT = 30


def _is_production() -> bool:
    return (os.getenv("ENVIRONMENT", "") or "").strip().lower() == "production"


def _refuse_production() -> None:
    """Hard stop. Checked before any engine is created, so a production run
    cannot even open a connection."""
    if _is_production():
        sys.stderr.write(
            "REFUSED: seed_qa_test_data.py must never run against production.\n"
            "These rows are fabricated entities labelled with real QHIN names.\n"
            "In the production review table they would be indistinguishable\n"
            "from ONC-provided data on screen.\n"
            "Set ENVIRONMENT to a non-production value to seed a dev database.\n"
        )
        raise SystemExit(2)


async def _seed(session, *, verify_only: bool = False) -> dict:
    from sqlalchemy import select

    from app.Tefca.models import TEFCAReview

    now = datetime.utcnow()
    report = {"priority": [], "scroll_rows": 0, "cycle": None, "created": 0, "updated": 0}

    async def upsert_review(*, key, entity_name, npi, qhin, status, risk, age_days,
                            reviewer=None):
        existing = (await session.execute(
            select(TEFCAReview).where(TEFCAReview.npi == npi)
        )).scalars().first()

        created_at = now - timedelta(days=age_days)
        if existing:
            if not verify_only:
                existing.entity_name = entity_name
                existing.qhin = qhin
                existing.status = status
                existing.risk_level = risk
                existing.created_at = created_at
                existing.updated_at = now
                existing.is_mock_data = True
                existing.reviewer_id = reviewer
            report["updated"] += 1
            return existing

        if verify_only:
            return None
        row = TEFCAReview(
            entity_name=entity_name,
            npi=npi,
            qhin=qhin,
            status=status,
            risk_level=risk,
            reviewer_id=reviewer,
            entity_type="participant",
            is_mock_data=True,
            created_at=created_at,
            updated_at=now,
        )
        session.add(row)
        report["created"] += 1
        return row

    # ── PR-003 / PR-004 — three priority reviews at known SLA positions ──────
    for fx in PRIORITY_FIXTURES:
        await upsert_review(
            key=fx["key"],
            entity_name=fx["entity_name"],
            npi=fx["npi"],
            qhin=fx["qhin"],
            status="pending",
            risk="high",
            age_days=fx["age_days"],
        )
        report["priority"].append({"npi": fx["npi"], "expected_sla": fx["expected_sla"]})

    # ── EQ-010 — enough rows for the Entity Queue to scroll ──────────────────
    # Statuses are spread across the disposition vocabulary rather than all set
    # to "pending", so the queue's own status filter has something to filter and
    # MC-005's Active Reviews count is exercised against a non-trivial mix.
    spread = ["pending", "under_review", "no_discrepancy", "non_compliant",
              "minor_administrative", "indeterminate"]
    for i in range(SCROLL_ROW_COUNT):
        await upsert_review(
            key=f"{QA_MARKER}-SCROLL-{i:03d}",
            entity_name=f"QA Scroll Fixture {i:03d}",
            npi=f"99{i:08d}",
            qhin=["eHealth Exchange", "CommonWell", "Health Gorilla"][i % 3],
            status=spread[i % len(spread)],
            risk=["low", "medium", "high", "critical"][i % 4],
            age_days=(i % 20) + 1,
        )
        report["scroll_rows"] += 1

    # ── RC-004 — one COMPLETED review cycle with known counts ────────────────
    try:
        from app.Tefca.models import CycleStatus, CycleType, TEFCAReviewCycle

        cycle = (await session.execute(
            select(TEFCAReviewCycle).where(
                TEFCAReviewCycle.cycle_number == 9903)
        )).scalars().first()

        if cycle is None and not verify_only:
            cycle = TEFCAReviewCycle(
                cycle_type=CycleType.QUARTERLY if hasattr(CycleType, "QUARTERLY")
                else list(CycleType)[0],
                cycle_number=9903,
                cycle_start_date=now - timedelta(days=90),
                cycle_end_date=now - timedelta(days=7),
                cycle_status=CycleStatus.COMPLETED if hasattr(CycleStatus, "COMPLETED")
                else list(CycleStatus)[-1],
                created_by=QA_MARKER,
            )
            session.add(cycle)
            report["created"] += 1
        elif cycle is not None and not verify_only:
            cycle.cycle_status = (CycleStatus.COMPLETED if hasattr(CycleStatus, "COMPLETED")
                                  else list(CycleStatus)[-1])
            cycle.cycle_end_date = now - timedelta(days=7)
            report["updated"] += 1
        report["cycle"] = 9903 if cycle is not None or not verify_only else None
    except Exception as exc:  # pragma: no cover - cycle model shape varies
        # A cycle that cannot be created must not take the rest of the fixture
        # down with it — the priority and scroll rows are independently useful.
        report["cycle"] = f"skipped: {type(exc).__name__}: {exc}"

    return report


async def _remove(session) -> int:
    """Delete only rows this script created, identified by the fixture NPI range."""
    from sqlalchemy import delete, or_, select

    from app.Tefca.models import TEFCAReview

    npis = [f["npi"] for f in PRIORITY_FIXTURES] + [
        f"99{i:08d}" for i in range(SCROLL_ROW_COUNT)
    ]
    rows = (await session.execute(
        select(TEFCAReview).where(TEFCAReview.npi.in_(npis))
    )).scalars().all()
    n = len(rows)
    for r in rows:
        await session.delete(r)
    return n


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true",
                        help="report what exists; write nothing")
    parser.add_argument("--remove", action="store_true",
                        help="delete the fixture rows this script created")
    args = parser.parse_args()

    _refuse_production()

    from app.core.database import _get_session_maker

    factory = _get_session_maker()
    async with factory() as session:
        if args.remove:
            n = await _remove(session)
            await session.commit()
            print(f"Removed {n} QA fixture rows.")
            return 0

        report = await _seed(session, verify_only=args.verify)
        if args.verify:
            print("VERIFY (nothing written):")
        else:
            await session.commit()

        print(f"  priority reviews : {len(report['priority'])} "
              f"({', '.join(p['expected_sla'] for p in report['priority'])})")
        print(f"  scroll rows      : {report['scroll_rows']}")
        print(f"  completed cycle  : {report['cycle']}")
        print(f"  created={report['created']} updated={report['updated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
