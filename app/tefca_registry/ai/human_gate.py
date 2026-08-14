"""The human decision gate for TEFCA entity resolution.

A TEFCA determination ALWAYS requires human review. This is not a threshold, a
default, or a configuration value — it is the architecture. There is no
argument, score, or agreement that routes around it.

Note what this class does NOT have: no threshold constant, no settings lookup,
no `if` on the AI result. `evaluate()` takes its arguments and ignores them. An
unused parameter normally reads as a smell; here it is the design. A gate whose
decision cannot depend on its inputs cannot be talked into a different answer by
a better-looking input, and there is no configuration surface for a future
change to loosen — flipping this off means editing this file and defending the
diff in review.

Even if:
    both models agree
    the evidence quality score is 1.0
    validation passed perfectly
    the NPIs match exactly
→ a human reviewer still makes the determination.

The AI is advisory. The reviewer is the decision of record.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("docuaction.tefca.ai.human_gate")

ACTION_QUEUE_FOR_REVIEW = "queue_for_human_review"
REASON_ALWAYS = "tefca_always_requires_human_review"


@dataclass(frozen=True)
class GateDecision:
    """Frozen so a decision cannot be edited after the fact by a later caller."""
    proceed: bool
    reason: str
    action: str

    @property
    def human_review_required(self) -> bool:
        return not self.proceed


class TEFCAHumanGate:
    """Always routes to human review. Cannot be bypassed."""

    async def evaluate(self, ai_result: Any = None, validation: Any = None,
                       policy: Any = None) -> GateDecision:
        """Return the same decision every time.

        The arguments are accepted so the gate sits in the pipeline where a
        conditional gate would sit, and so the call site reads as a real
        evaluation step. They are deliberately not consulted.
        """
        logger.debug("TEFCA human gate: review required (invariant)")
        return GateDecision(
            proceed=False,
            reason=REASON_ALWAYS,
            action=ACTION_QUEUE_FOR_REVIEW,
        )
