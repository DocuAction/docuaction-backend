"""25K-scale evidence planning: source records are not review cases.

    SOURCE RECORDS → NORMALIZE → ENTITY RESOLUTION → DEDUPLICATE → CANONICAL
    CANDIDATES → APPLICABLE EVIDENCE QUESTIONS → DEDUPLICATED EVIDENCE PLAN
    → (AUTHORIZED ACQUISITION → EVIDENCE → ASSESSMENT → HUMAN WORK ONLY WHERE
    LEGITIMATELY REQUIRED)

This module covers the planning stages: it resolves records to canonical
candidates by an exact identifier key (NPI) or, failing that, an exact
normalized name + ZIP5 key — never by similarity — and derives the
deduplicated set of evidence questions per source. It performs no
acquisition and asks no source anything.

Every structure is bounded by the number of distinct keys; every record is
normalized exactly once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .normalize import normalize_address, normalize_name


@dataclass(frozen=True)
class SourceRecord:
    record_id: str
    npi: Optional[str] = None
    name: Optional[str] = None
    address: Optional[Dict[str, Any]] = None
    taxonomy_medicare_relevant: Optional[bool] = None   # None = unknown → CMS question is CONDITIONALLY applicable


@dataclass
class CanonicalCandidate:
    canonical_key: str
    key_basis: str                      # "NPI" | "NAME_ZIP5" | "RECORD_ONLY"
    record_ids: List[str] = field(default_factory=list)
    npi: Optional[str] = None
    normalized_name: Optional[str] = None
    zip5: Optional[str] = None
    questions: List[str] = field(default_factory=list)


@dataclass
class EvidencePlan:
    source_record_count: int
    canonical_candidate_count: int
    duplicate_record_count: int
    lookups_by_source: Dict[str, int]
    candidates: List[CanonicalCandidate]
    normalizations_performed: int

    def to_dict(self) -> Dict[str, Any]:
        return {"source_record_count": self.source_record_count,
                "canonical_candidate_count": self.canonical_candidate_count,
                "duplicate_record_count": self.duplicate_record_count,
                "lookups_by_source": dict(self.lookups_by_source),
                "normalizations_performed": self.normalizations_performed,
                "note": "25K source records != 25K review cases: human work is planned only after evidence, "
                        "and only where the assessment leaves a question."}


def _questions_for(c: CanonicalCandidate, record: SourceRecord) -> List[str]:
    qs: List[str] = []
    if c.npi:
        qs += ["NPPES_V2:NPI_OBSERVATION", "NPPES_V2:ORGANIZATION_NAME", "NPPES_V2:PRACTICE_LOCATION"]
        if record.taxonomy_medicare_relevant is not False:
            qs.append("CMS_PPEF:MEDICARE_ENROLLMENT_OBSERVATION")
    elif c.normalized_name:
        qs.append("NPPES_V2:ORGANIZATION_NAME")        # name-keyed lookup; NPI absent → identifier not available
    return qs


def plan_evidence(records: Iterable[SourceRecord]) -> EvidencePlan:
    by_key: Dict[str, CanonicalCandidate] = {}
    total = 0
    normalizations = 0
    for r in records:
        total += 1
        norm_name = normalize_name(r.name) if r.name else None
        zip5 = normalize_address(r.address)["zip5"] if r.address else None
        normalizations += 1
        if r.npi and r.npi.strip():
            key, basis = f"NPI:{r.npi.strip()}", "NPI"
        elif norm_name and zip5:
            key, basis = f"NAME_ZIP5:{norm_name}|{zip5}", "NAME_ZIP5"
        else:
            key, basis = f"RECORD:{r.record_id}", "RECORD_ONLY"
        c = by_key.get(key)
        if c is None:
            c = CanonicalCandidate(canonical_key=key, key_basis=basis, npi=r.npi.strip() if r.npi else None,
                                   normalized_name=norm_name, zip5=zip5)
            c.questions = _questions_for(c, r)
            by_key[key] = c
        c.record_ids.append(r.record_id)
    lookups: Dict[str, int] = {}
    for c in by_key.values():
        for q in c.questions:
            src = q.split(":", 1)[0]
            lookups[src] = lookups.get(src, 0) + 1
    return EvidencePlan(source_record_count=total, canonical_candidate_count=len(by_key),
                        duplicate_record_count=total - len(by_key), lookups_by_source=lookups,
                        candidates=list(by_key.values()), normalizations_performed=normalizations)
