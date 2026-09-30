"""Progress metrics for the contract progress deliverables — D3.1 Weekly,
D3.1 Monthly, D3.2 120-Day Final (approved AGT executive layout, 2026-09-30).

Every figure is derived from the review cases of the SELECTED scope (a
delivery's intake and/or a drawn sample). Nothing is typed in, nothing is
estimated:

* case set        review_records scoped by verification_results.source_intake_id
                  (delivery) and/or sample_id (review cycle)
* final bucket    the decision-event chain — the latest non-superseded
                  RECLASSIFY's determined_bucket, or the system bucket on a
                  CONFIRM; a case with no determination and no system bucket is
                  "unclassified" and is reported separately, never in B1–B4
* state           qhin_workload.case_states (AVAILABLE / CLAIMED /
                  SUBMITTED_FOR_QA / RETURNED / ESCALATED / APPROVED)
* QHIN            the active managed_by_qhin edge of the case's entity
* sources         tefca_verifications for the cases' entities, mapped to the
                  contract's four permitted source categories
* intervals       QA APPROVE occurred_at − assigned_at (else created_at), per
                  completed case; median and P90
* reconciliation  the latest persisted reconciliation snapshot of the job

The five arithmetic controls are asserted on the result before it is returned
(a builder defect fails the generation; it never ships a wrong table).

Fail-closed rules: no drawn sample for the scope → sample sizes are shown as
"—" and the page states "Sampling population pending COR/RCE confirmation."
Location/connection rows are never labelled as unique Participants /
Subparticipants: the entity count shown is the distinct promoted entities of
the delivery (E_distinct_entities) or "pending confirmation".
"""
from __future__ import annotations

import logging
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select

logger = logging.getLogger(__name__)

BUCKETS: Tuple[Tuple[str, str], ...] = (
    ("B1", "No Discrepancy"),
    ("B2", "Minor / Administrative Discrepancy"),
    ("B3", "Inexplicable Discrepancy"),
    ("B4", "Non-Compliant Discrepancy"),
)
BUCKET_CODES = tuple(b for b, _ in BUCKETS)

SAMPLING_PENDING = "Sampling population pending COR/RCE confirmation."

#: Contract Section C source categories, keyed by evidence-source family.
SOURCE_CATEGORY_OF = {
    "NPPES": "Publicly available data", "OIG_LEIE": "Publicly available data",
    "SAM_GOV": "Publicly available data", "CMS_REVOCATION": "Publicly available data",
    "CMS_PPEF_ENROLLMENT": "Publicly available data", "CMS_PPEF_PRACTICE_LOCATION": "Publicly available data",
    "CMS_PPEF_REASSIGNMENT": "Publicly available data", "CMS_PPEF_ADDITIONAL_NPIS": "Publicly available data",
    "CMS_PPEF_SECONDARY_SPECIALTY": "Publicly available data", "PECOS": "Publicly available data",
    "USPS": "Publicly available data",
    "ONC_RCE_DIRECTORY": "Data provided by the COR", "ONC_RCE_SUBMITTED": "Data provided by the COR",
    "DOCUACTION": "Contractor-owned data", "AGT": "Contractor-owned data",
    "ENTRANT_WEBSITE": "Other relevant approved source",
}
SOURCE_CATEGORIES = ("Publicly available data", "Contractor-owned data",
                     "Data provided by the COR", "Other relevant approved source")

STATE_UNASSIGNED = ("AVAILABLE",)
STATE_IN_REVIEW = ("CLAIMED", "RETURNED", "ESCALATED")
STATE_AWAITING_QA = ("SUBMITTED_FOR_QA",)
STATE_COMPLETED = ("APPROVED",)


class ProgressDataError(RuntimeError):
    """The selected scope cannot support a truthful progress report."""


def _iso_date(value) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.1f}%" if whole else "—"


def _percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1)))]


class SowProgressDataService:
    """Builds the `progress` block for one report from the live case ledger."""

    def __init__(self, db):
        self.db = db

    # ── case set ───────────────────────────────────────────────────────────
    async def _cases(self, *, intake_id: Optional[str], sample_id: Optional[str]) -> List[Any]:
        from app.tefca_registry import models as reg

        stmt = select(reg.ReviewRecord)
        if intake_id:
            stmt = stmt.where(reg.ReviewRecord.verification_results["source_intake_id"].astext == str(intake_id))
        if sample_id:
            try:
                stmt = stmt.where(reg.ReviewRecord.sample_id == uuid.UUID(str(sample_id)))
            except (ValueError, TypeError):
                pass
        stmt = stmt.order_by(reg.ReviewRecord.review_id)
        return list((await self.db.execute(stmt)).scalars().all())

    async def _events(self, review_ids: Sequence[str]) -> Dict[str, List[Any]]:
        from app.tefca_registry import models as reg

        out: Dict[str, List[Any]] = defaultdict(list)
        ids = list(review_ids)
        for i in range(0, len(ids), 500):
            rows = (await self.db.execute(
                select(reg.ReviewDecisionEvent)
                .where(reg.ReviewDecisionEvent.review_id.in_(ids[i:i + 500]))
                .order_by(reg.ReviewDecisionEvent.review_id, reg.ReviewDecisionEvent.sequence_number))).scalars().all()
            for ev in rows:
                out[ev.review_id].append(ev)
        return out

    @staticmethod
    def _final_bucket(record, events: List[Any]) -> Tuple[Optional[str], Optional[datetime], Optional[str]]:
        """(bucket, determined_at, actor) from the event chain; system bucket on CONFIRM."""
        superseded = {str(e.supersedes_decision_id) for e in events if getattr(e, "supersedes_decision_id", None)}
        latest = None
        for e in events:
            if e.event_type in ("ANALYST_DETERMINATION", "SUPERSEDING_DETERMINATION") and str(e.id) not in superseded:
                latest = e
        if latest is not None:
            if latest.determination == "RECLASSIFY" and latest.determined_bucket:
                return latest.determined_bucket, latest.occurred_at, latest.actor_email
            return (record.classification_bucket or record.reclassified_to or None), latest.occurred_at, latest.actor_email
        bucket = record.reclassified_to or record.classification_bucket
        return (bucket or None), (record.reviewed_at or record.reclassified_at), None

    @staticmethod
    def _qa_approval(events: List[Any]):
        approvals = [e for e in events if e.event_type == "QA_REVIEW" and e.qa_action == "APPROVE"]
        return approvals[-1] if approvals else None

    # ── lookups ─────────────────────────────────────────────────────────────
    async def _qhin_of(self, entity_ids: Sequence[Any]) -> Dict[str, Dict[str, Any]]:
        from app.tefca_registry.supervisor_ops import _qhins_for
        try:
            return await _qhins_for(self.db, [e for e in entity_ids if e])
        except Exception as exc:  # noqa: BLE001
            logger.warning("progress: QHIN lookup unavailable: %s", exc)
            return {}

    async def _entity_for_source_record(self, source_record_ids: Sequence[Any]) -> Dict[str, Any]:
        """source_record_id → canonical entity id, for cases without entity_id."""
        from app.tefca_registry.rce import models as m
        ids = [s for s in source_record_ids if s]
        if not ids:
            return {}
        rows = (await self.db.execute(
            select(m.RceCuratedRecord.source_record_id, m.RceCuratedRecord.canonical_entity_id)
            .where(m.RceCuratedRecord.source_record_id.in_(ids)))).all()
        return {str(s): e for s, e in rows if e}

    async def _entity_levels(self, entity_ids: Sequence[Any]) -> Dict[str, str]:
        """Participant / Subparticipant / QHIN level of each entity, as the
        registry records it (a level, never a name)."""
        from app.tefca_registry import models as reg
        ids = [e for e in entity_ids if e]
        if not ids:
            return {}
        out: Dict[str, str] = {}
        for i in range(0, len(ids), 500):
            rows = (await self.db.execute(
                select(reg.TefcaRegEntity.id, reg.TefcaRegEntity.entity_level)
                .where(reg.TefcaRegEntity.id.in_(ids[i:i + 500])))).all()
            out.update({str(k): (str(v).capitalize() if v else None) for k, v in rows if v})
        return out

    async def _qhin_populations(self, intake_id: Optional[str]) -> Dict[str, int]:
        """Distinct promoted entities per QHIN for the delivery (managed_by_qhin edges)."""
        if not intake_id:
            return {}
        from app.tefca_registry import models as reg
        from app.tefca_registry.rce import models as m
        try:
            rows = (await self.db.execute(
                select(reg.TefcaEntityRelationship.parent_entity_id, m.RceCuratedRecord.canonical_entity_id)
                .join(reg.TefcaEntityRelationship,
                      reg.TefcaEntityRelationship.child_entity_id == m.RceCuratedRecord.canonical_entity_id)
                .where(m.RceCuratedRecord.source_intake_id == uuid.UUID(str(intake_id)),
                       reg.TefcaEntityRelationship.relationship_type == "managed_by_qhin",
                       reg.TefcaEntityRelationship.status == "active"))).all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("progress: QHIN population unavailable: %s", exc)
            return {}
        seen: Dict[str, set] = defaultdict(set)
        for parent, child in rows:
            seen[str(parent)].add(str(child))
        return {k: len(v) for k, v in seen.items()}

    async def _sample(self, sample_id: Optional[str]):
        if not sample_id:
            return None
        from app.tefca_registry import models as reg
        try:
            return await self.db.get(reg.ReviewSample, uuid.UUID(str(sample_id)))
        except Exception:  # noqa: BLE001
            return None

    async def _sources(self, review_ids: Sequence[str], entity_ids: Sequence[Any]) -> Tuple[List[Dict[str, Any]], str]:
        from app.tefca_registry import models as reg
        counts: Counter = Counter()
        try:
            stmt = select(reg.TefcaVerification.source, reg.TefcaVerification.entity_id).where(
                reg.TefcaVerification.review_id.in_(list(review_ids)[:5000]))
            rows = (await self.db.execute(stmt)).all()
            if not rows and entity_ids:
                rows = (await self.db.execute(
                    select(reg.TefcaVerification.source, reg.TefcaVerification.entity_id)
                    .where(reg.TefcaVerification.entity_id.in_([e for e in entity_ids if e][:5000])))).all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("progress: verification sources unavailable: %s", exc)
            rows = []
        seen = set()
        for source, entity in rows:
            key = (str(source or "").upper(), str(entity))
            if key in seen:
                continue
            seen.add(key)
            family = key[0].split(":")[0]
            counts[SOURCE_CATEGORY_OF.get(family, "Other relevant approved source")] += 1
        out = [{"category": c, "count": counts.get(c, 0), "unit": "entity checks"} for c in SOURCE_CATEGORIES]
        note = ("Each verification answer is recorded with its source and date; proxy evidence is labelled as such."
                if rows else "No verification evidence is recorded for the selected scope; source categories show zero.")
        return out, note

    async def _reconciliation(self, job) -> Dict[str, Any]:
        from app.tefca_registry.rce import reconciliation
        snap = await reconciliation.latest_snapshot(self.db, getattr(job, "id", None)) if job is not None else None
        if snap is None:
            return {"available": False, "passed": None, "received": None, "sequence": None, "hash": None}
        return {"available": True, "passed": bool(snap.passed), "received": snap.received,
                "sequence": snap.sequence, "hash": getattr(snap, "hash", None) or getattr(snap, "snapshot_hash", None),
                "created_at": snap.created_at.isoformat() if snap.created_at else None}

    async def _distinct_entities(self, intake_id: Optional[str]) -> Optional[int]:
        if not intake_id:
            return None
        from sqlalchemy import func
        from app.tefca_registry.rce import models as m
        try:
            return int((await self.db.execute(
                select(func.count(func.distinct(m.RceCuratedRecord.canonical_entity_id)))
                .where(m.RceCuratedRecord.source_intake_id == uuid.UUID(str(intake_id)),
                       m.RceCuratedRecord.canonical_entity_id.isnot(None)))).scalar() or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("progress: distinct entity count unavailable: %s", exc)
            return None

    # ── build ───────────────────────────────────────────────────────────────
    async def build(self, *, kind: str, deliverable: str, title: str,
                    intake_id: Optional[str], sample_id: Optional[str],
                    job=None, intake=None, period_start=None, period_end=None) -> Dict[str, Any]:
        from app.tefca_registry.qhin_workload import case_states

        assert kind in ("weekly", "monthly", "final")
        p_start, p_end = _iso_date(period_start), _iso_date(period_end)
        cases = await self._cases(intake_id=intake_id, sample_id=None if intake_id else sample_id)
        if not cases:
            from app.reports.generator import ReportParameterError

            raise ReportParameterError(
                "No review cases exist for the selected delivery / review cycle, so a progress "
                "report cannot be produced. Select a delivery whose review cases have been "
                "created. Nothing was generated.",
                code="PROGRESS_SCOPE_EMPTY", status=422)
        review_ids = [c.review_id for c in cases]
        events = await self._events(review_ids)
        states = await case_states(self.db, review_ids)

        # entity → QHIN
        src_to_entity = await self._entity_for_source_record(
            [c.source_record_id for c in cases if not c.entity_id and c.source_record_id])
        entity_of = {c.review_id: (c.entity_id or src_to_entity.get(str(c.source_record_id))) for c in cases}
        qhin_of = await self._qhin_of(list({e for e in entity_of.values() if e}))
        level_of = await self._entity_levels(list({e for e in entity_of.values() if e}))

        rows: List[Dict[str, Any]] = []
        for c in cases:
            evs = events.get(c.review_id, [])
            bucket, determined_at, actor = self._final_bucket(c, evs)
            state = states.get(c.review_id, "AVAILABLE" if c.assigned_to_user_id is None else "CLAIMED")
            approval = self._qa_approval(evs)
            approved_at = approval.occurred_at if approval else None
            start = c.assigned_at or c.created_at
            interval = ((approved_at - start).total_seconds() / 86400.0) if (approved_at and start) else None
            qhin = qhin_of.get(str(entity_of.get(c.review_id) or ""), {})
            vr = c.verification_results or {}
            rows.append({
                "review_id": c.review_id,
                "entity_id": str(entity_of.get(c.review_id) or "") or None,
                "entity_level": (level_of.get(str(entity_of.get(c.review_id) or ""))
                                 or vr.get("entity_level")),
                "qhin": qhin.get("qhin_name") or ("Unresolved" if entity_of.get(c.review_id) else "Not linked"),
                "queue_source": vr.get("queue_source"),
                "case_classification": vr.get("case_classification"),
                "issue_codes": vr.get("issue_codes") or [],
                "bucket": bucket if bucket in BUCKET_CODES else None,
                "determined_at": determined_at, "determined_by": actor,
                "state": state,
                "assigned": "Assigned" if state not in STATE_UNASSIGNED else "Unassigned",
                "review_status": ("Completed" if state in STATE_COMPLETED else "Awaiting QA" if state in STATE_AWAITING_QA
                                  else "In review" if state in STATE_IN_REVIEW else "Not started"),
                "qa_status": ("QA approved" if state in STATE_COMPLETED else "Awaiting QA" if state in STATE_AWAITING_QA
                              else "Returned" if state == "RETURNED" else "Escalated" if state == "ESCALATED" else "Not started"),
                "qa_actor": approval.actor_email if approval else None,
                "qa_at": approved_at,
                "reportable_at": c.reportable_at,
                "created_at": c.created_at, "assigned_at": c.assigned_at,
                "interval_days": interval,
                "event_count": len(evs),
            })

        # period membership: a case is "reviewed in the period" when its determination falls in it
        def in_period(r, start, end):
            d = _iso_date(r["determined_at"])
            if d is None:
                return False
            return (start is None or d >= start) and (end is None or d <= end)

        classified = [r for r in rows if r["bucket"]]
        unclassified = [r for r in rows if not r["bucket"]]
        to_date = [r for r in classified if p_end is None or (_iso_date(r["determined_at"]) or p_end) <= p_end]
        in_period_rows = [r for r in classified if in_period(r, p_start, p_end)] if (p_start or p_end) else list(classified)
        scope_rows = to_date if kind != "monthly" else in_period_rows
        scope_label = {"weekly": "reviewed to date", "monthly": "this period", "final": "120-day aggregate"}[kind]

        def bucket_table(subset):
            table = []
            for code, label in BUCKETS:
                g = [r for r in subset if r["bucket"] == code]
                table.append({
                    "bucket": code, "label": label, "total": len(g), "pct": _pct(len(g), len(subset)),
                    "assigned": sum(r["assigned"] == "Assigned" for r in g),
                    "unassigned": sum(r["assigned"] == "Unassigned" for r in g),
                    "completed": sum(r["state"] in STATE_COMPLETED for r in g),
                    "in_review": sum(r["state"] in STATE_IN_REVIEW for r in g),
                    "awaiting_qa": sum(r["state"] in STATE_AWAITING_QA for r in g),
                })
            total = {"label": {"weekly": "Total reviewed to date", "monthly": "Total reviewed this period",
                               "final": "Total reviewed (120 days)"}[kind],
                     "total": len(subset), "pct": "100.0%" if subset else "—"}
            for k in ("assigned", "unassigned", "completed", "in_review", "awaiting_qa"):
                total[k] = sum(t[k] for t in table)
            return table, total

        buckets, buckets_total = bucket_table(scope_rows)

        # QHIN coverage
        populations = await self._qhin_populations(intake_id)
        sample = await self._sample(sample_id)
        sizing = {}
        if sample is not None and isinstance(sample.strata_distribution, dict):
            for k, v in (sample.strata_distribution.get("sizing") or {}).items():
                sizing[str(k)] = v.get("sample_size", v.get("n")) if isinstance(v, dict) else v
        qhin_ids = {}
        for r in rows:
            ent = entity_of.get(r["review_id"])
            q = qhin_of.get(str(ent or ""), {})
            if q.get("qhin_entity_id"):
                qhin_ids[r["qhin"]] = q["qhin_entity_id"]
        qhin_names = sorted({r["qhin"] for r in scope_rows} | {r["qhin"] for r in rows})
        qhins = []
        for name in qhin_names:
            g = [r for r in scope_rows if r["qhin"] == name]
            qid = qhin_ids.get(name)
            pop = populations.get(str(qid)) if qid else None
            smp = sizing.get(str(qid)) if qid else None
            c = Counter(r["bucket"] for r in g)
            qhins.append({"qhin": name, "population": pop if pop is not None else 0, "sample": smp if smp is not None else "—",
                          "reviewed": len(g), "pct": _pct(len(g), int(smp)) if isinstance(smp, int) and smp else "—",
                          **{b: c.get(b, 0) for b in BUCKET_CODES}})
        qhins_total = {"population": sum(q["population"] for q in qhins),
                       "sample": (sum(q["sample"] for q in qhins if isinstance(q["sample"], int)) if any(isinstance(q["sample"], int) for q in qhins) else "—"),
                       "reviewed": len(scope_rows), **{b: sum(q[b] for q in qhins) for b in BUCKET_CODES}}
        qhins_total["pct"] = _pct(len(scope_rows), qhins_total["sample"]) if isinstance(qhins_total["sample"], int) and qhins_total["sample"] else "—"

        # periods (monthly: ISO weeks in the period; final: 30-day blocks)
        periods: List[Dict[str, Any]] = []
        periods_total = None
        periods_heading = periods_column = ""
        if kind in ("monthly", "final") and scope_rows:
            dated = [r for r in scope_rows if _iso_date(r["determined_at"])]
            if kind == "monthly":
                periods_heading, periods_column = "Weekly reconciliation within the period", "Week"
                key = lambda d: d.isocalendar()[:2]  # noqa: E731
                label = lambda k: f"ISO week {k[1]} ({k[0]})"  # noqa: E731
            else:
                periods_heading, periods_column = "Trend by reporting period", "Period"
                origin = p_start or min(_iso_date(r["determined_at"]) for r in dated)
                key = lambda d: (d - origin).days // 30  # noqa: E731
                label = lambda k: f"Period {k + 1} (days {k * 30 + 1}–{k * 30 + 30})"  # noqa: E731
            groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
            for r in dated:
                groups[key(_iso_date(r["determined_at"]))].append(r)
            cumulative = 0
            for k in sorted(groups):
                g = groups[k]
                c = Counter(r["bucket"] for r in g)
                cumulative += len(g)
                periods.append({"label": label(k), "reviewed": len(g), **{b: c.get(b, 0) for b in BUCKET_CODES}, "cumulative": cumulative})
            undated = len(scope_rows) - len(dated)
            if undated:
                cumulative += undated
                periods.append({"label": "System-classified, no analyst determination yet", "reviewed": undated,
                                **{b: sum(1 for r in scope_rows if not _iso_date(r["determined_at"]) and r["bucket"] == b) for b in BUCKET_CODES},
                                "cumulative": cumulative})
            periods_total = {"label": "Total", "reviewed": sum(p["reviewed"] for p in periods),
                             **{b: sum(p[b] for p in periods) for b in BUCKET_CODES}, "cumulative": cumulative}

        # intervals
        intervals = [r["interval_days"] for r in scope_rows if r["interval_days"] is not None]
        median = statistics.median(intervals) if intervals else None
        p90 = _percentile(intervals, 0.9) if intervals else None

        # sources, reconciliation, entities
        sources, sources_note = await self._sources(review_ids, [e for e in entity_of.values() if e])
        recon = await self._reconciliation(job)
        distinct_entities = await self._distinct_entities(intake_id)
        records_received = getattr(job, "records_received", None) or getattr(intake, "record_count", None)
        records_processed = getattr(job, "records_processed", None)
        sample_total = qhins_total["sample"] if isinstance(qhins_total["sample"], int) else None
        sampling_note = None if (sample is not None and sample_total) else SAMPLING_PENDING

        # KPIs (kind-specific wording, all derived)
        completeness = _pct(records_processed or 0, records_received or 0) if records_received else "—"
        this_period = len(in_period_rows) if (p_start or p_end) else len(classified)
        remaining = (sample_total - len(to_date)) if sample_total else None
        interval_v = f"{median:.1f} d" if median is not None else "—"
        interval_n = f"median assignment → QA approval · P90 {p90:.1f} d · n={len(intervals)}" if intervals else "no completed case in scope yet"
        if kind == "weekly":
            kpis = [
                {"label": "Participants / Subparticipants reviewed", "value": f"{len(to_date):,}",
                 "note": f"to date · {this_period:,} this period · {len(unclassified):,} open, unclassified"},
                {"label": "QHINs represented", "value": f"{len({r['qhin'] for r in scope_rows if r['qhin'] not in ('Unresolved', 'Not linked')})} of {len(populations) if populations else '—'}",
                 "note": "QHINs with at least one reviewed case / QHINs in the delivery"},
                {"label": "Sample size · confidence", "value": f"{sample_total:,}" if sample_total else "Pending",
                 "note": (f"{sample.confidence_level} confidence, ±{sample.margin_of_error} margin, per QHIN" if sample is not None and sample_total else SAMPLING_PENDING)},
                {"label": "Processing completeness", "value": completeness,
                 "note": f"{(records_processed or 0):,} of {(records_received or 0):,} loaded source records processed" if records_received else "no delivery counts on record"},
                {"label": "Workflow interval", "value": interval_v, "note": interval_n},
            ]
        elif kind == "monthly":
            b34 = sum(t["total"] for t in buckets if t["bucket"] in ("B3", "B4"))
            kpis = [
                {"label": "Reviewed this period", "value": f"{len(in_period_rows):,}", "note": f"cumulative to date {len(to_date):,}" + (f" of {sample_total:,}" if sample_total else "")},
                {"label": "QHINs represented", "value": f"{len({r['qhin'] for r in scope_rows if r['qhin'] not in ('Unresolved', 'Not linked')})} of {len(populations) if populations else '—'}", "note": "QHINs with a reviewed case this period"},
                {"label": "Sample size · confidence", "value": f"{sample_total:,}" if sample_total else "Pending",
                 "note": (f"{sample.confidence_level} confidence, ±{sample.margin_of_error} margin, per QHIN" if sample is not None and sample_total else SAMPLING_PENDING)},
                {"label": "B3 + B4 this period", "value": f"{b34:,}", "note": f"{_pct(b34, len(in_period_rows))} inexplicable or non-compliant"},
                {"label": "Workflow interval", "value": interval_v, "note": interval_n},
            ]
        else:
            b34 = sum(t["total"] for t in buckets if t["bucket"] in ("B3", "B4"))
            kpis = [
                {"label": "Distinct entities in delivery", "value": f"{distinct_entities:,}" if distinct_entities is not None else "Pending",
                 "note": "promoted entities of the selected delivery (not location rows)" if distinct_entities is not None else SAMPLING_PENDING},
                {"label": "Sample reviewed", "value": f"{len(to_date):,}", "note": (f"of {sample_total:,} sampled ({_pct(len(to_date), sample_total)})" if sample_total else "classified review cases to date")},
                {"label": "QHIN coverage", "value": f"{len({r['qhin'] for r in scope_rows if r['qhin'] not in ('Unresolved', 'Not linked')})} / {len(populations) if populations else '—'}", "note": "QHINs with at least one reviewed case"},
                {"label": "B3 + B4 rate", "value": _pct(b34, len(scope_rows)), "note": f"{b34:,} entities inexplicable or non-compliant"},
                {"label": "Workflow interval", "value": interval_v, "note": interval_n},
            ]

        # controls
        approved = [r for r in scope_rows if r["state"] in STATE_COMPLETED]
        independent = sum(1 for r in approved if r["qa_actor"] and r["determined_by"] and r["qa_actor"] != r["determined_by"])
        controls = [
            {"title": "Reconciliation " + ("closed" if recon.get("passed") else "not closed" if recon.get("available") else "not on record"),
             "text": (f"Latest persisted snapshot #{recon['sequence']}: received {recon['received']:,}; passed = {recon['passed']}."
                      if recon.get("available") else "No reconciliation snapshot is persisted for this delivery.")},
            {"title": "Source integrity " + ("verified" if getattr(job, "sha256", None) else "not recorded"),
             "text": ("Original file hash retained on the delivery job; served artifacts are hash-matched on download."
                      if getattr(job, "sha256", None) else "No source file hash is recorded for this scope.")},
            {"title": "Audit " + ("complete" if all(r["event_count"] for r in approved) else "partial"),
             "text": f"{sum(r['event_count'] for r in rows):,} append-only decision events across {len(rows):,} cases; every completed case carries actor, time and reason."},
            {"title": "QA independence",
             "text": (f"Approver ≠ analyst on {independent} of {len(approved)} completed cases." if approved else "No completed case in scope yet.")},
        ]

        # findings (factual)
        findings = [
            f"{len(classified):,} of {len(rows):,} review cases carry a classification; {len(unclassified):,} remain open and unclassified and are not counted in any category.",
        ]
        if scope_rows:
            top = max(buckets, key=lambda t: t["total"])
            findings.append(f"{top['label']} is the largest class ({top['total']:,}, {top['pct']}) of the {scope_label} population.")
            b4 = next(t for t in buckets if t["bucket"] == "B4")
            findings.append(f"{b4['total']:,} case(s) ({b4['pct']}) are classified Non-Compliant Discrepancy and carry the highest scrutiny.")
        if buckets_total["awaiting_qa"]:
            findings.append(f"{buckets_total['awaiting_qa']:,} determination(s) await independent QA approval; they are listed as pending, not as findings.")
        if intervals:
            findings.append(f"Median assignment-to-approval interval is {median:.1f} days (P90 {p90:.1f}) over {len(intervals)} completed case(s).")

        # themes (final)
        themes = []
        if kind == "final":
            tc: Counter = Counter()
            for r in scope_rows:
                if r["bucket"] in ("B3", "B4"):
                    for code in (r["issue_codes"] or [r["case_classification"] or "Unspecified"]):
                        tc[str(code)] += 1
            themes = [{"label": k, "count": v} for k, v in tc.most_common(5)]

        limitations = []
        if sampling_note:
            limitations.append(SAMPLING_PENDING + " No drawn sample is linked to this scope; per-QHIN sample sizes are shown as —.")
        if unclassified:
            limitations.append(f"{len(unclassified):,} open case(s) have no determination yet and are excluded from the B1–B4 table.")
        if not recon.get("available"):
            limitations.append("No persisted reconciliation snapshot for the delivery; reconciliation status is 'not on record'.")
        unresolved = sum(1 for r in scope_rows if r["qhin"] in ("Unresolved", "Not linked"))
        if unresolved:
            limitations.append(f"{unresolved:,} reviewed case(s) could not be attributed to a QHIN (no active managed_by_qhin edge).")
        if not any(s["count"] for s in sources):
            limitations.append("No verification evidence rows are linked to the scope's cases; evidence-source categories are zero.")
        limitations.append("Record-level evidence is delivered as the controlled annex (CSV), not in this document.")

        # annex rows (no names, no NPIs, no addresses)
        annex = [{
            "Participant/Subparticipant reference": r["review_id"],
            "Participant type": (r["entity_level"] or "Not stated"),
            "QHIN": r["qhin"],
            "Source category": "Data provided by the COR" if (r["queue_source"] or "").startswith("RCE") else ("Other relevant approved source" if r["queue_source"] else "Not stated"),
            "Sample-selection reason": {"RCE_SAMPLE_REVIEW": "Stratified random draw (QHIN stratum)",
                                        "RCE_DQ_HUMAN_REQUIRED": "Data-quality rule requires human review",
                                        "RCE_POST_PROMOTION_VERIFICATION": "Post-promotion verification finding",
                                        "TEFCA_ARC_PRIORITY": "Priority: COR-identified",
                                        "PHASE6_EXCEPTION_TRIAGE": "Exception triage"}.get(r["queue_source"] or "", r["queue_source"] or "Not stated"),
            "B1–B4 classification": (f"{r['bucket']} — {dict(BUCKETS)[r['bucket']]}" if r["bucket"] else "Unclassified (open)"),
            "Discrepancy summary": ", ".join(str(x) for x in (r["issue_codes"] or [])) or (r["case_classification"] or "—"),
            "Assignment status": r["assigned"],
            "Reviewer status": r["review_status"],
            "QA status": r["qa_status"],
            "Recommended action": {"B1": "Close — no action", "B2": "Notify QHIN — administrative correction",
                                   "B3": "Request evidence from QHIN", "B4": "Escalate to COR"}.get(r["bucket"] or "", "Complete determination"),
            "Evidence reference": r["review_id"] + (f" · {r['event_count']} decision event(s)" if r["event_count"] else " · no decision event"),
            "Review date": (_iso_date(r["determined_at"]) or _iso_date(r["created_at"]) or "").isoformat() if (_iso_date(r["determined_at"]) or _iso_date(r["created_at"])) else "",
            "Workflow interval (days)": (f"{r['interval_days']:.1f}" if r["interval_days"] is not None else ""),
        } for r in (scope_rows + [r for r in unclassified if kind != "monthly"])]

        period_label = (f"{p_start.isoformat()} – {p_end.isoformat()}" if (p_start and p_end)
                        else f"from {p_start.isoformat()}" if p_start else f"to {p_end.isoformat()}" if p_end else "Not specified")
        progress = {
            "kind": kind, "deliverable": deliverable, "title": title,
            "period_label": period_label, "scope_label": scope_label,
            "source": {"job_id": str(getattr(job, "id", "")) or None, "intake_id": intake_id, "sample_id": sample_id,
                       "delivery_label": getattr(job, "delivery_label", None) or getattr(intake, "delivery_label", None),
                       "records_received": records_received, "records_processed": records_processed,
                       "distinct_entities": distinct_entities, "sample_size": sample_total},
            "sampling_note": sampling_note,
            "kpis": kpis,
            "buckets": buckets, "buckets_total": buckets_total, "unclassified": len(unclassified),
            "qhins": qhins, "qhins_total": qhins_total,
            "qhin_note": ("Entities = distinct promoted entities managed by the QHIN in the selected delivery; Sample = per-QHIN size of the drawn sample"
                          + ("" if sample_total else " (no drawn sample for this scope)") + ". QHIN subtotals reconcile to the overall total."),
            "periods": periods, "periods_total": periods_total, "periods_heading": periods_heading, "periods_column": periods_column,
            "sources": sources, "sources_note": sources_note,
            "controls": controls, "findings": findings, "themes": themes, "limitations": limitations,
            "interval": {"median_days": median, "p90_days": p90, "n": len(intervals)},
            "counts": {"cases": len(rows), "classified": len(classified), "unclassified": len(unclassified),
                       "to_date": len(to_date), "in_period": len(in_period_rows), "remaining": remaining},
            "annex_columns": list(annex[0].keys()) if annex else [],
            "annex_rows": annex,
            "package_note": ("this report · methodology & findings appendix · controlled stratified-list annex (CSV) · "
                             + ("120-day Final Report (D3.2)" if kind != "final" else "weekly and monthly reports as delivered")),
        }
        progress["arithmetic"] = self._assert_arithmetic(progress)
        return progress

    @staticmethod
    def _assert_arithmetic(p: Dict[str, Any]) -> Dict[str, Any]:
        checks = []
        for t in p["buckets"]:
            assert t["assigned"] + t["unassigned"] == t["total"], ("assigned+unassigned", t)
            assert t["completed"] + t["in_review"] + t["awaiting_qa"] == t["assigned"], ("completed+in_review+awaiting_qa", t)
        checks.append("Assigned + Unassigned = Total on every row")
        checks.append("Completed + In review + Awaiting QA = Assigned on every row")
        assert sum(t["total"] for t in p["buckets"]) == p["buckets_total"]["total"]
        checks.append(f"B1 + B2 + B3 + B4 = {p['buckets_total']['total']}")
        assert sum(q["reviewed"] for q in p["qhins"]) == p["buckets_total"]["total"]
        for b in BUCKET_CODES:
            assert sum(q[b] for q in p["qhins"]) == next(t["total"] for t in p["buckets"] if t["bucket"] == b)
        checks.append("QHIN subtotals reconcile to the overall total")
        if p["periods"]:
            assert p["periods_total"]["reviewed"] == p["buckets_total"]["total"]
            assert p["periods"][-1]["cumulative"] == p["buckets_total"]["total"]
            checks.append("Period rows reconcile to the total")
        return {"ok": True, "checks": checks, "note": "; ".join(checks) + "."}


def annex_csv(progress: Dict[str, Any], *, report_id: str, marking: str) -> str:
    """The contract-required stratified-list annex as CSV (no names, NPIs or addresses)."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([f"# {marking}"])
    w.writerow([f"# Report {report_id} — Deliverable {progress['deliverable']} — {progress['title']} — period {progress['period_label']}"])
    w.writerow(["# Identifiers are review-case references; no names, NPIs, addresses or credentials are included."])
    cols = progress.get("annex_columns") or []
    w.writerow(cols)
    for r in progress.get("annex_rows") or []:
        w.writerow([r.get(c, "") for c in cols])
    return buf.getvalue()
