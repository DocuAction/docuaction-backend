"""The single entry point for every TEFCA AI call.

THERE IS NO OTHER PATH. No TEFCA code outside this package calls a provider,
and inside the package only gateway.py may. A CI test enforces that boundary
across app/tefca_registry/ and app/Tefca/, so a future contributor cannot
reintroduce a direct call without failing the build.

THE PIPELINE, in order, with the invariant each step holds:

    1. Policy check ......... an unapproved task never reaches a provider
    2. Egress filter ........ only allowlisted public fields leave the system
    3. Versioned prompt ..... the exact question asked is reconstructable
    4. Gateway .............. limits, retry, circuit breaker, temperature 0.0
    5. Validation ........... malformed or out-of-role output is discarded
    6. Evidence score ....... computed from objective signals, not the model
    7. Human gate ........... always requires review, unconditionally
    8. Audit ................ every step above is recorded, including denials
    9. Return ............... status is never "approved"

IF ANY STEP FAILS, NO AI RUNS AND DETERMINISTIC VERIFICATION CONTINUES.
Losing AI is a degraded capability. Losing governance would be a compliance
failure. Those are not the same severity, so they do not get the same handling:
this module treats every uncertainty as a reason to fall back to the
deterministic pipeline, which is fully functional on its own.

The orchestrator returns a recommendation. It never returns a decision.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.tefca_registry.ai.audit_logger import TEFCAAIAuditLogger, hash_input
from app.tefca_registry.ai.gateway import (
    DualResponse, GatewayError, GatewayResponse, TEFCAAIGateway,
)
from app.tefca_registry.ai.human_gate import TEFCAHumanGate
from app.tefca_registry.ai.policy_engine import TEFCAPolicyEngine
from app.tefca_registry.ai.prompt_registry import TEFCAPromptRegistry
from app.tefca_registry.ai.validation import (
    TEFCAEvidenceQualityEngine, TEFCAValidationEngine,
)

logger = logging.getLogger("docuaction.tefca.ai.orchestrator")

# The task this orchestrator performs, checked against the policy allowlist.
TASK_COMPARE_ENTITY_NAMES = "compare_entity_names"

STATUS_NEEDS_REVIEW = "needs_review"
STATUS_DENIED = "denied"
STATUS_AI_UNAVAILABLE = "ai_unavailable"

# The fields the orchestrator is willing to consider sending. Intersected with
# the policy allowlist, never used in its place — this tuple bounds what the
# code can offer, the policy bounds what is approved, and a field must clear
# both. Tightening either one tightens the result.
_CANDIDATE_FIELDS = ("name", "address", "npi", "entity_type")


@dataclass
class OrchestratorResult:
    """What the pipeline hands back. Advisory, always.

    There is no `approved` status and no `is_match` field. The result carries a
    recommendation and the evidence behind it; the determination is the
    reviewer's and lives on the review record, not here.
    """
    status: str
    text: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    evidence_quality_score: float = 0.0
    evidence_signals: Dict[str, Any] = field(default_factory=dict)
    validation_passed: bool = False
    validation_errors: List[str] = field(default_factory=list)
    human_review_required: bool = True
    prompt_version: Optional[str] = None
    ai_raw_confidence: Optional[float] = None
    models_agree: Optional[bool] = None
    reason: str = ""
    fallback: Optional[str] = None
    audit_records: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ai_consulted(self) -> bool:
        return self.status == STATUS_NEEDS_REVIEW


class TEFCAAIOrchestrator:
    """Policy -> filter -> gateway -> validation -> score -> gate -> audit."""

    def __init__(self, *, session: Any = None, entity_id: Any = None,
                 actor_id: Any = None, actor_email: Optional[str] = None,
                 policy: Optional[TEFCAPolicyEngine] = None,
                 gateway: Optional[TEFCAAIGateway] = None,
                 validator: Optional[TEFCAValidationEngine] = None,
                 evidence: Optional[TEFCAEvidenceQualityEngine] = None,
                 human_gate: Optional[TEFCAHumanGate] = None,
                 audit: Optional[TEFCAAIAuditLogger] = None,
                 prompts: Optional[TEFCAPromptRegistry] = None):
        # One policy instance shared by every component that consults it, so a
        # single YAML read backs the whole pipeline. Two engines reading the
        # file at different moments could disagree mid-request if the file were
        # replaced during a deploy.
        self.policy = policy or TEFCAPolicyEngine()
        self.gateway = gateway or TEFCAAIGateway()
        self.validator = validator or TEFCAValidationEngine(policy=self.policy)
        self.evidence = evidence or TEFCAEvidenceQualityEngine(policy=self.policy)
        self.human_gate = human_gate or TEFCAHumanGate()
        self.audit = audit or TEFCAAIAuditLogger(
            session=session, entity_id=entity_id,
            actor_id=actor_id, actor_email=actor_email)
        self.prompts = prompts or TEFCAPromptRegistry()

    async def resolve_entity(
        self,
        *,
        entity_name: Optional[str] = None,
        entity_address: Optional[str] = None,
        entity_npi: Optional[str] = None,
        entity_type: Optional[str] = None,
        registry_name: Optional[str] = None,
        registry_address: Optional[str] = None,
        registry_npi: Optional[str] = None,
        registry_type: Optional[str] = None,
        evidence_signals: Optional[Dict[str, Any]] = None,
    ) -> OrchestratorResult:
        """THE ONLY WAY to call AI for TEFCA entity resolution."""
        signals: Dict[str, Any] = dict(evidence_signals or {})

        # ── 1. Policy ────────────────────────────────────────────────────
        decision = self.policy.check_permission(TASK_COMPARE_ENTITY_NAMES)
        if not decision.allowed:
            await self.audit.log(policy_decision=STATUS_DENIED, reason=decision.reason,
                                 human_review_required=True, evidence_signals=signals)
            logger.info("TEFCA AI denied by policy: %s", decision.reason)
            return OrchestratorResult(
                status=STATUS_DENIED, reason=decision.reason,
                # A denial still scores the deterministic evidence. The signals
                # were computed without AI, so they remain valid and useful to
                # the reviewer who now has no recommendation to read.
                evidence_quality_score=self.evidence.calculate_score(signals),
                evidence_signals=signals, fallback="deterministic",
                audit_records=list(self.audit.records))

        # ── 2. Egress filter ─────────────────────────────────────────────
        public_fields = set(self.policy.get_public_fields())
        context = self._build_context(
            public_fields,
            submitted={"name": entity_name, "address": entity_address,
                       "npi": entity_npi, "entity_type": entity_type},
            registry={"name": registry_name, "address": registry_address,
                      "npi": registry_npi, "entity_type": registry_type},
        )

        if not context["fields_sent"]:
            # Nothing survived the allowlist, so there is no question to ask.
            reason = "no approved fields available to send"
            await self.audit.log(policy_decision=STATUS_AI_UNAVAILABLE, reason=reason,
                                 human_review_required=True, evidence_signals=signals)
            return OrchestratorResult(
                status=STATUS_AI_UNAVAILABLE, reason=reason,
                evidence_quality_score=self.evidence.calculate_score(signals),
                evidence_signals=signals, fallback="deterministic",
                audit_records=list(self.audit.records))

        # ── 3. Versioned prompt ──────────────────────────────────────────
        prompt = self.prompts.get("entity_match")
        rendered = prompt.render(context)
        input_hash = hash_input(context)

        # ── 4. Gateway ───────────────────────────────────────────────────
        try:
            ai_result, models_agree = await self._dispatch(rendered, context)
        except GatewayError as exc:
            # A caller-side violation (bad tier, non-zero temperature). Treated
            # as unavailable rather than raised: a control-plane bug must
            # degrade to the deterministic path, not break verification.
            reason = f"gateway rejected the request: {exc}"
            logger.error("TEFCA AI %s", reason)
            await self.audit.log(policy_decision=STATUS_AI_UNAVAILABLE, reason=reason,
                                 prompt_version=prompt.version, input_hash=input_hash,
                                 human_review_required=True, evidence_signals=signals)
            return OrchestratorResult(
                status=STATUS_AI_UNAVAILABLE, reason=reason,
                prompt_version=prompt.version,
                evidence_quality_score=self.evidence.calculate_score(signals),
                evidence_signals=signals, fallback="deterministic",
                audit_records=list(self.audit.records))

        if ai_result is None:
            reason = "no AI provider available"
            await self.audit.log(policy_decision=STATUS_AI_UNAVAILABLE, reason=reason,
                                 prompt_version=prompt.version, input_hash=input_hash,
                                 human_review_required=True, evidence_signals=signals)
            logger.info("TEFCA AI unavailable — deterministic resolution only")
            return OrchestratorResult(
                status=STATUS_AI_UNAVAILABLE, reason=reason,
                prompt_version=prompt.version,
                evidence_quality_score=self.evidence.calculate_score(signals),
                evidence_signals=signals, fallback="deterministic",
                audit_records=list(self.audit.records))

        # ── 5. Validation ────────────────────────────────────────────────
        validation = self.validator.validate_entity_match(ai_result.text, context)

        # ── 6. Evidence quality ──────────────────────────────────────────
        # Agreement is folded in as one weighted signal alongside the
        # deterministic ones — it does not get its own privileged path, and at
        # 0.05 it is the smallest weight in the table.
        if models_agree is not None:
            signals["models_agree"] = bool(models_agree)
        evidence_score = self.evidence.calculate_score(signals)

        # ── 7. Human gate ────────────────────────────────────────────────
        gate = await self.human_gate.evaluate(ai_result, validation, decision)

        # ── 8. Audit ─────────────────────────────────────────────────────
        await self.audit.log(
            provider=ai_result.provider,
            model=ai_result.model,
            prompt_version=prompt.version,
            input_hash=input_hash,
            output_text=ai_result.text,
            ai_raw_confidence=validation.ai_raw_confidence,
            evidence_quality_score=evidence_score,
            evidence_signals=signals,
            validation_passed=validation.passed,
            validation_errors=validation.errors,
            human_review_required=gate.human_review_required,
            policy_decision=STATUS_NEEDS_REVIEW,
            latency_ms=ai_result.latency_ms,
            reason=gate.reason,
            evidence_signals_fired=self.evidence.signals_fired(signals),
            evidence_scoring_calibrated=self.evidence.is_calibrated,
            models_agree=models_agree,
            gateway_attempts=ai_result.attempts,
        )

        if not validation.passed:
            logger.warning("TEFCA AI output failed validation (%s) — recommendation "
                           "withheld from the reviewer", "; ".join(validation.errors))

        # ── 9. Return — always needs review ──────────────────────────────
        return OrchestratorResult(
            status=STATUS_NEEDS_REVIEW,
            # Withheld on failure. A reviewer must never be shown output that
            # did not pass validation: unvalidated text sitting in a review
            # queue reads as evidence regardless of any flag beside it.
            text=ai_result.text if validation.passed else None,
            provider=ai_result.provider,
            model=ai_result.model,
            evidence_quality_score=evidence_score,
            evidence_signals=signals,
            validation_passed=validation.passed,
            validation_errors=validation.errors,
            human_review_required=gate.human_review_required,
            prompt_version=prompt.version,
            ai_raw_confidence=validation.ai_raw_confidence,
            models_agree=models_agree,
            reason=gate.reason,
            audit_records=list(self.audit.records),
        )

    # ── Helpers ──────────────────────────────────────────────────────────
    def _build_context(self, public_fields: set, submitted: Dict[str, Any],
                       registry: Dict[str, Any]) -> Dict[str, Any]:
        """Filter both records to the policy allowlist.

        `fields_sent` is derived from what actually survived filtering, not
        copied from the policy list. Reporting the policy's allowlist here
        would make the validator's egress check tautological — it would be
        re-checking the allowlist against itself instead of against the payload.
        """
        def keep(record: Dict[str, Any]) -> Dict[str, Any]:
            return {k: v for k, v in record.items()
                    if k in public_fields and k in _CANDIDATE_FIELDS
                    and v not in (None, "")}

        kept_submitted, kept_registry = keep(submitted), keep(registry)
        return {
            "submitted": kept_submitted,
            "registry": kept_registry,
            "fields_sent": sorted(set(kept_submitted) | set(kept_registry)),
        }

    async def _dispatch(self, rendered: str, context: Dict[str, Any]):
        """(response, models_agree). Dual when policy requires it and both
        providers are usable; single otherwise.

        Policy asks for dual verification, but OPENAI_API_KEY is unset until the
        BAA is signed. Falling back to a single provider is correct rather than
        a failure: dual agreement was only ever a signal, and review is required
        with or without it, so the absence of a second opinion costs 0.05 of
        evidence score and nothing else.
        """
        if self.policy.dual_verify_required() and self.gateway.dual_available:
            dual: DualResponse = await self.gateway.call_dual(rendered, context)
            return dual.best, dual.agree
        single: Optional[GatewayResponse] = await self.gateway.call(rendered, context)
        return single, None
