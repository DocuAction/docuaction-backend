"""NPI applicability — the six decision cases.

Source of the cases: TEFCA ARC Task 2 Methodology v1.4 (revision of 2026-10-25), section
"NPI applicability decision cases (proposed)", which supports Meley's business clarification
BR-NPI-TRTMNT-001 ("Under purposeofuse, T-TRTMNT requires an NPI", relayed 2026-10-25).
Official basis for the requirement and its date: Treatment XP SOP v2.0 (RCE, 7.1.2026;
SOP effective 2026-08-03), §7 "RCE Directory Service Requirements for Treatment XP Codes"
and §8 "Compliance Dates" (§7 compliance date 2026-09-03, applicability QHINs).

PURE. This module reads two delivered values (the `purposesofuse` list and the `NPI`), plus
dates and provenance the caller already holds, and returns data. It never writes, never
looks anything up, never decides a bucket (B1–B4), never holds a record (HELD), never
corrects a value and never approves anything. Those are separate controls, deliberately.

Field mapping (requirement: the actual source header is `purposesofuse`):

    source header            `purposesofuse`              (ONC/RCE delivery, column 5 of 41)
    quality-rule context     RecordContext.values["purposesofuse"]   (same name, verbatim)
    curated record           rce_curated_records.exchange_purposes   (JSON list of tokens)
    registry entity          tefca_reg_entities.exchange_purposes    ({"purposes": [...]})
    raw preservation         rce_attributes.purposesofuse_raw        (the cell, untouched)

The raw cell is never altered: `split_purpose_tokens` (field_map) only READS it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, Tuple

from app.tefca_registry.rce.field_map import PURPOSE_VOCABULARY, split_purpose_tokens

# ── provenance constants ─────────────────────────────────────────────────────

SOURCE_HEADER = "purposesofuse"
FIELD_MAPPING = {
    "source_header": SOURCE_HEADER,
    "record_context_key": "purposesofuse",
    "curated_column": "rce_curated_records.exchange_purposes",
    "entity_column": "tefca_reg_entities.exchange_purposes",
    "raw_preserved_in": "rce_attributes.purposesofuse_raw",
}

TOKEN_TRTMNT = "T-TRTMNT"
TOKEN_TREAT = "T-TREAT"
#: The recognized purpose-of-use vocabulary (PUR-001's). Anything else is UNRECOGNIZED and
#: never silently becomes a recognized purpose.
RECOGNIZED_PURPOSE_TOKENS = frozenset(PURPOSE_VOCABULARY)

SOP_REFERENCE = "Treatment XP SOP v2.0 (RCE, 7.1.2026), §7 and §8"
SOP_EFFECTIVE_DATE = date(2026, 8, 3)
#: §8 compliance date for §7 "NPI populated in the RCE Directory Service according to the
#: requirements" (applicability: QHINs).
SECTION_7_COMPLIANCE_DATE = date(2026, 9, 3)
#: Software rule-version effective date for the six-case assignment (NPI-001 1.4.0). An
#: implementation choice so already-issued runs are never re-interpreted; it is NOT the SOP
#: date and NOT Meley's policy date.
CASES_RULE_VERSION = "NPI-001 1.4.0"
CASES_SOFTWARE_EFFECTIVE_DATE = date(2026, 10, 26)

# ── vocabularies (strings, so they survive JSON and reports unchanged) ───────

INITIATING, RESPONDING, DIRECTION_UNKNOWN = "initiating", "responding", "unknown"
NPI_SUPPLIED, NPI_MISSING = "supplied", "missing"
APPLIES, DOES_NOT_APPLY, UNRESOLVED = "applies", "does_not_apply", "unresolved"
PRE_DEADLINE, ON_OR_AFTER_DEADLINE = "pre_deadline", "on_or_after_deadline"
EXCEPTION_ESTABLISHED, EXCEPTION_NOT_ESTABLISHED, EXCEPTION_UNKNOWN = (
    "established", "not_established", "unknown")

CASE_LABELS = {
    1: "T-TRTMNT included; NPI missing",
    2: "T-TRTMNT included; NPI supplied",
    3: "T-TREAT included without T-TRTMNT; NPI missing",
    4: "Other recognized purposes only; NPI missing",
    5: "Purpose missing or unrecognized; NPI missing",
    6: "T-TRTMNT absent; NPI supplied",
}
CASE_HANDLING = {
    1: ("Record the missing-NPI observation under BR-NPI-TRTMNT-001. Evaluate initiating-node "
        "applicability and the governing date separately before any compliance determination."),
    2: ("Validate the identifier (NPI-002/004/003) and verify its relationship to the entity "
        "(NPPES and later checks). Supplied does not mean verified."),
    3: ("Assess initiating-node applicability and the documented §7(a)(v) exception (entity has "
        "neither NPI nor CLIA Number and initiates T-TREAT). Do not automatically pass or fail."),
    4: ("No missing-NPI finding under this Treatment rule. Other applicable checks are preserved."),
    5: ("Applicability unresolved; review required. The NPI is not assumed optional."),
    6: ("Validate the identifier and perform the applicable checks, including when the purpose is "
        "missing or unrecognized. Supplied does not mean verified."),
}


@dataclass(frozen=True)
class CaseAssignment:
    """Which of the six cases a record falls in, with the raw inputs preserved."""

    case: int
    label: str
    handling: str
    raw_purposes: str
    raw_npi: str
    tokens: Tuple[str, ...]
    recognized_tokens: Tuple[str, ...]
    unrecognized_tokens: Tuple[str, ...]
    has_trtmnt: bool
    has_treat: bool
    npi_supplied: bool

    @property
    def missing_npi_observation(self) -> bool:
        """Case 1 only: the observation Meley's instruction asks for."""
        return self.case == 1

    @property
    def review_required(self) -> bool:
        """Cases 3 and 5: applicability cannot be decided from the delivered fields."""
        return self.case in (3, 5)


def assign_case(purposes_raw: Optional[str], npi_raw: Optional[str]) -> CaseAssignment:
    """Assign exactly one of the six mutually exclusive cases.

    Token matching is EXACT membership on the parsed list (separators `, ; |` and whitespace;
    surrounding whitespace removed; nothing else normalised): `T-TRTMNT` anywhere in the list
    takes precedence over every other token; `T-TRTMNTX`, `XT-TRTMNT`, `t-trtmnt`, `TRTMNT`
    never match. A list that contains any unrecognized code is "unrecognized" for case 5
    unless T-TRTMNT is present (precedence) or an NPI is supplied (case 6).
    """
    raw_p = purposes_raw or ""
    raw_n = npi_raw or ""
    tokens = tuple(split_purpose_tokens(raw_p))
    recognized = tuple(t for t in tokens if t in RECOGNIZED_PURPOSE_TOKENS)
    unrecognized = tuple(t for t in tokens if t not in RECOGNIZED_PURPOSE_TOKENS)
    npi_supplied = bool(raw_n.strip())
    has_trtmnt = TOKEN_TRTMNT in tokens
    has_treat = TOKEN_TREAT in tokens

    if has_trtmnt:
        case = 1 if not npi_supplied else 2
    elif npi_supplied:
        case = 6
    elif has_treat:
        case = 3
    elif recognized and not unrecognized:
        case = 4
    else:
        case = 5   # no purposes at all, or at least one unrecognized code
    return CaseAssignment(
        case=case, label=CASE_LABELS[case], handling=CASE_HANDLING[case],
        raw_purposes=raw_p, raw_npi=raw_n, tokens=tokens,
        recognized_tokens=recognized, unrecognized_tokens=unrecognized,
        has_trtmnt=has_trtmnt, has_treat=has_treat, npi_supplied=npi_supplied)


# ── applicability (separate from the observation) ────────────────────────────

def exchange_direction_from_record(org_node_type: Optional[str]) -> str:
    """Provenance of the delivered purposes. The only delivered field that states a node's
    role in exchange is `organizationNodeType` (observed value `initiating-node` on 2 of
    23,566 profiled records). Nothing else is inferred: a blank `hl7orgrole`, a blank
    `initiatoronly`, a Participant/Subparticipant label or a blank node type all leave the
    direction UNKNOWN. Whether `purposesofuse` enumerates Organization-level initiating
    purposes or Endpoint-level responding purposes is not documented in any delivered
    specification on file; unknown stays unknown."""
    value = (org_node_type or "").strip().lower()
    if value == "initiating-node":
        return INITIATING
    return DIRECTION_UNKNOWN


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    return date.fromisoformat(text[:10]) if text else None


@dataclass(frozen=True)
class ApplicabilityAssessment:
    """Requirement applicability and deadline applicability for ONE record, kept apart from
    the observation (`CaseAssignment`), from NPI validity, from verification outcome, from
    severity, from HELD and from B1–B4. There is deliberately no field for any of those."""

    case: int
    npi_presence: str                       # supplied | missing — a fact about the record, NOT applicability
    exchange_direction: str                 # initiating | responding | unknown
    initiating_requirement: str             # applies | does_not_apply | unresolved
    treat_exception: str                    # established | not_established | unknown
    snapshot_date: Optional[date]           # population date of the delivery (None = missing)
    assessment_date: date                   # when this assessment ran (recorded separately)
    governing_requirement_date: date        # SOP §8 compliance date for §7
    software_effective_date: date           # when this rule version began to apply
    deadline_applicability: str             # pre_deadline | on_or_after_deadline | unresolved
    rule_version: str
    sop_reference: str
    notes: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def compliance_determination(self) -> None:
        """Always None. A compliance determination is an analyst's act with independent QA;
        this object never carries one."""
        return None


def assess_applicability(assignment: CaseAssignment, *,
                         snapshot_date, assessment_date,
                         exchange_direction: str = DIRECTION_UNKNOWN,
                         treat_exception: str = EXCEPTION_UNKNOWN) -> ApplicabilityAssessment:
    """Decide what can be decided from the inputs and mark the rest UNRESOLVED.

    - §7 binds a Participant/Subparticipant operating a Principal Node that INITIATES Queries
      for either Treatment XP Code (applicability: QHINs populate the directory). Direction
      `responding` → does not apply; `initiating` → applies (case 3: unless the §7(a)(v)
      exception is established); `unknown` → unresolved. Never inferred from a blank role.
    - Whether an NPI is SUPPLIED is a separate fact (`npi_presence`). A supplied NPI satisfies
      presence; it never makes the requirement "inapplicable". So case 6 is decided by the
      purposes exactly as the missing-NPI cases are: T-TREAT present → by direction (applies /
      does not apply / unresolved); purposes missing or unrecognized → unresolved; other
      recognized purposes only → does not apply under this Treatment rule.
    - Deadline applicability is the SNAPSHOT date against 2026-09-03. A missing snapshot
      date is UNRESOLVED and is never replaced by the assessment date. A snapshot dated
      2026-09-02 is PRE_DEADLINE: it cannot establish a breach of §7; any requirement that
      applied earlier is a separate assessment and is not asserted here.
    """
    snap = _as_date(snapshot_date)
    assessed = _as_date(assessment_date) or date.today()
    notes = []

    if assignment.case in (1, 2):
        if exchange_direction == INITIATING:
            requirement = APPLIES
        elif exchange_direction == RESPONDING:
            requirement = DOES_NOT_APPLY
            notes.append("§7 binds initiating Principal Nodes; a responding-only node is outside it.")
        else:
            requirement = UNRESOLVED
            notes.append("Exchange direction of the delivered purposes is unknown (Organization-level "
                         "initiation vs Endpoint-level response is undocumented); applicability stays unresolved.")
    elif assignment.case == 3:
        if treat_exception == EXCEPTION_ESTABLISHED:
            requirement = DOES_NOT_APPLY
            notes.append("§7(a)(v): no NPI and no CLIA Number, initiating T-TREAT only — not required to populate.")
        elif exchange_direction == RESPONDING:
            requirement = DOES_NOT_APPLY
        elif exchange_direction == INITIATING and treat_exception == EXCEPTION_NOT_ESTABLISHED:
            requirement = APPLIES
        else:
            requirement = UNRESOLVED
            notes.append("T-TREAT initiator exception (no NPI and no CLIA) not established from delivered fields; review.")
    elif assignment.case == 4:
        requirement = DOES_NOT_APPLY
        notes.append("No Treatment purpose asserted; no missing-NPI finding under this rule. Other checks unaffected.")
    elif assignment.case == 5:
        requirement = UNRESOLVED
        notes.append("Purpose missing or unrecognized: the NPI is not assumed optional; analyst review required.")
    else:  # case 6 — NPI supplied, T-TRTMNT absent: presence satisfied, applicability by the purposes
        if assignment.has_treat:
            if exchange_direction == INITIATING:
                requirement = APPLIES
                notes.append("T-TREAT initiating node: §7 applies; the supplied NPI satisfies presence (validity and "
                             "verification are separate checks).")
            elif exchange_direction == RESPONDING:
                requirement = DOES_NOT_APPLY
                notes.append("§7 binds initiating Principal Nodes; a responding-only node is outside it.")
            else:
                requirement = UNRESOLVED
                notes.append("T-TREAT with an NPI supplied: presence satisfied, but exchange direction is unknown, so "
                             "whether §7 applies stays unresolved.")
        elif not assignment.tokens or assignment.unrecognized_tokens:
            requirement = UNRESOLVED
            notes.append("Purpose missing or unrecognized with an NPI supplied: presence satisfied; applicability of the "
                         "Treatment requirement cannot be decided from the delivered purposes.")
        else:
            requirement = DOES_NOT_APPLY
            notes.append("No Treatment purpose asserted; NPI supplied: validity and verification checks apply; this "
                         "Treatment rule does not.")

    if snap is None:
        deadline = UNRESOLVED
        notes.append("Snapshot date missing: deadline applicability unresolved (the assessment date is NOT a substitute).")
    elif snap < SECTION_7_COMPLIANCE_DATE:
        deadline = PRE_DEADLINE
        notes.append(f"Snapshot {snap.isoformat()} pre-dates the §7 compliance date "
                     f"{SECTION_7_COMPLIANCE_DATE.isoformat()}: this data cannot establish a §7 breach. "
                     f"Requirements that applied earlier are assessed separately.")
    else:
        deadline = ON_OR_AFTER_DEADLINE

    return ApplicabilityAssessment(
        case=assignment.case,
        npi_presence=NPI_SUPPLIED if assignment.npi_supplied else NPI_MISSING,
        exchange_direction=exchange_direction,
        initiating_requirement=requirement, treat_exception=treat_exception,
        snapshot_date=snap, assessment_date=assessed,
        governing_requirement_date=SECTION_7_COMPLIANCE_DATE,
        software_effective_date=CASES_SOFTWARE_EFFECTIVE_DATE,
        deadline_applicability=deadline, rule_version=CASES_RULE_VERSION,
        sop_reference=SOP_REFERENCE, notes=tuple(notes))


def describe(assignment: CaseAssignment, assessment: ApplicabilityAssessment) -> str:
    """One line for a finding description or an audit note — facts, no verdict."""
    snap = assessment.snapshot_date.isoformat() if assessment.snapshot_date else "missing"
    return (f"case {assignment.case} ({assignment.label}); purposes={assignment.raw_purposes!r}; "
            f"NPI={assessment.npi_presence}; "
            f"direction={assessment.exchange_direction}; §7 requirement={assessment.initiating_requirement}; "
            f"T-TREAT exception={assessment.treat_exception}; snapshot={snap}; "
            f"assessed={assessment.assessment_date.isoformat()}; "
            f"governing date={assessment.governing_requirement_date.isoformat()}; "
            f"deadline={assessment.deadline_applicability}; rule={assessment.rule_version}")
