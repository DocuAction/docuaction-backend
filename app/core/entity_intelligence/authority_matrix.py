"""SOURCE_QUESTION_AUTHORITY_MATRIX — which source can answer which question.

Authority is question-specific and descriptive. There is no voting, no
majority, no weight, no confidence score. A source either CAN_SUPPORT a
question (its statement is admissible evidence for it), CANNOT_ALONE_ESTABLISH
it (its statement is context at most), or the question is NOT_APPLICABLE to
it. A pair that is not in the matrix is UNKNOWN, and UNKNOWN != permitted.

The matrix is versioned like a policy: matrix_version, effective_date,
supersedes_version, change_reason, approved_by. approved_by is PENDING until
a human approves it; the engine only reads it, and reads nothing numeric.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional, Tuple


class EvidenceQuestion(str, Enum):
    NPI_OBSERVATION = "NPI_OBSERVATION"
    ORGANIZATION_NAME = "ORGANIZATION_NAME"
    OTHER_NAME_DBA = "OTHER_NAME_DBA"
    PRACTICE_LOCATION = "PRACTICE_LOCATION"
    CORPORATE_LEGAL_NAME = "CORPORATE_LEGAL_NAME"
    CORPORATE_REGISTRATION = "CORPORATE_REGISTRATION"
    DOMESTIC_FOREIGN_ROLE = "DOMESTIC_FOREIGN_ROLE"
    REGISTERED_AGENT = "REGISTERED_AGENT"
    CORPORATE_STATUS = "CORPORATE_STATUS"
    MEDICARE_ENROLLMENT_OBSERVATION = "MEDICARE_ENROLLMENT_OBSERVATION"
    BENEFIT_REASSIGNMENT = "BENEFIT_REASSIGNMENT"
    ADDITIONAL_NPI = "ADDITIONAL_NPI"
    EXCLUSION_RECORD = "EXCLUSION_RECORD"
    LICENSURE = "LICENSURE"
    CREDENTIALING = "CREDENTIALING"
    CORPORATE_GOOD_STANDING = "CORPORATE_GOOD_STANDING"
    TEFCA_ELIGIBILITY = "TEFCA_ELIGIBILITY"
    CONTRACT_COMPLIANCE = "CONTRACT_COMPLIANCE"
    SITE_OF_CARE = "SITE_OF_CARE"


class AuthorityAnswer(str, Enum):
    CAN_SUPPORT = "CAN_SUPPORT"
    CANNOT_ALONE_ESTABLISH = "CANNOT_ALONE_ESTABLISH"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AuthorityEntry:
    source_id: str
    question: EvidenceQuestion
    answer: AuthorityAnswer
    note: str = ""


@dataclass
class SourceQuestionAuthorityMatrix:
    matrix_version: str
    effective_date: date
    supersedes_version: Optional[str]
    change_reason: str
    approved_by: str = "PENDING_HUMAN_APPROVAL"
    entries: List[AuthorityEntry] = field(default_factory=list)

    def answer(self, source_id: str, question: EvidenceQuestion) -> AuthorityAnswer:
        for e in self.entries:
            if e.source_id == source_id and e.question is question:
                return e.answer
        return AuthorityAnswer.UNKNOWN

    def can_support(self, source_id: str, question: EvidenceQuestion) -> bool:
        return self.answer(source_id, question) is AuthorityAnswer.CAN_SUPPORT

    def sources_for(self, question: EvidenceQuestion) -> List[str]:
        return sorted({e.source_id for e in self.entries if e.question is question and e.answer is AuthorityAnswer.CAN_SUPPORT})

    def as_rows(self) -> List[Dict[str, str]]:
        return [{"source_id": e.source_id, "question": e.question.value, "answer": e.answer.value, "note": e.note}
                for e in self.entries]


def _rows(source: str, can: Tuple[EvidenceQuestion, ...], cannot: Tuple[EvidenceQuestion, ...],
          na: Tuple[EvidenceQuestion, ...] = ()) -> List[AuthorityEntry]:
    out = [AuthorityEntry(source, q, AuthorityAnswer.CAN_SUPPORT) for q in can]
    out += [AuthorityEntry(source, q, AuthorityAnswer.CANNOT_ALONE_ESTABLISH) for q in cannot]
    out += [AuthorityEntry(source, q, AuthorityAnswer.NOT_APPLICABLE) for q in na]
    return out


Q = EvidenceQuestion
NEVER_ALONE = (Q.LICENSURE, Q.CREDENTIALING, Q.CORPORATE_GOOD_STANDING, Q.TEFCA_ELIGIBILITY, Q.CONTRACT_COMPLIANCE)

MATRIX_V1 = SourceQuestionAuthorityMatrix(
    matrix_version="1.0",
    effective_date=date(2026, 9, 13),
    supersedes_version=None,
    change_reason="Initial matrix for Architecture v1.0: NPPES V2, CMS PPEF five-file model, state corporate registries "
                  "(design), OIG LEIE (existing Task 3 use), RCE-provided IQVIA (awaiting).",
    approved_by="PENDING_HUMAN_APPROVAL",
    entries=(
        _rows("NPPES_V2",
              can=(Q.NPI_OBSERVATION, Q.ORGANIZATION_NAME, Q.OTHER_NAME_DBA, Q.PRACTICE_LOCATION),
              cannot=NEVER_ALONE + (Q.SITE_OF_CARE, Q.MEDICARE_ENROLLMENT_OBSERVATION, Q.CORPORATE_REGISTRATION),
              na=(Q.EXCLUSION_RECORD, Q.REGISTERED_AGENT, Q.DOMESTIC_FOREIGN_ROLE, Q.CORPORATE_STATUS))
        + _rows("CMS_PPEF",
                can=(Q.MEDICARE_ENROLLMENT_OBSERVATION, Q.ORGANIZATION_NAME, Q.BENEFIT_REASSIGNMENT, Q.ADDITIONAL_NPI,
                     Q.PRACTICE_LOCATION),
                cannot=NEVER_ALONE + (Q.SITE_OF_CARE, Q.OTHER_NAME_DBA),
                na=(Q.EXCLUSION_RECORD, Q.REGISTERED_AGENT, Q.DOMESTIC_FOREIGN_ROLE, Q.CORPORATE_STATUS, Q.CORPORATE_REGISTRATION))
        + _rows("STATE_CORPORATE_REGISTRY",
                can=(Q.CORPORATE_LEGAL_NAME, Q.CORPORATE_REGISTRATION, Q.DOMESTIC_FOREIGN_ROLE, Q.REGISTERED_AGENT,
                     Q.CORPORATE_STATUS),
                cannot=(Q.LICENSURE, Q.CREDENTIALING, Q.PRACTICE_LOCATION, Q.SITE_OF_CARE,
                        Q.MEDICARE_ENROLLMENT_OBSERVATION, Q.TEFCA_ELIGIBILITY, Q.CONTRACT_COMPLIANCE, Q.CORPORATE_GOOD_STANDING),
                na=(Q.NPI_OBSERVATION, Q.EXCLUSION_RECORD))
        + _rows("OIG_LEIE", can=(Q.EXCLUSION_RECORD,), cannot=NEVER_ALONE,
                na=(Q.NPI_OBSERVATION, Q.PRACTICE_LOCATION, Q.CORPORATE_REGISTRATION))
        + _rows("IQVIA_ONEKEY_RCE_DELIVERY", can=(), cannot=tuple(Q),
                na=())   # nothing is admissible until terms, schema and a human decision exist
    ),
)

#: Questions that no source in the matrix may establish alone — kept explicit so
#: a future source cannot be added with CAN_SUPPORT for them by accident.
NO_SINGLE_SOURCE_QUESTIONS = frozenset(NEVER_ALONE)
