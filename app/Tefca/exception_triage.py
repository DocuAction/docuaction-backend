"""Which Phase-6 observations are worth a human's time, and which are not.

164,962 observations is not a work queue. Most of them are a source saying
"nothing here", which is a fact worth recording and no reason to interrupt an
analyst. This module sorts the population into five dispositions using ONLY
conditions that are already decided somewhere else — an observation state, a
severity the rules engine already assigned, or a documented cardinality. It
decides nothing about D1-D9.

WHY `METHODOLOGY_PENDING` IS NOT A POLITE WAY OF SAYING `IGNORE`
    Some conditions cannot be triaged without a methodology answer nobody has
    given yet. A ZIP that disagrees with NPPES is a fact; whether a
    *disagreement* is material enough to review is a threshold the approved
    methodology has to set, and inventing one here would quietly create a
    review requirement — or quietly suppress one. Those conditions are named
    and counted, with the decision they are waiting on attached, so the gap is
    visible rather than resolved by default.

WHAT THIS MODULE MUST NEVER DO
    Create a determination. Triage produces a WORK ITEM, never an answer. The
    analyst and QA layers (`app.tefca_registry.qa_gate`) own every human act,
    and the reportability gate stays theirs.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

#: Bump when a triage rule changes, so a queue item can be traced to the rules
#: that put it there rather than to whatever the file says today.
TRIAGE_VERSION = "1.0.0"


class Triage(str, Enum):
    """Five dispositions. Only the first produces analyst work."""

    #: A human must adjudicate this. Something adverse or ambiguous was observed.
    READY_FOR_ANALYST = "READY_FOR_ANALYST"
    #: Whether this needs review depends on a methodology decision not yet made.
    METHODOLOGY_PENDING = "METHODOLOGY_PENDING"
    #: Recorded, real, and expected. Normal cardinality is not an exception.
    INFORMATIONAL_ONLY = "INFORMATIONAL_ONLY"
    #: The limit is in OUR key or OUR access, not in the entity.
    SOURCE_LIMITATION = "SOURCE_LIMITATION"
    #: Same entity, same condition, already represented by another item.
    DUPLICATE_CONSOLIDATED = "DUPLICATE_CONSOLIDATED"


@dataclass(frozen=True)
class TriageDecision:
    """One observation's disposition, with the reason and the authority."""

    disposition: Triage
    reason: str
    #: The unresolved methodology decision, when that is what is blocking.
    blocked_by: Optional[str] = None
    #: Higher sorts first in the analyst queue. Never a severity judgement about
    #: the entity — only about how soon a human should look.
    priority: int = 50

    def to_dict(self) -> Dict[str, Any]:
        return {"disposition": self.disposition.value, "reason": self.reason,
                "blocked_by": self.blocked_by, "priority": self.priority,
                "triage_version": TRIAGE_VERSION}


#: Sources whose positive match is an adverse finding about the entity. A hit
#: here is the one case where the system is certain a human must look.
_ADVERSE_SOURCES = frozenset({"OIG_LEIE", "CMS_REVOCATION"})

#: Sources that establish identity. An identity source that cannot resolve a
#: delivered NPI is an anomaly in the delivery, not a missing nicety.
_IDENTITY_SOURCES = frozenset({"NPPES"})


def triage(observation: Dict[str, Any]) -> TriageDecision:
    """Sort one observation. Pure; no I/O, no database, no side effects.

    `observation` needs `source`, `observation_result` and
    `dimension_applicability` — the three fields Phase 6 always writes.
    """
    source = (observation.get("source") or "").strip()
    state = (observation.get("observation_result") or "").strip()
    applicability = (observation.get("dimension_applicability") or "").strip()

    # 1. Applicability that nobody has settled. Checked FIRST: an unresolved
    #    applicability makes every downstream question premature, including
    #    whether the observation is an exception at all.
    if applicability == "UNKNOWN_PENDING_METHODOLOGY":
        return TriageDecision(
            Triage.METHODOLOGY_PENDING,
            f"{source} applicability is undecided, so whether this observation "
            f"requires review is also undecided.",
            blocked_by="D4", priority=0)

    # 2. A source that says the entity IS excluded or revoked. The strongest
    #    signal in the population, and the only automatic analyst assignment.
    if source in _ADVERSE_SOURCES and state == "MATCH_OBSERVED":
        return TriageDecision(
            Triage.READY_FOR_ANALYST,
            f"{source} returned a positive match. An adverse finding from an "
            f"authoritative source requires human adjudication before it is "
            f"anything at all.",
            priority=100)

    # 3. Matched on supporting evidence only. The system has deliberately
    #    refused to call this a match; that refusal is what a human resolves.
    if state == "AMBIGUOUS":
        return TriageDecision(
            Triage.READY_FOR_ANALYST,
            f"{source} matched on supporting evidence with no decisive "
            f"identifier. Only a human can settle it, which is why the lookup "
            f"stopped here.",
            priority=90)

    # 4. Identity source could not resolve, or resolved to more than one record.
    if source in _IDENTITY_SOURCES and state in ("NO_MATCH_OBSERVED", "MULTIPLE_MATCHES"):
        return TriageDecision(
            Triage.READY_FOR_ANALYST,
            f"NPPES is the identity authority for a delivered NPI and returned "
            f"{state}. An NPI in the delivery that NPPES cannot resolve to one "
            f"record is an anomaly in the delivery.",
            priority=80)

    # 5. We could not form the key. A fact about the record we hold, and about
    #    our own ability to ask — never about the entity's compliance.
    if state == "INSUFFICIENT_IDENTIFIER":
        return TriageDecision(
            Triage.SOURCE_LIMITATION,
            "The delivered record carried no well-formed identifier for this "
            "lookup, so the source was never asked. Nothing about the entity "
            "follows from it.",
            priority=20)

    # 6. The source did not answer. Also not about the entity.
    if state == "SOURCE_UNAVAILABLE":
        return TriageDecision(
            Triage.SOURCE_LIMITATION,
            f"{source} did not answer. An outage or a missing credential is "
            f"news about our access, not evidence about the entity.",
            priority=10)

    # 7. Normal PPEF cardinality. `RceIssue`/PPEF documentation both state that
    #    a provider may legitimately hold several enrolments, so a count above
    #    one is the expected shape of the data, not a defect in it.
    if state == "MULTIPLE_MATCHES":
        return TriageDecision(
            Triage.INFORMATIONAL_ONLY,
            f"{source} returned more than one record. PPEF is one-to-many by "
            f"design — a provider may hold several enrolments — so cardinality "
            f"above one is expected and is recorded rather than queued.",
            priority=15)

    # 8. Everything else: a source answered, and said what it said.
    if state in ("MATCH_OBSERVED", "NO_MATCH_OBSERVED", "LOOKUP_NOT_APPLICABLE"):
        return TriageDecision(
            Triage.INFORMATIONAL_ONLY,
            f"{source} answered {state}. Recorded as evidence; no condition "
            f"requiring adjudication.",
            priority=5)

    if state == "ERROR":
        return TriageDecision(
            Triage.SOURCE_LIMITATION,
            "The lookup failed in OUR code. A defect to fix, not an entity "
            "finding and not an outage.",
            priority=30)

    return TriageDecision(
        Triage.METHODOLOGY_PENDING,
        f"Observation state {state!r} from {source} has no triage rule. "
        f"Recorded as undecided rather than assumed harmless.",
        blocked_by="UNMAPPED_STATE", priority=0)


def consolidate(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse repeats of the SAME condition on the SAME entity.

    One entity with an OIG hit and a CMS revocation hit has two distinct things
    to adjudicate and keeps both items. The same entity observed twice against
    one source for one condition is one piece of work, and queueing it twice
    would double-count the exception population.
    """
    seen: Dict[tuple, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for d in decisions:
        if d["disposition"] != Triage.READY_FOR_ANALYST.value:
            out.append(d)
            continue
        key = (d.get("entity_id"), d.get("source"), d.get("observation_result"))
        if key in seen:
            seen[key].setdefault("consolidated_observation_ids", []).append(
                d.get("observation_id"))
            dup = dict(d)
            dup["disposition"] = Triage.DUPLICATE_CONSOLIDATED.value
            dup["reason"] = (
                "Same entity, same source, same condition as an item already "
                "queued. Consolidated so the exception population is not "
                "double-counted.")
            out.append(dup)
            continue
        seen[key] = d
        out.append(d)
    return out
