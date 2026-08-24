"""
Legacy/canonical reconciliation — every legacy row gets a deterministic reason.

WHAT THIS PROVES
────────────────
    LEGACY POPULATION = CANONICAL REPORTABLE + RECONCILED NON-REPORTABLE

If a single legacy row cannot be given a reason derived from the database, the
reconciliation fails and Phase 7 does not close. Nothing here is hard-coded: the
populations, the dispositions and the totals are all read from the data.

It also re-tests the three legacy defects that earlier runs reported, rather
than trusting the earlier report. Each is expressed as a property that can fail.

Read-only. DEVELOPMENT / TEST DATA — nothing printed here is an ONC finding.
"""

from __future__ import annotations

import asyncio
import io
import os
import secrets
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bootstrap() -> None:
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        raw = io.open(env_path, "rb").read().decode("utf-8", "replace")
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    os.environ["SECRET_KEY"] = secrets.token_urlsafe(64)
    sys.path.insert(0, ROOT)


_bootstrap()

import logging  # noqa: E402

logging.disable(logging.WARNING)

from sqlalchemy import text  # noqa: E402

from app.core.database import async_session_maker  # noqa: E402

# ── disposition vocabulary ───────────────────────────────────────────────────
#
# Each is a reason a legacy-counted row is NOT canonically reportable. They are
# checked in order and the first that applies wins, so every row gets exactly
# one.

SYNTHETIC = "SYNTHETIC_DEMONSTRATION_ROW"
NO_RECORD = "NO_CORRESPONDING_REVIEW_RECORD"
NO_DETERMINATION = "NO_HUMAN_DETERMINATION"
NO_QA = "NO_QA_APPROVAL"
UNMAPPED = "UNSUPPORTED_CATEGORY_MAPPING"
UNEXPLAINED = "UNEXPLAINED"

REASON_TEXT = {
    SYNTHETIC: ("Flagged is_mock_data = TRUE. A synthetic demonstration row "
                "seeded for dashboard development. It is not a review of any "
                "entity and was never eligible to be a finding."),
    NO_RECORD: ("No review_record exists for this entity, so no determination "
                "could have been made about it."),
    NO_DETERMINATION: ("A review_record exists but reviewer_resolution is NULL "
                       "— no human has resolved it."),
    NO_QA: ("A determination exists but reportable_at is NULL — no QA approval "
            "stands behind it."),
    UNMAPPED: ("The recorded status does not map to one of the four Government "
               "discrepancy categories."),
}


async def main() -> None:
    async with async_session_maker() as db:
        async def scalar(sql, **kw):
            return (await db.execute(text(sql), kw)).scalar()

        async def rows(sql, **kw):
            return (await db.execute(text(sql), kw)).all()

        print("=" * 96)
        print("PHASE 7 CLOSURE — LEGACY / CANONICAL RECONCILIATION")
        print("DEVELOPMENT / TEST DATA — NOT ONC FINDINGS")
        print("=" * 96)

        # ── populations, derived ─────────────────────────────────────────────
        legacy_total = await scalar("select count(*) from tefca_reviews")
        canonical_records = await scalar("select count(*) from review_records")
        canonical_reportable = await scalar(
            "select count(*) from review_records where reportable_at is not null")
        decision_events = await scalar(
            "select count(*) from review_decision_events")

        print()
        print("POPULATIONS (derived, nothing hard-coded)")
        print(f"  legacy source table      tefca_reviews      {legacy_total}")
        print(f"  canonical source table   review_records     {canonical_records}")
        print(f"  canonical reportable                        {canonical_reportable}")
        print(f"  decision events on record                   {decision_events}")

        # ── per-row disposition ──────────────────────────────────────────────
        legacy_rows = await rows("""
            select tr.id,
                   tr.npi,
                   tr.entity_name,
                   tr.status,
                   coalesce(tr.is_mock_data, false) as is_mock,
                   exists (
                     select 1 from review_records rr
                     join tefca_entity_identifiers i on i.entity_id = rr.entity_id
                     where i.identifier_value = tr.npi
                   ) as has_record
            from tefca_reviews tr
            order by tr.npi
        """)

        valid_categories = {"no_discrepancy", "minor_administrative",
                            "inexplicable", "non_compliant"}

        dispositions = Counter()
        unexplained_rows = []
        for row in legacy_rows:
            _id, npi, name, status, is_mock, has_record = row
            if is_mock:
                disposition = SYNTHETIC
            elif not has_record:
                disposition = NO_RECORD
            elif (status or "").lower() not in valid_categories:
                disposition = UNMAPPED
            else:
                # A record exists; find out how far it got.
                resolved = await scalar("""
                    select count(*) from review_records rr
                    join tefca_entity_identifiers i on i.entity_id = rr.entity_id
                    where i.identifier_value = :npi
                      and rr.reviewer_resolution is not null""", npi=npi)
                approved = await scalar("""
                    select count(*) from review_records rr
                    join tefca_entity_identifiers i on i.entity_id = rr.entity_id
                    where i.identifier_value = :npi
                      and rr.reportable_at is not null""", npi=npi)
                if approved:
                    disposition = UNEXPLAINED  # reportable, yet not counted
                elif resolved:
                    disposition = NO_QA
                else:
                    disposition = NO_DETERMINATION
            dispositions[disposition] += 1
            if disposition == UNEXPLAINED:
                unexplained_rows.append((npi, name, status))

        print()
        print("DISPOSITION OF EVERY LEGACY ROW")
        for disposition, count in dispositions.most_common():
            print(f"  {disposition:<34} {count:>4}")
            print(f"      {REASON_TEXT.get(disposition, 'NO REASON DERIVED')}")

        reconciled = sum(c for d, c in dispositions.items() if d != UNEXPLAINED)
        unexplained = dispositions[UNEXPLAINED]

        print()
        print("THE EQUATION")
        print(f"  legacy population                    {legacy_total}")
        print(f"  canonical reportable                 {canonical_reportable}")
        print(f"  reconciled non-reportable            {reconciled}")
        print(f"  unexplained                          {unexplained}")
        balances = (canonical_reportable + reconciled == legacy_total
                    and unexplained == 0)
        print(f"  {canonical_reportable} + {reconciled} = {legacy_total}"
              f"   BALANCES: {balances}")
        if unexplained_rows:
            print()
            print("  UNEXPLAINED ROWS:")
            for npi, name, status in unexplained_rows:
                print(f"    {npi}  {name}  {status}")

        # ── the three legacy defects, re-tested ──────────────────────────────
        print()
        print("=" * 96)
        print("LEGACY DEFECTS — RE-TESTED, NOT ASSUMED")
        print("=" * 96)

        import inspect

        from app.Tefca import reporting as legacy_reporting
        legacy_source = inspect.getsource(legacy_reporting)

        # 1. reads the dashboard mirror rather than the QA table
        reads_mirror = "TEFCAReview" in legacy_source
        reads_qa_table = "ReviewRecord" in legacy_source
        print()
        print("  DEFECT 1 — source table")
        print(f"    legacy references TEFCAReview (dashboard mirror): {reads_mirror}")
        print(f"    legacy references ReviewRecord (QA table):        {reads_qa_table}")
        print(f"    CONFIRMED: {reads_mirror and not reads_qa_table}")

        # 2. no reportability gate
        gates = ("reportable_at" in legacy_source
                 or "is_reportable" in legacy_source)
        print()
        print("  DEFECT 2 — reportability gate")
        print(f"    legacy references reportable_at / is_reportable: {gates}")
        print(f"    CONFIRMED (gate absent): {not gates}")

        # 3. bypasses the canonical evidence selector
        uses_selector = ("current_rule_version" in legacy_source
                         or "ReportDataService" in legacy_source)
        print()
        print("  DEFECT 3 — canonical evidence selector")
        print(f"    legacy references the selector: {uses_selector}")
        print(f"    CONFIRMED (bypassed): {not uses_selector}")

        # ── and the canonical path does NOT reproduce them ───────────────────
        from app.reports.data import sow_report_data
        canonical_source = inspect.getsource(sow_report_data)
        print()
        print("  CANONICAL DOES NOT REPRODUCE THEM")
        print(f"    reads ReviewRecord:              "
              f"{'ReviewRecord' in canonical_source}")
        print(f"    applies reportable_at:           "
              f"{'reportable_at' in canonical_source}")
        print(f"    uses the canonical selector:     "
              f"{'current_rule_version' in canonical_source}")

        print()
        print("=" * 96)
        verdict = ("RECONCILED — every legacy row has a deterministic reason"
                   if balances else
                   "FAILED — at least one legacy row is unexplained")
        print(f"VERDICT: {verdict}")
        print("=" * 96)
        return 0 if balances else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
