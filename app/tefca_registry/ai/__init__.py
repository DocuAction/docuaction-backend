"""TEFCA AI Control Plane.

All AI calls for TEFCA entity resolution MUST go through the
TEFCAAIOrchestrator. No direct LLM calls are permitted in TEFCA code outside
gateway.py — enforced by
tests/test_tefca_ai_control_plane.py::test_no_direct_llm_calls_in_tefca.

AI is advisory only. The human reviewer makes all final determinations.
AI is disabled by default (AI_ENTITY_RESOLUTION defaults to "disabled").

Scope is TEFCA entity resolution. The bulletin module is not governed by this
package and is unchanged.

Submodules are imported lazily via __getattr__ so that importing the package
(or the registry above it) does not pull in yaml, httpx, or the orchestrator's
dependency graph until something actually calls AI.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "TEFCAAIOrchestrator",
    "OrchestratorResult",
    "TEFCAPolicyEngine",
    "TEFCAAIGateway",
    "TEFCAValidationEngine",
    "TEFCAEvidenceQualityEngine",
    "TEFCAHumanGate",
    "TEFCAAIAuditLogger",
    "TEFCAPromptRegistry",
    "TEFCAAgentBoundary",
    "AgentBoundaryViolation",
]

_EXPORTS = {
    "TEFCAAIOrchestrator": ("orchestrator", "TEFCAAIOrchestrator"),
    "OrchestratorResult": ("orchestrator", "OrchestratorResult"),
    "TEFCAPolicyEngine": ("policy_engine", "TEFCAPolicyEngine"),
    "TEFCAAIGateway": ("gateway", "TEFCAAIGateway"),
    "TEFCAValidationEngine": ("validation", "TEFCAValidationEngine"),
    "TEFCAEvidenceQualityEngine": ("validation", "TEFCAEvidenceQualityEngine"),
    "TEFCAHumanGate": ("human_gate", "TEFCAHumanGate"),
    "TEFCAAIAuditLogger": ("audit_logger", "TEFCAAIAuditLogger"),
    "TEFCAPromptRegistry": ("prompt_registry", "TEFCAPromptRegistry"),
    "TEFCAAgentBoundary": ("agent_boundary", "TEFCAAgentBoundary"),
    "AgentBoundaryViolation": ("agent_boundary", "AgentBoundaryViolation"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    from importlib import import_module
    return getattr(import_module(f"{__name__}.{module_name}"), attr)


def __dir__():
    return sorted(__all__)
