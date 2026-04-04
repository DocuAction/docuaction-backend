"""
DocuAction AI — Intelligence Engine v3
Production-grade AI pipeline with model routing, JSON safety, chunking, and fallback.

Architecture:
  Request → PII Masking → Complexity Router → Model (Sonnet/Haiku) → JSON Repair → Audit Log → Response
"""

import json
import time
import logging
import asyncio
from typing import Optional
from datetime import datetime

from app.services.json_repair import extract_and_repair_json, haiku_json_cleanup
from app.services.pii_masking import mask_pii
from app.services.text_chunker import chunk_and_summarize
from app.services.model_router import classify_complexity
from app.services.audit_logger import log_ai_request
from app.core.config import settings

logger = logging.getLogger("docuaction.ai_engine")

# ═══════════════════════════════════════════════════════
# PROMPT TEMPLATES
# ═══════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are DocuAction AI, an enterprise document intelligence system.
You analyze documents and transcripts to produce structured outputs for executive decision-making.

CRITICAL RULES:
1. Respond ONLY with valid JSON — no text before or after
2. Use internal reasoning but NEVER expose your thought process
3. Every claim must be supported by the source text
4. If information is not in the source, say "Not found in source"
5. Be specific — use exact numbers, names, dates from the document
6. Assign confidence scores based on source evidence strength"""

ACTION_PROMPTS = {
    "summary": """Analyze this document and produce a structured executive summary.
Return ONLY valid JSON:
{
  "summary": "A prose narrative (3-5 paragraphs) with specific metrics and findings from the document. Cite page numbers where possible.",
  "key_metrics": [{"metric": "name", "value": "number", "context": "explanation"}],
  "recommendations": ["specific recommendation 1", "specific recommendation 2"],
  "confidence": 0.0,
  "sources_cited": 0
}""",

    "actions": """Extract all action items, tasks, and follow-ups from this document.
Return ONLY valid JSON:
{
  "tasks": [
    {
      "task": "specific description of what needs to be done",
      "owner": "person name from document or TBD",
      "deadline": "date from document or TBD",
      "priority": "high|medium|low",
      "source_reference": "quote or page reference"
    }
  ],
  "decisions": [
    {
      "decision": "what was decided",
      "decided_by": "who made the decision",
      "context": "surrounding context",
      "impact": "business impact"
    }
  ],
  "follow_ups": [
    {
      "item": "follow-up description",
      "owner": "responsible person",
      "due": "timeframe"
    }
  ],
  "total_items": 0,
  "confidence": 0.0
}""",

    "insights": """Analyze this document and extract prioritized business insights.
Return ONLY valid JSON:
{
  "insights": [
    {
      "finding": "specific insight description",
      "impact": "high|medium|low",
      "evidence": "supporting quote or data from document",
      "recommendation": "what to do about it",
      "confidence": 0.0
    }
  ],
  "risk_factors": ["risk 1", "risk 2"],
  "opportunities": ["opportunity 1"],
  "confidence": 0.0
}""",

    "email": """Generate a professional email based on this document's content.
Return ONLY valid JSON:
{
  "subject": "clear subject line with specific topic",
  "to": "appropriate recipient based on content",
  "body": "professional email body with specific findings, numbers, and recommendations from the document. Include greeting and sign-off.",
  "attachments_suggested": ["list of documents to attach"],
  "urgency": "high|medium|low",
  "confidence": 0.0
}""",

    "brief": """Create a board-ready executive brief from this document.
Return ONLY valid JSON:
{
  "title": "brief title",
  "situation": "2-3 sentence situation analysis",
  "key_findings": [
    {"finding": "specific finding", "data": "supporting number or fact", "source": "page/section reference"}
  ],
  "metrics_table": [
    {"metric": "name", "current": "value", "target": "value", "status": "on_track|at_risk|behind"}
  ],
  "risk_assessment": [
    {"risk": "description", "severity": "high|medium|low", "mitigation": "recommended action"}
  ],
  "decision_required": "specific decision the reader needs to make",
  "recommendation": "what you recommend and why",
  "confidence": 0.0
}""",
}


# ═══════════════════════════════════════════════════════
# MAIN ENGINE
# ═══════════════════════════════════════════════════════

async def process_document(
    document_text: str,
    action_type: str = "summary",
    user_id: str = None,
    document_id: str = None,
    output_language: str = None,
) -> dict:
    """
    Main entry point for the AI engine.
    
    Pipeline:
    1. PII masking
    2. Context window management (chunking if needed)
    3. Complexity-based model routing
    4. AI generation with timeout + retry
    5. JSON extraction and repair
    6. Audit logging
    7. Return structured output
    """
    start_time = time.time()
    model_used = None
    fallback_used = False
    error_message = None

    try:
        # ─── Step 1: PII Masking ───
        masked_text, pii_count = mask_pii(document_text)
        logger.info(f"PII masking: {pii_count} items redacted")

        # ─── Step 2: Context Window Management ───
        word_count = len(masked_text.split())
        if word_count > 20000:
            logger.info(f"Long document ({word_count} words) — chunking and pre-summarizing")
            masked_text = await chunk_and_summarize(masked_text)
            logger.info(f"Chunked summary: {len(masked_text.split())} words")

        # ─── Step 3: Complexity-Based Model Routing ───
        complexity = classify_complexity(masked_text, action_type)
        
        if complexity == "simple":
            primary_model = "haiku"
            fallback_model = "haiku"
        else:
            primary_model = "sonnet"
            fallback_model = "haiku"

        logger.info(f"Routing: complexity={complexity}, model={primary_model}, action={action_type}")

        # ─── Step 4: Generate with Primary Model ───
        prompt = ACTION_PROMPTS.get(action_type, ACTION_PROMPTS["summary"])
        if output_language and output_language != "en":
            prompt += f"\n\nIMPORTANT: Generate all output text in {output_language} language."

        raw_response = await _call_model(
            model_type=primary_model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=f"{prompt}\n\n--- DOCUMENT START ---\n{masked_text[:40000]}\n--- DOCUMENT END ---",
        )
        model_used = _get_model_name(primary_model)

        # ─── Step 5: JSON Extraction & Repair ───
        parsed = extract_and_repair_json(raw_response)

        if parsed is None:
            logger.warning(f"JSON repair failed for {primary_model} output — sending to Haiku cleanup")
            parsed = await haiku_json_cleanup(raw_response, action_type)
            if parsed:
                fallback_used = True

        if parsed is None:
            raise ValueError("Failed to extract valid JSON from AI response")

        # ─── Add metadata ───
        processing_ms = int((time.time() - start_time) * 1000)
        parsed["_meta"] = {
            "model_used": model_used,
            "processing_time_ms": processing_ms,
            "pii_items_masked": pii_count,
            "word_count": word_count,
            "complexity": complexity,
            "fallback_used": fallback_used,
            "action_type": action_type,
        }

        # ─── Step 6: Audit Log ───
        await log_ai_request(
            user_id=user_id,
            document_id=document_id,
            action_type=action_type,
            model_used=model_used,
            processing_time_ms=processing_ms,
            status="success",
            confidence=parsed.get("confidence", 0),
            fallback_used=fallback_used,
        )

        logger.info(f"AI engine complete: {action_type} in {processing_ms}ms via {model_used}")
        return parsed

    except Exception as e:
        # ─── Fallback Strategy ───
        error_message = str(e)
        logger.error(f"Primary model failed: {error_message}")

        if not fallback_used:
            try:
                logger.info("Attempting fallback to Haiku...")
                raw_response = await _call_model(
                    model_type="haiku",
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=f"{ACTION_PROMPTS.get(action_type, ACTION_PROMPTS['summary'])}\n\n--- DOCUMENT ---\n{masked_text[:30000]}\n--- END ---",
                )
                parsed = extract_and_repair_json(raw_response)
                if parsed:
                    model_used = _get_model_name("haiku")
                    fallback_used = True
                    processing_ms = int((time.time() - start_time) * 1000)
                    parsed["_meta"] = {
                        "model_used": model_used,
                        "processing_time_ms": processing_ms,
                        "fallback_used": True,
                        "action_type": action_type,
                        "original_error": error_message,
                    }

                    await log_ai_request(
                        user_id=user_id, document_id=document_id,
                        action_type=action_type, model_used=model_used,
                        processing_time_ms=processing_ms, status="fallback",
                        confidence=parsed.get("confidence", 0), fallback_used=True,
                    )
                    return parsed
            except Exception as fallback_error:
                logger.error(f"Fallback also failed: {fallback_error}")

        # ─── Graceful Degradation ───
        processing_ms = int((time.time() - start_time) * 1000)
        await log_ai_request(
            user_id=user_id, document_id=document_id,
            action_type=action_type, model_used=model_used or "none",
            processing_time_ms=processing_ms, status="error",
            confidence=0, fallback_used=fallback_used,
            error=error_message,
        )

        return {
            "summary": f"AI processing encountered an error. Please try again or contact support.",
            "tasks": [],
            "decisions": [],
            "follow_ups": [],
            "confidence": 0,
            "_meta": {
                "model_used": "none",
                "processing_time_ms": processing_ms,
                "status": "error",
                "error": error_message,
            },
        }


# ═══════════════════════════════════════════════════════
# MODEL CALLERS
# ═══════════════════════════════════════════════════════

async def _call_model(model_type: str, system_prompt: str, user_prompt: str, timeout: int = 15) -> str:
    """Call Anthropic API with timeout and retry."""
    
    model_name = _get_model_name(model_type)
    max_tokens = 4096 if model_type == "sonnet" else 2048
    
    for attempt in range(2):  # 1 try + 1 retry
        try:
            response = await asyncio.wait_for(
                _anthropic_call(model_name, system_prompt, user_prompt, max_tokens),
                timeout=timeout,
            )
            return response
        except asyncio.TimeoutError:
            logger.warning(f"Timeout on attempt {attempt + 1} for {model_type}")
            if attempt == 0:
                continue
            raise
        except Exception as e:
            logger.error(f"API error on attempt {attempt + 1}: {e}")
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            raise


async def _anthropic_call(model: str, system: str, user: str, max_tokens: int) -> str:
    """Make the actual Anthropic API call."""
    import httpx

    headers = {
        "x-api-key": settings.ANTHROPIC_API_KEY,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "temperature": 0.1,  # Low temperature for structured output consistency
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
        )

    if response.status_code != 200:
        error_detail = response.text[:200]
        raise Exception(f"Anthropic API error {response.status_code}: {error_detail}")

    data = response.json()
    content = data.get("content", [])
    if content and content[0].get("type") == "text":
        return content[0]["text"]
    raise Exception("No text content in Anthropic response")


def _get_model_name(model_type: str) -> str:
    """Map model type to actual Anthropic model name."""
    if model_type == "sonnet":
        return getattr(settings, "ANTHROPIC_SONNET_MODEL", "claude-sonnet-4-20250514")
    else:
        return getattr(settings, "ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


# ═══════════════════════════════════════════════════════
# LEGACY COMPATIBILITY
# ═══════════════════════════════════════════════════════

async def generate_output(action_type: str, document_text: str, user_id: str = None, **kwargs) -> dict:
    """
    Legacy-compatible wrapper.
    Maps the old ai_service_v2.py interface to the new engine.
    """
    result = await process_document(
        document_text=document_text,
        action_type=action_type,
        user_id=user_id,
        output_language=kwargs.get("output_language"),
    )

    meta = result.pop("_meta", {})

    return {
        "content": json.dumps(result, indent=2, ensure_ascii=False),
        "model_used": meta.get("model_used", "unknown"),
        "confidence": result.get("confidence", 0.85),
        "processing_time_ms": meta.get("processing_time_ms", 0),
        "action_type": action_type,
    }
