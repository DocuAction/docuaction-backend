"""
DocuAction Zero-Trust AI Governance Engine
- Policy-driven gatekeeper pipeline
- Composite accuracy scoring (Semantic Match + Source Density + Model Certainty)
- Conflict detection across documents
- Source traceability with page/timestamp mapping
- Hallucination prevention (strict mode)
- Version control (golden record)
- Governance certificate export
"""
import json
import uuid
import logging
import hashlib
from datetime import datetime
from typing import Optional, List, Dict
from difflib import SequenceMatcher

logger = logging.getLogger("docuaction.governance")


# ═══════════════════════════════════════════════════════
# GOVERNANCE POLICY
# ═══════════════════════════════════════════════════════

DEFAULT_POLICY = {
    "policy_name": "Enterprise Standard",
    "strict_mode": True,
    "thresholds": {
        "min_accuracy": 75,
        "min_confidence": 0.80,
        "min_source_density": 1,
    },
    "behavior_profile": "Conservative",
    "allowed_models": ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"],
    "enforce_source_traceability": True,
    "require_conflict_resolution": True,
    "block_unsupported_claims": True,
    "require_hitl_for_low_reliability": True,
}

STRICT_POLICIES = {
    "enterprise": {
        "policy_name": "Enterprise Standard",
        "strict_mode": True,
        "thresholds": {"min_accuracy": 75, "min_confidence": 0.80, "min_source_density": 1},
        "behavior_profile": "Conservative",
    },
    "government": {
        "policy_name": "Government Strict",
        "strict_mode": True,
        "thresholds": {"min_accuracy": 85, "min_confidence": 0.85, "min_source_density": 2},
        "behavior_profile": "Ultra-Conservative",
        "require_conflict_resolution": True,
        "require_hitl_for_low_reliability": True,
    },
    "healthcare": {
        "policy_name": "Healthcare HIPAA",
        "strict_mode": True,
        "thresholds": {"min_accuracy": 85, "min_confidence": 0.85, "min_source_density": 1},
        "behavior_profile": "Ultra-Conservative",
        "enforce_phi_masking": True,
        "require_hitl_for_low_reliability": True,
    },
    "legal": {
        "policy_name": "Legal Privileged",
        "strict_mode": True,
        "thresholds": {"min_accuracy": 80, "min_confidence": 0.82, "min_source_density": 1},
        "behavior_profile": "Conservative",
        "enforce_privilege_detection": True,
    },
    "financial": {
        "policy_name": "Financial Audit",
        "strict_mode": True,
        "thresholds": {"min_accuracy": 90, "min_confidence": 0.90, "min_source_density": 2},
        "behavior_profile": "Ultra-Conservative",
        "require_conflict_resolution": True,
    },
}


def get_policy(domain: str = "enterprise", org_config: dict = None) -> dict:
    """Get governance policy for domain. Org config overrides defaults."""
    policy = {**DEFAULT_POLICY, **STRICT_POLICIES.get(domain, STRICT_POLICIES["enterprise"])}
    if org_config:
        policy.update(org_config)
    return policy


# ═══════════════════════════════════════════════════════
# ACCURACY ENGINE (COMPOSITE SCORING)
# ═══════════════════════════════════════════════════════

def compute_semantic_match(output_text: str, source_texts: List[str]) -> float:
    """
    S_m: Semantic match between output claims and source documents.
    Uses sequence matching as a proxy for semantic similarity.
    Range: 0.0 - 1.0
    """
    if not source_texts or not output_text:
        return 0.0

    output_lower = output_text.lower()
    best_scores = []

    # Split output into sentences/claims
    sentences = [s.strip() for s in output_text.replace('\n', '. ').split('.') if len(s.strip()) > 15]

    for sentence in sentences[:20]:  # Check up to 20 claims
        sentence_lower = sentence.lower()
        best_match = 0.0

        for source in source_texts:
            source_lower = source.lower()

            # Check for exact substring match
            if sentence_lower[:50] in source_lower:
                best_match = max(best_match, 0.95)
                continue

            # Check key phrases (3+ word sequences)
            words = sentence_lower.split()
            for i in range(len(words) - 2):
                phrase = ' '.join(words[i:i+3])
                if phrase in source_lower:
                    best_match = max(best_match, 0.85)
                    break

            # Sequence matcher for overall similarity
            ratio = SequenceMatcher(None, sentence_lower[:200], source_lower[:2000]).ratio()
            best_match = max(best_match, ratio)

        best_scores.append(best_match)

    if not best_scores:
        return 0.5  # Neutral if no claims to check

    return round(sum(best_scores) / len(best_scores), 4)


def compute_source_density(source_count: int) -> float:
    """
    D_s: Source density — how many independent sources support the output.
    Range: 0.0 - 1.0
    """
    if source_count == 0:
        return 0.0
    elif source_count == 1:
        return 0.6
    elif source_count == 2:
        return 0.8
    elif source_count >= 3:
        return 1.0
    return 0.5


def compute_model_certainty(confidence: float, model_used: str = "") -> float:
    """
    C_log: Model certainty based on reported confidence and model tier.
    Range: 0.0 - 1.0
    """
    model_bonus = 0.0
    if "sonnet" in model_used.lower():
        model_bonus = 0.05
    elif "opus" in model_used.lower():
        model_bonus = 0.08

    return min(1.0, round(confidence + model_bonus, 4))


def compute_accuracy_score(
    output_text: str,
    source_texts: List[str],
    confidence: float,
    model_used: str = "",
    weights: dict = None,
) -> dict:
    """
    Compute composite accuracy score.
    Accuracy = w1*S_m + w2*D_s + w3*C_log (weighted)

    Returns: {score, reliability, components}
    """
    w = weights or {"semantic": 0.45, "density": 0.25, "certainty": 0.30}

    s_m = compute_semantic_match(output_text, source_texts)
    d_s = compute_source_density(len(source_texts))
    c_log = compute_model_certainty(confidence, model_used)

    raw_score = (w["semantic"] * s_m + w["density"] * d_s + w["certainty"] * c_log)
    accuracy = round(raw_score * 100, 1)

    # Determine reliability level
    if s_m > 0.90 and len(source_texts) >= 2:
        reliability = "HIGH"
    elif s_m > 0.80 or len(source_texts) >= 1:
        reliability = "MEDIUM"
    else:
        reliability = "LOW"

    # Override to LOW if any component is very weak
    if s_m < 0.4 or confidence < 0.5:
        reliability = "LOW"

    return {
        "accuracy_score": accuracy,
        "reliability": reliability,
        "components": {
            "semantic_match": round(s_m, 4),
            "source_density": round(d_s, 4),
            "model_certainty": round(c_log, 4),
        },
        "source_count": len(source_texts),
        "weights_applied": w,
    }


# ═══════════════════════════════════════════════════════
# CONFLICT DETECTION ENGINE
# ═══════════════════════════════════════════════════════

def detect_conflicts(outputs_by_document: Dict[str, dict]) -> List[dict]:
    """
    Detect contradictions across multiple document outputs.
    Compares key values: numbers, dates, decisions, statuses.
    """
    conflicts = []

    # Extract all claims with values
    claims = []
    for doc_name, output in outputs_by_document.items():
        content = output if isinstance(output, dict) else {}

        # Check decisions
        for dec in content.get("decisions", content.get("decision_ledger", [])):
            if isinstance(dec, dict):
                claims.append({
                    "type": "decision",
                    "text": dec.get("decision", ""),
                    "source": doc_name,
                    "timestamp": dec.get("timestamp", ""),
                })

        # Check metrics/KPIs
        for kpi in content.get("key_metrics", content.get("kpi_signals", [])):
            if isinstance(kpi, dict):
                claims.append({
                    "type": "metric",
                    "metric": kpi.get("metric", ""),
                    "value": str(kpi.get("value", "")),
                    "source": doc_name,
                })

        # Check summary for numbers
        summary = content.get("summary", "")
        if summary:
            import re
            numbers = re.findall(r'\$[\d,]+\.?\d*[MBK]?|\d+(?:,\d{3})*(?:\.\d+)?%?', summary)
            for num in numbers[:10]:
                claims.append({
                    "type": "figure",
                    "value": num,
                    "source": doc_name,
                    "context": summary[:100],
                })

    # Compare claims across documents
    metrics_by_name = {}
    for c in claims:
        if c["type"] == "metric" and c.get("metric"):
            key = c["metric"].lower().strip()
            if key not in metrics_by_name:
                metrics_by_name[key] = []
            metrics_by_name[key].append(c)

    # Check for conflicting values
    for metric_name, entries in metrics_by_name.items():
        if len(entries) < 2:
            continue

        values = set(str(e.get("value", "")).strip() for e in entries)
        if len(values) > 1:
            conflicts.append({
                "conflict_id": "CNF-" + uuid.uuid4().hex[:6].upper(),
                "type": "value_conflict",
                "metric": entries[0].get("metric", metric_name),
                "severity": "high",
                "conflicting_values": [
                    {
                        "value": e.get("value", ""),
                        "source": e.get("source", ""),
                        "timestamp": e.get("timestamp", ""),
                    }
                    for e in entries
                ],
                "resolution_required": True,
                "recommendation": f"Human review required: '{metric_name}' has conflicting values across documents.",
            })

    # Check for contradicting decisions
    decision_texts = [c for c in claims if c["type"] == "decision"]
    for i, dec_a in enumerate(decision_texts):
        for dec_b in decision_texts[i+1:]:
            if dec_a["source"] == dec_b["source"]:
                continue

            text_a = dec_a.get("text", "").lower()
            text_b = dec_b.get("text", "").lower()

            # Check for contradiction signals
            contradiction_pairs = [
                ("approved", "rejected"), ("approved", "denied"),
                ("increase", "decrease"), ("expand", "reduce"),
                ("proceed", "cancel"), ("accept", "decline"),
                ("hire", "terminate"), ("extend", "end"),
            ]

            for word_a, word_b in contradiction_pairs:
                if (word_a in text_a and word_b in text_b) or (word_b in text_a and word_a in text_b):
                    # Check if about same topic (word overlap)
                    words_a = set(text_a.split())
                    words_b = set(text_b.split())
                    overlap = words_a & words_b - {"the", "a", "an", "is", "was", "to", "and", "or", "of", "in", "for"}
                    if len(overlap) >= 2:
                        conflicts.append({
                            "conflict_id": "CNF-" + uuid.uuid4().hex[:6].upper(),
                            "type": "decision_conflict",
                            "severity": "critical",
                            "claim_a": {"text": dec_a.get("text", ""), "source": dec_a.get("source", ""), "timestamp": dec_a.get("timestamp", "")},
                            "claim_b": {"text": dec_b.get("text", ""), "source": dec_b.get("source", ""), "timestamp": dec_b.get("timestamp", "")},
                            "overlapping_terms": list(overlap)[:5],
                            "resolution_required": True,
                            "recommendation": "Contradicting decisions detected. Escalate for human resolution.",
                        })
                        break

    return conflicts


# ═══════════════════════════════════════════════════════
# SOURCE TRACEABILITY
# ═══════════════════════════════════════════════════════

def extract_source_map(output_content: dict, source_texts: List[dict]) -> List[dict]:
    """
    Map output claims to specific source locations.
    source_texts: [{name, text, pages}]
    Returns: [{claim, source_name, page, snippet, match_strength}]
    """
    mappings = []

    # Collect all claims from output
    claims = []
    for key in ["summary", "situation"]:
        text = output_content.get(key, "")
        if text:
            sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 15]
            for s in sentences[:10]:
                claims.append({"field": key, "text": s})

    for finding in output_content.get("key_findings", []):
        if isinstance(finding, dict):
            claims.append({"field": "finding", "text": finding.get("finding", "")})

    for task in output_content.get("tasks", []):
        if isinstance(task, dict):
            claims.append({"field": "action", "text": task.get("task", "")})

    for insight in output_content.get("insights", []):
        if isinstance(insight, dict):
            claims.append({"field": "insight", "text": insight.get("finding", "")})

    # Match claims to sources
    for claim in claims:
        claim_lower = claim["text"].lower()
        best_match = None
        best_score = 0

        for source in source_texts:
            source_text = source.get("text", "").lower()
            source_name = source.get("name", "Unknown")

            # Find best matching section
            for page_idx, page_text in enumerate(source_text.split('\n\n')):
                if len(page_text) < 10:
                    continue

                score = SequenceMatcher(None, claim_lower[:150], page_text[:500]).ratio()

                # Boost if key phrases match
                claim_words = set(claim_lower.split())
                page_words = set(page_text.split())
                word_overlap = len(claim_words & page_words) / max(len(claim_words), 1)
                score = max(score, word_overlap)

                if score > best_score:
                    best_score = score
                    # Extract snippet around match
                    snippet_start = max(0, page_text.find(claim_lower[:20]))
                    if snippet_start == -1:
                        snippet_start = 0
                    snippet = page_text[snippet_start:snippet_start + 200]

                    best_match = {
                        "claim": claim["text"][:150],
                        "field": claim["field"],
                        "source_name": source_name,
                        "page": page_idx + 1,
                        "snippet": snippet[:200],
                        "match_strength": round(score, 3),
                        "verified": score > 0.6,
                    }

        if best_match:
            mappings.append(best_match)

    return mappings


# ═══════════════════════════════════════════════════════
# GOVERNANCE GATEKEEPER PIPELINE
# ═══════════════════════════════════════════════════════

def governance_gate(
    output_content: dict,
    source_texts: List[str],
    confidence: float,
    model_used: str,
    domain: str = "enterprise",
    org_config: dict = None,
) -> dict:
    """
    Main governance gatekeeper. Evaluates output against policy thresholds.

    Returns:
    {
        "approved": bool,
        "accuracy": {...},
        "policy": {...},
        "gate_result": "approved|review_required|blocked",
        "violations": [...],
        "source_map": [...],
        "audit_metadata": {...},
        "governance_certificate": {...}
    }
    """
    policy = get_policy(domain, org_config)
    violations = []
    gate_result = "approved"

    # ─── Compute Accuracy ───
    output_text = json.dumps(output_content) if isinstance(output_content, dict) else str(output_content)
    accuracy = compute_accuracy_score(output_text, source_texts, confidence, model_used)

    # ─── Policy Checks ───
    thresholds = policy.get("thresholds", {})

    # Check minimum accuracy
    if accuracy["accuracy_score"] < thresholds.get("min_accuracy", 75):
        violations.append({
            "rule": "min_accuracy",
            "expected": thresholds.get("min_accuracy"),
            "actual": accuracy["accuracy_score"],
            "severity": "high",
            "action": "Route to validation queue",
        })
        gate_result = "review_required"

    # Check minimum confidence
    if confidence < thresholds.get("min_confidence", 0.80):
        violations.append({
            "rule": "min_confidence",
            "expected": thresholds.get("min_confidence"),
            "actual": confidence,
            "severity": "medium",
            "action": "Flag for review",
        })
        if gate_result == "approved":
            gate_result = "review_required"

    # Check source density
    if len(source_texts) < thresholds.get("min_source_density", 1):
        violations.append({
            "rule": "min_source_density",
            "expected": thresholds.get("min_source_density"),
            "actual": len(source_texts),
            "severity": "medium",
            "action": "Insufficient source evidence",
        })
        if gate_result == "approved":
            gate_result = "review_required"

    # Check reliability level
    if accuracy["reliability"] == "LOW":
        violations.append({
            "rule": "low_reliability",
            "expected": "MEDIUM or HIGH",
            "actual": accuracy["reliability"],
            "severity": "high",
            "action": "Block or require human validation",
        })
        if policy.get("require_hitl_for_low_reliability"):
            gate_result = "blocked"

    # Check model is allowed
    if policy.get("allowed_models") and model_used not in policy["allowed_models"]:
        violations.append({
            "rule": "model_not_allowed",
            "expected": policy["allowed_models"],
            "actual": model_used,
            "severity": "critical",
            "action": "Blocked — unauthorized model",
        })
        gate_result = "blocked"

    # ─── Build Audit Metadata ───
    audit_id = "DA-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper()
    timestamp = datetime.utcnow().isoformat()

    audit_metadata = {
        "audit_id": audit_id,
        "timestamp": timestamp,
        "model_used": model_used,
        "policy_applied": policy["policy_name"],
        "domain": domain,
        "accuracy_score": accuracy["accuracy_score"],
        "reliability": accuracy["reliability"],
        "gate_result": gate_result,
        "violations_count": len(violations),
        "source_count": len(source_texts),
        "strict_mode": policy.get("strict_mode", False),
    }

    # ─── Governance Certificate ───
    certificate = {
        "certificate_id": "GC-" + uuid.uuid4().hex[:8].upper(),
        "audit_id": audit_id,
        "timestamp": timestamp,
        "accuracy_score": accuracy["accuracy_score"],
        "reliability": accuracy["reliability"],
        "gate_result": gate_result,
        "policy_applied": policy["policy_name"],
        "model_used": model_used,
        "source_count": len(source_texts),
        "violations": violations,
        "semantic_match": accuracy["components"]["semantic_match"],
        "source_density": accuracy["components"]["source_density"],
        "model_certainty": accuracy["components"]["model_certainty"],
        "hash": hashlib.sha256(
            f"{audit_id}{timestamp}{accuracy['accuracy_score']}{model_used}".encode()
        ).hexdigest()[:16],
        "exportable": True,
    }

    return {
        "approved": gate_result == "approved",
        "gate_result": gate_result,
        "accuracy": accuracy,
        "policy": {
            "name": policy["policy_name"],
            "domain": domain,
            "strict_mode": policy.get("strict_mode", False),
            "thresholds": thresholds,
        },
        "violations": violations,
        "audit_metadata": audit_metadata,
        "governance_certificate": certificate,
    }


# ═══════════════════════════════════════════════════════
# STRICT MODE: HALLUCINATION FILTER
# ═══════════════════════════════════════════════════════

def filter_unsupported_claims(output_content: dict, source_texts: List[str], threshold: float = 0.5) -> dict:
    """
    In strict mode, filter out or flag claims not supported by source text.
    Returns modified output with unsupported claims marked.
    """
    if not source_texts:
        return output_content

    filtered = dict(output_content)
    source_combined = " ".join(source_texts).lower()

    # Check summary sentences
    summary = filtered.get("summary", "")
    if summary:
        sentences = summary.split('.')
        verified = []
        for s in sentences:
            s = s.strip()
            if not s or len(s) < 10:
                verified.append(s)
                continue

            # Check if sentence has support
            words = set(s.lower().split())
            source_words = set(source_combined.split())
            overlap = len(words & source_words) / max(len(words), 1)

            if overlap > threshold:
                verified.append(s)
            else:
                verified.append(f"[UNVERIFIED] {s}")

        filtered["summary"] = '. '.join(verified)

    # Check insights
    for i, insight in enumerate(filtered.get("insights", [])):
        if isinstance(insight, dict):
            finding = insight.get("finding", "").lower()
            words = set(finding.split())
            source_words = set(source_combined.split())
            overlap = len(words & source_words) / max(len(words), 1)
            if overlap < threshold:
                filtered["insights"][i]["_unverified"] = True
                filtered["insights"][i]["_verification_score"] = round(overlap, 3)

    return filtered


# ═══════════════════════════════════════════════════════
# FULL GOVERNANCE PIPELINE
# ═══════════════════════════════════════════════════════

async def run_governance_pipeline(
    output_content: dict,
    source_texts: List[str],
    source_documents: List[dict],
    confidence: float,
    model_used: str,
    domain: str = "enterprise",
    org_config: dict = None,
    all_outputs: Dict[str, dict] = None,
) -> dict:
    """
    Complete governance pipeline:
    1. Policy load
    2. Accuracy computation
    3. Conflict detection
    4. Source traceability
    5. Hallucination filter
    6. Gate decision
    7. Audit metadata
    """
    # 1. Gate check
    gate = governance_gate(output_content, source_texts, confidence, model_used, domain, org_config)

    # 2. Source traceability
    source_map = extract_source_map(
        output_content,
        [{"name": d.get("name", ""), "text": d.get("text", "")} for d in (source_documents or [])]
    )

    # 3. Conflict detection (if multiple documents)
    conflicts = []
    if all_outputs and len(all_outputs) > 1:
        conflicts = detect_conflicts(all_outputs)

    # 4. Strict mode: filter unsupported claims
    policy = get_policy(domain, org_config)
    filtered_output = output_content
    if policy.get("block_unsupported_claims") and policy.get("strict_mode"):
        filtered_output = filter_unsupported_claims(output_content, source_texts)

    # 5. Override gate if conflicts found
    if conflicts and policy.get("require_conflict_resolution"):
        gate["gate_result"] = "conflict_detected"
        gate["approved"] = False

    return {
        "output": filtered_output,
        "governance": gate,
        "source_traceability": source_map,
        "conflicts": conflicts,
        "has_conflicts": len(conflicts) > 0,
        "verified_claims": len([s for s in source_map if s.get("verified")]),
        "total_claims": len(source_map),
        "governance_certificate": gate["governance_certificate"],
    }
