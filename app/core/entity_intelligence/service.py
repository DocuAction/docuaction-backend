"""The gated entry point. Everything the capability does starts here, and it
does nothing while ENTITY_INTELLIGENCE_ENABLED is False.

The service is pure: it takes observations in and returns comparisons, deltas
and an assessment. Persistence (models.py) and acquisition (adapters) are
separate so the reasoning can be tested without a database or a network.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import flags
from .assessment import AssessmentResult, assess
from .comparison import ComparisonResult, compare_all
from .delta import HistoricalDelta, compute_deltas, explain_deltas
from .observations import EvidenceObservation

SERVICE_VERSION = "1.0"


RUN_COMPLETED = "COMPLETED"
RUN_COMPLETED_WITH_UNAVAILABLE_SOURCES = "COMPLETED_WITH_UNAVAILABLE_SOURCES"


@dataclass(frozen=True)
class EntityIntelligenceRun:
    run_id: str
    canonical_entity_id: Optional[str]
    source_ids: List[str]
    comparisons: List[ComparisonResult]
    deltas: List[HistoricalDelta]
    assessment: AssessmentResult
    #: A reference to a prior human determination / QA result supplied by the
    #: PROGRAM (id, date, outcome label as recorded). Echoed for the analyst;
    #: never read by the rules. PRIOR_DECISION_EXISTS is answered from it.
    prior_review_reference: Optional[Dict[str, Any]] = None
    status: str = RUN_COMPLETED
    service_version: str = SERVICE_VERSION
    note: str = field(default=("System evidence assessment only. The analyst determines; independent QA "
                               "checks; the contractual workflow remains authoritative."))

    def to_dict(self) -> Dict[str, Any]:
        return {"run_id": self.run_id, "canonical_entity_id": self.canonical_entity_id,
                "source_ids": list(self.source_ids),
                "comparisons": [c.to_dict() for c in self.comparisons],
                "deltas": [d.to_dict() for d in self.deltas],
                "assessment": self.assessment.to_dict(),
                "prior_review_reference": dict(self.prior_review_reference) if self.prior_review_reference else None,
                "prior_decision_exists": bool(self.prior_review_reference),
                "status": self.status,
                "service_version": self.service_version, "note": self.note}


class EntityIntelligenceService:
    """Compose comparison → delta → assessment for one entity.

    `evaluate` is the ONLY public entry point and it is gated. A caller that
    reaches it with the master flag off gets FeatureDisabled, not a result.
    """

    def evaluate(self, *, canonical_entity_id: Optional[str],
                 current: List[EvidenceObservation],
                 prior: Optional[List[EvidenceObservation]] = None,
                 source_ids: List[str],
                 identifier_system: str = "NPI",
                 relationship_kinds: Optional[List[str]] = None,
                 prior_review_reference: Optional[Dict[str, Any]] = None) -> EntityIntelligenceRun:
        flags.require_enabled(boundary="EntityIntelligenceService.evaluate")
        comparisons: List[ComparisonResult] = []
        for source_id in source_ids:
            comparisons.extend(compare_all(current, source_id=source_id,
                                           identifier_system=identifier_system,
                                           relationship_kinds=relationship_kinds))
        deltas = explain_deltas(compute_deltas(prior or [], current), comparisons) if prior is not None else []
        result = assess(comparisons, deltas)
        unavailable = any(o.observed_value.get("unavailable") for o in current)
        return EntityIntelligenceRun(run_id=str(uuid.uuid4()), canonical_entity_id=canonical_entity_id,
                                     source_ids=list(source_ids), comparisons=comparisons,
                                     deltas=deltas, assessment=result,
                                     prior_review_reference=prior_review_reference,
                                     status=RUN_COMPLETED_WITH_UNAVAILABLE_SOURCES if unavailable else RUN_COMPLETED)
