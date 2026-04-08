"""
Zero-Trust AI Governance API Routes
- POST /api/governance/gate — Run output through governance pipeline
- GET /api/governance/policy — Get active policy for domain
- GET /api/governance/certificate/{audit_id} — Get governance certificate
- POST /api/governance/conflicts — Detect conflicts across documents
- GET /api/governance/source-map/{output_id} — Get source traceability
- POST /api/governance/validate — Validate output against policy
"""
import json
import uuid
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.config import settings

logger = logging.getLogger("docuaction.governance")
router = APIRouter(prefix="/api/governance", tags=["AI Governance"])


class GateRequest(BaseModel):
    output_content: dict
    source_texts: list = []
    confidence: float = 0.85
    model_used: str = "claude-sonnet-4-20250514"
    domain: str = "enterprise"


class ConflictRequest(BaseModel):
    document_outputs: dict  # {doc_name: output_content}


class PolicyUpdate(BaseModel):
    domain: str = "enterprise"
    min_accuracy: Optional[float] = None
    min_confidence: Optional[float] = None
    min_source_density: Optional[int] = None
    strict_mode: Optional[bool] = None


# ═══ GOVERNANCE GATE ═══
@router.post("/gate")
async def run_governance_gate(
    req: GateRequest,
    user=Depends(get_current_user),
):
    """
    Run AI output through the zero-trust governance pipeline.
    Returns: accuracy score, reliability, violations, certificate.
    """
    from app.services.governance_engine import governance_gate

    result = governance_gate(
        output_content=req.output_content,
        source_texts=req.source_texts,
        confidence=req.confidence,
        model_used=req.model_used,
        domain=req.domain,
    )

    return {
        "gate_result": result["gate_result"],
        "approved": result["approved"],
        "accuracy": result["accuracy"],
        "reliability": result["accuracy"]["reliability"],
        "score": result["accuracy"]["accuracy_score"],
        "policy": result["policy"],
        "violations": result["violations"],
        "audit_metadata": result["audit_metadata"],
        "governance_certificate": result["governance_certificate"],
    }


# ═══ FULL GOVERNANCE PIPELINE ═══
@router.post("/pipeline")
async def run_full_pipeline(
    req: GateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Full governance pipeline: gate + conflicts + source map + hallucination filter.
    """
    from app.services.governance_engine import run_governance_pipeline

    # Get all user outputs for cross-meeting validation
    all_outputs = {}
    try:
        from app.models.database import Output, Document
        results = await db.execute(
            select(Output).where(Output.user_id == user.id).order_by(desc(Output.created_at)).limit(50)
        )
        outputs = results.scalars().all()

        doc_ids = set(str(o.document_id) for o in outputs if o.document_id)
        doc_map = {}
        if doc_ids:
            docs = await db.execute(select(Document).where(Document.id.in_(doc_ids)))
            for d in docs.scalars().all():
                doc_map[str(d.id)] = d.filename

        for o in outputs:
            doc_name = doc_map.get(str(o.document_id), f"doc_{o.document_id}")
            try:
                content = json.loads(o.content) if isinstance(o.content, str) else o.content
            except:
                content = {}
            all_outputs[doc_name] = content
    except Exception as e:
        logger.warning(f"Could not load outputs for cross-validation: {e}")

    source_documents = [{"name": f"Source {i+1}", "text": t} for i, t in enumerate(req.source_texts)]

    result = await run_governance_pipeline(
        output_content=req.output_content,
        source_texts=req.source_texts,
        source_documents=source_documents,
        confidence=req.confidence,
        model_used=req.model_used,
        domain=req.domain,
        all_outputs=all_outputs,
    )

    return result


# ═══ CONFLICT DETECTION ═══
@router.post("/conflicts")
async def detect_conflicts(
    req: ConflictRequest,
    user=Depends(get_current_user),
):
    """Detect contradictions across multiple document outputs."""
    from app.services.governance_engine import detect_conflicts as _detect

    conflicts = _detect(req.document_outputs)

    return {
        "total_conflicts": len(conflicts),
        "has_conflicts": len(conflicts) > 0,
        "conflicts": conflicts,
        "resolution_required": any(c.get("resolution_required") for c in conflicts),
        "timestamp": datetime.utcnow().isoformat(),
    }


# ═══ CONFLICT DETECTION FROM DB ═══
@router.get("/conflicts/auto")
async def auto_detect_conflicts(
    days: int = Query(30),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Auto-detect conflicts across all user outputs in the last N days."""
    from app.services.governance_engine import detect_conflicts as _detect
    from app.models.database import Output, Document
    from datetime import timedelta

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
        if doc_name not in all_outputs:
            all_outputs[doc_name] = content
        else:
            # Merge outputs from same document
            for key in content:
                if key not in all_outputs[doc_name]:
                    all_outputs[doc_name][key] = content[key]

    conflicts = _detect(all_outputs)

    return {
        "period_days": days,
        "documents_analyzed": len(all_outputs),
        "total_conflicts": len(conflicts),
        "conflicts": conflicts,
        "resolution_required": any(c.get("resolution_required") for c in conflicts),
    }


# ═══ SOURCE TRACEABILITY ═══
@router.get("/source-map/{output_id}")
async def get_source_map(
    output_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get source traceability map for an output."""
    from app.services.governance_engine import extract_source_map, compute_accuracy_score
    from app.models.database import Output, Document

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

    # Get source document text
    source_docs = []
    if output.document_id:
        doc_result = await db.execute(select(Document).where(Document.id == output.document_id))
        doc = doc_result.scalar_one_or_none()
        if doc:
            doc_text = doc.content or doc.extracted_text or ""
            if doc_text:
                source_docs.append({"name": doc.filename, "text": doc_text})

    source_map = extract_source_map(content, source_docs)

    # Compute accuracy
    source_texts = [d["text"] for d in source_docs if d.get("text")]
    accuracy = compute_accuracy_score(
        json.dumps(content),
        source_texts,
        output.confidence or 0.85,
        output.model_used or "",
    )

    return {
        "output_id": output_id,
        "document": source_docs[0]["name"] if source_docs else "Unknown",
        "accuracy": accuracy,
        "source_map": source_map,
        "verified_claims": len([s for s in source_map if s.get("verified")]),
        "total_claims": len(source_map),
        "traceability_complete": len(source_map) > 0 and all(s.get("verified") for s in source_map),
    }


# ═══ GOVERNANCE CERTIFICATE ═══
@router.get("/certificate/{audit_id}")
async def get_certificate(
    audit_id: str,
    format: str = Query("json", description="json or pdf"),
    user=Depends(get_current_user),
):
    """Get governance certificate for an audit ID."""
    # In production, certificates would be stored in DB
    # For now, return the structure
    return {
        "certificate_id": "GC-" + uuid.uuid4().hex[:8].upper(),
        "audit_id": audit_id,
        "timestamp": datetime.utcnow().isoformat(),
        "status": "Certificate generated",
        "export_format": format,
        "note": "Full certificate storage requires governance_certificates table. Contact admin.",
    }


# ═══ POLICY MANAGEMENT ═══
@router.get("/policy")
async def get_policy(
    domain: str = Query("enterprise"),
    user=Depends(get_current_user),
):
    """Get governance policy for a domain."""
    from app.services.governance_engine import get_policy as _get_policy
    policy = _get_policy(domain)
    return {"domain": domain, "policy": policy}


@router.get("/policies")
async def list_policies(user=Depends(get_current_user)):
    """List all available governance policies."""
    from app.services.governance_engine import STRICT_POLICIES
    return {
        "policies": [
            {"domain": k, "name": v.get("policy_name", k), "strict_mode": v.get("strict_mode", True),
             "min_accuracy": v.get("thresholds", {}).get("min_accuracy", 75),
             "min_confidence": v.get("thresholds", {}).get("min_confidence", 0.80)}
            for k, v in STRICT_POLICIES.items()
        ]
    }


# ═══ GOVERNANCE STATUS ═══
@router.get("/status")
async def governance_status(user=Depends(get_current_user)):
    """Get governance engine status."""
    return {
        "engine": "active",
        "version": "1.0.0",
        "strict_mode": True,
        "policies_loaded": 5,
        "domains": ["enterprise", "government", "healthcare", "legal", "financial"],
        "features": {
            "accuracy_scoring": True,
            "conflict_detection": True,
            "source_traceability": True,
            "hallucination_filter": True,
            "version_control": True,
            "governance_certificates": True,
            "policy_enforcement": True,
        },
        "accuracy_model": {
            "components": ["semantic_match", "source_density", "model_certainty"],
            "weights": {"semantic": 0.45, "density": 0.25, "certainty": 0.30},
        },
    }


# ═══ VALIDATE OUTPUT (Quick check) ═══
@router.post("/validate")
async def validate_output(
    req: GateRequest,
    user=Depends(get_current_user),
):
    """Quick validation — returns pass/fail without full pipeline."""
    from app.services.governance_engine import compute_accuracy_score, get_policy

    policy = get_policy(req.domain)
    accuracy = compute_accuracy_score(
        json.dumps(req.output_content),
        req.source_texts,
        req.confidence,
        req.model_used,
    )

    thresholds = policy.get("thresholds", {})
    passed = (
        accuracy["accuracy_score"] >= thresholds.get("min_accuracy", 75) and
        req.confidence >= thresholds.get("min_confidence", 0.80) and
        accuracy["reliability"] != "LOW"
    )

    return {
        "passed": passed,
        "accuracy_score": accuracy["accuracy_score"],
        "reliability": accuracy["reliability"],
        "policy": policy.get("policy_name", ""),
        "domain": req.domain,
        "components": accuracy["components"],
    }
