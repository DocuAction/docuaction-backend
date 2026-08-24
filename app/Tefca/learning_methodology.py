"""
Module 6 — discrepancy categories and methodology, plus the D1-D9 status view.

WHY THIS IS A SEPARATE FILE
───────────────────────────
It is the module where getting the labelling wrong has contractual
consequences, and it is the one that has to stay in step with the COR decision
register. Keeping it beside the other lessons would make it easy to edit the
categories while thinking about prose.

THE RULE THIS MODULE ENFORCES ON ITSELF
───────────────────────────────────────
The four discrepancy categories are the Government's, quoted from the
solicitation. The mapping from evidence to category is AGT's, submitted under
D2. Every statement below carries which of those it is, and the category labels
are imported from `sow_report_data` rather than retyped — a lesson that spells a
contractual term differently from the report is worse than no lesson.

D1-D9 ARE NOT RESOLVED HERE
───────────────────────────
The status view reports them as they stand. Nothing in this file decides one,
and a test asserts none is presented as decided.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.core.learning import (Classification, KnowledgeCheck, Lesson, Module,
                               ProhibitedConclusion, Role, Statement)
from app.reports.data.sow_report_data import (GOVERNMENT_CATEGORIES,
                                              GOVERNMENT_CATEGORY_LABELS,
                                              GOVERNMENT_CATEGORY_NUMBER)

#: Cited on every statement that claims agency authority.
SOLICITATION = "RFQ 7571MN26Q00038 ¶136, ¶137, ¶142"
TAXONOMY_CLAUSE = "RFQ 7571MN26Q00038 ¶124"

# ── the categories, with what each side of the line contributes ──────────────

CATEGORY_GUIDANCE: Dict[str, Dict[str, Any]] = {
    "no_discrepancy": {
        "contractual_meaning": (
            "Every applicable check was performed and none produced a "
            "difference requiring review."),
        "evidence": (
            "All applicable observations returned a match, an equivalent value "
            "after normalisation, or a documented not-applicable."),
        "agt_implementation": (
            "Assigned only when no applicable source produced a conflict AND no "
            "applicable source went unanswered. An entity whose only clean "
            "result comes from sources that could not be reached is not in this "
            "category."),
        "analyst_must_verify": [
            "That every applicable source actually answered.",
            "That a not-applicable was recorded with its reason, not assumed.",
        ],
        "qa_must_verify": [
            "That no unanswered source was treated as a clean result.",
        ],
        "methodology_dependency": None,
    },
    "minor_administrative": {
        "contractual_meaning": (
            "A difference exists but is administrative in nature rather than "
            "indicating a substantive problem with the entity."),
        "evidence": (
            "A conflict on a descriptive field — most often address or name — "
            "that survives normalisation."),
        "agt_implementation": (
            "Proposed for differences that do not change who the entity is or "
            "whether it is eligible."),
        "analyst_must_verify": [
            "That the difference survives normalisation and is not formatting.",
            "That the identifier still resolves to the same organisation.",
        ],
        "qa_must_verify": [
            "That the rationale explains why the difference is administrative.",
        ],
        "methodology_dependency": "D4_ADDRESS_MATERIALITY",
    },
    "inexplicable": {
        "contractual_meaning": (
            "A difference for which no explanation can be established from the "
            "available evidence."),
        "evidence": (
            "Conflicting authoritative answers, or an identifier that resolves "
            "to something other than the delivered entity."),
        "agt_implementation": (
            "Proposed where sources disagree with each other and the "
            "disagreement cannot be resolved from what is on the record."),
        "analyst_must_verify": [
            "That the conflict is between sources, not between a source and a "
            "formatting artefact.",
            "That no further applicable source would settle it.",
        ],
        "qa_must_verify": [
            "That 'inexplicable' is not being used for 'not yet investigated'.",
        ],
        "methodology_dependency": "D5",
    },
    "non_compliant": {
        "contractual_meaning": (
            "A difference indicating the entity does not meet a requirement."),
        "evidence": (
            "An adverse finding from an authoritative source — exclusion, "
            "revocation or debarment — matched to this entity."),
        "agt_implementation": (
            "Never assigned automatically. The system can observe an adverse "
            "match; only a human determination followed by an independent QA "
            "approval can place an entity in this category."),
        "analyst_must_verify": [
            "That the adverse match is to this entity and not a name collision.",
            "That the source edition is current and cited.",
        ],
        "qa_must_verify": [
            "That the identifier match is decisive, not supporting-evidence only.",
            "That the analyst rationale would survive being read back by the COR.",
        ],
        "methodology_dependency": "D7",
    },
}


def category_guidance() -> List[Dict[str, Any]]:
    """The four categories, in the solicitation's order, fully described."""
    return [{
        "category": key,
        "number": GOVERNMENT_CATEGORY_NUMBER[key],
        "government_label": GOVERNMENT_CATEGORY_LABELS[key],
        "agt_shorthand": f"B{GOVERNMENT_CATEGORY_NUMBER[key]}",
        "label_authority": Classification.GOVERNMENT_REQUIREMENT.value,
        "mapping_authority": Classification.AGT_IMPLEMENTATION.value,
        **CATEGORY_GUIDANCE[key],
    } for key in GOVERNMENT_CATEGORIES]


# ── D1-D9 status, reported not resolved ──────────────────────────────────────

DECIDED = "DECIDED"
GUIDANCE_REQUESTED = "PROGRAM_GUIDANCE_REQUESTED"
NOT_APPLICABLE = "NOT_APPLICABLE"

#: Read from the COR Decision Register. Every entry is currently open; none is
#: marked DECIDED, because no written COR response exists for any of them and
#: inventing one would be fabricating a Government decision.
DECISIONS: List[Dict[str, Any]] = [
    {"id": "D1", "topic": "Uncorroborated NPI — how is it classified?",
     "status": GUIDANCE_REQUESTED,
     "consequence": "Affected records cannot be placed in a category.",
     "affects": ["Analyst queue", "Retrospective report", "Ongoing report"]},
    {"id": "D2", "topic": "No classification rule matches — what is the result?",
     "status": GUIDANCE_REQUESTED,
     "consequence": "A reachable path has an undefined outcome.",
     "affects": ["Analyst queue"]},
    {"id": "D3", "topic": "Category 3 — Reviewer or Senior Analyst tier?",
     "status": GUIDANCE_REQUESTED,
     "consequence": "Determines who may adjudicate; a staffing question, not a finding.",
     "affects": ["Analyst assignment"]},
    {"id": "D4", "topic": "Source unavailable — a classification or a readiness matter?",
     "status": GUIDANCE_REQUESTED,
     "consequence": ("SAM.gov is unavailable across the whole population. Until "
                     "this is answered those records carry a disclosed limitation "
                     "rather than a category."),
     "affects": ["All reports", "Analyst queue"]},
    {"id": "D4_ADDRESS_MATERIALITY",
     "topic": "Which address differences are material?",
     "status": GUIDANCE_REQUESTED,
     "consequence": ("Address conflicts are counted and disclosed as awaiting "
                     "methodology. They are not reported as failures."),
     "affects": ["Retrospective report", "Ongoing report", "Analyst queue"]},
    {"id": "D5", "topic": "Which name differences are reportable?",
     "status": GUIDANCE_REQUESTED,
     "consequence": "Affects whether a name difference reaches category 2 or 3.",
     "affects": ["Analyst queue", "All reports"]},
    {"id": "D6", "topic": "'Flagged' versus 'invalid' identifier",
     "status": GUIDANCE_REQUESTED,
     "consequence": "Affects how a malformed identifier is described.",
     "affects": ["Evidence display", "All reports"]},
    {"id": "D7", "topic": "Potential exclusion match — an automated finding?",
     "status": GUIDANCE_REQUESTED,
     "consequence": ("Gates whether a name-only exclusion match may become a "
                     "category 4 result. It may not, today."),
     "affects": ["Analyst queue", "All reports"]},
    {"id": "D8", "topic": "Records retention period",
     "status": GUIDANCE_REQUESTED,
     "consequence": ("Retention metadata is recorded but no period is set and "
                     "WORM retention is deliberately not enabled."),
     "affects": ["Artifact storage"]},
    {"id": "D9", "topic": "Official deliverable format and 508 checklist",
     "status": GUIDANCE_REQUESTED,
     "consequence": ("No file format is contractually mandated. AGT produces "
                     "HTML, PDF and CSV pending direction."),
     "affects": ["All deliverables"]},
]


def decision_status() -> Dict[str, Any]:
    """The methodology status view, for operators rather than engineers.

    Deliberately contains no table names, class names or migration references.
    """
    counts: Dict[str, int] = {}
    for entry in DECISIONS:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "decisions": [dict(d, classification=Classification.PROGRAM_GUIDANCE_REQUESTED.value
                           if d["status"] == GUIDANCE_REQUESTED
                           else Classification.GOVERNMENT_REQUIREMENT.value)
                      for d in DECISIONS],
        "counts": counts,
        "total": len(DECISIONS),
        "note": ("These are recorded as they stand. An unresolved decision is "
                 "reported as unresolved; it is never resolved by choosing a "
                 "default in software, and no COR response is assumed."),
    }


# ── Module 6 ─────────────────────────────────────────────────────────────────

_CATEGORY_PROHIBITED = [
    ProhibitedConclusion(
        "B1-B4 is the official TEFCA discrepancy classification.",
        "The four categories are the Government's; B1-B4 is AGT internal "
        "shorthand for them. No ONC, ASTP, RCE or Sequoia source establishes "
        "B1-B4 as a federal taxonomy."),
    ProhibitedConclusion(
        "An entity with an address difference is non-compliant.",
        "Address materiality is an open methodology decision. A difference is "
        "an observation, and category 4 requires an adverse authoritative "
        "finding plus a human determination and QA approval.",
        unblocked_by="D4_ADDRESS_MATERIALITY"),
    ProhibitedConclusion(
        "The system placed this entity in a category.",
        "The system proposes; a category is only assigned by an analyst "
        "determination that has passed independent QA."),
]

L6_1 = Lesson(
    slug="the-four-categories",
    title="The four discrepancy categories",
    objective=("Use the Government's words for the Government's categories, and "
               "know which part of the classification is AGT's."),
    body=(
        "The contract requires every stratified list to sort Participants and "
        "Subparticipants into four categories: no discrepancies identified; "
        "minor or administrative discrepancies; inexplicable discrepancies; and "
        "non-compliant discrepancies. That sentence appears three times in the "
        "solicitation, and those are the words a report uses.\n\n"
        "Internally the four are abbreviated B1 to B4. That shorthand is AGT's "
        "own. It is convenient in a queue and wrong in a deliverable, and it is "
        "not a TEFCA, ONC, ASTP, RCE or Sequoia classification.\n\n"
        "The rules that decide which category an entity falls into are also "
        "AGT's. The contract asks the contractor to establish a discrepancy "
        "taxonomy; it does not prescribe one. So the labels are the "
        "Government's and the mapping is ours, and those two facts are stated "
        "separately because confusing them in either direction is a contract "
        "problem."),
    example=("A report says 'Minor or administrative discrepancies: 4'. The "
             "queue behind it says 'B2: 4'. Both are correct in their place."),
    common_mistakes=[
        "Writing B2 in a document that leaves the building.",
        "Describing the mapping rules as a Government requirement.",
        "Describing the category names as an AGT invention.",
    ],
    prohibited=_CATEGORY_PROHIBITED,
    vocabulary=list(GOVERNMENT_CATEGORIES),
    statements=[
        Statement("The four discrepancy categories are defined by the "
                  "Government and are mandatory content of the weekly, final "
                  "and bi-weekly reports.",
                  Classification.GOVERNMENT_REQUIREMENT, SOLICITATION),
        Statement("The contractor shall establish a discrepancy taxonomy for "
                  "documenting and categorizing review findings.",
                  Classification.GOVERNMENT_REQUIREMENT, TAXONOMY_CLAUSE),
        Statement("B1-B4 is AGT internal shorthand for the four Government "
                  "categories and is not a federal taxonomy.",
                  Classification.AGT_IMPLEMENTATION),
        Statement("The rules mapping evidence to a category are AGT's proposal, "
                  "submitted under D2 and awaiting COR acceptance.",
                  Classification.AGT_RECOMMENDATION),
    ],
)

L6_2 = Lesson(
    slug="what-blocks-a-category",
    title="When a category cannot yet be assigned",
    objective=("Recognise that an undecided methodology question is reported as "
               "undecided, not resolved by the system."),
    body=(
        "Several conditions cannot be sorted into a category because the "
        "methodology does not yet say how. The largest on the current data is "
        "the address question: the delivered record carries a registered "
        "address, while NPPES and PECOS publish practice locations. Those are "
        "different kinds of address and can legitimately differ for a fully "
        "compliant organisation.\n\n"
        "Where that is true the condition is named, counted and reported as "
        "awaiting a methodology decision. It is not quietly assigned to a "
        "category, and it is not suppressed. Both of those would be the system "
        "answering a question that belongs to the COR — the first by asserting "
        "a threshold of 'any difference at all', the second by asserting "
        "'never'.\n\n"
        "The same applies to a source that could not answer. SAM.gov currently "
        "has no credential, so it is unavailable across the whole population. "
        "That is a fact about the lookup. It is never evidence about an entity, "
        "and an entity is never placed in a category because a Federal system "
        "was unreachable."),
    example=("9,032 development records show some address difference. They "
             "appear in reports as awaiting methodology, with the count shown, "
             "and in no category."),
    common_mistakes=[
        "Reading 'methodology pending' as 'no problem found'.",
        "Reading 'methodology pending' as 'problem found'.",
        "Treating an unreachable source as a clean result.",
    ],
    prohibited=[
        ProhibitedConclusion(
            "Pending means the entity passed.",
            "It means undecided. It supports no conclusion in either direction."),
        ProhibitedConclusion(
            "SAM.gov returned nothing, so the entity is not debarred.",
            "SAM.gov was not reached. Nothing was returned because nothing was "
            "asked.",
            unblocked_by="a SAM.gov credential"),
    ],
    vocabulary=["METHODOLOGY_PENDING", "SOURCE_UNAVAILABLE"],
    statements=[
        Statement("Address materiality is not settled and affected records are "
                  "reported as awaiting a methodology decision.",
                  Classification.PROGRAM_GUIDANCE_REQUESTED),
        Statement("SAM.gov cannot be queried because no credential has been "
                  "issued; its answers are recorded as unavailable.",
                  Classification.SOURCE_LIMITATION),
        Statement("AGT proposes treating a registered-address versus "
                  "practice-location street difference as informational, and a "
                  "state or ZIP difference as reportable.",
                  Classification.AGT_RECOMMENDATION),
    ],
)

MODULE_6 = Module(
    slug="discrepancies-and-methodology",
    title="6. Discrepancy Categories and Methodology",
    audience=[Role.ANY],
    objective=("Use the contractual categories correctly, and know which "
               "questions the system is not allowed to answer."),
    lessons=[L6_1, L6_2],
    checks=[
        KnowledgeCheck(
            question="Who defines the four discrepancy categories?",
            options=["AGT, as an internal operational classification",
                     "The Government, in the solicitation",
                     "The RCE, in the Common Agreement",
                     "Sequoia, in the QHIN Technical Framework"],
            correct_index=1,
            explanation=("The four categories are quoted from the solicitation, "
                         "where the same sentence appears three times. B1-B4 is "
                         "AGT shorthand for them and is not a federal "
                         "taxonomy.")),
        KnowledgeCheck(
            question=("An entity's registered address differs from its NPPES "
                      "practice location. What category is it?"),
            options=["Category 2, minor or administrative",
                     "Category 3, inexplicable",
                     "Category 4, non-compliant",
                     "None yet — it awaits a methodology decision"],
            correct_index=3,
            explanation=("Whether a registered-address versus practice-location "
                         "difference is material is an open COR decision. The "
                         "condition is counted and disclosed, not categorised.")),
        KnowledgeCheck(
            question="SAM.gov could not be reached for an entity. What follows?",
            options=["The entity is not debarred",
                     "The entity is non-compliant",
                     "Nothing about the entity — the limitation is recorded",
                     "The entity moves to category 3"],
            correct_index=2,
            explanation=("An unreachable source is a fact about the lookup. It "
                         "is never evidence about the entity, in either "
                         "direction.")),
    ],
)
