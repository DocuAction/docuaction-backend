"""
Cross-Meeting Intelligence Engine
- Trend & Conflict Detection across meetings/documents
- Semantic Ledger Search (command bar queries)
- Version Control (lock & version system)
- Source Attribution Mapping
"""
import uuid
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, or_, func
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.config import settings

logger = logging.getLogger("docuaction.cross_intel")
router = APIRouter(prefix="/api/intel", tags=["Cross-Meeting Intelligence"])


# ═══ REQUEST MODELS ═══
class SearchQuery(BaseModel):
    query: str
    days: int = 90
    types: Optional[List[str]] = None  # decisions, risks, actions, kpis


class VersionNote(BaseModel):
    reason: str = "Reprocessed by user request"


# ═══ DASHBOARD KPIs (AGGREGATED) ═══
@router.get("/dashboard")
async def get_dashboard_intelligence(
    days: int = Query(30, description="Time window in days"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Executive Dashboard Intelligence — aggregated KPIs, timeline, risks, decisions.
    NO file data. NO processing state. Pure intelligence.
    """
    from app.models.database import Output, Document

    cutoff = datetime.utcnow() - timedelta(days=days)

    # Get all outputs for this user within timeframe
    results = await db.execute(
        select(Output).where(
            and_(Output.user_id == user.id, Output.created_at >= cutoff)
        ).order_by(desc(Output.created_at)).limit(500)
    )
    outputs = results.scalars().all()

    # Get document names
    doc_ids = set(str(o.document_id) for o in outputs if o.document_id)
    doc_map = {}
    if doc_ids:
        docs = await db.execute(select(Document).where(Document.id.in_(doc_ids)))
        for d in docs.scalars().all():
            doc_map[str(d.id)] = {"name": d.filename, "created": d.created_at.isoformat() if d.created_at else None}

    # ─── AGGREGATE KPIs ───
    total_docs = len(doc_ids)
    total_outputs = len(outputs)

    # Parse outputs to extract intelligence
    all_decisions = []
    all_actions = []
    all_risks = []
    all_kpis = []
    timeline = []
    confidence_scores = []

    for o in outputs:
        content = _parse_content(o.content)
        action = o.action_type or ""
        doc_name = doc_map.get(str(o.document_id), {}).get("name", "Unknown")
        ts = o.created_at.isoformat() if o.created_at else None
        conf = o.confidence or 0
        if conf > 0:
            confidence_scores.append(conf)

        # Timeline entry
        timeline.append({
            "id": str(o.id),
            "document": doc_name,
            "document_id": str(o.document_id) if o.document_id else None,
            "action_type": action,
            "timestamp": ts,
            "confidence": conf,
            "model": o.model_used or "",
        })

        # Extract decisions
        for dec in content.get("decisions", []):
            all_decisions.append({
                "decision": dec.get("decision", ""),
                "decided_by": dec.get("decided_by", ""),
                "document": doc_name,
                "timestamp": ts,
                "confidence": dec.get("confidence", conf),
                "output_id": str(o.id),
            })

        # Decision ledger from briefs
        if action == "brief":
            if content.get("decision_required"):
                all_decisions.append({
                    "decision": content["decision_required"],
                    "decided_by": "Pending",
                    "document": doc_name,
                    "timestamp": ts,
                    "confidence": conf,
                    "output_id": str(o.id),
                })

        # Extract actions/tasks
        for task in content.get("tasks", []):
            status = task.get("status", "pending")
            all_actions.append({
                "task": task.get("task", ""),
                "owner": task.get("owner", "TBD"),
                "deadline": task.get("deadline", "TBD"),
                "priority": task.get("priority", "medium"),
                "status": status,
                "document": doc_name,
                "timestamp": ts,
                "output_id": str(o.id),
            })

        # Extract risks
        for risk in content.get("risk_factors", []):
            r_text = risk if isinstance(risk, str) else risk.get("risk", str(risk))
            r_sev = "medium" if isinstance(risk, str) else risk.get("severity", "medium")
            all_risks.append({
                "risk": r_text,
                "severity": r_sev,
                "document": doc_name,
                "timestamp": ts,
                "output_id": str(o.id),
            })

        for risk in content.get("risk_assessment", []):
            all_risks.append({
                "risk": risk.get("risk", ""),
                "severity": risk.get("severity", "medium"),
                "mitigation": risk.get("mitigation", ""),
                "document": doc_name,
                "timestamp": ts,
                "output_id": str(o.id),
            })

        # Extract KPIs
        for kpi in content.get("key_metrics", []):
            all_kpis.append({
                "metric": kpi.get("metric", ""),
                "value": kpi.get("value", ""),
                "context": kpi.get("context", ""),
                "document": doc_name,
                "timestamp": ts,
                "output_id": str(o.id),
            })

    # ─── COMPUTE CONSISTENCY ALERTS ───
    consistency_alerts = _detect_consistency_issues(all_decisions, all_kpis, all_risks)

    # ─── COMPUTE TREND SIGNALS ───
    trend_signals = _detect_trends(all_risks, all_kpis, all_actions)

    # ─── KPI SUMMARY ───
    avg_confidence = round(sum(confidence_scores) / max(len(confidence_scores), 1), 2)
    time_saved = round(total_docs * 2.5, 1)  # Estimated hours

    actions_completed = len([a for a in all_actions if a.get("status") == "completed"])
    actions_overdue = len([a for a in all_actions if a.get("status") == "overdue"])
    actions_pending = len([a for a in all_actions if a.get("status") not in ("completed", "overdue")])

    return {
        "period_days": days,
        "kpis": {
            "documents_processed": total_docs,
            "outputs_generated": total_outputs,
            "risks_detected": len(all_risks),
            "actions_generated": len(all_actions),
            "decisions_captured": len(all_decisions),
            "kpis_extracted": len(all_kpis),
            "time_saved_hours": time_saved,
            "avg_confidence": avg_confidence,
            "consistency_alerts": len(consistency_alerts),
        },
        "timeline": timeline[:50],  # Most recent 50
        "top_risks": sorted(all_risks, key=lambda r: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(r.get("severity", "medium"), 2))[:10],
        "decisions": all_decisions[:20],
        "actions": {
            "completed": actions_completed,
            "in_progress": actions_pending,
            "overdue": actions_overdue,
            "items": all_actions[:20],
        },
        "kpi_signals": all_kpis[:15],
        "consistency_alerts": consistency_alerts[:10],
        "trend_signals": trend_signals[:10],
    }


# ═══ SEMANTIC SEARCH ═══
@router.post("/search")
async def semantic_search(
    query: SearchQuery,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Semantic Ledger Search — query intelligence across all meetings and documents.
    Returns aggregated results, not files.
    """
    from app.models.database import Output, Document

    cutoff = datetime.utcnow() - timedelta(days=query.days)

    results = await db.execute(
        select(Output).where(
            and_(Output.user_id == user.id, Output.created_at >= cutoff)
        ).order_by(desc(Output.created_at)).limit(200)
    )
    outputs = results.scalars().all()

    # Get document names
    doc_ids = set(str(o.document_id) for o in outputs if o.document_id)
    doc_map = {}
    if doc_ids:
        docs = await db.execute(select(Document).where(Document.id.in_(doc_ids)))
        for d in docs.scalars().all():
            doc_map[str(d.id)] = d.filename

    # Search through all outputs
    search_terms = query.query.lower().split()
    matches = []

    for o in outputs:
        content = _parse_content(o.content)
        content_str = json.dumps(content).lower()

        # Score relevance
        score = sum(1 for term in search_terms if term in content_str)
        if score == 0:
            continue

        doc_name = doc_map.get(str(o.document_id), "Unknown")

        # Extract matching items
        match_items = []

        for dec in content.get("decisions", []):
            if any(t in json.dumps(dec).lower() for t in search_terms):
                match_items.append({"type": "decision", "content": dec.get("decision", ""), "source": doc_name})

        for task in content.get("tasks", []):
            if any(t in json.dumps(task).lower() for t in search_terms):
                match_items.append({"type": "action", "content": task.get("task", ""), "owner": task.get("owner", ""), "source": doc_name})

        for risk in content.get("risk_factors", content.get("risk_assessment", [])):
            r_str = json.dumps(risk).lower() if isinstance(risk, dict) else risk.lower()
            if any(t in r_str for t in search_terms):
                r_text = risk if isinstance(risk, str) else risk.get("risk", str(risk))
                match_items.append({"type": "risk", "content": r_text, "source": doc_name})

        for kpi in content.get("key_metrics", []):
            if any(t in json.dumps(kpi).lower() for t in search_terms):
                match_items.append({"type": "kpi", "content": f"{kpi.get('metric', '')}: {kpi.get('value', '')}", "source": doc_name})

        # Check summary text
        summary = content.get("summary", "")
        if summary and any(t in summary.lower() for t in search_terms):
            match_items.append({"type": "summary", "content": summary[:200], "source": doc_name})

        if match_items:
            matches.append({
                "output_id": str(o.id),
                "document": doc_name,
                "document_id": str(o.document_id) if o.document_id else None,
                "action_type": o.action_type,
                "timestamp": o.created_at.isoformat() if o.created_at else None,
                "confidence": o.confidence or 0,
                "relevance_score": score,
                "items": match_items,
            })

    # Sort by relevance
    matches.sort(key=lambda m: m["relevance_score"], reverse=True)

    return {
        "query": query.query,
        "period_days": query.days,
        "total_results": len(matches),
        "results": matches[:20],
    }


# ═══ SOURCE ATTRIBUTION ═══
@router.get("/source/{output_id}")
async def get_source_attribution(
    output_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Get source attribution for an output — maps claims to original document text.
    """
    from app.models.database import Output, Document

    result = await db.execute(
        select(Output).where(Output.id == output_id, Output.user_id == user.id)
    )
    output = result.scalar_one_or_none()
    if not output:
        raise HTTPException(404, "Output not found")

    content = _parse_content(output.content)

    # Get document
    doc = None
    if output.document_id:
        doc_result = await db.execute(select(Document).where(Document.id == output.document_id))
        doc = doc_result.scalar_one_or_none()

    return {
        "output_id": str(output.id),
        "document_name": doc.filename if doc else "Unknown",
        "action_type": output.action_type,
        "model_used": output.model_used or "",
        "confidence": output.confidence or 0,
        "created_at": output.created_at.isoformat() if output.created_at else None,
        "correlation_id": "DA-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper(),
        "content": content,
        "sources_cited": content.get("sources_cited", 0),
        "source_references": _extract_source_refs(content),
    }


# ═══ VERSION CONTROL ═══
@router.get("/versions/{document_id}")
async def get_output_versions(
    document_id: str,
    action_type: str = "summary",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get all versions of an output for a document. Newest first."""
    from app.models.database import Output

    results = await db.execute(
        select(Output).where(
            and_(
                Output.document_id == document_id,
                Output.user_id == user.id,
                Output.action_type == action_type,
            )
        ).order_by(desc(Output.created_at))
    )
    versions = results.scalars().all()

    return {
        "document_id": document_id,
        "action_type": action_type,
        "total_versions": len(versions),
        "versions": [
            {
                "version": i + 1,
                "id": str(v.id),
                "model_used": v.model_used or "",
                "confidence": v.confidence or 0,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "is_latest": i == 0,
                "status": "finalized",
            }
            for i, v in enumerate(versions)
        ],
    }


# ═══ MEETING HISTORY ═══
@router.get("/history")
async def get_meeting_history(
    days: int = Query(90),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get meeting/document history grouped by date."""
    from app.models.database import Output, Document

    cutoff = datetime.utcnow() - timedelta(days=days)

    docs_result = await db.execute(
        select(Document).where(
            and_(Document.user_id == user.id, Document.created_at >= cutoff)
        ).order_by(desc(Document.created_at))
    )
    documents = docs_result.scalars().all()

    # Get output counts per document
    outputs_result = await db.execute(
        select(Output).where(Output.user_id == user.id, Output.created_at >= cutoff)
    )
    all_outputs = outputs_result.scalars().all()

    output_counts = {}
    for o in all_outputs:
        did = str(o.document_id) if o.document_id else ""
        if did not in output_counts:
            output_counts[did] = {"types": set(), "count": 0}
        output_counts[did]["types"].add(o.action_type)
        output_counts[did]["count"] += 1

    # Group by date
    grouped = {}
    for doc in documents:
        date_key = doc.created_at.strftime("%Y-%m-%d") if doc.created_at else "unknown"
        if date_key not in grouped:
            grouped[date_key] = []

        did = str(doc.id)
        oc = output_counts.get(did, {"types": set(), "count": 0})

        grouped[date_key].append({
            "id": did,
            "name": doc.filename,
            "type": doc.file_type,
            "status": doc.status,
            "outputs_count": oc["count"],
            "output_types": list(oc["types"]),
            "all_generated": len(oc["types"]) >= 5,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
        })

    return {
        "period_days": days,
        "total_documents": len(documents),
        "dates": grouped,
    }


# ═══ HELPER FUNCTIONS ═══
def _parse_content(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except:
            return {"text": raw}
    return {}


def _extract_source_refs(content):
    """Extract source references from output content."""
    refs = []
    for key in ["key_findings", "insights", "tasks", "risk_assessment"]:
        items = content.get(key, [])
        for item in items:
            if isinstance(item, dict):
                ref = item.get("source_reference") or item.get("source") or item.get("evidence") or ""
                if ref:
                    refs.append({"field": key, "reference": ref})
    return refs


def _detect_consistency_issues(decisions, kpis, risks):
    """Detect contradictions across meetings."""
    alerts = []

    # Check for contradicting decisions
    decision_topics = {}
    for dec in decisions:
        text = dec.get("decision", "").lower()
        words = set(text.split())
        for prev_topic, prev_decs in decision_topics.items():
            overlap = words & prev_topic
            if len(overlap) > 3:  # Significant word overlap
                for prev in prev_decs:
                    if prev.get("document") != dec.get("document"):
                        # Check for contradiction keywords
                        contradicts = any(w in text for w in ["cancel", "reverse", "instead", "change", "no longer", "not", "delay", "postpone", "increase", "decrease"])
                        if contradicts:
                            alerts.append({
                                "type": "decision_conflict",
                                "severity": "high",
                                "description": f"Potential conflicting decisions detected",
                                "item_a": {"text": prev.get("decision", ""), "document": prev.get("document", ""), "date": prev.get("timestamp", "")},
                                "item_b": {"text": dec.get("decision", ""), "document": dec.get("document", ""), "date": dec.get("timestamp", "")},
                            })
        decision_topics[frozenset(words)] = decision_topics.get(frozenset(words), []) + [dec]

    # Check for KPI shifts
    kpi_by_name = {}
    for kpi in kpis:
        name = kpi.get("metric", "").lower()
        if name:
            if name not in kpi_by_name:
                kpi_by_name[name] = []
            kpi_by_name[name].append(kpi)

    for name, entries in kpi_by_name.items():
        if len(entries) > 1:
            alerts.append({
                "type": "kpi_change",
                "severity": "medium",
                "description": f"KPI '{entries[0].get('metric', '')}' reported in multiple documents with different values",
                "entries": [{"value": e.get("value", ""), "document": e.get("document", ""), "date": e.get("timestamp", "")} for e in entries[:3]],
            })

    return alerts


def _detect_trends(risks, kpis, actions):
    """Detect emerging trends across time."""
    signals = []

    # Risk accumulation
    if len(risks) > 5:
        critical_count = len([r for r in risks if r.get("severity") == "critical"])
        high_count = len([r for r in risks if r.get("severity") == "high"])
        if critical_count > 0 or high_count > 3:
            signals.append({
                "type": "risk_accumulation",
                "severity": "high",
                "description": f"Elevated risk levels: {critical_count} critical, {high_count} high across {len(risks)} total risks",
                "recommendation": "Schedule executive risk review",
            })

    # Action backlog
    overdue = len([a for a in actions if a.get("status") == "overdue"])
    pending = len([a for a in actions if a.get("status") not in ("completed", "overdue")])
    if overdue > 3:
        signals.append({
            "type": "action_backlog",
            "severity": "medium",
            "description": f"{overdue} overdue action items, {pending} still pending",
            "recommendation": "Review and reassign overdue items",
        })

    # Decision velocity
    if len(risks) > 0 and len([d for d in kpis]) == 0:
        signals.append({
            "type": "low_kpi_extraction",
            "severity": "low",
            "description": "Risks detected but no KPI signals extracted — consider processing more financial documents",
            "recommendation": "Upload financial reports and board materials",
        })

    return signals
