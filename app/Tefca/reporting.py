"""
DocuAction TEFCA — Reporting Engine (weekly progress + final retrospective)
ONC TEFCA Review Protocol — Contract No. 7571MN26F80064 (HHS/ONC)

New module (TEFCA ARC Task 3). Aggregates the tefca_reviews / tefca_findings
data into SOW Task 3 weekly progress reports and the final retrospective report,
persisted to tefca_reports. Does not duplicate the legacy D3.1/D3.2 builders in
routes.py (those aggregate tefca_evidence_records).
"""
import io
import csv
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from sqlalchemy import select, text

from .models import TEFCAReview, TEFCAFinding, TEFCAReport

# The four SOW discrepancy categories (== tefca_reviews.status values).
CATEGORIES = ["no_discrepancy", "minor_administrative", "inexplicable", "non_compliant"]

# Compliance-score weights per category (0..100; higher = cleaner).
_CATEGORY_WEIGHT = {
    "no_discrepancy": 100, "minor_administrative": 80,
    "inexplicable": 50, "non_compliant": 0,
}
# Disposition mapping for the CSV "Status" column.
_STATUS_MAP = {
    "no_discrepancy": "pass", "minor_administrative": "pass",
    "inexplicable": "pending", "non_compliant": "fail",
}
# Suggested methodology change per recurring finding type.
_METHODOLOGY_SUGGESTIONS = {
    "NAME_MISMATCH": "Tighten/relax fuzzy name-match tolerance and add DBA alias resolution.",
    "SOURCE_CONFLICT": "Add tie-break precedence rules across authoritative sources.",
    "LEIE_ACTIVE_EXCLUSION": "Add pre-submission OIG LEIE screening at QHIN onboarding.",
    "NO_DISCREPANCY": "No change indicated.",
}

_AGT_NOTE = ("AGT produces findings and recommendations; the ONC COR makes all "
             "final determinations.")
_CONTRACT = {"contract": "7571MN26F80064", "contractor": "Alliance Global Tech, Inc. (AGT)"}


# ─── helpers ─────────────────────────────────────────────────────────────────

async def _reviews_in_range(db, start: Optional[datetime], end: Optional[datetime]) -> list:
    q = select(TEFCAReview)
    if start:
        q = q.where(TEFCAReview.created_at >= start)
    if end:
        q = q.where(TEFCAReview.created_at <= end)
    return (await db.execute(q.order_by(TEFCAReview.created_at))).scalars().all()


def _overall_counts(reviews: list) -> Dict[str, int]:
    counts = {c: 0 for c in CATEGORIES}
    for r in reviews:
        cat = (r.status or "").lower()
        if cat in counts:
            counts[cat] += 1
    return counts


def _stratify_by_qhin(reviews: list) -> Dict[str, Any]:
    by_qhin: Dict[str, Dict[str, int]] = {}
    for r in reviews:
        q = r.qhin or "Unknown QHIN"
        d = by_qhin.setdefault(q, {c: 0 for c in CATEGORIES})
        cat = (r.status or "").lower()
        if cat in d:
            d[cat] += 1
    out = {}
    for q, counts in sorted(by_qhin.items()):
        total = sum(counts.values())
        out[q] = {
            "total": total,
            "counts": counts,
            "percentages": {c: (round(100 * counts[c] / total, 1) if total else 0.0) for c in CATEGORIES},
            "compliance_score": (round(sum(_CATEGORY_WEIGHT[c] * counts[c] for c in CATEGORIES) / total, 1)
                                 if total else None),
        }
    return out


async def _finding_type_counts(db, reviews: list) -> Dict[str, int]:
    ids = [r.id for r in reviews]
    out: Dict[str, int] = {}
    if ids:
        rows = (await db.execute(
            select(TEFCAFinding.finding_type).where(TEFCAFinding.review_id.in_(ids))
        )).scalars().all()
        for ft in rows:
            out[ft] = out.get(ft, 0) + 1
    return out


def _suggest_methodology_changes(finding_type_counts: Dict[str, int]) -> List[dict]:
    suggestions = []
    for ft, n in sorted(finding_type_counts.items(), key=lambda x: -x[1]):
        if ft == "NO_DISCREPANCY":
            continue
        suggestions.append({
            "finding_type": ft, "occurrences": n,
            "suggested_change": _METHODOLOGY_SUGGESTIONS.get(ft, f"Review handling of recurring finding '{ft}'."),
        })
    return suggestions


async def _persist(db, report_type: str, report_data: dict, period_start, period_end, generated_by) -> uuid.UUID:
    rid = uuid.uuid4()
    db.add(TEFCAReport(
        report_id=rid, report_type=report_type,
        period_start=period_start, period_end=period_end,
        report_data=report_data, generated_by=generated_by,
        generated_at=datetime.utcnow(), methodology_version="1.0",
    ))
    await db.flush()
    return rid


# ─── Weekly progress report (SOW Task 3) ─────────────────────────────────────

async def generate_weekly_report(db, week_start: datetime, week_end: datetime,
                                 generated_by: str = "SYSTEM") -> dict:
    reviews = await _reviews_in_range(db, week_start, week_end)
    ft_counts = await _finding_type_counts(db, reviews)
    report_data = {
        "report_type": "weekly",
        "task": "SOW Task 3 — Weekly Progress Report",
        "period_start": week_start.isoformat() if week_start else None,
        "period_end": week_end.isoformat() if week_end else None,
        "total_reviews": len(reviews),
        "overall_category_counts": _overall_counts(reviews),
        "stratified_by_qhin": _stratify_by_qhin(reviews),
        "suggested_methodology_changes": _suggest_methodology_changes(ft_counts),
        "contract_info": _CONTRACT,
        "agt_does_not_adjudicate": _AGT_NOTE,
        "generated_at": datetime.utcnow().isoformat(),
    }
    rid = await _persist(db, "weekly", report_data, week_start, week_end, generated_by)
    return {"report_id": str(rid), **report_data}


# ─── Final retrospective report (SOW Task 3, 120-day) ────────────────────────

def _weekly_trend(reviews: list) -> List[dict]:
    by_week: Dict[str, Dict[str, int]] = {}
    for r in reviews:
        if not r.created_at:
            continue
        wk = r.created_at.strftime("%G-W%V")  # ISO year-week
        d = by_week.setdefault(wk, {"total": 0, "discrepant": 0})
        d["total"] += 1
        if (r.status or "").lower() != "no_discrepancy":
            d["discrepant"] += 1
    return [{"week": wk, "total": d["total"], "discrepant": d["discrepant"],
             "discrepancy_rate": (round(d["discrepant"] / d["total"], 4) if d["total"] else 0.0)}
            for wk, d in sorted(by_week.items())]


async def generate_final_report(db, period_start: datetime, period_end: datetime,
                                generated_by: str = "SYSTEM") -> dict:
    reviews = await _reviews_in_range(db, period_start, period_end)
    strat = _stratify_by_qhin(reviews)
    ft_counts = await _finding_type_counts(db, reviews)
    total = len(reviews)

    # cross-QHIN comparison: rank by compliance score
    ranked = sorted(
        [{"qhin": q, "total": s["total"], "compliance_score": s["compliance_score"],
          "non_compliant_pct": s["percentages"]["non_compliant"]} for q, s in strat.items()],
        key=lambda x: (x["compliance_score"] is None, -(x["compliance_score"] or 0)),
    )

    # sampling methodology validation (expected vs actual)
    from . import review_engine
    expected_n = review_engine.calculate_sample_size(94231)
    sampling_validation = {
        "expected_confidence": 0.95,
        "expected_sample_size_full_population": expected_n,
        "actual_reviews_in_period": total,
        "meets_or_exceeds_expected": total >= expected_n,
        "note": "Mock dataset is a subset; production run targets the full 383 sample.",
    }

    report_data = {
        "report_type": "final",
        "task": "SOW Task 3 — Final Retrospective Report (120-day)",
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "total_reviews": total,
        "overall_category_counts": _overall_counts(reviews),
        "per_qhin_breakdown": strat,
        "cross_qhin_comparison": ranked,
        "weekly_trend": _weekly_trend(reviews),
        "compliance_score_by_qhin": {q: s["compliance_score"] for q, s in strat.items()},
        "methodology_changes": {
            "suggested": _suggest_methodology_changes(ft_counts),
            "implemented": [],  # populated from prior weekly reports when available
        },
        "sampling_methodology_validation": sampling_validation,
        "contract_info": _CONTRACT,
        "agt_does_not_adjudicate": _AGT_NOTE,
        "generated_at": datetime.utcnow().isoformat(),
    }
    rid = await _persist(db, "final", report_data, period_start, period_end, generated_by)
    return {"report_id": str(rid), **report_data}


# ─── CSV export (exactly 12 columns) ─────────────────────────────────────────

CSV_COLUMNS = ["QHIN", "Entity Name", "Entity Type", "NPI", "UEI", "State",
               "Review Date", "Discrepancy Category", "Risk Level",
               "Findings Count", "Connector Results", "Status"]


async def generate_csv_export(db, report_id) -> str:
    """Render the reviews behind a report as a 12-column CSV. Entity Type and
    State come from the tefca_reviews.entity_type / entity_state columns."""
    report = (await db.execute(
        select(TEFCAReport).where(TEFCAReport.report_id == report_id)
    )).scalar_one_or_none()
    start = report.period_start if report else None
    end = report.period_end if report else None
    reviews = await _reviews_in_range(db, start, end)

    # findings per review (count + connector summary)
    ids = [r.id for r in reviews]
    findings_by_review: Dict[Any, list] = {}
    if ids:
        frows = (await db.execute(select(TEFCAFinding).where(TEFCAFinding.review_id.in_(ids)))).scalars().all()
        for f in frows:
            findings_by_review.setdefault(f.review_id, []).append(f)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in reviews:
        fs = findings_by_review.get(r.id, [])
        connector_results = "; ".join(f"{(f.connector or '').lower()}={f.finding_type}" for f in fs)
        w.writerow([
            r.qhin or "", r.entity_name or "", (r.entity_type or ""), r.npi or "", r.uei or "",
            (r.entity_state or ""),
            r.created_at.date().isoformat() if r.created_at else "",
            r.status or "", r.risk_level or "",
            len(fs), connector_results, _STATUS_MAP.get((r.status or "").lower(), "pending"),
        ])
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════
# TEFCA Task 4 — bi-weekly ongoing reviews + quarterly reports + delta detection
# (additive; the weekly/final/CSV functions above are unchanged)
# ═══════════════════════════════════════════════════════════════════════════

_CATEGORY_COLORS = {
    "no_discrepancy": "#22c55e", "minor_administrative": "#eab308",
    "inexplicable": "#f97316", "non_compliant": "#ef4444",
}
_CATEGORY_LABELS = {
    "no_discrepancy": "No Discrepancy", "minor_administrative": "Minor / Administrative",
    "inexplicable": "Inexplicable", "non_compliant": "Non-Compliant",
}


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


# ─── Delta detection ─────────────────────────────────────────────────────────

async def get_new_submissions(db, qhin_name: Optional[str] = None,
                              since_date: Optional[datetime] = None) -> list:
    """Reviews created strictly after since_date (optionally for one QHIN)."""
    q = select(TEFCAReview)
    if since_date:
        q = q.where(TEFCAReview.created_at > since_date)
    if qhin_name:
        q = q.where(TEFCAReview.qhin == qhin_name)
    return (await db.execute(q.order_by(TEFCAReview.created_at))).scalars().all()


async def get_last_biweekly_date(db, qhin_name: Optional[str] = None) -> Optional[datetime]:
    """Most recent bi-weekly watermark — per-QHIN if stored in report_data, else
    the latest bi-weekly report's period_end. Returns None if no bi-weekly run yet."""
    latest = (await db.execute(
        select(TEFCAReport).where(TEFCAReport.report_type == "biweekly")
        .order_by(TEFCAReport.generated_at.desc())
    )).scalars().first()
    if not latest:
        return None
    if qhin_name:
        wm = (latest.report_data or {}).get("watermarks", {}).get(qhin_name)
        if wm:
            return _parse_iso(wm)
    return latest.period_end


# ─── Bi-weekly ongoing review (SOW Task 4) ───────────────────────────────────

async def generate_biweekly_report(db, period_start: Optional[datetime] = None,
                                   period_end: Optional[datetime] = None,
                                   generated_by: str = "SYSTEM") -> dict:
    period_end = period_end or datetime.utcnow()
    last = await get_last_biweekly_date(db, None)
    since = period_start or last or (period_end - timedelta(days=14))
    new_reviews = [r for r in await get_new_submissions(db, None, since)
                   if (r.created_at is None or r.created_at <= period_end)]
    counts = _overall_counts(new_reviews)
    strat = _stratify_by_qhin(new_reviews)
    ft_counts = await _finding_type_counts(db, new_reviews)

    # delta vs the previous bi-weekly report
    prev = (await db.execute(
        select(TEFCAReport).where(TEFCAReport.report_type == "biweekly")
        .order_by(TEFCAReport.generated_at.desc())
    )).scalars().first()
    prev_counts = (prev.report_data or {}).get("overall_category_counts") if prev else None
    delta = ({c: counts[c] - (prev_counts or {}).get(c, 0) for c in CATEGORIES}
             if prev_counts is not None else None)

    report_data = {
        "report_type": "biweekly",
        "task": "SOW Task 4 — Bi-Weekly Ongoing Review (new submissions only)",
        "period_start": since.isoformat() if since else None,
        "period_end": period_end.isoformat(),
        "new_submissions_reviewed": len(new_reviews),
        "overall_category_counts": counts,
        "delta_vs_previous": delta,
        "stratified_by_qhin": strat,
        "suggested_methodology_changes": _suggest_methodology_changes(ft_counts),
        "watermarks": {q: period_end.isoformat() for q in strat.keys()},
        "contract_info": _CONTRACT,
        "agt_does_not_adjudicate": _AGT_NOTE,
        "generated_at": datetime.utcnow().isoformat(),
    }
    rid = await _persist(db, "biweekly", report_data, since, period_end, generated_by)
    return {"report_id": str(rid), **report_data}


# ─── Quarterly report (SOW Task 4, 90-day) — Recharts-ready ──────────────────

def _chart_reviews_by_week(reviews: list) -> list:
    by_week: dict = {}
    for r in reviews:
        if not r.created_at:
            continue
        wk = r.created_at.strftime("%G-W%V")
        d = by_week.setdefault(wk, {"total": 0, "passed": 0, "failed": 0})
        d["total"] += 1
        st = (r.status or "").lower()
        if st in ("no_discrepancy", "minor_administrative"):
            d["passed"] += 1
        elif st == "non_compliant":
            d["failed"] += 1
    return [{"week": wk, **v} for wk, v in sorted(by_week.items())]


def _chart_discrepancy_distribution(reviews: list) -> list:
    counts = _overall_counts(reviews)
    return [{"name": _CATEGORY_LABELS[c], "value": counts[c], "color": _CATEGORY_COLORS[c]}
            for c in CATEGORIES]


def _chart_risk_by_qhin(reviews: list) -> list:
    by_qhin: dict = {}
    for r in reviews:
        q = r.qhin or "Unknown QHIN"
        d = by_qhin.setdefault(q, {"low": 0, "medium": 0, "high": 0, "critical": 0})
        rl = (r.risk_level or "").lower()
        if rl in d:
            d[rl] += 1
    return [{"qhin": q, **v} for q, v in sorted(by_qhin.items())]


async def _chart_connector_health_trend(db, start, end) -> list:
    # Build the WHERE conditionally — a ":p IS NULL" comparison gives asyncpg no
    # type hint and raises AmbiguousParameterError.
    clauses, params = [], {}
    if start is not None:
        clauses.append("checked_at >= :start"); params["start"] = start
    if end is not None:
        clauses.append("checked_at <= :end"); params["end"] = end
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = ("SELECT checked_at::date AS d, lower(connector_name) AS c, "
           "round(100.0 * sum(CASE WHEN status='available' THEN 1 ELSE 0 END) / count(*), 0) AS pct "
           "FROM tefca_connector_logs" + where + " GROUP BY 1, 2 ORDER BY 1")
    rows = (await db.execute(text(sql), params)).fetchall()
    name_map = {"nppes": "nppes", "pecos": "pecos", "sam_gov": "sam", "oig_leie": "leie"}
    by_date: dict = {}
    for d, c, pct in rows:
        key = name_map.get(c)
        if not key:
            continue
        by_date.setdefault(str(d), {})[key] = float(pct)
    return [{"date": dt, "nppes": v.get("nppes", 0), "pecos": v.get("pecos", 0),
             "sam": v.get("sam", 0), "leie": v.get("leie", 0)} for dt, v in sorted(by_date.items())]


async def generate_quarterly_report(db, period_start: Optional[datetime] = None,
                                    period_end: Optional[datetime] = None,
                                    generated_by: str = "SYSTEM") -> dict:
    period_end = period_end or datetime.utcnow()
    period_start = period_start or (period_end - timedelta(days=90))
    reviews = await _reviews_in_range(db, period_start, period_end)
    strat = _stratify_by_qhin(reviews)
    counts = _overall_counts(reviews)
    total = len(reviews)

    scorecard = {}
    for q, s in strat.items():
        t = s["total"]
        passed = s["counts"]["no_discrepancy"] + s["counts"]["minor_administrative"]
        scorecard[q] = {
            "pass_rate": round(passed / t, 4) if t else 0.0,
            "compliance_score": s["compliance_score"],
            "total_reviews": t,
            "risk_distribution": {"non_compliant_pct": s["percentages"]["non_compliant"]},
        }

    # previous-quarter comparison
    prev_reviews = await _reviews_in_range(db, period_start - timedelta(days=90), period_start)
    prev_compare = None
    if prev_reviews:
        prev_counts = _overall_counts(prev_reviews)
        prev_compare = {
            "previous_total": len(prev_reviews),
            "delta": {c: counts[c] - prev_counts[c] for c in CATEGORIES},
        }

    report_data = {
        "report_type": "quarterly",
        "task": "SOW Task 4 — Quarterly Report (90-day)",
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "total_reviews": total,
        "executive_summary": (
            f"{total} reviews across {len(strat)} QHINs over the 90-day quarter: "
            f"{counts['no_discrepancy']} no-discrepancy, {counts['minor_administrative']} minor, "
            f"{counts['inexplicable']} inexplicable, {counts['non_compliant']} non-compliant."
        ),
        "overall_category_counts": counts,
        "per_qhin_scorecard": scorecard,
        "charts": {
            "reviews_by_week": _chart_reviews_by_week(reviews),
            "discrepancy_distribution": _chart_discrepancy_distribution(reviews),
            "risk_by_qhin": _chart_risk_by_qhin(reviews),
            "connector_health_trend": await _chart_connector_health_trend(db, period_start, period_end),
        },
        "previous_quarter_comparison": prev_compare,
        "contract_info": _CONTRACT,
        "agt_does_not_adjudicate": _AGT_NOTE,
        "generated_at": datetime.utcnow().isoformat(),
    }
    rid = await _persist(db, "quarterly", report_data, period_start, period_end, generated_by)
    return {"report_id": str(rid), **report_data}


# ═══════════════════════════════════════════════════════════════════════════
# TEFCA Task 5 — priority review status + quarterly reports. Additive.
# ═══════════════════════════════════════════════════════════════════════════

_PRIORITY_STATUS_FRIENDLY = {
    "ASSIGNED": "queued", "IN_PROGRESS": "in_progress", "PENDING_COR": "pending_cor",
    "RESOLVED_ACTION": "completed", "RESOLVED_NO_ACTION": "completed", "ESCALATED": "escalated",
}
_IMPACT_BY_SEVERITY = {
    "CRITICAL": "Disqualifying — entity participation should be suspended pending COR determination.",
    "HIGH": "Material — QHIN corrective action required.",
    "MEDIUM": "Moderate — administrative correction recommended.",
    "LOW": "Minor — monitor; no immediate action.",
}
_FRAMEWORK_CHANGE_BY_ROOT_CAUSE = {
    "LEIE_ACTIVE_EXCLUSION": "Mandatory OIG LEIE screening at QHIN onboarding.",
    "PECOS_PAYMENT_SUSPENSION": "Add CMS PECOS suspension check to pre-submission validation.",
    "PECOS_ENROLLMENT_DISCREPANCY": "Reconcile PECOS enrollment at submission.",
    "NPI_MISMATCH": "Require NPPES verification before TEFCA submission.",
    "ADDRESS_STATE_CONFLICT": "Add cross-source address/state validation.",
    "NAME_MISMATCH": "Add DBA/legal-name alias resolution.",
}


def _framework_changes_for(root_cause):
    return _FRAMEWORK_CHANGE_BY_ROOT_CAUSE.get(root_cause, "Review framework controls for the recurring root cause.")


async def generate_priority_status_report(db, case_id) -> Optional[dict]:
    """Per-review COR status report (formatted view; not persisted)."""
    from .models import TEFCAPriorityCase
    case = (await db.execute(
        select(TEFCAPriorityCase).where(TEFCAPriorityCase.case_id == case_id)
    )).scalar_one_or_none()
    if not case:
        return None
    status = _PRIORITY_STATUS_FRIENDLY.get(case.case_status.value if case.case_status else None,
                                           case.case_status.value if case.case_status else None)
    sev = case.severity.value if case.severity else None
    return {
        "report_type": "priority_status",
        "task": "SOW Task 5 — Priority Review Status Report (for COR)",
        "case_id": str(case.case_id),
        "cor_reference": case.cor_reference,
        "qhin": case.qhin,
        "identified_issue": case.issue_description,
        "root_cause": case.root_cause_determination,
        "root_cause_detail": case.root_cause_description,
        "severity": sev,
        "impact_assessment": _IMPACT_BY_SEVERITY.get(sev, "Assessment pending."),
        "recommendations": case.recommendations or [
            {"recommendation": "QHIN to verify and correct the flagged data, then resubmit to the RCE Directory."}],
        "prevention_recommendation": case.prevention_recommendation,
        "resolution_status": status,
        "resolution_notes": case.resolution_notes,
        "suggested_framework_changes": _framework_changes_for(case.root_cause_determination),
        "assigned_date": case.assigned_date.isoformat() if case.assigned_date else None,
        "deadline_date": case.deadline_date.isoformat() if case.deadline_date else None,
        "completed_date": case.completed_date.isoformat() if case.completed_date else None,
        "contract_info": _CONTRACT,
        "agt_does_not_adjudicate": _AGT_NOTE,
        "generated_at": datetime.utcnow().isoformat(),
    }


async def generate_priority_quarterly_report(db, period_start=None, period_end=None,
                                             generated_by: str = "SYSTEM") -> dict:
    from .models import TEFCAPriorityCase
    period_end = period_end or datetime.utcnow()
    period_start = period_start or (period_end - timedelta(days=90))
    cases = (await db.execute(
        select(TEFCAPriorityCase).where(
            TEFCAPriorityCase.assigned_date >= period_start,
            TEFCAPriorityCase.assigned_date <= period_end,
        )
    )).scalars().all()
    total = len(cases)

    completed = [c for c in cases if c.completed_date and c.assigned_date]
    avg_days = (round(sum((c.completed_date - c.assigned_date).days for c in completed) / len(completed), 1)
                if completed else None)

    by_sev = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    by_qhin: dict = {}
    rc: dict = {}
    for c in cases:
        s = c.severity.value if c.severity else None
        if s in by_sev:
            by_sev[s] += 1
        by_qhin[c.qhin or "Unknown"] = by_qhin.get(c.qhin or "Unknown", 0) + 1
        if c.root_cause_determination:
            rc[c.root_cause_determination] = rc.get(c.root_cause_determination, 0) + 1

    common_root_causes = [{"root_cause": k, "count": v} for k, v in sorted(rc.items(), key=lambda x: -x[1])]
    methodology_updates = sorted({_framework_changes_for(k) for k in rc})

    report_data = {
        "report_type": "priority_quarterly",
        "task": "SOW Task 5 — Priority Reviews Quarterly Aggregation",
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "total_priority_reviews": total,
        "average_response_time_days": avg_days,
        "distribution_by_severity": by_sev,
        "distribution_by_qhin": by_qhin,
        "common_root_causes": common_root_causes,
        "methodology_updates": methodology_updates,
        "contract_info": _CONTRACT,
        "agt_does_not_adjudicate": _AGT_NOTE,
        "generated_at": datetime.utcnow().isoformat(),
    }
    rid = await _persist(db, "priority_quarterly", report_data, period_start, period_end, generated_by)
    return {"report_id": str(rid), **report_data}
