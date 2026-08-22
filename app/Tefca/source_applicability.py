"""Which authoritative SOURCES apply to this entity, and why.

WHY THIS IS NOT `applicability.py`
──────────────────────────────────
`applicability.py` decides which evidence DIMENSIONS apply — D1 identity, D2
Medicare enrolment, and so on. That is the methodology question and it is
already answered there. This module answers the operational one that Phase 6
needs before it can make a single external call: given this entity, is there any
point asking NPPES? Is PECOS even keyed on anything we hold?

They are different questions with different answers. D1 identity is REQUIRED for
every entity, but NPPES cannot be asked about an entity with no NPI and no name
worth searching — so the dimension is required and the source is not applicable.
Collapsing the two would either fabricate a source call or drop a required
dimension, and both are worse than keeping them apart.

THE FOUR-WAY DISTINCTION THAT MUST SURVIVE
──────────────────────────────────────────
    NOT_APPLICABLE      we did not ask, and asking would have been meaningless
    NO_MATCH_OBSERVED   we asked and the source said no
    SOURCE_UNAVAILABLE  we asked and the source did not answer
    MATCH_OBSERVED      we asked and the source said yes

Those are four different facts about the world and they must never collapse into
"missing". An entity with no NPI is not an entity NPPES rejected. A timeout is
not a clean result. This module produces the first of the four; the observation
layer produces the other three, using the canonical `ObservationState`
vocabulary from app/core/evidence_vocabulary.py.

WHAT IT REFUSES TO DECIDE
─────────────────────────
Nothing here concludes anything about an entity. An applicability of
NOT_APPLICABLE says a source has no bearing on this entity; it never says the
entity is compliant, verified, or anything else. Where applicability itself turns
on an unresolved COR decision, the answer is UNKNOWN_PENDING_METHODOLOGY with the
decision named — never a guess in either direction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.Tefca.applicability import (
    ApplicabilityProfile,
    EntityCategory,
    available_npi,
    build_profile,
)

#: Bumped when a rule below changes. Recorded on every observation so an old
#: decision can be read with the rules that produced it.
SOURCE_APPLICABILITY_VERSION = "1.0"


class SourceApplicability(str, Enum):
    """How much bearing a source has on one entity."""

    #: The methodology requires this source for this entity.
    REQUIRED = "REQUIRED"
    #: Worth asking; the answer is evidence either way.
    APPLICABLE = "APPLICABLE"
    #: Worth asking only if a precondition holds — usually an identifier we may
    #: not have until another source answers first.
    CONDITIONALLY_APPLICABLE = "CONDITIONALLY_APPLICABLE"
    #: Asking is meaningless for this entity. NOT a finding against it.
    NOT_APPLICABLE = "NOT_APPLICABLE"
    #: Applicability itself depends on a COR decision that has not been made.
    UNKNOWN_PENDING_METHODOLOGY = "UNKNOWN_PENDING_METHODOLOGY"


class Source(str, Enum):
    NPPES = "NPPES"
    CMS_PPEF_ENROLLMENT = "CMS_PPEF_ENROLLMENT"
    CMS_PPEF_PRACTICE_LOCATION = "CMS_PPEF_PRACTICE_LOCATION"
    CMS_PPEF_REASSIGNMENT = "CMS_PPEF_REASSIGNMENT"
    OIG_LEIE = "OIG_LEIE"
    SAM_GOV = "SAM_GOV"
    CMS_REVOCATION = "CMS_REVOCATION"


@dataclass
class SourceDecision:
    """One source, one entity, one decision, with the reason attached."""

    source: Source
    applicability: SourceApplicability
    rationale: str
    #: The identifier a lookup would key on, when there is one.
    lookup_key: Optional[str] = None
    lookup_kind: Optional[str] = None
    #: Set when applicability turns on an unresolved COR decision.
    blocked_by: Optional[str] = None

    @property
    def should_query(self) -> bool:
        """Only REQUIRED and APPLICABLE are queried in a population run.

        CONDITIONALLY_APPLICABLE waits for its precondition; the other two are
        not questions this run can ask.
        """
        return self.applicability in (SourceApplicability.REQUIRED,
                                      SourceApplicability.APPLICABLE)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.value,
            "applicability": self.applicability.value,
            "rationale": self.rationale,
            "lookup_key": self.lookup_key,
            "lookup_kind": self.lookup_kind,
            "blocked_by": self.blocked_by,
            "should_query": self.should_query,
        }


@dataclass
class SourceApplicabilityMatrix:
    """Every source decision for one entity."""

    entity_id: Optional[str]
    profile: ApplicabilityProfile
    decisions: Dict[str, SourceDecision] = field(default_factory=dict)
    version: str = SOURCE_APPLICABILITY_VERSION

    def of(self, source: Source) -> SourceDecision:
        return self.decisions[source.value]

    @property
    def queryable(self) -> List[SourceDecision]:
        return [d for d in self.decisions.values() if d.should_query]

    @property
    def blocked(self) -> List[SourceDecision]:
        return [d for d in self.decisions.values() if d.blocked_by]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "version": self.version,
            "entity_category": self.profile.entity_category,
            "tefca_class": self.profile.tefca_class,
            "npi_available": bool(self.profile.npi_available),
            "medicare_relevance": self.profile.medicare_relevance,
            "decisions": {k: v.to_dict() for k, v in self.decisions.items()},
        }


# ── the rules ───────────────────────────────────────────────────────────────
#
# Each rule states one thing about identifiers or entity kind. None of them
# states anything about compliance.

_NON_PROVIDER_CATEGORIES = (
    EntityCategory.PAYER,
    EntityCategory.HIE_HIN_QHIN,
    EntityCategory.PUBLIC_HEALTH_AGENCY,
)


def _nppes(profile: ApplicabilityProfile, entity: Dict[str, Any]) -> SourceDecision:
    npi = profile.npi_available
    if npi:
        return SourceDecision(
            Source.NPPES, SourceApplicability.REQUIRED,
            "an NPI is present, and NPPES is the identity authority for it",
            lookup_key=npi, lookup_kind="NPI")
    name = (entity.get("legal_name") or entity.get("organization_name")
            or entity.get("name") or "").strip()
    if name:
        return SourceDecision(
            Source.NPPES, SourceApplicability.APPLICABLE,
            ("no NPI was delivered, so NPPES can only be searched by name. A "
             "name search corroborates; it does not establish identity."),
            lookup_key=name, lookup_kind="ORGANIZATION_NAME")
    return SourceDecision(
        Source.NPPES, SourceApplicability.NOT_APPLICABLE,
        ("neither an NPI nor a usable name was delivered, so there is nothing "
         "to ask NPPES. This is a statement about the identifiers we hold, not "
         "a finding against the entity."))


def _ppef_enrollment(profile: ApplicabilityProfile) -> SourceDecision:
    if not profile.npi_available:
        return SourceDecision(
            Source.CMS_PPEF_ENROLLMENT, SourceApplicability.NOT_APPLICABLE,
            ("PPEF is keyed on NPI and no NPI was delivered. There is no key to "
             "look up, which is not the same as an absent enrolment."))
    if profile.entity_category in _NON_PROVIDER_CATEGORIES:
        return SourceDecision(
            Source.CMS_PPEF_ENROLLMENT, SourceApplicability.NOT_APPLICABLE,
            (f"{profile.entity_category} entities do not enrol in Medicare as "
             f"providers, so absence of an enrolment record is expected rather "
             f"than informative."),
            lookup_key=profile.npi_available, lookup_kind="NPI")
    if profile.medicare_relevance == "UNLIKELY":
        return SourceDecision(
            Source.CMS_PPEF_ENROLLMENT, SourceApplicability.NOT_APPLICABLE,
            "NPPES taxonomy indicates Medicare enrolment is not expected",
            lookup_key=profile.npi_available, lookup_kind="NPI")
    if profile.medicare_relevance == "UNDETERMINED":
        return SourceDecision(
            Source.CMS_PPEF_ENROLLMENT, SourceApplicability.APPLICABLE,
            ("Medicare relevance is undetermined, so the enrolment record is "
             "worth establishing as a fact either way"),
            lookup_key=profile.npi_available, lookup_kind="NPI")
    return SourceDecision(
        Source.CMS_PPEF_ENROLLMENT, SourceApplicability.REQUIRED,
        "an NPI is present and NPPES taxonomy indicates Medicare relevance",
        lookup_key=profile.npi_available, lookup_kind="NPI")


def _ppef_dependent(source: Source, profile: ApplicabilityProfile,
                    what: str) -> SourceDecision:
    """Sub-files keyed on ENRLMT_ID, which only the enrolment record supplies."""
    enrollment = _ppef_enrollment(profile)
    if enrollment.applicability is SourceApplicability.NOT_APPLICABLE:
        return SourceDecision(
            source, SourceApplicability.NOT_APPLICABLE,
            f"the enrolment record is not applicable, so neither is {what}")
    return SourceDecision(
        source, SourceApplicability.CONDITIONALLY_APPLICABLE,
        (f"{what} is keyed on ENRLMT_ID, which only exists once the enrolment "
         f"record has been matched. Conditional on that match, not on the "
         f"entity."),
        lookup_key=None, lookup_kind="ENRLMT_ID")


def _oig_leie(profile: ApplicabilityProfile,
              entity: Dict[str, Any]) -> SourceDecision:
    """Exclusion screening. Applies broadly — that is the point of it.

    LEIE covers individuals and entities across healthcare, not only Medicare
    enrollees, so a narrow applicability rule here would create exactly the
    blind spot exclusion screening exists to close.
    """
    npi = profile.npi_available
    name = (entity.get("legal_name") or entity.get("organization_name")
            or entity.get("name") or "").strip()
    if not npi and not name:
        return SourceDecision(
            Source.OIG_LEIE, SourceApplicability.NOT_APPLICABLE,
            "no NPI and no name, so there is nothing to screen against")
    return SourceDecision(
        Source.OIG_LEIE, SourceApplicability.REQUIRED,
        ("exclusion screening applies to every entity we can identify; "
         "LEIE is not limited to Medicare enrollees"),
        lookup_key=npi or name, lookup_kind="NPI" if npi else "NAME")


def _sam_gov(profile: ApplicabilityProfile,
             entity: Dict[str, Any]) -> SourceDecision:
    """Federal exclusion/registration. Applicability is a COR question.

    SAM registration is required of federal contractors and grant recipients. A
    TEFCA Participant is not automatically either, and the methodology package
    does not say which TEFCA entities are expected to appear in SAM. Deciding
    that here would be inventing a federal registration requirement, so it is
    named as an open decision instead.

    The exclusion half of SAM is different in kind from the registration half,
    and that distinction is part of what is undecided.
    """
    uei = (entity.get("uei") or entity.get("sam_uei") or "").strip()
    if uei:
        return SourceDecision(
            Source.SAM_GOV, SourceApplicability.APPLICABLE,
            ("a UEI was delivered, so the entity asserts a federal registration "
             "and SAM can be asked about it directly"),
            lookup_key=uei, lookup_kind="UEI")
    return SourceDecision(
        Source.SAM_GOV, SourceApplicability.UNKNOWN_PENDING_METHODOLOGY,
        ("no UEI was delivered. Whether a TEFCA entity without a UEI is expected "
         "to appear in SAM at all — and whether its absence is evidence of "
         "anything — is not settled by the approved methodology. Recorded as "
         "undecided rather than assumed in either direction."),
        blocked_by="D4")


def _cms_revocation(profile: ApplicabilityProfile) -> SourceDecision:
    """Revocation is keyed on NPI and is only meaningful for enrollees."""
    if not profile.npi_available:
        return SourceDecision(
            Source.CMS_REVOCATION, SourceApplicability.NOT_APPLICABLE,
            ("the revocation dataset is keyed on NPI and none was delivered"))
    if profile.entity_category in _NON_PROVIDER_CATEGORIES:
        return SourceDecision(
            Source.CMS_REVOCATION, SourceApplicability.NOT_APPLICABLE,
            (f"{profile.entity_category} entities do not hold Medicare billing "
             f"privileges, so there is nothing that could be revoked"),
            lookup_key=profile.npi_available, lookup_kind="NPI")
    return SourceDecision(
        Source.CMS_REVOCATION, SourceApplicability.REQUIRED,
        ("an NPI is present and the entity could hold Medicare billing "
         "privileges, so revocation status is a fact worth establishing"),
        lookup_key=profile.npi_available, lookup_kind="NPI")


def build_matrix(
    entity: Dict[str, Any],
    *,
    nppes_data: Optional[Dict[str, Any]] = None,
    pecos_found: Optional[bool] = None,
    entity_id: Optional[str] = None,
) -> SourceApplicabilityMatrix:
    """Decide, for one entity, which sources are worth asking and why.

    `nppes_data` is optional and changes the answer: taxonomy is what
    establishes Medicare relevance, and until NPPES has answered, relevance is
    UNDETERMINED rather than assumed. That is why a population run asks NPPES
    first and re-evaluates the PPEF decisions afterwards.
    """
    profile = build_profile(entity, nppes_data=nppes_data, pecos_found=pecos_found)
    decisions = [
        _nppes(profile, entity),
        _ppef_enrollment(profile),
        _ppef_dependent(Source.CMS_PPEF_PRACTICE_LOCATION, profile,
                        "the practice-location sub-file"),
        _ppef_dependent(Source.CMS_PPEF_REASSIGNMENT, profile,
                        "the reassignment sub-file"),
        _oig_leie(profile, entity),
        _sam_gov(profile, entity),
        _cms_revocation(profile),
    ]
    return SourceApplicabilityMatrix(
        entity_id=entity_id or entity.get("id") or entity.get("tefcaid"),
        profile=profile,
        decisions={d.source.value: d for d in decisions},
    )


def population_summary(matrices: List[SourceApplicabilityMatrix]) -> Dict[str, Any]:
    """Counts per source and applicability, for the population metrics.

    Deliberately reports every applicability value separately. Rolling
    NOT_APPLICABLE into a "not checked" bucket alongside failures would hide the
    distinction this module exists to preserve.
    """
    summary: Dict[str, Dict[str, int]] = {}
    for source in Source:
        summary[source.value] = {a.value: 0 for a in SourceApplicability}
    for matrix in matrices:
        for key, decision in matrix.decisions.items():
            summary[key][decision.applicability.value] += 1
    return {
        "version": SOURCE_APPLICABILITY_VERSION,
        "entities": len(matrices),
        "by_source": summary,
        "queryable_calls": sum(len(m.queryable) for m in matrices),
        "blocked_by_methodology": sum(len(m.blocked) for m in matrices),
    }
