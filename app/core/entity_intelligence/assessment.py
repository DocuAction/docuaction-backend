"""SYSTEM EVIDENCE ASSESSMENT — the last thing the engine says before a person.

    SOURCE DATA → INDEPENDENT EVIDENCE → SYSTEM EVIDENCE ASSESSMENT → ANALYST …

RULES (no arithmetic, no voting)
    1. If every consulted source was unavailable → SOURCE_UNAVAILABLE.
    2. Else if any dimension conflicts with an authoritative source → CONFLICTING_EVIDENCE
       (one authoritative conflict is enough; agreement elsewhere is not a counter-vote).
    3. Else if any dimension is explained only by a non-primary record (DBA,
       other name, additional location) or a delta is an explainable variation
       → EXPLAINABLE_VARIATION_IDENTIFIED.
    4. Else if every applicable dimension corroborates → EVIDENCE_CORROBORATES.
    5. Else if at least one corroborates and the rest are insufficient/ambiguous
       → EVIDENCE_PARTIALLY_CORROBORATES.
    6. Else → INSUFFICIENT_EVIDENCE.

The output vocabulary is closed and guarded: it can never be COMPLIANT,
NON_COMPLIANT, APPROVED, REJECTED, PASS, FAIL or VERDICT.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .comparison import (AMBIGUOUS_SIGNALS, CONFLICT_SIGNALS, CORROBORATING_SIGNALS,
                         EXPLAINING_LOCATION_SIGNALS, EXPLAINING_NAME_SIGNALS,
                         INSUFFICIENT_SIGNALS, UNAVAILABLE_SIGNALS, ComparisonResult)
from .delta import HistoricalDelta, VariationSignal

ASSESSMENT_RULES_VERSION = "1.0"

FORBIDDEN_TERMS = frozenset({"COMPLIANT", "NON_COMPLIANT", "NONCOMPLIANT", "APPROVED", "REJECTED",
                             "PASS", "FAIL", "VERDICT", "DETERMINATION"})


class SystemEvidenceAssessment(str, Enum):
    EVIDENCE_CORROBORATES = "EVIDENCE_CORROBORATES"
    EVIDENCE_PARTIALLY_CORROBORATES = "EVIDENCE_PARTIALLY_CORROBORATES"
    EXPLAINABLE_VARIATION_IDENTIFIED = "EXPLAINABLE_VARIATION_IDENTIFIED"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


for _member in SystemEvidenceAssessment:
    # An import-time guard, not an assert: it must hold under `python -O` too.
    if set(_member.value.split("_")) & FORBIDDEN_TERMS:
        raise RuntimeError(f"forbidden assessment vocabulary: {_member.value}")


@dataclass(frozen=True)
class AssessmentResult:
    assessment: SystemEvidenceAssessment
    basis: List[str]                       # the signals relied on, in rule order
    comparisons: List[ComparisonResult]
    deltas: List[HistoricalDelta]
    open_questions: List[str] = field(default_factory=list)   # what still needs a person
    requires_human_review: bool = True
    rules_version: str = ASSESSMENT_RULES_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {"assessment": self.assessment.value, "basis": list(self.basis),
                "comparisons": [c.to_dict() for c in self.comparisons],
                "deltas": [d.to_dict() for d in self.deltas],
                "open_questions": list(self.open_questions),
                "requires_human_review": True, "rules_version": self.rules_version,
                "note": ("System evidence assessment. Not a determination, not a contractual "
                         "category, not a verdict.")}


def assess(comparisons: List[ComparisonResult],
           deltas: Optional[List[HistoricalDelta]] = None) -> AssessmentResult:
    deltas = deltas or []
    signals = [c.signal for c in comparisons]
    basis = [s.value for s in signals]
    questions: List[str] = []

    if comparisons and all(s in UNAVAILABLE_SIGNALS for s in signals):
        return AssessmentResult(SystemEvidenceAssessment.SOURCE_UNAVAILABLE, basis, comparisons, deltas,
                                ["No independent source could be consulted."])

    if any(s in CONFLICT_SIGNALS for s in signals):
        for c in comparisons:
            if c.signal in CONFLICT_SIGNALS:
                questions.append(f"{c.dimension.value}: {c.explanation}")
        return AssessmentResult(SystemEvidenceAssessment.CONFLICTING_EVIDENCE, basis, comparisons, deltas, questions)

    explained = [c for c in comparisons if c.signal in EXPLAINING_NAME_SIGNALS | EXPLAINING_LOCATION_SIGNALS]
    explainable_deltas = [d for d in deltas if d.variation in (VariationSignal.EXPLAINABLE_VARIATION_SIGNAL,
                                                                VariationSignal.EXPLAINABLE_LOCATION_VARIATION_SIGNAL)]
    if explained or explainable_deltas:
        for c in explained:
            questions.append(f"{c.dimension.value}: {c.explanation}")
        for d in explainable_deltas:
            questions.append(f"{d.delta_type.value}: {d.explanation}")
        return AssessmentResult(SystemEvidenceAssessment.EXPLAINABLE_VARIATION_IDENTIFIED, basis,
                                comparisons, deltas, questions)

    corroborated = [c for c in comparisons if c.signal in CORROBORATING_SIGNALS]
    others = [c for c in comparisons if c.signal not in CORROBORATING_SIGNALS]
    for c in others:
        questions.append(f"{c.dimension.value}: {c.explanation}")
    for d in deltas:
        if d.variation is VariationSignal.UNEXPLAINED_VARIATION:
            questions.append(f"{d.delta_type.value}: {d.explanation}")
    if comparisons and not others and not any(d.variation is VariationSignal.UNEXPLAINED_VARIATION for d in deltas):
        return AssessmentResult(SystemEvidenceAssessment.EVIDENCE_CORROBORATES, basis, comparisons, deltas, questions)
    if corroborated and all(c.signal in INSUFFICIENT_SIGNALS | AMBIGUOUS_SIGNALS | UNAVAILABLE_SIGNALS for c in others):
        return AssessmentResult(SystemEvidenceAssessment.EVIDENCE_PARTIALLY_CORROBORATES, basis,
                                comparisons, deltas, questions)
    return AssessmentResult(SystemEvidenceAssessment.INSUFFICIENT_EVIDENCE, basis, comparisons, deltas,
                            questions or ["No independent evidence corroborates the delivered identity."])
