"""
DocuAction — WOW Feature API Routes
Endpoints for multi-document comparison, structured extraction,
explainability, and cross-document memory.

These extend the existing API — no rewrites.
"""
import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.database import Document

logger = logging.getLogger("docuaction.wow")
router = APIRouter(tags=["Intelligence Engine"])

# In-memory stores (Phase 1)
_comparisons = {}
_extractions = {}
_automations = {}


# ═══════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════

class CompareRequest(BaseModel):
    document_ids: List[str] = Field(..., min_length=2, max_length=10, description="2-10 document IDs to compare")
    mode: str = Field("general", description="Comparison mode: contract_review, policy_compliance, proposal_evaluation, version_diff, general")
    focus_areas: Optional[List[str]] = Field(None, description="Custom focus areas for comparison")

class ExtractRequest(BaseModel):
    document_id: str = Field(..., description="Document ID to extract from")
    template: str = Field("general", description="Extraction template: invoice, contract, claim, resume, general")
    custom_fields: Optional[List[dict]] = Field(None, description="Custom fields to extract")

class AutomationRule(BaseModel):
    name: str = Field(..., description="Rule name")
    trigger_type: str = Field(..., description="Type: risk_threshold, missing_field, conflict_detected, deadline_approaching")
    trigger_value: str = Field(..., description="Trigger value (e.g., '70' for risk > 70)")
    action_type: str = Field(..., description="Action: notify, assign, escalate, flag, block")
    action_target: str = Field("", description="Target email, user, or channel")
    enabled: bool = Field(True, description="Whether rule is active")


# ═══════════════════════════════════════════════════════
# 1. MULTI-DOCUMENT COMPARISON
# ═══════════════════════════════════════════════════════

@router.post("/api/compare-documents")
async def compare_documents_endpoint(
    request: CompareRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Compare 2-10 documents. Detects conflicts, inconsistencies,
    missing clauses, and generates risk scoring.
    
    WOW: No competitor does multi-doc comparison with governance built in.
    """
    # Load documents
    documents = []
    for doc_id in request.document_ids:
        result = await db.execute(
            select(Document).where(Document.id == doc_id, Document.user_id == user.id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(404, f"Document {doc_id} not found")

        # Extract text
        from app.services.document_extractor import extract_text
        try:
            text = await extract_text(doc.file_path, doc.file_type)
        except Exception as e:
            raise HTTPException(500, f"Failed to read {doc.filename}: {str(e)}")

        if not text or len(text.strip()) < 20:
            raise HTTPException(400, f"Could not extract text from {doc.filename}")

        documents.append({
            "id": str(doc.id),
            "name": doc.filename,
            "text": text,
        })

    # Run comparison
    from app.services.comparison_engine import compare_documents
    result = await compare_documents(documents, request.mode, request.focus_areas)

    # Store result
    _comparisons[result["comparison_id"]] = {
        **result,
        "user_id": str(user.id),
    }

    # Check automation rules
    risk_score = result.get("risk_assessment", {}).get("overall_risk_score", 0)
    conflicts = result.get("conflicts", [])
    triggered = _check_automation_rules(risk_score, conflicts, str(user.id))
    if triggered:
        result["automations_triggered"] = triggered

    return result


@router.get("/api/compare-documents/{comparison_id}")
async def get_comparison(comparison_id: str, user=Depends(get_current_user)):
    """Retrieve a previous comparison result."""
    comp = _comparisons.get(comparison_id)
    if not comp:
        raise HTTPException(404, "Comparison not found")
    if comp.get("user_id") != str(user.id):
        raise HTTPException(403, "Access denied")
    return comp


@router.get("/api/compare-documents")
async def list_comparisons(
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
):
    """List all comparisons for the current user."""
    user_comps = [c for c in _comparisons.values() if c.get("user_id") == str(user.id)]
    user_comps.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {"comparisons": user_comps[:limit], "total": len(user_comps)}


@router.get("/api/comparison-modes")
async def list_comparison_modes(user=Depends(get_current_user)):
    """List available comparison modes."""
    from app.services.comparison_engine import COMPARISON_MODES
    return {
        "modes": [{"id": k, "label": v["label"], "focus": v["focus"]} for k, v in COMPARISON_MODES.items()]
    }


# ═══════════════════════════════════════════════════════
# 2. STRUCTURED EXTRACTION (EXPLAINABLE AI)
# ═══════════════════════════════════════════════════════

@router.post("/api/extract-structured")
async def extract_structured_endpoint(
    request: ExtractRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Extract structured fields from a document using templates.
    Returns per-field confidence scores, source text, and reasoning.
    
    WOW: Full explainability — every field has a confidence score,
    source location, and reasoning chain.
    """
    # Load document
    result = await db.execute(
        select(Document).where(Document.id == request.document_id, Document.user_id == user.id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    from app.services.document_extractor import extract_text
    try:
        text = await extract_text(doc.file_path, doc.file_type)
    except Exception as e:
        raise HTTPException(500, f"Failed to read document: {str(e)}")

    if not text or len(text.strip()) < 20:
        raise HTTPException(400, "Could not extract text from document")

    # Run structured extraction
    from app.services.comparison_engine import extract_structured
    extraction = await extract_structured(text, request.template, request.custom_fields)

    # Record patterns for cross-document memory
    from app.services.comparison_engine import record_document_pattern, detect_anomalies
    pattern = record_document_pattern(
        document_id=str(doc.id),
        document_name=doc.filename,
        extracted_fields=extraction.get("fields", []),
        user_id=str(user.id),
    )
    extraction["memory"] = pattern

    # Check for anomalies
    anomalies = detect_anomalies(extraction.get("fields", []))
    if anomalies:
        extraction["anomalies"] = anomalies

    # Store
    _extractions[extraction["extraction_id"]] = {
        **extraction,
        "user_id": str(user.id),
        "document_id": str(doc.id),
        "document_name": doc.filename,
    }

    return extraction


@router.get("/api/extract-structured/{extraction_id}")
async def get_extraction(extraction_id: str, user=Depends(get_current_user)):
    """Retrieve a previous extraction result."""
    ext = _extractions.get(extraction_id)
    if not ext:
        raise HTTPException(404, "Extraction not found")
    if ext.get("user_id") != str(user.id):
        raise HTTPException(403, "Access denied")
    return ext


@router.get("/api/extraction-templates")
async def list_extraction_templates(user=Depends(get_current_user)):
    """List available extraction templates."""
    from app.services.comparison_engine import EXTRACTION_TEMPLATES
    return {
        "templates": [
            {"id": k, "label": v["label"], "field_count": len(v["fields"]), "fields": v["fields"]}
            for k, v in EXTRACTION_TEMPLATES.items()
        ]
    }


# ═══════════════════════════════════════════════════════
# 3. ACTION AUTOMATION ENGINE
# ═══════════════════════════════════════════════════════

@router.post("/api/automations/rules")
async def create_automation_rule(
    rule: AutomationRule,
    user=Depends(get_current_user),
):
    """
    Create an automation rule that triggers actions from insights.
    
    WOW: Insight → Decision → Action loop is fully automated.
    Example: If risk > 70% → auto-assign to compliance team.
    """
    rule_id = "RULE-" + __import__("uuid").uuid4().hex[:8].upper()

    stored_rule = {
        "rule_id": rule_id,
        "user_id": str(user.id),
        "name": rule.name,
        "trigger_type": rule.trigger_type,
        "trigger_value": rule.trigger_value,
        "action_type": rule.action_type,
        "action_target": rule.action_target,
        "enabled": rule.enabled,
        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
        "triggered_count": 0,
        "last_triggered": None,
    }

    _automations[rule_id] = stored_rule
    return stored_rule


@router.get("/api/automations/rules")
async def list_automation_rules(user=Depends(get_current_user)):
    """List all automation rules for the current user."""
    user_rules = [r for r in _automations.values() if r.get("user_id") == str(user.id)]
    return {"rules": user_rules, "total": len(user_rules)}


@router.delete("/api/automations/rules/{rule_id}")
async def delete_automation_rule(rule_id: str, user=Depends(get_current_user)):
    """Delete an automation rule."""
    rule = _automations.get(rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    if rule.get("user_id") != str(user.id):
        raise HTTPException(403, "Access denied")
    del _automations[rule_id]
    return {"detail": "Rule deleted"}


def _check_automation_rules(risk_score: float, conflicts: list, user_id: str) -> list:
    """Check if any automation rules should fire based on results."""
    triggered = []

    for rule in _automations.values():
        if rule.get("user_id") != user_id or not rule.get("enabled"):
            continue

        fired = False

        if rule["trigger_type"] == "risk_threshold":
            threshold = float(rule["trigger_value"])
            if risk_score >= threshold:
                fired = True

        elif rule["trigger_type"] == "conflict_detected":
            min_conflicts = int(rule["trigger_value"]) if rule["trigger_value"].isdigit() else 1
            if len(conflicts) >= min_conflicts:
                fired = True

        if fired:
            rule["triggered_count"] += 1
            rule["last_triggered"] = __import__("datetime").datetime.utcnow().isoformat()
            triggered.append({
                "rule_id": rule["rule_id"],
                "rule_name": rule["name"],
                "action_type": rule["action_type"],
                "action_target": rule["action_target"],
                "trigger_reason": f"{rule['trigger_type']} = {rule['trigger_value']}",
            })
            logger.info(f"Automation triggered: {rule['name']} → {rule['action_type']}")

    return triggered


# ═══════════════════════════════════════════════════════
# 4. CROSS-DOCUMENT MEMORY
# ═══════════════════════════════════════════════════════

@router.get("/api/document-memory")
async def get_document_memory(user=Depends(get_current_user)):
    """
    View the system's learned patterns across documents.
    Shows known entities, recurring patterns, and relationships.
    """
    from app.services.comparison_engine import _document_memory

    return {
        "total_patterns": len(_document_memory["patterns"]),
        "total_entities": len(_document_memory["entities"]),
        "total_relationships": len(_document_memory["relationships"]),
        "recent_patterns": _document_memory["patterns"][-10:],
        "top_entities": dict(
            sorted(
                _document_memory["entities"].items(),
                key=lambda x: len(x[1].get("documents", [])),
                reverse=True,
            )[:20]
        ),
    }


@router.get("/api/document-memory/anomalies")
async def check_anomalies(
    document_id: str = Query(..., description="Document ID to check"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Check a document's extracted data against historical patterns."""
    # Find the extraction for this document
    doc_extractions = [
        e for e in _extractions.values()
        if e.get("document_id") == document_id and e.get("user_id") == str(user.id)
    ]

    if not doc_extractions:
        raise HTTPException(404, "No extractions found for this document")

    latest = doc_extractions[-1]
    from app.services.comparison_engine import detect_anomalies
    anomalies = detect_anomalies(latest.get("fields", []))

    return {
        "document_id": document_id,
        "extraction_id": latest["extraction_id"],
        "anomalies": anomalies,
        "total_anomalies": len(anomalies),
    }
