"""Capability boundary for future TEFCA agents.

NO AGENTS ARE DEPLOYED. Nothing calls this module in the current pipeline.

It exists so that the permission model is settled and reviewed now, while the
answer is easy and nothing depends on it. The alternative — designing the
boundary at the moment someone wants to ship an agent — designs it under
delivery pressure, against a concrete feature that the boundary is inconvenient
for. Adding an agent later should be a question of "does this capability appear
on the allowlist?", not a re-litigation of the architecture with ONC.

The split mirrors the policy file exactly: agents may READ approved sources,
COMPARE structured records, and RECOMMEND. They may not WRITE, DECIDE, or
change the rules that constrain them.
"""
from __future__ import annotations

import logging
from typing import FrozenSet

logger = logging.getLogger("docuaction.tefca.ai.agent_boundary")


class AgentBoundaryViolation(Exception):
    """A prohibited capability was attempted.

    Raises rather than returning False. A prohibited action is not a routing
    decision to be handled quietly — it is either a bug or an attempt to
    escalate, and both need a stack trace and a log line rather than a
    `False` a caller might ignore.
    """


class TEFCAAgentBoundary:
    """What a TEFCA agent may and may not do."""

    # Read, compare, prepare, recommend. Every verb is non-mutating.
    ALLOWED_CAPABILITIES: FrozenSet[str] = frozenset({
        "read_nppes",
        "read_oig_leie",
        "read_approved_knowledge",
        "compare_structured_records",
        "prepare_evidence_package",
        "recommend_next_action",
    })

    # Named explicitly rather than left to the allowlist's default-deny, for the
    # same reason as the policy file: a denial that cites a prohibition is
    # stronger evidence than one citing absence from a list.
    PROHIBITED_CAPABILITIES: FrozenSet[str] = frozenset({
        "update_evidence",
        "change_classification",
        "approve_entity",
        "reject_entity",
        "delete_records",
        "change_policies",
        "change_prompts",
        "bypass_human_review",
        "modify_workflow_state",
        "publish_deliverables",
    })

    def check_capability(self, action: str) -> bool:
        """True if permitted; raise if prohibited; False if simply unknown.

        Prohibited is checked first so an action mistakenly placed on both lists
        is still refused loudly. An unknown action returns False rather than
        raising — it is not an escalation attempt, just an action nobody has
        approved yet, and default-deny already covers it.
        """
        if action in self.PROHIBITED_CAPABILITIES:
            logger.error("TEFCA agent boundary violation: %s", action)
            raise AgentBoundaryViolation(
                f"Agent attempted prohibited action: {action}")
        allowed = action in self.ALLOWED_CAPABILITIES
        if not allowed:
            logger.warning("TEFCA agent boundary: %r is not an approved capability", action)
        return allowed
