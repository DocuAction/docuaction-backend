"""
Decision Intelligence API Routes
- Decision Bank CRUD
- Human feedback capture + reliability adjustment
- Defensibility packet export
- Institutional memory context
- Knowledge provenance
"""
import json
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import get_current_user

logger = logging.getLogger("docuaction.decision_intel")
router = APIRouter(prefix="/api/decisions", tags=["Decision Intelligence"])


class DecisionInput(BaseModel):
    decision_text: str
    decided_by: str = ""
    source_document: str = ""
    source_output_id: str = ""
    domain: str = "enterprise"
    stakeholders: List[str] = []
    context: str = ""
    confidence: float = 0.85
    ai_recommended: str = ""
    human_override: bool = False
    override_reason: str = ""


class StakeholderAction(BaseModel):
    stakeholder: str
    action: str  # align | dissent | abstain
    notes: str = ""


class FeedbackInput(BaseModel):
    output_id: str
    action: str = "summary"
    feedback_type: str = "accuracy"  # accuracy | relevance | completeness | hallucination | override
    rating: int = 3  # 1-5
    override_reason: str = ""
    corrected_content: str = ""
    original_confidence: float = 0.85


class DefensibilityRequest(BaseModel):
    output_id: str
    include_decisions: bool = True
    include_validation: bool = True


# In-memory stores (replace with DB tables in production)
_decision_bank = {}
_feedback_store = []


# ═══ DECISION BANK ═══

@router.post("/bank/create")
async def create_decision(
    req: DecisionInput,
    user=Depends(get_current_user),
):
    """Create a new Decision Bank record with stakeholder tracking."""
    from app.services.decision_intel_engine import create_decision_record

    record = create_decision_record(
        decision_text=req.decision_text,
        decided_by=req.decided_by or user.email,
        source_document=req.source_document,
        source_output_id=req.source_output_id,
        domain=req.domain,
        stakeholders=req.stakeholders,
        context=req.context,
        confidence=req.confidence,
        ai_recommended=req.ai_recommended,
        human_override=req.human_override,
        override_reason=req.override_reason,
    )

    record["created_by"] = user.email
    _decision_bank[record["decision_id"]] = record

    return record


@router.get("/bank")
async def list_decisions(
    days: int = Query(90),
    domain: str = Query(""),
    user=Depends(get_current_user),
):
    """List all decisions in the Decision Bank."""
    decisions = list(_decision_bank.values())

    if domain:
        decisions = [d for d in decisions if d.get("domain") == domain]

    # Sort by timestamp descending
    decisions.sort(key=lambda d: d.get("timestamp", ""), reverse=True)

    return {
        "total": len(decisions),
        "decisions": decisions[:50],
        "overrides": len([d for d in decisions if d.get("ai_analysis", {}).get("human_override")]),
        "avg_alignment": round(
            sum(d.get("stakeholders", {}).get("alignment_score", 0) for d in decisions) / max(len(decisions), 1), 2
        ),
    }


@router.get("/bank/{decision_id}")
async def get_decision(
    decision_id: str,
    user=Depends(get_current_user),
):
    """Get a specific decision record."""
    if decision_id not in _decision_bank:
        raise HTTPException(404, "Decision not found")
    return _decision_bank[decision_id]


@router.post("/bank/{decision_id}/stakeholder")
async def update_stakeholder(
    decision_id: str,
    req: StakeholderAction,
    user=Depends(get_current_user),
):
    """Record stakeholder alignment on a decision."""
    if decision_id not in _decision_bank:
        raise HTTPException(404, "Decision not found")

    from app.services.decision_intel_engine import update_stakeholder_alignment

    decision = _decision_bank[decision_id]
    updated = update_stakeholder_alignment(decision, req.stakeholder, req.action, req.notes)
    _decision_bank[decision_id] = updated

    return {
        "decision_id": decision_id,
        "stakeholder": req.stakeholder,
        "action": req.action,
        "alignment_score": updated["stakeholders"]["alignment_score"],
    }


# ═══ EXTRACT DECISIONS FROM OUTPUT ═══

@router.post("/extract/{output_id}")
async def extract_decisions_from_output(
    output_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Auto-extract decisions from a processed output and add to Decision Bank."""
    from app.models.database import Output, Document
    from app.services.decision_intel_engine import create_decision_record

    result = await db.execute(
        select(Output).where(Output.id == output_id, Output.user_id == user.id)
    )
    output = result.scalar_one_or_none()
    if not output:
        raise HTTPException(404, "Output not found")

    try:
        content = json.loads(output.content) if isinstance(output.content, str) else (output.content or {})
    except:
        content = {}

    # Get document name
    doc_name = "Unknown"
    if output.document_id:
        doc_result = await db.execute(select(Document).where(Document.id == output.document_id))
        doc = doc_result.scalar_one_or_none()
        if doc:
            doc_name = doc.filename

    # Extract decisions
    extracted = []
    for dec in content.get("decisions", []):
        if isinstance(dec, dict) and dec.get("decision"):
            record = create_decision_record(
                decision_text=dec["decision"],
                decided_by=dec.get("decided_by", ""),
                source_document=doc_name,
                source_output_id=output_id,
                confidence=output.confidence or 0.85,
                ai_recommended=dec["decision"],
            )
            record["created_by"] = user.email
            _decision_bank[record["decision_id"]] = record
            extracted.append(record)

    return {
        "output_id": output_id,
        "document": doc_name,
        "decisions_extracted": len(extracted),
        "decisions": extracted,
    }


# ═══ HUMAN FEEDBACK ═══

@router.post("/feedback")
async def submit_feedback(
    req: FeedbackInput,
    user=Depends(get_current_user),
):
    """Submit human feedback on an AI output. Adjusts reliability scoring."""
    from app.services.decision_intel_engine import capture_human_feedback

    feedback = capture_human_feedback(
        output_id=req.output_id,
        action=req.action,
        reviewer_email=user.email,
        original_confidence=req.original_confidence,
        feedback_type=req.feedback_type,
        rating=req.rating,
        override_reason=req.override_reason,
        corrected_content=req.corrected_content,
    )

    _feedback_store.append(feedback)

    return feedback


@router.get("/feedback/stats")
async def get_feedback_stats(user=Depends(get_current_user)):
    """Get aggregated feedback statistics for system reliability."""
    from app.services.decision_intel_engine import aggregate_feedback_stats

    stats = aggregate_feedback_stats(_feedback_store)
    return stats


@router.get("/feedback/{output_id}")
async def get_output_feedback(
    output_id: str,
    user=Depends(get_current_user),
):
    """Get all feedback for a specific output."""
    feedbacks = [f for f in _feedback_store if f.get("output_id") == output_id]
    return {"output_id": output_id, "total": len(feedbacks), "feedbacks": feedbacks}


# ═══ KNOWLEDGE PROVENANCE ═══

@router.get("/provenance/{output_id}")
async def get_provenance(
    output_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get full knowledge provenance chain for an output."""
    from app.models.database import Output, Document
    from app.services.decision_intel_engine import build_provenance_chain

    result = await db.execute(
        select(Output).where(Output.id == output_id, Output.user_id == user.id)
    )
    output = result.scalar_one_or_none()
    if not output:
        raise HTTPException(404, "Output not found")

    try:
        content = json.loads(output.content) if isinstance(output.content, str) else (output.content or {})
    except:
        content = {}

    # Get source text
    source_text = ""
    doc_name = "Unknown"
    if output.document_id:
        doc_result = await db.execute(select(Document).where(Document.id == output.document_id))
        doc = doc_result.scalar_one_or_none()
        if doc:
            doc_name = doc.filename
            source_text = getattr(doc, 'content', '') or getattr(doc, 'extracted_text', '') or ''

    provenance = build_provenance_chain(
        output_content=content,
        source_document_name=doc_name,
        source_text=source_text,
        model_used=output.model_used or "",
        confidence=output.confidence or 0.85,
        domain="enterprise",
    )

    return provenance


# ═══ DEFENSIBILITY EXPORT ═══

@router.post("/defensibility")
async def generate_defensibility_packet(
    req: DefensibilityRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Generate a Compliance-Ready Defensibility Packet for an output."""
    from app.models.database import Output, Document
    from app.services.decision_intel_engine import build_provenance_chain
    from app.services.governance_engine import governance_gate
    from app.services.decision_intel_engine import generate_defensibility_packet as _gen_packet

    result = await db.execute(
        select(Output).where(Output.id == req.output_id, Output.user_id == user.id)
    )
    output = result.scalar_one_or_none()
    if not output:
        raise HTTPException(404, "Output not found")

    try:
        content = json.loads(output.content) if isinstance(output.content, str) else (output.content or {})
    except:
        content = {}

    # Get source
    source_text = ""
    doc_name = "Unknown"
    if output.document_id:
        doc_result = await db.execute(select(Document).where(Document.id == output.document_id))
        doc = doc_result.scalar_one_or_none()
        if doc:
            doc_name = doc.filename
            source_text = getattr(doc, 'content', '') or getattr(doc, 'extracted_text', '') or ''

    # Run governance gate
    source_texts = [source_text] if source_text else []
    gov = governance_gate(content, source_texts, output.confidence or 0.85, output.model_used or "")

    # Build provenance
    provenance = build_provenance_chain(content, doc_name, source_text, output.model_used or "", output.confidence or 0.85, "enterprise")

    # Get decisions
    decisions = []
    if req.include_decisions:
        decisions = [d for d in _decision_bank.values() if d.get("provenance", {}).get("source_output_id") == req.output_id]

    # Get validation history
    validation = []
    if req.include_validation:
        validation = [f for f in _feedback_store if f.get("output_id") == req.output_id]

    packet = _gen_packet(content, gov, provenance, decisions, validation)

    return packet


# ═══ INSTITUTIONAL MEMORY ═══

@router.get("/memory")
async def get_institutional_memory(
    days: int = Query(90),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get institutional memory context from Decision Bank and outputs."""
    from app.services.decision_intel_engine import build_institutional_context
    from app.models.database import Output, Document

    cutoff = datetime.utcnow() - timedelta(days=days)

    results = await db.execute(
        select(Output).where(
            and_(Output.user_id == user.id, Output.created_at >= cutoff)
        ).order_by(desc(Output.created_at)).limit(200)
    )
    outputs = results.scalars().all()

    doc_ids = set(str(o.document_id) for o in outputs if o.document_id)
    doc_map = {}
    if doc_ids:
        docs = await db.execute(select(Document).where(Document.id.in_(doc_ids)))
        for d in docs.scalars().all():
            doc_map[str(d.id)] = d.filename

    all_outputs = {}
    for o in outputs:
        doc_name = doc_map.get(str(o.document_id), f"doc_{o.document_id}")
        try:
            content = json.loads(o.content) if isinstance(o.content, str) else (o.content or {})
        except:
            content = {}
        all_outputs[doc_name] = content

    all_decisions = list(_decision_bank.values())

    context = build_institutional_context(all_decisions, all_outputs, days)

    return context


# ═══ DECISION INTELLIGENCE STATUS ═══

@router.get("/status")
async def decision_intel_status(user=Depends(get_current_user)):
    """Get Decision Intelligence system status."""
    from app.services.decision_intel_engine import aggregate_feedback_stats

    return {
        "engine": "active",
        "version": "1.0.0",
        "decision_bank": {
            "total_decisions": len(_decision_bank),
            "overrides": len([d for d in _decision_bank.values() if d.get("ai_analysis", {}).get("human_override")]),
        },
        "feedback": aggregate_feedback_stats(_feedback_store),
        "features": {
            "decision_bank": True,
            "knowledge_provenance": True,
            "defensibility_export": True,
            "human_feedback_loop": True,
            "institutional_memory": True,
            "stakeholder_alignment": True,
        },
    }
