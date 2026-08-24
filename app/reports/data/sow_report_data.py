"""
The contract's report families, computed from canonical evidence.

WHAT THIS REPLACES, AND WHY
───────────────────────────
The SOW deliverable families — weekly, final, bi-weekly, quarterly, priority —
were implemented in `app/Tefca/reporting.py`, which reads `tefca_reviews` with
one-off SQL and takes `review.status` as the discrepancy category. That path:

  * never consults the canonical evidence version, so a superseded generation
    could reach a contract deliverable;
  * never consults the reportability gate, so a system recommendation no human
    has approved is counted in a category exactly as if a QA reviewer had signed
    it off;
  * reads `tefca_reviews`, a denormalised dashboard mirror, rather than
    `review_records`, which is the table the decision-event architecture and the
    QA gate actually operate on.

This module is the replacement. Everything here reads through
`ReportDataService`, so there is exactly one place that decides which evidence a
report may see, and everything here respects `reportable_at`, so a category
count means "a human determined this and a different human approved it".

THE CATEGORIES ARE THE GOVERNMENT'S
───────────────────────────────────
`GOVERNMENT_CATEGORIES` below is quoted from the solicitation — ¶136, ¶137 and
¶142, where the identical sentence appears three times. Those are the labels a
report must use. B1–B4 is AGT's internal shorthand for the same four, and the
mapping between them is AGT methodology submitted under D2 (¶124 asks the
contractor to establish a discrepancy taxonomy; it does not prescribe one).

So: internal rules may map evidence to a bucket, and the report must print the
contractual words. `government_label()` is the only sanctioned way to put a
category on a page.

WHAT A CANONICAL SOW REPORT SHOWS THAT THE LEGACY ONE DID NOT
─────────────────────────────────────────────────────────────
A `pending` count. Legacy stratification implied every reviewed entity had a
settled category. Under the gate, an entity whose determination has not been
QA-approved is not in any category yet — it is pending — and saying so is the
difference between reporting what is known and reporting what was guessed.

On the current development data that means every entity is pending, because 0 of
43 review records carry a QA approval. That is the correct answer for
development data with no human decisions in it, not a defect.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

SOW_REPORT_DATA_VERSION = "1.0.0"

#: Verbatim from RFQ 7571MN26Q00038 ¶136 / ¶137 / ¶142. Order is the
#: solicitation's numbering, which reports must preserve.
GOVERNMENT_CATEGORIES = (
    "no_discrepancy",
    "minor_administrative",
    "inexplicable",
    "non_compliant",
)

#: The words that appear on a deliverable. Not paraphrased, not title-cased into
#: something friendlier — a report to the Government uses the Government's terms.
GOVERNMENT_CATEGORY_LABELS = {
    "no_discrepancy": "No discrepancies identified",
    "minor_administrative": "Minor or administrative discrepancies",
    "inexplicable": "Inexplicable discrepancies",
    "non_compliant": "Non-compliant discrepancies",
}

#: The solicitation numbers them 1-4. Reports that show a number must show this
#: one.
GOVERNMENT_CATEGORY_NUMBER = {
    "no_discrepancy": 1,
    "minor_administrative": 2,
    "inexplicable": 3,
    "non_compliant": 4,
}

#: AGT's internal shorthand → the Government's category. AGT METHODOLOGY, not a
#: contractual mapping: the solicitation defines the four categories and asks
#: the contractor to establish the taxonomy that assigns them (¶124).
BUCKET_TO_GOVERNMENT_CATEGORY = {
    "B1": "no_discrepancy",
    "B2": "minor_administrative",
    "B3": "inexplicable",
    "B4": "non_compliant",
}


def government_label(category: str) -> str:
    """The contractual wording for a category.

    Raises on an unknown category rather than falling back to the raw key. A
    report that printed `minor_administrative` at a COR would be using internal
    vocabulary in a Government deliverable, and a silent fallback is how that
    happens.
    """
    try:
        return GOVERNMENT_CATEGORY_LABELS[category]
    except KeyError:
        raise ValueError(
            f"{category!r} is not one of the four Government discrepancy "
            f"categories: {', '.join(GOVERNMENT_CATEGORIES)}")


def category_for_bucket(bucket: Optional[str]) -> Optional[str]:
    """Map an internal bucket to a Government category, or None."""
    return BUCKET_TO_GOVERNMENT_CATEGORY.get((bucket or "").strip().upper())


def empty_stratification() -> Dict[str, int]:
    return {c: 0 for c in GOVERNMENT_CATEGORIES}


class SowReportDataService:
    """Canonical data for the contract's report families.

    Composes `ReportDataService` rather than subclassing it: the canonical
    service answers "what evidence may this report see", and this one answers
    "what does the contract want said about it". Keeping them separate means the
    evidence rules cannot be quietly overridden by a report family.
    """

    version = SOW_REPORT_DATA_VERSION

    def __init__(self, db, canonical=None):
        from app.reports.data.report_data_service import ReportDataService

        self.db = db
        self.canonical = canonical or ReportDataService(db)

    # ── shared building blocks ───────────────────────────────────────────────

    async def _review_records(self, review_cycle_id: Optional[str] = None) -> List[Any]:
        from app.tefca_registry import models as reg

        stmt = select(reg.ReviewRecord)
        if review_cycle_id:
            stmt = stmt.where(reg.ReviewRecord.sample_id == review_cycle_id)
        try:
            return list((await self.db.execute(stmt)).scalars().all())
        except Exception as exc:  # noqa: BLE001
            logger.warning("sow report: review records unavailable: %s", exc)
            return []

    async def evidence_scope(self, review_cycle_id: Optional[str] = None
                             ) -> Dict[str, Any]:
        """What evidence this report is entitled to see, and what it excluded.

        Every SOW family calls this, so no family can quietly widen its own
        population.
        """
        await self.canonical._dimension_rows(review_cycle_id)
        return dict(self.canonical.evidence_scope)

    async def stratification(self, review_cycle_id: Optional[str] = None
                             ) -> Dict[str, Any]:
        """The four-category stratified list the contract requires.

        A record counts toward a category ONLY if a QA approval stands
        (`reportable_at` is set). Everything else is counted as pending, with the
        reason, because "we have not decided" and "we decided it was fine" are
        different statements and a stratified list that conflates them
        misrepresents both.
        """
        records = await self._review_records(review_cycle_id)

        reportable = empty_stratification()
        pending = empty_stratification()
        unclassified_reportable = 0
        unclassified_pending = 0

        for record in records:
            bucket = (getattr(record, "reclassified_to", None)
                      or getattr(record, "classification_bucket", None))
            category = category_for_bucket(bucket)
            is_reportable = getattr(record, "reportable_at", None) is not None
            if category is None:
                if is_reportable:
                    unclassified_reportable += 1
                else:
                    unclassified_pending += 1
                continue
            (reportable if is_reportable else pending)[category] += 1

        total_reportable = sum(reportable.values()) + unclassified_reportable
        total_pending = sum(pending.values()) + unclassified_pending

        return {
            "categories": list(GOVERNMENT_CATEGORIES),
            "labels": dict(GOVERNMENT_CATEGORY_LABELS),
            "numbers": dict(GOVERNMENT_CATEGORY_NUMBER),
            # Counts a Government deliverable may state.
            "reportable": reportable,
            "reportable_total": total_reportable,
            # Counts it may not state as findings, shown so nothing disappears.
            "pending_qa": pending,
            "pending_qa_total": total_pending,
            "unclassified_reportable": unclassified_reportable,
            "unclassified_pending": unclassified_pending,
            "records_considered": len(records),
            "gate": ("A record enters a category only on a standing QA approval. "
                     "Pending records are counted separately and are not findings."),
            "source_table": "review_records",
            "sow_data_version": self.version,
        }

    async def _envelope(self, family: str, review_cycle_id: Optional[str],
                        period_start=None, period_end=None) -> Dict[str, Any]:
        """The fields every SOW family carries, computed the same way once."""
        from app.Tefca.evidence_version import current_rule_version

        strat = await self.stratification(review_cycle_id)
        scope = await self.evidence_scope(review_cycle_id)
        return {
            "family": family,
            "review_cycle_id": review_cycle_id,
            "reporting_period_start": period_start,
            "reporting_period_end": period_end,
            "evidence_rule_version": current_rule_version(),
            "evidence_scope": scope,
            "stratification": strat,
            "methodology_pending": await self.methodology_pending(),
            "source_limitations": await self.source_limitations(review_cycle_id),
            "sow_data_version": self.version,
        }

    async def methodology_pending(self) -> Dict[str, Any]:
        """Open decisions that stop a conclusion being drawn.

        Reported, never suppressed. An unresolved question that is hidden
        becomes an assumption, and an assumption inside a report is very hard to
        find later.
        """
        from app.Tefca.exception_triage import Triage

        return {
            "disposition": Triage.METHODOLOGY_PENDING.value,
            "note": ("Items awaiting a COR methodology decision are counted and "
                     "disclosed. They are not findings and are not failures."),
        }

    async def source_limitations(self, review_cycle_id: Optional[str] = None
                                 ) -> Dict[str, Any]:
        """Sources that could not answer, as a fact about the lookup.

        Never a fact about the entity. Derived from persisted observation state,
        not from a hard-coded list of known outages.
        """
        rows = await self.canonical._dimension_rows(review_cycle_id)
        limited: Dict[str, int] = {}
        for row in rows:
            state = (getattr(row, "observation_result", "") or "").strip()
            if state == "SOURCE_UNAVAILABLE":
                key = getattr(row, "source", None) or "UNKNOWN"
                limited[key] = limited.get(key, 0) + 1
        return {
            "sources_unavailable": limited,
            "observations_affected": sum(limited.values()),
            "note": ("A source that could not answer says nothing about the "
                     "entity. These are recorded as limitations of the lookup."),
        }

    # ── the contract's families ──────────────────────────────────────────────

    async def retrospective_weekly(self, review_cycle_id=None,
                                   period_start=None, period_end=None):
        """D3.1 — Task 3 weekly progress report (¶136, ¶138)."""
        data = await self._envelope("D3.1_RETROSPECTIVE_WEEKLY", review_cycle_id,
                                    period_start, period_end)
        data["required_content"] = [
            "Stratified list across the four Government categories",
            "Suggested changes to the Task 2 methodology or control framework, as needed",
        ]
        return data

    async def retrospective_final(self, review_cycle_id=None,
                                  period_start=None, period_end=None):
        """D3.2 — Task 3 final report (¶137, ¶139)."""
        data = await self._envelope("D3.2_RETROSPECTIVE_FINAL", review_cycle_id,
                                    period_start, period_end)
        data["required_content"] = [
            "Aggregated data over the 120-day retrospective period",
            "Stratified list across the four Government categories",
            "All suggested AND implemented changes to the methodology and control framework",
        ]
        data["sampling"] = {
            "confidence_floor": "95% (CONTRACT REQUIREMENT, ¶128)",
            "parameters_status": "AGT METHODOLOGY — D2 §5.1, awaiting COR confirmation",
            "note": ("Per-QHIN sample draw is not implemented; it requires "
                     "approved sampling parameters."),
        }
        return data

    async def ongoing_biweekly(self, review_cycle_id=None,
                               period_start=None, period_end=None):
        """D4.1 — Task 4 bi-weekly progress report (¶140, ¶142)."""
        data = await self._envelope("D4.1_ONGOING_BIWEEKLY", review_cycle_id,
                                    period_start, period_end)
        data["scope_note"] = ("New submissions from each QHIN. Per Q&A Q2/Q8, "
                              "Task 4 covers new entrants only — not changes to "
                              "existing entities.")
        data["required_content"] = [
            "Stratified list across the four Government categories",
            "All suggested and implemented changes to the methodology and control framework",
        ]
        return data

    async def ongoing_quarterly(self, review_cycle_id=None,
                                period_start=None, period_end=None):
        """D4.2 — Task 4 quarterly report (¶143)."""
        data = await self._envelope("D4.2_ONGOING_QUARTERLY", review_cycle_id,
                                    period_start, period_end)
        data["required_content"] = [
            "Aggregated data for the previous ninety (90) days, synthesised succinctly",
        ]
        return data

    async def priority_status(self, case_id: Optional[str] = None,
                              review_cycle_id=None):
        """D5.1 — Task 5 priority review status report (¶146, ¶147)."""
        data = await self._envelope("D5.1_PRIORITY_STATUS", review_cycle_id)
        data["case_id"] = case_id
        # The five elements ¶147 names, in the order it names them.
        data["required_content"] = [
            "The identified issue",
            "Root cause, if determined",
            "The severity or impact",
            "Recommendations to prevent reoccurrence",
            "Resolution",
        ]
        data["turnaround"] = {
            "basis": ("Measured against the deadline communicated by the COR for "
                      "this request (¶146). There is no fixed contractual SLA."),
        }
        return data

    async def priority_quarterly(self, review_cycle_id=None,
                                 period_start=None, period_end=None):
        """D5.2 — Task 5 quarterly report (¶148)."""
        data = await self._envelope("D5.2_PRIORITY_QUARTERLY", review_cycle_id,
                                    period_start, period_end)
        data["required_content"] = [
            "Aggregated data for the previous ninety (90) days, synthesised succinctly",
        ]
        return data

    async def closeout_framework(self, review_cycle_id=None):
        """D6.1 — Task 6 closeout report framework (¶152).

        Framework only. Populating closeout findings before the work exists
        would be fabrication, so the sections are named and left empty.
        """
        data = await self._envelope("D6.1_CLOSEOUT", review_cycle_id)
        data["sections"] = [
            "Complete report of methodologies and framework",
            "All tools developed under this contract",
            "All files and data produced",
            "Review coverage and totals",
            "Findings",
            "Unresolved matters",
            "Lessons learned",
            "Audit and reproducibility information",
        ]
        data["rights_note"] = ("The Government obtains unlimited rights to the "
                              "methodologies and deliverables created under this "
                              "contract (¶152).")
        data["populated"] = False
        data["note"] = ("Framework only. No closeout findings exist because no "
                        "contract review work has been performed.")
        return data

    async def closeout_presentation(self, review_cycle_id=None):
        """D6.2 — Task 6 closeout educational presentation (¶153)."""
        data = await self.closeout_framework(review_cycle_id)
        data["family"] = "D6.2_CLOSEOUT_PRESENTATION"
        data["medium"] = ("A presentation is required by name — the only "
                          "deliverable whose medium the contract fixes. No file "
                          "format is specified.")
        return data


#: Every SOW family, by deliverable id, for callers that iterate.
SOW_FAMILIES = {
    "D3.1": "retrospective_weekly",
    "D3.2": "retrospective_final",
    "D4.1": "ongoing_biweekly",
    "D4.2": "ongoing_quarterly",
    "D5.1": "priority_status",
    "D5.2": "priority_quarterly",
    "D6.1": "closeout_framework",
    "D6.2": "closeout_presentation",
}
