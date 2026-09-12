"""Rule / policy versioning — WHAT RULE APPLIED ON THE REVIEW DATE?

Governing material changes over time (RCE SOPs are re-versioned several times a
year). A review performed on 2026-07-15 was subject to the Vetting SOP v1.0; the
same review on 2026-08-15 is subject to v2.0. This module records rule versions
with their authority layer, status and effective window and answers which
version(s) applied on a date. It holds NO rule logic — it is a register, not an
engine — and it never promotes a draft or a proposal into an effective rule.

AUTHORITY LAYERS (kept separate; never collapsed)
    1 EXECUTED_CONTRACT           executed contract / SOW / modifications
    2 COR_ACCEPTED_METHODOLOGY    COR-accepted Task 2 methodology
    3 WRITTEN_COR_DIRECTION       written COR / ONC direction
    4 RCE_GOVERNING_MATERIAL      current applicable Common Agreement / QTF / SOPs
    5 FEDERAL_REFERENCE_DATA      federal reference / program data (NPPES, CMS)
    6 INDUSTRY_COMMERCIAL         industry / commercial evidence
    7 DOCUACTION_PROPOSAL         proposed DocuAction innovation

A rule at layer 4–7 is never a contract requirement. `is_contract_requirement`
is True only for layers 1–3.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


class RuleStatus(str, Enum):
    DRAFT = "DRAFT"
    UNDER_CONSIDERATION = "UNDER_CONSIDERATION"
    APPROVED_FUTURE = "APPROVED_FUTURE"      # approved, effective date not yet reached
    EFFECTIVE = "EFFECTIVE"
    SUPERSEDED = "SUPERSEDED"
    GUIDANCE = "GUIDANCE"                    # FAQ / explanatory; never binding
    WITHDRAWN = "WITHDRAWN"


class AuthorityLayer(int, Enum):
    EXECUTED_CONTRACT = 1
    COR_ACCEPTED_METHODOLOGY = 2
    WRITTEN_COR_DIRECTION = 3
    RCE_GOVERNING_MATERIAL = 4
    FEDERAL_REFERENCE_DATA = 5
    INDUSTRY_COMMERCIAL = 6
    DOCUACTION_PROPOSAL = 7


BINDING_STATUSES = frozenset({RuleStatus.EFFECTIVE, RuleStatus.SUPERSEDED})


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    program: str                 # e.g. "TEFCA", "MEDICARE", "ARC_CONTRACT"
    topic: str                   # e.g. "T-TRTMNT vetting data points"
    description: str


@dataclass(frozen=True)
class RuleVersion:
    rule_id: str
    version: str                                  # the source document's version
    status: RuleStatus
    authority_layer: AuthorityLayer
    source_document: str
    section: Optional[str] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None           # exclusive; None = open
    approved_on: Optional[date] = None
    citation_url: Optional[str] = None             # document citation; full URLs live in the markdown register
    evidence_requirement: Optional[str] = None    # what evidence the rule asks for, verbatim where possible
    applicability: Optional[str] = None           # who it binds (e.g. "QHINs; Entrants for T-TRTMNT")
    supersedes: Optional[str] = None
    last_verified: Optional[date] = None
    note: Optional[str] = None

    @property
    def is_contract_requirement(self) -> bool:
        return self.authority_layer in (AuthorityLayer.EXECUTED_CONTRACT, AuthorityLayer.COR_ACCEPTED_METHODOLOGY,
                                        AuthorityLayer.WRITTEN_COR_DIRECTION)

    def status_on(self, on: date) -> RuleStatus:
        """The status as it stood on a date. A future-effective approval is
        APPROVED_FUTURE before its date and EFFECTIVE from it; an effective
        window that has closed is SUPERSEDED. Drafts and proposals never
        become anything by the passage of time."""
        if self.status in (RuleStatus.DRAFT, RuleStatus.UNDER_CONSIDERATION, RuleStatus.GUIDANCE, RuleStatus.WITHDRAWN):
            return self.status
        if self.effective_from is None:
            return self.status
        if on < self.effective_from:
            return RuleStatus.APPROVED_FUTURE
        if self.effective_to is not None and on >= self.effective_to:
            return RuleStatus.SUPERSEDED
        return RuleStatus.EFFECTIVE

    def applies_on(self, on: date) -> bool:
        return self.status_on(on) is RuleStatus.EFFECTIVE

    def to_dict(self) -> Dict[str, Any]:
        return {"rule_id": self.rule_id, "version": self.version, "status": self.status.value,
                "authority_layer": self.authority_layer.value, "authority_layer_name": self.authority_layer.name,
                "is_contract_requirement": self.is_contract_requirement,
                "source_document": self.source_document, "section": self.section,
                "effective_from": self.effective_from.isoformat() if self.effective_from else None,
                "effective_to": self.effective_to.isoformat() if self.effective_to else None,
                "approved_on": self.approved_on.isoformat() if self.approved_on else None,
                "citation_url": self.citation_url, "evidence_requirement": self.evidence_requirement,
                "applicability": self.applicability, "supersedes": self.supersedes,
                "last_verified": self.last_verified.isoformat() if self.last_verified else None, "note": self.note}


@dataclass
class PolicyRegister:
    definitions: Dict[str, RuleDefinition] = field(default_factory=dict)
    versions: List[RuleVersion] = field(default_factory=list)

    def add(self, definition: RuleDefinition, *versions: RuleVersion) -> None:
        self.definitions[definition.rule_id] = definition
        for v in versions:
            if v.rule_id != definition.rule_id:
                raise ValueError(f"version {v.version} belongs to {v.rule_id}, not {definition.rule_id}")
            self.versions.append(v)

    def applicable(self, rule_id: str, on: date) -> List[RuleVersion]:
        """Every version of a rule that was EFFECTIVE on the date. Usually one;
        zero when no version was yet effective; more than one signals an
        overlapping register that a human must fix (never silently resolved)."""
        return [v for v in self.versions if v.rule_id == rule_id and v.applies_on(on)]

    def status_report(self, on: date) -> List[Dict[str, Any]]:
        return [{**v.to_dict(), "status_on_date": v.status_on(on).value} for v in self.versions]

    def proposals(self) -> List[RuleVersion]:
        """Everything that is NOT binding: drafts, items under consideration,
        guidance. Listed so a product can align to them without ever treating
        them as requirements."""
        return [v for v in self.versions if v.status not in BINDING_STATUSES and v.status is not RuleStatus.APPROVED_FUTURE]


def overlapping(register: PolicyRegister, on: date) -> Dict[str, List[str]]:
    """Rule ids with more than one effective version on the date — an
    integrity check for the register itself."""
    out: Dict[str, List[str]] = {}
    for rule_id in register.definitions:
        hits = register.applicable(rule_id, on)
        if len(hits) > 1:
            out[rule_id] = [v.version for v in hits]
    return out
