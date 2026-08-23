"""TEFCA ARC operator guidance. Content only — the framework is in core.

WHY THE VOCABULARY IS IMPORTED RATHER THAN TYPED
    Every term this content teaches is pulled from the enum that defines it. A
    lesson cannot name a state that no longer exists, because the import would
    fail. That matters more here than in most content: the whole point of the
    training is that operators use the same words the evidence uses, and a
    glossary that has quietly drifted from the code is worse than none.

WHAT THIS DELIBERATELY DOES NOT DUPLICATE
    `docs/TEFCA_USER_OPERATIONS_GUIDE.md` is 2,252 lines and already covers
    logging in, dashboards, the daily procedure, entity review and
    troubleshooting. This module covers what that guide predates: the evidence
    vocabulary, applicability, evidence versioning, triage, address materiality
    and the release gates. Two guides that overlap disagree the first time
    either is edited.
"""
from __future__ import annotations

from app.core.evidence_vocabulary import ObservationState
from app.core.learning import (
    ContextualHelp, Glossary, GlossaryTerm, KnowledgeCheck, LearningRegistry,
    Lesson, Module, ProhibitedConclusion, Role)
from app.Tefca.address_comparison import AddressResult
from app.Tefca.exception_triage import Triage
from app.Tefca.source_applicability import SourceApplicability

#: Left navigation, in order.
NAVIGATION = [
    "Getting Started", "Program Overview", "Review Process",
    "Understanding Evidence", "Authoritative Sources", "Analyst Guide",
    "QA Reviewer Guide", "Reports & Deliverables", "Priority Reviews",
    "Ongoing Reviews", "Retrospective Reviews", "Methodology", "Data Quality",
    "Source Limitations", "Glossary", "FAQs", "Troubleshooting",
    "Program Manager Guide",
]

_OBS = [s.value for s in ObservationState]
_APP = [a.value for a in SourceApplicability]
_TRI = [t.value for t in Triage]
_ADDR = [r.value for r in AddressResult]

# ── the conclusions that must never be drawn ────────────────────────────────

_ADDRESS_PROHIBITED = [
    ProhibitedConclusion(
        "This entity's address is wrong / invalid / inaccurate.",
        "An address CONFLICT means the delivered registered address differs from "
        "a published practice location after normalisation. Those are different "
        "kinds of address and the difference may be entirely proper.",
        unblocked_by="D4_ADDRESS_MATERIALITY"),
    ProhibitedConclusion(
        "This entity failed address verification / is non-compliant.",
        "No approved methodology establishes when an address difference is "
        "material, so no pass/fail exists to report.",
        unblocked_by="D4_ADDRESS_MATERIALITY"),
    ProhibitedConclusion(
        "PPEF confirms the full street address.",
        "The PPEF practice-location extract publishes city, state and ZIP and no "
        "street line. Agreement with PPEF is city/state/ZIP agreement only."),
]

_UNAVAILABLE_PROHIBITED = [
    ProhibitedConclusion(
        "SAM.gov returned nothing, so the entity is not registered / is debarred / is clear.",
        "SOURCE_UNAVAILABLE means the source did not answer. It is a fact about "
        "our access, not about the entity, and supports no conclusion in any "
        "direction.",
        unblocked_by="D4 (source unavailable) and SAM.gov credentialing"),
]

_NOT_APPLICABLE_PROHIBITED = [
    ProhibitedConclusion(
        "The entity has no Medicare enrolment because PPEF was NOT_APPLICABLE.",
        "NOT_APPLICABLE means the lookup could not be keyed — usually no NPI was "
        "delivered. Nothing was asked, so nothing was answered."),
]

# ── modules ─────────────────────────────────────────────────────────────────

M1 = Module(
    slug="tefca-arc-overview", title="1. TEFCA ARC and DocuAction",
    audience=[Role.ANY],
    objective="Explain what the programme reviews and what the system does not decide.",
    lessons=[Lesson(
        slug="what-arc-is", title="What the ARC programme reviews",
        objective="Place the system in the contract.",
        body=("The Accuracy Review and Correction programme reviews the accuracy "
              "of organisation information in the TEFCA participant directory "
              "against authoritative federal sources. DocuAction collects and "
              "evidences that comparison.\n\n"
              "The system produces EVIDENCE and OBSERVATIONS. It does not produce "
              "government findings. A finding exists only after an analyst records "
              "a determination and a QA lead approves it."),
        example=("The current delivery contains 23,566 organisation records: "
                 "11,077 Participants and 12,489 Subparticipants across 11 QHINs."),
        common_mistakes=[
            "Describing an automated observation as a finding.",
            "Treating the record count as a count of reviewed entities.",
        ],
        prohibited=[ProhibitedConclusion(
            "The system determined this entity is non-compliant.",
            "The system determines nothing. Automation observes; humans "
            "determine; QA approves.")],
        vocabulary=[])],
    checks=[KnowledgeCheck(
        "The system records MATCH_OBSERVED against OIG LEIE for an entity. What is it?",
        ["A confirmed exclusion finding", "An observation requiring human adjudication",
         "A reason to suspend the entity", "A QA-approved finding"], 1,
        "It is an observation. It becomes a finding only after an analyst "
        "determination and a QA APPROVE.")])

M2 = Module(
    slug="evidence-and-sources", title="2. Evidence and Sources",
    audience=[Role.ANY],
    objective="Know which source answers which question, and what it cannot answer.",
    lessons=[Lesson(
        slug="applicability", title="Applicability comes before the lookup",
        objective="Understand why some sources are never asked.",
        body=("Before any lookup runs, each source is marked for each record:\n\n"
              "- REQUIRED — authoritative here and must answer\n"
              "- APPLICABLE — can answer, and the answer is informative\n"
              "- CONDITIONALLY_APPLICABLE — can answer only once a prior lookup "
              "supplies its key\n"
              "- NOT_APPLICABLE — cannot answer for this kind of record; absence "
              "is expected and means nothing\n"
              "- UNKNOWN_PENDING_METHODOLOGY — whether it applies is itself "
              "undecided\n\n"
              "Medicare enrolment is keyed on NPI. Where no NPI was delivered "
              "there is no key, so enrolment can be neither established nor "
              "refuted."),
        example="4,584 of 23,566 records (19.45%) carry no NPI. That is legitimate "
                "for payers, public health agencies and health information networks.",
        common_mistakes=["Reading NOT_APPLICABLE as 'not found'."],
        prohibited=_NOT_APPLICABLE_PROHIBITED,
        vocabulary=_APP),
        Lesson(
        slug="source-guides", title="Source guides",
        objective="Know each source's authority, key, and limits.",
        body=("**RCE delivery** — the directory under review. Authority: the "
              "programme. Key: organisation OID (the only unique identifier; "
              "TEFCAID and HCID are not unique). Limitation: it is the subject of "
              "the review, never a corroborating source.\n\n"
              "**NPPES** — CMS. Identity, organisation name, practice location, "
              "taxonomy. Key: NPI. Limitation: an NPI proves enumeration, not "
              "licensing or credentialing.\n\n"
              "**PPEF Enrollment** — CMS. Medicare enrolment. Key: NPI → "
              "ENRLMT_ID. Limitation: one-to-many by design; several enrolments "
              "is normal, and payment suspension is not published.\n\n"
              "**PPEF Practice Location** — CMS. Key: ENRLMT_ID. **Publishes city, "
              "state and ZIP and NO street line.**\n\n"
              "**OIG LEIE** — HHS OIG. Exclusion. Key: NPI, or business name where "
              "no NPI. Limitation: most individual rows carry 0000000000, so a "
              "name-only hit is AMBIGUOUS, never an exclusion.\n\n"
              "**CMS Revocation** — CMS. Revoked billing privileges. Key: NPI.\n\n"
              "**SAM.gov** — GSA. Federal registration and debarment. **Not "
              "evaluated: no credential is configured.** Recorded as "
              "SOURCE_UNAVAILABLE for all 23,566 records."),
        example="OIG returned 1 NPI match and 2 name-only matches. The 2 are "
                "AMBIGUOUS and are not exclusions.",
        common_mistakes=["Treating a name-only LEIE hit as an exclusion.",
                         "Reading PPEF agreement as street-address agreement."],
        prohibited=_UNAVAILABLE_PROHIBITED + [_ADDRESS_PROHIBITED[2]],
        vocabulary=[])],
    checks=[KnowledgeCheck(
        "PPEF practice location agrees on city, state and ZIP. What may you conclude?",
        ["The full address matches", "City/state/ZIP agree; no street line was published",
         "The entity is verified", "The address is correct"], 1,
        "PPEF publishes no street line, so street-level agreement was never assessed.")])

M3 = Module(
    slug="automated-observations", title="3. Automated Observations",
    audience=[Role.ANY],
    objective="Read the eight observation states precisely.",
    lessons=[Lesson(
        slug="observation-states", title="The eight states, and the four that are never adverse",
        objective="Never confuse 'we could not ask' with 'the answer was no'.",
        body=("MATCH_OBSERVED — the source answered; one record matched.\n"
              "NO_MATCH_OBSERVED — the source answered; nothing matched. A real, "
              "informative negative.\n"
              "MULTIPLE_MATCHES — more than one matched. Cardinality, not fraud.\n"
              "AMBIGUOUS — matched on supporting evidence only, no decisive "
              "identifier.\n"
              "SOURCE_UNAVAILABLE — the source did not answer.\n"
              "LOOKUP_NOT_APPLICABLE — the lookup does not apply here.\n"
              "INSUFFICIENT_IDENTIFIER — we lacked the key the lookup needs.\n"
              "ERROR — our code failed. A defect, not an outage and not an entity "
              "finding.\n\n"
              "**The last four are never adverse.** Conflating them with "
              "NO_MATCH_OBSERVED is the most consequential mistake available in "
              "this work."),
        example="SAM_GOV is SOURCE_UNAVAILABLE on all 23,566 records because no "
                "API key is configured. Zero entities are implicated by that.",
        common_mistakes=["Counting SOURCE_UNAVAILABLE as a failed check."],
        prohibited=_UNAVAILABLE_PROHIBITED,
        vocabulary=_OBS),
        Lesson(
        slug="address-verdicts", title="Address comparison verdicts",
        objective="Read the six verdicts, and know what a CONFLICT is not.",
        body=("Comparison runs after normalisation, so formatting differences are "
              "already excluded. `123 Main St.` and `123 MAIN STREET` match.\n\n"
              "EXACT_MATCH · NORMALIZED_MATCH · CONFLICT · INSUFFICIENT_DATA · "
              "NOT_APPLICABLE · SOURCE_UNAVAILABLE.\n\n"
              "INSUFFICIENT_DATA is never counted as CONFLICT.\n\n"
              "A CONFLICT is a recorded fact: the normalised values differ. It is "
              "**not** a compliance conclusion, because the delivery supplies a "
              "*registered* address and the sources publish *practice locations*."),
        example=("10,426 conflict observations across 9,032 distinct records — "
                 "1,394 records conflict with both NPPES and PPEF and appear in "
                 "both counts. Reporting 10,426 as an entity count overstates the "
                 "affected population by 1,394."),
        common_mistakes=["Reporting the observation count as an entity count.",
                         "Calling a conflict a failure."],
        prohibited=_ADDRESS_PROHIBITED,
        vocabulary=_ADDR)],
    checks=[KnowledgeCheck(
        "10,426 address conflicts were observed. How many entities are affected?",
        ["10,426", "9,032 — some entities conflict with both sources",
         "23,566", "Cannot be determined"], 1,
        "1,394 entities conflict with both NPPES and PPEF, so the observation "
        "count exceeds the entity count."),
        KnowledgeCheck(
        "An applicable source returns SOURCE_UNAVAILABLE. What does the entity's evidence show?",
        ["A failed check", "Nothing about the entity — the source did not answer",
         "A discrepancy", "Grounds for escalation"], 1,
        "SOURCE_UNAVAILABLE is a fact about access, never about the entity.")])

M4 = Module(
    slug="analyst-review", title="4. Analyst Review",
    audience=[Role.ANALYST, Role.QA, Role.PROGRAM_MANAGER],
    objective="Work an exception correctly, and know what you may not conclude.",
    lessons=[Lesson(
        slug="triage", title="What reaches you, and why",
        objective="Understand why 28 items and not 164,962.",
        body=("Triage sorts every observation into one of five dispositions using "
              "only conditions already decided elsewhere:\n\n"
              "READY_FOR_ANALYST — something adverse or ambiguous was observed.\n"
              "METHODOLOGY_PENDING — whether it needs review is itself undecided.\n"
              "INFORMATIONAL_ONLY — recorded, real and expected.\n"
              "SOURCE_LIMITATION — the limit is in our key or our access.\n"
              "DUPLICATE_CONSOLIDATED — already represented by another item.\n\n"
              "Triage assigns work. It never assigns an answer."),
        example=("Current: 28 ready for analyst · 33,992 methodology-pending · "
                 "154,499 informational · 9 source limitation."),
        common_mistakes=["Assuming METHODOLOGY_PENDING means 'ignore'."],
        prohibited=[], vocabulary=_TRI),
        Lesson(
        slug="determination", title="Recording a determination",
        objective="Produce a determination that survives audit.",
        body=("Open the item, confirm the evidence belongs to this entity by "
              "organisation OID (TEFCAID is not unique), review each cited "
              "observation and its source edition, check for a methodology-pending "
              "condition, then record a written rationale and the determination.\n\n"
              "A determination without a rationale cannot be recorded. A revised "
              "determination is a NEW event referencing the one it supersedes; the "
              "superseded event keeps its author, timestamp and rationale forever. "
              "There is no edit and no override.\n\n"
              "**You cannot approve your own determination.** The system refuses."),
        example="For an OIG NPI match: cite the observation, the LEIE edition and "
                "hash, and state what the match does and does not establish.",
        common_mistakes=["Matching on TEFCAID.",
                         "Concluding on a methodology-pending condition."],
        prohibited=[ProhibitedConclusion(
            "I reviewed it and it is fine, so it is reportable.",
            "Reportability comes only from a QA APPROVE, never from the analyst.")],
        vocabulary=[])],
    checks=[KnowledgeCheck(
        "You have determined an entity's evidence is clean. Is the finding reportable?",
        ["Yes, once you submit it", "Only after a QA lead records APPROVE",
         "Yes, if no exception remains", "Only if the program manager agrees"], 1,
        "reportable_at is set only by a QA APPROVE event.")])

M5 = Module(
    slug="qa-review", title="5. QA Review",
    audience=[Role.QA, Role.PROGRAM_MANAGER],
    objective="Apply the gate that makes a finding reportable.",
    lessons=[Lesson(
        slug="qa-actions", title="APPROVE, RETURN, ESCALATE",
        objective="Know what each action does to reportability.",
        body=("APPROVE — the determination stands and becomes reportable.\n"
              "RETURN — back to the analyst, with a reason.\n"
              "ESCALATE — to a named individual, with a reason.\n\n"
              "Only APPROVE makes a determination reportable, and a later RETURN "
              "or ESCALATE **withdraws** it — the determination is back in play "
              "and must not be cited as settled. Where an analyst issues a new "
              "determination after a RETURN, it needs fresh QA approval.\n\n"
              "You may not QA a determination you made. An exception requires a "
              "grant from a different, more senior person with a written reason; "
              "both are recorded permanently and counted in reconciliation."),
        example="Current state: 43 review records, 0 QA-approved, 0 decision "
                "events. No finding is reportable, which is accurate.",
        common_mistakes=["Assuming an earlier APPROVE survives a later RETURN."],
        prohibited=[ProhibitedConclusion(
            "The finding was approved once, so it stays reportable.",
            "A later RETURN or ESCALATE revokes reportability.")],
        vocabulary=[])],
    checks=[KnowledgeCheck(
        "A determination was APPROVEd, then later RETURNed. Is it reportable?",
        ["Yes — approval already happened", "No — the later RETURN revokes it",
         "Only for internal use", "Only with program manager sign-off"], 1,
        "Reportability requires an APPROVE that still stands.")])

M6 = Module(
    slug="reports", title="6. Reports and Deliverables",
    audience=[Role.ANY],
    objective="Read a report without over-reading it.",
    lessons=[Lesson(
        slug="counts", title="Four counts that are not interchangeable",
        objective="Never promote one count into another.",
        body=("OBSERVATION COUNT ≠ ENTITY COUNT ≠ FINDING COUNT ≠ QA-APPROVED "
              "COUNT.\n\n"
              "An observation is one source's answer about one entity. An entity "
              "may generate many. A finding is a human determination. A "
              "QA-approved finding is the only kind that is reportable.\n\n"
              "Every reported figure carries its denominator, the evidence "
              "version, the source scope and the calculation used."),
        example=("188,528 observations · 23,566 entities · 0 findings · 0 "
                 "QA-approved. All four are correct and none may be substituted "
                 "for another."),
        common_mistakes=["Quoting an observation count as an entity count."],
        prohibited=[], vocabulary=[]),
        Lesson(
        slug="release-gates", title="Why every report says DRAFT",
        objective="Understand the five gates and which one is closed.",
        body=("Evidence version · Human QA · Methodology · Dataset contractual "
              "provenance · Report QA.\n\n"
              "Any closed gate watermarks the report **DRAFT — NOT FOR COR "
              "RELEASE**. Gates are not bypassed; the audience changes.\n\n"
              "Currently one gate is closed: dataset contractual provenance. The "
              "delivery's schema, lineage and content are verified, but its "
              "sender, transmittal and ONC-issued control total are not "
              "documented. That is a contracts question and no engineering work "
              "closes it."),
        example="The internal population report is watermarked DRAFT with exactly "
                "one closed gate.",
        common_mistakes=["Removing the watermark to circulate a report."],
        prohibited=[ProhibitedConclusion(
            "The data passed all checks, so the report can go to the COR.",
            "Technical verification does not open the contractual provenance "
            "gate.", unblocked_by="documented sender, transmittal and control total")],
        vocabulary=[])],
    checks=[KnowledgeCheck(
        "A report shows 188,528 observations. How many entities were reviewed by a person?",
        ["188,528", "23,566", "0 — no human determination has been recorded", "28"], 2,
        "Automated evidence collection is not human review.")])

M7 = Module(
    slug="auditability", title="7. Auditability and Provenance",
    audience=[Role.ANY],
    objective="Know how any number is defended six months later.",
    lessons=[Lesson(
        slug="reconstruction", title="Reconstructing a number",
        objective="Trace a figure to the source row that produced it.",
        body=("Every observation records the identifier searched, the source "
              "edition, that edition's SHA-256, the rule version and a hash of "
              "itself. Every source artefact is retained on disk with its hash, so "
              "a review can be repeated and produce the same result.\n\n"
              "Evidence is append-only. A correction is a NEW version; the "
              "superseded version stays queryable and is never rewritten. Reports "
              "read the current version only.\n\n"
              "The chain runs: reported value → query → observation → source "
              "edition and hash → source row key → the row in the retained file."),
        example=("Two evidence versions exist: phase6-bulk-1.0.0 (164,962, "
                 "historical) and phase6-bulk-1.1.0 (188,528, current). Reports "
                 "read only the latter."),
        common_mistakes=["Mixing evidence versions in one figure."],
        prohibited=[], vocabulary=[])],
    checks=[KnowledgeCheck(
        "Why is the superseded evidence version kept?",
        ["Storage was cheap", "So the original run stays auditable",
         "It is a backup", "Accidentally"], 1,
        "It answers 'what did the system observe on the day it ran' — the first "
        "question an audit asks.")])

MODULES = [M1, M2, M3, M4, M5, M6, M7]

# ── glossary ────────────────────────────────────────────────────────────────

GLOSSARY = Glossary([
    GlossaryTerm("ARC", "Accuracy Review and Correction — the programme reviewing TEFCA directory accuracy.", authority="ONC/ASTP contract"),
    GlossaryTerm("RCE", "Recognized Coordinating Entity — publisher of the TEFCA participant directory.", authority="ONC/ASTP"),
    GlossaryTerm("QHIN", "Qualified Health Information Network. 11 are referenced by the current delivery; none is delivered as a record.", authority="TEFCA"),
    GlossaryTerm("Participant", "An organisation participating in TEFCA under a QHIN. 11,077 in the current delivery.", authority="TEFCA (delivery field sequoiaorgtype)"),
    GlossaryTerm("Subparticipant", "An organisation participating under a Participant. 12,489 in the current delivery.", authority="TEFCA (delivery field sequoiaorgtype)"),
    GlossaryTerm("NPI", "National Provider Identifier. 10 digits. Enumeration only — it does not validate licensing or credentialing.", authority="CMS"),
    GlossaryTerm("NPPES", "CMS registry publishing NPI identity, name, practice location and taxonomy.", authority="CMS"),
    GlossaryTerm("PECOS", "CMS Medicare enrolment system. Its public extract is PPEF.", authority="CMS", not_to_be_confused_with="PPEF, which is the published extract"),
    GlossaryTerm("PPEF", "Public Provider Enrollment File — CMS's published Medicare enrolment extract and its sub-files.", authority="CMS"),
    GlossaryTerm("LEIE", "List of Excluded Individuals and Entities, published by HHS OIG.", authority="HHS OIG"),
    GlossaryTerm("SAM.gov", "Federal registration and debarment system. Not evaluated in the current run — no credential configured.", authority="GSA"),
    GlossaryTerm("Area 1", "The immutable delivery layer: the source file byte-for-byte plus one row per delivered line. Never updated."),
    GlossaryTerm("Area 2", "The curated layer, where deterministic non-substantive corrections are applied. Area 1 is never touched."),
    GlossaryTerm("Evidence", "A recorded statement of what a source said about one entity, with the provenance needed to re-derive it."),
    GlossaryTerm("Observation", "One source's answer, in the Layer-1 vocabulary.", not_to_be_confused_with="a finding, which requires a human determination and QA approval"),
    GlossaryTerm("Applicability", "Whether a source can meaningfully answer for a given record, decided before any lookup runs."),
    GlossaryTerm("Provenance", "The source edition, artefact hash, identifier searched and rule version recorded on every observation."),
    GlossaryTerm("Disposition", "The Layer-3 conclusion drawn from an observation.", not_to_be_confused_with="the Layer-1 observation state"),
    GlossaryTerm("Determination", "An analyst's recorded decision, with a mandatory written rationale. Append-only."),
    GlossaryTerm("Methodology pending", "A condition whose review requirement depends on a COR decision not yet made. Not a failure."),
    GlossaryTerm("Reportable", "A determination carrying a QA APPROVE that still stands. A later RETURN or ESCALATE revokes it."),
    GlossaryTerm("Priority Review", "An ad-hoc review of a specific entity flagged by ONC.", authority="Contract Task 5"),
    GlossaryTerm("Ongoing Review", "A recurring review of each new delivery against the preceding one.", authority="Contract Task 4"),
    GlossaryTerm("Retrospective Review", "The initial-period review of the delivered population.", authority="Contract Task 3"),
    GlossaryTerm("B1-B4", "A four-bucket triage classification used to prioritise review work.",
                 authority="ALLIANCE GLOBAL TECH INTERNAL OPERATIONAL CLASSIFICATION — no ONC, ASTP, RCE, Sequoia or federal source establishes it as a federal taxonomy",
                 not_to_be_confused_with="any federal or TEFCA classification"),
    GlossaryTerm("D4_ADDRESS_MATERIALITY", "The open COR decision on when an address difference is material. Distinct from D4.",
                 not_to_be_confused_with="D4, which concerns an unavailable source"),
])

# ── contextual help ─────────────────────────────────────────────────────────

HELP = [
    ContextualHelp(
        key="evidence.observation",
        what_is_this="One authoritative source's answer about one entity, with the source edition and hash that produced it.",
        why_am_i_seeing_it="Every applicable source is asked for every record, and the answer is recorded whether or not it is interesting.",
        allowed_actions=["Open the source detail", "View provenance", "Open the exception if one was raised"],
        prohibited_conclusions=[ProhibitedConclusion(
            "This observation is a finding.",
            "A finding requires an analyst determination and a QA approval.")],
        evidence_location="tefca_dimension_evidence, current rule_version only"),
    ContextualHelp(
        key="evidence.address_conflict",
        what_is_this="The delivered address and a published practice location differ after normalisation.",
        why_am_i_seeing_it="Formatting differences were already excluded; this difference survived normalisation.",
        allowed_actions=["Compare normalised values", "View both source editions"],
        prohibited_conclusions=_ADDRESS_PROHIBITED,
        evidence_location="dimension_disposition = CONFLICT, with field_conflicts and normalized_values"),
    ContextualHelp(
        key="evidence.source_unavailable",
        what_is_this="An applicable source did not answer.",
        why_am_i_seeing_it="Access to the source is unavailable — for SAM.gov, no credential is configured.",
        allowed_actions=["Record the gap", "Escalate the access question"],
        prohibited_conclusions=_UNAVAILABLE_PROHIBITED,
        evidence_location="observation_result = SOURCE_UNAVAILABLE"),
    ContextualHelp(
        key="exception.queue_item",
        what_is_this="An observation triage marked as requiring human adjudication.",
        why_am_i_seeing_it="Something adverse or ambiguous was observed and only a human can settle it.",
        allowed_actions=["Review evidence", "Record a rationale and determination", "Submit to QA"],
        prohibited_conclusions=[ProhibitedConclusion(
            "I can approve my own determination.",
            "Segregation of duties: the QA reviewer must be a different person.")],
        evidence_location="review_records linked to the cited observations",
        audience=[Role.ANALYST, Role.QA, Role.PROGRAM_MANAGER]),
    ContextualHelp(
        key="qa.decision",
        what_is_this="The gate that makes a determination reportable.",
        why_am_i_seeing_it="An analyst has submitted a determination for independent review.",
        allowed_actions=["APPROVE", "RETURN with a reason", "ESCALATE to a named individual"],
        prohibited_conclusions=[ProhibitedConclusion(
            "Approval is permanent.",
            "A later RETURN or ESCALATE revokes reportability.")],
        evidence_location="review_decision_events, append-only",
        audience=[Role.QA, Role.PROGRAM_MANAGER]),
    ContextualHelp(
        key="report.release_status",
        what_is_this="Whether this report may go to the COR.",
        why_am_i_seeing_it="Five gates are evaluated on every generation and any closed gate watermarks the report.",
        allowed_actions=["View which gate is closed and its remedy", "Circulate internally"],
        prohibited_conclusions=[ProhibitedConclusion(
            "All tests pass, so the report is releasable.",
            "Dataset contractual provenance is not an engineering gate.",
            unblocked_by="documented sender, transmittal and control total")],
        evidence_location="release gate evaluation attached to the report payload"),
    ContextualHelp(
        key="methodology.pending",
        what_is_this="A condition whose review requirement depends on a COR decision not yet made.",
        why_am_i_seeing_it="The methodology available does not establish how to treat it, and the system will not guess.",
        allowed_actions=["View the decision register entry"],
        prohibited_conclusions=[ProhibitedConclusion(
            "Pending means no problem.",
            "It means undecided. It supports no conclusion in either direction.")],
        evidence_location="COR Decision Register"),
    ContextualHelp(
        key="source.limitation",
        what_is_this="Something a source cannot tell us, distinct from something it told us.",
        why_am_i_seeing_it="The limit is in our key or our access, not in the entity.",
        allowed_actions=["View the limitation in the methodology"],
        prohibited_conclusions=_NOT_APPLICABLE_PROHIBITED + _UNAVAILABLE_PROHIBITED,
        evidence_location="Methodology §23 Source limitations"),
]

REGISTRY = LearningRegistry(modules=MODULES, glossary=GLOSSARY,
                            help_topics=HELP, navigation=NAVIGATION)
