"""
DocuAction — Multi-Document Intelligence Engine
WOW Feature #1: No competitor combines multi-doc comparison with governance.

Capabilities:
  - Compare 2-10 documents simultaneously
  - Detect conflicts, inconsistencies, missing clauses
  - Generate per-document and cross-document risk scores
  - Track document relationships (parent/child, version, conflicting)
  - Produce structured diff with field-level confidence
  
Architecture: Extends existing document_extractor + ai_engine.
Database: Uses new document_relationships table.
"""
import json
import uuid
import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger("docuaction.comparison")


# ═══════════════════════════════════════════════════════
# COMPARISON TYPES
# ═══════════════════════════════════════════════════════

COMPARISON_MODES = {
    "contract_review": {
        "label": "Contract Comparison",
        "focus": "terms, obligations, deadlines, penalties, indemnification, liability caps, IP ownership, termination clauses, renewal terms, governing law",
        "risk_keywords": ["indemnif", "terminat", "penalt", "liabilit", "breach", "default", "waiver", "force majeure"],
    },
    "policy_compliance": {
        "label": "Policy Compliance Check",
        "focus": "policy requirements vs implementation documents, missing controls, non-compliant sections, gaps in coverage",
        "risk_keywords": ["shall", "must", "required", "mandatory", "prohibited", "violation"],
    },
    "proposal_evaluation": {
        "label": "Proposal Evaluation",
        "focus": "pricing differences, scope variations, timeline discrepancies, resource commitments, deliverable definitions, SLA terms",
        "risk_keywords": ["cost", "price", "deliver", "milestone", "payment", "scope"],
    },
    "version_diff": {
        "label": "Version Comparison",
        "focus": "what changed between document versions, added sections, removed sections, modified terms, date changes",
        "risk_keywords": ["amend", "modif", "revis", "update", "change", "new", "remov"],
    },
    "general": {
        "label": "General Comparison",
        "focus": "key themes, factual differences, contradictions, missing information, structural differences",
        "risk_keywords": [],
    },
}


# ═══════════════════════════════════════════════════════
# CORE COMPARISON ENGINE
# ═══════════════════════════════════════════════════════

async def compare_documents(
    documents: List[Dict[str, str]],
    mode: str = "general",
    focus_areas: Optional[List[str]] = None,
) -> dict:
    """
    Compare 2-10 documents using Claude AI.
    
    Args:
        documents: List of {"id": str, "name": str, "text": str}
        mode: Comparison mode (contract_review, policy_compliance, etc.)
        focus_areas: Optional custom focus areas
    
    Returns:
        Structured comparison with conflicts, risks, and recommendations.
    """
    comparison_id = "CMP-" + uuid.uuid4().hex[:8].upper()
    start_time = datetime.utcnow()

    if len(documents) < 2:
        raise ValueError("At least 2 documents required for comparison")
    if len(documents) > 10:
        raise ValueError("Maximum 10 documents per comparison")

    config = COMPARISON_MODES.get(mode, COMPARISON_MODES["general"])

    # Build the comparison prompt
    doc_sections = []
    for i, doc in enumerate(documents):
        text = doc["text"][:25000]  # Cap each doc at 25K chars
        doc_sections.append(f"=== DOCUMENT {i+1}: {doc['name']} ===\n{text}\n")

    focus = focus_areas or [config["focus"]]
    focus_text = ", ".join(focus) if isinstance(focus, list) else focus

    prompt = f"""You are an enterprise document intelligence system performing a {config['label']}.

COMPARISON MODE: {config['label']}
FOCUS AREAS: {focus_text}
NUMBER OF DOCUMENTS: {len(documents)}

{chr(10).join(doc_sections)}

Analyze these documents and produce a STRUCTURED comparison. Return ONLY valid JSON with this exact schema:

{{
  "summary": "2-3 sentence executive summary of the comparison",
  "documents_analyzed": {len(documents)},
  "comparison_mode": "{mode}",
  
  "conflicts": [
    {{
      "id": "C1",
      "severity": "critical|high|medium|low",
      "category": "string (e.g., 'pricing', 'timeline', 'liability', 'scope')",
      "description": "Clear description of the conflict",
      "document_a": "{documents[0]['name']}",
      "document_a_text": "Exact text from document A",
      "document_b": "{documents[1]['name'] if len(documents) > 1 else ''}",
      "document_b_text": "Exact text from document B",
      "recommendation": "How to resolve this conflict",
      "risk_score": 0-100
    }}
  ],
  
  "missing_elements": [
    {{
      "id": "M1",
      "severity": "critical|high|medium|low",
      "element": "What is missing",
      "expected_in": "Which document should have it",
      "present_in": "Which document has it (if any)",
      "recommendation": "What to do about it"
    }}
  ],
  
  "key_differences": [
    {{
      "category": "string",
      "document_values": {{"doc1_name": "value in doc1", "doc2_name": "value in doc2"}},
      "significance": "high|medium|low",
      "analysis": "Why this difference matters"
    }}
  ],
  
  "risk_assessment": {{
    "overall_risk_score": 0-100,
    "risk_level": "CRITICAL|HIGH|MEDIUM|LOW",
    "risk_factors": ["list of risk factors"],
    "recommendations": ["prioritized list of actions"]
  }},
  
  "field_comparison": [
    {{
      "field_name": "string (e.g., 'effective_date', 'total_value', 'termination_notice')",
      "values": {{"doc1_name": "value or null", "doc2_name": "value or null"}},
      "match": true/false,
      "confidence": 0.0-1.0
    }}
  ]
}}

Be thorough. Find EVERY conflict, inconsistency, and missing element. Assign accurate risk scores.
Return ONLY the JSON object, no markdown, no explanation."""

    # Call Claude AI
    try:
        import anthropic
        from app.core.config import settings

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract JSON from response
        response_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                response_text += block.text

        # Parse JSON
        response_text = response_text.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
        
        ai_result = json.loads(response_text)

    except json.JSONDecodeError:
        # Fallback: structural comparison without AI
        ai_result = _fallback_comparison(documents, config)
    except Exception as e:
        logger.error(f"AI comparison failed: {e}")
        ai_result = _fallback_comparison(documents, config)

    processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

    # Enhance with metadata
    result = {
        "comparison_id": comparison_id,
        "timestamp": datetime.utcnow().isoformat(),
        "processing_time_ms": round(processing_time, 1),
        "mode": mode,
        "mode_label": config["label"],
        "documents": [{"id": d["id"], "name": d["name"], "word_count": len(d["text"].split())} for d in documents],

        # AI results
        **ai_result,

        # Governance
        "governance": {
            "correlation_id": "DA-" + uuid.uuid4().hex[:4].upper() + "-CMP",
            "hash": hashlib.sha256(f"{comparison_id}{''.join(d['id'] for d in documents)}".encode()).hexdigest()[:16],
            "ai_disclosure": "This comparison was generated by AI. Human review required before any contractual or legal action.",
            "hitl_required": True,
            "model_used": "claude-sonnet-4-20250514",
        },
    }

    logger.info(f"Comparison {comparison_id}: {len(documents)} docs, mode={mode}, "
                f"conflicts={len(ai_result.get('conflicts', []))}, "
                f"risk={ai_result.get('risk_assessment', {}).get('overall_risk_score', 'N/A')}")

    return result


def _fallback_comparison(documents: List[Dict], config: dict) -> dict:
    """Structural comparison when AI is unavailable."""
    # Basic word overlap analysis
    word_sets = []
    for doc in documents:
        words = set(doc["text"].lower().split())
        word_sets.append(words)

    # Find unique terms per document
    key_differences = []
    for i, doc in enumerate(documents):
        unique = word_sets[i] - set.union(*(word_sets[j] for j in range(len(word_sets)) if j != i))
        if len(unique) > 5:
            key_differences.append({
                "category": "unique_terminology",
                "document_values": {doc["name"]: f"{len(unique)} unique terms"},
                "significance": "medium",
                "analysis": f"Document contains {len(unique)} terms not found in other documents",
            })

    # Check for risk keywords
    risk_hits = []
    for keyword in config.get("risk_keywords", []):
        for doc in documents:
            if keyword.lower() in doc["text"].lower():
                risk_hits.append({"keyword": keyword, "document": doc["name"]})

    risk_score = min(100, len(risk_hits) * 10)

    return {
        "summary": f"Structural comparison of {len(documents)} documents. AI-powered semantic analysis unavailable — showing basic structural analysis.",
        "documents_analyzed": len(documents),
        "comparison_mode": config.get("label", "General"),
        "conflicts": [],
        "missing_elements": [],
        "key_differences": key_differences,
        "risk_assessment": {
            "overall_risk_score": risk_score,
            "risk_level": "HIGH" if risk_score >= 60 else "MEDIUM" if risk_score >= 30 else "LOW",
            "risk_factors": [f"Risk keyword '{h['keyword']}' found in {h['document']}" for h in risk_hits[:5]],
            "recommendations": ["Run AI-powered comparison for detailed analysis"],
        },
        "field_comparison": [],
    }


# ═══════════════════════════════════════════════════════
# STRUCTURED EXTRACTION WITH TEMPLATES
# ═══════════════════════════════════════════════════════

# Pre-built extraction templates
EXTRACTION_TEMPLATES = {
    "invoice": {
        "label": "Invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "invoice_date", "type": "date", "required": True},
            {"name": "due_date", "type": "date", "required": True},
            {"name": "vendor_name", "type": "string", "required": True},
            {"name": "vendor_address", "type": "string", "required": False},
            {"name": "buyer_name", "type": "string", "required": True},
            {"name": "subtotal", "type": "currency", "required": True},
            {"name": "tax_amount", "type": "currency", "required": False},
            {"name": "total_amount", "type": "currency", "required": True},
            {"name": "payment_terms", "type": "string", "required": False},
            {"name": "line_items", "type": "table", "required": False},
        ],
    },
    "contract": {
        "label": "Contract",
        "fields": [
            {"name": "contract_title", "type": "string", "required": True},
            {"name": "parties", "type": "list", "required": True},
            {"name": "effective_date", "type": "date", "required": True},
            {"name": "expiration_date", "type": "date", "required": True},
            {"name": "contract_value", "type": "currency", "required": False},
            {"name": "governing_law", "type": "string", "required": False},
            {"name": "termination_notice_days", "type": "number", "required": False},
            {"name": "auto_renewal", "type": "boolean", "required": False},
            {"name": "key_obligations", "type": "list", "required": False},
            {"name": "penalty_clauses", "type": "list", "required": False},
        ],
    },
    "claim": {
        "label": "Insurance/Healthcare Claim",
        "fields": [
            {"name": "claim_number", "type": "string", "required": True},
            {"name": "claimant_name", "type": "string", "required": True},
            {"name": "date_of_service", "type": "date", "required": True},
            {"name": "date_filed", "type": "date", "required": True},
            {"name": "diagnosis_codes", "type": "list", "required": False},
            {"name": "procedure_codes", "type": "list", "required": False},
            {"name": "billed_amount", "type": "currency", "required": True},
            {"name": "approved_amount", "type": "currency", "required": False},
            {"name": "provider_name", "type": "string", "required": True},
            {"name": "payer_name", "type": "string", "required": True},
        ],
    },
    "resume": {
        "label": "Resume/CV",
        "fields": [
            {"name": "full_name", "type": "string", "required": True},
            {"name": "email", "type": "string", "required": True},
            {"name": "phone", "type": "string", "required": False},
            {"name": "location", "type": "string", "required": False},
            {"name": "summary", "type": "text", "required": False},
            {"name": "work_experience", "type": "table", "required": True},
            {"name": "education", "type": "table", "required": True},
            {"name": "skills", "type": "list", "required": False},
            {"name": "certifications", "type": "list", "required": False},
        ],
    },
}


async def extract_structured(
    document_text: str,
    template: str = "general",
    custom_fields: Optional[List[Dict]] = None,
) -> dict:
    """
    Extract structured fields from a document using a template.
    Returns per-field values with confidence scores and source locations.
    
    WOW Factor: Per-field confidence + source traceability = explainable AI.
    """
    extraction_id = "EXT-" + uuid.uuid4().hex[:8].upper()
    start_time = datetime.utcnow()

    # Get template fields
    if custom_fields:
        fields = custom_fields
        template_label = "Custom Template"
    elif template in EXTRACTION_TEMPLATES:
        fields = EXTRACTION_TEMPLATES[template]["fields"]
        template_label = EXTRACTION_TEMPLATES[template]["label"]
    else:
        fields = [{"name": "key_data", "type": "text", "required": False}]
        template_label = "General Extraction"

    field_descriptions = json.dumps(fields, indent=2)

    prompt = f"""You are an enterprise document extraction system. Extract structured data from this document.

TEMPLATE: {template_label}
FIELDS TO EXTRACT:
{field_descriptions}

DOCUMENT:
{document_text[:40000]}

Return ONLY valid JSON with this schema:
{{
  "fields": [
    {{
      "field_name": "string",
      "value": "extracted value or null if not found",
      "confidence": 0.0-1.0,
      "source_text": "exact text from document that this value was extracted from (max 100 chars)",
      "reasoning": "1-sentence explanation of why this value was extracted",
      "data_type": "string|date|currency|number|boolean|list|table|text",
      "is_required": true/false,
      "status": "found|not_found|uncertain"
    }}
  ],
  "tables_found": [
    {{
      "table_name": "descriptive name",
      "headers": ["col1", "col2"],
      "rows": [["val1", "val2"]],
      "confidence": 0.0-1.0
    }}
  ],
  "extraction_quality": {{
    "completeness": 0.0-1.0,
    "required_fields_found": 0,
    "required_fields_total": 0,
    "overall_confidence": 0.0-1.0
  }}
}}

Be precise. Every confidence score must reflect actual certainty. If a field is ambiguous, mark it as "uncertain" with lower confidence. Return ONLY JSON."""

    try:
        import anthropic
        from app.core.config import settings

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                response_text += block.text

        response_text = response_text.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]

        ai_result = json.loads(response_text)

    except Exception as e:
        logger.error(f"Structured extraction failed: {e}")
        ai_result = {
            "fields": [{"field_name": f["name"], "value": None, "confidence": 0, "source_text": "", "reasoning": "AI extraction unavailable", "data_type": f["type"], "is_required": f.get("required", False), "status": "not_found"} for f in fields],
            "tables_found": [],
            "extraction_quality": {"completeness": 0, "required_fields_found": 0, "required_fields_total": len([f for f in fields if f.get("required")]), "overall_confidence": 0},
        }

    processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

    return {
        "extraction_id": extraction_id,
        "timestamp": datetime.utcnow().isoformat(),
        "processing_time_ms": round(processing_time, 1),
        "template": template,
        "template_label": template_label,
        **ai_result,
        "governance": {
            "correlation_id": "DA-" + uuid.uuid4().hex[:4].upper() + "-EXT",
            "hash": hashlib.sha256(f"{extraction_id}{template}".encode()).hexdigest()[:16],
            "ai_disclosure": "Fields extracted by AI. Human verification required for critical data.",
            "hitl_required": True,
            "model_used": "claude-sonnet-4-20250514",
        },
    }


# ═══════════════════════════════════════════════════════
# CROSS-DOCUMENT MEMORY
# ═══════════════════════════════════════════════════════

# In-memory pattern store (Phase 1 — move to PostgreSQL)
_document_memory = {
    "patterns": [],       # Recurring patterns across documents
    "entities": {},       # Known entities and their attributes
    "relationships": [],  # Document-to-document relationships
}


def record_document_pattern(
    document_id: str,
    document_name: str,
    extracted_fields: List[Dict],
    user_id: str,
) -> dict:
    """
    Record patterns from a processed document for cross-document learning.
    Over time, the system learns what's normal vs anomalous.
    """
    pattern_id = "PAT-" + uuid.uuid4().hex[:8].upper()

    # Extract key entities
    entities_found = []
    for field in extracted_fields:
        if field.get("value") and field.get("confidence", 0) > 0.7:
            entity_key = f"{field['field_name']}:{field['value']}"
            entities_found.append({
                "field": field["field_name"],
                "value": field["value"],
                "confidence": field["confidence"],
            })

            # Track entity across documents
            if entity_key not in _document_memory["entities"]:
                _document_memory["entities"][entity_key] = {
                    "first_seen": datetime.utcnow().isoformat(),
                    "documents": [],
                    "field": field["field_name"],
                    "value": field["value"],
                }
            _document_memory["entities"][entity_key]["documents"].append({
                "document_id": document_id,
                "document_name": document_name,
                "seen_at": datetime.utcnow().isoformat(),
            })

    pattern = {
        "pattern_id": pattern_id,
        "document_id": document_id,
        "document_name": document_name,
        "user_id": user_id,
        "entities_found": entities_found,
        "recorded_at": datetime.utcnow().isoformat(),
    }

    _document_memory["patterns"].append(pattern)

    return {
        "pattern_id": pattern_id,
        "entities_recorded": len(entities_found),
        "total_patterns": len(_document_memory["patterns"]),
        "total_known_entities": len(_document_memory["entities"]),
    }


def detect_anomalies(extracted_fields: List[Dict]) -> List[Dict]:
    """
    Check extracted fields against historical patterns.
    Flag values that deviate from what the system has seen before.
    """
    anomalies = []

    for field in extracted_fields:
        if not field.get("value"):
            continue

        entity_key = f"{field['field_name']}:{field['value']}"
        history = _document_memory["entities"].get(entity_key)

        if history and len(history["documents"]) > 3:
            # This exact value has been seen many times — likely normal
            continue

        # Check if this field name has been seen with DIFFERENT values
        field_entries = {k: v for k, v in _document_memory["entities"].items()
                       if v["field"] == field["field_name"]}

        if field_entries and entity_key not in field_entries:
            known_values = list(set(v["value"] for v in field_entries.values()))[:5]
            anomalies.append({
                "field": field["field_name"],
                "current_value": field["value"],
                "known_values": known_values,
                "severity": "medium",
                "message": f"Value '{field['value']}' for '{field['field_name']}' has not been seen before. Known values: {', '.join(str(v) for v in known_values[:3])}",
            })

    return anomalies
