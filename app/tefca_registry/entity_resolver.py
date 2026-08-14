"""Entity resolution — deterministic first, AI only as a last resort.

RESOLUTION ORDER (cheapest and most defensible first):
    1. Exact identifier match (NPI / TEFCAID)      — decisive, free
    2. USPS address normalization                  — free, deterministic
    3. Jaro-Winkler name similarity                — free, deterministic
    4. AI adjudication                             — only if 1-3 are inconclusive
                                                     AND AI_ENTITY_RESOLUTION != disabled

Steps 1-3 settle the overwhelming majority of cases. Step 4 exists for the
residue where two records plausibly describe one organization and no
deterministic signal decides it.

NO SDK DEPENDENCY. This module never imports `anthropic`. It calls an injected
client object satisfying a two-method protocol (see AIClient). That keeps the
package list unchanged — which matters here, because DEPLOYMENT_GUIDE.md
documents an incident where installing one package moved 11 pinned dependencies
including fastapi, silently flipping auth failures from 401 to 403 and
invalidating a full test run. When an SDK is approved, it is wired in behind
this same interface with no change to the pipeline.

GOVERNANCE (docs/AI_GOVERNANCE.md):
    * The default is "disabled". The system is fully functional without AI.
    * AI never decides. It produces a recommendation a human reviewer accepts
      or rejects; the reviewer is always the decision of record.
    * Only public data is ever sent: organization name, business address, NPI,
      entity type. Never PHI, patient data, or SSNs.
    * Every call is audit-logged with model, prompt version, input, output,
      confidence, the threshold applied, latency, and software version.

CONTROL PLANE (app/tefca_registry/ai/):
    The production AI path is resolve_with_orchestrator() below, which runs the
    deterministic steps 1-3 first and only then hands an inconclusive pair to
    TEFCAAIOrchestrator — policy, egress filter, versioned prompt, gateway,
    validation, evidence scoring, human gate, audit. Nothing in this module
    imports a provider SDK, and the orchestrator is the only route to one.

    The injected-AIClient path (resolve() with an ai_client) predates the
    control plane and is retained for the tests that exercise threshold
    behaviour with a double. Production never supplies a client:
    build_ai_client() returns None unconditionally, so the only way to reach a
    provider is through the orchestrator.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger("docuaction.tefca.entity_resolver")

# ── Configuration ────────────────────────────────────────────────────────────
MODE_DISABLED = "disabled"
MODE_ADVISORY = "advisory"
MODE_PRODUCTION = "production"
VALID_MODES = (MODE_DISABLED, MODE_ADVISORY, MODE_PRODUCTION)


def resolution_mode() -> str:
    """Current mode. Defaults to disabled; an unrecognized value also disables.

    Fail-closed on purpose: a typo in an env var must not silently switch AI on
    in a pipeline that produces compliance evidence.
    """
    mode = (os.getenv("AI_ENTITY_RESOLUTION", MODE_DISABLED) or "").strip().lower()
    if mode not in VALID_MODES:
        if mode:
            logger.warning("AI_ENTITY_RESOLUTION=%r is not one of %s — treating as "
                           "disabled", mode, VALID_MODES)
        return MODE_DISABLED
    return mode


# Model is configurable; the default names a current Claude model but nothing
# here imports an SDK — the injected client owns transport entirely.
AI_MODEL_ID = os.getenv("AI_ENTITY_RESOLUTION_MODEL", "claude-sonnet-5")
PROMPT_VERSION = "entity-resolution/v1"

# ── Confidence thresholds ────────────────────────────────────────────────────
# >= 0.95      recommendation is surfaced to the reviewer
# 0.70 - 0.94  mandatory manual review; recommendation shown as context only
# < 0.70       recommendation is discarded entirely
THRESHOLD_SHOW = 0.95
THRESHOLD_MANUAL = 0.70

# Fields that may leave the system. Anything not on this list is dropped before
# the payload is built — an allowlist, so a new PHI-bearing column added to the
# entity model later cannot leak by default.
PUBLIC_FIELDS = ("name", "address", "npi", "entity_type", "state", "tefcaid")


@dataclass
class ResolutionResult:
    is_match: Optional[bool]        # None = undetermined
    confidence: float
    method: str                     # identifier | address | name | ai | none
    reasoning: str = ""
    requires_manual_review: bool = True
    ai_consulted: bool = False
    threshold_applied: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


class AIClient(Protocol):
    """Minimal contract an AI backend must satisfy.

    Deliberately tiny so an Anthropic SDK adapter, an internal gateway, or a test
    double are all equally easy to supply.
    """

    def complete(self, *, model: str, system: str, prompt: str) -> str:
        """Return the model's raw text response."""
        ...


# ── Jaro-Winkler (local implementation — no `jellyfish` dependency) ──────────

def _jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    window = max(max(len1, len2) // 2 - 1, 0)
    m1 = [False] * len1
    m2 = [False] * len2
    matches = 0
    for i in range(len1):
        for j in range(max(0, i - window), min(i + window + 1, len2)):
            if m2[j] or s1[i] != s2[j]:
                continue
            m1[i] = m2[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i in range(len1):
        if not m1[i]:
            continue
        while not m2[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    transpositions //= 2
    return (matches / len1 + matches / len2
            + (matches - transpositions) / matches) / 3.0


def jaro_winkler(s1: str, s2: str, prefix_weight: float = 0.1) -> float:
    """Jaro-Winkler similarity in [0, 1]."""
    j = _jaro(s1, s2)
    if j <= 0.7:
        return j
    prefix = 0
    for a, b in zip(s1[:4], s2[:4]):
        if a != b:
            break
        prefix += 1
    return j + prefix * prefix_weight * (1 - j)


# Legal-form suffixes carry no distinguishing information: "Mercy Health LLC" and
# "Mercy Health Inc" are the same organization far more often than not.
_ORG_NOISE = re.compile(
    r"\b(inc|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|pllc|pc|pa|"
    r"lp|llp|group|holdings|the|of|and)\b", re.I)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def normalize_org_name(name: Optional[str]) -> str:
    if not name:
        return ""
    text = _NON_ALNUM.sub(" ", str(name).lower())
    text = _ORG_NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def compare_names(a: Optional[str], b: Optional[str]) -> float:
    na, nb = normalize_org_name(a), normalize_org_name(b)
    if not na or not nb:
        return 0.0
    return jaro_winkler(na, nb)


# ── Audit logging ────────────────────────────────────────────────────────────

def _software_version() -> str:
    return os.getenv("APP_VERSION", "6.0.0")


def build_audit_record(*, model_id: str, prompt_version: str, payload: Dict[str, Any],
                       output: str, confidence: float, threshold_applied: str,
                       latency_ms: int, error: str = "") -> Dict[str, Any]:
    """The full record required by docs/AI_GOVERNANCE.md §3."""
    return {
        "event": "ai_entity_resolution",
        "model_id": model_id,
        "prompt_version": prompt_version,
        "input": payload,
        "output": output[:4000],
        "confidence": confidence,
        "threshold_applied": threshold_applied,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "latency_ms": latency_ms,
        "software_version": _software_version(),
        "error": error,
    }


def _public_payload(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Allowlist filter. Only PUBLIC_FIELDS ever leave the system."""
    return {k: entity.get(k) for k in PUBLIC_FIELDS if entity.get(k) not in (None, "")}


SYSTEM_PROMPT = (
    "You determine whether two healthcare organization records refer to the same "
    "real-world entity. You are given only public directory data. Respond with a "
    "single JSON object and nothing else: "
    '{"is_match": true|false, "confidence": <0.0-1.0>, "reasoning": "<one sentence>"}. '
    "Be conservative: when the evidence is genuinely ambiguous, return a low "
    "confidence rather than guessing. Your output is a recommendation for a human "
    "reviewer, not a decision."
)


class EntityResolver:
    """Deterministic-first entity resolution with optional AI adjudication."""

    def __init__(self, ai_client: Optional[AIClient] = None,
                 normalizer=None, mode: Optional[str] = None):
        self.ai_client = ai_client
        self._mode_override = mode
        if normalizer is None:
            from app.tefca_registry.address_normalizer import USPSNormalizer
            normalizer = USPSNormalizer()
        self.normalizer = normalizer
        self.audit_records: List[Dict[str, Any]] = []

    @property
    def mode(self) -> str:
        return self._mode_override or resolution_mode()

    # ── Deterministic steps ─────────────────────────────────────────────────
    def _by_identifier(self, a: Dict[str, Any], b: Dict[str, Any]) -> Optional[ResolutionResult]:
        for field_name in ("npi", "tefcaid"):
            va, vb = (a.get(field_name) or "").strip(), (b.get(field_name) or "").strip()
            if va and vb:
                if va == vb:
                    return ResolutionResult(
                        True, 1.0, "identifier",
                        f"{field_name.upper()} matches exactly ({va})",
                        requires_manual_review=False)
                # A shared identifier space with different values is decisive the
                # other way: two distinct NPIs are two distinct entities.
                return ResolutionResult(
                    False, 1.0, "identifier",
                    f"{field_name.upper()} differs ({va} vs {vb})",
                    requires_manual_review=False)
        return None

    def resolve(self, a: Dict[str, Any], b: Dict[str, Any]) -> ResolutionResult:
        """Resolve whether two entity records describe the same organization."""
        by_id = self._by_identifier(a, b)
        if by_id is not None:
            return by_id

        addr = self.normalizer.compare(a.get("address"), b.get("address"))
        name_score = compare_names(a.get("name"), b.get("name"))

        # Both deterministic signals strong → match, no AI needed.
        if addr.is_match and name_score >= 0.90:
            return ResolutionResult(
                True, round(min(1.0, (addr.confidence + name_score) / 2), 4),
                "address+name",
                f"address matches after USPS normalization and names agree "
                f"(Jaro-Winkler {name_score:.2f})",
                requires_manual_review=False,
                details={"address": asdict(addr), "name_score": round(name_score, 4)})

        # Both weak → not a match, no AI needed.
        if not addr.is_match and name_score < 0.70:
            return ResolutionResult(
                False, round(1.0 - name_score, 4), "address+name",
                f"names differ (Jaro-Winkler {name_score:.2f}) and addresses do not "
                f"normalize to the same value",
                requires_manual_review=False,
                details={"address": asdict(addr), "name_score": round(name_score, 4)})

        # Inconclusive — this is the only path that may consult AI.
        base = ResolutionResult(
            None, round(name_score, 4), "inconclusive",
            f"deterministic signals disagree (name {name_score:.2f}, address "
            f"match={addr.is_match}) — manual review required",
            requires_manual_review=True,
            details={"address": asdict(addr), "name_score": round(name_score, 4)})

        if self.mode == MODE_DISABLED or self.ai_client is None:
            return base
        return self._resolve_with_ai(a, b, base)

    # ── AI step ─────────────────────────────────────────────────────────────
    async def resolve_with_orchestrator(
        self, a: Dict[str, Any], b: Dict[str, Any], *,
        orchestrator: Any = None,
        evidence_signals: Optional[Dict[str, Any]] = None,
    ) -> ResolutionResult:
        """Deterministic first; the orchestrator only for the residue.

        Same resolution order as resolve() — identifiers, address, name — and
        the same short-circuits. Only a genuinely inconclusive pair reaches AI,
        and only through the control plane.

        Never raises. Every failure path returns the deterministic result, so a
        broken control plane degrades the recommendation and not the
        verification.
        """
        base = self.resolve(a, b)

        # Steps 1-3 settled it, or AI is off. Either way there is nothing to ask.
        if base.method != "inconclusive" or self.mode == MODE_DISABLED:
            return base

        try:
            if orchestrator is None:
                from app.tefca_registry.ai.orchestrator import TEFCAAIOrchestrator
                orchestrator = TEFCAAIOrchestrator()

            signals = self._evidence_signals(a, b, base, evidence_signals)
            result = await orchestrator.resolve_entity(
                entity_name=a.get("name"), entity_address=a.get("address"),
                entity_npi=a.get("npi"), entity_type=a.get("entity_type"),
                registry_name=b.get("name"), registry_address=b.get("address"),
                registry_npi=b.get("npi"), registry_type=b.get("entity_type"),
                evidence_signals=signals,
            )
        except Exception as exc:  # noqa: BLE001 — AI must never break the pipeline
            logger.warning("TEFCA AI orchestrator failed (%s: %s) — falling back to "
                           "deterministic result", type(exc).__name__, exc)
            base.threshold_applied = "orchestrator_error"
            return base

        # The orchestrator's own audit rows join the resolver's list so the
        # caller's existing "write every audit record" loop persists them in the
        # same transaction. One write path, not two.
        self.audit_records.extend(result.audit_records)

        if result.status != "needs_review":
            # denied or ai_unavailable — the deterministic result stands.
            base.threshold_applied = result.status
            base.details["evidence_quality_score"] = result.evidence_quality_score
            return base

        return ResolutionResult(
            # None, always. The orchestrator returns a recommendation; setting
            # is_match here would convert advice into a determination, which is
            # precisely what the reviewer exists to do instead.
            is_match=None,
            confidence=base.confidence,
            method="ai_advisory",
            reasoning=(result.text or "")[:500],
            requires_manual_review=True,
            ai_consulted=True,
            threshold_applied="human_review_required",
            details={
                **base.details,
                "evidence_quality_score": result.evidence_quality_score,
                "evidence_signals": result.evidence_signals,
                "validation_passed": result.validation_passed,
                "validation_errors": result.validation_errors,
                "prompt_version": result.prompt_version,
                "provider": result.provider,
                "model": result.model,
                # Recorded for the audit trail; nothing reads it to decide.
                "ai_raw_confidence": result.ai_raw_confidence,
                "models_agree": result.models_agree,
            },
        )

    def _evidence_signals(self, a: Dict[str, Any], b: Dict[str, Any],
                          base: ResolutionResult,
                          supplied: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Objective signals for the evidence score.

        Derived from the deterministic comparisons already performed, so the
        score reflects measurements the pipeline made rather than anything the
        model reported about itself. A caller may supply extras (usps_zip4_match,
        source_agreement) that only the review context knows; those take
        precedence, since the caller has strictly more information here.
        """
        address = base.details.get("address") or {}
        name_score = base.details.get("name_score", 0.0)
        npi_a = (a.get("npi") or "").strip()
        npi_b = (b.get("npi") or "").strip()
        type_a = (a.get("entity_type") or "").strip().lower()
        type_b = (b.get("entity_type") or "").strip().lower()

        no_identifier_conflict = not (npi_a and npi_b and npi_a != npi_b)

        signals: Dict[str, Any] = {
            "npi_exact_match": bool(npi_a and npi_b and npi_a == npi_b),
            "address_normalized_match": bool(address.get("is_match")),
            "name_similarity": name_score,
            "entity_type_match": bool(type_a and type_b and type_a == type_b),
        }
        if supplied:
            signals.update(supplied)

        # A conjunction, not an override. The resolver knows about conflicting
        # identifiers; the caller knows whether the upstream sources contradict
        # each other. Either one being true means fields conflict, so letting
        # the caller's value replace the resolver's would silently discard a
        # detected NPI conflict and inflate the score for exactly the pairs
        # that most need a reviewer's attention.
        signals["no_conflicting_fields"] = bool(
            no_identifier_conflict
            and signals.get("no_conflicting_fields", True)
            and signals.get("source_agreement", True)
        )
        return signals

    # ── Legacy injected-client AI step ──────────────────────────────────
    # Predates the control plane. Reached only when a caller supplies an
    # ai_client; production supplies None (build_ai_client returns None
    # unconditionally), so the only live route to a provider is the
    # orchestrator. Retained because the threshold behaviour it implements is
    # covered by tests that inject a double.
    def _resolve_with_ai(self, a: Dict[str, Any], b: Dict[str, Any],
                         base: ResolutionResult) -> ResolutionResult:
        payload = {"record_a": _public_payload(a), "record_b": _public_payload(b)}
        prompt = ("Do these two records refer to the same organization?\n\n"
                  + json.dumps(payload, indent=2, sort_keys=True))

        started = time.time()
        raw, error = "", ""
        try:
            raw = self.ai_client.complete(
                model=AI_MODEL_ID, system=SYSTEM_PROMPT, prompt=prompt)
        except Exception as exc:  # noqa: BLE001 — AI must never break the pipeline
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("AI entity resolution failed (%s) — falling back to "
                           "deterministic result", error)
        latency_ms = int((time.time() - started) * 1000)

        parsed = self._parse(raw) if raw else None
        confidence = float(parsed.get("confidence", 0.0)) if parsed else 0.0
        confidence = max(0.0, min(1.0, confidence))

        if confidence >= THRESHOLD_SHOW:
            threshold = "show_recommendation"
        elif confidence >= THRESHOLD_MANUAL:
            threshold = "mandatory_manual_review"
        else:
            threshold = "ignored_below_threshold"

        self.audit_records.append(build_audit_record(
            model_id=AI_MODEL_ID, prompt_version=PROMPT_VERSION, payload=payload,
            output=raw, confidence=confidence, threshold_applied=threshold,
            latency_ms=latency_ms, error=error))

        if parsed is None or threshold == "ignored_below_threshold":
            # Below threshold the recommendation is discarded, not downgraded —
            # a low-confidence guess must not reach a reviewer as evidence.
            base.ai_consulted = True
            base.threshold_applied = threshold
            return base

        # Even at the highest confidence the reviewer decides. In advisory mode
        # the recommendation is context only and never sets is_match.
        result = ResolutionResult(
            is_match=parsed.get("is_match") if self.mode == MODE_PRODUCTION else None,
            confidence=confidence,
            method="ai",
            reasoning=str(parsed.get("reasoning", ""))[:500],
            requires_manual_review=True,
            ai_consulted=True,
            threshold_applied=threshold,
            details=base.details,
        )
        return result

    @staticmethod
    def _parse(raw: str) -> Optional[Dict[str, Any]]:
        """Extract the JSON object from a model response, tolerating prose around it."""
        text = (raw or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            pass
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            logger.warning("AI entity resolution returned unparseable output")
            return None
