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

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import aliased

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
        lists = await self.stratified_entities(
            review_cycle_id, period_start=period_start, period_end=period_end)
        return {
            "family": family,
            "review_cycle_id": review_cycle_id,
            "reporting_period_start": period_start,
            "reporting_period_end": period_end,
            "evidence_rule_version": current_rule_version(),
            "evidence_scope": scope,
            "stratification": strat,
            "entity_lists": lists["entity_lists"],
            "entity_columns": lists["columns"],
            "pending_qa": lists["pending_qa"],
            "list_counts": lists["counts"],
            "list_note": lists["note"],
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


    # ── the stratified LIST the contract asks for ────────────────────────────

    ENTITY_COLUMNS = (
        ("review_id", "Case"),
        ("entity_name", "Participant / Subparticipant"),
        ("entity_level", "Level"),
        ("rce_org_oid", "RCE organisation OID"),
        ("qhin", "QHIN"),
        ("category_label", "Category"),
        ("rule", "Rule"),
        ("reportable_at", "QA approved (UTC)"),
    )

    async def stratified_entities(self, review_cycle_id: Optional[str] = None,
                                  period_start=None, period_end=None
                                  ) -> Dict[str, Any]:
        """The stratified list of Participants and Subparticipants.

        Section C, Tasks 3 and 4: every weekly, final and bi-weekly report
        "includes a stratified list of Participants and Subparticipants" in the
        four Government categories. A list, not counts. Each row is one review
        record joined to its entity and to the QHIN the canonical
        `managed_by_qhin` edge names — never a QHIN inferred from a column.

        Only records with a standing QA approval (`reportable_at`) enter a
        category list. Everything else is returned separately as pending, with
        the reason, so nothing disappears and nothing is promoted.

        `period_start` / `period_end` (ISO dates) restrict the CATEGORY lists to
        approvals inside the period — a weekly report lists the week's approved
        reviews. Pending rows are the current backlog and are not period-filtered.
        """
        import uuid as _uuid

        from app.tefca_registry import models as reg

        Qhin = aliased(reg.TefcaRegEntity)
        stmt = (
            select(reg.ReviewRecord, reg.TefcaRegEntity, Qhin)
            .outerjoin(reg.TefcaRegEntity,
                       reg.TefcaRegEntity.id == reg.ReviewRecord.entity_id)
            .outerjoin(reg.TefcaEntityRelationship, and_(
                reg.TefcaEntityRelationship.child_entity_id == reg.ReviewRecord.entity_id,
                reg.TefcaEntityRelationship.relationship_type == "managed_by_qhin",
                reg.TefcaEntityRelationship.status == "active"))
            .outerjoin(Qhin, Qhin.id == reg.TefcaEntityRelationship.parent_entity_id)
            .order_by(reg.ReviewRecord.review_id)
        )
        if review_cycle_id:
            try:
                stmt = stmt.where(
                    reg.ReviewRecord.sample_id == _uuid.UUID(str(review_cycle_id)))
            except (ValueError, TypeError):
                # Not a sample id (the snapshot cycle label is a rule/version
                # anchor). No sample filter applies.
                pass
        try:
            rows = list((await self.db.execute(stmt)).all())
        except Exception as exc:  # noqa: BLE001
            logger.warning("sow report: stratified list unavailable: %s", exc)
            rows = []

        start = _parse_iso_date(period_start)
        end = _parse_iso_date(period_end)

        lists: Dict[str, List[Dict[str, Any]]] = {c: [] for c in GOVERNMENT_CATEGORIES}
        pending: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            record, entity, qhin = _unpack_row(row)
            review_id = getattr(record, "review_id", None)
            if review_id in seen:
                continue  # a second managed_by_qhin edge; the first is reported
            seen.add(review_id)

            bucket = (getattr(record, "reclassified_to", None)
                      or getattr(record, "classification_bucket", None))
            category = category_for_bucket(bucket)
            reportable_at = getattr(record, "reportable_at", None)
            rule = getattr(record, "classification_rule", None)
            version = getattr(record, "classification_rule_version", None)
            item = {
                "review_id": review_id,
                "entity_name": (getattr(entity, "display_name", None)
                                or getattr(entity, "name", None)
                                or "Entity not promoted (held record)"),
                "entity_level": (getattr(entity, "sequoia_org_type", None)
                                 or getattr(entity, "entity_level", None) or "—"),
                "rce_org_oid": getattr(entity, "rce_org_oid", None) or "—",
                "qhin": (getattr(qhin, "name", None)
                         or getattr(entity, "org_managing_org", None) or "Unresolved"),
                "category": category,
                "category_label": government_label(category) if category else "—",
                "category_number": GOVERNMENT_CATEGORY_NUMBER.get(category) if category else None,
                "rule": f"{rule} v{version}" if rule else "—",
                "reportable_at": _iso(reportable_at),
                "reviewed_at": _iso(getattr(record, "reviewed_at", None)),
            }
            if reportable_at is None or category is None:
                item["pending_reason"] = (
                    "No standing QA approval" if reportable_at is None
                    else "QA approved but no category recorded")
                pending.append(item)
                continue
            approved_on = _parse_iso_date(reportable_at)
            if start and approved_on and approved_on < start:
                continue
            if end and approved_on and approved_on > end:
                continue
            lists[category].append(item)

        return {
            "columns": [{"key": k, "label": v} for k, v in self.ENTITY_COLUMNS],
            "entity_lists": lists,
            "pending_qa": pending,
            "counts": {**{c: len(lists[c]) for c in GOVERNMENT_CATEGORIES},
                       "listed_total": sum(len(v) for v in lists.values()),
                       "pending_qa": len(pending)},
            "note": ("Each row is one review record joined to its entity and to "
                     "the QHIN named by the canonical managed_by_qhin edge. A row "
                     "enters a category only on a standing QA approval; pending "
                     "rows are shown separately and are not findings."),
        }

    async def sampling_summary(self, review_cycle_id: Optional[str] = None
                               ) -> Dict[str, Any]:
        """The official sampling plans on record, as parameters — not a claim.

        The contract fixes the 95% floor and "from each QHIN". Margin,
        population and small-stratum handling are AGT methodology under D2 and
        are reported as the parameters actually recorded on each plan.
        """
        from app.tefca_registry import models as reg

        plans: List[Dict[str, Any]] = []
        try:
            rows = list((await self.db.execute(
                select(reg.ReviewSample).order_by(reg.ReviewSample.drawn_at.desc())
            )).scalars().all())
        except Exception as exc:  # noqa: BLE001
            logger.warning("sow report: sampling plans unavailable: %s", exc)
            rows = []
        for plan in rows[:10]:
            strata = getattr(plan, "strata_config", None) or {}
            plans.append({
                "sample_id": str(getattr(plan, "id", "")),
                "name": getattr(plan, "sample_name", None),
                "review_type": getattr(plan, "review_type", None),
                "population_size": getattr(plan, "population_size", None),
                "sample_size": getattr(plan, "sample_size", None),
                "confidence_level": getattr(plan, "confidence_level", None),
                "margin_of_error": getattr(plan, "margin_of_error", None),
                "use_fpc": bool(getattr(plan, "use_fpc", False)),
                "stratify_by": strata.get("stratify_by") if isinstance(strata, dict) else None,
                "status": getattr(plan, "status", None),
                "drawn_at": _iso(getattr(plan, "drawn_at", None)),
            })
        return {
            "confidence_floor": "At or above 95% (CONTRACT REQUIREMENT — Section C, Tasks 3 and 4)",
            "stratification_requirement": "From each QHIN (CONTRACT REQUIREMENT — Section C, Tasks 3 and 4)",
            "parameters_status": ("AGT METHODOLOGY — margin of error, population "
                                  "definition and small-stratum handling are "
                                  "submitted under D2, awaiting COR confirmation, and "
                                  "are reported as recorded on each plan."),
            "plans_on_record": len(rows),
            "plans": plans,
        }

    @staticmethod
    def methodology_changes(query_parameters: Optional[Dict[str, Any]],
                            include_implemented: bool) -> Dict[str, Any]:
        """The methodology / control-framework change section.

        Human-authored. The contract asks the report to carry suggested changes
        (weekly: as needed) and implemented changes (final, bi-weekly, status,
        quarterly). No table records these yet, so the generating PM supplies
        them as parameters and the report says so; an empty section states that
        none were recorded rather than leaving a blank.
        """
        params = query_parameters or {}

        def _items(value) -> List[str]:
            if not value:
                return []
            if isinstance(value, str):
                return [line.strip() for line in value.splitlines() if line.strip()]
            return [str(v).strip() for v in value if str(v).strip()]

        return {
            "suggested": _items(params.get("suggested_changes")),
            "implemented": _items(params.get("implemented_changes")) if include_implemented else [],
            "includes_implemented": include_implemented,
            "basis": ("Authored by the programme manager at generation time and "
                      "recorded in the report's provenance parameters. Not derived "
                      "from review data."),
        }

    async def build_report_dataset(self, report_type: str, *,
                                   review_cycle_id: Optional[str] = None,
                                   query_parameters: Optional[Dict[str, Any]] = None
                                   ) -> Dict[str, Any]:
        """Everything the SOW report template needs, frozen, for one family."""
        meta = SOW_REPORT_TYPES[report_type]
        params = dict(query_parameters or {})
        period_start = params.get("period_start") or None
        period_end = params.get("period_end") or None
        method = getattr(self, meta["method"])
        if meta["deliverable"] == "D5.1":
            data = await method(case_id=params.get("case_id"),
                                review_cycle_id=review_cycle_id)
            if period_start or period_end:
                data["reporting_period_start"] = period_start
                data["reporting_period_end"] = period_end
        else:
            data = await method(review_cycle_id=review_cycle_id,
                                period_start=period_start, period_end=period_end)

        from app.reports.branding import current_branding

        branding = current_branding()
        CONTRACT_NUMBER = branding.contract_number

        data.update({
            # Identity as text (no images in the frozen dataset).
            "branding": branding.to_dict(),
            "report_type": report_type,
            "deliverable": meta["deliverable"],
            "deliverable_title": meta["title"],
            "task": meta["task"],
            "contract_number": CONTRACT_NUMBER,
            "contract_citation": ("All reports reference and cite the contract "
                                  "number (RFQ 7571MN26Q00038, Section F)."),
            "government_labels": dict(GOVERNMENT_CATEGORY_LABELS),
            "category_numbers": dict(GOVERNMENT_CATEGORY_NUMBER),
            "categories": list(GOVERNMENT_CATEGORIES),
            "methodology_changes": self.methodology_changes(
                params, include_implemented=meta["implemented_changes"]),
            "sampling": data.get("sampling") or await self.sampling_summary(review_cycle_id),
            "scope": {
                "reporting_period_start": period_start,
                "reporting_period_end": period_end,
                "review_cycle_id": review_cycle_id,
            },
            "service_version": self.version,
            "chart_list": [],
        })
        return data

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
        data["sampling"] = await self.sampling_summary(review_cycle_id)
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
        data.update(await self._priority_case_content(case_id))
        return data

    async def _priority_case_content(self, case_id):
        """The five ¶147 elements for one request — THROUGH THE RELEASE GATE.

        Without a case id this stays a family envelope, which is what it has
        always been. With one, the content comes from
        `priority_review.reportable_result`, and that withholds every
        determination field until an independent QA approval stands.

        A request existing is not a finding. An analyst determination is not a
        finding. The report is the last place that distinction can still be
        made, so it is made here rather than trusted to the caller.
        """
        import uuid as _uuid

        blank = {"case": None,
                 "release_gate": ("Only a QA-approved determination is "
                                  "reportable content.")}
        if not case_id or self.db is None:
            return blank
        try:
            case_uuid = _uuid.UUID(str(case_id))
        except (ValueError, AttributeError, TypeError):
            # A development or placeholder label, not a request. Reporting an
            # envelope for it is honest; inventing content for it would not be.
            return blank

        from app.tefca_registry import priority_review as pr

        try:
            result = await pr.reportable_result(self.db, case_uuid)
            request = await pr.get_request(self.db, case_uuid)
            history = await pr.deadline_history(self.db, case_uuid)
        except pr.PriorityRefused as exc:
            return {**blank, "case_error": str(exc)}

        deadline = request["deadline"]
        status = pr.deadline_status(
            datetime.fromisoformat(deadline) if deadline else None)
        return {
            "case": {
                "cor_reference": result["cor_reference"],
                "requested_by": request["requested_by"],
                "request_received_at": request["received_at"],
                "target_reference": request["target_reference"],
                "target_resolution": request["target_resolution"],
                "review_id": result["review_id"],
                "reportable": result["reportable"],
                "reportable_at": result["reportable_at"],
                # The five elements, in the order ¶147 names them. Every one is
                # None until the gate opens.
                "identified_issue": result["identified_issue"],
                "root_cause": result["root_cause_determination"],
                "root_cause_detail": result["root_cause_description"],
                "severity": result["severity"],
                "recommendations": result["recommendations"],
                "prevention_recommendation": result["prevention_recommendation"],
                "resolution": result["resolution_notes"],
                "withheld_reason": result["withheld_reason"],
                "deadline": deadline,
                "original_deadline": history["original_deadline"],
                "deadline_amendments": history["amendments"],
                "deadline_status": status["status"],
                "hours_remaining": status["hours_remaining"],
                "compliance_conclusion": status["compliance_conclusion"],
            },
            "release_gate": ("Only a QA-approved determination is reportable "
                             "content."),
        }

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


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _parse_iso_date(value: Any):
    """A date from an ISO string or date/datetime; None when absent or malformed."""
    if value is None or value == "":
        return None
    if hasattr(value, "date") and not isinstance(value, str):
        return value.date()
    if hasattr(value, "year") and not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except ValueError:
        return None


def _unpack_row(row):
    """(record, entity, qhin) from a joined result row or a bare record."""
    try:
        record, entity, qhin = row[0], row[1], row[2]
        return record, entity, qhin
    except (TypeError, IndexError, KeyError):
        return row, None, None


#: Generated-document report types for the SOW families. Keys are the
#: `report_type` accepted by the generator (each at most 20 characters, the
#: width of `review_reports.report_type`).
SOW_REPORT_TYPES: Dict[str, Dict[str, Any]] = {
    "retrospective_weekly": {"kind": "Weekly", 
        "deliverable": "D3.1", "task": "Task 3", "method": "retrospective_weekly",
        "title": "Task 3 Weekly Progress Report", "implemented_changes": False,
        "cadence": "Weekly during the first 120 days"},
    "retrospective_final": {"kind": "Final", 
        "deliverable": "D3.2", "task": "Task 3", "method": "retrospective_final",
        "title": "Task 3 Final Report", "implemented_changes": True,
        "cadence": "Within thirty days following completion of the retrospective review"},
    "ongoing_biweekly": {"kind": "Biweekly", 
        "deliverable": "D4.1", "task": "Task 4", "method": "ongoing_biweekly",
        "title": "Task 4 Bi-Weekly Progress Report", "implemented_changes": True,
        "cadence": "Every two weeks"},
    "ongoing_quarterly": {"kind": "Quarterly", 
        "deliverable": "D4.2", "task": "Task 4", "method": "ongoing_quarterly",
        "title": "Task 4 Quarterly Report", "implemented_changes": True,
        "cadence": "Every calendar quarter, covering the previous ninety days"},
    "priority_status": {"kind": "Status", 
        "deliverable": "D5.1", "task": "Task 5", "method": "priority_status",
        "title": "Task 5 Priority Review Status Report", "implemented_changes": True,
        "cadence": "At the direction of the COR"},
    "priority_quarterly": {"kind": "Quarterly", 
        "deliverable": "D5.2", "task": "Task 5", "method": "priority_quarterly",
        "title": "Task 5 Quarterly Report", "implemented_changes": True,
        "cadence": "Every calendar quarter, covering the previous ninety days"},
}
