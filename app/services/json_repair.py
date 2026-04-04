"""
JSON Safety & Repair Layer
Extracts and repairs malformed JSON from AI model responses.
If regex repair fails, sends to Claude Haiku for cleanup pass.
"""

import re
import json
import logging
from typing import Optional

logger = logging.getLogger("docuaction.json_repair")


def extract_and_repair_json(raw: str) -> Optional[dict]:
    """
    Multi-stage JSON extraction and repair.
    
    Stage 1: Try direct parse
    Stage 2: Extract JSON from markdown code blocks
    Stage 3: Find JSON object boundaries with regex
    Stage 4: Repair common malformations
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip()

    # ─── Stage 1: Direct parse ───
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # ─── Stage 2: Extract from code blocks ───
    code_block = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1).strip())
        except json.JSONDecodeError:
            text = code_block.group(1).strip()

    # ─── Stage 3: Find JSON boundaries ───
    # Find first { and last }
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace >= 0 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            text = candidate

    # ─── Stage 4: Repair common issues ───
    repaired = text

    # Fix trailing commas before } or ]
    repaired = re.sub(r',\s*([}\]])', r'\1', repaired)

    # Fix single quotes to double quotes (careful with apostrophes)
    repaired = re.sub(r"(?<=[{,:\[]\s*)'([^']*)'(?=\s*[},:\]])", r'"\1"', repaired)

    # Fix unquoted keys
    repaired = re.sub(r'(?<=[{,])\s*(\w+)\s*:', r' "\1":', repaired)

    # Fix missing commas between key-value pairs
    repaired = re.sub(r'"\s*\n\s*"', '",\n"', repaired)

    # Remove control characters
    repaired = re.sub(r'[\x00-\x1f]', '', repaired)

    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # ─── Stage 5: Try to build minimal valid JSON ───
    # Extract key-value pairs with regex
    pairs = re.findall(r'"(\w+)"\s*:\s*("(?:[^"\\]|\\.)*"|\d+(?:\.\d+)?|true|false|null|\[.*?\]|\{.*?\})', repaired, re.DOTALL)
    if pairs:
        obj = {}
        for key, value in pairs:
            try:
                obj[key] = json.loads(value)
            except json.JSONDecodeError:
                obj[key] = value.strip('"')
        if obj:
            return obj

    logger.warning("All JSON repair stages failed")
    return None


async def haiku_json_cleanup(raw_response: str, action_type: str) -> Optional[dict]:
    """
    Send malformed response to Claude Haiku for JSON cleanup.
    This is the last resort before returning an error.
    """
    try:
        from app.core.config import settings
        import httpx

        cleanup_prompt = f"""The following text was supposed to be valid JSON for a "{action_type}" action, but it's malformed.
Extract the data and return it as clean, valid JSON. Return ONLY the JSON object, nothing else.

Malformed text:
{raw_response[:8000]}"""

        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        body = {
            "model": getattr(settings, "ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            "max_tokens": 2048,
            "temperature": 0,
            "messages": [{"role": "user", "content": cleanup_prompt}],
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
            )

        if response.status_code != 200:
            logger.error(f"Haiku cleanup failed: {response.status_code}")
            return None

        data = response.json()
        content = data.get("content", [])
        if content and content[0].get("type") == "text":
            cleaned = content[0]["text"]
            result = extract_and_repair_json(cleaned)
            if result:
                logger.info("Haiku JSON cleanup succeeded")
                return result

    except Exception as e:
        logger.error(f"Haiku cleanup error: {e}")

    return None


def validate_output_schema(parsed: dict, action_type: str) -> dict:
    """
    Ensure output has required fields for the action type.
    Adds missing fields with defaults.
    """
    schemas = {
        "summary": {"summary": "", "key_metrics": [], "recommendations": [], "confidence": 0},
        "actions": {"tasks": [], "decisions": [], "follow_ups": [], "total_items": 0, "confidence": 0},
        "insights": {"insights": [], "risk_factors": [], "opportunities": [], "confidence": 0},
        "email": {"subject": "", "to": "", "body": "", "urgency": "medium", "confidence": 0},
        "brief": {"title": "", "situation": "", "key_findings": [], "risk_assessment": [], "decision_required": "", "confidence": 0},
    }

    schema = schemas.get(action_type, schemas["summary"])
    for key, default in schema.items():
        if key not in parsed:
            parsed[key] = default

    return parsed
