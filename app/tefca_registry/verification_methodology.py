"""Contract-control determination: from evidence observations to a coverage figure.

WHAT THIS ADDS, AND WHAT IT DELIBERATELY DOES NOT REPLACE
─────────────────────────────────────────────────────────
Three layers already exist and are unchanged:

    applicability.py         which evidence DIMENSIONS apply to an entity
    source_applicability.py  which SOURCES it is meaningful to ask
    evidence_vocabulary.py   ObservationState — what a source actually SAID

This module is the layer above them: it turns observations into a per-CONTROL
state, and controls into a coverage figure a reviewer can act on. It concludes
nothing about compliance on its own — see `assess_control`'s refusal to emit
NON_COMPLIANT.

THE DEFECT THIS EXISTS TO PREVENT
─────────────────────────────────
"1 of 3 sources agree" as the headline determination. That sentence treats
NPPES, SAM.gov and LEIE as three interchangeable votes on the same question,
which they are not: they answer different questions, about different entity
types, under different contract requirements. Counting them makes an HIE with no
NPI look like a provider hiding one, and makes a SAM.gov absence — which is
non-determinative for most TEFCA participants — look like a finding.

A missing record is not a discrepancy. The headline is now:

    5 of 6 applicable controls satisfied, 1 requires analyst review

Source agreement still appears, but INSIDE a control, as supporting evidence.

TWO CONCEPTS THAT MUST NEVER COLLAPSE
─────────────────────────────────────
    ENTITY VERIFICATION      did we establish who this organisation is?
    CONTRACTUAL COMPLIANCE   does the contract requirement appear satisfied?

"Identity not automatically verified" is a statement about our evidence. It is
not "TEFCA non-compliant", which is a statement about the organisation — and one
only a human, checked by an independent QA reviewer, may make.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.core.evidence_vocabulary import ObservationState

METHODOLOGY_VERSION = "1.0.0"


# ═══════════════════════════════════════════════════════════════════════════
# D1 — the participation anchor
# ═══════════════════════════════════════════════════════════════════════════

#: TEFCA participation is established by the RCE/QHIN-delivered population and
#: nothing else. No external database is consulted to decide whether an
#: organisation participates — NPPES does not know about TEFCA, and an entity's
#: absence from SAM.gov says nothing about its Participant status. External
#: evidence validates ATTRIBUTES of a participant we already know about.
PARTICIPATION_ANCHOR = "RCE_DELIVERED_POPULATION"


# ═══════════════════════════════════════════════════════════════════════════
# D2 — entity classification
# ═══════════════════════════════════════════════════════════════════════════

class EntityClass(str, Enum):
    """What kind of organisation this is, for applicability purposes."""

    PROVIDER = "PROVIDER"
    HOSPITAL_HEALTH_SYSTEM = "HOSPITAL_HEALTH_SYSTEM"
    HIE_HIN = "HIE_HIN"
    HEALTH_IT_ORGANIZATION = "HEALTH_IT_ORGANIZATION"
    HEALTH_PLAN_PAYER = "HEALTH_PLAN_PAYER"
    PUBLIC_HEALTH_ORGANIZATION = "PUBLIC_HEALTH_ORGANIZATION"
    FEDERAL_GOVERNMENT_ORGANIZATION = "FEDERAL_GOVERNMENT_ORGANIZATION"
    #: Not a failure and not a finding. It means a human must classify this one
    #: before applicability can be decided, and the workflow says so.
    REQUIRES_CLASSIFICATION = "REQUIRES_CLASSIFICATION"


#: Signals in the delivered record that indicate a class. Ordered: the first
#: match wins, so the most specific signals come first. Deliberately DATA rather
#: than a chain of `if` statements — the whole point of D2 is that applicability
#: is configurable, and a rule someone can read in a table is a rule they can
#: correct without touching control flow.
CLASSIFICATION_SIGNALS: Tuple[Tuple[EntityClass, str, Tuple[str, ...]], ...] = (
    (EntityClass.FEDERAL_GOVERNMENT_ORGANIZATION, "sequoia_org_type",
     ("federal", "government agency", "va ", "dod", "indian health")),
    (EntityClass.PUBLIC_HEALTH_ORGANIZATION, "sequoia_org_type",
     ("public health", "health department", "state agency")),
    (EntityClass.HEALTH_PLAN_PAYER, "sequoia_org_type",
     ("payer", "health plan", "insurer", "managed care")),
    (EntityClass.HIE_HIN, "sequoia_org_type",
     ("qhin", "hie", "hin", "health information network",
      "health information exchange")),
    (EntityClass.HEALTH_IT_ORGANIZATION, "sequoia_org_type",
     ("health it", "technology", "vendor", "developer", "ehr")),
    (EntityClass.HOSPITAL_HEALTH_SYSTEM, "sequoia_org_type",
     ("hospital", "health system", "medical center", "academic")),
    (EntityClass.PROVIDER, "sequoia_org_type",
     ("provider", "practice", "clinic", "physician", "ambulatory")),
)


@dataclass
class Classification:
    """An entity class, with the evidence that produced it."""

    entity_class: EntityClass
    signal: Optional[str]
    signal_value: Optional[str]
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {"entity_class": self.entity_class.value, "signal": self.signal,
                "signal_value": self.signal_value, "rationale": self.rationale}


def _matches(text: str, needle: str) -> bool:
    """Word-boundary match, not a bare substring.

    `"hin"` as a substring matches "somet**hin**g", which classified an
    unmapped organisation as an HIE. Short acronyms are exactly the tokens most
    likely to appear inside ordinary words, so every needle is anchored at word
    boundaries. A multi-word needle like `"health plan"` still works because the
    boundary applies to the whole phrase.
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(needle.strip())}(?![a-z0-9])",
                     text) is not None


def classify_entity(record: Dict[str, Any]) -> Classification:
    """Classify from the DELIVERED record, never from an external source.

    An unrecognised organisation is REQUIRES_CLASSIFICATION, which routes it to
    a human. Guessing PROVIDER because most entities are providers would make
    the applicability matrix silently wrong for exactly the entities that most
    need care.
    """
    for entity_class, field_name, needles in CLASSIFICATION_SIGNALS:
        raw = (record.get(field_name) or record.get(field_name.replace("_", "")) or "")
        text = str(raw).strip().lower()
        if not text:
            continue
        for needle in needles:
            if _matches(text, needle):
                return Classification(
                    entity_class=entity_class, signal=field_name,
                    signal_value=str(raw),
                    rationale=f"{field_name}={raw!r} matched {needle!r}")
    return Classification(
        entity_class=EntityClass.REQUIRES_CLASSIFICATION, signal=None,
        signal_value=None,
        rationale="No delivered signal identified the entity class. A human "
                  "must classify this entity before applicability is decided.")


# ═══════════════════════════════════════════════════════════════════════════
# D2 — controls and their configurable applicability
# ═══════════════════════════════════════════════════════════════════════════

class Control(str, Enum):
    """A contract-derived verification control. NOT a data source."""

    ENTITY_IDENTITY = "ENTITY_IDENTITY"
    PROVIDER_ENUMERATION = "PROVIDER_ENUMERATION"
    MEDICARE_ENROLMENT = "MEDICARE_ENROLMENT"
    EXCLUSION_SCREENING = "EXCLUSION_SCREENING"
    FEDERAL_AWARD_ELIGIBILITY = "FEDERAL_AWARD_ELIGIBILITY"
    TEFCA_RELATIONSHIP = "TEFCA_RELATIONSHIP"


class Requirement(str, Enum):
    """How much the methodology asks of a control for this entity class."""

    REQUIRED = "REQUIRED"          # must resolve, or a human must look
    CORROBORATIVE = "CORROBORATIVE"  # strengthens, never decides alone
    NOT_APPLICABLE = "NOT_APPLICABLE"  # has no bearing on this entity


@dataclass(frozen=True)
class ControlSpec:
    """One row of the applicability matrix."""

    control: Control
    contract_task: str
    requirement: Requirement
    sources: Tuple[str, ...]
    rationale: str
    #: What ABSENCE means for this control, which is not the same question for
    #: all of them. Exclusion screening is satisfied BY absence — a provider not
    #: on the LEIE is the good outcome, and sending every clean screen to a
    #: human would make the queue unusable and teach reviewers to click through.
    #: Identity and enumeration are the opposite: absence means we did not
    #: confirm what we set out to confirm, so a person should look.
    satisfied_by_absence: bool = False


#: THE APPLICABILITY MATRIX — configuration, not control flow.
#:
#: `entity class -> control -> (requirement, applicable sources, why)`. This is
#: the artefact a methodology reviewer reads and corrects. Nothing below
#: branches on an entity class; they all consult this table.
#:
#: Two rules are visible in it and are the point of the whole exercise:
#:   * NPI is NOT required of an HIE/HIN, a Health IT organisation, a payer or
#:     a federal body. Those organisations legitimately have none.
#:   * SAM.gov is CORROBORATIVE almost everywhere. Absence from SAM.gov is not
#:     evidence against a TEFCA participant; it is determinative only where a
#:     federal award relationship is actually in question.
APPLICABILITY_MATRIX: Dict[EntityClass, Tuple[ControlSpec, ...]] = {}


def _spec(control, task, requirement, sources, rationale,
          satisfied_by_absence=False):
    return ControlSpec(control=control, contract_task=task,
                       requirement=requirement, sources=tuple(sources),
                       rationale=rationale,
                       satisfied_by_absence=satisfied_by_absence)


_IDENTITY_ALL = "Every delivered participant must be identifiable."
_REL_ALL = "The delivered relationship graph is the TEFCA anchor."

def _common(extra: Tuple[ControlSpec, ...]) -> Tuple[ControlSpec, ...]:
    return (
        _spec(Control.ENTITY_IDENTITY, "Task 3", Requirement.REQUIRED,
              ("rce", "nppes"), _IDENTITY_ALL),
        _spec(Control.TEFCA_RELATIONSHIP, "Task 3", Requirement.REQUIRED,
              ("rce",), _REL_ALL),
        _spec(Control.FEDERAL_AWARD_ELIGIBILITY, "Task 4",
              Requirement.CORROBORATIVE, ("sam_gov",),
              "SAM.gov registration is not a TEFCA participation requirement. "
              "Absence is non-determinative: it neither satisfies nor "
              "withholds. Because the control is CORROBORATIVE it cannot gate "
              "compliance either way."),
    ) + extra


APPLICABILITY_MATRIX[EntityClass.PROVIDER] = _common((
    _spec(Control.PROVIDER_ENUMERATION, "Task 3", Requirement.REQUIRED,
          ("nppes",), "A provider organisation is expected to be enumerated."),
    _spec(Control.MEDICARE_ENROLMENT, "Task 4", Requirement.CORROBORATIVE,
          ("pecos",), "Medicare enrolment is common but not required by TEFCA."),
    _spec(Control.EXCLUSION_SCREENING, "Task 4", Requirement.REQUIRED,
          ("leie",), "Exclusion screening applies to healthcare providers. A "
                     "clean screen SATISFIES this control; an exclusion match "
                     "is what requires review.",
          satisfied_by_absence=True),
))

APPLICABILITY_MATRIX[EntityClass.HOSPITAL_HEALTH_SYSTEM] = \
    APPLICABILITY_MATRIX[EntityClass.PROVIDER]

APPLICABILITY_MATRIX[EntityClass.HIE_HIN] = _common((
    _spec(Control.PROVIDER_ENUMERATION, "Task 3", Requirement.NOT_APPLICABLE,
          (), "An HIE/HIN delivers no clinical care and is not enumerated. "
              "Absence of an NPI is expected, not a discrepancy."),
    _spec(Control.MEDICARE_ENROLMENT, "Task 4", Requirement.NOT_APPLICABLE,
          (), "Not a Medicare-enrolling entity."),
    _spec(Control.EXCLUSION_SCREENING, "Task 4", Requirement.CORROBORATIVE,
          ("leie",), "Organisation-level screening only where a name match is "
                     "meaningful. A clean screen satisfies it.",
          satisfied_by_absence=True),
))

APPLICABILITY_MATRIX[EntityClass.HEALTH_IT_ORGANIZATION] = \
    APPLICABILITY_MATRIX[EntityClass.HIE_HIN]

APPLICABILITY_MATRIX[EntityClass.HEALTH_PLAN_PAYER] = _common((
    _spec(Control.PROVIDER_ENUMERATION, "Task 3", Requirement.NOT_APPLICABLE,
          (), "A payer is not a provider and is not enumerated as one."),
    _spec(Control.MEDICARE_ENROLMENT, "Task 4", Requirement.NOT_APPLICABLE,
          (), "Payer participation is not Medicare provider enrolment."),
    _spec(Control.EXCLUSION_SCREENING, "Task 4", Requirement.CORROBORATIVE,
          ("leie",), "Organisation-level screening only. A clean screen "
                     "satisfies it.",
          satisfied_by_absence=True),
))

APPLICABILITY_MATRIX[EntityClass.PUBLIC_HEALTH_ORGANIZATION] = _common((
    _spec(Control.PROVIDER_ENUMERATION, "Task 3", Requirement.CORROBORATIVE,
          ("nppes",), "Some public health agencies are enumerated; many are "
                      "not. Absence is not adverse."),
    _spec(Control.MEDICARE_ENROLMENT, "Task 4", Requirement.NOT_APPLICABLE,
          (), "Not a Medicare-enrolling entity."),
    _spec(Control.EXCLUSION_SCREENING, "Task 4", Requirement.CORROBORATIVE,
          ("leie",), "Organisation-level screening only. A clean screen "
                     "satisfies it.",
          satisfied_by_absence=True),
))

APPLICABILITY_MATRIX[EntityClass.FEDERAL_GOVERNMENT_ORGANIZATION] = _common((
    _spec(Control.PROVIDER_ENUMERATION, "Task 3", Requirement.NOT_APPLICABLE,
          (), "A federal body is not enumerated as a provider."),
    _spec(Control.MEDICARE_ENROLMENT, "Task 4", Requirement.NOT_APPLICABLE,
          (), "Not applicable to a federal organisation."),
    _spec(Control.EXCLUSION_SCREENING, "Task 4", Requirement.NOT_APPLICABLE,
          (), "Exclusion screening does not apply to a federal agency."),
))

#: An unclassified entity gets identity and relationship only, and everything
#: else waits for the human classification. Applying a provider's matrix to an
#: unknown entity is the error this whole module exists to avoid.
APPLICABILITY_MATRIX[EntityClass.REQUIRES_CLASSIFICATION] = (
    _spec(Control.ENTITY_IDENTITY, "Task 3", Requirement.REQUIRED,
          ("rce",), _IDENTITY_ALL),
    _spec(Control.TEFCA_RELATIONSHIP, "Task 3", Requirement.REQUIRED,
          ("rce",), _REL_ALL),
)


def controls_for(entity_class: EntityClass) -> Tuple[ControlSpec, ...]:
    return APPLICABILITY_MATRIX.get(entity_class, ())


# ═══════════════════════════════════════════════════════════════════════════
# D3 — the six evidence states
# ═══════════════════════════════════════════════════════════════════════════

class EvidenceState(str, Enum):
    """How one control resolved. Exactly six, and three of them are not bad news."""

    VERIFIED = "VERIFIED"
    #: Applicable evidence MATERIALLY CONTRADICTS the attribute under review.
    #: This is the only state that may support an adverse preliminary
    #: assessment, and even then only a human may conclude from it.
    CONFLICT = "CONFLICT"
    #: The source answered and had no record. Informative, and not adverse.
    NOT_FOUND = "NOT_FOUND"
    #: The control has no bearing on this entity class.
    NOT_APPLICABLE = "NOT_APPLICABLE"
    #: The source did not answer. A fact about the world, not the entity.
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    #: Electronic evidence cannot settle it. A person must look.
    MANUAL_VERIFICATION_REQUIRED = "MANUAL_VERIFICATION_REQUIRED"


#: THE RULE THIS MODULE EXISTS FOR. None of these three may ever, by itself,
#: produce an adverse contractual determination.
NON_ADVERSE_STATES = frozenset({
    EvidenceState.NOT_FOUND,
    EvidenceState.NOT_APPLICABLE,
    EvidenceState.SOURCE_UNAVAILABLE,
})

#: How a Layer-1 observation maps into the control layer. `MULTIPLE_MATCHES`
#: and `AMBIGUOUS` are cardinality and weak-match problems, not contradictions —
#: they go to a human rather than becoming CONFLICT.
_OBSERVATION_TO_STATE: Dict[str, EvidenceState] = {
    ObservationState.MATCH_OBSERVED.value: EvidenceState.VERIFIED,
    ObservationState.NO_MATCH_OBSERVED.value: EvidenceState.NOT_FOUND,
    ObservationState.SOURCE_UNAVAILABLE.value: EvidenceState.SOURCE_UNAVAILABLE,
    ObservationState.LOOKUP_NOT_APPLICABLE.value: EvidenceState.NOT_APPLICABLE,
    ObservationState.MULTIPLE_MATCHES.value:
        EvidenceState.MANUAL_VERIFICATION_REQUIRED,
    ObservationState.AMBIGUOUS.value:
        EvidenceState.MANUAL_VERIFICATION_REQUIRED,
    ObservationState.INSUFFICIENT_IDENTIFIER.value:
        EvidenceState.MANUAL_VERIFICATION_REQUIRED,
    # OUR bug is not the source's outage and not the entity's problem.
    ObservationState.ERROR.value: EvidenceState.MANUAL_VERIFICATION_REQUIRED,
}


def evidence_state_for(observation_state: str, *,
                       contradicts: bool = False) -> EvidenceState:
    """One observation's contribution to a control.

    `contradicts` is the ONLY route to CONFLICT, and it is the caller's explicit
    assertion that applicable evidence materially disagrees — a returned legal
    name that is a different organisation, an exclusion that matches the entity.
    A source simply having no record can never reach it.
    """
    if contradicts:
        return EvidenceState.CONFLICT
    return _OBSERVATION_TO_STATE.get(
        observation_state, EvidenceState.MANUAL_VERIFICATION_REQUIRED)


# ═══════════════════════════════════════════════════════════════════════════
# D3 — control assessment
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Observation:
    """One source's answer, with the provenance needed to reproduce it."""

    source: str
    state: str                          # an ObservationState value
    contradicts: bool = False
    matched_name: Optional[str] = None
    matched_identifier: Optional[str] = None
    match_method: Optional[str] = None
    dataset_version: Optional[str] = None
    retrieved_at: Optional[str] = None
    query_attributes: Dict[str, Any] = field(default_factory=dict)
    detail: Optional[str] = None
    evidence_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source, "observation_state": self.state,
            "contradicts": self.contradicts, "matched_name": self.matched_name,
            "matched_identifier": self.matched_identifier,
            "match_method": self.match_method,
            "dataset_version": self.dataset_version,
            "retrieved_at": self.retrieved_at,
            "query_attributes": dict(self.query_attributes),
            "detail": self.detail, "evidence_hash": self.evidence_hash,
        }


@dataclass
class ManualEvidence:
    """Documentary evidence an authorised analyst attached to a control.

    MANUAL_VERIFICATION_REQUIRED is a state with no electronic way out. Without
    somewhere for a document to attach, that state is a dead end and the honest
    workflow becomes an unfalsifiable backlog — so this is what closes it.

    It enters the SAME audit chain as an electronic observation: it carries a
    hash, an actor, a rationale and a QA disposition, and it is never applied
    without a control to support. Nothing here concludes compliance; an analyst
    attaching a document is still only evidence, and independent QA still
    decides whether it stands.
    """

    control: Control
    evidence_type: str            # e.g. "participation agreement", "letter"
    source: str                   # who supplied it
    received_date: str
    document_hash: str            # sha256 of the document as received
    analyst: str
    analyst_rationale: str
    qa_reviewer: Optional[str] = None
    qa_disposition: Optional[str] = None   # APPROVE | RETURN | ESCALATE

    @property
    def qa_approved(self) -> bool:
        """Only an independently approved document may satisfy a control."""
        return (self.qa_disposition or "").strip().upper() == "APPROVE" and             bool(self.qa_reviewer) and             self.qa_reviewer.strip().lower() != self.analyst.strip().lower()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "control": self.control.value, "evidence_type": self.evidence_type,
            "source": self.source, "received_date": self.received_date,
            "document_hash": self.document_hash, "analyst": self.analyst,
            "analyst_rationale": self.analyst_rationale,
            "qa_reviewer": self.qa_reviewer,
            "qa_disposition": self.qa_disposition,
            "qa_approved": self.qa_approved,
        }


@dataclass
class ControlAssessment:
    """One control's resolution, with its supporting evidence intact."""

    control: Control
    contract_task: str
    requirement: Requirement
    state: EvidenceState
    observations: List[Observation]
    rationale: str
    #: Source agreement, kept INSIDE the control where it belongs.
    agreement: Tuple[int, int] = (0, 0)
    #: QA-approved documentary evidence that resolved this control, if any.
    manual_evidence: List["ManualEvidence"] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return self.state is EvidenceState.VERIFIED

    @property
    def needs_analyst(self) -> bool:
        return self.state in (EvidenceState.MANUAL_VERIFICATION_REQUIRED,
                              EvidenceState.CONFLICT)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "control": self.control.value, "contract_task": self.contract_task,
            "requirement": self.requirement.value, "state": self.state.value,
            "rationale": self.rationale,
            "agreement": {"agreeing": self.agreement[0],
                          "applicable_observations": self.agreement[1]},
            "observations": [o.to_dict() for o in self.observations],
            "manual_evidence": [d.to_dict() for d in self.manual_evidence],
        }


def assess_control(spec: ControlSpec,
                   observations: List[Observation],
                   manual: Optional[List[ManualEvidence]] = None
                   ) -> ControlAssessment:
    """Resolve one control from the observations that apply to it.

    AUTOMATION STOPS HERE. The worst thing this can say is CONFLICT, which is a
    statement about evidence. It cannot say NON_COMPLIANT, which is a statement
    about an organisation and belongs to an analyst whose work an independent QA
    reviewer has checked.
    """
    if spec.requirement is Requirement.NOT_APPLICABLE:
        return ControlAssessment(
            control=spec.control, contract_task=spec.contract_task,
            requirement=spec.requirement, state=EvidenceState.NOT_APPLICABLE,
            observations=[], rationale=spec.rationale, agreement=(0, 0))

    relevant = [o for o in observations if o.source in spec.sources]
    states = [evidence_state_for(o.state, contradicts=o.contradicts)
              for o in relevant]

    verified = sum(1 for s in states if s is EvidenceState.VERIFIED)
    considered = sum(1 for s in states if s is not EvidenceState.NOT_APPLICABLE)

    if not relevant:
        state = EvidenceState.NOT_APPLICABLE if not spec.sources else \
            EvidenceState.SOURCE_UNAVAILABLE
        why = ("No applicable source for this control." if not spec.sources
               else "No observation was recorded from any applicable source.")
    elif EvidenceState.CONFLICT in states:
        state, why = EvidenceState.CONFLICT, (
            "Applicable evidence materially contradicts the attribute under "
            "review.")
    elif verified:
        state, why = EvidenceState.VERIFIED, (
            f"{verified} of {considered} applicable observation(s) corroborate "
            f"the attribute.")
    elif EvidenceState.MANUAL_VERIFICATION_REQUIRED in states:
        state, why = EvidenceState.MANUAL_VERIFICATION_REQUIRED, (
            "Electronic evidence could not settle this control.")
    elif all(s is EvidenceState.SOURCE_UNAVAILABLE for s in states):
        state, why = EvidenceState.SOURCE_UNAVAILABLE, (
            "No applicable source answered. This is a fact about the sources, "
            "not about the entity.")
    elif all(s in NON_ADVERSE_STATES for s in states):
        # Everything applicable said "no record". What that MEANS depends on the
        # control, and never on its own is it adverse.
        if spec.satisfied_by_absence:
            state, why = EvidenceState.VERIFIED, (
                "No applicable source held a record, which is what satisfies "
                "this control.")
        elif spec.requirement is Requirement.REQUIRED:
            state, why = EvidenceState.MANUAL_VERIFICATION_REQUIRED, (
                "No applicable source held a record. Absence is not a "
                "discrepancy; a reviewer must determine whether it matters.")
        else:
            state, why = EvidenceState.NOT_FOUND, (
                "No applicable source held a record. This control is "
                "corroborative, so absence is not adverse.")
    else:
        state, why = EvidenceState.MANUAL_VERIFICATION_REQUIRED, (
            "Mixed evidence that automation may not resolve.")

    # Documentary evidence can only ever RESOLVE a control that electronic
    # evidence could not, and only once independent QA has approved it. It can
    # never overturn a CONFLICT — a document asserting the contrary of the
    # evidence is a disagreement for a human to weigh, not an override.
    approved_docs = [d for d in (manual or [])
                     if d.control is spec.control and d.qa_approved]
    if approved_docs and state is EvidenceState.MANUAL_VERIFICATION_REQUIRED:
        state = EvidenceState.VERIFIED
        why = (f"Resolved by documentary evidence "
               f"({approved_docs[0].evidence_type}), attached by "
               f"{approved_docs[0].analyst} and approved by "
               f"{approved_docs[0].qa_reviewer}.")

    return ControlAssessment(
        control=spec.control, contract_task=spec.contract_task,
        requirement=spec.requirement, state=state, observations=relevant,
        rationale=why, agreement=(verified, considered),
        manual_evidence=list(approved_docs))


# ═══════════════════════════════════════════════════════════════════════════
# Verification coverage, and the two concepts kept apart
# ═══════════════════════════════════════════════════════════════════════════

class EntityVerification(str, Enum):
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    UNRESOLVED = "UNRESOLVED"
    CONFLICTING = "CONFLICTING"


class ContractualCompliance(str, Enum):
    SATISFIED = "SATISFIED"
    POTENTIAL_FINDING = "POTENTIAL_FINDING"
    #: Reachable ONLY through an analyst determination that independent QA has
    #: approved. `preliminary_assessment` never returns it.
    NON_COMPLIANT = "NON_COMPLIANT"
    UNABLE_TO_DETERMINE = "UNABLE_TO_DETERMINE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class VerificationCoverage:
    """The headline figure, stated in controls rather than database votes."""

    applicable: int
    satisfied: int
    requires_analyst: int
    not_found: int
    source_unavailable: int
    not_applicable: int

    def summary(self) -> str:
        parts = [f"{self.satisfied} of {self.applicable} applicable controls "
                 f"satisfied"]
        if self.requires_analyst:
            parts.append(f"{self.requires_analyst} require analyst review")
        if self.source_unavailable:
            parts.append(f"{self.source_unavailable} could not be checked")
        return ", ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {"applicable_controls": self.applicable,
                "satisfied": self.satisfied,
                "requires_analyst_review": self.requires_analyst,
                "not_found": self.not_found,
                "source_unavailable": self.source_unavailable,
                "not_applicable": self.not_applicable,
                "summary": self.summary()}


@dataclass
class PreliminaryAssessment:
    """Everything automation is permitted to say about one entity."""

    entity_identifier: str
    classification: Classification
    controls: List[ControlAssessment]
    coverage: VerificationCoverage
    entity_verification: EntityVerification
    contractual_compliance: ContractualCompliance
    methodology_version: str = METHODOLOGY_VERSION
    participation_anchor: str = PARTICIPATION_ANCHOR

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_identifier": self.entity_identifier,
            "methodology_version": self.methodology_version,
            "participation_anchor": self.participation_anchor,
            "classification": self.classification.to_dict(),
            "verification_coverage": self.coverage.to_dict(),
            "entity_verification": self.entity_verification.value,
            "contractual_compliance": self.contractual_compliance.value,
            "controls": [c.to_dict() for c in self.controls],
        }


def preliminary_assessment(entity_identifier: str,
                           record: Dict[str, Any],
                           observations: List[Observation],
                           manual: Optional[List[ManualEvidence]] = None
                           ) -> PreliminaryAssessment:
    """Classify, assess every applicable control, and report coverage.

    Returns a PRELIMINARY assessment. It is the input to an analyst, never a
    determination, and `contractual_compliance` can never be NON_COMPLIANT here
    — that value is reachable only after a human decision an independent QA
    reviewer has approved.
    """
    classification = classify_entity(record)
    specs = controls_for(classification.entity_class)
    assessments = [assess_control(spec, observations, manual)
                   for spec in specs]

    applicable = [a for a in assessments
                  if a.state is not EvidenceState.NOT_APPLICABLE]
    satisfied = [a for a in applicable if a.satisfied]
    needs = [a for a in applicable if a.needs_analyst]

    coverage = VerificationCoverage(
        applicable=len(applicable),
        satisfied=len(satisfied),
        requires_analyst=len(needs),
        not_found=sum(1 for a in applicable
                      if a.state is EvidenceState.NOT_FOUND),
        source_unavailable=sum(1 for a in applicable
                               if a.state is EvidenceState.SOURCE_UNAVAILABLE),
        not_applicable=len(assessments) - len(applicable),
    )

    # ENTITY VERIFICATION — about our evidence.
    if any(a.state is EvidenceState.CONFLICT for a in applicable):
        verification = EntityVerification.CONFLICTING
    elif applicable and len(satisfied) == len(applicable):
        verification = EntityVerification.VERIFIED
    elif satisfied:
        verification = EntityVerification.PARTIALLY_VERIFIED
    else:
        verification = EntityVerification.UNRESOLVED

    # CONTRACTUAL COMPLIANCE — about the organisation, and deliberately timid.
    required = [a for a in applicable
                if a.requirement is Requirement.REQUIRED]
    if not applicable:
        compliance = ContractualCompliance.NOT_APPLICABLE
    elif any(a.state is EvidenceState.CONFLICT for a in required):
        # The strongest automation may say. A human decides whether it is a
        # finding; QA decides whether that stands.
        compliance = ContractualCompliance.POTENTIAL_FINDING
    elif required and all(a.satisfied for a in required):
        compliance = ContractualCompliance.SATISFIED
    else:
        # Includes every NOT_FOUND, SOURCE_UNAVAILABLE and
        # MANUAL_VERIFICATION_REQUIRED case. None of them is adverse.
        compliance = ContractualCompliance.UNABLE_TO_DETERMINE

    return PreliminaryAssessment(
        entity_identifier=entity_identifier, classification=classification,
        controls=assessments, coverage=coverage,
        entity_verification=verification, contractual_compliance=compliance)
